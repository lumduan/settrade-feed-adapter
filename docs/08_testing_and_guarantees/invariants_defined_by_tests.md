# Invariants Defined by Tests

System invariants backed by 301 tests across 6 test files.

---

## Overview

Every design guarantee in the feed adapter is backed by at least one test.
This page catalogs the key invariants and references the tests that enforce
them.

---

## Dispatcher Invariants

### Queue Accounting Invariant

```text
total_pushed - total_dropped - total_polled == queue_len
```

Under quiescent conditions (no concurrent push/poll), this invariant holds
exactly. It is checked via `Dispatcher._invariant_ok()`.

Source: `core/dispatcher.py` lines 548-575

Tests: `test_dispatcher.py` -- multiple tests call `_invariant_ok()` after
push/poll sequences.

### maxlen >= 1

The dispatcher requires `maxlen > 0` (enforced by Pydantic `gt=0`).
`DispatcherConfig(maxlen=0)` raises `ValidationError`.

Tests:

- `test_dispatcher.py::TestDispatcherConfig::test_maxlen_zero_rejected`
- `test_dispatcher.py::TestDispatcherConfig::test_maxlen_negative_rejected`
- `test_dispatcher.py::TestDispatcherConfig::test_maxlen_one`

### poll(max_events <= 0) Rejected

`poll()` raises `ValueError` if `max_events` is not greater than zero.

```python
dispatcher.poll(max_events=0)    # raises ValueError
dispatcher.poll(max_events=-1)   # raises ValueError
```

Source: `core/dispatcher.py` line 433-434

### Exactly One Counter Increment Per Error

Each message processed by the adapter increments exactly one of:

- `messages_parsed` (success)
- `parse_errors` (protobuf parse failure)
- `callback_errors` (downstream callback failure)

A parse error does NOT increment `callback_errors`, and a callback error does
NOT increment `parse_errors`.

Source: `infra/settrade_adapter.py` -- `_on_message()`

### Drop Count Matches Evicted Events

When the queue is full (`len(queue) == maxlen`), each `push()` increments
`total_dropped` by exactly 1. The `deque(maxlen)` contract guarantees the
oldest item is evicted atomically.

### FIFO Ordering Preserved

Events are consumed in the same order they were pushed. `poll()` uses
`deque.popleft()` which removes from the front.

```python
dispatcher.push("a")
dispatcher.push("b")
dispatcher.push("c")
events = dispatcher.poll(max_events=10)
# events == ["a", "b", "c"]
```

### EMA Decays Without Drops

When no drops occur, the EMA converges toward zero:

```text
ema = alpha * 0.0 + (1 - alpha) * ema
```

Each non-drop push multiplies the EMA by `(1 - alpha)`, causing exponential
decay.

---

## Transport Invariants

### No Duplicate Reconnect Loops

The `_reconnecting` flag under `_reconnect_lock` prevents multiple reconnect
threads from being spawned when multiple disconnect events fire concurrently.

```python
# From infra/settrade_mqtt.py
with self._reconnect_lock:
    if self._reconnecting:
        return
    self._reconnecting = True
```

### Shutdown is Idempotent

Calling `shutdown()` multiple times is safe. The second call returns
immediately because the state is already `SHUTDOWN`.

```python
client.shutdown()   # performs cleanup
client.shutdown()   # returns immediately (no-op)
```

Source: `infra/settrade_mqtt.py` -- `shutdown()` checks state under lock.

### Generation Prevents Stale Event Dispatch

Each new MQTT client increments `_client_generation`. The `_on_message`
callback captures the generation at binding time and rejects messages from
old client instances:

```python
if generation != self._client_generation:
    return
```

This prevents stale callbacks from a previous client from dispatching events
after a reconnect.

---

## Feed Health Invariants

### Startup-Aware: No False Dead Before First Event

`is_feed_dead()` returns `False` when `_global_last_event_mono_ns is None`.
The monitor cannot declare the feed dead before it has ever received data.

### Never-Seen Symbols Are Not Stale

`is_stale()` returns `False` for symbols not in `_last_event_mono_ns`. Use
`has_seen()` to distinguish "never tracked" from "healthy."

### Purge Does Not Affect Global

`purge(symbol)` removes per-symbol state but does not reset
`_global_last_event_mono_ns`. After purging, `has_ever_received()` still
returns `True`.

### Reset Returns to Startup State

`reset()` clears both `_global_last_event_mono_ns` (set to `None`) and
`_last_event_mono_ns` (cleared). After reset, the monitor behaves identically
to a freshly constructed instance.

---

## Event Model Invariants

### Frozen Immutability

All event models (`BestBidAsk`, `FullBidOffer`) and stats models
(`DispatcherStats`, `DispatcherHealth`, `FeedHealthConfig`) use
`frozen=True`. Attribute assignment raises `ValidationError`.

### Extra Fields Rejected

All models use `extra="forbid"`. Passing unexpected fields to the constructor
raises `ValidationError`.

### FullBidOffer Has Exactly 10 Levels

`bid_prices`, `ask_prices`, `bid_volumes`, and `ask_volumes` are constrained
to `min_length=10, max_length=10`. Tuples with fewer or more than 10 elements
are rejected by validation.

---

## Test Suite Summary

| Test File | Tests | Coverage Area |
| --- | --- | --- |
| `test_benchmark_utils.py` | 46 | Percentile, stats, payloads, config, GC, CPU, aggregation, formatting, JSON |
| `test_dispatcher.py` | 113 | Config, stats, health, init, push/poll, overflow/drops, clear, invariant, input validation, thread safety, stress, EMA |
| `test_events.py` | 48 | BidAskFlag, BestBidAsk, FullBidOffer -- frozen, extra rejected, validation, hashable, equality, coercion, auction, epoch |
| `test_feed_health.py` | 25 | Config, startup state, global liveness, per-symbol, last_seen_gap_ms, lifecycle, multiple symbols |
| `test_settrade_adapter.py` | 36 | Config, money_to_float, subscription, parsing, error isolation, rate-limited logging, stats, end-to-end |
| `test_settrade_mqtt.py` | 33 | Config, state machine, subscription, message dispatch, reconnect, token refresh, stats, generation, shutdown |
| **Total** | **301** | |

---

## Related Pages

- [Concurrency Guarantees](./concurrency_guarantees.md) -- thread safety contracts
- [Failure Scenarios](./failure_scenarios.md) -- error handling coverage
