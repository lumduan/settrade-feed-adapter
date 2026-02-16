"""Unit tests for core.dispatcher module.

Tests the Dispatcher (bounded event queue), DispatcherConfig, and
DispatcherStats. Covers configuration validation, push/poll flow,
FIFO ordering, overflow/drop detection, clear behaviour, stats
accuracy, invariant checks, input validation, and thread safety.
"""

import threading

import pytest
from pydantic import ValidationError

from core.dispatcher import (
    Dispatcher,
    DispatcherConfig,
    DispatcherHealth,
    DispatcherStats,
)


# ---------------------------------------------------------------------------
# DispatcherConfig Tests
# ---------------------------------------------------------------------------


class TestDispatcherConfig:
    """Tests for DispatcherConfig Pydantic model."""

    def test_default_maxlen(self) -> None:
        """Default maxlen is 100_000."""
        config: DispatcherConfig = DispatcherConfig()
        assert config.maxlen == 100_000

    def test_custom_maxlen(self) -> None:
        """Custom maxlen is accepted."""
        config: DispatcherConfig = DispatcherConfig(maxlen=500)
        assert config.maxlen == 500

    def test_maxlen_one(self) -> None:
        """Minimum valid maxlen is 1."""
        config: DispatcherConfig = DispatcherConfig(maxlen=1)
        assert config.maxlen == 1

    def test_maxlen_zero_rejected(self) -> None:
        """maxlen=0 is rejected (gt=0)."""
        with pytest.raises(ValidationError):
            DispatcherConfig(maxlen=0)

    def test_maxlen_negative_rejected(self) -> None:
        """Negative maxlen is rejected."""
        with pytest.raises(ValidationError):
            DispatcherConfig(maxlen=-1)


# ---------------------------------------------------------------------------
# DispatcherStats Tests
# ---------------------------------------------------------------------------


class TestDispatcherStats:
    """Tests for DispatcherStats Pydantic model."""

    def test_creation(self) -> None:
        """Stats model is created with valid data."""
        stats: DispatcherStats = DispatcherStats(
            total_pushed=100,
            total_polled=90,
            total_dropped=5,
            queue_len=5,
            maxlen=1000,
        )
        assert stats.total_pushed == 100
        assert stats.total_polled == 90
        assert stats.total_dropped == 5
        assert stats.queue_len == 5
        assert stats.maxlen == 1000

    def test_frozen_immutability(self) -> None:
        """Frozen model rejects attribute assignment."""
        stats: DispatcherStats = DispatcherStats(
            total_pushed=100,
            total_polled=90,
            total_dropped=5,
            queue_len=5,
            maxlen=1000,
        )
        with pytest.raises(ValidationError):
            stats.total_pushed = 200  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected (extra='forbid')."""
        with pytest.raises(ValidationError):
            DispatcherStats(
                total_pushed=100,
                total_polled=90,
                total_dropped=5,
                queue_len=5,
                maxlen=1000,
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_negative_counter_rejected(self) -> None:
        """Negative counter values are rejected (ge=0)."""
        with pytest.raises(ValidationError):
            DispatcherStats(
                total_pushed=-1,
                total_polled=0,
                total_dropped=0,
                queue_len=0,
                maxlen=1000,
            )

    def test_zero_maxlen_rejected(self) -> None:
        """maxlen=0 is rejected in stats (gt=0)."""
        with pytest.raises(ValidationError):
            DispatcherStats(
                total_pushed=0,
                total_polled=0,
                total_dropped=0,
                queue_len=0,
                maxlen=0,
            )


# ---------------------------------------------------------------------------
# Dispatcher Initialization Tests
# ---------------------------------------------------------------------------


class TestDispatcherInit:
    """Tests for Dispatcher initialization."""

    def test_default_config(self) -> None:
        """Dispatcher uses default config when none provided."""
        dispatcher: Dispatcher[str] = Dispatcher()
        stats: DispatcherStats = dispatcher.stats()
        assert stats.maxlen == 100_000
        assert stats.queue_len == 0
        assert stats.total_pushed == 0

    def test_custom_config(self) -> None:
        """Dispatcher accepts custom config."""
        config: DispatcherConfig = DispatcherConfig(maxlen=50)
        dispatcher: Dispatcher[str] = Dispatcher(config=config)
        assert dispatcher.stats().maxlen == 50

    def test_empty_on_creation(self) -> None:
        """Dispatcher starts with empty queue and zero counters."""
        dispatcher: Dispatcher[str] = Dispatcher()
        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == 0
        assert stats.total_polled == 0
        assert stats.total_dropped == 0
        assert stats.queue_len == 0

    def test_invariant_on_creation(self) -> None:
        """Invariant holds on fresh dispatcher."""
        dispatcher: Dispatcher[str] = Dispatcher()
        assert dispatcher._invariant_ok()


# ---------------------------------------------------------------------------
# Push & Poll Basic Flow Tests
# ---------------------------------------------------------------------------


class TestPushPoll:
    """Tests for push/poll basic flow."""

    def test_push_and_poll_single_event(self) -> None:
        """Push one event, poll it back."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("event_1")
        events: list[str] = dispatcher.poll(max_events=10)
        assert events == ["event_1"]

    def test_push_and_poll_multiple_events(self) -> None:
        """Push multiple events, poll them all back."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("a")
        dispatcher.push("b")
        dispatcher.push("c")
        events: list[str] = dispatcher.poll(max_events=10)
        assert events == ["a", "b", "c"]

    def test_fifo_ordering(self) -> None:
        """Events are returned in FIFO order."""
        dispatcher: Dispatcher[int] = Dispatcher()
        for i in range(10):
            dispatcher.push(i)
        events: list[int] = dispatcher.poll(max_events=10)
        assert events == list(range(10))

    def test_poll_empty_queue(self) -> None:
        """Poll on empty queue returns empty list."""
        dispatcher: Dispatcher[str] = Dispatcher()
        events: list[str] = dispatcher.poll(max_events=10)
        assert events == []

    def test_poll_respects_max_events(self) -> None:
        """Poll returns at most max_events items."""
        dispatcher: Dispatcher[int] = Dispatcher()
        for i in range(20):
            dispatcher.push(i)
        events: list[int] = dispatcher.poll(max_events=5)
        assert len(events) == 5
        assert events == [0, 1, 2, 3, 4]

    def test_poll_fewer_than_max_events(self) -> None:
        """Poll returns fewer events when queue has less than max_events."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("x")
        dispatcher.push("y")
        events: list[str] = dispatcher.poll(max_events=100)
        assert events == ["x", "y"]

    def test_multiple_polls_drain_queue(self) -> None:
        """Multiple polls eventually drain the queue."""
        dispatcher: Dispatcher[int] = Dispatcher()
        for i in range(10):
            dispatcher.push(i)

        batch_1: list[int] = dispatcher.poll(max_events=3)
        batch_2: list[int] = dispatcher.poll(max_events=3)
        batch_3: list[int] = dispatcher.poll(max_events=3)
        batch_4: list[int] = dispatcher.poll(max_events=3)

        assert batch_1 == [0, 1, 2]
        assert batch_2 == [3, 4, 5]
        assert batch_3 == [6, 7, 8]
        assert batch_4 == [9]

    def test_push_various_types(self) -> None:
        """Dispatcher accepts any event type (generic)."""
        dispatcher: Dispatcher[object] = Dispatcher()
        dispatcher.push("string")
        dispatcher.push(42)
        dispatcher.push({"key": "value"})
        dispatcher.push(None)
        events: list[object] = dispatcher.poll(max_events=10)
        assert len(events) == 4
        assert events[0] == "string"
        assert events[1] == 42
        assert events[2] == {"key": "value"}
        assert events[3] is None

    def test_poll_max_events_one(self) -> None:
        """poll(max_events=1) returns exactly one event."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("a")
        dispatcher.push("b")
        events: list[str] = dispatcher.poll(max_events=1)
        assert events == ["a"]
        # Second event still in queue
        remaining: list[str] = dispatcher.poll(max_events=1)
        assert remaining == ["b"]


# ---------------------------------------------------------------------------
# Overflow & Drop Detection Tests
# ---------------------------------------------------------------------------


class TestOverflowDrops:
    """Tests for overflow and drop detection."""

    def test_drop_at_maxlen_boundary(self) -> None:
        """Push to a full queue triggers drop detection."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=3),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        dispatcher.push(3)
        assert dispatcher.stats().total_dropped == 0

        # This push should trigger a drop
        dispatcher.push(4)
        assert dispatcher.stats().total_dropped == 1
        assert dispatcher.stats().total_pushed == 4

    def test_drop_oldest_evicted(self) -> None:
        """Oldest event is evicted when queue overflows."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=3),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        dispatcher.push(3)
        dispatcher.push(4)  # Evicts 1

        events: list[int] = dispatcher.poll(max_events=10)
        assert events == [2, 3, 4]

    def test_multiple_sequential_drops(self) -> None:
        """Multiple overflows count drops correctly."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=2),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        # Queue full, subsequent pushes cause drops
        dispatcher.push(3)  # Drop 1 (evicts 1)
        dispatcher.push(4)  # Drop 2 (evicts 2)
        dispatcher.push(5)  # Drop 3 (evicts 3)

        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == 5
        assert stats.total_dropped == 3
        assert stats.queue_len == 2

        events: list[int] = dispatcher.poll(max_events=10)
        assert events == [4, 5]

    def test_maxlen_one_every_push_after_first_drops(self) -> None:
        """With maxlen=1, every push after the first causes a drop."""
        dispatcher: Dispatcher[str] = Dispatcher(
            config=DispatcherConfig(maxlen=1),
        )
        dispatcher.push("a")
        assert dispatcher.stats().total_dropped == 0

        dispatcher.push("b")
        assert dispatcher.stats().total_dropped == 1

        dispatcher.push("c")
        assert dispatcher.stats().total_dropped == 2

        events: list[str] = dispatcher.poll(max_events=10)
        assert events == ["c"]

    def test_no_drop_after_poll_frees_space(self) -> None:
        """No drop occurs if poll frees space before next push."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=2),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        # Queue full — poll one
        dispatcher.poll(max_events=1)
        # Now space available — no drop
        dispatcher.push(3)
        assert dispatcher.stats().total_dropped == 0
        assert dispatcher.stats().total_pushed == 3

    def test_drop_count_matches_evicted_events(self) -> None:
        """total_dropped matches the number of actually evicted events."""
        maxlen: int = 5
        total_pushes: int = 15
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=maxlen),
        )
        for i in range(total_pushes):
            dispatcher.push(i)

        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == total_pushes
        assert stats.total_dropped == total_pushes - maxlen
        assert stats.queue_len == maxlen

        # Remaining events should be the last `maxlen` items
        events: list[int] = dispatcher.poll(max_events=maxlen)
        assert events == list(range(total_pushes - maxlen, total_pushes))


# ---------------------------------------------------------------------------
# Clear Tests
# ---------------------------------------------------------------------------


class TestClear:
    """Tests for clear() method."""

    def test_clear_empties_queue(self) -> None:
        """Clear removes all events from the queue."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("a")
        dispatcher.push("b")
        dispatcher.clear()
        assert dispatcher.stats().queue_len == 0

    def test_clear_resets_counters(self) -> None:
        """Clear resets all counters to zero."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=2),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        dispatcher.push(3)  # Causes drop
        dispatcher.poll(max_events=1)

        dispatcher.clear()

        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == 0
        assert stats.total_polled == 0
        assert stats.total_dropped == 0
        assert stats.queue_len == 0

    def test_push_poll_work_after_clear(self) -> None:
        """Push and poll work normally after clear."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("old_event")
        dispatcher.clear()

        dispatcher.push("new_event")
        events: list[str] = dispatcher.poll(max_events=10)
        assert events == ["new_event"]
        assert dispatcher.stats().total_pushed == 1
        assert dispatcher.stats().total_polled == 1

    def test_clear_empty_queue(self) -> None:
        """Clearing an already empty queue is a no-op."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.clear()
        stats: DispatcherStats = dispatcher.stats()
        assert stats.queue_len == 0
        assert stats.total_pushed == 0

    def test_invariant_after_clear(self) -> None:
        """Invariant holds after clear."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=2),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        dispatcher.push(3)  # Drop
        dispatcher.clear()
        assert dispatcher._invariant_ok()


# ---------------------------------------------------------------------------
# Stats Tests
# ---------------------------------------------------------------------------


class TestStats:
    """Tests for stats() method."""

    def test_stats_after_push(self) -> None:
        """Stats reflect pushed events."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("a")
        dispatcher.push("b")
        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == 2
        assert stats.total_polled == 0
        assert stats.total_dropped == 0
        assert stats.queue_len == 2

    def test_stats_after_poll(self) -> None:
        """Stats reflect polled events."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("a")
        dispatcher.push("b")
        dispatcher.poll(max_events=1)
        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == 2
        assert stats.total_polled == 1
        assert stats.queue_len == 1

    def test_stats_after_drop(self) -> None:
        """Stats reflect dropped events."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=2),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        dispatcher.push(3)  # Drop
        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == 3
        assert stats.total_dropped == 1
        assert stats.queue_len == 2

    def test_stats_returns_frozen_model(self) -> None:
        """Stats returns a frozen DispatcherStats model."""
        dispatcher: Dispatcher[str] = Dispatcher()
        stats: DispatcherStats = dispatcher.stats()
        assert isinstance(stats, DispatcherStats)
        with pytest.raises(ValidationError):
            stats.total_pushed = 999  # type: ignore[misc]

    def test_stats_includes_maxlen(self) -> None:
        """Stats includes configured maxlen."""
        dispatcher: Dispatcher[str] = Dispatcher(
            config=DispatcherConfig(maxlen=42),
        )
        assert dispatcher.stats().maxlen == 42

    def test_stats_queue_len_zero_after_drain(self) -> None:
        """queue_len is 0 after draining all events."""
        dispatcher: Dispatcher[str] = Dispatcher()
        dispatcher.push("a")
        dispatcher.push("b")
        dispatcher.poll(max_events=10)
        assert dispatcher.stats().queue_len == 0


# ---------------------------------------------------------------------------
# Invariant Tests
# ---------------------------------------------------------------------------


class TestInvariant:
    """Tests for _invariant_ok() internal consistency check."""

    def test_invariant_after_push(self) -> None:
        """Invariant holds after push operations."""
        dispatcher: Dispatcher[int] = Dispatcher()
        for i in range(50):
            dispatcher.push(i)
            assert dispatcher._invariant_ok()

    def test_invariant_after_poll(self) -> None:
        """Invariant holds after poll operations."""
        dispatcher: Dispatcher[int] = Dispatcher()
        for i in range(20):
            dispatcher.push(i)
        for _ in range(5):
            dispatcher.poll(max_events=3)
            assert dispatcher._invariant_ok()

    def test_invariant_after_overflow(self) -> None:
        """Invariant holds after overflow/drop events."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=5),
        )
        for i in range(20):
            dispatcher.push(i)
            assert dispatcher._invariant_ok()

    def test_invariant_after_mixed_operations(self) -> None:
        """Invariant holds through mixed push/poll/overflow."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=3),
        )
        dispatcher.push(1)
        assert dispatcher._invariant_ok()
        dispatcher.push(2)
        assert dispatcher._invariant_ok()
        dispatcher.push(3)
        assert dispatcher._invariant_ok()
        dispatcher.push(4)  # Drop
        assert dispatcher._invariant_ok()
        dispatcher.poll(max_events=1)
        assert dispatcher._invariant_ok()
        dispatcher.push(5)
        assert dispatcher._invariant_ok()
        dispatcher.poll(max_events=10)
        assert dispatcher._invariant_ok()


# ---------------------------------------------------------------------------
# Input Validation Tests
# ---------------------------------------------------------------------------


class TestInputValidation:
    """Tests for input validation in poll()."""

    def test_poll_zero_raises_value_error(self) -> None:
        """poll(max_events=0) raises ValueError."""
        dispatcher: Dispatcher[str] = Dispatcher()
        with pytest.raises(ValueError, match="max_events must be > 0"):
            dispatcher.poll(max_events=0)

    def test_poll_negative_raises_value_error(self) -> None:
        """poll(max_events=-1) raises ValueError."""
        dispatcher: Dispatcher[str] = Dispatcher()
        with pytest.raises(ValueError, match="max_events must be > 0"):
            dispatcher.poll(max_events=-1)

    def test_poll_large_negative_raises_value_error(self) -> None:
        """poll(max_events=-999) raises ValueError."""
        dispatcher: Dispatcher[str] = Dispatcher()
        with pytest.raises(ValueError, match="max_events must be > 0"):
            dispatcher.poll(max_events=-999)


# ---------------------------------------------------------------------------
# Thread Safety Tests (SPSC)
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Tests for thread safety under SPSC access pattern."""

    def test_concurrent_push_and_poll(self) -> None:
        """Concurrent push/poll from separate threads preserves invariant.

        Producer pushes N events, consumer polls in batches. After both
        threads complete, verify that no events were lost:
        total_pushed - total_dropped == total_polled + queue_len.
        """
        num_events: int = 10_000
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=1000),
        )
        consumed: list[int] = []
        producer_done: threading.Event = threading.Event()

        def producer() -> None:
            for i in range(num_events):
                dispatcher.push(i)
            producer_done.set()

        def consumer() -> None:
            while not producer_done.is_set() or dispatcher.stats().queue_len > 0:
                batch: list[int] = dispatcher.poll(max_events=50)
                consumed.extend(batch)

        producer_thread: threading.Thread = threading.Thread(
            target=producer,
            name="test-producer",
        )
        consumer_thread: threading.Thread = threading.Thread(
            target=consumer,
            name="test-consumer",
        )

        producer_thread.start()
        consumer_thread.start()
        producer_thread.join(timeout=10)
        consumer_thread.join(timeout=10)

        stats: DispatcherStats = dispatcher.stats()

        # Core invariant: no events lost
        assert stats.total_pushed == num_events
        total_accounted: int = (
            stats.total_polled + stats.total_dropped + stats.queue_len
        )
        assert total_accounted == num_events

        # All polled events should be consumed by our list
        remaining: list[int] = dispatcher.poll(max_events=num_events)
        consumed.extend(remaining)
        assert len(consumed) + stats.total_dropped == num_events

    def test_concurrent_push_poll_no_overflow(self) -> None:
        """Concurrent push/poll with large queue (no overflow expected).

        Tests that events flow through correctly when the queue is
        large enough to avoid drops.
        """
        num_events: int = 5_000
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=100_000),
        )
        consumed: list[int] = []
        producer_done: threading.Event = threading.Event()

        def producer() -> None:
            for i in range(num_events):
                dispatcher.push(i)
            producer_done.set()

        def consumer() -> None:
            while not producer_done.is_set() or dispatcher.stats().queue_len > 0:
                batch: list[int] = dispatcher.poll(max_events=100)
                consumed.extend(batch)

        producer_thread: threading.Thread = threading.Thread(
            target=producer,
            name="test-producer",
        )
        consumer_thread: threading.Thread = threading.Thread(
            target=consumer,
            name="test-consumer",
        )

        producer_thread.start()
        consumer_thread.start()
        producer_thread.join(timeout=10)
        consumer_thread.join(timeout=10)

        # Drain remaining
        remaining: list[int] = dispatcher.poll(max_events=num_events)
        consumed.extend(remaining)

        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == num_events
        assert stats.total_dropped == 0
        assert len(consumed) == num_events

        # Verify FIFO ordering (all events received in order)
        assert consumed == list(range(num_events))


# ---------------------------------------------------------------------------
# Large Batch / Stress Tests
# ---------------------------------------------------------------------------


class TestStress:
    """Stress tests for large batch operations."""

    def test_push_and_poll_large_batch(self) -> None:
        """Push and poll 100K events without overflow."""
        count: int = 100_000
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=count),
        )
        for i in range(count):
            dispatcher.push(i)

        assert dispatcher.stats().queue_len == count
        assert dispatcher.stats().total_pushed == count
        assert dispatcher.stats().total_dropped == 0

        events: list[int] = dispatcher.poll(max_events=count)
        assert len(events) == count
        assert events[0] == 0
        assert events[-1] == count - 1
        assert dispatcher._invariant_ok()

    def test_overflow_large_batch(self) -> None:
        """Push 200K events into 100K queue — 100K drops expected."""
        maxlen: int = 100_000
        total: int = 200_000
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=maxlen),
        )
        for i in range(total):
            dispatcher.push(i)

        stats: DispatcherStats = dispatcher.stats()
        assert stats.total_pushed == total
        assert stats.total_dropped == total - maxlen
        assert stats.queue_len == maxlen
        assert dispatcher._invariant_ok()

        # Remaining events should be the last `maxlen` items
        events: list[int] = dispatcher.poll(max_events=maxlen)
        assert events == list(range(total - maxlen, total))


# ---------------------------------------------------------------------------
# DispatcherConfig EMA Tests
# ---------------------------------------------------------------------------


class TestDispatcherConfigEMA:
    """Tests for EMA-related DispatcherConfig fields."""

    def test_default_ema_alpha(self) -> None:
        """Default ema_alpha is 0.01."""
        config: DispatcherConfig = DispatcherConfig()
        assert config.ema_alpha == 0.01

    def test_default_drop_warning_threshold(self) -> None:
        """Default drop_warning_threshold is 0.01."""
        config: DispatcherConfig = DispatcherConfig()
        assert config.drop_warning_threshold == 0.01

    def test_custom_ema_alpha(self) -> None:
        """Custom ema_alpha is accepted."""
        config: DispatcherConfig = DispatcherConfig(ema_alpha=0.1)
        assert config.ema_alpha == 0.1

    def test_ema_alpha_zero_rejected(self) -> None:
        """ema_alpha=0 is rejected (gt=0)."""
        with pytest.raises(ValidationError):
            DispatcherConfig(ema_alpha=0.0)

    def test_ema_alpha_above_one_rejected(self) -> None:
        """ema_alpha > 1.0 is rejected (le=1)."""
        with pytest.raises(ValidationError):
            DispatcherConfig(ema_alpha=1.5)

    def test_threshold_zero_rejected(self) -> None:
        """drop_warning_threshold=0 is rejected."""
        with pytest.raises(ValidationError):
            DispatcherConfig(drop_warning_threshold=0.0)


# ---------------------------------------------------------------------------
# DispatcherHealth Model Tests
# ---------------------------------------------------------------------------


class TestDispatcherHealth:
    """Tests for DispatcherHealth Pydantic model."""

    def test_creation(self) -> None:
        """DispatcherHealth model is created with valid data."""
        health: DispatcherHealth = DispatcherHealth(
            drop_rate_ema=0.05,
            queue_utilization=0.5,
            total_dropped=10,
            total_pushed=200,
        )
        assert health.drop_rate_ema == 0.05
        assert health.queue_utilization == 0.5
        assert health.total_dropped == 10
        assert health.total_pushed == 200

    def test_frozen_immutability(self) -> None:
        """Frozen model rejects attribute assignment."""
        health: DispatcherHealth = DispatcherHealth(
            drop_rate_ema=0.0,
            queue_utilization=0.0,
            total_dropped=0,
            total_pushed=0,
        )
        with pytest.raises(ValidationError):
            health.drop_rate_ema = 1.0  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            DispatcherHealth(
                drop_rate_ema=0.0,
                queue_utilization=0.0,
                total_dropped=0,
                total_pushed=0,
                extra="bad",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Dispatcher Health & EMA Tests
# ---------------------------------------------------------------------------


class TestDispatcherEMAHealth:
    """Tests for EMA drop-rate tracking and health() method."""

    def test_health_initial_state(self) -> None:
        """health() returns zero state on fresh dispatcher."""
        dispatcher: Dispatcher[str] = Dispatcher()
        health: DispatcherHealth = dispatcher.health()
        assert health.drop_rate_ema == 0.0
        assert health.queue_utilization == 0.0
        assert health.total_dropped == 0
        assert health.total_pushed == 0

    def test_health_after_push_no_drop(self) -> None:
        """EMA stays near 0 with no drops."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=100),
        )
        for i in range(50):
            dispatcher.push(i)
        health: DispatcherHealth = dispatcher.health()
        assert health.drop_rate_ema == pytest.approx(0.0, abs=1e-10)
        assert health.total_dropped == 0
        assert health.total_pushed == 50

    def test_health_ema_rises_on_drops(self) -> None:
        """EMA rises when drops occur."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=2, ema_alpha=0.5),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        # Queue full — next push drops
        dispatcher.push(3)
        health: DispatcherHealth = dispatcher.health()
        assert health.drop_rate_ema > 0.0
        assert health.total_dropped == 1

    def test_health_ema_decays_without_drops(self) -> None:
        """EMA decays back toward 0 when no drops occur."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=2, ema_alpha=0.5),
        )
        # Fill and drop
        dispatcher.push(1)
        dispatcher.push(2)
        dispatcher.push(3)  # drop
        ema_after_drop: float = dispatcher.health().drop_rate_ema

        # Poll to free space, then push without drops (large maxlen)
        dispatcher.poll(max_events=10)
        # After poll, queue is empty (0/2), so 20 pushes into maxlen=2
        # will cause 18 drops. Instead, just poll between pushes.
        for _ in range(20):
            dispatcher.poll(max_events=10)
            dispatcher.push(0)
        ema_after_recovery: float = dispatcher.health().drop_rate_ema
        assert ema_after_recovery < ema_after_drop

    def test_health_queue_utilization(self) -> None:
        """queue_utilization reflects current fill ratio."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=100),
        )
        for i in range(50):
            dispatcher.push(i)
        health: DispatcherHealth = dispatcher.health()
        assert health.queue_utilization == pytest.approx(0.5)

    def test_health_returns_frozen_model(self) -> None:
        """health() returns a frozen DispatcherHealth model."""
        dispatcher: Dispatcher[str] = Dispatcher()
        health: DispatcherHealth = dispatcher.health()
        assert isinstance(health, DispatcherHealth)

    def test_clear_resets_ema(self) -> None:
        """clear() resets EMA to 0."""
        dispatcher: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=2, ema_alpha=0.5),
        )
        dispatcher.push(1)
        dispatcher.push(2)
        dispatcher.push(3)  # drop
        assert dispatcher.health().drop_rate_ema > 0.0

        dispatcher.clear()
        assert dispatcher.health().drop_rate_ema == 0.0

    def test_ema_alpha_configurable(self) -> None:
        """Different alpha values produce different EMA responses."""
        # High alpha → faster response
        d_fast: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=1, ema_alpha=0.9),
        )
        d_fast.push(1)
        d_fast.push(2)  # drop

        # Low alpha → slower response
        d_slow: Dispatcher[int] = Dispatcher(
            config=DispatcherConfig(maxlen=1, ema_alpha=0.01),
        )
        d_slow.push(1)
        d_slow.push(2)  # drop

        # Higher alpha → higher EMA after single drop
        assert d_fast.health().drop_rate_ema > d_slow.health().drop_rate_ema
