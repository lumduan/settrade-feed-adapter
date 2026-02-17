# Global Liveness

System-wide feed health monitoring.

---

## Overview

**Global liveness** tracks whether the **entire feed** is receiving data.

**Purpose**: Detect when MQTT connection is broken or silently stalled.

---

## FeedHealth Architecture

### Purpose

The `FeedHealth` component provides:
- ✅ **Global liveness**: Is the feed alive?
- ✅ **Per-symbol liveness**: Is a specific symbol receiving data?
- ✅ **Timeout detection**: Configurable stall thresholds
- ✅ **Gap tracking**: Identify message delivery gaps

See [Phase 5 plan](../../plan/low-latency-mqtt-feed-adapter/phase5-feed-integrity.md) for design rationale.

---

## Global Liveness Model

### Definition

**Global liveness** = Has **any** event been received within the timeout?

**Timeout**: Configurable duration (default: 5 seconds)

**Status**:
- ✅ **ALIVE**: Event received within timeout
- ❌ **STALE**: No events received for > timeout

---

## is_alive() → bool

**Contract**: Returns `True` if **any** event received within `global_timeout`.

```python
from core.feed_health import FeedHealth

feed_health = FeedHealth(global_timeout_sec=5.0)

# Record events
feed_health.record_event("AOT")
feed_health.record_event("PTT")

# Check global liveness
if feed_health.is_alive():
    print("✅ Feed is alive")
else:
    print("❌ Feed is stale")
```

**Test**: `test_feed_health.py::test_is_alive_returns_true_when_recent`

---

## Global Timeout Configuration

### global_timeout_sec

**Definition**: Maximum time (seconds) without **any** events before feed considered stale.

**Default**: 5.0 seconds

**Recommendation**:
- **Real-time feed** (1-100 msg/sec): 5-10 seconds
- **Low-volume feed** (< 1 msg/sec): 30-60 seconds
- **Batch/delayed feed**: 120+ seconds

```python
# High-frequency feed
feed_health = FeedHealth(global_timeout_sec=5.0)

# Low-frequency feed
feed_health = FeedHealth(global_timeout_sec=30.0)
```

---

## Global Liveness Semantics

### Any-symbol liveness

**Contract**: Feed is alive if **any symbol** has recent data.

```python
feed_health = FeedHealth(global_timeout_sec=5.0)

# Record events for different symbols
feed_health.record_event("AOT")  # t=0
time.sleep(3)
feed_health.record_event("PTT")  # t=3

# At t=4
assert feed_health.is_alive()  # ✅ AOT or PTT recent
```

**Why?**
- Single active symbol keeps feed alive
- Prevents false negatives during off-hours

---

### Stale detection

**Contract**: Feed is stale if **no symbols** have recent data.

```python
feed_health = FeedHealth(global_timeout_sec=5.0)

feed_health.record_event("AOT")  # t=0

time.sleep(6)  # Wait > timeout

# At t=6
assert not feed_health.is_alive()  # ❌ No recent events
```

**Test**: `test_feed_health.py::test_is_alive_returns_false_when_stale`

---

## Monitoring Global Liveness

### Periodic Check

```python
import time
from core.feed_health import FeedHealth

feed_health = FeedHealth(global_timeout_sec=5.0)

def monitor_global_liveness():
    while True:
        if not feed_health.is_alive():
            print("🚨 ALERT: Feed is stale!")
            # Send alert, restart connection, etc.
        
        time.sleep(1.0)  # Check every second

# Run in separate thread
import threading
monitor_thread = threading.Thread(target=monitor_global_liveness, daemon=True)
monitor_thread.start()
```

---

### Health Check Endpoint

```python
from flask import Flask, jsonify
from core.feed_health import FeedHealth

app = Flask(__name__)
feed_health = FeedHealth(global_timeout_sec=5.0)

@app.route("/health/feed")
def feed_health_check():
    is_alive = feed_health.is_alive()
    
    return jsonify({
        "is_alive": is_alive,
        "status": "healthy" if is_alive else "stale",
    }), 200 if is_alive else 503

# Usage
# curl http://localhost:5000/health/feed
```

---

### Prometheus Metric

```python
from prometheus_client import Gauge

feed_alive_gauge = Gauge('feed_global_alive', 'Feed global liveness (1=alive, 0=stale)')

# Update metric
feed_alive_gauge.set(1 if feed_health.is_alive() else 0)
```

**Alert**:
```yaml
- alert: FeedStale
  expr: feed_global_alive == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Feed is stale"
    description: "No events received for > global_timeout"
```

---

## Use Cases

### Reconnect Trigger

**Pattern**: Reconnect MQTT when feed is stale.

```python
from infra.settrade_mqtt import SettradeMQTTClient
from core.feed_health import FeedHealth

client = SettradeMQTTClient(...)
feed_health = FeedHealth(global_timeout_sec=10.0)

def monitor_and_reconnect():
    while True:
        if not feed_health.is_alive():
            print("Feed stale, reconnecting...")
            client.reconnect()
        
        time.sleep(5.0)
```

---

### Strategy State Management

**Pattern**: Reset strategy state when feed is stale.

```python
from core.feed_health import FeedHealth

feed_health = FeedHealth(global_timeout_sec=5.0)

def strategy_loop():
    for event in dispatcher.poll():
        if not feed_health.is_alive():
            print("Feed stale, resetting state...")
            reset_positions()
            reset_cached_data()
        
        feed_health.record_event(event.symbol)
        process_event(event)
```

---

## Global vs Per-Symbol Liveness

| Aspect | Global Liveness | Per-Symbol Liveness |
|--------|----------------|---------------------|
| **Scope** | Entire feed | Individual symbol |
| **Timeout** | `global_timeout_sec` | `symbol_timeout_sec` |
| **Check** | `is_alive()` | `is_symbol_alive(symbol)` |
| **Use** | Detect connection issues | Detect symbol-specific gaps |

**See**: [Per-Symbol Liveness](./per_symbol_liveness.md) for symbol-level tracking.

---

## Implementation Reference

See [core/feed_health.py](../../core/feed_health.py):
- `is_alive()` method
- `global_timeout_sec` configuration
- Global timestamp tracking

---

## Test Coverage

Key tests in `test_feed_health.py`:
- `test_is_alive_returns_true_when_recent` — Alive when recent
- `test_is_alive_returns_false_when_stale` — Stale detection
- `test_is_alive_any_symbol` — Any-symbol semantics
- `test_is_alive_after_clear` — Clear resets state

---

## Next Steps

- **[Per-Symbol Liveness](./per_symbol_liveness.md)** — Symbol-level tracking
- **[Gap Semantics](./gap_semantics.md)** — Message gap detection
- **[Failure Scenarios](../08_testing_and_guarantees/failure_scenarios.md)** — Stall scenarios
