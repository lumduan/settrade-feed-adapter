# Glossary

Terminology reference for the settrade-feed-adapter project.

---

## General

**Feed adapter** -- The complete system that receives raw MQTT market data
from the Settrade broker, parses protobuf messages, normalizes them into
typed event models, and delivers them to a strategy consumer via a bounded
queue.

**Hot path** -- The code path executed for every incoming MQTT message:
`on_message` callback -> protobuf parse -> event construction -> `push()`.
All hot-path code avoids locks, exceptions for control flow, and unnecessary
allocations.

**Cold path** -- Code that runs infrequently: `connect()`, `subscribe()`,
`shutdown()`, `stats()`. May use locks and perform I/O.

---

## MQTT

**paho-mqtt** -- The Python MQTT client library used for broker connectivity.
Runs a background IO loop thread for message dispatch.

**MQTT IO thread** -- The background thread managed by `paho-mqtt`'s
`loop_start()`. All `on_message` callbacks execute in this thread.

**WebSocket+TLS (WSS)** -- The transport protocol used to connect to the
Settrade MQTT broker on port 443.

**clean_session** -- MQTT connection flag set to `True`. Means no QoS
persistence, no message replay on reconnect. Prioritizes freshness over
reliability.

**Topic** -- An MQTT subscription path. BidOfferV3 topics follow the pattern
`proto/topic/bidofferv3/{symbol}`.

---

## Market Data

**BidOfferV3** -- The protobuf message type published by the Settrade broker
containing 10-level bid/ask prices, volumes, and session flags.

**Money** -- A protobuf nested message with `units` (int) and `nanos` (int)
fields representing a monetary value. Converted to float via
`units + nanos * 1e-9`.

**BestBidAsk** -- A normalized event model containing only the best (level 1)
bid and ask price/volume. Default output of `BidOfferAdapter`.

**FullBidOffer** -- A normalized event model containing all 10 levels of
bid/ask prices and volumes. Produced when `full_depth=True`.

**BidAskFlag** -- An `IntEnum` indicating the market session: `UNDEFINED` (0),
`NORMAL` (1), `ATO` (2), `ATC` (3).

**ATO (At-The-Opening)** -- Auction session at market open. Bid/ask prices
are zero during ATO.

**ATC (At-The-Close)** -- Auction session at market close. Bid/ask prices
are zero during ATC.

**connection_epoch** -- An integer counter on each event that increments on
every MQTT reconnect after subscription replay. Starts at 0 for the initial
connection. Allows strategy code to detect reconnects.

---

## Performance

**P50 / P95 / P99** -- Percentile latency measurements. P99 means 99% of
messages were processed in less than this time.

**EMA (Exponential Moving Average)** -- A smoothed signal computed as
`ema = alpha * sample + (1 - alpha) * ema`. Used for drop rate tracking
in the dispatcher.

**model_construct()** -- Pydantic method that creates a model instance
without running validation. Used in the hot path to avoid validation overhead.

**Warmup** -- The first N messages (default 1000) discarded from benchmark
statistics to account for CPython 3.11+ adaptive specialization.

**Linear interpolation percentile** -- The percentile method used by the
benchmark, matching `numpy.percentile(method='linear')`. Interpolates between
adjacent sorted ranks.

---

## Configuration

**maxlen** -- Maximum number of events the dispatcher queue can hold.
Default 100,000. When full, the oldest event is evicted (drop-oldest policy).

**ema_alpha** -- Smoothing factor for the drop rate EMA. Default 0.01.
Smaller values produce smoother signals with slower response.

**drop_warning_threshold** -- EMA threshold that triggers a warning log.
Default 0.01 (1% drop rate).

**max_gap_seconds** -- Maximum time in seconds without events before the feed
is considered dead. Default 5.0. Converted to nanoseconds internally.

**per_symbol_max_gap** -- Dictionary of symbol-specific staleness thresholds.
Symbols not listed use the global `max_gap_seconds`.

**reconnect_min_delay** -- Minimum backoff delay for reconnect attempts.
Default 1.0 seconds.

**reconnect_max_delay** -- Maximum backoff delay for reconnect attempts.
Default 30.0 seconds.

**token_refresh_before_exp_seconds** -- Seconds before token expiry to
trigger a controlled reconnect. Default 100.

**full_depth** -- Boolean flag on `BidOfferAdapterConfig`. When `True`,
produces `FullBidOffer` events with all 10 levels. Default `False`.

---

## Testing

**Invariant** -- A property that must always hold. The primary dispatcher
invariant: `total_pushed - total_dropped - total_polled == queue_len`.

**Quiescent conditions** -- No concurrent `push()` or `poll()` operations
are in progress. Under quiescent conditions, all counters and queue length
are exactly consistent.

**Eventually consistent** -- Values may reflect slightly different points in
time under concurrent access, but converge to consistent state when
operations quiesce.

---

## State Machine

**ClientState** -- The connection state machine for `SettradeMQTTClient`:

- `INIT` -- Client created, `connect()` not yet called.
- `CONNECTING` -- Authentication complete, MQTT connect in progress.
- `CONNECTED` -- MQTT connected, subscriptions active.
- `RECONNECTING` -- Disconnected, background reconnect loop running.
- `SHUTDOWN` -- Terminal state after `shutdown()`.

---

## Architecture

**SPSC (Single-Producer, Single-Consumer)** -- The threading contract for the
dispatcher. Only one thread pushes (MQTT IO), only one thread polls
(strategy). Violating this invalidates all safety guarantees.

**Drop-oldest** -- The backpressure policy. When the queue is full, the oldest
event is evicted to make room for the new one. Stale market data is worthless.

**Generation** -- An integer incremented each time a new paho-mqtt client is
created. Captured in the `on_message` closure to reject callbacks from
previous client instances.

**Subscription replay** -- After reconnecting, all topics in the
`_subscriptions` dictionary are re-subscribed on the new MQTT client.

---

## Error Handling

**Error isolation** -- Parse errors and callback errors are tracked in
separate counters. A parse failure does not increment the callback error
counter, and vice versa.

**Rate-limited logging** -- In the hot path, the first 10 errors are logged
with full stack traces (`logger.exception()`), then every 1000th error is
logged at `logger.error()` level without a trace.

**Exponential backoff with jitter** -- Reconnect retry strategy. Delay
doubles each attempt (up to `reconnect_max_delay`) and is randomized by
+/-20% to prevent thundering herd.

**Controlled reconnect** -- A reconnect triggered by the token refresh timer
rather than a network failure. Uses the same reconnect guard and backoff
mechanism.
