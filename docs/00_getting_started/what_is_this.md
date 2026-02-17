# What Is This?

A lightweight, low-latency MQTT-based market data adapter for the Settrade Open API. Built for algorithmic trading systems that need deterministic event delivery and strong data contracts — without relying on the official Settrade SDK's realtime layer.

---

## Architecture at a Glance

```text
Settrade Open API (MQTT Broker)
         |
         | WebSocket + TLS (port 443)
         v
  SettradeMQTTClient          <- Phase 1: Transport
         |
         | Binary protobuf (BidOfferV3)
         v
   BidOfferAdapter            <- Phase 2: Parse + Normalize
         |
         | BestBidAsk / FullBidOffer events
         v
     Dispatcher               <- Phase 3: Bounded Queue
         |
         | poll(max_events)
         v
   Your Strategy Code         <- Consumer
         |
   FeedHealthMonitor          <- Phase 5: Liveness Detection
```

---

## 10 Key Facts

1. **Direct MQTT** — Connects directly to the Settrade MQTT broker via paho-mqtt over WebSocket+TLS, bypassing the SDK's realtime client
2. **Protobuf parsing** — Parses `BidOfferV3` binary messages using betterproto-generated code
3. **Two event types** — `BestBidAsk` (top-of-book, default) or `FullBidOffer` (10-level depth)
4. **Pydantic models** — All events are frozen, hashable, immutable Pydantic models
5. **Hot-path optimized** — Uses `model_construct()` to skip validation, inline Money conversion, no `Decimal`
6. **Bounded deque** — `collections.deque(maxlen)` with drop-oldest backpressure
7. **SPSC pattern** — Single-producer (MQTT thread) / single-consumer (strategy thread), lock-free under CPython GIL
8. **Auto-reconnect** — Exponential backoff with jitter, token refresh, subscription replay
9. **Generation + epoch** — Stale message rejection via generation counter; reconnect detection via connection epoch
10. **Feed health** — Two-tier liveness monitor (global feed + per-symbol) using monotonic timestamps

---

## 3 Design Guarantees

### 1. Transport Reliability

The MQTT client automatically recovers from disconnects with exponential backoff + jitter, refreshes tokens before expiry via controlled reconnect, replays all subscriptions on reconnect, and rejects stale messages from old connections via a generation counter.

### 2. Data Correctness

Events are strongly typed (Pydantic frozen models), symbols are normalized to uppercase, prices use IEEE 754 float via `units + nanos * 1e-9`, and extra fields are rejected. Parse errors and callback errors are counted in separate counters with rate-limited logging.

### 3. Delivery Control

The dispatcher provides bounded queuing with explicit backpressure. When the queue is full, the oldest event is evicted (drop-oldest policy). Drop counts are tracked exactly. An EMA-smoothed drop rate signals when the consumer is falling behind.

---

## What This Is NOT

- **Not a full trading framework** — No order management, no position tracking
- **Not a replacement for the SDK** — Uses the SDK for authentication and protobuf definitions
- **Not for HFT** — Python/CPython has inherent latency limits; this targets microsecond-aware (not nanosecond) strategies
- **Not multi-broker** — Designed for Settrade Open API only

---

## Quick Facts

| Aspect | Value |
| ------ | ----- |
| **Language** | Python 3.11+ |
| **Transport** | MQTT over WebSocket Secure (WSS, port 443) |
| **Serialization** | Protobuf (betterproto) |
| **Concurrency** | Threading (paho-mqtt IO thread + strategy thread) |
| **Type System** | Pydantic v2 frozen models |
| **Test Coverage** | 301 test cases across 6 test files |

---

## Next Steps

- **[Quickstart](./quickstart.md)** — Get running in 5 minutes
- **[Mental Model](./mental_model.md)** — Conceptual understanding of the pipeline
- **[Architecture](../01_system_overview/architecture.md)** — Component-level design
