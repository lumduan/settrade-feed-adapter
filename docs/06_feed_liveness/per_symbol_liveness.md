# Per-Symbol Liveness

Detecting when individual symbols stop receiving updates.

Source: `core/feed_health.py` -- `FeedHealthMonitor.is_stale()`, `stale_symbols()`

---

## Overview

Per-symbol liveness tracks whether a **specific symbol** has received data
within its configured threshold. This complements global liveness by detecting
cases where the overall feed is alive but a particular symbol has gone silent
(exchange halt, subscription issue, illiquid instrument).

---

## is_stale(symbol)

```python
def is_stale(self, symbol: str, now_ns: int | None = None) -> bool
```

Returns `True` if the symbol was previously seen and its gap exceeds the
threshold. Returns `False` in two cases:

1. The symbol has never been seen (use `has_seen()` to distinguish).
2. The symbol was seen recently (gap within threshold).

```python
from core.feed_health import FeedHealthMonitor, FeedHealthConfig

monitor = FeedHealthMonitor(
    config=FeedHealthConfig(max_gap_seconds=5.0),
)
base_ns = 1_000_000_000_000
monitor.on_event("PTT", now_ns=base_ns)

# Within threshold
monitor.is_stale("PTT", now_ns=base_ns + 1_000_000_000)   # False

# Beyond threshold
monitor.is_stale("PTT", now_ns=base_ns + 6_000_000_000)   # True

# Never seen
monitor.is_stale("UNKNOWN")                                 # False
```

The comparison uses strictly greater than (`>`), consistent with
`is_feed_dead()`. A gap exactly equal to the threshold is not considered stale.

---

## has_seen(symbol)

```python
def has_seen(self, symbol: str) -> bool
```

Returns `True` if at least one event for the symbol has been recorded via
`on_event()`. Use this to distinguish "never tracked" from "healthy" when
`is_stale()` returns `False`.

```python
monitor = FeedHealthMonitor()

monitor.has_seen("PTT")       # False
monitor.on_event("PTT")
monitor.has_seen("PTT")       # True
```

Test: `test_feed_health.py::TestStartupState::test_has_seen_false_for_never_seen`

---

## Per-Symbol Gap Override

Different symbols have different activity patterns. Liquid SET equities like
AOT or PTT update many times per second, while illiquid instruments may go
minutes without a quote. The `per_symbol_max_gap` dictionary configures
symbol-specific staleness thresholds.

```python
from core.feed_health import FeedHealthMonitor, FeedHealthConfig

monitor = FeedHealthMonitor(
    config=FeedHealthConfig(
        max_gap_seconds=5.0,
        per_symbol_max_gap={"RARE": 60.0, "ILLIQUID": 30.0},
    ),
)
base_ns = 1_000_000_000_000
monitor.on_event("RARE", now_ns=base_ns)
monitor.on_event("PTT", now_ns=base_ns)

check_ns = base_ns + 10_000_000_000   # 10 seconds later

monitor.is_stale("PTT", now_ns=check_ns)    # True  (10s > 5s global)
monitor.is_stale("RARE", now_ns=check_ns)   # False (10s < 60s override)
```

Symbols not in the override dictionary use the global `max_gap_seconds`.

Test: `test_feed_health.py::TestPerSymbolLiveness::test_per_symbol_gap_override`

---

## stale_symbols()

```python
def stale_symbols(self, now_ns: int | None = None) -> list[str]
```

Returns a list of all currently tracked symbols whose gap exceeds their
threshold. Cost is O(N) where N is the number of tracked symbols.

```python
monitor = FeedHealthMonitor(
    config=FeedHealthConfig(max_gap_seconds=5.0),
)
base_ns = 1_000_000_000_000
monitor.on_event("PTT", now_ns=base_ns)
monitor.on_event("AOT", now_ns=base_ns + 4_000_000_000)

# At base + 6s: PTT stale (6s > 5s), AOT alive (2s < 5s)
monitor.stale_symbols(now_ns=base_ns + 6_000_000_000)   # ["PTT"]
```

Returns an empty list when all tracked symbols are fresh.

Test: `test_feed_health.py::TestPerSymbolLiveness::test_stale_symbols_returns_stale_only`

---

## purge(symbol)

```python
def purge(self, symbol: str) -> bool
```

Removes tracking state for a single symbol. Returns `True` if the symbol was
tracked and removed, `False` if it was never tracked.

**Does not affect global feed liveness.** After purging, `has_ever_received()`
still returns `True` if any event was ever recorded.

```python
monitor = FeedHealthMonitor()
monitor.on_event("PTT", now_ns=1_000_000_000)

monitor.purge("PTT")              # True
monitor.has_seen("PTT")           # False
monitor.has_ever_received()       # True (global unaffected)

monitor.purge("UNKNOWN")          # False (was never tracked)
```

Use `purge()` when a symbol is unsubscribed or a derivatives contract expires.

Test: `test_feed_health.py::TestLifecycleManagement::test_purge_does_not_affect_global`

---

## reset()

```python
def reset(self) -> None
```

Clears all tracking state -- both global and per-symbol. Returns the monitor
to startup state: `is_feed_dead()` returns `False`, `has_ever_received()`
returns `False`, all per-symbol data is cleared.

```python
monitor = FeedHealthMonitor()
monitor.on_event("PTT", now_ns=1_000_000_000)
monitor.on_event("AOT", now_ns=2_000_000_000)

monitor.reset()

monitor.has_ever_received()       # False
monitor.has_seen("PTT")           # False
monitor.has_seen("AOT")           # False
monitor.tracked_symbol_count()    # 0
monitor.is_feed_dead()            # False
```

Use `reset()` during full reconnection or trading session boundaries.

Test: `test_feed_health.py::TestLifecycleManagement::test_reset_clears_all_state`

---

## tracked_symbol_count()

```python
def tracked_symbol_count(self) -> int
```

Returns the number of distinct symbols currently tracked. Duplicate
`on_event()` calls for the same symbol do not increase the count.

```python
monitor = FeedHealthMonitor()
monitor.on_event("PTT", now_ns=1_000_000_000)
monitor.on_event("AOT", now_ns=2_000_000_000)
monitor.on_event("PTT", now_ns=3_000_000_000)   # duplicate

monitor.tracked_symbol_count()   # 2
```

Test: `test_feed_health.py::TestLifecycleManagement::test_tracked_symbol_count`

---

## Memory Model

The per-symbol dictionary grows with the symbol universe and is **never
automatically evicted**. This is intentional for a fixed subscription model
(SET equities). For dynamic symbol universes (e.g., derivatives rolling
contracts), call `purge()` to remove unsubscribed symbols or `reset()` to
clear all state.

---

## Independent Symbol Tracking

Each symbol has independent staleness tracking. One symbol going stale does
not affect another. The global feed can be alive (recent events from some
symbols) even when individual symbols are stale.

```python
monitor = FeedHealthMonitor(
    config=FeedHealthConfig(max_gap_seconds=5.0),
)
base_ns = 1_000_000_000_000
monitor.on_event("PTT", now_ns=base_ns)
monitor.on_event("AOT", now_ns=base_ns + 3_000_000_000)

check_ns = base_ns + 6_000_000_000
monitor.is_stale("PTT", now_ns=check_ns)      # True  (6s > 5s)
monitor.is_feed_dead(now_ns=check_ns)          # False (AOT was 3s ago)
```

Test: `test_feed_health.py::TestMultipleSymbols::test_global_tracks_most_recent`

---

## Test Coverage

Tests in `tests/test_feed_health.py`:

- `TestStartupState::test_is_stale_false_for_never_seen`
- `TestStartupState::test_has_seen_false_for_never_seen`
- `TestStartupState::test_has_seen_true_after_event`
- `TestStartupState::test_tracked_symbol_count_zero_initially`
- `TestPerSymbolLiveness::test_not_stale_within_gap`
- `TestPerSymbolLiveness::test_stale_beyond_gap`
- `TestPerSymbolLiveness::test_per_symbol_gap_override`
- `TestPerSymbolLiveness::test_stale_symbols_returns_stale_only`
- `TestPerSymbolLiveness::test_stale_symbols_empty_when_all_fresh`
- `TestPerSymbolLiveness::test_negative_delta_clamped_per_symbol`
- `TestLifecycleManagement::test_purge_removes_symbol`
- `TestLifecycleManagement::test_purge_returns_false_for_unknown`
- `TestLifecycleManagement::test_purge_does_not_affect_global`
- `TestLifecycleManagement::test_reset_clears_all_state`
- `TestLifecycleManagement::test_tracked_symbol_count`
- `TestMultipleSymbols::test_independent_symbol_tracking`
- `TestMultipleSymbols::test_global_tracks_most_recent`

---

## Related Pages

- [Global Liveness](./global_liveness.md) -- system-wide feed dead detection
- [Gap Semantics](./gap_semantics.md) -- time units, conversion, and measurement
