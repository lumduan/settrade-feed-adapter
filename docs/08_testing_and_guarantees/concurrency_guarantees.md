# Concurrency Guarantees

Thread safety contracts and the concurrency model.

---

## Overview

The feed adapter uses a strictly partitioned concurrency model with two
threads and one synchronization point. All safety guarantees depend on the
CPython GIL and the single-producer, single-consumer (SPSC) contract.

---

## Threading Model

| Thread | Components | Operations |
| --- | --- | --- |
| MQTT IO thread | paho-mqtt loop, `_on_message`, `BidOfferAdapter._on_message`, `Dispatcher.push()` | Message receive, parse, normalize, push |
| Strategy thread | `Dispatcher.poll()`, `FeedHealthMonitor`, strategy logic | Event consumption, liveness checks, trading logic |
| Main thread | `connect()`, `subscribe()`, `unsubscribe()`, `shutdown()`, `clear()` | Lifecycle management |
| Background daemon | `_reconnect_loop`, `_token_refresh_check` | Reconnection, token refresh |

---

## SPSC Contract

The `Dispatcher` is **strictly single-producer, single-consumer (SPSC)**:

- **Single producer:** Only the MQTT IO thread calls `push()`.
- **Single consumer:** Only the strategy thread calls `poll()`.
- **No concurrent clear:** `clear()` must not overlap with `push()` or `poll()`.

Any change to this threading model (multi-producer, multi-consumer)
invalidates all safety guarantees. If MPMC is needed, replace `deque` with
`threading.Queue` or add explicit locking.

---

## CPython GIL Assumption

The dispatcher relies on CPython's GIL for atomic operations:

- `deque.append()` -- atomic under CPython GIL (single bytecode instruction)
- `deque.popleft()` -- atomic under CPython GIL
- `int` reads -- atomic under CPython GIL (single `LOAD_FAST`)

This is **NOT guaranteed** on:

- PyPy
- GraalPy
- nogil Python (PEP 703)

If migrating away from CPython, replace `deque` with `threading.Queue` or a
lock-protected buffer.

---

## Counter Contract (Single-Writer, Multi-Reader)

The dispatcher uses three counters with strict ownership:

| Counter | Writer Thread | Reader Threads |
| --- | --- | --- |
| `_total_pushed` | Push thread (MQTT IO) | Any (via `stats()`) |
| `_total_dropped` | Push thread (MQTT IO) | Any (via `stats()`) |
| `_total_polled` | Poll thread (strategy) | Any (via `stats()`) |

No two threads ever write to the same counter. Reads from other threads see
eventually-consistent values. CPython `int` reads are atomic (single bytecode
instruction).

---

## Lock Usage

### Transport (`SettradeMQTTClient`)

| Lock | Protects | Used By |
| --- | --- | --- |
| `_state_lock` | `_state` (ClientState) | All threads |
| `_sub_lock` | `_subscriptions` dict | Main thread (write), IO thread (read) |
| `_counter_lock` | `_messages_received`, `_callback_errors`, `_reconnect_count` | IO thread (write), any thread (read via `stats()`) |
| `_reconnect_lock` | `_reconnecting` flag | Reconnect scheduling |

### Adapter (`BidOfferAdapter`)

| Lock | Protects | Used By |
| --- | --- | --- |
| `_sub_lock` | `_subscribed_symbols` set | Main thread (write), any thread (read via `subscribed_symbols`) |
| `_counter_lock` | `_messages_parsed`, `_parse_errors`, `_callback_errors` | IO thread (write), any thread (read via `stats()`) |

### Dispatcher

No locks. Relies on CPython GIL for atomic `deque` operations and single-writer
counter semantics.

### Feed Health Monitor

No locks. **NOT thread-safe.** Must be called from the strategy thread only.

---

## Concurrent Push/Poll Safety

The dispatcher's `push()` and `poll()` are safe to call concurrently from
different threads because:

1. `push()` only calls `deque.append()` (atomic) and writes `_total_pushed`
   and `_total_dropped` (single-writer).
2. `poll()` only calls `deque.popleft()` (atomic) and writes `_total_polled`
   (single-writer).
3. No counter is written by both threads.

This is tested by the thread safety and stress tests in `test_dispatcher.py`.

---

## Reconnect Serialization

The reconnect loop is serialized by the `_reconnecting` flag protected by
`_reconnect_lock`. This prevents duplicate reconnect threads when multiple
disconnect events or token refresh timers fire concurrently.

```python
with self._reconnect_lock:
    if self._reconnecting:
        return              # Already reconnecting
    self._reconnecting = True
```

The flag is cleared in a `finally` block to guarantee cleanup:

```python
finally:
    with self._reconnect_lock:
        self._reconnecting = False
```

---

## Generation-Based Stale Event Rejection

Each call to `_create_mqtt_client()` increments `_client_generation`. The
`on_message` callback captures the generation at bind time via a closure:

```python
client.on_message = lambda c, u, m: self._on_message(
    client=c, userdata=u, msg=m, generation=generation,
)
```

In `_on_message`, messages from old generations are silently dropped:

```python
if generation != self._client_generation:
    return
```

This prevents stale callbacks from a previous paho-mqtt client instance from
dispatching events after a reconnect has created a new client.

---

## Shutdown Safety

`shutdown()` is safe to call from any thread and is idempotent:

1. Sets state to `SHUTDOWN` under `_state_lock` (prevents reconnect).
2. Signals `_shutdown_event` (stops background threads).
3. Stops MQTT IO loop and disconnects.

The `_shutdown_event` is checked in:

- `_reconnect_loop` -- exits the retry loop
- `_token_refresh_check` -- exits the monitoring loop

---

## What Is NOT Thread-Safe

- `FeedHealthMonitor` -- all methods must be called from the strategy thread
- `Dispatcher.clear()` -- must not be called concurrently with `push()` or `poll()`
- `BidOfferAdapter.subscribe()` / `unsubscribe()` -- main thread only

---

## Related Pages

- [Invariants Defined by Tests](./invariants_defined_by_tests.md) -- tested guarantees
- [Failure Scenarios](./failure_scenarios.md) -- error handling under concurrency
