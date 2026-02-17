# Health Monitoring and EMA Drop-Rate Tracking

The Dispatcher exposes a `health()` method that returns a frozen Pydantic model
with real-time backpressure indicators. The centrepiece is an exponential moving
average (EMA) of the drop rate, which smooths out transient spikes and provides
a stable signal for alerting.

## DispatcherHealth Model

`health()` returns a frozen, immutable Pydantic model -- not a dict:

```python
class DispatcherHealth(BaseModel, frozen=True, extra="forbid"):
    drop_rate_ema: float       # ge=0.0  -- 0.0 = no drops, 1.0 = every push drops
    queue_utilization: float   # ge=0.0, le=1.0
    total_dropped: int         # ge=0
    total_pushed: int          # ge=0
```

Usage from any thread:

```python
h = dispatcher.health()
print(h.drop_rate_ema)         # e.g. 0.003
print(h.queue_utilization)     # e.g. 0.72
print(h.total_dropped)         # e.g. 14
print(h.total_pushed)          # e.g. 50000
```

## EMA Formula

On every call to `push()`, the Dispatcher updates its running EMA of the drop
rate:

```text
sample = 1.0   if the push caused a drop (queue was full before append)
         0.0   otherwise

ema = alpha * sample + (1 - alpha) * ema
```

This is a standard single-pole exponential moving average:

- When drops are occurring (`sample = 1.0`), the EMA rises toward 1.0.
- When no drops occur (`sample = 0.0`), the EMA decays toward 0.0.

The update happens inside the hot-path `push()` method, so it adds negligible
overhead -- a single multiply-add per push.

## Configuration

Both EMA parameters live in `DispatcherConfig`:

| Parameter | Default | Constraint | Meaning |
| --- | --- | --- | --- |
| `ema_alpha` | 0.01 | gt=0.0, le=1.0 | Smoothing factor. Smaller values respond more slowly. Default gives roughly a 100-message half-life. |
| `drop_warning_threshold` | 0.01 | gt=0.0, le=1.0 | EMA level that triggers a warning log. Default is 0.01 (1% drop rate). |

Example with custom values:

```python
from core.dispatcher import Dispatcher, DispatcherConfig

cfg = DispatcherConfig(
    maxlen=200_000,
    ema_alpha=0.05,
    drop_warning_threshold=0.02,
)
dispatcher: Dispatcher[BestBidAsk] = Dispatcher(config=cfg)
```

## Warning and Recovery Logging

The Dispatcher emits structured log messages when the EMA crosses the configured
threshold:

- **Warning log** -- emitted the first time `drop_rate_ema` rises above
  `drop_warning_threshold`. This signals sustained backpressure, not just a
  single dropped event.
- **Info log (recovery)** -- emitted when the EMA falls back below the
  threshold, indicating the consumer has caught up.

Logging is limited to threshold-crossing transitions to avoid flooding the log
during sustained drop periods.

## Queue Utilisation

```text
queue_utilization = len(queue) / maxlen
```

This value ranges from 0.0 (empty) to 1.0 (full). It provides an at-a-glance
measure of how close the queue is to capacity. A sustained value near 1.0
combined with a rising `drop_rate_ema` indicates the consumer cannot keep up
with the producer.

## Effect of clear()

Calling `clear()` resets the EMA to 0.0 along with all counters and the queue
itself. After a clear, `health()` returns:

```python
DispatcherHealth(
    drop_rate_ema=0.0,
    queue_utilization=0.0,
    total_dropped=0,
    total_pushed=0,
)
```

This is intentional -- `clear()` represents a full lifecycle reset (e.g. on
reconnect), so historical drop state should not carry over.

## Consistency Guarantees

All fields returned by `health()` are **eventually consistent**. There are no
locks protecting the reads. Instead, the SPSC thread-ownership contract ensures
each value has a single writer:

| Field | Written by |
| --- | --- |
| `drop_rate_ema` | MQTT IO thread (inside `push()`) |
| `queue_utilization` | Derived from `len(queue)` and `maxlen` at read time |
| `total_dropped` | MQTT IO thread (inside `push()`) |
| `total_pushed` | MQTT IO thread (inside `push()`) |

CPython's GIL guarantees that individual int and float reads are atomic, so
observer threads always see a valid (though potentially slightly stale) value
without explicit locks.

## Interpreting the EMA

| `drop_rate_ema` range | Interpretation |
| --- | --- |
| 0.0 | No drops have occurred recently. |
| 0.001 -- 0.01 | Occasional drops, likely transient bursts. |
| 0.01 -- 0.05 | Moderate sustained backpressure. Investigate consumer throughput. |
| 0.05 -- 0.20 | Heavy backpressure. Consumer is significantly behind. |
| above 0.20 | Severe. Nearly every push is evicting data. Consider increasing `maxlen` or optimising the consumer. |

## Test Coverage

Tests confirm:

- EMA rises when drops occur
- EMA decays toward zero when no drops occur
- `queue_utilization` is correct relative to current queue length and `maxlen`
- `clear()` resets EMA to 0.0
- All health fields are non-negative and within documented bounds
