"""Normalized event models for Settrade market data feeds.

This module defines the domain event types that adapters produce
and strategy consumers receive. All models are Pydantic-based with
``frozen=True`` for immutability and thread safety.

Architecture note:
    These models are constructed in the MQTT IO thread hot path.
    All hot-path construction MUST use ``model_construct()`` to skip
    Pydantic validation. Regular construction (with validation) is
    safe for tests, examples, and untrusted external data.

Timestamp convention:
    All events carry dual timestamps:
    - ``recv_ts``: ``time.time_ns()`` wall clock — for correlating
      with external timestamps (exchange time, logs). Subject to
      NTP adjustment.
    - ``recv_mono_ns``: ``time.perf_counter_ns()`` monotonic — for
      latency measurement. Never goes backwards.

Connection epoch:
    Each event carries a ``connection_epoch`` field (default 0) that
    increments on every MQTT reconnect. Strategy code can detect
    reconnects by comparing ``event.connection_epoch != last_epoch``
    and take appropriate action (clear state, cancel orders, etc.).

Float precision contract:
    Prices are stored as IEEE 754 ``float`` (15-17 significant digits).
    Downstream strategy code MUST compare prices using tolerance
    (e.g., ``abs(a - b) < 1e-9``), not exact equality. See the
    Phase 2 plan for the full float precision contract.

Example:
    >>> from core.events import BestBidAsk, BidAskFlag
    >>> event = BestBidAsk(
    ...     symbol="AOT",
    ...     bid=25.5,
    ...     ask=26.0,
    ...     bid_vol=1000,
    ...     ask_vol=500,
    ...     bid_flag=BidAskFlag.NORMAL,
    ...     ask_flag=BidAskFlag.NORMAL,
    ...     recv_ts=1739500000000000000,
    ...     recv_mono_ns=123456789,
    ... )
    >>> event.symbol
    'AOT'
    >>> event.bid_flag == BidAskFlag.NORMAL
    True
    >>> event.is_auction()
    False
    >>> event.connection_epoch
    0
"""

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BidAskFlag(IntEnum):
    """Market session flag for bid/ask prices.

    Mirrors ``BidOfferV3BidAskFlag`` from the Settrade protobuf schema.
    Using IntEnum allows direct comparison with ``int`` values and
    seamless use in Pydantic models with automatic int-to-enum coercion.

    Attributes:
        UNDEFINED: Default/unknown flag (protobuf default value).
        NORMAL: Normal continuous trading session.
        ATO: At-The-Opening session. Prices are zero during ATO.
        ATC: At-The-Close session. Prices are zero during ATC.

    Example:
        >>> BidAskFlag.NORMAL == 1
        True
        >>> int(BidAskFlag.ATO)
        2
    """

    UNDEFINED = 0
    NORMAL = 1
    ATO = 2
    ATC = 3


# ---------------------------------------------------------------------------
# Shared auction detection logic
# ---------------------------------------------------------------------------

_AUCTION_FLAGS: tuple[BidAskFlag, ...] = (BidAskFlag.ATO, BidAskFlag.ATC)
"""Flags that indicate an auction period. Used by ``is_auction()``."""


def _is_auction(bid_flag: int, ask_flag: int) -> bool:
    """Shared auction detection logic for event models.

    Returns ``True`` if either bid or ask flag indicates an auction
    session (At-The-Opening or At-The-Close).

    Uses :data:`_AUCTION_FLAGS` tuple for clarity — no magic numbers.
    Works with both ``BidAskFlag`` enum values and raw ``int`` values
    (important for ``model_construct()`` hot path where validation is
    skipped and flags may be stored as plain ``int``).

    Args:
        bid_flag: Bid session flag value.
        ask_flag: Ask session flag value.

    Returns:
        ``True`` if either flag is ATO (2) or ATC (3).
    """
    return bid_flag in _AUCTION_FLAGS or ask_flag in _AUCTION_FLAGS


# ---------------------------------------------------------------------------
# Event Models
# ---------------------------------------------------------------------------


class BestBidAsk(BaseModel):
    """Top-of-book bid/ask snapshot from a BidOfferV3 message.

    Contains only the best (level 1) bid and ask price/volume,
    optimised for minimal allocation in the hot path. This is the
    default event type produced by ``BidOfferAdapter``.

    Hot-path construction:
        Use ``BestBidAsk.model_construct(...)`` in the adapter to
        skip Pydantic validation. Regular ``BestBidAsk(...)``
        construction is safe for tests and external data.

    Note on ``model_construct()`` and flag types:
        When using ``model_construct()``, Pydantic validation is
        skipped, so ``bid_flag``/``ask_flag`` may be stored as plain
        ``int`` rather than ``BidAskFlag``. This is safe because
        ``IntEnum`` comparison works with ``int`` values
        (``2 in (BidAskFlag.ATO, BidAskFlag.ATC)`` is ``True``).
        Regular construction auto-coerces ``int`` → ``BidAskFlag``.

    Attributes:
        symbol: Stock symbol (e.g., ``"AOT"``). Non-empty.
        bid: Best bid price converted from ``Money(units, nanos)``
            via ``units + nanos * 1e-9``. Zero during ATO/ATC.
        ask: Best ask price. Same conversion as ``bid``.
        bid_vol: Best bid volume (number of shares). Non-negative.
        ask_vol: Best ask volume (number of shares). Non-negative.
        bid_flag: Bid session flag. See :class:`BidAskFlag`.
        ask_flag: Ask session flag. See :class:`BidAskFlag`.
        recv_ts: Wall-clock timestamp (``time.time_ns()``) captured
            at MQTT message receive. For correlation with external
            timestamps. Subject to NTP adjustment. Non-negative.
        recv_mono_ns: Monotonic timestamp (``time.perf_counter_ns()``)
            captured at MQTT message receive. For latency measurement.
            Never goes backwards. Non-negative.
        connection_epoch: Reconnect version counter. 0 = initial
            connection. Increments on each MQTT reconnect after
            subscription replay.

    Example:
        >>> event = BestBidAsk(
        ...     symbol="AOT",
        ...     bid=25.5,
        ...     ask=26.0,
        ...     bid_vol=1000,
        ...     ask_vol=500,
        ...     bid_flag=BidAskFlag.NORMAL,
        ...     ask_flag=BidAskFlag.NORMAL,
        ...     recv_ts=1739500000000000000,
        ...     recv_mono_ns=123456789,
        ... )
        >>> event.bid
        25.5
        >>> event.bid_flag == BidAskFlag.NORMAL
        True
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, description="Stock symbol (e.g., 'AOT')")
    bid: float = Field(
        description="Best bid price (units + nanos * 1e-9). Zero during ATO/ATC.",
    )
    ask: float = Field(
        description="Best ask price (units + nanos * 1e-9). Zero during ATO/ATC.",
    )
    bid_vol: int = Field(ge=0, description="Best bid volume (number of shares)")
    ask_vol: int = Field(ge=0, description="Best ask volume (number of shares)")
    bid_flag: BidAskFlag = Field(
        description="Bid session flag. See BidAskFlag enum.",
    )
    ask_flag: BidAskFlag = Field(
        description="Ask session flag. See BidAskFlag enum.",
    )
    recv_ts: int = Field(
        ge=0,
        description="Wall-clock receive timestamp (time.time_ns())",
    )
    recv_mono_ns: int = Field(
        ge=0,
        description="Monotonic receive timestamp (time.perf_counter_ns())",
    )
    connection_epoch: int = Field(
        default=0,
        ge=0,
        description=(
            "Reconnect version counter. Increments on each MQTT reconnect "
            "after subscription replay. 0 = initial connection."
        ),
    )

    def is_auction(self) -> bool:
        """Check if this event occurred during an auction period (ATO/ATC).

        Uses :class:`BidAskFlag` enum for clarity — no magic numbers.
        Returns ``True`` if either bid or ask flag indicates an auction
        session (At-The-Opening or At-The-Close).

        Returns:
            ``True`` if bid_flag or ask_flag is ATO (2) or ATC (3).

        Example:
            >>> event = BestBidAsk(
            ...     symbol="AOT", bid=0.0, ask=0.0,
            ...     bid_vol=0, ask_vol=0,
            ...     bid_flag=BidAskFlag.ATO, ask_flag=BidAskFlag.ATO,
            ...     recv_ts=0, recv_mono_ns=0,
            ... )
            >>> event.is_auction()
            True
        """
        return _is_auction(self.bid_flag, self.ask_flag)


class FullBidOffer(BaseModel):
    """Full 10-level bid/offer depth book from a BidOfferV3 message.

    Contains all 10 levels of bid and ask prices and volumes.
    Produced by ``BidOfferAdapter`` when ``full_depth=True``.

    Performance caveat:
        FullDepth mode allocates ~46 objects per message (4 tuples +
        40 float/int objects). This creates significant GC pressure
        at high message rates. **Not intended for sub-100us strategies.**
        Use :class:`BestBidAsk` (default) for ultra-low-latency.

    Hot-path construction:
        Use ``FullBidOffer.model_construct(...)`` in the adapter to
        skip Pydantic validation. All price/volume tuples are built
        with explicit field unroll (no ``getattr``/f-string loops).

    Attributes:
        symbol: Stock symbol (e.g., ``"AOT"``). Non-empty.
        bid_prices: Exactly 10 bid prices, index 0 is best bid. Each
            converted from ``Money(units, nanos)`` via
            ``units + nanos * 1e-9``.
        ask_prices: Exactly 10 ask prices, index 0 is best ask.
        bid_volumes: Exactly 10 bid volumes (number of shares).
        ask_volumes: Exactly 10 ask volumes (number of shares).
        bid_flag: Bid session flag. See :class:`BidAskFlag`.
        ask_flag: Ask session flag. See :class:`BidAskFlag`.
        recv_ts: Wall-clock timestamp (``time.time_ns()``). Non-negative.
        recv_mono_ns: Monotonic timestamp (``time.perf_counter_ns()``).
            Non-negative.
        connection_epoch: Reconnect version counter. 0 = initial
            connection. Increments on each MQTT reconnect after
            subscription replay.

    Example:
        >>> event = FullBidOffer(
        ...     symbol="AOT",
        ...     bid_prices=(25.5, 25.25, 25.0, 0, 0, 0, 0, 0, 0, 0),
        ...     ask_prices=(26.0, 26.25, 26.5, 0, 0, 0, 0, 0, 0, 0),
        ...     bid_volumes=(1000, 500, 200, 0, 0, 0, 0, 0, 0, 0),
        ...     ask_volumes=(800, 300, 100, 0, 0, 0, 0, 0, 0, 0),
        ...     bid_flag=BidAskFlag.NORMAL,
        ...     ask_flag=BidAskFlag.NORMAL,
        ...     recv_ts=1739500000000000000,
        ...     recv_mono_ns=123456789,
        ... )
        >>> event.bid_prices[0]
        25.5
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, description="Stock symbol (e.g., 'AOT')")
    bid_prices: tuple[float, ...] = Field(
        min_length=10,
        max_length=10,
        description="Exactly 10 bid prices (index 0 = best bid). Money -> float.",
    )
    ask_prices: tuple[float, ...] = Field(
        min_length=10,
        max_length=10,
        description="Exactly 10 ask prices (index 0 = best ask). Money -> float.",
    )
    bid_volumes: tuple[int, ...] = Field(
        min_length=10,
        max_length=10,
        description="Exactly 10 bid volumes (number of shares).",
    )
    ask_volumes: tuple[int, ...] = Field(
        min_length=10,
        max_length=10,
        description="Exactly 10 ask volumes (number of shares).",
    )
    bid_flag: BidAskFlag = Field(
        description="Bid session flag. See BidAskFlag enum.",
    )
    ask_flag: BidAskFlag = Field(
        description="Ask session flag. See BidAskFlag enum.",
    )
    recv_ts: int = Field(
        ge=0,
        description="Wall-clock receive timestamp (time.time_ns())",
    )
    recv_mono_ns: int = Field(
        ge=0,
        description="Monotonic receive timestamp (time.perf_counter_ns())",
    )
    connection_epoch: int = Field(
        default=0,
        ge=0,
        description=(
            "Reconnect version counter. Increments on each MQTT reconnect "
            "after subscription replay. 0 = initial connection."
        ),
    )

    def is_auction(self) -> bool:
        """Check if this event occurred during an auction period (ATO/ATC).

        Uses :class:`BidAskFlag` enum for clarity — no magic numbers.
        Returns ``True`` if either bid or ask flag indicates an auction
        session (At-The-Opening or At-The-Close).

        Returns:
            ``True`` if bid_flag or ask_flag is ATO (2) or ATC (3).
        """
        return _is_auction(self.bid_flag, self.ask_flag)
