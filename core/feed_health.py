"""Two-tier feed health monitor for production liveness detection.

Monitors both **global feed silence** (entire MQTT connection dead) and
**per-symbol liveness** (individual symbol gap detection). Uses monotonic
timestamps (``time.perf_counter_ns()``) exclusively — never wall clock —
to avoid false alerts from NTP adjustments.

Architecture note:
    ``FeedHealthMonitor`` sits on the **strategy/consumer side** of the
    pipeline. Call :meth:`on_event` for every event consumed from the
    dispatcher, then query liveness via :meth:`is_feed_dead`,
    :meth:`is_stale`, or :meth:`stale_symbols`.

Thread safety:
    **NOT thread-safe.** Designed for single-consumer (strategy thread)
    use only. Consistent with the SPSC pattern of the dispatcher.

Startup-aware state:
    Before the first event is received, :meth:`is_feed_dead` returns
    ``False`` (unknown state, not dead). Use :meth:`has_ever_received`
    to distinguish "unknown" from "healthy".

Per-symbol gap override:
    Different symbols have different activity patterns. Configure
    ``per_symbol_max_gap`` in :class:`FeedHealthConfig` to set
    symbol-specific staleness thresholds. Symbols not in the override
    dict use the global ``max_gap_seconds``.

Memory growth:
    The per-symbol dictionary (``_last_event_mono_ns``) grows with the
    symbol universe and is **never automatically evicted**. This is
    intentional for a fixed subscription model (SET equities). If the
    symbol universe is dynamic (e.g., derivatives rolling contracts),
    call :meth:`purge` to remove unsubscribed symbols, or
    :meth:`reset` to clear all state.

Example:
    >>> from core.feed_health import FeedHealthMonitor, FeedHealthConfig
    >>> monitor = FeedHealthMonitor(
    ...     config=FeedHealthConfig(
    ...         max_gap_seconds=5.0,
    ...         per_symbol_max_gap={"RARE": 60.0},
    ...     ),
    ... )
    >>> monitor.on_event("PTT")
    >>> monitor.is_stale("PTT")
    False
    >>> monitor.is_feed_dead()
    False
"""

import time

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class FeedHealthConfig(BaseModel):
    """Immutable configuration for :class:`FeedHealthMonitor`.

    Frozen after construction. Mutating fields raises ``ValidationError``
    because ``_max_gap_ns`` and ``_per_symbol_max_gap_ns`` are cached
    at init time — changing the config after construction would silently
    desync from the cached values.

    Attributes:
        max_gap_seconds: Global maximum gap (in seconds) before a
            symbol is considered stale. Default 5.0 seconds.
        per_symbol_max_gap: Per-symbol override for ``max_gap_seconds``.
            Symbols not in this dict use the global default.

    Example:
        >>> config = FeedHealthConfig(
        ...     max_gap_seconds=5.0,
        ...     per_symbol_max_gap={"RARE": 60.0, "ILLIQUID": 30.0},
        ... )
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_gap_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description=(
            "Global maximum gap (seconds) before a symbol is stale. "
            "Default 5.0 seconds."
        ),
    )
    per_symbol_max_gap: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Per-symbol max gap override (seconds). "
            "Symbols not listed use the global max_gap_seconds."
        ),
    )


# ---------------------------------------------------------------------------
# Feed Health Monitor
# ---------------------------------------------------------------------------


class FeedHealthMonitor:
    """Two-tier feed health monitor using monotonic timestamps.

    Tracks both global feed liveness and per-symbol staleness using
    ``time.perf_counter_ns()`` (monotonic, NTP-immune).

    **NOT thread-safe** — call from the strategy/consumer thread only.

    Two-tier detection:
        - **Global:** :meth:`is_feed_dead` checks if *any* event has
          arrived recently. Before the first event, returns ``False``
          (unknown, not dead).
        - **Per-symbol:** :meth:`is_stale` checks if a *specific symbol*
          has been seen recently. Returns ``False`` for never-seen
          symbols (use :meth:`has_seen` to distinguish).

    Memory model:
        Per-symbol state grows with the symbol universe and is never
        automatically evicted. For dynamic symbol universes, use
        :meth:`purge` to remove individual symbols or :meth:`reset`
        to clear all state.

    Args:
        config: Feed health configuration. Defaults to
            ``FeedHealthConfig()`` with ``max_gap_seconds=5.0``.

    Example:
        >>> monitor = FeedHealthMonitor()
        >>> monitor.on_event("PTT")
        >>> monitor.is_stale("PTT")
        False
        >>> monitor.has_ever_received()
        True
    """

    __slots__ = (
        "_config",
        "_max_gap_ns",
        "_per_symbol_max_gap_ns",
        "_global_last_event_mono_ns",
        "_last_event_mono_ns",
    )

    def __init__(self, config: FeedHealthConfig | None = None) -> None:
        self._config: FeedHealthConfig = config or FeedHealthConfig()
        self._max_gap_ns: int = int(
            self._config.max_gap_seconds * 1_000_000_000,
        )
        self._per_symbol_max_gap_ns: dict[str, int] = {
            symbol: int(gap * 1_000_000_000)
            for symbol, gap in self._config.per_symbol_max_gap.items()
        }

        # Global: None = no event received yet (startup-aware)
        self._global_last_event_mono_ns: int | None = None

        # Per-symbol: symbol → last event monotonic timestamp (ns)
        self._last_event_mono_ns: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Event Ingestion
    # ------------------------------------------------------------------

    def on_event(self, symbol: str, now_ns: int | None = None) -> None:
        """Record that an event was received for ``symbol``.

        Call this for every event consumed from the dispatcher.
        Updates both global and per-symbol timestamps.

        Args:
            symbol: The symbol that received an event.
            now_ns: Optional pre-captured ``time.perf_counter_ns()``
                for reuse across multiple calls in a single poll loop.
                If ``None``, captures internally.
        """
        now: int = now_ns if now_ns is not None else time.perf_counter_ns()
        self._global_last_event_mono_ns = now
        self._last_event_mono_ns[symbol] = now

    # ------------------------------------------------------------------
    # Global Feed Liveness
    # ------------------------------------------------------------------

    def is_feed_dead(self, now_ns: int | None = None) -> bool:
        """Check if the entire feed appears dead (no events at all).

        Returns ``False`` before the first event (unknown state, not
        dead). After the first event, returns ``True`` if the gap
        since the last event exceeds ``max_gap_seconds``.

        Args:
            now_ns: Optional pre-captured ``time.perf_counter_ns()``.

        Returns:
            ``True`` if the feed is considered dead, ``False`` otherwise
            (including before first event).
        """
        if self._global_last_event_mono_ns is None:
            return False  # Unknown state — not dead
        now: int = now_ns if now_ns is not None else time.perf_counter_ns()
        gap: int = max(0, now - self._global_last_event_mono_ns)
        return gap > self._max_gap_ns

    def has_ever_received(self) -> bool:
        """Check if any event has ever been received.

        Allows callers to distinguish "unknown" (before first event)
        from "healthy" (events arriving normally).

        Returns:
            ``True`` if at least one event has been recorded.
        """
        return self._global_last_event_mono_ns is not None

    # ------------------------------------------------------------------
    # Per-Symbol Liveness
    # ------------------------------------------------------------------

    def is_stale(self, symbol: str, now_ns: int | None = None) -> bool:
        """Check if a specific symbol's data is stale.

        Returns ``False`` for never-seen symbols. Use :meth:`has_seen`
        to distinguish "not tracked" from "healthy".

        Uses the per-symbol gap override if configured, otherwise
        falls back to the global ``max_gap_seconds``.

        Args:
            symbol: The symbol to check.
            now_ns: Optional pre-captured ``time.perf_counter_ns()``.

        Returns:
            ``True`` if the symbol was seen before and the gap exceeds
            its threshold, ``False`` otherwise.
        """
        last_ns: int | None = self._last_event_mono_ns.get(symbol)
        if last_ns is None:
            return False  # Never seen — not stale
        now: int = now_ns if now_ns is not None else time.perf_counter_ns()
        max_gap: int = self._per_symbol_max_gap_ns.get(
            symbol,
            self._max_gap_ns,
        )
        gap: int = max(0, now - last_ns)
        return gap > max_gap

    def has_seen(self, symbol: str) -> bool:
        """Check if a symbol has ever been recorded.

        Allows callers to distinguish "never seen" from "healthy"
        when :meth:`is_stale` returns ``False``.

        Args:
            symbol: The symbol to check.

        Returns:
            ``True`` if at least one event for ``symbol`` was recorded.
        """
        return symbol in self._last_event_mono_ns

    def tracked_symbol_count(self) -> int:
        """Return the number of symbols currently being tracked.

        Returns:
            Number of distinct symbols that have been recorded via
            :meth:`on_event`.
        """
        return len(self._last_event_mono_ns)

    def stale_symbols(self, now_ns: int | None = None) -> list[str]:
        """Return all symbols currently considered stale.

        Iterates over all tracked symbols and returns those whose
        gap exceeds their threshold. Cost is O(N) where N is the
        number of tracked symbols.

        Args:
            now_ns: Optional pre-captured ``time.perf_counter_ns()``.

        Returns:
            List of stale symbol names. Empty if none are stale.
        """
        now: int = now_ns if now_ns is not None else time.perf_counter_ns()
        stale: list[str] = []
        for symbol, last_ns in self._last_event_mono_ns.items():
            max_gap: int = self._per_symbol_max_gap_ns.get(
                symbol,
                self._max_gap_ns,
            )
            gap: int = max(0, now - last_ns)
            if gap > max_gap:
                stale.append(symbol)
        return stale

    def last_seen_gap_ms(
        self,
        symbol: str,
        now_ns: int | None = None,
    ) -> float | None:
        """Return milliseconds since last event for a symbol.

        Args:
            symbol: The symbol to check.
            now_ns: Optional pre-captured ``time.perf_counter_ns()``.

        Returns:
            Gap in milliseconds, or ``None`` if the symbol has never
            been seen.
        """
        last_ns: int | None = self._last_event_mono_ns.get(symbol)
        if last_ns is None:
            return None
        now: int = now_ns if now_ns is not None else time.perf_counter_ns()
        return max(0, now - last_ns) / 1_000_000

    # ------------------------------------------------------------------
    # Lifecycle Management
    # ------------------------------------------------------------------

    def purge(self, symbol: str) -> bool:
        """Remove tracking state for a single symbol.

        Use when a symbol is unsubscribed or no longer relevant.
        Does not affect global feed liveness tracking.

        Args:
            symbol: The symbol to remove.

        Returns:
            ``True`` if the symbol was tracked and removed,
            ``False`` if it was never tracked.
        """
        return self._last_event_mono_ns.pop(symbol, None) is not None

    def reset(self) -> None:
        """Clear all tracking state (global and per-symbol).

        Resets to startup state: :meth:`is_feed_dead` returns
        ``False``, :meth:`has_ever_received` returns ``False``,
        all per-symbol data is cleared.

        Use during full reconnection or trading session boundaries.
        """
        self._global_last_event_mono_ns = None
        self._last_event_mono_ns.clear()
