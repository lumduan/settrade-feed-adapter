# settrade-feed-adapter

A **market data ingestion layer** for Settrade Open API that provides direct control over the MQTT → Protobuf → Event pipeline.

Designed for developers who need **explicit control**, **typed event models**, and **deterministic backpressure handling** instead of the convenient but opaque official SDK.

> This adapter is an infrastructure foundation, not a trading framework. It ends at normalized event emission — what you do with those events is your responsibility.

Official Settrade API Docs: https://developer.settrade.com/open-api/api-reference/reference/sdkv2/python/market-mqtt-realtime-data/1_gettingStart

---

## Design Philosophy

This project is built on these architectural principles:

- **Minimal hot-path logic** — Parse and normalize only, no business logic
- **Explicit backpressure** — Bounded queue with drop-oldest strategy (no hidden buffering)
- **No hidden thread pools** — Single MQTT IO thread, explicit dispatcher, clear ownership
- **Strong typing** — Pydantic models instead of dynamic dictionaries
- **Predictable event flow** — Deterministic pipeline from bytes to typed events
- **Nanosecond timestamps** — Dual timestamps (`recv_ts` wall clock + `recv_mono_ns` monotonic) for latency measurement

---

## Scope

This project focuses **solely on market data ingestion**.

### What This Adapter Provides

- MQTT transport handling (WebSocket Secure + TLS + auto-reconnect)
- Protobuf decoding (BidOfferV3 → typed events)
- Event normalization (`BestBidAsk`, `FullBidOffer` models)
- Bounded dispatch queue with explicit drop strategy
- Direct protobuf field access (no JSON/dict layer)

### What This Adapter Does NOT Provide

- **Order execution** — Use the official SDK's order API
- **Strategy logic** — Implement your own
- **Persistence / data storage** — Up to you (InfluxDB, Parquet, etc.)
- **Replay systems** — Build on top if needed
- **Backtesting** — Out of scope
- **Risk management** — Your responsibility
- **Position tracking** — Not included

This is an **ingestion layer**, not a trading framework.

---

## Key Architectural Advantages

Compared to the official SDK's dictionary-based approach:

| Aspect | Official SDK | This Adapter |
| --- | --- | --- |
| **Data Model** | Dynamic `dict` | Typed Pydantic models |
| **Event Dispatch** | Hidden threading | Explicit bounded queue |
| **Pipeline Visibility** | Opaque callbacks | You own the flow |
| **Abstraction Layer** | JSON-style `.to_dict()` | Direct protobuf field access |
| **Backpressure** | Implicit (thread pool) | Explicit (drop-oldest) |
| **Replay Support** | Not designed for it | Easier to integrate |
| **Integration** | High-level convenience | Low-level control |

### Why Choose This Adapter?

The official SDK is **production-ready and convenient** for most use cases.

However, it returns dynamic JSON-style dictionaries and hides the ingestion pipeline behind callback threads.

**Use this adapter if you need:**

- Explicit control over message parsing and event flow
- Strongly typed events for safer integration
- Custom backpressure handling for high-frequency data
- Integration into low-latency pipelines with measurable overhead
- Foundation for building custom trading infrastructure
- Easier testing and replay mechanisms

**Stick with the SDK if you need:**

- Convenience and simplicity
- Official support and updates
- Order execution API integration
- You don't need pipeline-level control

---

## Performance

### Realistic Performance Expectations

This adapter provides **modest latency improvements** in parse + normalize operations (approximately **1.1–1.3x faster** than the SDK path in practice), depending on workload and environment.

**The primary value is architectural control and deterministic event flow, not raw microsecond gains.**

### Implementation Differences

The adapter takes a different approach than the SDK:

| Implementation Choice | Official SDK | This Adapter | Tradeoff |
| --- | --- | --- | --- |
| **Callback execution** | `threading.Thread` per message | Inline in MQTT thread | Less overhead, but blocks IO thread |
| **Message parsing** | `.parse(msg).to_dict(casing=SNAKE)` | `.parse(msg)` + direct field access | Fewer allocations, but less convenient |
| **Price representation** | `Decimal(units) + Decimal(nanos) / 1e9` | `units + nanos * 1e-9` (float) | Faster, but loses exact decimal precision |
| **Synchronization** | `threading.Lock` on callback pool | `deque.append()` (GIL-atomic) | Simpler, assumes CPython GIL |

### Benchmark Methodology

The benchmark suite measures **parse + normalize latency only** — the cost of converting a raw protobuf payload into a normalized event object. This isolates the performance delta we actually control, excluding network latency which is identical for both paths.

**SDK path measured:**

```python
BidOfferV3().parse(payload).to_dict(casing=betterproto.Casing.SNAKE, include_default_values=True)
```

**Adapter path measured:**

```python
msg = BidOfferV3().parse(payload)
BestBidAsk.model_construct(symbol=..., bid=msg.bid_price1.units + msg.bid_price1.nanos * 1e-9, ...)
```

Both paths use:

- Identical synthetic payloads (fully-populated BidOfferV3 with 10 bid/ask levels)
- Per-message variation to defeat branch predictor / CPU cache effects
- 1,000 warmup messages discarded before measurement
- 3 independent runs with mean +/- stddev for confidence
- GC enabled (realistic conditions)
- Separate processes for isolation

### Running Benchmarks

```bash
# Run full comparison (SDK vs Adapter)
python -m scripts.benchmark_compare

# Run SDK baseline only
python -m scripts.benchmark_sdk --num-messages 50000 --num-runs 3

# Run adapter only
python -m scripts.benchmark_adapter --num-messages 50000 --num-runs 3

# Custom comparison with different target
python -m scripts.benchmark_compare --num-messages 100000 --target-p99-ratio 3.0
```

### Performance Notes

**In practice**, parse + normalize latency improvements are **modest (~1.1–1.3x)** and highly environment-dependent.

Absolute latency numbers vary by:
- CPU model and clock speed
- OS scheduler behavior and system load
- Python version and interpreter optimizations
- Memory pressure and garbage collection timing

**The comparison ratio (adapter vs SDK) is more stable than absolute numbers.**

For authoritative results, benchmark on your target production environment with your actual workload.

### Important Benchmark Limitations

**Read this carefully before interpreting results:**

- **Synthetic payloads only** — Benchmarks use `SerializeToString()` payloads, not live broker traffic. Real-world payloads may differ in size and field population.
  
- **Isolated measurement** — Benchmarks measure parse latency in isolation. Production systems have GIL contention from the MQTT IO thread and strategy thread running concurrently. **Real-world speedups will be lower.**

- **CPython-specific** — All results assume CPython's GIL guarantees for `deque.append()` / `deque.popleft()` atomicity. Results are not valid for PyPy, GraalPy, or nogil Python.

- **Float vs Decimal precision** — The adapter converts `Money(units, nanos)` to `float` via `units + nanos * 1e-9`. The SDK uses `Decimal` for exact arithmetic. **If you need exact decimal representation (e.g., regulatory reporting, accounting), the SDK path may be more appropriate.** The adapter path is designed for latency-sensitive trading where float precision (~15 significant digits) is typically sufficient.

- **Environment-dependent** — Absolute latency numbers vary by CPU, OS scheduler, and system load. Benchmark on your target production environment for authoritative results.

- **No network latency** — Benchmarks measure parse + normalize cost only. Network latency (broker to client) is identical for both paths and excluded from measurement.

- **`process_time` resolution** — CPU measurement uses `time.process_time()` which has OS-dependent resolution (~1ms on some platforms).

---

## Architecture

```text
settrade-feed-adapter/
├── core/
│   ├── events.py                # Pydantic event models: BestBidAsk, FullBidOffer
│   ├── dispatcher.py            # Bounded deque dispatcher with backpressure
│   └── __init__.py
├── infra/
│   ├── settrade_mqtt.py         # MQTT transport (WSS + TLS + auto-reconnect)
│   ├── settrade_adapter.py      # BidOffer adapter (protobuf → event)
│   └── __init__.py
├── scripts/
│   ├── benchmark_utils.py       # Shared benchmark infrastructure
│   ├── benchmark_sdk.py         # SDK baseline benchmark
│   ├── benchmark_adapter.py     # Adapter benchmark
│   └── benchmark_compare.py     # Comparison report generator
├── examples/
│   └── example_bidoffer.py      # Real-world usage with latency measurement
├── tests/
│   ├── test_events.py
│   ├── test_dispatcher.py
│   ├── test_settrade_adapter.py
│   ├── test_settrade_mqtt.py
│   └── test_benchmark_utils.py
└── docs/
    └── plan/
        └── low-latency-mqtt-feed-adapter/
            └── PLAN.md
```

### Data Flow

```text
MQTT Broker (WSS/443)
    │
    ▼
SettradeMQTTClient          ← MQTT IO thread (paho loop_start)
    │ on_message(payload)
    ▼
BidOfferAdapter             ← Parse protobuf, normalize to BestBidAsk
    │ on_event(event)
    ▼
Dispatcher[BestBidAsk]      ← Bounded deque (drop-oldest backpressure)
    │ poll(max_events=100)
    ▼
Strategy Loop               ← Main thread consumes events
```

---

## Requirements

- Python 3.10+
- `paho-mqtt` for MQTT transport
- `pydantic` >= 2.0 for event models
- `settrade-v2` >= 2.2.1 for protobuf schemas and authentication
- A valid Settrade Open API **App ID**, **App Secret**, **App Code**, and **Broker ID**

---

## Installation

```bash
git clone https://github.com/lumduan/settrade-feed-adapter.git
cd settrade-feed-adapter
pip install -e .
```

---

## Quick Start

1. Copy `.env.sample` to `.env` and fill in credentials:

```bash
cp .env.sample .env
# Edit .env with your SETTRADE_APP_ID, SETTRADE_APP_SECRET, SETTRADE_APP_CODE, SETTRADE_BROKER_ID
```

2. Run the example:

```bash
python -m examples.example_bidoffer --symbol AOT
```

### Usage Example

```python
import os
from core.dispatcher import Dispatcher, DispatcherConfig
from core.events import BestBidAsk
from infra.settrade_adapter import BidOfferAdapter, BidOfferAdapterConfig
from infra.settrade_mqtt import MQTTClientConfig, SettradeMQTTClient

# Setup MQTT client
mqtt_config = MQTTClientConfig(
    app_id=os.environ["SETTRADE_APP_ID"],
    app_secret=os.environ["SETTRADE_APP_SECRET"],
    app_code=os.environ["SETTRADE_APP_CODE"],
    broker_id=os.environ["SETTRADE_BROKER_ID"],
)
mqtt_client = SettradeMQTTClient(config=mqtt_config)

# Setup dispatcher and adapter
dispatcher: Dispatcher[BestBidAsk] = Dispatcher(
    config=DispatcherConfig(maxlen=100_000),
)
adapter = BidOfferAdapter(
    config=BidOfferAdapterConfig(),
    mqtt_client=mqtt_client,
    on_event=dispatcher.push,
)

# Connect and subscribe
mqtt_client.connect()
adapter.subscribe(symbol="AOT")

# Strategy loop
try:
    while True:
        events = dispatcher.poll(max_events=100)
        for event in events:
            print(f"{event.symbol} bid={event.bid:.2f} ask={event.ask:.2f}")
        if not events:
            import time
            time.sleep(0.05)
except KeyboardInterrupt:
    mqtt_client.shutdown()
```

---

## Event Models

### BestBidAsk

Best bid/ask (level 1) with dual timestamps:

```python
class BestBidAsk(BaseModel):
    symbol: str
    bid: float          # Best bid price (bid_price1)
    ask: float          # Best ask price (ask_price1)
    bid_vol: int        # Best bid volume (bid_volume1)
    ask_vol: int        # Best ask volume (ask_volume1)
    bid_flag: int       # 0=NONE, 1=NORMAL, 2=ATO, 3=ATC
    ask_flag: int       # 0=NONE, 1=NORMAL, 2=ATO, 3=ATC
    recv_ts: int        # time.time_ns() at receive (wall clock)
    recv_mono_ns: int   # time.perf_counter_ns() (monotonic)
```

### FullBidOffer

Full 10-level depth book:

```python
class FullBidOffer(BaseModel):
    symbol: str
    bid_prices: tuple[float, ...]    # Top 10 bid prices
    ask_prices: tuple[float, ...]    # Top 10 ask prices
    bid_volumes: tuple[int, ...]     # Top 10 bid volumes
    ask_volumes: tuple[int, ...]     # Top 10 ask volumes
    bid_flag: int
    ask_flag: int
    recv_ts: int
    recv_mono_ns: int
```

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=core --cov=infra --cov=scripts
```

---

## Development

### Project Structure

| Directory | Purpose |
| --- | --- |
| `core/` | Domain layer: event models, dispatcher |
| `infra/` | Infrastructure: MQTT transport, protobuf adapter |
| `scripts/` | Benchmark scripts and utilities |
| `examples/` | Usage examples with latency measurement |
| `tests/` | Unit tests (223 tests) |
| `docs/` | Implementation plans |

---

## Notes

- Ensure your API credentials can fetch real-time feeds (some broker sandbox accounts may have limitations)
- Market data structure may evolve — always refer to official Settrade docs
- This adapter relies on CPython's GIL for thread safety — not compatible with nogil Python or alternative interpreters without modification

---

## License

MIT License

---

## Contributing

1. Fork it
2. Build in a feature branch
3. Write tests
4. Submit a PR
