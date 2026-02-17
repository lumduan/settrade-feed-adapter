# Quickstart Guide

Get started with settrade-feed-adapter in 5 minutes.

---

## Prerequisites

- Python 3.12+
- Settrade Open API credentials (obtain from [open-api.settrade.com](https://open-api.settrade.com)):
  - `app_id` -- application identifier
  - `app_secret` -- base64-encoded ECDSA key (padding is auto-corrected)
  - `app_code` -- application code
  - `broker_id` -- broker identifier (use `"SANDBOX"` for UAT testing)

---

## Installation

```bash
# Clone the repository
git clone https://github.com/lumduan/settrade-feed-adapter
cd settrade-feed-adapter

# Install dependencies using uv
uv sync
```

---

## Environment Setup

Copy the sample environment file and fill in your credentials:

```bash
cp .env.sample .env
```

Edit `.env` with your values:

```bash
SETTRADE_APP_ID=your_app_id_here
SETTRADE_APP_SECRET=your_app_secret_here
SETTRADE_APP_CODE=your_app_code_here
SETTRADE_BROKER_ID=your_broker_id_here
```

The `app_secret` field accepts base64-encoded strings. If padding characters (`=`) are missing, the client adds them automatically via a Pydantic field validator.

---

## Basic Usage

The pipeline flows from left to right:

```text
SettradeMQTTClient -> BidOfferAdapter -> Dispatcher -> Strategy poll loop
```

The adapter uses a **callback pattern** -- you pass a callable (`on_event`) that receives each parsed event. The typical wiring passes `dispatcher.push` as the callback so events land in the bounded queue for the strategy thread to poll.

```python
import time
from dotenv import load_dotenv
import os

from infra.settrade_mqtt import MQTTClientConfig, SettradeMQTTClient
from infra.settrade_adapter import BidOfferAdapter, BidOfferAdapterConfig
from core.dispatcher import Dispatcher, DispatcherConfig
from core.events import BestBidAsk

load_dotenv()

# 1. Configure and connect the MQTT client
mqtt_config = MQTTClientConfig(
    app_id=os.environ["SETTRADE_APP_ID"],
    app_secret=os.environ["SETTRADE_APP_SECRET"],
    app_code=os.environ["SETTRADE_APP_CODE"],
    broker_id=os.environ["SETTRADE_BROKER_ID"],
)
mqtt_client = SettradeMQTTClient(config=mqtt_config)
mqtt_client.connect()

# 2. Create the dispatcher (bounded queue backed by collections.deque)
dispatcher: Dispatcher[BestBidAsk] = Dispatcher(
    config=DispatcherConfig(maxlen=100_000),
)

# 3. Create the adapter with on_event=dispatcher.push
adapter = BidOfferAdapter(
    config=BidOfferAdapterConfig(),
    mqtt_client=mqtt_client,
    on_event=dispatcher.push,          # callback pattern
)

# 4. Subscribe to one or more symbols
adapter.subscribe("AOT")

# 5. Poll events in your strategy loop
try:
    while True:
        events: list[BestBidAsk] = dispatcher.poll(max_events=100)
        for event in events:
            print(f"{event.symbol}: bid={event.bid:.2f} ask={event.ask:.2f}")
        if not events:
            time.sleep(0.05)
except KeyboardInterrupt:
    pass
finally:
    mqtt_client.shutdown()
```

Key points about the wiring:

- `BidOfferAdapter.__init__` takes three positional-or-keyword arguments: `config`, `mqtt_client`, and `on_event`.
- `on_event` is any `Callable[[BidOfferEvent], None]`. Passing `dispatcher.push` connects the adapter output to the dispatcher input.
- The dispatcher uses `collections.deque(maxlen=100_000)` internally. When the queue is full, the oldest event is silently dropped (drop-oldest backpressure).
- `push(event)` is called from the MQTT IO thread; `poll(max_events)` is called from your strategy thread.

---

## Feed Health Monitoring

`FeedHealthMonitor` detects two classes of problems: the entire feed going silent, and individual symbols becoming stale. It uses monotonic timestamps exclusively (`time.perf_counter_ns()`), so it is immune to NTP clock adjustments.

```python
from core.feed_health import FeedHealthMonitor, FeedHealthConfig

# Configure with global threshold and optional per-symbol overrides
monitor = FeedHealthMonitor(
    config=FeedHealthConfig(
        max_gap_seconds=5.0,                          # default
        per_symbol_max_gap={"RARE": 60.0},            # override for illiquid stocks
    ),
)

# Inside your strategy loop, after polling from the dispatcher:
now_ns = time.perf_counter_ns()

for event in events:
    # Record that this symbol just produced an event
    monitor.on_event(event.symbol, now_ns=now_ns)

# Global liveness check (returns False before first event -- not dead, unknown)
if monitor.has_ever_received() and monitor.is_feed_dead(now_ns=now_ns):
    print("FEED DEAD -- no events for 5+ seconds")

# Per-symbol staleness check
if monitor.is_stale("AOT", now_ns=now_ns):
    print("AOT is stale")

# Bulk query for all stale symbols
stale: list[str] = monitor.stale_symbols(now_ns=now_ns)
if stale:
    print(f"Stale symbols: {stale}")
```

`FeedHealthConfig` fields:

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `max_gap_seconds` | `float` | `5.0` | Global gap threshold in seconds |
| `per_symbol_max_gap` | `dict[str, float]` | `{}` | Per-symbol overrides (seconds) |

---

## Reconnect Detection

Every event carries a `connection_epoch` field. It starts at 0 for the initial connection and increments by 1 after each successful reconnect (specifically, after all subscriptions have been replayed). Compare it against the last-seen epoch to detect reconnects:

```python
last_epoch: int | None = None

for event in dispatcher.poll(max_events=100):
    if last_epoch is None:
        last_epoch = event.connection_epoch
    elif event.connection_epoch != last_epoch:
        print(
            f"Reconnect detected: epoch {last_epoch} -> {event.connection_epoch}"
        )
        last_epoch = event.connection_epoch
        # Clear cached order book, cancel pending orders, rebuild state
```

Initializing `last_epoch` to `None` (rather than `0`) avoids a false reconnect signal on startup.

---

## Running the Examples

The repository includes two runnable examples. Both load credentials from `.env` automatically.

**Basic bid/offer feed with latency measurement:**

```bash
python -m examples.example_bidoffer
python -m examples.example_bidoffer --symbol PTT
python -m examples.example_bidoffer --symbol AOT --log-every 100
```

**Feed health monitoring with production guard rails:**

```bash
python -m examples.example_feed_health
python -m examples.example_feed_health --symbol PTT
python -m examples.example_feed_health --symbol AOT --max-gap 10.0
```

Both examples accept `--poll-interval` (seconds, default 0.05) and `--max-events` (batch size, default 100).

---

## Troubleshooting

### Connection Fails with Authentication Error

1. Verify all four credential fields are set in `.env`.
2. Check that `app_secret` is the base64-encoded ECDSA private key, not the raw key.
3. For sandbox testing, set `SETTRADE_BROKER_ID=SANDBOX`. The client internally maps this to broker `"098"` and switches to UAT mode.
4. If your `base_url` needs to be overridden (e.g., for UAT), pass it in `MQTTClientConfig(base_url="https://open-api-test.settrade.com", ...)`.

### Connected But No Events Received

1. Confirm the symbol is subscribed: check `adapter.subscribed_symbols`.
2. Verify Thai market hours (SET: 10:00-12:30, 14:30-16:30 ICT). Outside hours, no BidOffer messages are published.
3. Inspect transport-level stats: `mqtt_client.stats()` shows `messages_received` and `state`.
4. Inspect adapter-level stats: `adapter.stats()` shows `messages_parsed` and `parse_errors`.

### High Drop Rate

When `dispatcher.stats().total_dropped > 0`, the strategy thread is not consuming events fast enough.

1. Reduce sleep time in the poll loop, or eliminate sleep when events are available.
2. Increase batch size: `dispatcher.poll(max_events=500)`.
3. Increase queue depth: `DispatcherConfig(maxlen=500_000)`.
4. Avoid blocking I/O (database writes, HTTP calls) inside the poll loop -- offload to a worker thread.
5. Monitor `dispatcher.health().drop_rate_ema` for a smoothed view of drop pressure.

### Token Expiry During Long Runs

The MQTT client automatically schedules a controlled reconnect before the token expires. The default is `token_refresh_before_exp_seconds=100` (reconnect 100 seconds before expiry). This causes a brief interruption (~1-3 seconds) during which the client transitions through `RECONNECTING` back to `CONNECTED`, and the reconnect epoch increments by 1.

---

## Next Steps

- **[Mental Model](./mental_model.md)** -- Understand the pipeline architecture and threading model
- **[Architecture Overview](../01_system_overview/architecture.md)** -- Deep dive into components
- **[Event Models](../04_event_models/event_contract.md)** -- Understand event contracts and field semantics
- **[Feed Health Monitoring](../06_feed_liveness/global_liveness.md)** -- Production liveness detection
