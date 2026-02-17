# Mental Model

Understanding the conceptual model of settrade-feed-adapter.

---

## The Big Picture

```
┌──────────────────────────────────────────────────────────────┐
│                      Conceptual Flow                          │
└──────────────────────────────────────────────────────────────┘

MQTT → Adapter → Event Model → Dispatcher → Strategy
  ↑        ↑           ↑            ↑           ↑
  │        │           │            │           │
Phase 1  Phase 2    Phase 2      Phase 3    Your Code
```

---

## Three Core Principles

### 1. Transport Reliability

**Goal**: Never lose connection without knowing about it.

**Mechanisms**:
- **Auto-reconnect** with exponential backoff
- **Token refresh** before expiration
- **Generation counter** to reject stale messages
- **Connection epoch** tracking in every event

**Mental Model**: Think of the MQTT client as a **self-healing network pipe**.

```
[Normal Operation]
  Connected ───> Receiving messages ───> (network blip) ───> Auto-reconnect ───> Connected

[Token Expiry]
  Connected ───> Token near expiry ───> Proactive disconnect ───> Refresh token ───> Reconnect
```

### 2. Data Correctness

**Goal**: Typed, validated events — no surprises.

**Mechanisms**:
- **Pydantic models** for strong typing
- **Input validation** and normalization
- **Direct protobuf access** (no dict conversion)
- **Float precision contract** for price comparisons

**Mental Model**: Think of events as **immutable data packets with guarantees**.

```
Raw Binary Protobuf
      ↓
Betterproto Parse (validates structure)
      ↓
Normalization (uppercase symbol, validate ranges)
      ↓
Pydantic Model Construction (typed, frozen, hashable)
      ↓
BestBidAsk Event (ready for strategy)
```

**Key Guarantee**: If you receive a `BestBidAsk` event, it is **structurally valid** and passed normalization.

### 3. Delivery Control

**Goal**: Predictable backpressure — no hidden buffering.

**Mechanisms**:
- **Bounded queue** with explicit maxlen
- **Drop-oldest policy** (stale data is worthless)
- **Visible overflow metrics** (exact drop count)
- **Batch polling** from strategy thread

**Mental Model**: Think of the dispatcher as a **bounded conveyor belt**.

```
Producer (MQTT thread)          Consumer (Strategy thread)
       ↓                                ↑
   [Push Event]                    [Poll Batch]
       ↓                                ↑
 ┌─────────────────────────────────────┐
 │  Bounded Queue (maxlen=100K)        │
 │  [Event1][Event2][Event3]...[EventN]│
 └─────────────────────────────────────┘
       ↓ (Queue Full)
   [Drop Oldest]
   _total_dropped++
```

**Key Guarantee**: If the queue overflows, you **know exactly how many events were dropped**.

---

## Threading Model

### Single Producer, Single Consumer (SPSC)

```
┌─────────────────────────────────────────────────────────────┐
│  Main Thread (Your Strategy)                                │
│  ├─ dispatcher.poll() → batch of events                     │
│  ├─ Process events                                          │
│  └─ Loop                                                    │
└─────────────────────────────────────────────────────────────┘
                          ↑
                          │ (thread-safe deque)
                          │
┌─────────────────────────────────────────────────────────────┐
│  MQTT IO Thread (paho-mqtt loop)                            │
│  ├─ Receive binary message                                  │
│  ├─ on_message callback (inline)                            │
│  │   ├─ Parse protobuf                                      │
│  │   ├─ Normalize → BestBidAsk                              │
│  │   └─ dispatcher.push(event)                              │
│  └─ Loop                                                    │
└─────────────────────────────────────────────────────────────┘
```

**Key Insight**: Only **one synchronization point** — the bounded queue.

---

## Error Isolation

Errors are **isolated** at each layer and **counted separately**.

### Parse Errors (Phase 2)

```
Binary Payload → Parse Fails → Log + Counter++ → Continue
```

**Guarantee**: Parse error does **not** crash the adapter.

### Callback Errors (Phase 1)

```
Message Arrives → Callback Raises → Log + Counter++ → Continue
```

**Guarantee**: Callback error does **not** crash the MQTT client.

### Strategy Errors (Your Code)

```
Process Event → Strategy Raises → Your Error Handling
```

**Guarantee**: Strategy error does **not** affect the adapter.

---

## State Machines

### MQTT Client State Machine

```
INIT
  ↓ (connect())
CONNECTING
  ↓ (on_connect)
CONNECTED
  ↓ (on_disconnect, not shutdown)
RECONNECTING
  ↓ (reconnect_delay, reconnect())
CONNECTING
  ↓ (on_connect)
CONNECTED
  ↓ (shutdown())
SHUTDOWN (terminal)
```

**Key States**:
- `RECONNECTING`: Background loop attempting reconnect
- `SHUTDOWN`: Terminal state, no further reconnects

### Dispatcher Invariant

At all times:
```
total_pushed - total_dropped - total_polled == queue_len
```

This invariant is **eventually consistent** (lock-free reads), but holds exactly under quiescent conditions.

---

## Data Flow Example

Let's trace a single event:

```
1. Broker Sends BidOfferV3 Protobuf
        ↓
2. SettradeMQTTClient Receives (WSS)
   client._on_message(client, userdata, msg)
   recv_ts = time.time_ns()
   recv_mono_ns = time.perf_counter_ns()
        ↓
3. BidOfferAdapter Parses
   bid_offer_msg = BidOfferV3().parse(msg.payload)
   symbol = bid_offer_msg.symbol.upper()
   bid = bid_offer_msg.bid_price1.units + bid_offer_msg.bid_price1.nanos * 1e-9
        ↓
4. Normalize to BestBidAsk
   event = BestBidAsk.model_construct(
       symbol=symbol,
       bid=bid,
       ask=ask,
       recv_ts=recv_ts,
       recv_mono_ns=recv_mono_ns,
       connection_epoch=client.connection_epoch,
   )
        ↓
5. Dispatcher Pushes
   dispatcher.push(event)
   _total_pushed++
   (if queue full: drop oldest, _total_dropped++)
        ↓
6. Strategy Polls
   events = dispatcher.poll(max_events=100)
   _total_polled += len(events)
        ↓
7. Strategy Processes
   for event in events:
       # Your logic here
       pass
```

---

## Key Takeaways

1. **Transport Reliability**: Self-healing MQTT connection with generation-based stale rejection
2. **Data Correctness**: Typed, validated events with explicit normalization
3. **Delivery Control**: Bounded queue with visible drop-oldest backpressure
4. **Error Isolation**: Errors are counted and logged, never crash the pipeline
5. **Threading Model**: SPSC — one producer (MQTT), one consumer (Strategy)
6. **Observable**: Comprehensive metrics at every layer

---

## Anti-Patterns to Avoid

❌ **Don't block in your strategy loop**  
   → Use async I/O or offload to worker threads

❌ **Don't ignore `connection_epoch` changes**  
   → Clear state when reconnect detected

❌ **Don't compare floats with `==`**  
   → Use tolerance: `abs(a - b) < 1e-9`

❌ **Don't assume zero drops**  
   → Monitor `dispatcher.stats().total_dropped`

❌ **Don't ignore parse_errors**  
   → Check `adapter.stats().parse_errors`

---

## Next Steps

- **[System Overview](../01_system_overview/architecture.md)** — Architecture deep dive
- **[Threading and Concurrency](../01_system_overview/threading_and_concurrency.md)** — Concurrency guarantees
- **[Event Models](../04_event_models/event_contract.md)** — Event contracts
