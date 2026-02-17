# Architecture

Component-level architecture of settrade-feed-adapter.

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Settrade Open API Broker                      │
│               (MQTT over WebSocket+TLS, port 443)                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           │ BidOfferV3 (protobuf binary)
                           ▼
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                   Phase 1: Transport Layer                      ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
┌─────────────────────────────────────────────────────────────────┐
│               SettradeMQTTClient                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  • paho.mqtt.client.Client (loop_start thread)            │  │
│  │  • WebSocket+TLS transport                                │  │
│  │  • Token-based authentication (settrade_v2.Context)       │  │
│  │  • Auto-reconnect with exponential backoff                │  │
│  │  • Token refresh before expiration                        │  │
│  │  • Generation-based stale message rejection               │  │
│  │  • Connection epoch tracking                              │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  State Machine: INIT → CONNECTING → CONNECTED → RECONNECTING    │
│                                                                   │
│  Metrics: messages_received, reconnect_count, callback_errors    │
└────────────────────────┬──────────────────────────────────────────┘
                         │
                         │ (topic: str, payload: bytes)
                         ▼
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃               Phase 2: Adapter & Normalization                  ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
┌─────────────────────────────────────────────────────────────────┐
│                  BidOfferAdapter                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  • Protobuf decode (betterproto)                          │  │
│  │  • Field extraction (direct access, no .to_dict())        │  │
│  │  • Money conversion: units + nanos * 1e-9                 │  │
│  │  • Symbol normalization (uppercase)                       │  │
│  │  • Timestamp capture: recv_ts + recv_mono_ns              │  │
│  │  • Connection epoch stamping                              │  │
│  │  • Error isolation (parse error → log + counter)          │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  Output: BestBidAsk | FullBidOffer (Pydantic models)            │
│                                                                   │
│  Metrics: messages_parsed, parse_errors                          │
└────────────────────────┬──────────────────────────────────────────┘
                         │
                         │ BestBidAsk | FullBidOffer
                         ▼
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃              Phase 3: Dispatcher & Backpressure                 ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
┌─────────────────────────────────────────────────────────────────┐
│                      Dispatcher                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  • Bounded deque (maxlen=100,000 default)                 │  │
│  │  • Drop-oldest overflow policy                            │  │
│  │  • Lock-free push/poll (CPython GIL)                      │  │
│  │  • SPSC contract (single producer, single consumer)       │  │
│  │  • EMA drop rate health monitoring                        │  │
│  │  • Exact drop counting                                    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                   │
│  Invariant: total_pushed - total_dropped - total_polled == len  │
│                                                                   │
│  Metrics: total_pushed, total_dropped, total_polled, queue_len   │
└────────────────────────┬──────────────────────────────────────────┘
                         │
                         │ dispatcher.poll(max_events=100)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Your Strategy Code                              │
│  • Process events in batches                                     │
│  • Implement trading logic                                       │
│  • Monitor feed health                                           │
│  • Handle reconnects (connection_epoch)                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Separation of Concerns

Each component has a **single, well-defined responsibility**:

| Component | Responsibility | Never Does |
|-----------|----------------|------------|
| **SettradeMQTTClient** | Transport reliability | Parse business data |
| **BidOfferAdapter** | Protobuf → Typed events | Network I/O |
| **Dispatcher** | Bounded queuing + backpressure | Business logic |
| **Strategy** | Trading decisions | MQTT connection management |

**Key Insight**: No cross-layer dependencies. You can test each component in isolation.

---

## No Cross-Layer Dependencies

```
✅ GOOD: Clean separation
   Strategy → Dispatcher → Adapter → MQTT Client

❌ BAD: Strategy directly calling MQTT client
   Strategy ⤫ MQTT Client
```

**Why this matters**:
- **Testability**: Mock each layer independently
- **Maintainability**: Change one layer without affecting others
- **Replayability**: Inject pre-recorded events at dispatcher level

---

## Error Isolation Boundaries

Errors are **isolated** at each layer:

```
┌─────────────────────────────────┐
│  Strategy Error                 │ → Your error handling
└─────────────────────────────────┘

┌─────────────────────────────────┐
│  Dispatcher Overflow            │ → Drop oldest + counter++
└─────────────────────────────────┘

┌─────────────────────────────────┐
│  Parse Error (Adapter)          │ → Log + parse_errors++
└─────────────────────────────────┘

┌─────────────────────────────────┐
│  Callback Error (MQTT Client)   │ → Log + callback_errors++
└─────────────────────────────────┘

┌─────────────────────────────────┐
│  Network Disconnect             │ → Auto-reconnect loop
└─────────────────────────────────┘
```

**Guarantee**: An error in one layer **never crashes** another layer.

---

## Data Models

### Event Models (core/events.py)

- `BestBidAsk`: Top-of-book bid/ask
- `FullBidOffer`: Full 10-level order book
- `BidAskFlag`: Market session enum (NORMAL, ATO, ATC)

**Properties**:
- Immutable (`frozen=True`)
- Hashable
- Pydantic v2 with strict validation

### Configuration Models

- `MQTTClientConfig`: Transport configuration
- `DispatcherConfig`: Queue configuration
- `FeedHealthConfig`: Feed monitoring configuration

**Properties**:
- Pydantic BaseModel with field validation
- Sensible defaults
- Field descriptions for documentation

---

## Metrics Architecture

Each component exposes a `.stats()` method:

```python
# Transport stats
client.stats()
# → MQTTClientStats(
#      messages_received=1000,
#      reconnect_count=0,
#      callback_errors=0,
#      current_state="CONNECTED"
#    )

# Adapter stats
adapter.stats()
# → AdapterStats(
#      messages_parsed=1000,
#      parse_errors=0
#    )

# Dispatcher stats
dispatcher.stats()
# → DispatcherStats(
#      total_pushed=1000,
#      total_polled=950,
#      total_dropped=0,
#      queue_len=50
#    )
```

**Key Properties**:
- Zero external dependencies (no Prometheus, StatsD, etc.)
- Lock-free reads (eventually consistent)
- Exact counters (not sampled)

---

## Threading Model

**Single Producer, Single Consumer (SPSC)**:

```
┌──────────────────────────────────┐
│  Producer (MQTT IO Thread)       │
│  • Receives binary messages      │
│  • Parses protobuf               │
│  • dispatcher.push(event)        │
└──────────────────────────────────┘
              ↓
        [Bounded Deque]
              ↓
┌──────────────────────────────────┐
│  Consumer (Strategy Thread)      │
│  • events = dispatcher.poll()    │
│  • Process events                │
└──────────────────────────────────┘
```

**Critical Assumption**: CPython GIL makes `deque.append()` and `deque.popleft()` atomic.

**Warning**: Not thread-safe on PyPy, GraalPy, or nogil Python.

---

## Design Philosophy

### Minimal Hot-Path Logic

The hot path (MQTT thread) does **only**:
1. Parse protobuf
2. Extract fields
3. Push to queue

**No**:
- No locks
- No disk I/O
- No network calls
- No blocking operations
- No business logic

### Explicit Over Implicit

**Explicit**:
- Visible queue with maxlen
- Visible drop count
- Visible state machine
- Visible metrics

**Not Implicit**:
- No hidden thread pools
- No hidden buffering
- No hidden retries in adapter layer
- No hidden state

### Type Safety

**All** public APIs use Pydantic models:
- Configuration
- Events
- Stats

**Benefits**:
- IDE autocomplete
- Static type checking (mypy)
- Runtime validation
- Self-documenting

---

## Extension Points

### Adding New Adapters

To support new Settrade protocols (e.g., InfoV3, CandlestickV3):

1. Create new adapter class (e.g., `PriceInfoAdapter`)
2. Implement protobuf parsing for that protocol
3. Define new event model (e.g., `PriceInfo`)
4. Register with MQTT client

**Key**: Adapters are **isolated** — no changes to dispatcher or transport.

### Custom Backpressure Strategies

Replace `Dispatcher` with a custom queue implementation:

```python
class LatencyPriorityDispatcher:
    """Priority queue: drop highest-latency events first."""
    pass
```

### Custom Health Monitors

Extend `FeedHealthMonitor`:

```python
class AdvancedHealthMonitor(FeedHealthMonitor):
    """Add sequence gap detection, heartbeat checks, etc."""
    pass
```

---

## Next Steps

- **[Data Flow](./data_flow.md)** — Trace a message end-to-end
- **[Threading and Concurrency](./threading_and_concurrency.md)** — Concurrency guarantees
- **[State Machines](./state_machines.md)** — State transition diagrams
