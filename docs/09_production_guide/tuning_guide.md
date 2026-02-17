# Tuning Guide

Performance optimization and system tuning for production.

---

## Overview

This guide covers:
- ✅ Queue sizing strategies
- ✅ Timeout configuration
- ✅ Thread tuning
- ✅ Memory optimization
- ✅ OS-level tuning

---

## Queue Tuning

### Sizing Formula

**Formula**:
```
maxsize = peak_message_rate × max_consumer_latency × safety_factor
```

**Example**:
- Peak rate: 100,000 events/sec (market open)
- Max consumer latency: 0.5 sec (processing time)
- Safety factor: 20x (handle bursts)

```
maxsize = 100,000 × 0.5 × 20 = 1,000,000
```

**Memory impact**:
- BestBidAsk: 1,000,000 × 200 bytes = 200 MB
- FullBidOffer: 1,000,000 × 440 bytes = 440 MB

---

### Undersized Queue Symptoms

**Indicators**:
- Frequent overflow errors in logs
- High overflow count (`dispatcher._overflow_count`)
- Events dropped even when consumer is processing
- `queue.Full` exceptions

**Solution**: Increase `maxsize`

```python
# Before (too small)
dispatcher = Dispatcher(maxsize=1000)

# After (larger buffer)
dispatcher = Dispatcher(maxsize=10000)
```

---

### Oversized Queue Symptoms

**Indicators**:
- High memory usage (> 500 MB for queue alone)
- Long processing delays (events stale before consumed)
- Consumer cannot keep up even with large buffer
- Latency > 1 second end-to-end

**Solution**: Decrease `maxsize` or optimize consumer

```python
# Before (too large, wastes memory)
dispatcher = Dispatcher(maxsize=1000000)

# After (right-sized)
dispatcher = Dispatcher(maxsize=50000)
```

---

## Timeout Tuning

### Global Feed Timeout

**Parameter**: `global_timeout_sec`

**Purpose**: Detect when **entire feed** is stalled.

**Formula**:
```
global_timeout_sec ≈ 2-3 × expected_message_interval
```

**Example**:
- Expected interval: ~0.01 sec (100 msg/sec)
- Timeout: 2-3 × 0.01 = ~0.02-0.03 sec (20-30 ms)

**Too short**: False alarms during normal gaps

**Too long**: Slow detection of connection issues

**Recommendations**:
- **Real-time feed** (100+ msg/sec): 5-10 sec
- **Low-volume feed** (< 10 msg/sec): 30-60 sec

```python
# High-frequency feed
feed_health = FeedHealth(global_timeout_sec=5.0)

# Low-frequency feed
feed_health = FeedHealth(global_timeout_sec=30.0)
```

---

### Symbol-Specific Timeout

**Parameter**: `symbol_timeout_sec`

**Purpose**: Detect when **specific symbol** stops updating.

**Formula**:
```
symbol_timeout_sec ≈ 5 × expected_symbol_update_interval
```

**Example**:
- Liquid symbol (AOT): ~0.1 sec between updates
- Timeout: 5 × 0.1 = 0.5 sec

- Illiquid symbol: ~10 sec between updates
- Timeout: 5 × 10 = 50 sec

**Recommendations**:
- **Liquid symbols**: 10 sec
- **Illiquid symbols**: 60 sec
- **After-hours**: 120 sec

```python
# Liquid symbols (intraday)
feed_health = FeedHealth(symbol_timeout_sec=10.0)

# Illiquid symbols / after-hours
feed_health = FeedHealth(symbol_timeout_sec=60.0)
```

---

### MQTT Reconnect Timeout

**Parameter**: Backoff exponential parameters

**Current**: 1s base, 2x multiplier, 5 max retries

```python
# In SettradeMQTTClient
base_delay = 1.0  # sec
max_retries = 5
multiplier = 2

# Retry schedule: 1s, 2s, 4s, 8s, 16s
```

**Tuning**:
- **Faster reconnect**: Decrease base_delay (e.g., 0.5s)
- **More attempts**: Increase max_retries (e.g., 10)
- **Aggressive backoff**: Increase multiplier (e.g., 3)

**Trade-off**: Faster reconnect vs server load

---

## Thread Tuning

### Consumer Thread Count

**Formula**:
```
thread_count = min(CPU_cores, messages_per_sec / per_thread_capacity)
```

**Example**:
- CPU cores: 8
- Message rate: 100,000 msg/sec
- Per-thread capacity: 50,000 msg/sec

```
thread_count = min(8, 100,000 / 50,000) = min(8, 2) = 2
```

**Recommendations**:
- **Low load** (< 50k msg/sec): 1 thread
- **Medium load** (50k-150k msg/sec): 2-4 threads
- **High load** (> 150k msg/sec): 4-8 threads

**Diminishing returns**: GIL contention above 4-8 threads

---

### MQTT Callback Thread

**Policy**: Single-threaded (paho-mqtt design)

**Tuning**: Minimize work in callback

```python
def on_message(client, userdata, msg):
    # ✅ FAST: Just enqueue
    dispatcher.put_if_fits(event)
    
    # ❌ SLOW: Don't process here
    # process_event(event)  # Blocks MQTT thread!
```

---

## Memory Optimization

### Event Object Recycling

**Problem**: High allocation rate (100k+ events/sec)

**Solution**: Object pool (advanced, not currently implemented)

```python
from queue import Queue

class EventPool:
    def __init__(self, size: int):
        self.pool: Queue[BestBidAsk] = Queue(maxsize=size)
        
        # Pre-allocate objects
        for _ in range(size):
            self.pool.put(BestBidAsk.model_construct(...))
    
    def acquire(self) -> BestBidAsk:
        try:
            return self.pool.get_nowait()
        except:
            return BestBidAsk.model_construct(...)  # Fallback
    
    def release(self, event: BestBidAsk):
        try:
            self.pool.put_nowait(event)
        except:
            pass  # Pool full, let GC handle it
```

**Trade-off**: Complexity vs memory churn reduction

---

### GC Tuning (CPython)

**Problem**: GC pauses during high allocation rate

**Solution**: Adjust GC thresholds

```python
import gc

# Default thresholds: (700, 10, 10)
gc.set_threshold(10000, 20, 20)  # Less frequent GC

# Or disable GC during critical sections
gc.disable()
process_burst()
gc.enable()
```

**Trade-off**: Memory usage vs GC pause frequency

---

### Tuple vs List for FullBidOffer

**Current**: Uses tuples (immutable)

**Alternative**: lists (mutable)

**Benchmark**:
- Tuple creation: ~faster~ (C-level optimization)
- List creation: Slightly slower

**Verdict**: Keep tuples (immutable + hashable benefits)

---

## OS-Level Tuning (Linux)

### CPU Frequency Scaling

**Problem**: CPU governor throttles performance

**Solution**: Set to performance mode

```bash
# Check current governor
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# Set to performance (requires root)
sudo cpupower frequency-set -g performance

# Verify
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

**Trade-off**: Power consumption vs performance

---

### CPU Isolation (Real-time)

**Problem**: Other processes interfere with feed adapter

**Solution**: Isolate CPU cores

```bash
# Add to kernel boot parameters (/etc/default/grub)
isolcpus=2,3  # Isolate cores 2-3

# Then pin feed adapter to isolated cores
taskset -c 2,3 python main.py
```

**Trade-off**: Resource dedication vs flexibility

---

### Network Tuning

**Problem**: Network buffer sizes too small

**Solution**: Increase TCP buffer sizes

```bash
# Increase receive buffer (requires root)
sudo sysctl -w net.core.rmem_max=16777216
sudo sysctl -w net.core.rmem_default=262144

# Increase send buffer
sudo sysctl -w net.core.wmem_max=16777216
sudo sysctl -w net.core.wmem_default=262144

# Make permanent
sudo sysctl -p
```

---

### File Descriptor Limits

**Problem**: Too many connections → "Too many open files"

**Solution**: Increase limits

```bash
# Check current limit
ulimit -n

# Increase (temporary)
ulimit -n 65536

# Permanent: Edit /etc/security/limits.conf
* soft nofile 65536
* hard nofile 65536
```

---

## Python-Specific Tuning

### Disable Assertions

**Problem**: Debug assertions in production

**Solution**: Run with `-O` (optimize)

```bash
# Normal (assertions enabled)
python main.py

# Optimized (assertions disabled)
python -O main.py

# More aggressive (remove docstrings too)
python -OO main.py
```

**Speedup**: ~5-10% for assertion-heavy code

---

### Use PyPy (Alternative)

**Problem**: CPython GIL limits parallelism

**Solution**: Try PyPy (JIT compiler, no GIL in some cases)

**Note**: Not all dependencies work with PyPy (betterproto, pydantic)

**Verdict**: Stick with CPython for now

---

## Configuration Examples

### Low-Latency Configuration

**Goal**: Minimize latency (< 10 µs end-to-end)

```python
from core.dispatcher import Dispatcher
from core.feed_health import FeedHealth

# Small queue (low buffering)
dispatcher = Dispatcher(maxsize=1000)

# Short timeouts (fast failure detection)
feed_health = FeedHealth(
    global_timeout_sec=5.0,
    symbol_timeout_sec=10.0,
)

# Single consumer thread (no context switching)
def consumer():
    for event in dispatcher.poll(timeout=0.001):  # 1ms timeout
        process_event(event)
```

---

### High-Throughput Configuration

**Goal**: Maximize throughput (> 150k events/sec)

```python
# Large queue (buffer bursts)
dispatcher = Dispatcher(maxsize=100000)

# Longer timeouts (tolerate gaps)
feed_health = FeedHealth(
    global_timeout_sec=10.0,
    symbol_timeout_sec=30.0,
)

# Multiple consumer threads
import threading

def consumer():
    for event in dispatcher.poll(timeout=0.1):  # 100ms timeout
        process_event(event)

threads = [threading.Thread(target=consumer, daemon=True) for _ in range(4)]
for t in threads:
    t.start()
```

---

### Memory-Constrained Configuration

**Goal**: Minimize memory usage (< 50 MB)

```python
# Small queue (low memory)
dispatcher = Dispatcher(maxsize=5000)

# Filter symbols (reduce subscriptions)
TRACKED_SYMBOLS = ["AOT", "PTT", "CPALL"]  # Only 3 symbols
client.subscribe_to_symbols(TRACKED_SYMBOLS)

# Disable GC during critical sections
import gc
gc.disable()
```

---

## Monitoring Tuning Effectiveness

### Before Tuning

```bash
# Baseline metrics
uv run python scripts/benchmark_adapter.py > before.txt
```

### Apply Tuning

```python
# Modify configuration
dispatcher = Dispatcher(maxsize=50000)  # Changed from 10000
```

### After Tuning

```bash
# New metrics
uv run python scripts/benchmark_adapter.py > after.txt

# Compare
diff before.txt after.txt
```

### Metrics to Track

- **Latency**: p50, p99 (lower is better)
- **Throughput**: events/sec (higher is better)
- **Memory**: RSS (lower is better)
- **CPU**: % utilization (lower is better)
- **Overflow**: count (should be 0)

---

## Implementation Reference

See:
- [core/dispatcher.py](../../core/dispatcher.py) — Queue configuration
- [core/feed_health.py](../../core/feed_health.py) — Timeout configuration
- [infra/settrade_mqtt.py](../../infra/settrade_mqtt.py) — Reconnect tuning

---

## Next Steps

- **[Deployment Checklist](./deployment_checklist.md)** — Pre-deployment verification
- **[Failure Playbook](./failure_playbook.md)** — Troubleshooting guide
- **[Performance Targets](../07_observability/performance_targets.md)** — Target metrics
