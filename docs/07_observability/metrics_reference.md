# Metrics Reference

Complete catalog of all metrics exposed by the feed adapter components.

---

## Overview

Each component exposes metrics through a `stats()` or `health()` method that
returns a snapshot (dict or frozen Pydantic model). There is no external
metrics library dependency -- consumers pull metrics on demand.

---

## Transport Metrics

Source: `infra/settrade_mqtt.py` -- `SettradeMQTTClient.stats()`

| Metric | Type | Description |
| --- | --- | --- |
| `messages_received` | Counter | Total MQTT messages received across all topics. Incremented under `_counter_lock` in the IO thread. |
| `callback_errors` | Counter | Total errors caught in per-callback `try/except` during message dispatch. Each failed callback increments this once. |
| `reconnect_count` | Counter | Total successful TCP-level reconnect attempts. Incremented in `_reconnect_loop` after `connect()` returns. |
| `reconnect_epoch` | Counter | Reconnect version counter. Starts at 0 for initial connection. Incremented by 1 after each successful reconnect and subscription replay. Strategy code compares `event.connection_epoch` against this. |
| `state` | Gauge | Current `ClientState` value: `INIT`, `CONNECTING`, `CONNECTED`, `RECONNECTING`, or `SHUTDOWN`. |
| `connected` | Gauge | Boolean (`True`/`False`) indicating whether state is `CONNECTED`. |
| `last_connect_ts` | Gauge | `time.time()` wall-clock timestamp of the most recent successful connection. `0.0` if never connected. |
| `last_disconnect_ts` | Gauge | `time.time()` wall-clock timestamp of the most recent disconnection. `0.0` if never disconnected. |

Access:

```python
stats = mqtt_client.stats()
stats["messages_received"]   # int
stats["reconnect_epoch"]     # int
stats["state"]               # str
```

---

## Adapter Metrics

Source: `infra/settrade_adapter.py` -- `BidOfferAdapter.stats()`

| Metric | Type | Description |
| --- | --- | --- |
| `messages_parsed` | Counter | Total messages successfully parsed and delivered to callback. Incremented only when both parse and callback succeed. |
| `parse_errors` | Counter | Total protobuf parse failures. Incremented in the `except` block of the parse phase. Does not overlap with `callback_errors`. |
| `callback_errors` | Counter | Total downstream callback failures. Incremented in the `except` block of the callback phase. Does not overlap with `parse_errors`. |
| `subscribed_symbols` | Gauge | Sorted list of currently subscribed symbol strings. Snapshot taken under `_sub_lock`. |
| `full_depth` | Gauge | Boolean indicating whether `FullBidOffer` (10-level) or `BestBidAsk` (top-of-book) events are produced. |

Error isolation guarantee: each message increments exactly one of
`messages_parsed`, `parse_errors`, or `callback_errors`.

Access:

```python
stats = adapter.stats()
stats["messages_parsed"]       # int
stats["parse_errors"]          # int
stats["callback_errors"]       # int
stats["subscribed_symbols"]    # list[str]
stats["full_depth"]            # bool
```

---

## Dispatcher Metrics

Source: `core/dispatcher.py` -- `Dispatcher.stats()` and `Dispatcher.health()`

### Stats (via `stats()`)

Returns a frozen `DispatcherStats` Pydantic model.

| Metric | Type | Description |
| --- | --- | --- |
| `total_pushed` | Counter | Total events pushed into the queue, including those that caused a drop. Single-writer: push thread only. |
| `total_polled` | Counter | Total events consumed via `poll()`. Single-writer: poll thread only. |
| `total_dropped` | Counter | Total events dropped due to queue overflow. Each drop means the oldest event was evicted by a new push. Single-writer: push thread only. |
| `queue_len` | Gauge | Current number of events in the queue. Eventually consistent with counter values. |
| `maxlen` | Gauge | Configured maximum queue length. |

Invariant (under quiescent conditions):

```text
total_pushed - total_dropped - total_polled == queue_len
```

### Health (via `health()`)

Returns a frozen `DispatcherHealth` Pydantic model.

| Metric | Type | Description |
| --- | --- | --- |
| `drop_rate_ema` | Gauge | Exponential moving average of drop rate. `0.0` = no drops, `1.0` = every push drops. Updated on each `push()`. Formula: `ema = alpha * sample + (1 - alpha) * ema` where sample is 1.0 on drop, 0.0 otherwise. |
| `queue_utilization` | Gauge | Current queue fill ratio: `len(queue) / maxlen`. Range `[0.0, 1.0]`. |
| `total_dropped` | Counter | Same as in stats -- cumulative drops since last `clear()`. |
| `total_pushed` | Counter | Same as in stats -- cumulative pushes since last `clear()`. |

Access:

```python
stats = dispatcher.stats()
stats.total_pushed      # int
stats.total_dropped     # int
stats.queue_len         # int

health = dispatcher.health()
health.drop_rate_ema        # float
health.queue_utilization    # float
```

---

## Feed Health Metrics

Source: `core/feed_health.py` -- `FeedHealthMonitor` methods

The feed health monitor does not have a single `stats()` method. Instead,
metrics are queried individually:

| Metric | Method | Return Type | Description |
| --- | --- | --- | --- |
| Feed dead | `is_feed_dead(now_ns)` | `bool` | `True` if gap since last global event exceeds `max_gap_seconds`. `False` before first event. |
| Stale symbols | `stale_symbols(now_ns)` | `list[str]` | All symbols whose gap exceeds their threshold. O(N) scan. |
| Tracked count | `tracked_symbol_count()` | `int` | Number of distinct symbols recorded via `on_event()`. |
| Ever received | `has_ever_received()` | `bool` | `True` if at least one event was recorded. Distinguishes startup from healthy. |
| Symbol seen | `has_seen(symbol)` | `bool` | `True` if the specific symbol was ever recorded. |
| Symbol stale | `is_stale(symbol, now_ns)` | `bool` | `True` if the symbol was seen before and its gap exceeds threshold. |
| Symbol gap | `last_seen_gap_ms(symbol, now_ns)` | `float or None` | Gap in milliseconds since last event for a symbol. `None` if never seen. |

Access:

```python
monitor.is_feed_dead()                   # bool
monitor.stale_symbols()                  # list[str]
monitor.tracked_symbol_count()           # int
monitor.last_seen_gap_ms("PTT")          # float or None
```

---

## Metric Consistency

All metrics are **eventually consistent**. Under concurrent access, counter
reads may reflect slightly different points in time. Under quiescent
conditions (no concurrent push/poll), all invariants hold exactly.

Transport and adapter metrics use `threading.Lock` for thread-safe snapshots.
Dispatcher metrics are lock-free (CPython GIL-atomic int reads). Feed health
metrics are not thread-safe and must be read from the strategy thread only.

---

## Related Pages

- [Logging Policy](./logging_policy.md) -- log levels and rate limiting
- [Benchmark Guide](./benchmark_guide.md) -- performance measurement
- [Tuning Guide](../09_production_guide/tuning_guide.md) -- configuring thresholds
