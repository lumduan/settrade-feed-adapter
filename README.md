# settrade-feed-adapter

A lightweight Python adapter for subscribing to real-time market data from **Settrade Open API** via MQTT, bypassing the official Python SDK.
Designed for algorithmic trading systems and market data pipelines where **low latency and minimal overhead** are critical.

> This adapter connects to Settrade's MQTT feed directly, parses protobuf messages using the official protobuf schemas, and emits normalized events to your own dispatcher or strategy engine.

Official Settrade API Docs: https://developer.settrade.com/open-api/api-reference/reference/sdkv2/python/market-mqtt-realtime-data/1_gettingStart

---

## Features

- Direct MQTT connection to Settrade's real-time data feed (no SDK wrapper layer)
- Parse binary protobuf messages (BidOfferV3) for depth & bid/offer data
- Normalized Pydantic event models (`BestBidAsk`, `FullBidOffer`) for downstream processing
- Bounded event dispatcher (`Dispatcher[T]`) with drop-oldest backpressure
- Minimal allocations & overhead for low-latency use cases
- Easy integration with event dispatcher / strategy loops

---

## Performance

This adapter eliminates four specific SDK bottlenecks in the hot path:

| Bottleneck | Official SDK | This Adapter |
| --- | --- | --- |
| Callback execution | `threading.Thread` per message | Inline in MQTT thread |
| Message parsing | `.parse(msg).to_dict(casing=SNAKE)` | `.parse(msg)` + direct field access |
| Price conversion | `Decimal(units) + Decimal(nanos) / 1e9` | `units + nanos * 1e-9` (float) |
| Synchronization | `threading.Lock` on callback pool | `deque.append()` (GIL-atomic) |

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

### Expected Performance Targets

Based on architectural improvements (no thread spawn, no `.to_dict()`, no `Decimal`, no locks):

| Metric | SDK Baseline (est.) | Adapter Target | Improvement Target |
| --- | --- | --- | --- |
| P50 latency | ~120-150us | ~30-50us | 3-4x faster |
| P95 latency | ~250-350us | ~60-90us | 3-5x faster |
| P99 latency | ~400-600us | ~100-150us | 3-6x faster |
| GC gen-0 collections | Higher | Lower | Reduced allocation pressure |

### Benchmark Limitations

These benchmarks have known limitations that should be considered when interpreting results:

- **Synthetic payloads only** — benchmarks use `SerializeToString()` payloads, not live broker traffic. Real-world payloads may differ in size and field population.
- **Single-threaded measurement** — benchmarks measure parse latency in isolation. Production systems have GIL contention from the MQTT IO thread and strategy thread running concurrently.
- **CPython-specific** — all results assume CPython's GIL guarantees for `deque.append()` / `deque.popleft()` atomicity. Results are not valid for PyPy, GraalPy, or nogil Python.
- **Float vs Decimal** — the adapter converts `Money(units, nanos)` to `float` via `units + nanos * 1e-9`. The SDK uses `Decimal` for exact arithmetic. For prices requiring exact decimal representation (e.g., regulatory reporting), the SDK path may be more appropriate. The adapter path is designed for latency-sensitive trading where float precision (~15 significant digits) is sufficient.
- **Environment-dependent** — latency numbers vary by CPU, OS scheduler, and system load. The comparison **ratio** (adapter vs SDK) is more stable than absolute numbers. Benchmark on your target production environment for authoritative results.
- **No network latency** — benchmarks measure parse + normalize cost only. Network latency (broker to client) is identical for both paths and not measured.
- **`process_time` resolution** — CPU measurement uses `time.process_time()` which has OS-dependent resolution (~1ms on some platforms). Short benchmark runs may show 0% CPU.

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
