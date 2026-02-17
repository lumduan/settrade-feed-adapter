# Mental Model

How to think about the settrade-feed-adapter pipeline, its invariants, and its failure modes.

---

## The Pipeline

Every piece of market data flows through five stages:

```text
MQTT  ->  Adapter  ->  Event Model  ->  Dispatcher  ->  Strategy
```

| Stage | Module | Thread | Responsibility |
| --- | --- | --- | --- |
| MQTT | `SettradeMQTTClient` | IO thread (paho) | WebSocket+TLS transport, reconnect, token refresh |
| Adapter | `BidOfferAdapter` | IO thread (callback) | Parse protobuf, normalize, forward via `on_event` |
| Event Model | `BestBidAsk` / `FullBidOffer` | (immutable data) | Typed, frozen Pydantic model with timestamps |
| Dispatcher | `Dispatcher` | IO thread writes, strategy thread reads | Bounded `deque(maxlen)`, drop-oldest backpressure |
| Strategy | Your code | Strategy thread | Poll events, make decisions |

The adapter does not hold a reference to the dispatcher. Instead it accepts a callback (`on_event: Callable`) at construction time. The standard wiring is:

```python
adapter = BidOfferAdapter(
    config=BidOfferAdapterConfig(),
    mqtt_client=mqtt_client,
    on_event=dispatcher.push,
)
```

This decoupling means you can replace the dispatcher with any callable -- a list's `.append`, a logging function, or a custom queue -- without modifying the adapter.

---

## Three Core Principles

### 1. Transport Reliability

The MQTT client is a **self-healing connection**. When the network drops or the broker rejects a connection, it enters a reconnect loop with exponential backoff and jitter. When a token approaches expiry, it triggers a **controlled reconnect** -- disconnect, fetch fresh credentials, create an entirely new paho `mqtt.Client` instance, and reconnect. There is no live header mutation on an existing connection.

Each new client instance gets an incremented **generation ID**. Messages arriving from a stale client generation are silently rejected in `_on_message` before any callback runs.

After a successful reconnect, the client replays all subscriptions from its internal source-of-truth dictionary. Only after replay completes does it increment the **reconnect epoch**. This means:

- Epoch 0 = the initial connection.
- Epoch 1 = first reconnect, subscriptions replayed.
- Epoch N = Nth reconnect.

Strategy code detects reconnects by comparing `event.connection_epoch` against the last-seen value.

### 2. Data Correctness

Parsing happens inline in the MQTT IO thread callback. The adapter calls `BidOfferV3().parse(payload)` (betterproto) to deserialize the binary protobuf, then converts prices from the protobuf `Money` type using inline arithmetic:

```python
bid = msg.bid_price1.units + msg.bid_price1.nanos * 1e-9
```

No `Decimal` allocation, no `.to_dict()` roundtrip, no `getattr` loops. The result is a `BestBidAsk` (or `FullBidOffer`) built via `model_construct()`, which skips Pydantic validation entirely. This is the hot-path optimization -- validation is bypassed because the protobuf schema guarantees structural correctness.

When `model_construct()` is used, `bid_flag` and `ask_flag` are stored as plain `int` rather than `BidAskFlag` enum instances. This is safe because `IntEnum` comparison works with raw integers (`2 in (BidAskFlag.ATO, BidAskFlag.ATC)` is `True`).

Prices are IEEE 754 `float` with 15-17 significant digits. Strategy code must compare prices with tolerance (`abs(a - b) < 1e-9`), never with `==`.

### 3. Delivery Control

The dispatcher wraps `collections.deque(maxlen)`. When the deque is full:

1. `push()` checks `len(queue) == maxlen` (pre-append length check).
2. If full, increments `_total_dropped`.
3. `queue.append(event)` executes -- the deque contract automatically evicts the oldest item.
4. Increments `_total_pushed`.

The backpressure policy is **drop-oldest**. Stale market data is worthless; new data always wins.

The consumer calls `poll(max_events)` which pops up to `max_events` items from the left (FIFO order) and increments `_total_polled` by the count returned.

The fundamental accounting invariant is:

```text
total_pushed - total_dropped - total_polled == queue_len
```

This holds exactly under quiescent conditions (no concurrent push/poll). Under concurrent access, all counters are individually atomic (CPython GIL), but the combination is eventually consistent -- reads may reflect slightly different points in time.

---

## Threading Model

The system uses exactly two threads for the data path:

```text
MQTT IO Thread (paho loop_start)       Strategy Thread (your code)
-------------------------------        ---------------------------
on_message callback fires              dispatcher.poll(max_events)
  -> BidOfferAdapter._on_message         -> popleft up to N items
    -> parse protobuf                    -> _total_polled += len
    -> model_construct()                 -> process events
    -> on_event(event)                   -> monitor.on_event(...)
      -> dispatcher.push(event)          -> check health signals
        -> _queue.append(event)
        -> _total_pushed += 1
```

The synchronization point is the `deque`. CPython's GIL guarantees that `deque.append()` and `deque.popleft()` are atomic (single bytecode instruction each). No explicit locks are used in the push/poll hot path.

This is a **strictly SPSC** (single-producer, single-consumer) design:

- `push()` is called only from the MQTT IO thread.
- `poll()` is called only from the strategy thread.
- `_total_pushed` and `_total_dropped` are written only by the push thread.
- `_total_polled` is written only by the poll thread.
- No two threads ever write to the same counter.

If you need multi-producer or multi-consumer, replace `deque` with `threading.Queue` or add explicit locking. The current design is not safe on PyPy, GraalPy, or nogil Python.

---

## Error Isolation

Parse errors and callback errors are tracked in **separate counters**. Each message increments exactly one of three outcomes:

| Outcome | Counter incremented | What happened |
| --- | --- | --- |
| Full success | `_messages_parsed` | Protobuf parsed and callback completed |
| Parse failure | `_parse_errors` | `BidOfferV3().parse()` raised an exception |
| Callback failure | `_callback_errors` | `on_event(event)` raised an exception |

A protobuf parse failure does NOT increment `callback_errors`. A downstream callback failure does NOT increment `parse_errors`. This separation enables precise production debugging -- you can immediately tell whether the problem is in deserialization or in the consumer.

Error logging is rate-limited: the first 10 errors of each type include full stack traces. After that, only every 1000th error is logged to prevent log storms at high message rates.

---

## State Machine

The MQTT client moves through five states:

```text
INIT  ->  CONNECTING  ->  CONNECTED  ->  RECONNECTING  ->  CONNECTED  -> ...
                                                                         |
                                              (any state)  ->  SHUTDOWN
```

| State | Meaning |
| --- | --- |
| `INIT` | Client created, `connect()` not yet called |
| `CONNECTING` | Authentication done, MQTT TCP connect in progress |
| `CONNECTED` | MQTT connected, `on_connect` fired with `rc=0`, subscriptions replayed |
| `RECONNECTING` | Disconnected, background reconnect loop running with backoff |
| `SHUTDOWN` | Terminal state after `shutdown()` -- no further reconnects |

Key design detail: the state transitions to `CONNECTED` only inside the `on_connect` callback (which runs in the IO thread), not when the TCP connect returns. TCP connect success does not guarantee MQTT-level authentication success. The `RECONNECTING` state persists until `on_connect` fires with `rc=0`.

The reconnect loop uses exponential backoff with jitter:

- Starts at `reconnect_min_delay` (default 1.0s).
- Doubles on each failure up to `reconnect_max_delay` (default 30.0s).
- Each delay is jittered by +/-20% to avoid thundering herd.

---

## Anti-Patterns to Avoid

**Do not block inside the poll loop.** Database writes, HTTP calls, or heavy computation in the poll loop will cause the dispatcher queue to fill up and start dropping events. Offload slow work to a separate worker thread.

**Do not ignore `connection_epoch` changes.** When the epoch increments, the MQTT client has reconnected and replayed subscriptions. Any cached order book state, pending order tracking, or derived signals from the old connection are now suspect. Clear and rebuild.

**Do not compare float prices with `==`.** Prices are IEEE 754 floats converted from protobuf `Money(units, nanos)`. Use tolerance comparisons: `abs(a - b) < 1e-9`.

**Do not assume zero drops.** Always monitor `dispatcher.stats().total_dropped`. A non-zero count means your strategy is not keeping up with the feed rate. Check `dispatcher.health().drop_rate_ema` for a smoothed signal.

**Do not ignore `parse_errors`.** Non-zero `adapter.stats()["parse_errors"]` means protobuf messages are arriving that the adapter cannot deserialize. This may indicate a schema version mismatch or corrupted payloads.

**Do not call `push()` from multiple threads.** The dispatcher is strictly SPSC. Adding a second producer invalidates all lock-free safety guarantees. If you need multiple producers, switch to `threading.Queue`.

**Do not call `dispatcher.clear()` while push/poll are active.** `clear()` resets all counters and empties the queue. It must be called from the main thread only, with the MQTT client disconnected or the adapter paused.

---

## Next Steps

- **[Quickstart](./quickstart.md)** -- Get running in 5 minutes
- **[Architecture Overview](../01_system_overview/architecture.md)** -- Component-level deep dive
- **[Threading and Concurrency](../01_system_overview/threading_and_concurrency.md)** -- Concurrency guarantees
- **[Event Models](../04_event_models/event_contract.md)** -- Field semantics and type contracts
