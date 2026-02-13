"""Unit tests for infra.settrade_mqtt module.

All external dependencies (settrade_v2.context.Context, paho.mqtt.client)
are mocked to allow testing without network access or credentials.
"""

import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from infra.settrade_mqtt import (
    ClientState,
    MessageCallback,
    MQTTClientConfig,
    SettradeMQTTClient,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config() -> MQTTClientConfig:
    """Return a valid test configuration."""
    return MQTTClientConfig(
        app_id="test_app",
        app_secret="test_secret",
        app_code="test_code",
        broker_id="test_broker",
    )


@pytest.fixture()
def mock_context() -> MagicMock:
    """Return a mocked settrade_v2 Context."""
    ctx = MagicMock()
    ctx.expired_at = time.time() + 3600  # 1 hour from now
    ctx.token_type = "Bearer"
    ctx.dispatch.return_value.json.return_value = {
        "hosts": ["mqtt-host.example.com"],
        "token": "dispatcher-token-123",
    }
    return ctx


@pytest.fixture()
def mock_mqtt_client() -> MagicMock:
    """Return a mocked paho MQTT Client."""
    client = MagicMock()
    client.connect.return_value = 0
    client.subscribe.return_value = (0, 1)
    client.unsubscribe.return_value = (0, 1)
    return client


@pytest.fixture()
def client_with_mocks(
    config: MQTTClientConfig,
    mock_context: MagicMock,
    mock_mqtt_client: MagicMock,
) -> SettradeMQTTClient:
    """Return a SettradeMQTTClient with mocked internals, in CONNECTED state.

    Patches Context and mqtt.Client so that connect() succeeds without
    network access. Manually triggers on_connect to move to CONNECTED.
    """
    with (
        patch("infra.settrade_mqtt.Context", return_value=mock_context),
        patch("infra.settrade_mqtt.mqtt.Client", return_value=mock_mqtt_client),
    ):
        sut = SettradeMQTTClient(config=config)
        sut.connect()

        # Simulate on_connect callback from broker
        sut._on_connect(
            client=mock_mqtt_client,
            userdata=None,
            flags={},
            rc=0,
        )

    return sut


# ---------------------------------------------------------------------------
# Configuration Tests
# ---------------------------------------------------------------------------


class TestMQTTClientConfig:
    """Tests for MQTTClientConfig Pydantic model."""

    def test_defaults(self, config: MQTTClientConfig) -> None:
        """Default values are applied correctly."""
        assert config.port == 443
        assert config.keepalive == 30
        assert config.reconnect_min_delay == 1.0
        assert config.reconnect_max_delay == 30.0
        assert config.token_refresh_before_exp_seconds == 100

    def test_custom_values(self) -> None:
        """Custom values override defaults."""
        cfg: MQTTClientConfig = MQTTClientConfig(
            app_id="a",
            app_secret="s",
            app_code="c",
            broker_id="b",
            port=8883,
            keepalive=60,
            reconnect_min_delay=2.0,
            reconnect_max_delay=60.0,
            token_refresh_before_exp_seconds=200,
        )
        assert cfg.port == 8883
        assert cfg.keepalive == 60
        assert cfg.reconnect_min_delay == 2.0
        assert cfg.reconnect_max_delay == 60.0
        assert cfg.token_refresh_before_exp_seconds == 200

    def test_keepalive_min_constraint(self) -> None:
        """Keepalive must be >= 5."""
        with pytest.raises(Exception):
            MQTTClientConfig(
                app_id="a",
                app_secret="s",
                app_code="c",
                broker_id="b",
                keepalive=2,
            )

    def test_keepalive_max_constraint(self) -> None:
        """Keepalive must be <= 300."""
        with pytest.raises(Exception):
            MQTTClientConfig(
                app_id="a",
                app_secret="s",
                app_code="c",
                broker_id="b",
                keepalive=999,
            )

    def test_reconnect_min_delay_constraint(self) -> None:
        """Reconnect min delay must be >= 0.1."""
        with pytest.raises(Exception):
            MQTTClientConfig(
                app_id="a",
                app_secret="s",
                app_code="c",
                broker_id="b",
                reconnect_min_delay=0.01,
            )

    def test_token_refresh_constraint(self) -> None:
        """Token refresh threshold must be >= 10."""
        with pytest.raises(Exception):
            MQTTClientConfig(
                app_id="a",
                app_secret="s",
                app_code="c",
                broker_id="b",
                token_refresh_before_exp_seconds=5,
            )


# ---------------------------------------------------------------------------
# State Machine Tests
# ---------------------------------------------------------------------------


class TestStateMachine:
    """Tests for ClientState transitions."""

    def test_initial_state(self, config: MQTTClientConfig) -> None:
        """Client starts in INIT state."""
        sut = SettradeMQTTClient(config=config)
        assert sut.state == ClientState.INIT
        assert sut.connected is False

    def test_connect_transitions_to_connecting(
        self,
        config: MQTTClientConfig,
        mock_context: MagicMock,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """connect() moves state to CONNECTING."""
        with (
            patch("infra.settrade_mqtt.Context", return_value=mock_context),
            patch(
                "infra.settrade_mqtt.mqtt.Client",
                return_value=mock_mqtt_client,
            ),
        ):
            sut = SettradeMQTTClient(config=config)
            sut.connect()
            # State is CONNECTING until on_connect fires
            assert sut.state == ClientState.CONNECTING

    def test_on_connect_success_transitions_to_connected(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """on_connect with rc=0 transitions to CONNECTED."""
        assert client_with_mocks.state == ClientState.CONNECTED
        assert client_with_mocks.connected is True

    def test_on_disconnect_transitions_to_reconnecting(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Unexpected disconnect triggers RECONNECTING state."""
        # Patch _reconnect_loop to prevent actual reconnect
        with patch.object(client_with_mocks, "_reconnect_loop"):
            client_with_mocks._on_disconnect(
                client=MagicMock(),
                userdata=None,
                rc=1,
            )
        assert client_with_mocks.state == ClientState.RECONNECTING

    def test_shutdown_transitions_to_shutdown(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """shutdown() transitions to SHUTDOWN."""
        client_with_mocks.shutdown()
        assert client_with_mocks.state == ClientState.SHUTDOWN

    def test_shutdown_is_idempotent(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Multiple shutdown() calls are safe."""
        client_with_mocks.shutdown()
        client_with_mocks.shutdown()
        assert client_with_mocks.state == ClientState.SHUTDOWN

    def test_connect_rejects_non_init_state(
        self,
        config: MQTTClientConfig,
    ) -> None:
        """connect() raises if not in INIT state."""
        sut = SettradeMQTTClient(config=config)
        sut._state = ClientState.CONNECTED
        with pytest.raises(RuntimeError, match="Cannot connect"):
            sut.connect()


# ---------------------------------------------------------------------------
# Subscription Tests
# ---------------------------------------------------------------------------


class TestSubscription:
    """Tests for subscribe/unsubscribe and replay logic."""

    def test_subscribe_registers_callback(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """subscribe() adds callback to subscriptions dict."""
        cb: MessageCallback = Mock()
        client_with_mocks.subscribe(
            topic="proto/topic/bidofferv3/AOT",
            callback=cb,
        )
        assert "proto/topic/bidofferv3/AOT" in client_with_mocks._subscriptions
        assert cb in client_with_mocks._subscriptions["proto/topic/bidofferv3/AOT"]

    def test_subscribe_sends_mqtt_subscribe_when_connected(
        self,
        client_with_mocks: SettradeMQTTClient,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """subscribe() calls client.subscribe() when connected."""
        cb: MessageCallback = Mock()
        client_with_mocks.subscribe(
            topic="proto/topic/bidofferv3/AOT",
            callback=cb,
        )
        mock_mqtt_client.subscribe.assert_called_with(
            topic="proto/topic/bidofferv3/AOT",
        )

    def test_subscribe_multiple_callbacks_same_topic(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Multiple callbacks can be registered for the same topic."""
        cb1: MessageCallback = Mock()
        cb2: MessageCallback = Mock()
        topic: str = "proto/topic/bidofferv3/AOT"
        client_with_mocks.subscribe(topic=topic, callback=cb1)
        client_with_mocks.subscribe(topic=topic, callback=cb2)
        assert len(client_with_mocks._subscriptions[topic]) == 2

    def test_subscribe_during_reconnecting(
        self,
        config: MQTTClientConfig,
    ) -> None:
        """Subscribe during RECONNECTING stores in source-of-truth."""
        sut = SettradeMQTTClient(config=config)
        sut._state = ClientState.RECONNECTING
        cb: MessageCallback = Mock()
        sut.subscribe(topic="proto/topic/bidofferv3/AOT", callback=cb)
        assert "proto/topic/bidofferv3/AOT" in sut._subscriptions

    def test_unsubscribe_removes_topic(
        self,
        client_with_mocks: SettradeMQTTClient,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """unsubscribe() removes topic from subscriptions dict."""
        cb: MessageCallback = Mock()
        topic: str = "proto/topic/bidofferv3/AOT"
        client_with_mocks.subscribe(topic=topic, callback=cb)
        client_with_mocks.unsubscribe(topic=topic)
        assert topic not in client_with_mocks._subscriptions
        mock_mqtt_client.unsubscribe.assert_called_with(topic=topic)

    def test_unsubscribe_nonexistent_topic(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """unsubscribe() for nonexistent topic is a no-op."""
        client_with_mocks.unsubscribe(topic="nonexistent")
        # No exception raised

    def test_replay_subscriptions_on_connect(
        self,
        config: MQTTClientConfig,
        mock_context: MagicMock,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """on_connect replays all subscriptions from source of truth."""
        with (
            patch("infra.settrade_mqtt.Context", return_value=mock_context),
            patch(
                "infra.settrade_mqtt.mqtt.Client",
                return_value=mock_mqtt_client,
            ),
        ):
            sut = SettradeMQTTClient(config=config)
            sut.connect()

            # Pre-register subscriptions
            sut._subscriptions = {
                "topic/a": [Mock()],
                "topic/b": [Mock()],
            }

            # Simulate on_connect — should replay both
            mock_mqtt_client.subscribe.reset_mock()
            sut._on_connect(
                client=mock_mqtt_client,
                userdata=None,
                flags={},
                rc=0,
            )

            subscribe_calls: list = [
                call.kwargs.get("topic") or call.args[0]
                for call in mock_mqtt_client.subscribe.call_args_list
            ]
            assert "topic/a" in subscribe_calls
            assert "topic/b" in subscribe_calls


# ---------------------------------------------------------------------------
# Message Dispatch Tests
# ---------------------------------------------------------------------------


class TestMessageDispatch:
    """Tests for on_message hot path."""

    def test_dispatch_to_correct_callback(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Messages are dispatched to callbacks matching the topic."""
        cb: Mock = Mock()
        topic: str = "proto/topic/bidofferv3/AOT"
        client_with_mocks.subscribe(topic=topic, callback=cb)

        msg: MagicMock = MagicMock()
        msg.topic = topic
        msg.payload = b"\x01\x02\x03"

        client_with_mocks._on_message(
            client=MagicMock(),
            userdata=None,
            msg=msg,
            generation=client_with_mocks._client_generation,
        )

        cb.assert_called_once_with(topic, b"\x01\x02\x03")

    def test_dispatch_multiple_callbacks(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """All callbacks for a topic receive the message."""
        cb1: Mock = Mock()
        cb2: Mock = Mock()
        topic: str = "proto/topic/bidofferv3/AOT"
        client_with_mocks.subscribe(topic=topic, callback=cb1)
        client_with_mocks.subscribe(topic=topic, callback=cb2)

        msg: MagicMock = MagicMock()
        msg.topic = topic
        msg.payload = b"\x01"

        client_with_mocks._on_message(
            client=MagicMock(),
            userdata=None,
            msg=msg,
            generation=client_with_mocks._client_generation,
        )

        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_dispatch_unknown_topic_is_noop(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Messages for unsubscribed topics are silently ignored."""
        msg: MagicMock = MagicMock()
        msg.topic = "unknown/topic"
        msg.payload = b"\x01"

        # Should not raise
        client_with_mocks._on_message(
            client=MagicMock(),
            userdata=None,
            msg=msg,
            generation=client_with_mocks._client_generation,
        )

    def test_callback_isolation(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """A failing callback does not prevent other callbacks from running."""
        cb_bad: Mock = Mock(side_effect=ValueError("boom"))
        cb_good: Mock = Mock()
        topic: str = "proto/topic/bidofferv3/AOT"
        client_with_mocks.subscribe(topic=topic, callback=cb_bad)
        client_with_mocks.subscribe(topic=topic, callback=cb_good)

        msg: MagicMock = MagicMock()
        msg.topic = topic
        msg.payload = b"\x01"

        client_with_mocks._on_message(
            client=MagicMock(),
            userdata=None,
            msg=msg,
            generation=client_with_mocks._client_generation,
        )

        # Bad callback was called and raised
        cb_bad.assert_called_once()
        # Good callback still ran
        cb_good.assert_called_once()

    def test_callback_error_increments_counter(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Callback exceptions increment _callback_errors counter."""
        cb: Mock = Mock(side_effect=RuntimeError("fail"))
        topic: str = "proto/topic/bidofferv3/AOT"
        client_with_mocks.subscribe(topic=topic, callback=cb)

        msg: MagicMock = MagicMock()
        msg.topic = topic
        msg.payload = b"\x01"

        client_with_mocks._on_message(
            client=MagicMock(),
            userdata=None,
            msg=msg,
            generation=client_with_mocks._client_generation,
        )

        assert client_with_mocks._callback_errors == 1

    def test_stale_generation_rejected(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Messages from old client generation are rejected."""
        cb: Mock = Mock()
        topic: str = "proto/topic/bidofferv3/AOT"
        client_with_mocks.subscribe(topic=topic, callback=cb)

        msg: MagicMock = MagicMock()
        msg.topic = topic
        msg.payload = b"\x01"

        # Use stale generation (current - 1)
        stale_gen: int = client_with_mocks._client_generation - 1
        client_with_mocks._on_message(
            client=MagicMock(),
            userdata=None,
            msg=msg,
            generation=stale_gen,
        )

        cb.assert_not_called()

    def test_messages_received_counter(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """_messages_received increments on each valid message."""
        topic: str = "proto/topic/bidofferv3/AOT"
        client_with_mocks.subscribe(topic=topic, callback=Mock())

        msg: MagicMock = MagicMock()
        msg.topic = topic
        msg.payload = b"\x01"

        gen: int = client_with_mocks._client_generation
        for _ in range(5):
            client_with_mocks._on_message(
                client=MagicMock(),
                userdata=None,
                msg=msg,
                generation=gen,
            )

        assert client_with_mocks._messages_received == 5


# ---------------------------------------------------------------------------
# Reconnect Tests
# ---------------------------------------------------------------------------


class TestReconnect:
    """Tests for reconnection logic."""

    def test_schedule_reconnect_sets_state(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """_schedule_reconnect sets state to RECONNECTING."""
        with patch.object(client_with_mocks, "_reconnect_loop"):
            client_with_mocks._schedule_reconnect()
        assert client_with_mocks.state == ClientState.RECONNECTING

    def test_schedule_reconnect_prevents_duplicates(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Only one reconnect thread is spawned for concurrent calls."""
        thread_count: int = 0
        original_start = threading.Thread.start

        def counting_start(self_thread: threading.Thread) -> None:
            nonlocal thread_count
            if self_thread.name == "mqtt-reconnect":
                thread_count += 1
            original_start(self_thread)

        with (
            patch.object(client_with_mocks, "_reconnect_loop"),
            patch.object(threading.Thread, "start", counting_start),
        ):
            client_with_mocks._schedule_reconnect()
            client_with_mocks._schedule_reconnect()
            client_with_mocks._schedule_reconnect()

        assert thread_count == 1

    def test_schedule_reconnect_blocked_after_shutdown(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Reconnect is not scheduled after shutdown."""
        client_with_mocks.shutdown()
        with patch.object(threading.Thread, "start") as mock_start:
            client_with_mocks._schedule_reconnect()
        mock_start.assert_not_called()

    def test_on_disconnect_clean_does_not_reconnect(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Clean disconnect (rc=0) does not trigger reconnect."""
        with patch.object(
            client_with_mocks, "_schedule_reconnect"
        ) as mock_sched:
            client_with_mocks._on_disconnect(
                client=MagicMock(),
                userdata=None,
                rc=0,
            )
        mock_sched.assert_not_called()

    def test_on_disconnect_unexpected_triggers_reconnect(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Unexpected disconnect (rc!=0) triggers reconnect."""
        with patch.object(
            client_with_mocks, "_schedule_reconnect"
        ) as mock_sched:
            client_with_mocks._on_disconnect(
                client=MagicMock(),
                userdata=None,
                rc=1,
            )
        mock_sched.assert_called_once()

    def test_on_connect_failure_triggers_reconnect(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """on_connect with rc!=0 triggers reconnect."""
        with patch.object(
            client_with_mocks, "_schedule_reconnect"
        ) as mock_sched:
            client_with_mocks._on_connect(
                client=MagicMock(),
                userdata=None,
                flags={},
                rc=5,
            )
        mock_sched.assert_called_once()

    def test_reconnect_loop_clears_flag_on_success(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """_reconnect_loop clears _reconnecting flag after success."""
        client_with_mocks._reconnecting = True

        with (
            patch.object(client_with_mocks, "_fetch_host_token"),
            patch.object(
                client_with_mocks,
                "_create_mqtt_client",
                return_value=MagicMock(),
            ),
        ):
            client_with_mocks._reconnect_loop()

        assert client_with_mocks._reconnecting is False

    def test_reconnect_loop_clears_flag_on_shutdown(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """_reconnect_loop clears _reconnecting flag on shutdown."""
        client_with_mocks._reconnecting = True
        client_with_mocks._shutdown_event.set()

        client_with_mocks._reconnect_loop()

        assert client_with_mocks._reconnecting is False

    def test_reconnect_loop_retries_on_failure(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """_reconnect_loop retries with backoff on failure."""
        attempt_count: int = 0

        def failing_fetch() -> None:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise ConnectionError("simulated failure")

        client_with_mocks._reconnecting = True
        client_with_mocks._config.reconnect_min_delay = 0.01
        client_with_mocks._config.reconnect_max_delay = 0.05

        with (
            patch.object(
                client_with_mocks,
                "_fetch_host_token",
                side_effect=failing_fetch,
            ),
            patch.object(
                client_with_mocks,
                "_create_mqtt_client",
                return_value=MagicMock(),
            ),
        ):
            client_with_mocks._reconnect_loop()

        assert attempt_count == 3
        assert client_with_mocks._reconnect_count > 0

    def test_reconnect_increments_count(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Successful reconnect increments _reconnect_count."""
        initial: int = client_with_mocks._reconnect_count
        client_with_mocks._reconnecting = True

        with (
            patch.object(client_with_mocks, "_fetch_host_token"),
            patch.object(
                client_with_mocks,
                "_create_mqtt_client",
                return_value=MagicMock(),
            ),
        ):
            client_with_mocks._reconnect_loop()

        assert client_with_mocks._reconnect_count == initial + 1

    def test_on_disconnect_ignored_after_shutdown(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """on_disconnect during SHUTDOWN does not trigger reconnect."""
        client_with_mocks.shutdown()
        with patch.object(
            client_with_mocks, "_schedule_reconnect"
        ) as mock_sched:
            client_with_mocks._on_disconnect(
                client=MagicMock(),
                userdata=None,
                rc=1,
            )
        mock_sched.assert_not_called()


# ---------------------------------------------------------------------------
# Token Refresh Tests
# ---------------------------------------------------------------------------


class TestTokenRefresh:
    """Tests for token refresh and expired_at sync."""

    def test_fetch_host_token_syncs_expired_at(
        self,
        config: MQTTClientConfig,
        mock_context: MagicMock,
    ) -> None:
        """_fetch_host_token updates _expired_at from Context."""
        sut = SettradeMQTTClient(config=config)
        sut._ctx = mock_context

        new_expiry: float = time.time() + 7200
        mock_context.expired_at = new_expiry

        sut._fetch_host_token()

        assert sut._expired_at == new_expiry

    def test_fetch_host_token_sets_host_and_token(
        self,
        config: MQTTClientConfig,
        mock_context: MagicMock,
    ) -> None:
        """_fetch_host_token sets _host and _token from response."""
        sut = SettradeMQTTClient(config=config)
        sut._ctx = mock_context

        sut._fetch_host_token()

        assert sut._host == "mqtt-host.example.com"
        assert sut._token == "dispatcher-token-123"

    def test_fetch_host_token_raises_without_login(
        self,
        config: MQTTClientConfig,
    ) -> None:
        """_fetch_host_token raises if not logged in."""
        sut = SettradeMQTTClient(config=config)
        with pytest.raises(RuntimeError, match="Must login"):
            sut._fetch_host_token()

    def test_fetch_host_token_raises_on_empty_hosts(
        self,
        config: MQTTClientConfig,
        mock_context: MagicMock,
    ) -> None:
        """_fetch_host_token raises if no hosts returned."""
        mock_context.dispatch.return_value.json.return_value = {
            "hosts": [],
            "token": "t",
        }
        sut = SettradeMQTTClient(config=config)
        sut._ctx = mock_context
        with pytest.raises(ValueError, match="No MQTT hosts"):
            sut._fetch_host_token()

    def test_token_refresh_triggers_reconnect(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """Token refresh timer triggers reconnect when token near expiry."""
        # Set expired_at to now (already expired)
        client_with_mocks._expired_at = time.time() - 1

        def shutdown_after_call() -> None:
            # Signal shutdown after first _schedule_reconnect call
            # so the while loop exits after one iteration
            client_with_mocks._shutdown_event.set()

        with patch.object(
            client_with_mocks,
            "_schedule_reconnect",
            side_effect=shutdown_after_call,
        ) as mock_sched:
            client_with_mocks._token_refresh_check()

        mock_sched.assert_called_once()


# ---------------------------------------------------------------------------
# Statistics Tests
# ---------------------------------------------------------------------------


class TestStats:
    """Tests for stats() method."""

    def test_stats_returns_expected_keys(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """stats() returns all expected keys."""
        result: dict = client_with_mocks.stats()
        expected_keys: set[str] = {
            "state",
            "connected",
            "messages_received",
            "callback_errors",
            "reconnect_count",
            "last_connect_ts",
            "last_disconnect_ts",
        }
        assert set(result.keys()) == expected_keys

    def test_stats_reflects_connected_state(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """stats() shows connected=True when in CONNECTED state."""
        result: dict = client_with_mocks.stats()
        assert result["state"] == "CONNECTED"
        assert result["connected"] is True

    def test_stats_reflects_counters(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """stats() reflects current counter values."""
        client_with_mocks._messages_received = 42
        client_with_mocks._callback_errors = 3
        client_with_mocks._reconnect_count = 1
        result: dict = client_with_mocks.stats()
        assert result["messages_received"] == 42
        assert result["callback_errors"] == 3
        assert result["reconnect_count"] == 1

    def test_stats_reflects_timestamps(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """stats() includes connect/disconnect timestamps."""
        result: dict = client_with_mocks.stats()
        assert result["last_connect_ts"] > 0


# ---------------------------------------------------------------------------
# Client Generation Tests
# ---------------------------------------------------------------------------


class TestClientGeneration:
    """Tests for client generation ID mechanism."""

    def test_generation_increments_on_create(
        self,
        config: MQTTClientConfig,
        mock_context: MagicMock,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """_create_mqtt_client increments _client_generation."""
        with patch(
            "infra.settrade_mqtt.mqtt.Client",
            return_value=mock_mqtt_client,
        ):
            sut = SettradeMQTTClient(config=config)
            sut._token = "test-token"
            sut._token_type = "Bearer"

            gen_before: int = sut._client_generation
            sut._create_mqtt_client()
            assert sut._client_generation == gen_before + 1

    def test_successive_creates_increment_sequentially(
        self,
        config: MQTTClientConfig,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Multiple _create_mqtt_client calls produce sequential IDs."""
        with patch(
            "infra.settrade_mqtt.mqtt.Client",
            return_value=mock_mqtt_client,
        ):
            sut = SettradeMQTTClient(config=config)
            sut._token = "test-token"
            sut._token_type = "Bearer"

            sut._create_mqtt_client()
            gen1: int = sut._client_generation
            sut._client = mock_mqtt_client  # Reset for next create
            sut._create_mqtt_client()
            gen2: int = sut._client_generation

            assert gen2 == gen1 + 1


# ---------------------------------------------------------------------------
# Shutdown Tests
# ---------------------------------------------------------------------------


class TestShutdown:
    """Tests for graceful shutdown."""

    def test_shutdown_calls_loop_stop_then_disconnect(
        self,
        client_with_mocks: SettradeMQTTClient,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """shutdown() calls loop_stop() before disconnect()."""
        call_order: list[str] = []
        mock_mqtt_client.loop_stop.side_effect = (
            lambda: call_order.append("loop_stop")
        )
        mock_mqtt_client.disconnect.side_effect = (
            lambda: call_order.append("disconnect")
        )

        client_with_mocks.shutdown()

        assert call_order == ["loop_stop", "disconnect"]

    def test_shutdown_sets_event(
        self,
        client_with_mocks: SettradeMQTTClient,
    ) -> None:
        """shutdown() sets the shutdown event."""
        client_with_mocks.shutdown()
        assert client_with_mocks._shutdown_event.is_set()

    def test_shutdown_tolerates_loop_stop_exception(
        self,
        client_with_mocks: SettradeMQTTClient,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """shutdown() handles loop_stop exception gracefully."""
        mock_mqtt_client.loop_stop.side_effect = OSError("socket error")
        client_with_mocks.shutdown()
        assert client_with_mocks.state == ClientState.SHUTDOWN

    def test_shutdown_tolerates_disconnect_exception(
        self,
        client_with_mocks: SettradeMQTTClient,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """shutdown() handles disconnect exception gracefully."""
        mock_mqtt_client.disconnect.side_effect = OSError("socket error")
        client_with_mocks.shutdown()
        assert client_with_mocks.state == ClientState.SHUTDOWN
