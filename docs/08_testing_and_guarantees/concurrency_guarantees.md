# Concurrency Guarantees

Thread safety contracts and concurrency model.

---

## Overview

This document specifies all concurrency guarantees backed by test coverage. The system uses a **Single Producer, Single Consumer (SPSC)** model with specific thread safety contracts.

---

## Thread Model

```
┌──────────────────────────────┐
│  Producer (MQTT IO Thread)   │
│  • dispatcher.push(event)    │
│  • Writes: _total_pushed     │
│  • Writes: _total_dropped    │
└──────────────────────────────┘
           ↓
    [Bounded Deque]
           ↓
┌──────────────────────────────┐
│  Consumer (Strategy Thread)  │
│  • dispatcher.poll()         │
│  • Writes: _total_polled     │
└──────────────────────────────┘
```

---

## Guarantees

### 1. Concurrent Push/Poll is Safe

**Contract**: Simultaneous `push()` and `poll()` operations do not corrupt data.

**Test Coverage**: `test_dispatcher.py::TestConcurrency::test_concurrent_push_poll`

**Mechanism**: CPython GIL makes `deque.append()` and `deque.popleft()` atomic.

**Example**:
```python
# Thread 1 (MQTT IO)
dispatcher.push(event)  # Atomic

# Thread 2 (Strategy)
events = dispatcher.poll(max_events=100)  # Atomic
```

**Note**: This guarantee is **CPython-specific**. PyPy, GraalPy, and nogil Python require explicit locking.

---

### 2. Single-Writer Per Counter

**Contract**: No two threads write to the same counter.

**Counters**:
- `_total_pushed`: Written by producer only
- `_total_dropped`: Written by producer only
- `_total_polled`: Written by consumer only

**Guarantee**: No data races on counters.

---

### 3. Eventually Consistent Stats

**Contract**: `dispatcher.stats()` can be called from any thread but returns eventually consistent values.

**Test Coverage**: `test_dispatcher.py::TestStats::test_stats_returns_frozen_model`

**Guarantee**: Under quiescent conditions, stats are exact. During concurrent operations, stats reflect different points in time.

---

### 4. Generation Prevents Stale Dispatch

**Contract**: Messages from old connections are never dispatched after reconnect.

**Test Coverage**: `test_settrade_mqtt.py::TestMessageDispatch::test_stale_generation_rejected`

**Mechanism**:
1. On disconnect: `generation++`
2. On message: `if self._generation != current_generation: reject`

**Guarantee**: Reconnect safety.

---

### 5. No Duplicate Reconnect Loops

**Contract**: Only one reconnect thread runs at a time.

**Test Coverage**: `test_settrade_mqtt.py::TestReconnect::test_schedule_reconnect_prevents_duplicates`

**Mechanism**:
```python
if self._state != ClientState.RECONNECTING:
    self._state = ClientState.RECONNECTING
    spawn_reconnect_thread()
```

**Guarantee**: State check before thread spawn.

---

### 6. Callback Isolation

**Contract**: Exception in one callback does not affect other callbacks or MQTT client.

**Test Coverage**: `test_settrade_mqtt.py::TestMessageDispatch::test_callback_isolation`

**Mechanism**: Try-except around each callback invocation.

---

### 7. Lock-Free Hot Path

**Contract**: No explicit locks in the critical path (MQTT callback → parse → normalize → push).

**Guarantee**: Lower latency, no lock contention.

**Trade-off**: CPython-only, SPSC-only.

---

## CPython GIL Assumptions

### What We Rely On

1. `deque.append()` is atomic
2. `deque.popleft()` is atomic
3. `int` reads and writes are atomic (single bytecode instruction)

### What This Means

**Safe**:
```python
self._total_pushed += 1  # Atomic (single bytecode: INPLACE_ADD)
self._queue.append(event)  # Atomic
event = self._queue.popleft()  # Atomic
```

**NOT Safe Without GIL**:
- Multi-producer (multiple threads calling `push()`)
- Multi-consumer (multiple threads calling `poll()`)

---

## Migration to Other Python Implementations

If migrating to PyPy, GraalPy, or nogil Python, replace with `threading.Queue`:

```python
import queue

class ThreadSafeDispatcher:
    def __init__(self, maxlen: int):
        self._queue = queue.Queue(maxsize=maxlen)
    
    def push(self, event: T) -> None:
        try:
            self._queue.put_nowait(event)
            self._total_pushed += 1
        except queue.Full:
            # Drop oldest
            _ = self._queue.get_nowait()
            self._queue.put_nowait(event)
            self._total_pushed += 1
            self._total_dropped += 1
    
    def poll(self, max_events: int) -> list[T]:
        result = []
        for _ in range(max_events):
            try:
                result.append(self._queue.get_nowait())
            except queue.Empty:
                break
        self._total_polled += len(result)
        return result
```

---

## Testing Strategies

### Concurrent Testing Example

```python
from concurrent.futures import ThreadPoolExecutor

def test_concurrent_push_poll():
    dispatcher = Dispatcher(maxlen=1000)
    
    def push_worker():
        for i in range(1000):
            dispatcher.push(i)
    
    def poll_worker():
        total = []
        while len(total) < 1000:
            total.extend(dispatcher.poll(max_events=10))
        return total
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        push_future = executor.submit(push_worker)
        poll_future = executor.submit(poll_worker)
        
        push_future.result()
        result = poll_future.result()
    
    # All events received
    assert len(result) == 1000
    
    # FIFO ordering may not be perfect due to race conditions
    # but no data corruption
```

---

## Known Limitations

### 1. SPSC Only

**Limitation**: Multi-producer or multi-consumer violates safety guarantees.

**Solution**: Use `threading.Queue` for MPMC.

### 2. CPython Only

**Limitation**: Relies on CPython GIL for atomicity.

**Solution**: Add explicit locks for other Python implementations.

### 3. Eventually Consistent Stats

**Limitation**: `stats()` reads may reflect different points in time.

**Solution**: Only read stats during quiescent periods for exactness.

---

## Next Steps

- **[Invariants Defined by Tests](./invariants_defined_by_tests.md)** — All design guarantees
- **[Failure Scenarios](./failure_scenarios.md)** — Error handling coverage
- **[Threading and Concurrency](../01_system_overview/threading_and_concurrency.md)** — Architecture details
