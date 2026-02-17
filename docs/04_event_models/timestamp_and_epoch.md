# Timestamp and Epoch

Understanding timing fields and reconnect tracking in event models.

---

## Timestamp Fields

Every event includes **two timestamps** for different purposes:

### recv_ts: int
**Wall clock timestamp** (nanoseconds since Unix epoch).

**Source**: `time.time_ns()` at MQTT message receipt

**Purpose**:
- Correlation with external logs
- Absolute time reference
- Exchange timestamp comparison

**Characteristics**:
- ✅ Human-readable (can convert to datetime)
- ✅ Synchronized across machines (via NTP)
- ⚠️ Subject to NTP adjustment (may jump backwards)
- ⚠️ Not monotonic

**Example**:
```python
import time
from datetime import datetime

recv_ts = event.recv_ts  # e.g., 1739500000123456789

# Convert to seconds
ts_sec = recv_ts / 1e9

# Convert to datetime
dt = datetime.fromtimestamp(ts_sec)
print(dt)  # 2025-02-14 14:13:20.123456
```

---

### recv_mono_ns: int
**Monotonic timestamp** (nanoseconds).

**Source**: `time.perf_counter_ns()` at MQTT message receipt

**Purpose**:
- Latency measurement
- Elapsed time calculation
- Performance profiling

**Characteristics**:
- ✅ Never goes backwards
- ✅ High resolution (~1 microsecond)
- ✅ Immune to NTP adjustment
- ⚠️ Not human-readable
- ⚠️ Cannot compare across processes

**Example**:
```python
# Measure processing latency
start_mono = event.recv_mono_ns
process_event(event)
end_mono = time.perf_counter_ns()

latency_ns = end_mono - start_mono
latency_us = latency_ns / 1000
print(f"Processing latency: {latency_us:.2f} µs")
```

---

## When to Use Which Timestamp

| Use Case | Use recv_ts | Use recv_mono_ns |
|----------|-------------|------------------|
| Log correlation | ✅ | ❌ |
| Human-readable time | ✅ | ❌ |
| Latency measurement | ❌ | ✅ |
| Elapsed time | ❌ | ✅ |
| Performance profiling | ❌ | ✅ |
| Compare with exchange timestamps | ✅ | ❌ |
| Detect clock skew | ⚠️ (problematic) | ✅ |

---

## Connection Epoch

### connection_epoch: int
**Reconnect counter** (increments on each MQTT reconnect).

**Initial value**: `0` (first connection)

**Increment**: Incremented by `+1` after each reconnect

**Purpose**:
- Detect when strategy needs to reset state
- Identify events from different connection sessions
- Debug reconnect-related issues

---

## Reconnect Detection Pattern

**Problem**: After reconnect, strategy state may be stale.

**Solution**: Track `connection_epoch` to detect reconnects.

```python
from core.dispatcher import Dispatcher

dispatcher = Dispatcher(maxsize=10000)
last_epoch = None

for event in dispatcher.poll():
    if last_epoch is None:
        last_epoch = event.connection_epoch
    
    if event.connection_epoch != last_epoch:
        print(f"🔄 Reconnect detected! Epoch changed: {last_epoch} → {event.connection_epoch}")
        
        # Reset strategy state
        clear_position_tracking()
        clear_cached_market_data()
        
        last_epoch = event.connection_epoch
    
    # Process event normally
    process_event(event)
```

---

## Epoch Semantics

### All events from same connection have same epoch

**Contract**: Within a single MQTT connection session, all events have the same `connection_epoch`.

```python
# First connection (epoch=0)
event1 = BestBidAsk(..., connection_epoch=0)
event2 = BestBidAsk(..., connection_epoch=0)
event3 = BestBidAsk(..., connection_epoch=0)

# Reconnect occurs
# New connection (epoch=1)
event4 = BestBidAsk(..., connection_epoch=1)
event5 = BestBidAsk(..., connection_epoch=1)
```

**Test**: `test_settrade_adapter.py::test_connection_epoch_tracked`

---

### Epoch survives reconnect

**Contract**: After reconnect, new events have incremented epoch.

```python
# Before reconnect
assert all(e.connection_epoch == 0 for e in old_events)

# After reconnect
assert all(e.connection_epoch == 1 for e in new_events)
```

**Test**: `test_settrade_adapter.py::test_connection_epoch_increments_on_reconnect`

---

## Why Two Timestamps?

### Historical Context

Early implementations used only `recv_ts` (wall clock).

**Problem discovered**: NTP adjustments caused latency measurements to be negative:
```python
latency = event2.recv_ts - event1.recv_ts
# Could be negative if NTP adjusted clock backwards!
```

**Solution**: Add `recv_mono_ns` for latency measurement.

---

### CPython Implementation Notes

**recv_ts** → `time.time_ns()`:
- System clock
- Calls `clock_gettime(CLOCK_REALTIME)`
- Subject to NTP `adjtime()` adjustments
- **Use for**: Absolute time, logging

**recv_mono_ns** → `time.perf_counter_ns()`:
- Monotonic clock
- Calls `clock_gettime(CLOCK_MONOTONIC)`
- Never goes backwards
- **Use for**: Intervals, latency

---

## Example: Latency Tracking

```python
import time
from collections import deque

class LatencyTracker:
    def __init__(self, window_size: int = 100):
        self.latencies: deque[int] = deque(maxlen=window_size)
    
    def record(self, event):
        # Measure time from event receipt to processing
        now_mono = time.perf_counter_ns()
        latency_ns = now_mono - event.recv_mono_ns
        self.latencies.append(latency_ns)
    
    def get_stats_us(self) -> dict:
        if not self.latencies:
            return {}
        
        latencies_us = [lat / 1000 for lat in self.latencies]
        return {
            "mean": sum(latencies_us) / len(latencies_us),
            "min": min(latencies_us),
            "max": max(latencies_us),
            "p50": sorted(latencies_us)[len(latencies_us) // 2],
            "p99": sorted(latencies_us)[int(len(latencies_us) * 0.99)],
        }

# Usage
tracker = LatencyTracker()

for event in dispatcher.poll():
    tracker.record(event)
    process_event(event)
    
    if should_print_stats():
        stats = tracker.get_stats_us()
        print(f"Latency: mean={stats['mean']:.2f}µs p99={stats['p99']:.2f}µs")
```

---

## Implementation Reference

See:
- [core/events.py](../../core/events.py) — Event model definitions
- [infra/settrade_adapter.py](../../infra/settrade_adapter.py) — Timestamp injection
- [core/dispatcher.py](../../core/dispatcher.py) — Epoch propagation

---

## Test Coverage

Key tests:
- `test_events.py::test_recv_mono_ns_negative_rejected` — Validates monotonic timestamp >= 0
- `test_settrade_adapter.py::test_recv_ts_and_recv_mono_ns_populated` — Ensures both timestamps set
- `test_settrade_adapter.py::test_connection_epoch_tracked` — Validates epoch behavior
- `test_settrade_adapter.py::test_connection_epoch_increments_on_reconnect` — Reconnect tracking

---

## Next Steps

- **[BestBidAsk](./best_bid_ask.md)** — Field reference
- **[Event Contract](./event_contract.md)** — Model specifications
- **[Global Liveness](../06_feed_liveness/global_liveness.md)** — Feed health tracking
