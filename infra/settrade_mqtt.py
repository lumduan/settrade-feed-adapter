"""Low-level MQTT transport for Settrade Open API over WebSocket+SSL.

This module provides direct MQTT connectivity to the Settrade Open API
broker, bypassing the official SDK's realtime client layer. It handles
WebSocket+TLS connection, token-based authentication, subscription
management, and automatic reconnection with token refresh.

Architecture note:
    This module intentionally uses synchronous paho-mqtt with threading
    rather than async/await. This is a deliberate performance trade-off:
    the MQTT message callback runs inline in the IO thread with zero
    scheduling overhead, which is critical for the <200us latency target.
    See docs/plan/low-latency-mqtt-feed-adapter/PLAN.md for details.

Connection semantics:
    Uses ``clean_session=True`` which means at-most-once delivery,
    no QoS persistence, and no message replay on reconnect. This
    prioritises freshness over reliability — correct for real-time
    market data where stale data is worthless.

Callback contract:
    Callbacks registered via ``subscribe()`` MUST be non-blocking (<1ms),
    perform no I/O, acquire no locks, and spawn no threads. See the
    phase1-mqtt-transport.md plan document for the full callback contract.
"""

import logging
import random
import threading
import time
from enum import Enum
from typing import Callable

import paho.mqtt.client as mqtt
from pydantic import BaseModel, Field, field_validator
from settrade_v2.context import Context, Option

logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

MessageCallback = Callable[[str, bytes], None]
"""Callback signature: ``(topic: str, payload: bytes) -> None``."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ClientState(str, Enum):
    """Connection state machine for :class:`SettradeMQTTClient`.

    States:
        INIT: Client created but ``connect()`` not yet called.
        CONNECTING: Authentication complete, MQTT connect in progress.
        CONNECTED: MQTT connected and subscriptions active.
        RECONNECTING: Disconnected, background reconnect loop running.
        SHUTDOWN: ``shutdown()`` called — terminal state.
    """

    INIT = "INIT"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    SHUTDOWN = "SHUTDOWN"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class MQTTClientConfig(BaseModel):
    """Configuration for :class:`SettradeMQTTClient`.

    All fields are validated by Pydantic on construction.

    Attributes:
        app_id: Settrade API application ID.
        app_secret: Settrade API application secret (base64-encoded ECDSA key).
        app_code: Settrade API application code.
        broker_id: Settrade broker identifier.
        base_url: Override the Settrade API base URL. Use
            ``"https://open-api-test.settrade.com"`` for sandbox/UAT.
            ``None`` uses the SDK default (production).
        port: MQTT broker port (WSS). Default 443.
        keepalive: MQTT keepalive interval in seconds. Default 30.
        reconnect_min_delay: Minimum reconnect backoff delay in seconds.
        reconnect_max_delay: Maximum reconnect backoff delay in seconds.
        token_refresh_before_exp_seconds: Seconds before token expiry to
            trigger a controlled reconnect for token refresh.
    """

    app_id: str = Field(description="Settrade API application ID")
    app_secret: str = Field(
        description="Settrade API application secret (base64-encoded ECDSA key)",
    )
    app_code: str = Field(description="Settrade API application code")
    broker_id: str = Field(description="Settrade broker identifier")
    base_url: str | None = Field(
        default=None,
        description=(
            "Override Settrade API base URL. "
            "Use 'https://open-api-test.settrade.com' for sandbox/UAT. "
            "None uses the SDK default (production)."
        ),
    )
    port: int = Field(default=443, description="MQTT broker port (WSS)")
    keepalive: int = Field(
        default=30,
        ge=5,
        le=300,
        description="MQTT keepalive interval in seconds",
    )
    reconnect_min_delay: float = Field(
        default=1.0,
        ge=0.1,
        description="Minimum reconnect backoff delay in seconds",
    )
    reconnect_max_delay: float = Field(
        default=30.0,
        ge=1.0,
        description="Maximum reconnect backoff delay in seconds",
    )
    token_refresh_before_exp_seconds: int = Field(
        default=100,
        ge=10,
        description="Seconds before token expiry to trigger controlled reconnect",
    )

    @field_validator("app_secret")
    @classmethod
    def _validate_base64_padding(cls, v: str) -> str:
        """Ensure app_secret has proper base64 padding.

        The Settrade API console may provide app_secret without proper
        base64 padding characters ('='). This validator automatically
        adds the required padding to prevent 'Incorrect padding' errors
        during SDK authentication.

        Args:
            v: The app_secret string from user configuration.

        Returns:
            The app_secret with proper base64 padding added if needed.

        Note:
            Base64 strings must have length divisible by 4. This validator
            adds the minimum number of '=' padding characters needed.
        """
        if not v:
            return v

        # Remove any whitespace
        v = v.strip()

        # Calculate padding needed
        padding_needed: int = (4 - len(v) % 4) % 4

        if padding_needed > 0:
            logger.debug(
                "Auto-padding app_secret: added %d padding character(s)",
                padding_needed,
            )
            v = v + "=" * padding_needed

        return v


# ---------------------------------------------------------------------------
# MQTT Client
# ---------------------------------------------------------------------------


class SettradeMQTTClient:
    """Direct MQTT transport for Settrade Open API.

    Connects to the Settrade MQTT broker via WebSocket+SSL (port 443),
    authenticates using a token fetched via the Settrade REST API,
    and dispatches incoming messages to registered callbacks.

    Features:
        - WebSocket+TLS connection on port 443
        - Token-based authentication via REST API
        - Topic subscription with callback dispatch
        - Auto-reconnect with exponential backoff + jitter
        - Token refresh via controlled reconnect (no live header mutation)
        - Callback isolation (per-callback try/except)
        - Client generation ID to reject stale callbacks
        - Graceful shutdown with state guards

    State transitions:
        State transitions to CONNECTED only occur inside ``on_connect``
        (which runs in the MQTT IO thread). After a successful
        ``_reconnect_loop`` TCP connect, the state remains RECONNECTING
        until ``on_connect`` fires with ``rc=0``. This is by design —
        TCP connect success does not guarantee MQTT-level authentication.

    Args:
        config: MQTT client configuration.

    Example::

        config = MQTTClientConfig(
            app_id="my_app",
            app_secret="my_secret",
            app_code="my_code",
            broker_id="my_broker",
        )
        client = SettradeMQTTClient(config=config)
        client.connect()
        client.subscribe("proto/topic/bidofferv3/AOT", my_callback)
        # ... receive messages ...
        client.shutdown()
    """

    def __init__(self, config: MQTTClientConfig) -> None:
        self._config: MQTTClientConfig = config

        # Authentication (broker_id resolved after login — "SANDBOX" → "098")
        self._ctx: Context | None = None
        self._broker_id: str = config.broker_id
        self._host: str | None = None
        self._token: str | None = None
        self._token_type: str = "Bearer"
        self._expired_at: float = 0.0

        # MQTT client
        self._client: mqtt.Client | None = None
        self._client_generation: int = 0

        # Subscription registry: topic → list of callbacks (source of truth)
        self._subscriptions: dict[str, list[MessageCallback]] = {}
        self._sub_lock: threading.Lock = threading.Lock()

        # State machine
        self._state: ClientState = ClientState.INIT
        self._state_lock: threading.Lock = threading.Lock()

        # Reconnect guard
        self._reconnecting: bool = False
        self._reconnect_lock: threading.Lock = threading.Lock()
        self._shutdown_event: threading.Event = threading.Event()

        # Counters (guarded by _counter_lock for thread-safe read/write)
        self._messages_received: int = 0
        self._callback_errors: int = 0
        self._reconnect_count: int = 0
        self._counter_lock: threading.Lock = threading.Lock()

        # Timestamps
        self._last_connect_ts: float = 0.0
        self._last_disconnect_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected to the broker."""
        with self._state_lock:
            return self._state == ClientState.CONNECTED

    @property
    def state(self) -> ClientState:
        """Current connection state."""
        with self._state_lock:
            return self._state

    def connect(self) -> None:
        """Login, fetch dispatcher token, and connect to MQTT broker.

        Performs the full authentication flow:

        1. Login via Settrade REST API (obtains access token)
        2. Fetch MQTT host and dispatcher token
        3. Create and connect paho MQTT client over WSS
        4. Start background token refresh timer

        Raises:
            RuntimeError: If client is not in INIT state.
            Exception: If login or connection fails.
        """
        with self._state_lock:
            if self._state != ClientState.INIT:
                raise RuntimeError(
                    f"Cannot connect: client is in {self._state} state"
                )
            self._state = ClientState.CONNECTING

        self._login()
        self._fetch_host_token()
        self._client = self._create_mqtt_client()
        self._client.connect(
            host=self._host,  # type: ignore[arg-type]
            port=self._config.port,
            keepalive=self._config.keepalive,
        )
        self._client.loop_start()
        self._start_token_refresh_timer()
        logger.info(
            "MQTT client started, connecting to %s:%d",
            self._host,
            self._config.port,
        )

    def subscribe(self, topic: str, callback: MessageCallback) -> None:
        """Subscribe to a topic and register a message callback.

        Multiple callbacks can be registered for the same topic.
        If already connected, the MQTT subscription is sent immediately.
        If called during RECONNECTING, the subscription is stored in the
        source-of-truth dict and will be replayed on the next successful
        ``on_connect``.

        Args:
            topic: MQTT topic to subscribe to
                (e.g., ``"proto/topic/bidofferv3/AOT"``).
            callback: Function called with ``(topic, payload)`` on each
                message. Must be non-blocking (<1ms).
        """
        with self._sub_lock:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
                # Send MQTT subscribe only if connected
                with self._state_lock:
                    is_connected: bool = self._state == ClientState.CONNECTED
                if is_connected and self._client is not None:
                    self._client.subscribe(topic=topic)
                    logger.info("Subscribed to topic: %s", topic)
            self._subscriptions[topic].append(callback)

    def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a topic and remove all callbacks.

        Args:
            topic: MQTT topic to unsubscribe from.
        """
        with self._sub_lock:
            if topic in self._subscriptions:
                del self._subscriptions[topic]
                with self._state_lock:
                    is_connected: bool = self._state == ClientState.CONNECTED
                if is_connected and self._client is not None:
                    self._client.unsubscribe(topic=topic)
                    logger.info("Unsubscribed from topic: %s", topic)

    def shutdown(self) -> None:
        """Gracefully shut down the MQTT client.

        Performs orderly shutdown:

        1. Sets state to SHUTDOWN (prevents reconnect)
        2. Signals all background threads to stop
        3. Stops MQTT IO loop
        4. Disconnects from broker

        Safe to call from any thread. Idempotent.
        """
        with self._state_lock:
            if self._state == ClientState.SHUTDOWN:
                return
            self._state = ClientState.SHUTDOWN

        logger.info("Shutting down MQTT client")
        self._shutdown_event.set()

        if self._client is not None:
            try:
                self._client.loop_stop()
            except Exception:
                logger.debug("Exception during loop_stop", exc_info=True)
            try:
                self._client.disconnect()
            except Exception:
                logger.debug("Exception during disconnect", exc_info=True)

        with self._counter_lock:
            msgs: int = self._messages_received
            errs: int = self._callback_errors
            reconns: int = self._reconnect_count

        logger.info(
            "MQTT client shut down (messages=%d, errors=%d, reconnects=%d)",
            msgs,
            errs,
            reconns,
        )

    def stats(self) -> dict[str, str | int | float | bool]:
        """Return client statistics.

        Returns:
            Dictionary with connection state, counters, and timestamps.
        """
        with self._state_lock:
            current_state: str = self._state.value
        with self._counter_lock:
            msgs: int = self._messages_received
            errs: int = self._callback_errors
            reconns: int = self._reconnect_count
        return {
            "state": current_state,
            "connected": current_state == ClientState.CONNECTED.value,
            "messages_received": msgs,
            "callback_errors": errs,
            "reconnect_count": reconns,
            "last_connect_ts": self._last_connect_ts,
            "last_disconnect_ts": self._last_disconnect_ts,
        }

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _login(self) -> None:
        """Authenticate via Settrade REST API using the SDK Context.

        Creates a new :class:`settrade_v2.context.Context` and calls
        ``login()`` to obtain an access token.

        Handles SANDBOX broker detection: when ``broker_id`` is
        ``"SANDBOX"``, the SDK's global config is set to ``"uat"``
        environment and the actual broker_id is changed to ``"098"``,
        matching the behaviour of the official SDK's ``Investor`` class.

        If ``base_url`` is explicitly configured, it overrides the
        environment-based URL.
        """
        broker_id: str = self._broker_id

        # Replicate SDK's SANDBOX detection (see settrade_v2.user._BaseUser)
        if broker_id.upper() == "SANDBOX":
            from settrade_v2.config import config as sdk_config

            sdk_config["environment"] = "uat"
            broker_id = "098"

        self._broker_id = broker_id

        self._ctx = Context(
            app_id=self._config.app_id,
            app_secret=self._config.app_secret,
            app_code=self._config.app_code,
            broker_id=self._broker_id,
        )
        if self._config.base_url is not None:
            self._ctx.base_url = self._config.base_url
        self._ctx.login()
        self._expired_at = self._ctx.expired_at
        self._token_type = self._ctx.token_type
        logger.info("Authenticated with Settrade API (broker=%s)", self._broker_id)

    def _fetch_host_token(self) -> None:
        """Fetch MQTT broker host and dispatcher token via REST API.

        Calls ``GET /api/dispatcher/v3/{broker_id}/token`` using the
        SDK's ``dispatch(Option(...))`` pattern. This routes through
        ``send_request()`` which auto-refreshes the access token if
        near expiry, so ``_expired_at`` is updated after the call to
        stay in sync.

        Raises:
            RuntimeError: If not logged in.
            ValueError: If no MQTT hosts returned.
        """
        if self._ctx is None:
            raise RuntimeError("Must login before fetching host/token")

        token_url: str = (
            f"{self._ctx.base_url}/api/dispatcher/v3/{self._broker_id}/token"
        )
        option: Option = Option("GET", token_url)
        resp = self._ctx.dispatch(option)
        data: dict = resp.json()
        hosts: list[str] = data["hosts"]
        if not hosts:
            raise ValueError("No MQTT hosts returned from dispatcher")

        self._host = hosts[0]
        self._token = data["token"]

        # Sync expired_at — Context.request() may have auto-refreshed
        self._expired_at = self._ctx.expired_at

        logger.info("Fetched MQTT host: %s", self._host)

    # ------------------------------------------------------------------
    # MQTT Client Factory
    # ------------------------------------------------------------------

    def _create_mqtt_client(self) -> mqtt.Client:
        """Create and configure a new paho MQTT client.

        Each invocation increments ``_client_generation`` to allow
        stale callback rejection. The previous client (if any) is
        stopped and disconnected.

        Returns:
            Configured paho MQTT client ready for ``connect()``.
        """
        # Clean up previous client
        if self._client is not None:
            try:
                self._client.loop_stop()
            except Exception:
                pass
            try:
                self._client.disconnect()
            except Exception:
                pass

        self._client_generation += 1
        generation: int = self._client_generation

        client: mqtt.Client = mqtt.Client(
            clean_session=True,
            transport="websockets",
        )
        client.tls_set()
        client.ws_set_options(
            headers={"Authorization": f"{self._token_type} {self._token}"},
            path=f"/api/dispatcher/v3/{self._broker_id}/mqtt",
        )

        # Bind callbacks — on_message captures generation for staleness check
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = lambda c, u, m: self._on_message(
            client=c,
            userdata=u,
            msg=m,
            generation=generation,
        )

        return client

    # ------------------------------------------------------------------
    # MQTT Callbacks
    # ------------------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: dict,
        rc: int,
    ) -> None:
        """Handle MQTT connection event.

        On successful connection (``rc=0``), updates state to CONNECTED,
        records timestamp, and replays all subscriptions from the
        source-of-truth dict.

        On failure (``rc!=0``), triggers reconnect with fresh credentials.
        State transitions to CONNECTED **only** happen here — not in
        ``_reconnect_loop``. This ensures MQTT-level auth is confirmed
        before declaring the connection alive.

        Args:
            client: The paho MQTT client instance.
            userdata: User data (unused).
            flags: Connection flags from broker.
            rc: Connection result code (0 = success).
        """
        if rc == 0:
            with self._state_lock:
                self._state = ClientState.CONNECTED
            self._last_connect_ts = time.time()
            logger.info("Connected to MQTT broker at %s", self._host)

            # Replay ALL subscriptions from source of truth
            with self._sub_lock:
                topics: list[str] = list(self._subscriptions.keys())
            for topic in topics:
                client.subscribe(topic=topic)
                logger.info("Replayed subscription: %s", topic)
        else:
            logger.error("MQTT connection failed (rc=%d)", rc)
            self._schedule_reconnect()

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        rc: int,
    ) -> None:
        """Handle MQTT disconnection event.

        On unexpected disconnect (``rc != 0``), triggers auto-reconnect
        with exponential backoff + jitter.

        Args:
            client: The paho MQTT client instance.
            userdata: User data (unused).
            rc: Disconnect reason code (0 = clean disconnect).
        """
        self._last_disconnect_ts = time.time()

        if rc == 0:
            logger.info("Disconnected from MQTT broker (clean)")
        else:
            with self._state_lock:
                if self._state == ClientState.SHUTDOWN:
                    return
            logger.warning("Unexpected MQTT disconnect (rc=%d)", rc)
            self._schedule_reconnect()

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: object,
        msg: mqtt.MQTTMessage,
        generation: int,
    ) -> None:
        """Dispatch incoming message to registered callbacks.

        **HOT PATH** — runs inline in the MQTT IO thread.

        - Generation check rejects stale messages from old client instances
        - Counter lock (~50ns) for thread-safe increment
        - No subscription lock (dict.get is CPython-GIL-safe for reads)
        - Per-callback try/except for isolation

        Args:
            client: The paho MQTT client instance.
            userdata: User data (unused).
            msg: The received MQTT message.
            generation: Client generation ID at time of client creation.
        """
        # Reject messages from stale client instances
        if generation != self._client_generation:
            return

        with self._counter_lock:
            self._messages_received += 1

        topic: str = msg.topic

        # Direct dict lookup — thread-safe in CPython (GIL)
        callbacks: list[MessageCallback] | None = self._subscriptions.get(topic)
        if callbacks is not None:
            for cb in callbacks:
                try:
                    cb(topic, msg.payload)
                except Exception:
                    with self._counter_lock:
                        self._callback_errors += 1
                    logger.exception("Callback error for topic %s", topic)

    # ------------------------------------------------------------------
    # Reconnection
    # ------------------------------------------------------------------

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt in a background thread.

        Uses a state guard (``_reconnecting`` flag under ``_reconnect_lock``)
        to prevent duplicate reconnect threads from being spawned when
        multiple disconnect events or token refresh timers fire concurrently.
        """
        with self._reconnect_lock:
            if self._reconnecting:
                return
            with self._state_lock:
                if self._state == ClientState.SHUTDOWN:
                    return
                self._state = ClientState.RECONNECTING
            self._reconnecting = True

        thread: threading.Thread = threading.Thread(
            target=self._reconnect_loop,
            daemon=True,
            name="mqtt-reconnect",
        )
        thread.start()

    def _reconnect_loop(self) -> None:
        """Reconnect with exponential backoff + jitter.

        Runs in a background thread. Each attempt:

        1. Fetches fresh host/token (Context auto-refreshes access token)
        2. Syncs ``_expired_at`` from Context
        3. Creates a new MQTT client with fresh headers
        4. Connects and starts the IO loop

        On success, ``_reconnecting`` is cleared via ``finally`` block.
        On failure, waits with jittered backoff before retrying.

        Infinite retry — only exits on success or shutdown.
        Backoff resets implicitly: each invocation starts with min delay.

        Note: State transition to CONNECTED happens in ``_on_connect``,
        not here. TCP connect success ≠ MQTT-level authentication success.
        """
        delay: float = self._config.reconnect_min_delay

        try:
            while not self._shutdown_event.is_set():
                try:
                    logger.info("Reconnect attempt (delay=%.1fs)", delay)
                    self._fetch_host_token()
                    new_client: mqtt.Client = self._create_mqtt_client()
                    new_client.connect(
                        host=self._host,  # type: ignore[arg-type]
                        port=self._config.port,
                        keepalive=self._config.keepalive,
                    )
                    new_client.loop_start()
                    self._client = new_client
                    with self._counter_lock:
                        self._reconnect_count += 1
                    logger.info(
                        "Reconnect TCP success (total=%d, gen=%d) — "
                        "awaiting on_connect for MQTT-level confirmation",
                        self._reconnect_count,
                        self._client_generation,
                    )
                    return
                except Exception:
                    logger.exception("Reconnect attempt failed")
                    jittered_delay: float = delay * random.uniform(0.8, 1.2)
                    self._shutdown_event.wait(timeout=jittered_delay)
                    delay = min(delay * 2, self._config.reconnect_max_delay)
        finally:
            with self._reconnect_lock:
                self._reconnecting = False

    # ------------------------------------------------------------------
    # Token Refresh
    # ------------------------------------------------------------------

    def _start_token_refresh_timer(self) -> None:
        """Start a background timer to refresh token before expiration.

        Monitors the ``expired_at`` timestamp and triggers a controlled
        reconnect when the token is within ``token_refresh_before_exp_seconds``
        of expiry.

        Uses ``_schedule_reconnect()`` which shares the same state guard
        as disconnect-triggered reconnect, preventing dual reconnect flows
        if timer and network drop coincide.
        """
        thread: threading.Thread = threading.Thread(
            target=self._token_refresh_check,
            daemon=True,
            name="token-refresh",
        )
        thread.start()

    def _token_refresh_check(self) -> None:
        """Background loop that monitors token expiration.

        When the token is about to expire, triggers a controlled reconnect
        via ``_schedule_reconnect()``. This ensures fresh credentials are
        obtained and a new MQTT client is created (no live header mutation).

        Controlled reconnect may cause brief downtime (~1-3s) if it
        coincides with network instability. This is acceptable for market
        data feeds where freshness > reliability.
        """
        while not self._shutdown_event.is_set():
            time_until_refresh: float = (
                self._expired_at
                - self._config.token_refresh_before_exp_seconds
                - time.time()
            )

            if time_until_refresh <= 0:
                logger.info(
                    "Token near expiry (expires_at=%.0f), "
                    "triggering controlled reconnect",
                    self._expired_at,
                )
                self._schedule_reconnect()
                # Wait for reconnect to complete before checking again
                self._shutdown_event.wait(
                    timeout=self._config.reconnect_max_delay,
                )
            else:
                # Check every 60s or when refresh is due, whichever is sooner
                wait_time: float = min(time_until_refresh, 60.0)
                self._shutdown_event.wait(timeout=wait_time)
