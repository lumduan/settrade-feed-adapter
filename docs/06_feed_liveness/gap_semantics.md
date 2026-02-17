# Gap Semantics

Time units, conversion, and gap measurement in the feed health monitor.

Source: `core/feed_health.py` -- `FeedHealthMonitor`

---

## Overview

The `FeedHealthMonitor` measures gaps between events using monotonic
nanosecond timestamps. This page documents how time units are converted,
how gaps are computed, and the design decisions behind the implementation.

---

## Nanosecond Internal Representation

Configuration uses seconds (human-friendly), but all internal state is stored
in nanoseconds for integer arithmetic precision.

At construction time, `FeedHealthMonitor` converts:

```python
_max_gap_ns = int(max_gap_seconds * 1_000_000_000)
_per_symbol_max_gap_ns = {
    symbol: int(gap * 1_000_000_000)
    for symbol, gap in config.per_symbol_max_gap.items()
}
```

This conversion happens once at init and is cached. The frozen config ensures
the cached values cannot desync from the config.

---

## Monotonic Timestamps

All timestamps use `time.perf_counter_ns()` exclusively. This is a monotonic
clock that:

- Never goes backwards (immune to NTP adjustments)
- Has nanosecond resolution
- Is not affected by system clock changes, leap seconds, or daylight savings
- Is suitable for measuring elapsed time, not wall-clock time

The `now_ns` parameter on all query methods (`is_feed_dead()`, `is_stale()`,
`stale_symbols()`, `last_seen_gap_ms()`) allows injecting a pre-captured
timestamp. This avoids redundant `perf_counter_ns()` calls when checking
multiple symbols in a single poll loop:

```python
now = time.perf_counter_ns()
is_dead = monitor.is_feed_dead(now_ns=now)
stale = monitor.stale_symbols(now_ns=now)
gap_ptt = monitor.last_seen_gap_ms("PTT", now_ns=now)
```

If `now_ns` is not provided, each method captures its own timestamp internally.

---

## Gap Computation

All gap computations follow the same pattern:

```text
gap = max(0, now - last_event_ns)
```

The `max(0, ...)` clamp prevents negative deltas from producing incorrect
results. A negative delta can occur when `now_ns` is injected with a value
earlier than the last recorded timestamp (e.g., in tests or if timestamps
are captured in an unexpected order).

The comparison is strictly greater than:

```text
return gap > threshold_ns
```

This means a gap exactly equal to the threshold is **not** considered
stale/dead. The feed must exceed the threshold to trigger detection.

---

## Negative Delta Clamping

When `now_ns` is less than the last recorded timestamp, the gap is clamped
to zero rather than producing a negative value. This ensures:

- `is_feed_dead()` returns `False` (gap 0 is not > threshold)
- `is_stale()` returns `False` (gap 0 is not > threshold)
- `last_seen_gap_ms()` returns `0.0` (not a negative millisecond value)

```python
monitor = FeedHealthMonitor()
monitor.on_event("PTT", now_ns=1_000_000_000)

# now_ns < last event -- clamped to 0
monitor.last_seen_gap_ms("PTT", now_ns=500_000_000)   # 0.0
monitor.is_stale("PTT", now_ns=500_000_000)            # False
monitor.is_feed_dead(now_ns=500_000_000)                # False
```

Tests:

- `test_feed_health.py::TestGlobalFeedLiveness::test_negative_delta_clamped`
- `test_feed_health.py::TestPerSymbolLiveness::test_negative_delta_clamped_per_symbol`
- `test_feed_health.py::TestLastSeenGapMs::test_negative_delta_clamped`

---

## last_seen_gap_ms()

```python
def last_seen_gap_ms(self, symbol: str, now_ns: int | None = None) -> float | None
```

Returns the gap in **milliseconds** since the last event for a symbol, or
`None` if the symbol has never been seen.

The conversion from nanoseconds to milliseconds:

```text
gap_ms = max(0, now - last_ns) / 1_000_000
```

```python
monitor = FeedHealthMonitor()
base_ns = 1_000_000_000_000
monitor.on_event("PTT", now_ns=base_ns)

gap = monitor.last_seen_gap_ms("PTT", now_ns=base_ns + 500_000_000)
# gap == 500.0 (milliseconds)

gap = monitor.last_seen_gap_ms("UNKNOWN")
# gap is None (never seen)
```

Tests:

- `test_feed_health.py::TestLastSeenGapMs::test_returns_none_for_unknown_symbol`
- `test_feed_health.py::TestLastSeenGapMs::test_returns_correct_gap`
- `test_feed_health.py::TestLastSeenGapMs::test_negative_delta_clamped`

---

## Per-Symbol Override Dictionary

The `per_symbol_max_gap` field in `FeedHealthConfig` maps symbol names to
custom gap thresholds in seconds:

```python
config = FeedHealthConfig(
    max_gap_seconds=5.0,
    per_symbol_max_gap={"RARE": 60.0, "ILLIQUID": 30.0},
)
```

At init, each override is converted to nanoseconds and stored in
`_per_symbol_max_gap_ns`. When checking staleness, the monitor looks up the
symbol in the override dictionary first. If not found, it falls back to the
global `_max_gap_ns`.

```python
# In is_stale() and stale_symbols():
max_gap = self._per_symbol_max_gap_ns.get(symbol, self._max_gap_ns)
gap = max(0, now - last_ns)
return gap > max_gap
```

The `per_symbol_max_gap` dictionary uses `default_factory=dict`, so each
config instance gets its own dictionary (no shared mutable default).

---

## Configuration Constraints

| Field | Type | Default | Constraint |
| --- | --- | --- | --- |
| `max_gap_seconds` | `float` | `5.0` | `gt=0` (must be positive) |
| `per_symbol_max_gap` | `dict[str, float]` | `{}` | No constraints on values |

`FeedHealthConfig` is a frozen Pydantic model (`frozen=True`, `extra="forbid"`).
Mutating fields or passing extra fields raises `ValidationError`.

---

## Related Pages

- [Global Liveness](./global_liveness.md) -- `is_feed_dead()` semantics
- [Per-Symbol Liveness](./per_symbol_liveness.md) -- `is_stale()` and `stale_symbols()`
