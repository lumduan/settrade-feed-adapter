# Gap Semantics

Understanding message delivery gaps and their implications.

---

## Overview

A **gap** is a period where expected messages are not received.

**Causes**:
- Network instability (packet loss, reconnect)
- Exchange outage (no data published)
- Symbol illiquidity (no trades/quotes)
- Subscription issues (not subscribed to symbol)

---

## Gap Types

### 1. Connection Gap

**Definition**: No messages received from **any** symbol.

**Detection**: Global liveness (`is_alive()` returns `False`)

**Cause**: MQTT connection broken or stalled

**Impact**: **Severe** — entire feed is down

**Recovery**: Reconnect MQTT client

---

### 2. Symbol Gap

**Definition**: No messages received from **specific** symbol.

**Detection**: Per-symbol liveness (`is_symbol_alive(symbol)` returns `False`)

**Cause**: Symbol-specific issue (not subscribed, illiquid, exchange halt)

**Impact**: **Moderate** — single symbol affected

**Recovery**: Resubscribe, check exchange status

---

### 3. Transient Gap

**Definition**: Brief interruption (< timeout duration).

**Detection**: None (within tolerance)

**Cause**: Temporary network blip, burst of events elsewhere

**Impact**: **Low** — no action needed

**Recovery**: Self-recovers automatically

---

## Gap Detection

### Global Gap Detection

**Method**: Monitor `is_alive()` status.

```python
from core.feed_health import FeedHealth
import time

feed_health = FeedHealth(global_timeout_sec=5.0)

last_alive = True

while True:
    is_alive = feed_health.is_alive()
    
    if last_alive and not is_alive:
        print("🚨 CONNECTION GAP DETECTED")
        # Alert, reconnect, etc.
    
    last_alive = is_alive
    time.sleep(1.0)
```

**Test**: `test_feed_health.py::test_is_alive_returns_false_when_stale`

---

### Symbol Gap Detection

**Method**: Monitor `is_symbol_alive(symbol)` for each tracked symbol.

```python
from core.feed_health import FeedHealth
import time

feed_health = FeedHealth(symbol_timeout_sec=10.0)

TRACKED_SYMBOLS = ["AOT", "PTT", "CPALL"]
last_status = {s: True for s in TRACKED_SYMBOLS}

while True:
    for symbol in TRACKED_SYMBOLS:
        is_alive = feed_health.is_symbol_alive(symbol)
        
        if last_status[symbol] and not is_alive:
            print(f"⚠️ SYMBOL GAP: {symbol}")
            # Alert, resubscribe, etc.
        
        last_status[symbol] = is_alive
    
    time.sleep(1.0)
```

**Test**: `test_feed_health.py::test_is_symbol_alive_returns_false_when_stale`

---

## Gap Semantics

### Gap vs Stale

**Stale**: Current status (snapshot)
- Symbol has not received events for > timeout

**Gap**: Event detection (edge trigger)
- Transition from alive → stale

```python
# Symbol is alive
assert feed_health.is_symbol_alive("AOT")  # True

# ... time passes > timeout ...

# Symbol is now stale (GAP OCCURRED)
assert not feed_health.is_symbol_alive("AOT")  # False
```

---

### Gap Recovery

**Contract**: Gap ends when next event is received.

```python
feed_health = FeedHealth(symbol_timeout_sec=10.0)

feed_health.record_event("AOT")  # t=0
time.sleep(11)  # Gap! (> timeout)

# Symbol is stale
assert not feed_health.is_symbol_alive("AOT")

# Receive new event
feed_health.record_event("AOT")  # Gap ends

# Symbol is alive again
assert feed_health.is_symbol_alive("AOT")
```

**Test**: `test_feed_health.py::test_gap_recovery`

---

### Gap Duration

**Definition**: Time elapsed since last message.

**Calculation**:
```python
import time

def get_gap_duration(feed_health: FeedHealth, symbol: str) -> float:
    """Get gap duration in seconds for symbol."""
    last_ts = feed_health._symbol_timestamps.get(symbol)
    
    if last_ts is None:
        return float('inf')  # Never seen
    
    now = time.time()
    return now - last_ts
```

**Example**:
```python
feed_health.record_event("AOT")  # t=0
time.sleep(15)  # Wait 15 seconds

gap_duration = get_gap_duration(feed_health, "AOT")
print(f"Gap duration: {gap_duration:.2f} sec")  # ~15.00
```

---

## Gap Handling Strategies

### 1. Ignore (for transient gaps)

**When**: Gap < symbol_timeout (self-recovers)

**Action**: None

```python
# Just continue processing
for event in dispatcher.poll():
    feed_health.record_event(event.symbol)
    process_event(event)
```

---

### 2. Alert (for monitoring)

**When**: Gap > symbol_timeout (symbol stale)

**Action**: Log, send alert

```python
import logging

logger = logging.getLogger(__name__)

for symbol in TRACKED_SYMBOLS:
    if not feed_health.is_symbol_alive(symbol):
        logger.warning(f"Symbol gap detected: {symbol}")
        send_alert(f"Symbol {symbol} is stale")
```

---

### 3. Resubscribe (for symbol gaps)

**When**: Symbol stale but connection alive

**Action**: Resubscribe to symbol

```python
from infra.settrade_mqtt import SettradeMQTTClient

client = SettradeMQTTClient(...)

for symbol in TRACKED_SYMBOLS:
    if not feed_health.is_symbol_alive(symbol) and feed_health.is_alive():
        logger.info(f"Resubscribing to {symbol}")
        client.subscribe_to_symbol(symbol)
```

---

### 4. Reconnect (for connection gaps)

**When**: Global feed stale (no recent events from any symbol)

**Action**: Reconnect MQTT client

```python
if not feed_health.is_alive():
    logger.error("Connection gap detected, reconnecting...")
    client.reconnect()
```

---

### 5. Reset State (for strategy safety)

**When**: Gap detected in critical symbol

**Action**: Reset strategy state to avoid stale data

```python
CRITICAL_SYMBOLS = ["AOT", "PTT"]

for symbol in CRITICAL_SYMBOLS:
    if not feed_health.is_symbol_alive(symbol):
        logger.warning(f"Critical symbol gap: {symbol}, resetting state")
        reset_positions()
        reset_cached_data()
        break
```

---

## Gap Metrics

### Connection Gap Count

**Definition**: Number of times global feed became stale.

```python
connection_gap_count = 0

last_alive = True

while True:
    is_alive = feed_health.is_alive()
    
    if last_alive and not is_alive:
        connection_gap_count += 1
    
    last_alive = is_alive
```

---

### Symbol Gap Count (per symbol)

**Definition**: Number of times each symbol became stale.

```python
symbol_gap_count = {s: 0 for s in TRACKED_SYMBOLS}
last_status = {s: True for s in TRACKED_SYMBOLS}

while True:
    for symbol in TRACKED_SYMBOLS:
        is_alive = feed_health.is_symbol_alive(symbol)
        
        if last_status[symbol] and not is_alive:
            symbol_gap_count[symbol] += 1
        
        last_status[symbol] = is_alive
```

---

### Gap Duration Distribution

**Definition**: Histogram of gap durations.

```python
from collections import defaultdict

gap_durations = defaultdict(list)

def record_gap_ended(symbol: str, start_time: float, end_time: float):
    duration = end_time - start_time
    gap_durations[symbol].append(duration)

# Analyze
for symbol, durations in gap_durations.items():
    avg = sum(durations) / len(durations)
    max_dur = max(durations)
    print(f"{symbol}: avg={avg:.2f}s, max={max_dur:.2f}s")
```

---

## Gap Testing

### Simulating Connection Gap

```python
# Stop recording events (simulate stalled feed)
feed_health.record_event("AOT")  # Last event
time.sleep(global_timeout + 1)

# Connection gap detected
assert not feed_health.is_alive()
```

**Test**: `test_feed_health.py::test_is_alive_returns_false_when_stale`

---

### Simulating Symbol Gap

```python
# Stop recording specific symbol
feed_health.record_event("AOT")  # Last AOT event
feed_health.record_event("PTT")  # PTT still alive

time.sleep(symbol_timeout + 1)

# AOT gap detected, PTT still alive
assert not feed_health.is_symbol_alive("AOT")
assert feed_health.is_symbol_alive("PTT")
```

**Test**: `test_feed_health.py::test_is_symbol_alive_independent`

---

## Implementation Reference

See:
- [core/feed_health.py](../../core/feed_health.py) — Gap detection logic
- [examples/example_feed_health.py](../../examples/example_feed_health.py) — Gap handling examples

---

## Test Coverage

Key tests in `test_feed_health.py`:
- `test_is_alive_returns_false_when_stale` — Connection gap
- `test_is_symbol_alive_returns_false_when_stale` — Symbol gap
- `test_is_symbol_alive_independent` — Independent symbol gaps
- `test_gap_recovery` — Gap recovery semantics

---

## Next Steps

- **[Global Liveness](./global_liveness.md)** — System-wide tracking
- **[Per-Symbol Liveness](./per_symbol_liveness.md)** — Symbol-level tracking
- **[Failure Scenarios](../08_testing_and_guarantees/failure_scenarios.md)** — Gap failure cases
