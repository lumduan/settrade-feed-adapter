"""Unit tests for core.events module.

Tests Pydantic event models (BestBidAsk, FullBidOffer) and BidAskFlag enum.
Validates creation, immutability, field constraints, model_construct bypass,
boundary values, hashability, type coercion, and deep immutability.
"""

import pytest
from pydantic import ValidationError

from core.events import BestBidAsk, BidAskFlag, FullBidOffer


# ---------------------------------------------------------------------------
# BidAskFlag Enum Tests
# ---------------------------------------------------------------------------


class TestBidAskFlag:
    """Tests for BidAskFlag IntEnum."""

    def test_enum_values(self) -> None:
        """Enum values match protobuf BidOfferV3BidAskFlag."""
        assert BidAskFlag.UNDEFINED == 0
        assert BidAskFlag.NORMAL == 1
        assert BidAskFlag.ATO == 2
        assert BidAskFlag.ATC == 3

    def test_int_interchangeability(self) -> None:
        """IntEnum values compare equal to raw ints."""
        assert BidAskFlag.NORMAL == 1
        assert 1 == BidAskFlag.NORMAL
        assert int(BidAskFlag.ATC) == 3

    def test_enum_from_int(self) -> None:
        """IntEnum can be constructed from int."""
        flag: BidAskFlag = BidAskFlag(2)
        assert flag == BidAskFlag.ATO
        assert flag.name == "ATO"

    def test_invalid_value_raises(self) -> None:
        """Invalid int raises ValueError."""
        with pytest.raises(ValueError):
            BidAskFlag(99)


# ---------------------------------------------------------------------------
# BestBidAsk Tests
# ---------------------------------------------------------------------------


class TestBestBidAsk:
    """Tests for BestBidAsk Pydantic model."""

    def test_creation_with_valid_data(self) -> None:
        """Model is created with valid data."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.symbol == "AOT"
        assert event.bid == 25.5
        assert event.ask == 26.0
        assert event.bid_vol == 1000
        assert event.ask_vol == 500
        assert event.bid_flag == 1
        assert event.ask_flag == 1
        assert event.recv_ts == 1739500000000000000
        assert event.recv_mono_ns == 123456789

    def test_frozen_immutability(self) -> None:
        """Frozen model rejects attribute assignment."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        with pytest.raises(ValidationError):
            event.bid = 99.0  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected (extra='forbid')."""
        with pytest.raises(ValidationError):
            BestBidAsk(
                symbol="AOT",
                bid=25.5,
                ask=26.0,
                bid_vol=1000,
                ask_vol=500,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_empty_symbol_rejected(self) -> None:
        """Empty symbol string is rejected (min_length=1)."""
        with pytest.raises(ValidationError):
            BestBidAsk(
                symbol="",
                bid=25.5,
                ask=26.0,
                bid_vol=1000,
                ask_vol=500,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
            )

    def test_negative_volume_rejected(self) -> None:
        """Negative volume is rejected (ge=0)."""
        with pytest.raises(ValidationError):
            BestBidAsk(
                symbol="AOT",
                bid=25.5,
                ask=26.0,
                bid_vol=-1,
                ask_vol=500,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
            )

    def test_invalid_flag_rejected(self) -> None:
        """Flag value > 3 is rejected (le=3)."""
        with pytest.raises(ValidationError):
            BestBidAsk(
                symbol="AOT",
                bid=25.5,
                ask=26.0,
                bid_vol=1000,
                ask_vol=500,
                bid_flag=5,  # type: ignore[arg-type]
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
            )

    def test_negative_timestamp_rejected(self) -> None:
        """Negative timestamp is rejected (ge=0)."""
        with pytest.raises(ValidationError):
            BestBidAsk(
                symbol="AOT",
                bid=25.5,
                ask=26.0,
                bid_vol=1000,
                ask_vol=500,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=-1,
                recv_mono_ns=123456789,
            )

    def test_negative_mono_timestamp_rejected(self) -> None:
        """Negative monotonic timestamp is rejected (ge=0)."""
        with pytest.raises(ValidationError):
            BestBidAsk(
                symbol="AOT",
                bid=25.5,
                ask=26.0,
                bid_vol=1000,
                ask_vol=500,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=-1,
            )

    def test_bid_flag_compares_with_enum(self) -> None:
        """int bid_flag compares with BidAskFlag enum."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.ATO,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.bid_flag == BidAskFlag.NORMAL
        assert event.ask_flag == BidAskFlag.ATO

    def test_model_construct_bypasses_validation(self) -> None:
        """model_construct() truly skips validation — empty symbol accepted."""
        event: BestBidAsk = BestBidAsk.model_construct(
            symbol="",  # Would fail with regular construction
            bid=25.5,
            ask=26.0,
            bid_vol=-1,  # Would fail with regular construction
            ask_vol=500,
            bid_flag=99,  # Would fail with regular construction
            ask_flag=1,
            recv_ts=-999,  # Would fail with regular construction
            recv_mono_ns=123456789,
        )
        assert event.symbol == ""
        assert event.bid_vol == -1
        assert event.bid_flag == 99
        assert event.recv_ts == -999

    def test_zero_prices_during_ato(self) -> None:
        """Zero prices are valid during ATO/ATC sessions."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=0.0,
            ask=0.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.ATO,
            ask_flag=BidAskFlag.ATO,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.bid == 0.0
        assert event.ask == 0.0

    def test_negative_prices_allowed(self) -> None:
        """Negative prices are allowed (no ge=0 on price fields).

        Prices have no ge=0 constraint because the Money protobuf
        type supports negative values, and ATO/ATC edge cases may
        produce zero. Defensive constraints on price are deferred
        to strategy-level validation.
        """
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=-1.5,
            ask=-0.5,
            bid_vol=0,
            ask_vol=0,
            bid_flag=BidAskFlag.UNDEFINED,
            ask_flag=BidAskFlag.UNDEFINED,
            recv_ts=0,
            recv_mono_ns=0,
        )
        assert event.bid == -1.5
        assert event.ask == -0.5

    def test_flag_boundary_zero(self) -> None:
        """Flag value 0 (UNDEFINED) is accepted."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.UNDEFINED,
            ask_flag=BidAskFlag.UNDEFINED,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.bid_flag == BidAskFlag.UNDEFINED

    def test_flag_boundary_three(self) -> None:
        """Flag value 3 (ATC) is accepted."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.ATC,
            ask_flag=BidAskFlag.ATC,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.bid_flag == BidAskFlag.ATC
        assert event.ask_flag == BidAskFlag.ATC

    def test_hashable(self) -> None:
        """Frozen model is hashable (can be used as dict key)."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        h: int = hash(event)
        assert isinstance(h, int)
        # Can be used as dict key
        d: dict[BestBidAsk, str] = {event: "test"}
        assert d[event] == "test"

    def test_equality(self) -> None:
        """Two models with same data are equal."""
        kwargs: dict = dict(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        e1: BestBidAsk = BestBidAsk(**kwargs)
        e2: BestBidAsk = BestBidAsk(**kwargs)
        assert e1 == e2
        assert hash(e1) == hash(e2)

    def test_type_coercion_string_to_int(self) -> None:
        """Pydantic v2 coerces compatible types (string → int for volume)."""
        # Pydantic v2 strict mode would reject this, but default mode coerces
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol="1000",  # type: ignore[arg-type]
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert isinstance(event.bid_vol, int)
        assert event.bid_vol == 1000

    def test_bid_greater_than_ask_allowed(self) -> None:
        """bid > ask is allowed — model does not enforce spread logic.

        Strategy-level validation owns spread sanity checks. The event
        model is a transport-layer struct, not a business rule enforcer.
        """
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=30.0,
            ask=25.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.bid > event.ask

    def test_large_timestamp_accepted(self) -> None:
        """Extremely large timestamps are accepted (no upper bound)."""
        large_ts: int = 2**63 - 1
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=large_ts,
            recv_mono_ns=large_ts,
        )
        assert event.recv_ts == large_ts
        assert event.recv_mono_ns == large_ts

    def test_connection_epoch_default_zero(self) -> None:
        """connection_epoch defaults to 0."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.connection_epoch == 0

    def test_connection_epoch_custom_value(self) -> None:
        """connection_epoch accepts custom values."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
            connection_epoch=3,
        )
        assert event.connection_epoch == 3

    def test_connection_epoch_negative_rejected(self) -> None:
        """Negative connection_epoch is rejected (ge=0)."""
        with pytest.raises(ValidationError):
            BestBidAsk(
                symbol="AOT",
                bid=25.5,
                ask=26.0,
                bid_vol=1000,
                ask_vol=500,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
                connection_epoch=-1,
            )

    def test_is_auction_normal_flags(self) -> None:
        """is_auction() returns False for NORMAL flags."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.is_auction() is False

    def test_is_auction_ato_bid_flag(self) -> None:
        """is_auction() returns True when bid_flag is ATO."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.ATO,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.is_auction() is True

    def test_is_auction_atc_ask_flag(self) -> None:
        """is_auction() returns True when ask_flag is ATC."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.ATC,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.is_auction() is True

    def test_is_auction_undefined_flags(self) -> None:
        """is_auction() returns False for UNDEFINED flags."""
        event: BestBidAsk = BestBidAsk(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=BidAskFlag.UNDEFINED,
            ask_flag=BidAskFlag.UNDEFINED,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.is_auction() is False

    def test_is_auction_model_construct_int_values(self) -> None:
        """is_auction() works with raw int values from model_construct()."""
        event: BestBidAsk = BestBidAsk.model_construct(
            symbol="AOT",
            bid=25.5,
            ask=26.0,
            bid_vol=1000,
            ask_vol=500,
            bid_flag=2,  # ATO as raw int
            ask_flag=1,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
            connection_epoch=0,
        )
        assert event.is_auction() is True


# ---------------------------------------------------------------------------
# FullBidOffer Tests
# ---------------------------------------------------------------------------

_TEN_PRICES: tuple[float, ...] = (25.5, 25.25, 25.0, 24.75, 24.5, 0, 0, 0, 0, 0)
_TEN_VOLUMES: tuple[int, ...] = (1000, 500, 200, 100, 50, 0, 0, 0, 0, 0)


class TestFullBidOffer:
    """Tests for FullBidOffer Pydantic model."""

    def test_creation_with_valid_data(self) -> None:
        """Model is created with valid 10-element tuples."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.symbol == "AOT"
        assert len(event.bid_prices) == 10
        assert len(event.ask_prices) == 10
        assert len(event.bid_volumes) == 10
        assert len(event.ask_volumes) == 10
        assert event.bid_prices[0] == 25.5

    def test_frozen_immutability(self) -> None:
        """Frozen model rejects attribute assignment."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        with pytest.raises(ValidationError):
            event.symbol = "PTT"  # type: ignore[misc]

    def test_deep_immutability_tuple(self) -> None:
        """Tuple elements cannot be mutated (TypeError on assignment)."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        with pytest.raises(TypeError):
            event.bid_prices[0] = 99.0  # type: ignore[index]

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            FullBidOffer(
                symbol="AOT",
                bid_prices=_TEN_PRICES,
                ask_prices=_TEN_PRICES,
                bid_volumes=_TEN_VOLUMES,
                ask_volumes=_TEN_VOLUMES,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
                extra="bad",  # type: ignore[call-arg]
            )

    def test_too_few_elements_rejected(self) -> None:
        """Tuples with < 10 elements are rejected (min_length=10)."""
        with pytest.raises(ValidationError):
            FullBidOffer(
                symbol="AOT",
                bid_prices=(1.0, 2.0, 3.0),
                ask_prices=_TEN_PRICES,
                bid_volumes=_TEN_VOLUMES,
                ask_volumes=_TEN_VOLUMES,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
            )

    def test_too_many_elements_rejected(self) -> None:
        """Tuples with > 10 elements are rejected (max_length=10)."""
        with pytest.raises(ValidationError):
            FullBidOffer(
                symbol="AOT",
                bid_prices=tuple(float(i) for i in range(11)),
                ask_prices=_TEN_PRICES,
                bid_volumes=_TEN_VOLUMES,
                ask_volumes=_TEN_VOLUMES,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
            )

    def test_empty_symbol_rejected(self) -> None:
        """Empty symbol is rejected."""
        with pytest.raises(ValidationError):
            FullBidOffer(
                symbol="",
                bid_prices=_TEN_PRICES,
                ask_prices=_TEN_PRICES,
                bid_volumes=_TEN_VOLUMES,
                ask_volumes=_TEN_VOLUMES,
                bid_flag=BidAskFlag.NORMAL,
                ask_flag=BidAskFlag.NORMAL,
                recv_ts=1739500000000000000,
                recv_mono_ns=123456789,
            )

    def test_model_construct_bypasses_validation(self) -> None:
        """model_construct() truly skips validation — 3-element tuple accepted."""
        event: FullBidOffer = FullBidOffer.model_construct(
            symbol="",  # Would fail: min_length=1
            bid_prices=(1.0, 2.0, 3.0),  # Would fail: min_length=10
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=99,  # Would fail: le=3
            ask_flag=1,
            recv_ts=-1,  # Would fail: ge=0
            recv_mono_ns=123456789,
        )
        assert event.symbol == ""
        assert len(event.bid_prices) == 3
        assert event.bid_flag == 99

    def test_dual_timestamps(self) -> None:
        """Both wall-clock and monotonic timestamps are stored."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1000000000,
            recv_mono_ns=2000000000,
        )
        assert event.recv_ts == 1000000000
        assert event.recv_mono_ns == 2000000000

    def test_flag_boundary_values(self) -> None:
        """Flag values 0 (UNDEFINED) and 3 (ATC) are both accepted."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.UNDEFINED,
            ask_flag=BidAskFlag.ATC,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.bid_flag == BidAskFlag.UNDEFINED
        assert event.ask_flag == BidAskFlag.ATC

    def test_hashable_and_equality(self) -> None:
        """Frozen model is hashable and supports equality."""
        kwargs: dict = dict(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        e1: FullBidOffer = FullBidOffer(**kwargs)
        e2: FullBidOffer = FullBidOffer(**kwargs)
        assert e1 == e2
        assert hash(e1) == hash(e2)
        # Can be used as dict key
        d: dict[FullBidOffer, str] = {e1: "test"}
        assert d[e1] == "test"

    def test_connection_epoch_default_zero(self) -> None:
        """connection_epoch defaults to 0."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.connection_epoch == 0

    def test_connection_epoch_custom_value(self) -> None:
        """connection_epoch accepts custom values."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
            connection_epoch=5,
        )
        assert event.connection_epoch == 5

    def test_negative_volume_allowed(self) -> None:
        """Negative volume is allowed — no ge=0 on tuple elements.

        Volume tuples use ``tuple[int, ...]`` without per-element
        constraints. Defensive validation is deferred to strategy layer,
        consistent with BestBidAsk volume fields.
        """
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=(-1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.bid_volumes[0] == -1

    def test_is_auction_normal(self) -> None:
        """is_auction() returns False for NORMAL flags."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.NORMAL,
            ask_flag=BidAskFlag.NORMAL,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.is_auction() is False

    def test_is_auction_ato(self) -> None:
        """is_auction() returns True for ATO flags."""
        event: FullBidOffer = FullBidOffer(
            symbol="AOT",
            bid_prices=_TEN_PRICES,
            ask_prices=_TEN_PRICES,
            bid_volumes=_TEN_VOLUMES,
            ask_volumes=_TEN_VOLUMES,
            bid_flag=BidAskFlag.ATO,
            ask_flag=BidAskFlag.ATO,
            recv_ts=1739500000000000000,
            recv_mono_ns=123456789,
        )
        assert event.is_auction() is True
