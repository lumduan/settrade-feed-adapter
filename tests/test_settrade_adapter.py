"""Unit tests for infra.settrade_adapter module.

All external dependencies (settrade_v2 protobuf, SettradeMQTTClient)
are mocked to allow testing without network access or credentials.
Tests verify protobuf parsing, event creation, error isolation,
rate-limited logging, subscription management, and stats.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.events import BestBidAsk, BidAskFlag, FullBidOffer
from infra.settrade_adapter import (
    BidOfferAdapter,
    BidOfferAdapterConfig,
    _LOG_EVERY_N,
    _LOG_FIRST_N,
    _TOPIC_PREFIX,
    money_to_float,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_money(units: int, nanos: int) -> SimpleNamespace:
    """Create a mock Money-like object with units and nanos."""
    return SimpleNamespace(units=units, nanos=nanos)


def _make_bid_offer_msg(
    symbol: str = "AOT",
    bid_units: int = 25,
    bid_nanos: int = 500_000_000,
    ask_units: int = 26,
    ask_nanos: int = 0,
    bid_volume: int = 1000,
    ask_volume: int = 500,
    bid_flag: int = 1,
    ask_flag: int = 1,
) -> MagicMock:
    """Create a mock BidOfferV3 message with known values.

    Default: bid=25.5, ask=26.0, bid_vol=1000, ask_vol=500, NORMAL flags.
    All 10 levels are populated — levels 2-10 use decreasing prices.
    """
    msg: MagicMock = MagicMock()
    msg.symbol = symbol
    msg.bid_flag = bid_flag
    msg.ask_flag = ask_flag

    # Level 1 (best)
    msg.bid_price1 = _make_money(bid_units, bid_nanos)
    msg.ask_price1 = _make_money(ask_units, ask_nanos)
    msg.bid_volume1 = bid_volume
    msg.ask_volume1 = ask_volume

    # Levels 2-10 (decreasing prices)
    for i in range(2, 11):
        setattr(msg, f"bid_price{i}", _make_money(bid_units - i, 0))
        setattr(msg, f"ask_price{i}", _make_money(ask_units + i, 0))
        setattr(msg, f"bid_volume{i}", bid_volume - (i * 100))
        setattr(msg, f"ask_volume{i}", ask_volume - (i * 50))

    return msg


def _patch_bidofferv3(mock_msg: MagicMock):
    """Return a context manager that patches BidOfferV3 to return mock_msg."""
    instance: MagicMock = MagicMock()
    instance.parse.return_value = mock_msg
    return patch(
        "infra.settrade_adapter.BidOfferV3",
        return_value=instance,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_mqtt_client() -> MagicMock:
    """Return a mocked SettradeMQTTClient."""
    return MagicMock()


@pytest.fixture()
def default_config() -> BidOfferAdapterConfig:
    """Return default adapter config (BestBidAsk mode)."""
    return BidOfferAdapterConfig()


@pytest.fixture()
def full_depth_config() -> BidOfferAdapterConfig:
    """Return full-depth adapter config (FullBidOffer mode)."""
    return BidOfferAdapterConfig(full_depth=True)


@pytest.fixture()
def events() -> list:
    """Return a list to collect events via callback."""
    return []


@pytest.fixture()
def adapter(
    default_config: BidOfferAdapterConfig,
    mock_mqtt_client: MagicMock,
    events: list,
) -> BidOfferAdapter:
    """Return a BidOfferAdapter with default config and list callback."""
    return BidOfferAdapter(
        config=default_config,
        mqtt_client=mock_mqtt_client,
        on_event=events.append,
    )


@pytest.fixture()
def full_depth_adapter(
    full_depth_config: BidOfferAdapterConfig,
    mock_mqtt_client: MagicMock,
    events: list,
) -> BidOfferAdapter:
    """Return a BidOfferAdapter with full_depth=True and list callback."""
    return BidOfferAdapter(
        config=full_depth_config,
        mqtt_client=mock_mqtt_client,
        on_event=events.append,
    )


# ---------------------------------------------------------------------------
# Configuration Tests
# ---------------------------------------------------------------------------


class TestBidOfferAdapterConfig:
    """Tests for BidOfferAdapterConfig Pydantic model."""

    def test_default_full_depth_false(self) -> None:
        """Default full_depth is False."""
        config: BidOfferAdapterConfig = BidOfferAdapterConfig()
        assert config.full_depth is False

    def test_full_depth_true(self) -> None:
        """full_depth can be set to True."""
        config: BidOfferAdapterConfig = BidOfferAdapterConfig(full_depth=True)
        assert config.full_depth is True


# ---------------------------------------------------------------------------
# money_to_float Tests
# ---------------------------------------------------------------------------


class TestMoneyToFloat:
    """Tests for money_to_float utility function."""

    def test_positive_money(self) -> None:
        """Converts positive Money to float."""
        m: SimpleNamespace = SimpleNamespace(units=25, nanos=500_000_000)
        assert money_to_float(m) == 25.5

    def test_zero_money(self) -> None:
        """Converts zero Money to 0.0."""
        m: SimpleNamespace = SimpleNamespace(units=0, nanos=0)
        assert money_to_float(m) == 0.0

    def test_nanos_only(self) -> None:
        """Converts Money with only nanos to float."""
        m: SimpleNamespace = SimpleNamespace(units=0, nanos=750_000_000)
        assert money_to_float(m) == 0.75

    def test_units_only(self) -> None:
        """Converts Money with only units to float."""
        m: SimpleNamespace = SimpleNamespace(units=100, nanos=0)
        assert money_to_float(m) == 100.0

    def test_small_nanos(self) -> None:
        """Converts Money with small nanos value."""
        m: SimpleNamespace = SimpleNamespace(units=10, nanos=10_000_000)
        result: float = money_to_float(m)
        assert abs(result - 10.01) < 1e-9

    def test_large_price(self) -> None:
        """Converts large price correctly."""
        m: SimpleNamespace = SimpleNamespace(units=999, nanos=990_000_000)
        result: float = money_to_float(m)
        assert abs(result - 999.99) < 1e-9

    def test_missing_attribute_raises(self) -> None:
        """Raises AttributeError if Money lacks required fields."""
        with pytest.raises(AttributeError):
            money_to_float(SimpleNamespace(units=10))


# ---------------------------------------------------------------------------
# Subscription Tests
# ---------------------------------------------------------------------------


class TestSubscription:
    """Tests for subscribe/unsubscribe and topic management."""

    def test_subscribe_registers_with_mqtt(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """subscribe() calls mqtt_client.subscribe with correct topic."""
        adapter.subscribe("AOT")
        mock_mqtt_client.subscribe.assert_called_once_with(
            topic="proto/topic/bidofferv3/AOT",
            callback=adapter._on_message,
        )

    def test_subscribe_adds_to_subscribed_symbols(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """subscribe() adds symbol to subscribed_symbols set."""
        adapter.subscribe("AOT")
        assert "AOT" in adapter.subscribed_symbols

    def test_subscribe_multiple_symbols(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Multiple symbols can be subscribed."""
        adapter.subscribe("AOT")
        adapter.subscribe("PTT")
        assert adapter.subscribed_symbols == frozenset({"AOT", "PTT"})
        assert mock_mqtt_client.subscribe.call_count == 2

    def test_subscribe_duplicate_ignored(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Duplicate subscribe for same symbol is ignored."""
        adapter.subscribe("AOT")
        adapter.subscribe("AOT")
        # MQTT subscribe called only once
        assert mock_mqtt_client.subscribe.call_count == 1

    def test_unsubscribe_removes_symbol(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """unsubscribe() removes symbol and calls mqtt unsubscribe."""
        adapter.subscribe("AOT")
        adapter.unsubscribe("AOT")
        assert "AOT" not in adapter.subscribed_symbols
        mock_mqtt_client.unsubscribe.assert_called_once_with(
            topic="proto/topic/bidofferv3/AOT",
        )

    def test_unsubscribe_nonexistent_is_noop(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """Unsubscribing a non-subscribed symbol is safe."""
        adapter.unsubscribe("NONEXISTENT")
        # No exception raised

    def test_unsubscribe_idempotent(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Double unsubscribe calls mqtt.unsubscribe twice but is safe."""
        adapter.subscribe("AOT")
        adapter.unsubscribe("AOT")
        adapter.unsubscribe("AOT")
        # mqtt.unsubscribe called twice (adapter always forwards)
        assert mock_mqtt_client.unsubscribe.call_count == 2
        assert "AOT" not in adapter.subscribed_symbols

    def test_subscribed_symbols_returns_frozenset(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """subscribed_symbols returns an immutable frozenset snapshot."""
        adapter.subscribe("AOT")
        symbols: frozenset[str] = adapter.subscribed_symbols
        assert isinstance(symbols, frozenset)

    def test_subscribe_lowercase_converted_to_uppercase(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Lowercase symbol is automatically converted to uppercase."""
        adapter.subscribe("aot")
        assert "AOT" in adapter.subscribed_symbols
        assert "aot" not in adapter.subscribed_symbols
        mock_mqtt_client.subscribe.assert_called_once_with(
            topic="proto/topic/bidofferv3/AOT",
            callback=adapter._on_message,
        )

    def test_subscribe_mixed_case_converted_to_uppercase(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Mixed-case symbol is automatically converted to uppercase."""
        adapter.subscribe("PtT")
        assert "PTT" in adapter.subscribed_symbols
        assert "PtT" not in adapter.subscribed_symbols
        mock_mqtt_client.subscribe.assert_called_once_with(
            topic="proto/topic/bidofferv3/PTT",
            callback=adapter._on_message,
        )

    def test_subscribe_already_uppercase_unchanged(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Uppercase symbol remains unchanged."""
        adapter.subscribe("VGI")
        assert "VGI" in adapter.subscribed_symbols
        mock_mqtt_client.subscribe.assert_called_once_with(
            topic="proto/topic/bidofferv3/VGI",
            callback=adapter._on_message,
        )

    def test_subscribe_lowercase_duplicate_handled_correctly(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Subscribing lowercase then uppercase is treated as duplicate."""
        adapter.subscribe("aot")
        adapter.subscribe("AOT")
        # Both resolve to "AOT", so only one MQTT subscription
        assert mock_mqtt_client.subscribe.call_count == 1
        assert adapter.subscribed_symbols == frozenset({"AOT"})

    def test_unsubscribe_lowercase_converted_to_uppercase(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Unsubscribe with lowercase symbol works correctly."""
        adapter.subscribe("AOT")
        adapter.unsubscribe("aot")  # Lowercase
        assert "AOT" not in adapter.subscribed_symbols
        mock_mqtt_client.unsubscribe.assert_called_once_with(
            topic="proto/topic/bidofferv3/AOT",
        )

    def test_unsubscribe_mixed_case_converted_to_uppercase(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Unsubscribe with mixed-case symbol works correctly."""
        adapter.subscribe("PTT")
        adapter.unsubscribe("pTt")  # Mixed case
        assert "PTT" not in adapter.subscribed_symbols
        mock_mqtt_client.unsubscribe.assert_called_once_with(
            topic="proto/topic/bidofferv3/PTT",
        )


# ---------------------------------------------------------------------------
# BestBidAsk Parsing Tests
# ---------------------------------------------------------------------------


class TestBestBidAskParsing:
    """Tests for on_message in BestBidAsk mode (default)."""

    def test_parse_produces_best_bid_ask(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """on_message produces a BestBidAsk event."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x01\x02\x03",
            )

        assert len(events) == 1
        event: BestBidAsk = events[0]
        assert isinstance(event, BestBidAsk)
        assert event.symbol == "AOT"
        assert event.bid == 25.5
        assert event.ask == 26.0
        assert event.bid_vol == 1000
        assert event.ask_vol == 500
        assert event.bid_flag == 1
        assert event.ask_flag == 1

    def test_parse_sets_dual_timestamps(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """on_message sets both recv_ts and recv_mono_ns."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x01",
            )

        event: BestBidAsk = events[0]
        assert event.recv_ts > 0
        assert event.recv_mono_ns > 0

    def test_parse_increments_messages_parsed(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """Successful parse increments messages_parsed counter."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x01",
            )

        assert adapter._messages_parsed == 1

    def test_parse_zero_prices_ato(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """Parses zero prices during ATO session correctly."""
        mock_msg: MagicMock = _make_bid_offer_msg(
            bid_units=0,
            bid_nanos=0,
            ask_units=0,
            ask_nanos=0,
            bid_flag=2,
            ask_flag=2,
        )

        with _patch_bidofferv3(mock_msg):
            adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x01",
            )

        event: BestBidAsk = events[0]
        assert event.bid == 0.0
        assert event.ask == 0.0
        assert event.bid_flag == BidAskFlag.ATO

    def test_parse_negative_prices_propagated(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """Negative prices from protobuf are passed through to event."""
        mock_msg: MagicMock = _make_bid_offer_msg(
            bid_units=-1,
            bid_nanos=-500_000_000,
            ask_units=-2,
            ask_nanos=0,
        )

        with _patch_bidofferv3(mock_msg):
            adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x01",
            )

        event: BestBidAsk = events[0]
        assert event.bid == -1.5
        assert event.ask == -2.0

    def test_default_mode_never_produces_full_bid_offer(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """full_depth=False never produces FullBidOffer events."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            for _ in range(5):
                adapter._on_message(
                    topic="proto/topic/bidofferv3/AOT",
                    payload=b"\x01",
                )

        for event in events:
            assert isinstance(event, BestBidAsk)
            assert not isinstance(event, FullBidOffer)


# ---------------------------------------------------------------------------
# FullBidOffer Parsing Tests
# ---------------------------------------------------------------------------


class TestFullBidOfferParsing:
    """Tests for on_message in FullBidOffer mode (full_depth=True)."""

    def test_parse_produces_full_bid_offer(
        self,
        full_depth_adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """on_message produces a FullBidOffer event with 10 levels."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            full_depth_adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x01",
            )

        assert len(events) == 1
        event: FullBidOffer = events[0]
        assert isinstance(event, FullBidOffer)
        assert event.symbol == "AOT"
        assert len(event.bid_prices) == 10
        assert len(event.ask_prices) == 10
        assert len(event.bid_volumes) == 10
        assert len(event.ask_volumes) == 10

    def test_full_depth_level1_matches_best(
        self,
        full_depth_adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """FullBidOffer level 0 matches BestBidAsk values."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            full_depth_adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x01",
            )

        event: FullBidOffer = events[0]
        assert event.bid_prices[0] == 25.5  # Level 1 bid
        assert event.ask_prices[0] == 26.0  # Level 1 ask
        assert event.bid_volumes[0] == 1000
        assert event.ask_volumes[0] == 500

    def test_full_depth_mode_never_produces_best_bid_ask(
        self,
        full_depth_adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """full_depth=True never produces BestBidAsk events."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            for _ in range(5):
                full_depth_adapter._on_message(
                    topic="proto/topic/bidofferv3/AOT",
                    payload=b"\x01",
                )

        for event in events:
            assert isinstance(event, FullBidOffer)
            assert not isinstance(event, BestBidAsk)


# ---------------------------------------------------------------------------
# Error Isolation Tests
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    """Tests for separated parse vs callback error counting."""

    def test_parse_error_increments_parse_errors(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """Protobuf parse failure increments parse_errors only."""
        with patch(
            "infra.settrade_adapter.BidOfferV3"
        ) as MockBidOfferV3:
            instance: MagicMock = MagicMock()
            instance.parse.side_effect = ValueError("bad protobuf")
            MockBidOfferV3.return_value = instance

            adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x00",
            )

        assert adapter._parse_errors == 1
        assert adapter._callback_errors == 0
        assert adapter._messages_parsed == 0

    def test_callback_error_increments_callback_errors(
        self,
        default_config: BidOfferAdapterConfig,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Callback failure increments callback_errors only."""
        def bad_callback(event: BestBidAsk) -> None:
            raise RuntimeError("downstream bug")

        adapter: BidOfferAdapter = BidOfferAdapter(
            config=default_config,
            mqtt_client=mock_mqtt_client,
            on_event=bad_callback,
        )

        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            adapter._on_message(
                topic="proto/topic/bidofferv3/AOT",
                payload=b"\x01",
            )

        assert adapter._callback_errors == 1
        assert adapter._parse_errors == 0
        assert adapter._messages_parsed == 0

    def test_counter_semantics_exactly_one_increment(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """Each message increments exactly one counter."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            for _ in range(3):
                adapter._on_message(
                    topic="proto/topic/bidofferv3/AOT",
                    payload=b"\x01",
                )

        total: int = (
            adapter._messages_parsed
            + adapter._parse_errors
            + adapter._callback_errors
        )
        assert total == 3
        assert adapter._messages_parsed == 3

    def test_mixed_error_success_sequence(
        self,
        default_config: BidOfferAdapterConfig,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """Counters track correctly through mixed error/success sequence."""
        call_count: int = 0

        def sometimes_bad_callback(event: BestBidAsk) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("intermittent bug")

        adapter: BidOfferAdapter = BidOfferAdapter(
            config=default_config,
            mqtt_client=mock_mqtt_client,
            on_event=sometimes_bad_callback,
        )

        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            # Message 1: success
            adapter._on_message(topic="t", payload=b"\x01")
            # Message 2: callback error
            adapter._on_message(topic="t", payload=b"\x01")
            # Message 3: success
            adapter._on_message(topic="t", payload=b"\x01")

        assert adapter._messages_parsed == 2
        assert adapter._callback_errors == 1
        assert adapter._parse_errors == 0

    def test_parse_error_then_success(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """Parse error followed by success: counters independent."""
        good_msg: MagicMock = _make_bid_offer_msg()
        call_count: int = 0

        def parse_side_effect(payload: bytes) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("bad first message")
            return good_msg

        with patch(
            "infra.settrade_adapter.BidOfferV3"
        ) as MockBidOfferV3:
            instance: MagicMock = MagicMock()
            instance.parse.side_effect = parse_side_effect
            MockBidOfferV3.return_value = instance

            # Message 1: parse error
            adapter._on_message(topic="t", payload=b"\x00")
            # Message 2: success
            adapter._on_message(topic="t", payload=b"\x01")

        assert adapter._parse_errors == 1
        assert adapter._messages_parsed == 1
        assert adapter._callback_errors == 0
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Rate-Limited Logging Tests
# ---------------------------------------------------------------------------


class TestRateLimitedLogging:
    """Tests for rate-limited logging in hot path."""

    def test_parse_error_logs_first_n_with_exception(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """First _LOG_FIRST_N parse errors are logged with exception."""
        with (
            patch(
                "infra.settrade_adapter.BidOfferV3"
            ) as MockBidOfferV3,
            patch("infra.settrade_adapter.logger") as mock_logger,
        ):
            instance: MagicMock = MagicMock()
            instance.parse.side_effect = ValueError("bad")
            MockBidOfferV3.return_value = instance

            for _ in range(_LOG_FIRST_N + 5):
                adapter._on_message(
                    topic="proto/topic/bidofferv3/AOT",
                    payload=b"\x00",
                )

        # First N errors should use logger.exception
        exception_calls: int = mock_logger.exception.call_count
        assert exception_calls == _LOG_FIRST_N

    def test_parse_error_logs_every_n_after_threshold(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """After first N, parse errors log every _LOG_EVERY_N-th occurrence."""
        with (
            patch(
                "infra.settrade_adapter.BidOfferV3"
            ) as MockBidOfferV3,
            patch("infra.settrade_adapter.logger") as mock_logger,
        ):
            instance: MagicMock = MagicMock()
            instance.parse.side_effect = ValueError("bad")
            MockBidOfferV3.return_value = instance

            # Generate exactly _LOG_EVERY_N errors
            for _ in range(_LOG_EVERY_N):
                adapter._on_message(
                    topic="proto/topic/bidofferv3/AOT",
                    payload=b"\x00",
                )

        # First N → logger.exception, then at _LOG_EVERY_N → logger.error
        assert mock_logger.exception.call_count == _LOG_FIRST_N
        assert mock_logger.error.call_count == 1  # At _LOG_EVERY_N

    def test_callback_error_logs_first_n_with_exception(
        self,
        default_config: BidOfferAdapterConfig,
        mock_mqtt_client: MagicMock,
    ) -> None:
        """First _LOG_FIRST_N callback errors are logged with exception."""
        adapter: BidOfferAdapter = BidOfferAdapter(
            config=default_config,
            mqtt_client=mock_mqtt_client,
            on_event=Mock(side_effect=RuntimeError("bad callback")),
        )

        mock_msg: MagicMock = _make_bid_offer_msg()

        with (
            _patch_bidofferv3(mock_msg),
            patch("infra.settrade_adapter.logger") as mock_logger,
        ):
            for _ in range(_LOG_FIRST_N + 5):
                adapter._on_message(
                    topic="proto/topic/bidofferv3/AOT",
                    payload=b"\x01",
                )

        # First N errors should use logger.exception
        exception_calls: int = mock_logger.exception.call_count
        assert exception_calls == _LOG_FIRST_N


# ---------------------------------------------------------------------------
# Stats Tests
# ---------------------------------------------------------------------------


class TestStats:
    """Tests for stats() method."""

    def test_stats_returns_expected_keys(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """stats() returns all expected keys."""
        result: dict = adapter.stats()
        expected_keys: set[str] = {
            "subscribed_symbols",
            "messages_parsed",
            "parse_errors",
            "callback_errors",
            "full_depth",
        }
        assert set(result.keys()) == expected_keys

    def test_stats_initial_values(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """stats() returns zero counters initially."""
        result: dict = adapter.stats()
        assert result["messages_parsed"] == 0
        assert result["parse_errors"] == 0
        assert result["callback_errors"] == 0
        assert result["subscribed_symbols"] == []
        assert result["full_depth"] is False

    def test_stats_reflects_subscriptions(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """stats() includes sorted subscribed symbols."""
        adapter.subscribe("PTT")
        adapter.subscribe("AOT")
        result: dict = adapter.stats()
        assert result["subscribed_symbols"] == ["AOT", "PTT"]

    def test_stats_reflects_counters(
        self,
        adapter: BidOfferAdapter,
    ) -> None:
        """stats() reflects current counter values."""
        adapter._messages_parsed = 100
        adapter._parse_errors = 2
        adapter._callback_errors = 1
        result: dict = adapter.stats()
        assert result["messages_parsed"] == 100
        assert result["parse_errors"] == 2
        assert result["callback_errors"] == 1

    def test_stats_full_depth_flag(
        self,
        full_depth_adapter: BidOfferAdapter,
    ) -> None:
        """stats() reflects full_depth config."""
        result: dict = full_depth_adapter.stats()
        assert result["full_depth"] is True


# ---------------------------------------------------------------------------
# End-to-End Tests
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """End-to-end tests: mock MQTT message -> adapter -> event callback."""

    def test_subscribe_and_receive(
        self,
        adapter: BidOfferAdapter,
        mock_mqtt_client: MagicMock,
        events: list,
    ) -> None:
        """Full flow: subscribe -> receive message -> event in callback."""
        adapter.subscribe("AOT")

        # Get the callback that was registered
        subscribe_call = mock_mqtt_client.subscribe.call_args
        registered_callback = subscribe_call.kwargs["callback"]

        # Simulate MQTT message via registered callback
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            registered_callback(
                "proto/topic/bidofferv3/AOT",
                b"\x01\x02\x03",
            )

        assert len(events) == 1
        assert events[0].symbol == "AOT"
        assert events[0].bid == 25.5

    def test_multiple_messages(
        self,
        adapter: BidOfferAdapter,
        events: list,
    ) -> None:
        """Multiple messages produce multiple events."""
        mock_msg: MagicMock = _make_bid_offer_msg()

        with _patch_bidofferv3(mock_msg):
            for _ in range(10):
                adapter._on_message(
                    topic="proto/topic/bidofferv3/AOT",
                    payload=b"\x01",
                )

        assert len(events) == 10
        assert adapter._messages_parsed == 10
