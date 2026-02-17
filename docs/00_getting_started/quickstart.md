# Quickstart Guide

Get started with settrade-feed-adapter in 5 minutes.

---

## Prerequisites

- Python 3.11+
- Settrade Open API credentials:
  - `app_id`
  - `app_secret` (base64-encoded ECDSA key)
  - `app_code`
  - `broker_id`

---

## Installation

```bash
# Clone the repository
git clone https://github.com/lumduan/settrade-feed-adapter
cd settrade-feed-adapter

# Install dependencies using uv
uv pip install -r requirements.txt

# Or install manually
pip install paho-mqtt pydantic settrade-v2 betterproto
```

---

## Environment Setup

Create a `.env` file with your credentials:

```bash
SETTRADE_APP_ID=your_app_id
SETTRADE_APP_SECRET=your_app_secret
SETTRADE_APP_CODE=your_app_code
SETTRADE_BROKER_ID=your_broker_id
```

---

## Basic Usage Example

```python
from infra.settrade_mqtt import SettradeMQTTClient, MQTTClientConfig
from infra.settrade_adapter import BidOfferAdapter
from core.dispatcher import Dispatcher, DispatcherConfig

# Step 1: Configure MQTT client
config = MQTTClientConfig(
    app_id="your_app_id",
    app_secret="your_app_secret",
    app_code="your_app_code",
    broker_id="your_broker_id",
)

# Step 2: Create client and connect
client = SettradeMQTTClient(config=config)
client.connect()

# Step 3: Create dispatcher
dispatcher = Dispatcher(config=DispatcherConfig(maxlen=100_000))

# Step 4: Create adapter and subscribe
adapter = BidOfferAdapter(mqtt_client=client, dispatcher=dispatcher)
adapter.subscribe("AOT")  # Subscribe to AOT symbol

# Step 5: Consume events in your strategy loop
try:
    while True:
        events = dispatcher.poll(max_events=100)
        for event in events:
            print(f"{event.symbol}: bid={event.bid}, ask={event.ask}")
except KeyboardInterrupt:
    print("Shutting down...")
finally:
    client.shutdown()
```

---

## Testing the Connection

```python
# Test script (examples/example_bidoffer.py)
from infra.settrade_mqtt import SettradeMQTTClient, MQTTClientConfig
from infra.settrade_adapter import BidOfferAdapter
from core.dispatcher import Dispatcher, DispatcherConfig
import time

# Configure
config = MQTTClientConfig(
    app_id="your_app_id",
    app_secret="your_app_secret",
    app_code="your_app_code",
    broker_id="your_broker_id",
)

# Connect
client = SettradeMQTTClient(config=config)
client.connect()

dispatcher = Dispatcher(config=DispatcherConfig(maxlen=100_000))
adapter = BidOfferAdapter(mqtt_client=client, dispatcher=dispatcher)

# Subscribe to multiple symbols
adapter.subscribe("AOT")
adapter.subscribe("PTT")

# Collect events for 60 seconds
start_time = time.time()
event_count = 0

try:
    while time.time() - start_time < 60:
        events = dispatcher.poll(max_events=100)
        event_count += len(events)
        
        for event in events:
            print(
                f"{event.symbol}: "
                f"bid={event.bid:.2f} ({event.bid_vol}) "
                f"ask={event.ask:.2f} ({event.ask_vol})"
            )
        
        time.sleep(0.1)  # 100ms polling interval
finally:
    print(f"\nReceived {event_count} events in 60 seconds")
    print(f"Stats: {dispatcher.stats()}")
    client.shutdown()
```

---

## Monitoring Feed Health

```python
from core.feed_health import FeedHealthMonitor, FeedHealthConfig

# Create health monitor
health_config = FeedHealthConfig(
    global_gap_ms=5000,  # 5 seconds global gap threshold
)
health_monitor = FeedHealthMonitor(config=health_config)

# In your strategy loop
for event in dispatcher.poll(max_events=100):
    # Update health monitor
    health_monitor.update(event.symbol, event.recv_mono_ns)
    
    # Check if feed is healthy
    if health_monitor.is_feed_dead(event.recv_mono_ns):
        print("WARNING: Feed is dead!")
    
    # Check for stale symbols
    stale = health_monitor.stale_symbols(event.recv_mono_ns)
    if stale:
        print(f"Stale symbols: {stale}")
    
    # Process event
    print(f"{event.symbol}: bid={event.bid}, ask={event.ask}")
```

---

## Common Patterns

### Latency Measurement

```python
import time

for event in dispatcher.poll(max_events=100):
    now_ns = time.time_ns()
    latency_us = (now_ns - event.recv_ts) / 1_000
    print(f"{event.symbol}: latency={latency_us:.0f}us")
```

### Error Handling

```python
from core.events import BestBidAsk

try:
    while True:
        events = dispatcher.poll(max_events=100)
        
        for event in events:
            try:
                # Process event
                process_event(event)
            except Exception as e:
                print(f"Error processing {event.symbol}: {e}")
                
except KeyboardInterrupt:
    print("Shutting down...")
finally:
    # Always shutdown cleanly
    client.shutdown()
```

### Reconnect Detection

```python
last_epoch = 0

for event in dispatcher.poll(max_events=100):
    if event.connection_epoch != last_epoch:
        print(f"Reconnect detected! Epoch: {event.connection_epoch}")
        # Clear state, cancel orders, etc.
        last_epoch = event.connection_epoch
    
    # Process event
    process_event(event)
```

---

## Troubleshooting

### Connection Issues

**Problem**: `ConnectionRefusedError` or `TimeoutError`

**Solution**:
1. Verify credentials are correct
2. Check network connectivity
3. Try sandbox environment first: `base_url="https://open-api-test.settrade.com"`

### No Events Received

**Problem**: Connected but no events in dispatcher

**Solution**:
1. Verify symbol subscription: `adapter.subscribe("AOT")`
2. Check market hours (Thai market: 10:00-12:30, 14:30-16:30 ICT)
3. Inspect MQTT client stats: `client.stats()`

### High Drop Rate

**Problem**: `dispatcher.stats().total_dropped > 0`

**Solution**:
1. Increase polling frequency (reduce sleep time)
2. Increase batch size: `dispatcher.poll(max_events=1000)`
3. Increase queue size: `DispatcherConfig(maxlen=500_000)`
4. Optimize processing logic (avoid blocking operations)

---

## Next Steps

- **[Mental Model](./mental_model.md)** — Understand the conceptual flow
- **[Architecture Overview](../01_system_overview/architecture.md)** — Deep dive into components
- **[Event Models](../04_event_models/event_contract.md)** — Understand event contracts
- **[Feed Health Monitoring](../06_feed_liveness/global_liveness.md)** — Monitor feed reliability
