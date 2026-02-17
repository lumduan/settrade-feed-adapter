# Global Feed Liveness

Detecting whether the entire MQTT feed is alive or dead.

Source: `core/feed_health.py` -- `FeedHealthMonitor.is_feed_dead()`

---

## Overview

Global liveness answers one question: **has any event arrived recently?**

The `FeedHealthMonitor` tracks the timestamp of the most recent event across
all symbols. If the gap between now and that timestamp exceeds
`max_gap_seconds`, the feed is considered dead.

---

## Startup-Aware Behavior

Before the first event is received, `is_feed_dead()` returns `False`. This is
intentional -- the monitor cannot distinguish "feed is dead" from "feed has not
started yet." Use `has_ever_received()` to tell these states apart.

```python
from core.feed_health import FeedHealthMonitor

monitor = FeedHealthMonitor()

# Before any event
monitor.is_feed_dead()        # False (unknown, not dead)
monitor.has_ever_received()   # False (no events yet)

# After first event
monitor.on_event("PTT")
monitor.is_feed_dead()        # False (just received)
monitor.has_ever_received()   # True
```

**State transitions:**

| `has_ever_received()` | `is_feed_dead()` | Meaning |
| --- | --- | --- |
| `False` | `False` | Startup -- no events yet |
| `True` | `False` | Healthy -- events arriving within threshold |
| `True` | `True` | Dead -- gap exceeds `max_gap_seconds` |

The combination `has_ever_received() == False` and `is_feed_dead() == True`
never occurs. Before the first event, `is_feed_dead()` always returns `False`.

---

## Feed Dead Detection

After the first event, `is_feed_dead()` computes:

```text
gap = max(0, now - last_global_event_ns)
return gap > max_gap_ns
```

The comparison is strictly greater than (`>`). A gap exactly equal to
`max_gap_seconds` is **not** considered dead.

```python
from core.feed_health import FeedHealthMonitor, FeedHealthConfig

monitor = FeedHealthMonitor(
    config=FeedHealthConfig(max_gap_seconds=5.0),
)
base_ns = 1_000_000_000_000
monitor.on_event("PTT", now_ns=base_ns)

# 1 second later -- alive
monitor.is_feed_dead(now_ns=base_ns + 1_000_000_000)   # False

# Exactly 5 seconds -- alive (> not >=)
monitor.is_feed_dead(now_ns=base_ns + 5_000_000_000)   # False

# 6 seconds later -- dead
monitor.is_feed_dead(now_ns=base_ns + 6_000_000_000)   # True
```

Test: `test_feed_health.py::TestGlobalFeedLiveness::test_feed_dead_exactly_at_boundary`

---

## Feed Recovery

The feed recovers as soon as any new event arrives. There is no hysteresis
or debounce -- a single event immediately brings the feed back to alive.

```python
monitor = FeedHealthMonitor(
    config=FeedHealthConfig(max_gap_seconds=5.0),
)
base_ns = 1_000_000_000_000
monitor.on_event("PTT", now_ns=base_ns)

dead_ns = base_ns + 6_000_000_000
assert monitor.is_feed_dead(now_ns=dead_ns) is True

# New event arrives
monitor.on_event("PTT", now_ns=dead_ns)
assert monitor.is_feed_dead(now_ns=dead_ns + 100_000) is False
```

Test: `test_feed_health.py::TestGlobalFeedLiveness::test_feed_recovers_after_new_event`

---

## Negative Delta Clamping

All time computations use `max(0, now - last_ns)` to clamp negative deltas
to zero. This prevents spurious results if `now_ns` is passed out of order
during testing.

```python
monitor = FeedHealthMonitor()
monitor.on_event("PTT", now_ns=1_000_000_000)

# now_ns < last event -- clamped to 0, feed is alive
monitor.is_feed_dead(now_ns=500_000_000)   # False
```

Test: `test_feed_health.py::TestGlobalFeedLiveness::test_negative_delta_clamped`

---

## Configuration

Global liveness uses `FeedHealthConfig.max_gap_seconds`:

```python
from core.feed_health import FeedHealthConfig

config = FeedHealthConfig(
    max_gap_seconds=5.0,   # default; must be > 0
)
```

- **Type:** `float`
- **Default:** `5.0`
- **Constraint:** `gt=0` (zero and negative values rejected)
- **Internal conversion:** Multiplied by `1_000_000_000` to nanoseconds at init

The config is a frozen Pydantic model. Mutating fields after construction raises
`ValidationError`.

---

## Monotonic Timestamps

All timestamps use `time.perf_counter_ns()` (monotonic clock). This makes
liveness detection immune to NTP adjustments, leap seconds, and wall-clock
skew.

The `now_ns` parameter on `is_feed_dead()` and `on_event()` allows injecting
a pre-captured timestamp for reuse across multiple calls in a single poll
loop, avoiding redundant `perf_counter_ns()` calls.

---

## Thread Safety

`FeedHealthMonitor` is **NOT thread-safe**. It is designed for use from the
strategy/consumer thread only, consistent with the SPSC (single-producer,
single-consumer) pattern of the dispatcher.

---

## has_ever_received()

```python
def has_ever_received(self) -> bool
```

Returns `True` if at least one event has been recorded via `on_event()`.
Use this to distinguish startup (unknown) from healthy (events flowing).

---

## Test Coverage

Tests in `tests/test_feed_health.py`:

- `TestStartupState::test_is_feed_dead_false_before_first_event`
- `TestStartupState::test_has_ever_received_false_initially`
- `TestStartupState::test_has_ever_received_true_after_event`
- `TestGlobalFeedLiveness::test_feed_alive_within_gap`
- `TestGlobalFeedLiveness::test_feed_dead_beyond_gap`
- `TestGlobalFeedLiveness::test_feed_dead_exactly_at_boundary`
- `TestGlobalFeedLiveness::test_feed_recovers_after_new_event`
- `TestGlobalFeedLiveness::test_negative_delta_clamped`

---

## Related Pages

- [Per-Symbol Liveness](./per_symbol_liveness.md) -- symbol-level staleness
- [Gap Semantics](./gap_semantics.md) -- time unit conversion and gap measurement
- [Failure Scenarios](../08_testing_and_guarantees/failure_scenarios.md) -- feed silence handling
