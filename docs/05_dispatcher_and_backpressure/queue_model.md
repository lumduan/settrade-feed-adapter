# Queue Model

Understanding the dispatcher's internal queue architecture.

---

## Overview

The Dispatcher uses a **bounded MPMC queue** (multi-producer multi-consumer) with:
- Fixed capacity (`maxsize`)
- Non-copying semantics (references only)
- Lock-based synchronization (Python `queue.Queue`)
- Configurable overflow policy

---

## Queue Implementation

**Type**: `queue.Queue[T]` (standard library)

**Configuration**:
```python
dispatcher = Dispatcher(maxsize=10000)
# Creates queue.Queue(maxsize=10000)
```

**Why `queue.Queue`?**
- ✅ Thread-safe (CPython GIL + internal locks)
- ✅ Blocking and non-blocking operations
- ✅ Production-proven
- ✅ Memory-efficient (stores references, not copies)

---

## Queue Capacity

### maxsize Parameter

**Contract**: Queue can hold up to `maxsize` events.

```python
dispatcher = Dispatcher(maxsize=100)

# Producer puts 100 events → OK
# Producer puts 101st event → Blocks or rejects (depends on policy)
```

**Test**: `test_dispatcher.py::test_maxsize_enforced`

---

### Choosing maxsize

**Formula**:
```
maxsize ≥ (message_rate_per_sec × max_processing_time_sec) × safety_factor
```

**Example**:
- Message rate: 1,000 events/sec
- Max processing time: 0.5 sec (500ms)
- Safety factor: 20x

```
maxsize = 1,000 × 0.5 × 20 = 10,000
```

**Guidelines**:
- **Small** (100-1,000): Low-latency strategies, fast processing
- **Medium** (10,000-50,000): Production systems, moderate bursts
- **Large** (100,000+): High-frequency, bursty feeds

**Memory impact**:
- BestBidAsk: ~200 bytes/event
- FullBidOffer: ~440 bytes/event
- 10,000 events ≈ 2-4 MB

---

## Queue Operations

### put_nowait(event)

**Behavior**: Add event to queue without blocking.

**Success**: Event added to queue

**Failure**: Raises `queue.Full` if queue is full

```python
try:
    dispatcher._queue.put_nowait(event)
except queue.Full:
    # Handle overflow (see overflow_policy.md)
    handle_overflow(event)
```

**Thread-safety**: ✅ Safe to call from multiple threads

---

### get(timeout)

**Behavior**: Remove and return event from queue with timeout.

**Success**: Returns event

**Timeout**: Raises `queue.Empty` if timeout expires

```python
try:
    event = dispatcher._queue.get(timeout=0.1)  # 100ms timeout
except queue.Empty:
    # No events available
    continue
```

**Thread-safety**: ✅ Safe to call from multiple threads

---

### qsize()

**Behavior**: Return approximate number of events in queue.

**Note**: Result is approximate due to thread-safety semantics.

```python
size = dispatcher._queue.qsize()
print(f"Queue depth: {size}/{dispatcher._maxsize}")
```

**Use**: Monitoring, health checks

---

## Queue Health Metrics

### Current Depth

**Definition**: Number of events currently in queue.

```python
depth = dispatcher._queue.qsize()
```

**Healthy**: < 50% of maxsize

**Warning**: 50-80% of maxsize

**Critical**: > 80% of maxsize

---

### Fill Ratio

**Definition**: Current depth as percentage of capacity.

```python
fill_ratio = dispatcher._queue.qsize() / dispatcher._maxsize
```

**Healthy**: < 0.5 (50%)

**Warning**: 0.5 - 0.8

**Critical**: > 0.8

---

### Overflow Count

**Definition**: Number of events rejected due to queue full.

**Tracking**:
```python
from core.dispatcher import Dispatcher

dispatcher = Dispatcher(maxsize=100)
print(f"Overflows: {dispatcher._overflow_count}")  # Tracks rejections
```

**Test**: `test_dispatcher.py::test_overflow_count_tracked`

---

## Memory Characteristics

### Storage Semantics

**Contract**: Queue stores **references** to events, not copies.

```python
event = BestBidAsk(...)
dispatcher.put_if_fits(event)
# Queue stores reference to `event`, not a copy
```

**Memory**: O(N) where N = queue depth

**Performance**: O(1) put/get (amortized)

---

### Memory Layout

```
Queue object: ~100 bytes
+ (N events × reference size)
+ (N events × event size)

Reference: 8 bytes (CPython 64-bit)
BestBidAsk: ~200 bytes
FullBidOffer: ~440 bytes

Total for 10,000 BestBidAsk events:
  100 + (10,000 × 8) + (10,000 × 200) ≈ 2.08 MB
```

---

## Queue Observability

### Health Check

```python
def check_queue_health(dispatcher: Dispatcher) -> dict:
    depth = dispatcher._queue.qsize()
    capacity = dispatcher._maxsize
    fill_ratio = depth / capacity if capacity > 0 else 0.0
    
    return {
        "depth": depth,
        "capacity": capacity,
        "fill_ratio": fill_ratio,
        "status": "healthy" if fill_ratio < 0.5 else "warning" if fill_ratio < 0.8 else "critical",
    }
```

---

### Monitoring Integration

**Prometheus metrics**:
```python
from prometheus_client import Gauge

queue_depth = Gauge('dispatcher_queue_depth', 'Current queue depth')
queue_capacity = Gauge('dispatcher_queue_capacity', 'Queue capacity')
queue_fill_ratio = Gauge('dispatcher_queue_fill_ratio', 'Queue fill ratio')

# Update metrics
queue_depth.set(dispatcher._queue.qsize())
queue_capacity.set(dispatcher._maxsize)
queue_fill_ratio.set(dispatcher._queue.qsize() / dispatcher._maxsize)
```

---

## Queue Tuning

### Symptoms of Undersized Queue

- Frequent overflow errors
- High overflow count
- Events dropped even when consumer is processing
- `queue.Full` exceptions in logs

**Solution**: Increase `maxsize`

---

### Symptoms of Oversized Queue

- High memory usage (> 100 MB for queue)
- Long processing delays (events stale before consumed)
- Consumer cannot keep up even with large buffer

**Solution**: Decrease `maxsize` or optimize consumer

---

## Implementation Reference

See [core/dispatcher.py](../../core/dispatcher.py):
- Queue initialization (`__init__`)
- `put_if_fits()` method
- `poll()` method
- Overflow tracking

---

## Test Coverage

Key tests in `test_dispatcher.py`:
- `test_maxsize_enforced` — Capacity limits
- `test_put_if_fits_rejects_when_full` — Overflow behavior
- `test_overflow_count_tracked` — Rejection tracking
- `test_poll_empty_queue` — Empty queue behavior

---

## Next Steps

- **[Overflow Policy](./overflow_policy.md)** — Handling queue full conditions
- **[Health and EMA](./health_and_ema.md)** — Queue health metrics
- **[Threading and Concurrency](../01_system_overview/threading_and_concurrency.md)** — Thread-safety details
