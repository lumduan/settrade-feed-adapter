# Health and EMA

Queue health monitoring using exponential moving averages.

---

## Overview

The Dispatcher tracks **queue health** using:
- Current queue depth
- Overflow count
- Exponential moving average (EMA) of message rate

**Purpose**: Detect consumer performance degradation before overflow occurs.

---

## Health Metrics

### get_health() → dict

**Contract**: Returns dictionary with current health metrics.

```python
health = dispatcher.get_health()

print(health)
# {
#     "queue_depth": 1234,
#     "maxsize": 10000,
#     "fill_ratio": 0.1234,
#     "overflow_count": 0,
#     "is_healthy": True,
# }
```

**Test**: `test_dispatcher.py::test_get_health_returns_metrics`

---

### queue_depth

**Definition**: Number of events currently in queue.

**Source**: `dispatcher._queue.qsize()`

**Range**: [0, maxsize]

---

### maxsize

**Definition**: Maximum queue capacity.

**Source**: `dispatcher._maxsize`

**Configured at**: Dispatcher initialization

---

### fill_ratio

**Definition**: Current depth as fraction of capacity.

**Formula**: `queue_depth / maxsize`

**Range**: [0.0, 1.0]

**Healthy**: < 0.5

**Warning**: 0.5 - 0.8

**Critical**: > 0.8

---

### overflow_count

**Definition**: Total number of events dropped due to queue full.

**Source**: `dispatcher._overflow_count`

**Range**: [0, ∞)

**Healthy**: 0

**Warning**: > 0 (investigate consumer)

---

### is_healthy

**Definition**: Boolean indicating overall health status.

**Formula**:
```python
is_healthy = (fill_ratio < 0.8) and (overflow_count == 0)
```

**Use**: Quick health check for monitoring

---

## Exponential Moving Average (EMA)

### Purpose

Track **smoothed message rate** to detect trends and predict overflow.

**Why EMA?**
- ✅ Smooths out spikes (better than raw rate)
- ✅ Reacts faster than simple moving average (SMA)
- ✅ O(1) memory (no sliding window)

---

### EMA Formula

```
EMA(t) = α × value(t) + (1 - α) × EMA(t-1)
```

**Where**:
- `α` = Smoothing factor (0 < α < 1)
- `value(t)` = Current measurement
- `EMA(t-1)` = Previous EMA

**Higher α** → More weight on recent values (faster reaction)

**Lower α** → More smoothing (slower reaction)

---

### Typical α Values

| α | Half-life | Use Case |
|---|-----------|----------|
| 0.1 | ~7 samples | Long-term trend |
| 0.2 | ~3 samples | Medium-term trend |
| 0.5 | ~1 sample | Fast reaction |
| 0.9 | <1 sample | Very fast reaction |

**Recommendation**: α = 0.2 for message rate tracking

---

### Message Rate EMA Example

```python
import time

class MessageRateEMA:
    def __init__(self, alpha: float = 0.2):
        self.alpha: float = alpha
        self.ema: float = 0.0
        self.last_count: int = 0
        self.last_time: float = time.time()
    
    def update(self, current_count: int) -> float:
        """Update EMA with current message count."""
        now = time.time()
        elapsed = now - self.last_time
        
        if elapsed > 0:
            # Calculate instantaneous rate
            rate = (current_count - self.last_count) / elapsed
            
            # Update EMA
            self.ema = self.alpha * rate + (1 - self.alpha) * self.ema
            
            self.last_count = current_count
            self.last_time = now
        
        return self.ema
    
    def get_ema(self) -> float:
        """Get current EMA value."""
        return self.ema

# Usage
ema_tracker = MessageRateEMA(alpha=0.2)
total_received = 0

for event in dispatcher.poll():
    total_received += 1
    process_event(event)
    
    # Update EMA every 100 messages
    if total_received % 100 == 0:
        ema_rate = ema_tracker.update(total_received)
        print(f"Message rate EMA: {ema_rate:.2f} events/sec")
```

---

## Health Monitoring Patterns

### Periodic Health Check

```python
import time
from core.dispatcher import Dispatcher

dispatcher = Dispatcher(maxsize=10000)

def monitor_health():
    while True:
        health = dispatcher.get_health()
        
        if not health["is_healthy"]:
            print(f"⚠️ Dispatcher unhealthy: {health}")
        
        time.sleep(1.0)  # Check every second

# Run in separate thread
import threading
monitor_thread = threading.Thread(target=monitor_health, daemon=True)
monitor_thread.start()
```

---

### Alert on Fill Ratio

```python
def check_fill_ratio(dispatcher: Dispatcher) -> None:
    health = dispatcher.get_health()
    fill_ratio = health["fill_ratio"]
    
    if fill_ratio > 0.8:
        print(f"🚨 CRITICAL: Queue {fill_ratio*100:.1f}% full")
    elif fill_ratio > 0.5:
        print(f"⚠️ WARNING: Queue {fill_ratio*100:.1f}% full")
    else:
        print(f"✅ OK: Queue {fill_ratio*100:.1f}% full")
```

---

### Alert on Overflow

```python
def check_overflow(dispatcher: Dispatcher) -> None:
    health = dispatcher.get_health()
    overflow = health["overflow_count"]
    
    if overflow > 0:
        print(f"🚨 ALERT: {overflow} events dropped")
```

---

## Prometheus Integration

### Metrics Export

```python
from prometheus_client import Gauge, Counter

# Define metrics
queue_depth_gauge = Gauge('dispatcher_queue_depth', 'Current queue depth')
queue_fill_ratio_gauge = Gauge('dispatcher_queue_fill_ratio', 'Queue fill ratio')
overflow_counter = Counter('dispatcher_overflow_total', 'Total events dropped')
message_rate_ema_gauge = Gauge('dispatcher_message_rate_ema', 'Message rate EMA')

# Update metrics
def export_metrics(dispatcher: Dispatcher, ema_tracker: MessageRateEMA):
    health = dispatcher.get_health()
    
    queue_depth_gauge.set(health["queue_depth"])
    queue_fill_ratio_gauge.set(health["fill_ratio"])
    overflow_counter._value.set(health["overflow_count"])  # Set counter directly
    message_rate_ema_gauge.set(ema_tracker.get_ema())
```

---

### Alerting Rules

**Queue fill ratio alert**:
```yaml
- alert: DispatcherQueueFilling
  expr: dispatcher_queue_fill_ratio > 0.8
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "Dispatcher queue filling up"
    description: "Queue is {{ $value | humanizePercentage }} full"
```

**Overflow alert**:
```yaml
- alert: DispatcherOverflow
  expr: rate(dispatcher_overflow_total[1m]) > 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Dispatcher overflow detected"
    description: "Events are being dropped"
```

---

## Health Check Endpoint

```python
from flask import Flask, jsonify
from core.dispatcher import Dispatcher

app = Flask(__name__)
dispatcher = Dispatcher(maxsize=10000)

@app.route("/health")
def health():
    health_data = dispatcher.get_health()
    
    status_code = 200 if health_data["is_healthy"] else 503
    
    return jsonify(health_data), status_code

# Usage
# curl http://localhost:5000/health
```

---

## Implementation Reference

See [core/dispatcher.py](../../core/dispatcher.py):
- `get_health()` method
- Health metrics calculation
- Overflow tracking

---

## Test Coverage

Key tests in `test_dispatcher.py`:
- `test_get_health_returns_metrics` — Health metrics
- `test_get_health_fill_ratio` — Fill ratio calculation
- `test_get_health_is_healthy` — Health status logic

---

## Next Steps

- **[Queue Model](./queue_model.md)** — Queue architecture
- **[Overflow Policy](./overflow_policy.md)** — Overflow handling
- **[Metrics Reference](../07_observability/metrics_reference.md)** — All metrics
- **[Tuning Guide](../09_production_guide/tuning_guide.md)** — Performance tuning
