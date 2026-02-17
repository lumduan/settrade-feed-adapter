# Overflow Policy

Handling queue overflow when backpressure occurs.

---

## Overview

When the dispatcher queue reaches capacity (`maxsize`), new events cannot be added. The **overflow policy** determines how to handle this condition.

**Current Policy**: **Drop on overflow** (fail-fast)

---

## put_if_fits() Semantics

### Contract

**Behavior**: Try to add event to queue without blocking.

**Success**: Returns `True`, event added to queue

**Failure**: Returns `False`, event **dropped** (not added)

```python
success = dispatcher.put_if_fits(event)

if not success:
    # Event dropped due to overflow
    log_overflow(event)
```

**Test**: `test_dispatcher.py::test_put_if_fits_rejects_when_full`

---

### Non-Blocking Guarantee

**Contract**: `put_if_fits()` never blocks the caller.

**Why?**
- MQTT callback thread must not block
- Blocking MQTT thread → reconnects, message loss
- Fail-fast allows caller to handle overflow

**Implementation**:
```python
def put_if_fits(self, item: T) -> bool:
    try:
        self._queue.put_nowait(item)  # Non-blocking put
        return True
    except queue.Full:
        self._overflow_count += 1
        return False
```

---

## Overflow Tracking

### _overflow_count

**Contract**: Dispatcher tracks total number of dropped events.

```python
dispatcher = Dispatcher(maxsize=100)

# Fill queue + overflow
for i in range(150):
    dispatcher.put_if_fits(event)

print(dispatcher._overflow_count)  # 50 (events dropped)
```

**Test**: `test_dispatcher.py::test_overflow_count_tracked`

---

### Observability

**Prometheus metric**:
```python
from prometheus_client import Counter

overflow_counter = Counter('dispatcher_overflow_total', 'Total events dropped')

# After put_if_fits()
if not success:
    overflow_counter.inc()
```

**Log on overflow**:
```python
import logging

logger = logging.getLogger(__name__)

success = dispatcher.put_if_fits(event)
if not success:
    logger.warning(
        "Dispatcher overflow",
        extra={
            "symbol": event.symbol,
            "queue_size": dispatcher._queue.qsize(),
            "maxsize": dispatcher._maxsize,
            "overflow_count": dispatcher._overflow_count,
        }
    )
```

---

## Overflow Scenarios

### Scenario 1: Consumer Too Slow

**Symptom**: Queue fills up even during normal market hours.

**Root cause**: Consumer processing time > message arrival rate.

**Solution**:
1. Optimize consumer (reduce processing time)
2. Increase `maxsize` (temporary mitigation)
3. Filter symbols (reduce message rate)

---

### Scenario 2: Burst Traffic

**Symptom**: Queue fills during market open or volatile periods.

**Root cause**: Temporary spike in message rate.

**Solution**:
1. Increase `maxsize` to handle bursts
2. Use exponential moving average to smooth processing

---

### Scenario 3: Consumer Stalled

**Symptom**: Queue fills instantly, consumer not processing.

**Root cause**: Consumer blocked or crashed.

**Solution**:
1. Check consumer thread is alive: `consumer_thread.is_alive()`
2. Add watchdog to restart stalled consumer
3. Log consumer health metrics

---

## Alternative Policies (Not Implemented)

### Block on Overflow

**Behavior**: Block caller until space available.

**Pros**: No data loss

**Cons**: Blocks MQTT thread → reconnects, deadlocks

**Verdict**: ❌ Not suitable for MQTT callback thread

---

### Reject Oldest

**Behavior**: Remove oldest event from queue to make space.

**Pros**: Always accepts new events

**Cons**: Unpredictable data loss, complex implementation

**Verdict**: ❌ Not implemented (use drop-on-overflow instead)

---

### Callback on Overflow

**Behavior**: Call user-provided callback when overflow occurs.

**Pros**: User can implement custom policy

**Cons**: Adds complexity, callback must not block

**Verdict**: 🤔 Possible future enhancement

---

## Monitoring Overflow Health

### Overflow Rate

**Definition**: Events dropped per second.

```python
import time

last_overflow = dispatcher._overflow_count
last_time = time.time()

# Later...
now = time.time()
current_overflow = dispatcher._overflow_count

overflow_rate = (current_overflow - last_overflow) / (now - last_time)
print(f"Overflow rate: {overflow_rate:.2f} events/sec")
```

**Healthy**: 0 events/sec

**Warning**: > 0 events/sec (investigate consumer)

**Critical**: > 100 events/sec (consumer stalled)

---

### Overflow Percentage

**Definition**: Percentage of events dropped.

```python
total_events = dispatcher.get_health().get("total_received", 0)
overflow_count = dispatcher._overflow_count

overflow_pct = (overflow_count / total_events * 100) if total_events > 0 else 0.0
print(f"Overflow: {overflow_pct:.2f}%")
```

**Healthy**: < 0.1%

**Warning**: 0.1% - 1%

**Critical**: > 1%

---

## Recovery from Overflow

### Automatic Recovery

**Contract**: Once consumer catches up, overflow stops automatically.

```python
# Queue full (overflow)
while not dispatcher.put_if_fits(event):
    time.sleep(0.001)  # Wait for consumer

# Consumer catches up
# Queue has space again
# New events accepted
```

---

### Manual Intervention

**Increase maxsize** (requires restart):
```python
# Old
dispatcher = Dispatcher(maxsize=10000)

# New
dispatcher = Dispatcher(maxsize=50000)
```

**Add filtering** (reduce message rate):
```python
# Only subscribe to needed symbols
symbols = ["AOT", "PTT"]  # Instead of all 800+
client.subscribe_to_symbols(symbols)
```

---

## Tuning Guidelines

### Target: Zero Overflow

**Goal**: Size queue such that overflow never occurs during normal operation.

**Formula**:
```
maxsize = peak_message_rate × max_consumer_latency × safety_factor
```

**Example**:
- Peak rate: 2,000 events/sec (market open)
- Max latency: 0.5 sec (consumer processing)
- Safety factor: 20x

```
maxsize = 2,000 × 0.5 × 20 = 20,000
```

---

### Monitoring Alert

**Prometheus alert**:
```yaml
- alert: DispatcherOverflow
  expr: rate(dispatcher_overflow_total[1m]) > 0
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "Dispatcher overflow detected"
    description: "Events are being dropped due to queue overflow"
```

---

## Implementation Reference

See [core/dispatcher.py](../../core/dispatcher.py):
- `put_if_fits()` method
- `_overflow_count` tracking
- Queue overflow handling

---

## Test Coverage

Key tests in `test_dispatcher.py`:
- `test_put_if_fits_returns_false_when_full` — Overflow behavior
- `test_put_if_fits_rejects_when_full` — Event rejection
- `test_overflow_count_tracked` — Overflow counting
- `test_overflow_count_resets` — Reset on clear

---

## Next Steps

- **[Queue Model](./queue_model.md)** — Queue architecture
- **[Health and EMA](./health_and_ema.md)** — Queue health metrics
- **[Tuning Guide](../09_production_guide/tuning_guide.md)** — Performance tuning
