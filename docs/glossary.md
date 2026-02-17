# Glossary

Terminology reference for settrade-feed-adapter.

---

## General Terms

### Adapter
A component that translates between protocols. In this project, adapters parse protobuf messages and convert them to typed event models (e.g., `BidOfferAdapter`).

### Backpressure
A mechanism for handling situations where events arrive faster than they can be processed. This project uses a **drop-oldest** policy.

### Bounded Queue
A queue with a fixed maximum capacity (`maxlen`). When full, adding a new item evicts the oldest item.

### Connection Epoch
An integer counter that increments on every MQTT reconnect. Used by strategy code to detect reconnects and clear state.

### Dispatcher
The bounded event queue that decouples the MQTT IO thread (producer) from the strategy thread (consumer).

### Drop-Oldest Policy
Backpressure strategy where the oldest event in the queue is evicted when the queue is full. Correct for market data where stale data is worthless.

### Event Model
A typed, immutable Pydantic model representing normalized market data (e.g., `BestBidAsk`, `FullBidOffer`).

### Generation
An integer counter that increments on every reconnect. Used to reject stale messages from old connections.

### GIL (Global Interpreter Lock)
CPython's mechanism that ensures only one thread executes Python bytecode at a time. Makes `deque.append()` and `deque.popleft()` atomic.

### Hot Path
The critical code path where latency is most important. In this project, the hot path is: MQTT callback → parse → normalize → push to queue.

### Monotonic Timestamp
A timestamp from `time.perf_counter_ns()` that never goes backwards, even if the system clock is adjusted (NTP). Used for latency measurement.

### Normalization
The process of converting raw protobuf data into a standardized format (e.g., uppercase symbols, float prices).

### SPSC (Single Producer, Single Consumer)
A concurrency pattern where exactly one thread produces items and exactly one thread consumes items. This project's dispatcher is SPSC.

### Wall Clock Timestamp
A timestamp from `time.time_ns()` that represents actual calendar time. Subject to NTP adjustment.

---

## MQTT Terms

### Clean Session
MQTT client flag (`clean_session=True`) that disables persistent subscriptions and queued messages. Correct for real-time data feeds.

### Keepalive
MQTT interval (seconds) between heartbeat messages. Detects stale connections.

### QoS (Quality of Service)
MQTT delivery guarantee level (0, 1, or 2). This project uses QoS 0 (at-most-once) for freshness over reliability.

### Topic
MQTT subscription path (e.g., `proto/topic/bidofferv3/AOT`). Uses hierarchical structure with `/` separator.

### WebSocket Secure (WSS)
MQTT transport over WebSocket with TLS encryption. Used by Settrade Open API (port 443).

---

## Market Data Terms

### ATC (At-The-Close)
Auction period at market close. Prices are zero during ATC.

### ATO (At-The-Opening)
Auction period at market open. Prices are zero during ATO.

### Best Bid/Ask
Top-of-book prices (best bid price and best ask price). Also called Level 1 data.

### Bid Flag / Ask Flag
Market session indicator: `1=NORMAL`, `2=ATO`, `3=ATC`.

### BidOfferV3
Settrade protobuf message containing full order book data (10 bid/ask levels).

### Full Depth
Complete order book (10 bid levels + 10 ask levels). Also called Level 2 or Market Depth.

### Money Type
Protobuf message with `units` (int64) and `nanos` (int32) fields. Converted to float: `units + nanos * 1e-9`.

### Symbol
Stock ticker (e.g., "AOT", "PTT"). Normalized to uppercase in this project.

---

## Performance Terms

### EMA (Exponential Moving Average)
Weighted average that gives more weight to recent values. Used to smooth drop rate measurements.

### Latency Measurement
Time elapsed from message receipt to processing. Measured using monotonic timestamps.

### P50 / P95 / P99 Percentiles
Statistical measures of latency distribution:
- P50 (median): 50% of measurements are below this value
- P95: 95% of measurements are below this value
- P99: 99% of measurements are below this value

### Parse Latency
Time spent parsing protobuf and normalizing to event model.

### Queue Wait Time
Time an event spends waiting in the dispatcher queue before being polled.

### Warmup Period
Initial messages discarded from benchmark measurements to avoid cold-start effects (TLS handshake, bytecode cache, etc.).

---

## Configuration Terms

### alpha (EMA Alpha)
Smoothing factor for exponential moving average (0 < alpha < 1). Smaller values → smoother signal.

### gap_ms
Maximum time (milliseconds) between messages before feed is considered stale.

### maxlen
Maximum queue capacity (default 100,000 events).

### reconnect_min_delay / reconnect_max_delay
Exponential backoff bounds for reconnect attempts (seconds).

### token_refresh_before_exp_seconds
Seconds before token expiry to trigger proactive reconnect for token refresh.

---

## Testing Terms

### Invariant
A property that must always be true. Example: `total_pushed - total_dropped - total_polled == queue_len`.

### Mock
A test double that simulates external dependencies (e.g., mock MQTT client).

### Synthetic Payload
Artificially generated protobuf message for benchmark testing (deterministic, no network latency).

---

## State Machine Terms

### INIT
Initial state after client creation, before `connect()` called.

### CONNECTING
Authentication complete, MQTT connection in progress.

### CONNECTED
MQTT connected and subscriptions active.

### RECONNECTING
Disconnected, background reconnect loop running.

### SHUTDOWN
Terminal state, no further reconnects allowed.

---

## Architecture Terms

### Adapter Layer (Phase 2)
Component responsible for protobuf parsing and normalization.

### Dispatcher Layer (Phase 3)
Component responsible for bounded queuing and backpressure.

### Transport Layer (Phase 1)
Component responsible for MQTT connection, authentication, and reconnection.

### Strategy Layer
Your application code that processes events from the dispatcher.

---

## Error Terms

### Callback Error
Exception raised in user-defined callback function. Counted separately, does not crash MQTT client.

### Error Isolation
Design pattern where errors in one layer do not propagate to other layers.

### Parse Error
Exception during protobuf parsing or normalization. Counted separately, does not crash adapter.

---

## Common Acronyms

- **API**: Application Programming Interface
- **CPU**: Central Processing Unit
- **EMA**: Exponential Moving Average
- **FIFO**: First In, First Out
- **GC**: Garbage Collection
- **GIL**: Global Interpreter Lock
- **HFT**: High-Frequency Trading
- **I/O**: Input/Output
- **MQTT**: Message Queuing Telemetry Transport
- **MPMC**: Multi-Producer, Multi-Consumer
- **NTP**: Network Time Protocol
- **QoS**: Quality of Service
- **SDK**: Software Development Kit
- **SPSC**: Single Producer, Single Consumer
- **TDD**: Test-Driven Development
- **TLS**: Transport Layer Security
- **UAT**: User Acceptance Testing (sandbox environment)
- **WSS**: WebSocket Secure (WebSocket over TLS)

---

## Next Steps

- **[What Is This?](./00_getting_started/what_is_this.md)** — Overview
- **[Architecture](./01_system_overview/architecture.md)** — Component-level design
- **[Event Contract](./04_event_models/event_contract.md)** — Event model specifications
