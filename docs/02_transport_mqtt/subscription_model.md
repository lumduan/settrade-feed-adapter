# Subscription Model

MQTT topic subscription and message routing.

---

## Topic Pattern

Settrade Open API uses hierarchical MQTT topics:

```
proto/topic/bidofferv3/<symbol>
```

**Examples**:
- `proto/topic/bidofferv3/AOT` — AOT stock bid/offer
- `proto/topic/bidofferv3/PTT` — PTT stock bid/offer

---

## Subscription API

```python
from infra.settrade_mqtt import SettradeMQTTClient

# Subscribe to topic
client.subscribe(
    topic="proto/topic/bidofferv3/AOT",
    callback=on_message,
)

# Callback signature
def on_message(topic: str, payload: bytes, recv_ts: int, recv_mono_ns: int):
    print(f"Received {len(payload)} bytes on {topic}")
```

---

## Multiple Callbacks Per Topic

Multiple callbacks can be registered for the same topic:

```python
client.subscribe("proto/topic/bidofferv3/AOT", callback1)
client.subscribe("proto/topic/bidofferv3/AOT", callback2)

# Both callback1 and callback2 will be invoked for each message
```

**Use case**: Different processing pipelines for the same symbol.

---

## Unsubscribe

```python
client.unsubscribe("proto/topic/bidofferv3/AOT")
```

Removes topic from subscription list and stops MQTT subscription.

---

## Subscription Persistence

Subscriptions are **replayed on reconnect**:

```python
def _on_connect(self, client, userdata, flags, rc):
    # Automatic replay
    for topic in self._subscriptions.keys():
        self._client.subscribe(topic, qos=0)
```

**Benefit**: No need to re-subscribe manually after reconnect.

---

## Quality of Service (QoS)

All subscriptions use **QoS 0** (at-most-once):

```python
self._client.subscribe(topic, qos=0)
```

**Why QoS 0?**
- **Lowest latency**: No acknowledgments
- **Fresh data**: Stale data is worthless
- **Snapshot feed**: No sequence guarantees anyway

**Trade-off**: Messages may be lost during disconnect (acceptable for market data).

---

## Clean Session

Client uses `clean_session=True`:

```python
self._client = mqtt.Client(clean_session=True)
```

**Implications**:
- No persistent subscriptions
- No queued messages on broker
- Connection state not preserved across disconnects

**Benefit**: Always receive fresh data, no stale backlog.

---

## Message Routing

```
Message arrives on topic
  ↓
Lookup callbacks for topic
  ↓
For each callback:
  ├─ Check generation (stale rejection)
  ├─ Try: callback(topic, payload, recv_ts, recv_mono_ns)
  └─ Except: log error, increment callback_errors
```

---

## When to Subscribe

### During INIT or CONNECTING

```python
client = SettradeMQTTClient(config)
client.subscribe("proto/topic/bidofferv3/AOT", callback)
client.connect()  # Subscription activated after connect
```

### During CONNECTED

```python
client.connect()
# ... wait for connection ...
client.subscribe("proto/topic/bidofferv3/AOT", callback)
# Subscription activated immediately
```

### During RECONNECTING

```python
# Subscription registered but not yet active
client.subscribe("proto/topic/bidofferv3/AOT", callback)
# Will be activated on next successful connect
```

---

## Adapter Integration

The `BidOfferAdapter` uses subscriptions internally:

```python
from infra.settrade_adapter import BidOfferAdapter

adapter = BidOfferAdapter(
    mqtt_client=client,
    dispatcher=dispatcher,
)

# Subscribe via adapter (handles topic construction)
adapter.subscribe("AOT")  # → proto/topic/bidofferv3/AOT
```

---

## Implementation Reference

See [infra/settrade_mqtt.py](../../infra/settrade_mqtt.py):
- `subscribe()` method
- `unsubscribe()` method
- `_on_message()` callback
- Subscription replay logic in `_on_connect()`

---

## Test Coverage

- `test_settrade_mqtt.py::TestSubscription::test_subscribe_registers_callback`
- `test_settrade_mqtt.py::TestSubscription::test_subscribe_multiple_callbacks_same_topic`
- `test_settrade_mqtt.py::TestSubscription::test_replay_subscriptions_on_connect`
- `test_settrade_mqtt.py::TestSubscription::test_unsubscribe_removes_topic`

---

## Best Practices

1. **Subscribe early**: Before `connect()` or right after
2. **Handle reconnects**: Subscriptions are automatically replayed
3. **Monitor callback_errors**: Track callback exceptions
4. **Use adapters**: Let adapters handle topic construction

---

## Next Steps

- **[Client Lifecycle](./client_lifecycle.md)** — State machine
- **[Reconnect Strategy](./reconnect_strategy.md)** — Connection recovery
- **[BidOffer Adapter](../03_adapter_and_normalization/parsing_pipeline.md)** — Higher-level API
