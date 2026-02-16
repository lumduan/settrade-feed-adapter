"""BidOffer adapter for Settrade Open API protobuf messages.

This module provides the ``BidOfferAdapter`` that sits between the
MQTT transport (Phase 1) and the event consumer (dispatcher in Phase 3).
It subscribes to ``proto/topic/bidofferv3/{symbol}`` topics, parses
binary BidOfferV3 protobuf messages, and forwards normalized events
via a callback.

Architecture note:
    The adapter's ``_on_message`` runs inline in the MQTT IO thread.
    All parsing uses direct field access (no ``.to_dict()``), Money
    conversion uses inline ``units + nanos * 1e-9`` (no ``Decimal``),
    and event models are created via ``model_construct()`` (no Pydantic
    validation). This eliminates all SDK overhead sources identified
    in the PLAN.md.

Thread ownership:
    - ``subscribe()`` / ``unsubscribe()`` — main thread only.
    - ``_on_message()`` — MQTT IO thread only (via paho callback).
    - ``stats()`` — any thread (lock-protected read snapshot).

Error isolation:
    Parse errors and callback errors are counted in separate counters.
    A protobuf parse failure does NOT increment ``callback_errors``,
    and a downstream callback failure does NOT increment ``parse_errors``.
    This enables precise production debugging.

Logging safety:
    Hot-path error logging is rate-limited. The first 10 errors of
    each type are logged with full stack traces. Subsequent errors
    are logged at reduced frequency (every 1000th) to prevent log
    storms from overwhelming the system at high message rates.

Protobuf instance reuse (future optimisation):
    Currently creates a new ``BidOfferV3()`` per message. Instance
    reuse (``self._msg.parse(payload)``) is a potential future
    optimisation pending betterproto state-safety verification.

Example:
    >>> from infra.settrade_adapter import BidOfferAdapter, BidOfferAdapterConfig
    >>> from infra.settrade_mqtt import MQTTClientConfig, SettradeMQTTClient
    >>>
    >>> events = []
    >>> mqtt_config = MQTTClientConfig(
    ...     app_id="id", app_secret="secret",
    ...     app_code="code", broker_id="broker",
    ... )
    >>> client = SettradeMQTTClient(config=mqtt_config)
    >>> adapter = BidOfferAdapter(
    ...     config=BidOfferAdapterConfig(),
    ...     mqtt_client=client,
    ...     on_event=events.append,
    ... )
    >>> adapter.subscribe("AOT")
    >>> # ... messages arrive, events are appended to list ...
"""

import logging
import threading
import time
from typing import Callable, Union

from pydantic import BaseModel, Field
from settrade_v2.pb.bidofferv3_pb2 import BidOfferV3

from core.events import BestBidAsk, FullBidOffer
from infra.settrade_mqtt import SettradeMQTTClient

logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

BidOfferEvent = Union[BestBidAsk, FullBidOffer]
"""Union type for events produced by :class:`BidOfferAdapter`."""

EventCallback = Callable[[BidOfferEvent], None]
"""Callback signature for event consumers: ``(event) -> None``.

Must be non-blocking (<1ms), perform no I/O, acquire no locks.
Runs in the MQTT IO thread.
"""

# ---------------------------------------------------------------------------
# Topic pattern
# ---------------------------------------------------------------------------

_TOPIC_PREFIX: str = "proto/topic/bidofferv3/"
"""MQTT topic prefix for BidOfferV3 subscriptions."""

# ---------------------------------------------------------------------------
# Rate-limited logging thresholds
# ---------------------------------------------------------------------------

_LOG_FIRST_N: int = 10
"""Log full stack trace for the first N errors of each type."""

_LOG_EVERY_N: int = 1000
"""After the first N errors, log every Nth occurrence."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class BidOfferAdapterConfig(BaseModel):
    """Configuration for :class:`BidOfferAdapter`.

    Attributes:
        full_depth: If ``True``, produce :class:`FullBidOffer` events
            with all 10 price/volume levels. If ``False`` (default),
            produce :class:`BestBidAsk` with top-of-book only.

    Example:
        >>> config = BidOfferAdapterConfig(full_depth=True)
        >>> config.full_depth
        True
    """

    full_depth: bool = Field(
        default=False,
        description=(
            "Produce FullBidOffer (10 levels) instead of BestBidAsk. "
            "WARNING: FullDepth mode allocates ~46 objects per message "
            "and is not intended for sub-100us strategies."
        ),
    )


# ---------------------------------------------------------------------------
# Money conversion (public utility — NOT for hot paths)
# ---------------------------------------------------------------------------


def money_to_float(money: object) -> float:
    """Convert a betterproto ``Money`` message to ``float``.

    Uses integer arithmetic (``units + nanos * 1e-9``) to avoid
    ``Decimal`` allocation overhead.

    **Not for hot paths.** The adapter's hot path uses inline
    expressions (``msg.bid_price1.units + msg.bid_price1.nanos * 1e-9``)
    to avoid function call overhead. This utility is provided for
    external callers, tests, and non-performance-critical code.

    Args:
        money: A betterproto ``Money`` instance with ``units`` (int)
            and ``nanos`` (int) attributes. Duck-typed for flexibility.

    Returns:
        The monetary value as a float.

    Raises:
        AttributeError: If ``money`` lacks ``units`` or ``nanos``.

    Example:
        >>> from unittest.mock import SimpleNamespace
        >>> m = SimpleNamespace(units=25, nanos=500_000_000)
        >>> money_to_float(m)
        25.5
        >>> money_to_float(SimpleNamespace(units=0, nanos=0))
        0.0
    """
    return money.units + money.nanos * 1e-9  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class BidOfferAdapter:
    """Adapter for BidOfferV3 protobuf messages.

    Subscribes to ``proto/topic/bidofferv3/{symbol}`` topics via an
    :class:`SettradeMQTTClient`, parses binary BidOfferV3 messages,
    normalizes them into :class:`BestBidAsk` or :class:`FullBidOffer`
    events, and forwards them via the ``on_event`` callback.

    Hot path performance:
        - ``model_construct()`` — no Pydantic validation overhead
        - Inline ``units + nanos * 1e-9`` — no Decimal allocation
        - Direct field access — no ``.to_dict()`` or ``getattr``
        - Lock-free counters — GIL-atomic increments in CPython
        - Separated error isolation — parse vs callback errors
        - Rate-limited logging — prevents log storms on error loops

    Thread ownership:
        - ``subscribe()`` / ``unsubscribe()`` — main thread only.
        - ``_on_message()`` — MQTT IO thread only (via paho).
        - ``stats()`` — any thread (lock-protected snapshot).

    Args:
        config: Adapter configuration (e.g., ``full_depth`` mode).
        mqtt_client: Connected :class:`SettradeMQTTClient` instance.
        on_event: Callback invoked with each normalized event.
            Must be non-blocking, no I/O, no locks. Runs in MQTT
            IO thread.

    Example:
        >>> events = []
        >>> adapter = BidOfferAdapter(
        ...     config=BidOfferAdapterConfig(),
        ...     mqtt_client=client,
        ...     on_event=events.append,
        ... )
        >>> adapter.subscribe("AOT")
        >>> adapter.subscribe("PTT")
        >>> # ... events arrive ...
        >>> print(adapter.stats())
        {'subscribed_symbols': ['AOT', 'PTT'], ...}
    """

    def __init__(
        self,
        config: BidOfferAdapterConfig,
        mqtt_client: SettradeMQTTClient,
        on_event: EventCallback,
    ) -> None:
        self._config: BidOfferAdapterConfig = config
        self._mqtt_client: SettradeMQTTClient = mqtt_client
        self._on_event: EventCallback = on_event

        # Subscribed symbols (guarded by _sub_lock for thread-safe reads)
        self._subscribed_symbols: set[str] = set()
        self._sub_lock: threading.Lock = threading.Lock()

        # Counters (written GIL-atomic in MQTT IO thread, read under lock)
        self._messages_parsed: int = 0
        self._parse_errors: int = 0
        self._callback_errors: int = 0
        self._counter_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribe(self, symbol: str) -> None:
        """Subscribe to BidOfferV3 updates for a symbol.

        Builds the MQTT topic ``proto/topic/bidofferv3/{symbol}`` and
        registers ``_on_message`` as the callback on the MQTT client.

        Duplicate subscriptions for the same symbol are ignored.

        Must be called from the main thread only. Safe to call before
        or after MQTT connection — the underlying ``SettradeMQTTClient``
        stores subscriptions and replays them on reconnect.

        Args:
            symbol: Stock symbol to subscribe to (e.g., ``"AOT"`` or ``"aot"``).
                Automatically converted to uppercase. Must be non-empty.

        Example:
            >>> adapter.subscribe("AOT")
            >>> adapter.subscribe("ptt")  # Converted to "PTT"
            >>> "AOT" in adapter.subscribed_symbols
            True
            >>> "PTT" in adapter.subscribed_symbols
            True
        """
        # Normalize symbol to uppercase
        symbol = symbol.upper()

        with self._sub_lock:
            if symbol in self._subscribed_symbols:
                logger.debug(
                    "BidOfferAdapter already subscribed to %s, skipping",
                    symbol,
                )
                return
            self._subscribed_symbols.add(symbol)

        topic: str = f"{_TOPIC_PREFIX}{symbol}"
        self._mqtt_client.subscribe(topic=topic, callback=self._on_message)
        logger.info("BidOfferAdapter subscribed to %s", symbol)

    def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from BidOfferV3 updates for a symbol.

        Must be called from the main thread only.

        Args:
            symbol: Stock symbol to unsubscribe from (e.g., ``"AOT"`` or ``"aot"``).  
                Automatically converted to uppercase.

        Example:
            >>> adapter.unsubscribe("AOT")
            >>> "AOT" in adapter.subscribed_symbols
            False
            >>> adapter.unsubscribe("ptt")  # Converted to "PTT"
        """
        # Normalize symbol to uppercase
        symbol = symbol.upper()

        with self._sub_lock:
            self._subscribed_symbols.discard(symbol)

        topic: str = f"{_TOPIC_PREFIX}{symbol}"
        self._mqtt_client.unsubscribe(topic=topic)
        logger.info("BidOfferAdapter unsubscribed from %s", symbol)

    @property
    def subscribed_symbols(self) -> frozenset[str]:
        """Currently subscribed symbols (read-only snapshot).

        Thread-safe — acquires subscription lock for snapshot.

        Returns:
            Frozen set of subscribed symbol strings.
        """
        with self._sub_lock:
            return frozenset(self._subscribed_symbols)

    def stats(self) -> dict[str, object]:
        """Return adapter statistics. Thread-safe.

        Acquires both ``_counter_lock`` and ``_sub_lock`` to snapshot
        all state atomically. Can be called from any thread.

        Returns:
            Dictionary with subscribed symbols, counter values, and
            configuration state.

        Example:
            >>> stats = adapter.stats()
            >>> stats["messages_parsed"]
            1234
            >>> stats["parse_errors"]
            0
        """
        with self._counter_lock:
            messages_parsed: int = self._messages_parsed
            parse_errors: int = self._parse_errors
            callback_errors: int = self._callback_errors

        with self._sub_lock:
            symbols: list[str] = sorted(self._subscribed_symbols)

        return {
            "subscribed_symbols": symbols,
            "messages_parsed": messages_parsed,
            "parse_errors": parse_errors,
            "callback_errors": callback_errors,
            "full_depth": self._config.full_depth,
        }

    # ------------------------------------------------------------------
    # Hot Path (MQTT IO thread)
    # ------------------------------------------------------------------

    def _on_message(self, topic: str, payload: bytes) -> None:
        """Parse BidOfferV3 and forward normalized event.

        **HOT PATH** — runs inline in the MQTT IO thread.

        Error isolation: parse errors and callback errors are tracked
        in separate counters. A message increments exactly one of:
        ``_messages_parsed``, ``_parse_errors``, or ``_callback_errors``.

        Counter increments are lock-free (GIL-atomic in CPython).
        Logging is rate-limited to prevent log storms.

        Args:
            topic: MQTT topic string
                (e.g., ``"proto/topic/bidofferv3/AOT"``).
            payload: Raw binary protobuf payload.
        """
        recv_ts: int = time.time_ns()
        recv_mono_ns: int = time.perf_counter_ns()

        # Phase 1: Parse protobuf and create event (isolated)
        try:
            msg: BidOfferV3 = BidOfferV3().parse(payload)
            if self._config.full_depth:
                event: BidOfferEvent = self._parse_full_bid_offer(
                    msg=msg,
                    recv_ts=recv_ts,
                    recv_mono_ns=recv_mono_ns,
                )
            else:
                event = self._parse_best_bid_ask(
                    msg=msg,
                    recv_ts=recv_ts,
                    recv_mono_ns=recv_mono_ns,
                )
        except Exception:
            self._parse_errors += 1
            self._log_parse_error(topic=topic)
            return

        # Phase 2: Forward event to callback (isolated)
        try:
            self._on_event(event)
        except Exception:
            self._callback_errors += 1
            self._log_callback_error(topic=topic)
            return

        # Only increment on full success (parse + callback)
        self._messages_parsed += 1

    # ------------------------------------------------------------------
    # Rate-Limited Logging
    # ------------------------------------------------------------------

    def _log_parse_error(self, topic: str) -> None:
        """Log parse error with rate limiting.

        First ``_LOG_FIRST_N`` errors: full stack trace via
        ``logger.exception()``. Subsequent errors: every
        ``_LOG_EVERY_N``-th occurrence at ERROR level (no trace).

        Args:
            topic: MQTT topic that caused the error.
        """
        count: int = self._parse_errors
        if count <= _LOG_FIRST_N:
            logger.exception(
                "Failed to parse BidOfferV3 on %s (%d/%d)",
                topic,
                count,
                _LOG_FIRST_N,
            )
        elif count % _LOG_EVERY_N == 0:
            logger.error(
                "Parse errors ongoing: %d total (topic=%s)",
                count,
                topic,
            )

    def _log_callback_error(self, topic: str) -> None:
        """Log callback error with rate limiting.

        First ``_LOG_FIRST_N`` errors: full stack trace via
        ``logger.exception()``. Subsequent errors: every
        ``_LOG_EVERY_N``-th occurrence at ERROR level (no trace).

        Args:
            topic: MQTT topic that caused the error.
        """
        count: int = self._callback_errors
        if count <= _LOG_FIRST_N:
            logger.exception(
                "Event callback error for %s (%d/%d)",
                topic,
                count,
                _LOG_FIRST_N,
            )
        elif count % _LOG_EVERY_N == 0:
            logger.error(
                "Callback errors ongoing: %d total (topic=%s)",
                count,
                topic,
            )

    # ------------------------------------------------------------------
    # Parsers (model_construct — no validation)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_best_bid_ask(
        msg: BidOfferV3,
        recv_ts: int,
        recv_mono_ns: int,
    ) -> BestBidAsk:
        """Extract top-of-book from BidOfferV3 into BestBidAsk.

        Uses ``model_construct()`` to skip Pydantic validation.
        Direct field access — no ``getattr``, no ``Decimal``.

        Args:
            msg: Parsed BidOfferV3 betterproto message.
            recv_ts: Wall-clock timestamp from ``time.time_ns()``.
            recv_mono_ns: Monotonic timestamp from
                ``time.perf_counter_ns()``.

        Returns:
            Normalized :class:`BestBidAsk` event.
        """
        return BestBidAsk.model_construct(
            symbol=msg.symbol,
            bid=msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
            ask=msg.ask_price1.units + msg.ask_price1.nanos * 1e-9,
            bid_vol=msg.bid_volume1,
            ask_vol=msg.ask_volume1,
            bid_flag=int(msg.bid_flag),
            ask_flag=int(msg.ask_flag),
            recv_ts=recv_ts,
            recv_mono_ns=recv_mono_ns,
        )

    @staticmethod
    def _parse_full_bid_offer(
        msg: BidOfferV3,
        recv_ts: int,
        recv_mono_ns: int,
    ) -> FullBidOffer:
        """Extract full 10-level depth from BidOfferV3 into FullBidOffer.

        Uses ``model_construct()`` to skip Pydantic validation.
        Explicit field unroll — no ``getattr``, no f-string allocation,
        no dynamic attribute lookup in the hot path.

        Performance caveat:
            Allocates 4 tuples + 40 float/int objects per message.
            Not intended for sub-100us strategies.

        Args:
            msg: Parsed BidOfferV3 betterproto message.
            recv_ts: Wall-clock timestamp from ``time.time_ns()``.
            recv_mono_ns: Monotonic timestamp from
                ``time.perf_counter_ns()``.

        Returns:
            Normalized :class:`FullBidOffer` event.
        """
        return FullBidOffer.model_construct(
            symbol=msg.symbol,
            bid_prices=(
                msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
                msg.bid_price2.units + msg.bid_price2.nanos * 1e-9,
                msg.bid_price3.units + msg.bid_price3.nanos * 1e-9,
                msg.bid_price4.units + msg.bid_price4.nanos * 1e-9,
                msg.bid_price5.units + msg.bid_price5.nanos * 1e-9,
                msg.bid_price6.units + msg.bid_price6.nanos * 1e-9,
                msg.bid_price7.units + msg.bid_price7.nanos * 1e-9,
                msg.bid_price8.units + msg.bid_price8.nanos * 1e-9,
                msg.bid_price9.units + msg.bid_price9.nanos * 1e-9,
                msg.bid_price10.units + msg.bid_price10.nanos * 1e-9,
            ),
            ask_prices=(
                msg.ask_price1.units + msg.ask_price1.nanos * 1e-9,
                msg.ask_price2.units + msg.ask_price2.nanos * 1e-9,
                msg.ask_price3.units + msg.ask_price3.nanos * 1e-9,
                msg.ask_price4.units + msg.ask_price4.nanos * 1e-9,
                msg.ask_price5.units + msg.ask_price5.nanos * 1e-9,
                msg.ask_price6.units + msg.ask_price6.nanos * 1e-9,
                msg.ask_price7.units + msg.ask_price7.nanos * 1e-9,
                msg.ask_price8.units + msg.ask_price8.nanos * 1e-9,
                msg.ask_price9.units + msg.ask_price9.nanos * 1e-9,
                msg.ask_price10.units + msg.ask_price10.nanos * 1e-9,
            ),
            bid_volumes=(
                msg.bid_volume1,
                msg.bid_volume2,
                msg.bid_volume3,
                msg.bid_volume4,
                msg.bid_volume5,
                msg.bid_volume6,
                msg.bid_volume7,
                msg.bid_volume8,
                msg.bid_volume9,
                msg.bid_volume10,
            ),
            ask_volumes=(
                msg.ask_volume1,
                msg.ask_volume2,
                msg.ask_volume3,
                msg.ask_volume4,
                msg.ask_volume5,
                msg.ask_volume6,
                msg.ask_volume7,
                msg.ask_volume8,
                msg.ask_volume9,
                msg.ask_volume10,
            ),
            bid_flag=int(msg.bid_flag),
            ask_flag=int(msg.ask_flag),
            recv_ts=recv_ts,
            recv_mono_ns=recv_mono_ns,
        )
