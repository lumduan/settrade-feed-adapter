# Threading and Concurrency

Concurrency model, guarantees, and thread safety contracts.

---

## Threading Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Main Thread (Your Strategy)                                 │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  • Create MQTT client, adapter, dispatcher            │  │
│  │  • client.connect()                                    │  │
│  │  • Loop: dispatcher.poll() → process events           │  │
│  │  • client.shutdown()                                   │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                           ↕
                    (thread-safe deque)
                           ↕
┌──────────────────────────────────────────────────────────────┐
│  MQTT IO Thread (paho-mqtt loop_start)                       │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  • Maintains WebSocket connection                      │  │
│  │  • Receives binary messages                            │  │
│  │  • Calls on_message callback (inline)                  │  │
│  │    ├─ Parse protobuf                                   │  │
│  │    ├─ Normalize → BestBidAsk                           │  │
│  │    └─ dispatcher.push(event)                           │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                           ↕
                  (reconnect thread)
                           ↕
┌──────────────────────────────────────────────────────────────┐
│  Reconnect Thread (spawned on disconnect)                    │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  • Runs only when state == RECONNECTING                │  │
│  │  • Exponential backoff loop                            │  │
│  │  • Calls client.reconnect()                            │  │
│  │  • Exits on success or SHUTDOWN                        │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

## Concurrency Guarantees

### 1. Concurrent Push/Poll is Safe

**Test Coverage**: `test_dispatcher.py::test_concurrent_push_poll`

```python
def push_thread():
    for i in range(1000):
        dispatcher.push(i)

def poll_thread():
    total = []
    while len(total) < 1000:
        total.extend(dispatcher.poll(max_events=10))
```

**Guarantee**: No data corruption, no deadlock.

**Mechanism**: CPython GIL makes `deque.append()` and `deque.popleft()` atomic.

---

### 2. Generation Prevents Stale Dispatch

**Test Coverage**: `test_settrade_mqtt.py::test_generation_prevents_stale_messages`

```python
# Before reconnect
generation = 1
message arrives → on_message checks generation → OK

# Trigger reconnect
client.reconnect()
generation = 2

# Old in-flight message arrives
message arrives → on_message checks generation → REJECT
```

**Guarantee**: Messages from old connection are **never dispatched** after reconnect.

---

### 3. No Duplicate Reconnect Loops

**Test Coverage**: `test_settrade_mqtt.py::test_no_duplicate_reconnect_scheduling`

```python
# Disconnect
client._on_disconnect(...) → state = RECONNECTING
                           → spawn _reconnect_loop thread

# Second disconnect (race condition)
client._on_disconnect(...) → state == RECONNECTING (no-op)
```

**Guarantee**: Only **one** reconnect thread runs at a time.

**Mechanism**: State check before spawning thread.

---

### 4. Reconnect Blocked After Shutdown

**Test Coverage**: `test_settrade_mqtt.py::test_reconnect_blocked_after_shutdown`

```python
client.shutdown()  # state = SHUTDOWN

# Network disconnect arrives
client._on_disconnect(...) → if state == SHUTDOWN: return
```

**Guarantee**: No reconnect attempts after shutdown.

---

### 5. Shutdown is Idempotent

**Test Coverage**: `test_settrade_mqtt.py::test_shutdown_idempotent`

```python
client.shutdown()  # OK
client.shutdown()  # OK (no-op)
client.shutdown()  # OK (no-op)
```

**Guarantee**: Multiple shutdown calls are safe.

---

## SPSC (Single Producer, Single Consumer) Contract

### Strict Ownership

**Producer (MQTT IO Thread)**:
- Calls `dispatcher.push(event)` **only**
- Writes to `_total_pushed` and `_total_dropped` **only**

**Consumer (Strategy Thread)**:
- Calls `dispatcher.poll(max_events)` **only**
- Writes to `_total_polled` **only**

**Key Invariant**: No two threads ever write to the same counter.

### Violation → Undefined Behavior

If you violate SPSC (e.g., multi-producer):
- Data races on counters
- Queue corruption possible (depending on Python implementation)
- **No longer thread-safe**

**Solution**: Use `threading.Queue` if you need MPMC.

---

## Counter Contract (Single-Writer, Multi-Reader)

### Write Contract

```python
# In push thread (MQTT IO thread)
self._total_pushed += 1      # ONLY written by push thread
self._total_dropped += 1     # ONLY written by push thread

# In poll thread (Strategy thread)
self._total_polled += 1      # ONLY written by poll thread
```

**Guarantee**: No two threads write to the same counter.

### Read Contract

```python
# Any thread can read (eventually consistent)
stats = dispatcher.stats()
print(stats.total_pushed)    # Eventually consistent read
print(stats.total_dropped)   # Eventually consistent read
print(stats.total_polled)    # Eventually consistent read
print(stats.queue_len)       # Eventually consistent read
```

**Guarantee**: Reads are safe from any thread, but values may reflect different points in time.

### Quiescent Invariant

Under quiescent conditions (no concurrent push/poll):
```python
total_pushed - total_dropped - total_polled == queue_len
```

**Test Coverage**: `test_dispatcher.py::test_invariant_always_holds`

---

## CPython GIL Assumption

### What We Rely On

**CPython's GIL guarantees atomic operations for**:
- `deque.append()`
- `deque.popleft()`
- `int` reads and writes (single bytecode instruction)

### What This Means

```python
# Thread 1 (push)
self._queue.append(event)     # Atomic

# Thread 2 (poll)
event = self._queue.popleft() # Atomic
```

**No locks needed** for SPSC on CPython.

### If You Migrate Away from CPython

**Warning**: PyPy, GraalPy, nogil Python do **not** guarantee this.

**Solution**: Replace `deque` with `threading.Queue`:

```python
import queue

class ThreadSafeDispatcher:
    def __init__(self, maxlen: int):
        self._queue = queue.Queue(maxsize=maxlen)
    
    def push(self, event: T) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Drop oldest manually
            self._queue.get_nowait()
            self._queue.put_nowait(event)
            self._total_dropped += 1
```

---

## Lock-Free Design

### Hot Path (No Locks)

```python
# MQTT IO thread (hot path)
def push(event):
    # No lock here
    self._queue.append(event)  # GIL-atomic
    self._total_pushed += 1    # GIL-atomic (int increment)
```

### Why This Matters

- **Lower latency**: No lock contention
- **Simpler code**: No deadlock risk
- **Predictable**: No priority inversion

### Tradeoff

- **CPython-only**: Not portable to PyPy, GraalPy, nogil Python
- **SPSC-only**: Cannot handle multi-producer or multi-consumer

---

## Reconnect Thread Lifecycle

### Spawn Condition

```python
def _on_disconnect(self, client, userdata, rc):
    # Only spawn if not already reconnecting
    if self._state != ClientState.RECONNECTING:
        self._state = ClientState.RECONNECTING
        self._generation += 1  # Invalidate in-flight messages
        
        # Spawn background thread
        threading.Thread(
            target=self._reconnect_loop,
            daemon=True,
            name="mqtt-reconnect",
        ).start()
```

### Reconnect Loop

```python
def _reconnect_loop(self):
    delay = self._config.reconnect_min_delay
    
    while self._state == ClientState.RECONNECTING:
        time.sleep(delay)
        
        try:
            self._client.reconnect()
            # on_connect() will set state = CONNECTED
            break
        except Exception as e:
            logger.error(f"Reconnect failed: {e}")
            delay = min(delay * 2, self._config.reconnect_max_delay)
```

### Exit Conditions

**Success**: `on_connect()` → state = CONNECTED → loop exits  
**Shutdown**: state = SHUTDOWN → loop exits  

**Guarantee**: Thread exits cleanly in both cases.

---

## Error Isolation

### Parse Errors (Adapter Layer)

```python
try:
    msg = BidOfferV3().parse(payload)
    event = normalize(msg)
    dispatcher.push(event)
except Exception as e:
    self._stats.parse_errors += 1
    logger.error(f"Parse error: {e}")
    # Continue (no crash)
```

**Guarantee**: Parse error does **not** crash MQTT IO thread.

### Callback Errors (MQTT Layer)

```python
for callback in callbacks:
    try:
        callback(topic, payload)
    except Exception as e:
        self._stats.callback_errors += 1
        logger.error(f"Callback error: {e}")
        # Continue (no crash)
```

**Guarantee**: Callback error does **not** crash MQTT IO thread.

---

## Race Conditions Handled

### 1. Queue Empty During Poll

```python
def poll(self, max_events: int) -> list[T]:
    result = []
    for _ in range(min(max_events, len(self._queue))):
        try:
            event = self._queue.popleft()
            result.append(event)
        except IndexError:
            # Queue became empty (race)
            break
    return result
```

**Guarantee**: Graceful handling (no crash).

### 2. Disconnect During Message Callback

```python
def _on_message(self, client, userdata, msg):
    # Check generation (may have changed during callback)
    if self._generation != current_generation:
        return  # Reject stale message
    
    # Dispatch
    for callback in callbacks:
        callback(msg)
```

**Guarantee**: Stale messages rejected.

---

## Performance Implications

### Lock-Free Benefits

- **Latency**: ~10-20us saved vs explicit lock
- **Predictability**: No worst-case lock contention spike
- **Simplicity**: No deadlock debugging

### GIL Limitations

- **Python-level concurrency only**: CPU-bound work still serialized
- **Not true parallelism**: For CPU-bound work, use multiprocessing

---

## Testing Strategies

### Concurrent Testing

```python
# test_dispatcher.py
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
    
    assert len(result) == 1000
```

---

## Next Steps

- **[State Machines](./state_machines.md)** — State transition diagrams
- **[Invariants Defined by Tests](../08_testing_and_guarantees/invariants_defined_by_tests.md)** — Design guarantees
- **[Concurrency Guarantees](../08_testing_and_guarantees/concurrency_guarantees.md)** — Test-backed guarantees
