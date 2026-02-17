# Metrics Reference

Comprehensive reference for all system metrics.

---

## Overview

This document catalogs all metrics emitted by the feed adapter for monitoring and observability.

**Metric types**:
- **Counter**: Monotonically increasing value (e.g., total events)
- **Gauge**: Point-in-time measurement (e.g., queue depth)
- **Histogram**: Distribution of values (e.g., latency percentiles)

---

## Dispatcher Metrics

### dispatcher_queue_depth (Gauge)

**Description**: Current number of events in dispatcher queue.

**Unit**: Events

**Range**: [0, maxsize]

**Labels**: None

**Healthy**: < 50% of maxsize

**Example**:
```python
from prometheus_client import Gauge

queue_depth = Gauge('dispatcher_queue_depth', 'Current queue depth')
queue_depth.set(dispatcher._queue.qsize())
```

---

### dispatcher_queue_capacity (Gauge)

**Description**: Maximum queue capacity (`maxsize`).

**Unit**: Events

**Range**: [1, ∞)

**Labels**: None

**Example**:
```python
from prometheus_client import Gauge

queue_capacity = Gauge('dispatcher_queue_capacity', 'Queue capacity')
queue_capacity.set(dispatcher._maxsize)
```

---

### dispatcher_queue_fill_ratio (Gauge)

**Description**: Queue depth as fraction of capacity.

**Unit**: Ratio (0.0 to 1.0)

**Range**: [0.0, 1.0]

**Labels**: None

**Healthy**: < 0.5

**Warning**: 0.5 - 0.8

**Critical**: > 0.8

**Example**:
```python
from prometheus_client import Gauge

fill_ratio = Gauge('dispatcher_queue_fill_ratio', 'Queue fill ratio')
fill_ratio.set(dispatcher._queue.qsize() / dispatcher._maxsize)
```

---

### dispatcher_overflow_total (Counter)

**Description**: Total number of events dropped due to queue full.

**Unit**: Events

**Range**: [0, ∞)

**Labels**: None

**Healthy**: 0

**Warning**: > 0

**Example**:
```python
from prometheus_client import Counter

overflow_counter = Counter('dispatcher_overflow_total', 'Total events dropped')
overflow_counter.inc()  # Increment on overflow
```

---

### dispatcher_events_received_total (Counter)

**Description**: Total number of events received (all symbols).

**Unit**: Events

**Range**: [0, ∞)

**Labels**: `symbol` (optional)

**Example**:
```python
from prometheus_client import Counter

events_counter = Counter(
    'dispatcher_events_received_total',
    'Total events received',
    ['symbol']
)

events_counter.labels(symbol="AOT").inc()
```

---

## Feed Health Metrics

### feed_global_alive (Gauge)

**Description**: Global feed liveness (1=alive, 0=stale).

**Unit**: Boolean (0 or 1)

**Range**: {0, 1}

**Labels**: None

**Healthy**: 1

**Critical**: 0

**Example**:
```python
from prometheus_client import Gauge

feed_alive = Gauge('feed_global_alive', 'Feed global liveness')
feed_alive.set(1 if feed_health.is_alive() else 0)
```

---

### feed_symbol_alive (Gauge)

**Description**: Per-symbol liveness (1=alive, 0=stale).

**Unit**: Boolean (0 or 1)

**Range**: {0, 1}

**Labels**: `symbol`

**Healthy**: 1

**Warning**: 0

**Example**:
```python
from prometheus_client import Gauge

symbol_alive = Gauge(
    'feed_symbol_alive',
    'Symbol liveness',
    ['symbol']
)

for symbol in tracked_symbols:
    is_alive = feed_health.is_symbol_alive(symbol)
    symbol_alive.labels(symbol=symbol).set(1 if is_alive else 0)
```

---

### feed_global_last_event_seconds_ago (Gauge)

**Description**: Seconds since last event (any symbol).

**Unit**: Seconds

**Range**: [0, ∞)

**Labels**: None

**Healthy**: < global_timeout

**Example**:
```python
import time
from prometheus_client import Gauge

last_event_ago = Gauge('feed_global_last_event_seconds_ago', 'Seconds since last event')

now = time.time()
last_ts = feed_health._global_last_event_time
last_event_ago.set(now - last_ts if last_ts else float('inf'))
```

---

### feed_symbol_last_event_seconds_ago (Gauge)

**Description**: Seconds since last event for specific symbol.

**Unit**: Seconds

**Range**: [0, ∞)

**Labels**: `symbol`

**Healthy**: < symbol_timeout

**Example**:
```python
import time
from prometheus_client import Gauge

symbol_last_event_ago = Gauge(
    'feed_symbol_last_event_seconds_ago',
    'Seconds since last event for symbol',
    ['symbol']
)

now = time.time()
for symbol in tracked_symbols:
    last_ts = feed_health._symbol_timestamps.get(symbol)
    age = now - last_ts if last_ts else float('inf')
    symbol_last_event_ago.labels(symbol=symbol).set(age)
```

---

## MQTT Metrics

### mqtt_connected (Gauge)

**Description**: MQTT connection status (1=connected, 0=disconnected).

**Unit**: Boolean (0 or 1)

**Range**: {0, 1}

**Labels**: None

**Healthy**: 1

**Critical**: 0

**Example**:
```python
from prometheus_client import Gauge

mqtt_connected = Gauge('mqtt_connected', 'MQTT connection status')
mqtt_connected.set(1 if client.is_connected() else 0)
```

---

### mqtt_reconnect_total (Counter)

**Description**: Total number of MQTT reconnects.

**Unit**: Reconnects

**Range**: [0, ∞)

**Labels**: None

**Healthy**: Low value (< 10 per day)

**Warning**: > 10 per day

**Example**:
```python
from prometheus_client import Counter

reconnect_counter = Counter('mqtt_reconnect_total', 'Total MQTT reconnects')
reconnect_counter.inc()  # Increment on reconnect
```

---

### mqtt_subscription_total (Counter)

**Description**: Total number of symbol subscriptions.

**Unit**: Subscriptions

**Range**: [0, ∞)

**Labels**: `symbol` (optional)

**Example**:
```python
from prometheus_client import Counter

subscription_counter = Counter(
    'mqtt_subscription_total',
    'Total subscriptions',
    ['symbol']
)

subscription_counter.labels(symbol="AOT").inc()
```

---

## Latency Metrics

### event_processing_latency_seconds (Histogram)

**Description**: Time from event receipt to processing completion.

**Unit**: Seconds

**Range**: [0, ∞)

**Labels**: `symbol` (optional), `event_type`

**Buckets**: 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1

**Healthy**: p99 < 0.001 (1ms)

**Example**:
```python
import time
from prometheus_client import Histogram

processing_latency = Histogram(
    'event_processing_latency_seconds',
    'Event processing latency',
    ['symbol', 'event_type'],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1]
)

start = time.perf_counter()
process_event(event)
duration = time.perf_counter() - start

processing_latency.labels(
    symbol=event.symbol,
    event_type=type(event).__name__
).observe(duration)
```

---

### mqtt_message_receive_latency_seconds (Histogram)

**Description**: Time from MQTT message arrival to adapter processing.

**Unit**: Seconds

**Range**: [0, ∞)

**Labels**: None

**Buckets**: 0.00001, 0.00005, 0.0001, 0.0005, 0.001

**Healthy**: p99 < 0.0001 (100µs)

---

## Performance Metrics

### event_parse_duration_seconds (Histogram)

**Description**: Time to parse raw protobuf message.

**Unit**: Seconds range**: [0, ∞)

**Labels**: `message_type`

**Buckets**: 0.000001, 0.000005, 0.00001, 0.00005, 0.0001

**Healthy**: p99 < 0.00001 (10µs)

---

### event_normalize_duration_seconds (Histogram)

**Description**: Time to normalize parsed message to event model.

**Unit**: Seconds

**Range**: [0, ∞)

**Labels**: `event_type`

**Buckets**: 0.000001, 0.000005, 0.00001, 0.00005, 0.0001

**Healthy**: p99 < 0.00005 (50µs)

---

## System Metrics

### python_gc_collections_total (Counter)

**Description**: Total garbage collection runs (by generation).

**Unit**: Collections

**Range**: [0, ∞)

**Labels**: `generation`

**Source**: CPython `gc` module

**Example**:
```python
import gc
from prometheus_client import Counter

gc_counter = Counter(
    'python_gc_collections_total',
    'Total GC collections',
    ['generation']
)

for gen in [0, 1, 2]:
    gc_counter.labels(generation=str(gen)).inc(gc.get_count()[gen])
```

---

### process_cpu_seconds_total (Counter)

**Description**: Total CPU time consumed by process.

**Unit**: Seconds

**Range**: [0, ∞)

**Labels**: None

**Source**: `/proc/self/stat` or `resource` module

---

### process_resident_memory_bytes (Gauge)

**Description**: Resident memory (RSS) usage.

**Unit**: Bytes

**Range**: [0, ∞)

**Labels**: None

**Healthy**: < 500 MB for typical workload

---

## Prometheus Export Example

```python
from prometheus_client import start_http_server, Gauge, Counter, Histogram
import time

# Start metrics server
start_http_server(8000)

# Define metrics
queue_depth = Gauge('dispatcher_queue_depth', 'Current queue depth')
events_counter = Counter('dispatcher_events_received_total', 'Total events')
feed_alive = Gauge('feed_global_alive', 'Feed liveness')

# Update loop
while True:
    queue_depth.set(dispatcher._queue.qsize())
    feed_alive.set(1 if feed_health.is_alive() else 0)
    
    time.sleep(1.0)  # Update every second

# Metrics available at http://localhost:8000/metrics
```

---

## Implementation Reference

See:
- [core/dispatcher.py](../../core/dispatcher.py) — Dispatcher metrics
- [core/feed_health.py](../../core/feed_health.py) — Feed health metrics
- [infra/settrade_mqtt.py](../../infra/settrade_mqtt.py) — MQTT metrics

---

## Next Steps

- **[Logging Policy](./logging_policy.md)** — Log message formatting
- **[Benchmark Guide](./benchmark_guide.md)** — Performance measurement
- **[Performance Targets](./performance_targets.md)** — Target latencies
