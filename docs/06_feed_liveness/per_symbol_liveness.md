# Per-Symbol Liveness

Symbol-level feed health monitoring.

---

## Overview

**Per-symbol liveness** tracks whether a **specific symbol** is receiving data.

**Purpose**: Detect when individual symbols stop updating (gap detection).

---

## Per-Symbol Liveness Model

### Definition

**Per-symbol liveness** = Has **this symbol** received an event within its timeout?

**Timeout**: Configurable duration (default: 10 seconds)

**Status** (per symbol):
- ✅ **ALIVE**: Symbol received event within timeout
- ❌ **STALE**: Symbol has not received event for > timeout
- ❓ **UNKNOWN**: Symbol never seen (no baseline)

---

## is_symbol_alive(symbol) → bool

**Contract**: Returns `True` if symbol received event within `symbol_timeout`.

```python
from core.feed_health import FeedHealth

feed_health = FeedHealth(symbol_timeout_sec=10.0)

# Record events
feed_health.record_event("AOT")  # t=0
time.sleep(5)
feed_health.record_event("PTT")  # t=5

# Check per-symbol liveness
assert feed_health.is_symbol_alive("AOT")  # ✅ Recent (5 sec ago)
assert feed_health.is_symbol_alive("PTT")  # ✅ Recent (0 sec ago)
assert not feed_health.is_symbol_alive("UNKNOWN")  # ❌ Never seen
```

**Test**: `test_feed_health.py::test_is_symbol_alive_returns_true_when_recent`

---

## Symbol Timeout Configuration

### symbol_timeout_sec

**Definition**: Maximum time (seconds) without events for **specific symbol** before considered stale.

**Default**: 10.0 seconds

**Recommendation**:
- **Liquid symbols** (AOT, PTT, CPALL): 5-10 seconds
- **Illiquid symbols**: 30-60 seconds
- **After-hours**: 120+ seconds

```python
# Liquid symbols
feed_health = FeedHealth(symbol_timeout_sec=10.0)

# Illiquid symbols
feed_health = FeedHealth(symbol_timeout_sec=60.0)
```

---

## Per-Symbol Liveness Semantics

### Independent tracking

**Contract**: Each symbol has independent timeout.

```python
feed_health = FeedHealth(symbol_timeout_sec=10.0)

feed_health.record_event("AOT")  # t=0
time.sleep(5)
feed_health.record_event("PTT")  # t=5
time.sleep(6)  # Now t=11

# At t=11
assert not feed_health.is_symbol_alive("AOT")  # ❌ Stale (11 sec ago)
assert feed_health.is_symbol_alive("PTT")      # ✅ Recent (6 sec ago)
```

**Test**: `test_feed_health.py::test_is_symbol_alive_independent`

---

### Unknown symbols

**Contract**: Unknown symbols (never seen) return `False`.

```python
feed_health = FeedHealth(symbol_timeout_sec=10.0)

# Never recorded this symbol
assert not feed_health.is_symbol_alive("UNKNOWN")  # ❌ Unknown
```

**Test**: `test_feed_health.py::test_is_symbol_alive_unknown_symbol`

---

### First observation

**Contract**: Symbol becomes alive immediately after first event.

```python
feed_health = FeedHealth(symbol_timeout_sec=10.0)

# First event for symbol
feed_health.record_event("AOT")

# Immediately alive
assert feed_health.is_symbol_alive("AOT")  # ✅ Just recorded
```

---

## Monitoring Per-Symbol Liveness

### Check All Tracked Symbols

```python
from core.feed_health import FeedHealth

feed_health = FeedHealth(symbol_timeout_sec=10.0)

def check_all_symbols(symbols: list[str]):
    stale_symbols = []
    
    for symbol in symbols:
        if not feed_health.is_symbol_alive(symbol):
            stale_symbols.append(symbol)
    
    if stale_symbols:
        print(f"🚨 Stale symbols: {stale_symbols}")
    
    return stale_symbols
```

---

### Alert on Stale Symbol

```python
import time

def monitor_symbol_liveness(symbols: list[str], feed_health: FeedHealth):
    while True:
        for symbol in symbols:
            if not feed_health.is_symbol_alive(symbol):
                print(f"⚠️ Symbol stale: {symbol}")
                # Send alert, log, etc.
        
        time.sleep(1.0)  # Check every second
```

---

### Prometheus Metrics

```python
from prometheus_client import Gauge

symbol_alive_gauge = Gauge(
    'feed_symbol_alive',
    'Symbol liveness (1=alive, 0=stale)',
    ['symbol']
)

# Update metrics
for symbol in tracked_symbols:
    is_alive = feed_health.is_symbol_alive(symbol)
    symbol_alive_gauge.labels(symbol=symbol).set(1 if is_alive else 0)
```

**Alert**:
```yaml
- alert: SymbolStale
  expr: feed_symbol_alive{symbol=~"AOT|PTT|CPALL"} == 0
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "Symbol {{ $labels.symbol }} is stale"
    description: "No events for > symbol_timeout"
```

---

## Use Cases

### Gap Detection

**Pattern**: Detect when expected symbols stop updating.

```python
from core.feed_health import FeedHealth

feed_health = FeedHealth(symbol_timeout_sec=10.0)

# Tracked symbols (watchlist)
WATCHLIST = ["AOT", "PTT", "CPALL", "KBANK", "SCB"]

def detect_gaps():
    gaps = []
    for symbol in WATCHLIST:
        if not feed_health.is_symbol_alive(symbol):
            gaps.append(symbol)
    
    if gaps:
        print(f"Gap detected: {gaps}")
        # Log, alert, or resubscribe
    
    return gaps
```

---

### Selective Resubscription

**Pattern**: Resubscribe only stale symbols.

```python
from infra.settrade_mqtt import SettradeMQTTClient
from core.feed_health import FeedHealth

client = SettradeMQTTClient(...)
feed_health = FeedHealth(symbol_timeout_sec=10.0)

def resubscribe_stale_symbols(symbols: list[str]):
    stale = [s for s in symbols if not feed_health.is_symbol_alive(s)]
    
    if stale:
        print(f"Resubscribing: {stale}")
        for symbol in stale:
            client.subscribe_to_symbol(symbol)
```

---

### Strategy Symbol Filter

**Pattern**: Only process events from alive symbols.

```python
from core.feed_health import FeedHealth

feed_health = FeedHealth(symbol_timeout_sec=10.0)

def strategy_loop():
    for event in dispatcher.poll():
        feed_health.record_event(event.symbol)
        
        # Skip stale symbols
        if not feed_health.is_symbol_alive(event.symbol):
            print(f"Skipping stale symbol: {event.symbol}")
            continue
        
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
| **Typical timeout** | 5-10 sec | 10-60 sec |

**Why different timeouts?**
- Global: Short timeout to detect connection issues quickly
- Per-symbol: Longer timeout to account for illiquid symbols

---

## get_stale_symbols() → list[str]

**Contract**: Returns list of symbols that are currently stale.

```python
feed_health = FeedHealth(symbol_timeout_sec=10.0)

feed_health.record_event("AOT")  # t=0
feed_health.record_event("PTT")  # t=0
time.sleep(11)  # Wait > timeout

# Both symbols now stale
stale = feed_health.get_stale_symbols()
assert set(stale) == {"AOT", "PTT"}
```

**Test**: `test_feed_health.py::test_get_stale_symbols`

---

## Implementation Reference

See [core/feed_health.py](../../core/feed_health.py):
- `is_symbol_alive(symbol)` method
- `get_stale_symbols()` method
- `symbol_timeout_sec` configuration
- Per-symbol timestamp tracking

---

## Test Coverage

Key tests in `test_feed_health.py`:
- `test_is_symbol_alive_returns_true_when_recent` — Alive when recent
- `test_is_symbol_alive_returns_false_when_stale` — Stale detection
- `test_is_symbol_alive_independent` — Independent tracking
- `test_is_symbol_alive_unknown_symbol` — Unknown symbols
- `test_get_stale_symbols` — Stale symbol list

---

## Next Steps

- **[Global Liveness](./global_liveness.md)** — System-wide tracking
- **[Gap Semantics](./gap_semantics.md)** — Message gap detection
- **[Failure Scenarios](../08_testing_and_guarantees/failure_scenarios.md)** — Gap scenarios
