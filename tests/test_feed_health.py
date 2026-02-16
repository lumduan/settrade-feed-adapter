"""Unit tests for core.feed_health module.

Tests FeedHealthMonitor and FeedHealthConfig: global feed liveness,
per-symbol staleness, startup-aware state, per-symbol gap overrides,
monotonic time injection, lifecycle management, and edge cases.
"""

import pytest
from pydantic import ValidationError

from core.feed_health import FeedHealthConfig, FeedHealthMonitor


# ---------------------------------------------------------------------------
# FeedHealthConfig Tests
# ---------------------------------------------------------------------------


class TestFeedHealthConfig:
    """Tests for FeedHealthConfig Pydantic model."""

    def test_default_config(self) -> None:
        """Default config has max_gap_seconds=5.0 and empty overrides."""
        config: FeedHealthConfig = FeedHealthConfig()
        assert config.max_gap_seconds == 5.0
        assert config.per_symbol_max_gap == {}

    def test_custom_config(self) -> None:
        """Custom config values are accepted."""
        config: FeedHealthConfig = FeedHealthConfig(
            max_gap_seconds=10.0,
            per_symbol_max_gap={"RARE": 60.0},
        )
        assert config.max_gap_seconds == 10.0
        assert config.per_symbol_max_gap == {"RARE": 60.0}

    def test_frozen_immutability(self) -> None:
        """Frozen config rejects attribute assignment."""
        config: FeedHealthConfig = FeedHealthConfig()
        with pytest.raises(ValidationError):
            config.max_gap_seconds = 99.0  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected (extra='forbid')."""
        with pytest.raises(ValidationError):
            FeedHealthConfig(extra_field="bad")  # type: ignore[call-arg]

    def test_zero_gap_rejected(self) -> None:
        """max_gap_seconds=0 is rejected (gt=0)."""
        with pytest.raises(ValidationError):
            FeedHealthConfig(max_gap_seconds=0.0)

    def test_negative_gap_rejected(self) -> None:
        """Negative max_gap_seconds is rejected."""
        with pytest.raises(ValidationError):
            FeedHealthConfig(max_gap_seconds=-1.0)

    def test_default_factory_no_shared_mutable(self) -> None:
        """Each config gets its own dict (no shared mutable default)."""
        c1: FeedHealthConfig = FeedHealthConfig()
        c2: FeedHealthConfig = FeedHealthConfig()
        assert c1.per_symbol_max_gap is not c2.per_symbol_max_gap


# ---------------------------------------------------------------------------
# FeedHealthMonitor: Startup-Aware State
# ---------------------------------------------------------------------------


class TestStartupState:
    """Tests for startup-aware state (before first event)."""

    def test_is_feed_dead_false_before_first_event(self) -> None:
        """is_feed_dead() returns False before any event (unknown, not dead)."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        assert monitor.is_feed_dead() is False

    def test_has_ever_received_false_initially(self) -> None:
        """has_ever_received() returns False before any event."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        assert monitor.has_ever_received() is False

    def test_has_ever_received_true_after_event(self) -> None:
        """has_ever_received() returns True after first event."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        monitor.on_event("PTT", now_ns=1_000_000_000)
        assert monitor.has_ever_received() is True

    def test_is_stale_false_for_never_seen(self) -> None:
        """is_stale() returns False for a symbol never recorded."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        assert monitor.is_stale("UNKNOWN") is False

    def test_has_seen_false_for_never_seen(self) -> None:
        """has_seen() returns False for a symbol never recorded."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        assert monitor.has_seen("UNKNOWN") is False

    def test_has_seen_true_after_event(self) -> None:
        """has_seen() returns True after recording a symbol."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        monitor.on_event("PTT", now_ns=1_000_000_000)
        assert monitor.has_seen("PTT") is True

    def test_tracked_symbol_count_zero_initially(self) -> None:
        """tracked_symbol_count() returns 0 before any event."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        assert monitor.tracked_symbol_count() == 0


# ---------------------------------------------------------------------------
# FeedHealthMonitor: Global Feed Liveness
# ---------------------------------------------------------------------------


class TestGlobalFeedLiveness:
    """Tests for global feed liveness (is_feed_dead)."""

    def test_feed_alive_within_gap(self) -> None:
        """Feed is alive when gap is within max_gap_seconds."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)

        # 1 second later — still alive
        assert monitor.is_feed_dead(now_ns=base_ns + 1_000_000_000) is False

    def test_feed_dead_beyond_gap(self) -> None:
        """Feed is dead when gap exceeds max_gap_seconds."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)

        # 6 seconds later — dead
        assert monitor.is_feed_dead(now_ns=base_ns + 6_000_000_000) is True

    def test_feed_dead_exactly_at_boundary(self) -> None:
        """Feed is alive at exactly max_gap_seconds (not stale)."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)

        # Exactly 5 seconds — not dead (> not >=)
        assert monitor.is_feed_dead(now_ns=base_ns + 5_000_000_000) is False

    def test_feed_recovers_after_new_event(self) -> None:
        """Feed recovers after a new event arrives."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)

        # 6 seconds later — dead
        dead_ns: int = base_ns + 6_000_000_000
        assert monitor.is_feed_dead(now_ns=dead_ns) is True

        # New event arrives
        monitor.on_event("PTT", now_ns=dead_ns)

        # Immediately after — alive
        assert monitor.is_feed_dead(now_ns=dead_ns + 100_000) is False

    def test_negative_delta_clamped(self) -> None:
        """Negative time delta (testing edge case) is clamped to 0."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        monitor.on_event("PTT", now_ns=1_000_000_000)
        # now_ns < last event — negative delta clamped to 0
        assert monitor.is_feed_dead(now_ns=500_000_000) is False


# ---------------------------------------------------------------------------
# FeedHealthMonitor: Per-Symbol Liveness
# ---------------------------------------------------------------------------


class TestPerSymbolLiveness:
    """Tests for per-symbol staleness (is_stale)."""

    def test_not_stale_within_gap(self) -> None:
        """Symbol is not stale within max_gap_seconds."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)
        assert monitor.is_stale("PTT", now_ns=base_ns + 1_000_000_000) is False

    def test_stale_beyond_gap(self) -> None:
        """Symbol is stale when gap exceeds max_gap_seconds."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)
        assert monitor.is_stale("PTT", now_ns=base_ns + 6_000_000_000) is True

    def test_per_symbol_gap_override(self) -> None:
        """Per-symbol override uses symbol-specific gap."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(
                max_gap_seconds=5.0,
                per_symbol_max_gap={"RARE": 60.0},
            ),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("RARE", now_ns=base_ns)
        monitor.on_event("PTT", now_ns=base_ns)

        check_ns: int = base_ns + 10_000_000_000  # 10 seconds later

        # PTT: stale (10s > 5s global)
        assert monitor.is_stale("PTT", now_ns=check_ns) is True
        # RARE: not stale (10s < 60s override)
        assert monitor.is_stale("RARE", now_ns=check_ns) is False

    def test_stale_symbols_returns_stale_only(self) -> None:
        """stale_symbols() returns only stale symbols."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)
        monitor.on_event("AOT", now_ns=base_ns + 4_000_000_000)

        # Check at base + 6s: PTT stale (6s > 5s), AOT alive (2s < 5s)
        stale: list[str] = monitor.stale_symbols(
            now_ns=base_ns + 6_000_000_000,
        )
        assert stale == ["PTT"]

    def test_stale_symbols_empty_when_all_fresh(self) -> None:
        """stale_symbols() returns empty when all symbols are fresh."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)
        monitor.on_event("AOT", now_ns=base_ns)
        assert monitor.stale_symbols(now_ns=base_ns + 1_000_000_000) == []

    def test_negative_delta_clamped_per_symbol(self) -> None:
        """Negative time delta in is_stale is clamped to 0."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        monitor.on_event("PTT", now_ns=1_000_000_000)
        assert monitor.is_stale("PTT", now_ns=500_000_000) is False


# ---------------------------------------------------------------------------
# FeedHealthMonitor: last_seen_gap_ms
# ---------------------------------------------------------------------------


class TestLastSeenGapMs:
    """Tests for last_seen_gap_ms()."""

    def test_returns_none_for_unknown_symbol(self) -> None:
        """Returns None for a symbol never seen."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        assert monitor.last_seen_gap_ms("UNKNOWN") is None

    def test_returns_correct_gap(self) -> None:
        """Returns correct gap in milliseconds."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        base_ns: int = 1_000_000_000_000
        monitor.on_event("PTT", now_ns=base_ns)
        gap: float | None = monitor.last_seen_gap_ms(
            "PTT",
            now_ns=base_ns + 500_000_000,
        )
        assert gap is not None
        assert gap == pytest.approx(500.0)

    def test_negative_delta_clamped(self) -> None:
        """Negative delta returns 0.0 (clamped)."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        monitor.on_event("PTT", now_ns=1_000_000_000)
        gap: float | None = monitor.last_seen_gap_ms(
            "PTT",
            now_ns=500_000_000,
        )
        assert gap is not None
        assert gap == 0.0


# ---------------------------------------------------------------------------
# FeedHealthMonitor: Lifecycle Management
# ---------------------------------------------------------------------------


class TestLifecycleManagement:
    """Tests for purge() and reset()."""

    def test_purge_removes_symbol(self) -> None:
        """purge() removes a tracked symbol."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        monitor.on_event("PTT", now_ns=1_000_000_000)
        assert monitor.has_seen("PTT") is True

        result: bool = monitor.purge("PTT")
        assert result is True
        assert monitor.has_seen("PTT") is False

    def test_purge_returns_false_for_unknown(self) -> None:
        """purge() returns False for a symbol never tracked."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        assert monitor.purge("UNKNOWN") is False

    def test_purge_does_not_affect_global(self) -> None:
        """purge() does not reset global feed liveness."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        monitor.on_event("PTT", now_ns=1_000_000_000)
        monitor.purge("PTT")
        assert monitor.has_ever_received() is True

    def test_reset_clears_all_state(self) -> None:
        """reset() clears all tracking state."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        monitor.on_event("PTT", now_ns=1_000_000_000)
        monitor.on_event("AOT", now_ns=2_000_000_000)

        monitor.reset()

        assert monitor.has_ever_received() is False
        assert monitor.has_seen("PTT") is False
        assert monitor.has_seen("AOT") is False
        assert monitor.tracked_symbol_count() == 0
        assert monitor.is_feed_dead() is False

    def test_tracked_symbol_count(self) -> None:
        """tracked_symbol_count() reflects distinct symbols tracked."""
        monitor: FeedHealthMonitor = FeedHealthMonitor()
        monitor.on_event("PTT", now_ns=1_000_000_000)
        monitor.on_event("AOT", now_ns=2_000_000_000)
        monitor.on_event("PTT", now_ns=3_000_000_000)  # duplicate
        assert monitor.tracked_symbol_count() == 2


# ---------------------------------------------------------------------------
# FeedHealthMonitor: Multiple Symbols
# ---------------------------------------------------------------------------


class TestMultipleSymbols:
    """Tests for multi-symbol tracking."""

    def test_independent_symbol_tracking(self) -> None:
        """Each symbol has independent staleness tracking."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000

        monitor.on_event("PTT", now_ns=base_ns)
        monitor.on_event("AOT", now_ns=base_ns + 4_000_000_000)

        check_ns: int = base_ns + 6_000_000_000
        assert monitor.is_stale("PTT", now_ns=check_ns) is True
        assert monitor.is_stale("AOT", now_ns=check_ns) is False

    def test_global_tracks_most_recent(self) -> None:
        """Global timestamp tracks the most recent event across all symbols."""
        monitor: FeedHealthMonitor = FeedHealthMonitor(
            config=FeedHealthConfig(max_gap_seconds=5.0),
        )
        base_ns: int = 1_000_000_000_000

        monitor.on_event("PTT", now_ns=base_ns)
        monitor.on_event("AOT", now_ns=base_ns + 3_000_000_000)

        # At base + 6s: PTT stale but global alive (AOT was 3s ago)
        check_ns: int = base_ns + 6_000_000_000
        assert monitor.is_stale("PTT", now_ns=check_ns) is True
        assert monitor.is_feed_dead(now_ns=check_ns) is False
