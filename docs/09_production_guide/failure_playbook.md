# Failure Playbook

Troubleshooting guide for production issues.

---

## Overview

This playbook provides step-by-step troubleshooting for common production failures.

**Organization**: Symptom → Diagnosis → Resolution

---

## Connection Issues

### Symptom: MQTT Won't Connect

**Indicators**:
- "Connection refused" error
- "Connection timeout" error
- `mqtt_connected` metric = 0

**Diagnosis**:

1. **Check broker reachability**:
```bash
ping mqtt.settrade.com
```

2. **Check port open**:
```bash
telnet mqtt.settrade.com 8883
# or
nc -zv mqtt.settrade.com 8883
```

3. **Check API token**:
```python
import os
print(os.getenv("API_TOKEN"))  # Should not be empty
```

4. **Check TLS certificate**:
```bash
openssl s_client -connect mqtt.settrade.com:8883 -showcerts
```

**Resolution**:

**If broker unreachable**:
- Check network connectivity
- Verify firewall rules
- Contact Settrade support

**If token invalid**:
- Regenerate API token from Settrade portal
- Update environment variable: `export API_TOKEN=new_token`

**If TLS error**:
- Update CA certificates: `sudo update-ca-certificates`
- Check system time: `date` (must be synchronized)

---

### Symptom: Frequent Reconnects

**Indicators**:
- `mqtt_reconnect_total` counter increasing rapidly
- "Disconnected" → "Reconnecting" log spam
- `connection_epoch` incrementing frequently

**Diagnosis**:

1. **Check network stability**:
```bash
ping -c 100 mqtt.settrade.com | tail -1
# Look for packet loss
```

2. **Check broker logs** (if accessible):
```
# Look for "client kicked" or "quota exceeded"
```

3. **Monitor reconnect pattern**:
```python
import time

last_epoch = 0
for event in dispatcher.poll():
    if event.connection_epoch != last_epoch:
        print(f"Reconnect at {time.time()}")
        last_epoch = event.connection_epoch
```

**Resolution**:

**If network unstable**:
- Switch to wired connection (avoid WiFi)
- Check with ISP for connectivity issues
- Use different network route

**If broker-side issue**:
- Contact Settrade support
- Check for server maintenance schedule

**If too many connections**:
- Reduce connection rate
- Implement connection pooling
- Check for duplicate clients

---

## Data Flow Issues

### Symptom: No Events Received

**Indicators**:
- `feed_global_alive` = 0
- `dispatcher_events_received_total` not increasing
- Empty queue (`dispatcher._queue.qsize()` = 0)

**Diagnosis**:

1. **Check connection status**:
```python
assert client.is_connected()  # Should be True
```

2. **Check subscriptions**:
```python
# Verify symbols subscribed
print(client._subscribed_symbols)  # Should not be empty
```

3. **Check MQTT message receipt**:
```python
# Add logging to on_message callback
def on_message(client, userdata, msg):
    print(f"Received: {msg.topic}")  # Should print messages
    ...
```

4. **Check parse errors**:
```bash
# Look for parse error logs
grep "Failed to parse" logs/feed_adapter.log
```

**Resolution**:

**If not connected**:
- See "MQTT Won't Connect" above

**If not subscribed**:
- Resubscribe to symbols:
```python
client.subscribe_to_symbols(["AOT", "PTT"])
```

**If messages not reaching callback**:
- Check topic wildcards correct: `/quote/bidoffer/+/+`
- Verify broker sending data (use MQTT client tester)

**If parse errors**:
- Check protobuf schema version matches broker
- Update `settrade_v2` package: `uv pip install --upgrade settrade-v2`

---

### Symptom: Events Delayed

**Indicators**:
- High end-to-end latency (> 1 sec)
- `dispatcher_queue_depth` near maxsize
- Consumer falling behind

**Diagnosis**:

1. **Check queue depth**:
```python
depth = dispatcher._queue.qsize()
fill_ratio = depth / dispatcher._maxsize
print(f"Queue: {depth}/{dispatcher._maxsize} ({fill_ratio:.1%})")
```

2. **Check consumer processing time**:
```python
import time

start = time.perf_counter()
for event in dispatcher.poll(timeout=1.0):
    process_event(event)
    duration = time.perf_counter() - start
    if duration > 0.01:  # 10ms threshold
        print(f"Slow processing: {duration*1000:.2f}ms")
    start = time.perf_counter()
```

3. **Profile consumer code**:
```bash
python -m cProfile -o profile.stats main.py
python -c "import pstats; pstats.Stats('profile.stats').sort_stats('cumulative').print_stats(10)"
```

**Resolution**:

**If queue filling up**:
- Increase maxsize:
```python
dispatcher = Dispatcher(maxsize=50000)  # Was 10000
```

**If consumer too slow**:
- Optimize processing logic (remove database calls, etc.)
- Add more consumer threads:
```python
threads = [threading.Thread(target=consumer) for _ in range(4)]
```

**If GC pauses**:
- Tune GC thresholds:
```python
import gc
gc.set_threshold(10000, 20, 20)
```

---

### Symptom: Events Dropped (Overflow)

**Indicators**:
- `dispatcher_overflow_total` > 0
- "Dispatcher overflow" warnings in logs
- Queue full (`fill_ratio` = 1.0)

**Diagnosis**:

1. **Check overflow count**:
```python
print(f"Overflows: {dispatcher._overflow_count}")
```

2. **Calculate overflow rate**:
```python
import time

last_overflow = dispatcher._overflow_count
last_time = time.time()

time.sleep(10)  # Wait 10 seconds

now = time.time()
overflow_rate = (dispatcher._overflow_count - last_overflow) / (now - last_time)
print(f"Overflow rate: {overflow_rate:.2f} events/sec")
```

3. **Check consumer thread alive**:
```python
import threading

consumer_thread = threading.Thread(target=consumer, daemon=True)
consumer_thread.start()

# Later
assert consumer_thread.is_alive()  # Should be True
```

**Resolution**:

**Immediate** (stop the bleeding):
- Increase maxsize temporarily:
```python
dispatcher = Dispatcher(maxsize=100000)  # Emergency increase
```

**Short-term** (optimize consumer):
- Reduce processing per event
- Add more consumer threads
- Filter unnecessary symbols

**Long-term** (capacity planning):
- Right-size maxsize based on load:
```
maxsize = peak_rate × max_latency × safety_factor
```

---

## Health Monitoring Issues

### Symptom: False "Feed Stale" Alerts

**Indicators**:
- `feed_global_alive` = 0 but events are arriving
- "Feed is stale" alerts during normal operation

**Diagnosis**:

1. **Check timeout configuration**:
```python
print(f"Global timeout: {feed_health._global_timeout_sec}s")
```

2. **Check last event time**:
```python
import time

last_event_time = feed_health._global_last_event_time
now = time.time()
age = now - last_event_time if last_event_time else float('inf')

print(f"Last event: {age:.2f}s ago")
```

3. **Check if events being recorded**:
```python
# Verify record_event() called
def on_message(...):
    feed_health.record_event(event.symbol)  # This must be present
    ...
```

**Resolution**:

**If timeout too short**:
- Increase `global_timeout_sec`:
```python
feed_health = FeedHealth(global_timeout_sec=10.0)  # Was 5.0
```

**If events not recorded**:
- Ensure `record_event()` called for every event:
```python
for event in dispatcher.poll():
    feed_health.record_event(event.symbol)  # Add this
    process_event(event)
```

---

### Symptom: Symbol Incorrectly Marked Stale

**Indicators**:
- `is_symbol_alive(symbol)` returns `False` but symbol is updating
- Symbol-specific stale alerts for active symbols

**Diagnosis**:

1. **Check symbol timeout**:
```python
print(f"Symbol timeout: {feed_health._symbol_timeout_sec}s")
```

2. **Check last event for symbol**:
```python
import time

last_ts = feed_health._symbol_timestamps.get("AOT")
now = time.time()
age = now - last_ts if last_ts else float('inf')

print(f"AOT last event: {age:.2f}s ago")
```

3. **Monitor symbol update rate**:
```python
from collections import defaultdict
import time

symbol_counts = defaultdict(int)

for event in dispatcher.poll():
    symbol_counts[event.symbol] += 1
    if event.symbol == "AOT" and symbol_counts["AOT"] % 100 == 0:
        print(f"AOT: {symbol_counts['AOT']} events")
```

**Resolution**:

**If symbol illiquid** (few updates):
- Increase `symbol_timeout_sec`:
```python
feed_health = FeedHealth(symbol_timeout_sec=60.0)  # Was 10.0
```

**If after-hours** (low activity):
- Use different timeout for off-hours:
```python
import datetime

hour = datetime.datetime.now().hour
if hour < 9 or hour >= 17:  # Outside 9 AM - 5 PM
    feed_health = FeedHealth(symbol_timeout_sec=120.0)
else:
    feed_health = FeedHealth(symbol_timeout_sec=10.0)
```

---

## Performance Issues

### Symptom: High CPU Usage

**Indicators**:
- CPU > 80% sustained
- Process consuming multiple cores
- System becomes unresponsive

**Diagnosis**:

1. **Profile CPU usage**:
```bash
# Install py-spy
pip install py-spy

# Profile running process
py-spy top --pid $(pgrep -f main.py)

# Generate flame graph
py-spy record -o flamegraph.svg --pid $(pgrep -f main.py) -- sleep 30
```

2. **Check message rate**:
```python
import time

start_count = total_events
start_time = time.time()

time.sleep(10)

rate = (total_events - start_count) / (time.time() - start_time)
print(f"Message rate: {rate:.2f} events/sec")
```

3. **Check thread count**:
```bash
ps -T -p $(pgrep -f main.py) | wc -l
```

**Resolution**:

**If too many threads**:
- Reduce consumer thread count:
```python
# Was 8 threads
threads = [threading.Thread(target=consumer) for _ in range(4)]  # Now 4
```

**If GIL contention**:
- Use single-threaded consumer (avoid GIL)
- Consider multiprocessing (separate processes)

**If hot loop**:
- Optimize processing logic (identified via profiling)
- Add sleep in tight loops:
```python
for event in dispatcher.poll(timeout=0.001):  # 1ms sleep if empty
    process_event(event)
```

---

### Symptom: High Memory Usage

**Indicators**:
- RSS > 500 MB
- Memory growing over time (leak)
- OOM killer triggers

**Diagnosis**:

1. **Check queue size**:
```python
queue_memory = dispatcher._queue.qsize() * 200  # bytes per event
print(f"Queue memory: {queue_memory / 1024**2:.2f} MB")
```

2. **Profile memory**:
```bash
# Install memory_profiler
pip install memory-profiler

# Profile code
python -m memory_profiler main.py
```

3. **Check for leaks**:
```python
import gc
import sys

# Force GC
gc.collect()

# Count objects
print(f"Object count: {len(gc.get_objects())}")

# Find largest objects
import objgraph
objgraph.show_most_common_types(limit=10)
```

**Resolution**:

**If queue too large**:
- Reduce maxsize:
```python
dispatcher = Dispatcher(maxsize=10000)  # Was 100000
```

**If memory leak**:
- Identify leak source (via profiling)
- Add object cleanup:
```python
del event  # Explicit cleanup
gc.collect()  # Force GC
```

**If Python overhead**:
- Run with `-O` flag (disable debug overhead)
- Consider PyPy (JIT compiler)

---

## Contact & Escalation

If issues persist:

1. **Check logs**:
```bash
tail -f logs/feed_adapter.log | grep -i error
```

2. **Collect diagnostics**:
```bash
# System info
uname -a
python --version

# Process info
ps aux | grep main.py
netstat -an | grep 8883

# Metrics snapshot
curl http://localhost:8000/metrics > metrics_snapshot.txt
```

3. **Create issue**:
- GitHub: https://github.com/lumduan/settrade-feed-adapter/issues
- Include: logs, metrics, reproduction steps

4. **Contact Settrade support**:
- Email: support@settrade.com
- Phone: (Thailand number)

---

## Implementation Reference

See:
- [tests/](../../tests/) — Failure scenario tests
- [examples/](../../examples/) — Working examples
- [docs/08_testing_and_guarantees/failure_scenarios.md](../08_testing_and_guarantees/failure_scenarios.md) — Known failure modes

---

## Next Steps

- **[Deployment Checklist](./deployment_checklist.md)** — Pre-deployment verification
- **[Tuning Guide](./tuning_guide.md)** — Performance optimization
- **[Failure Scenarios](../08_testing_and_guarantees/failure_scenarios.md)** — Test coverage
