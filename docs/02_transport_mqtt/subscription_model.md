# Subscription Model

MQTT topic subscription, message routing, and reconnect replay semantics
for `SettradeMQTTClient` in `infra/settrade_mqtt.py`.

---

## Topic Pattern

Settrade Open API uses hierarchical MQTT topics for market data. The
BidOfferV3 topic pattern is:

```text
proto/topic/bidofferv3/{symbol}
```

Examples:

- `proto/topic/bidofferv3/AOT` -- AOT stock bid/offer data
- `proto/topic/bidofferv3/PTT` -- PTT stock bid/offer data
- `proto/topic/bidofferv3/KBANK` -- KBANK stock bid/offer data

The topic prefix `proto/topic/bidofferv3/` is defined as `_TOPIC_PREFIX`
in `infra/settrade_adapter.py`. The `BidOfferAdapter` constructs full
topics by appending the uppercase symbol to this prefix.

---

## Subscribe API

The `subscribe()` method registers a callback for a given MQTT topic.

```python
from infra.settrade_mqtt import SettradeMQTTClient, MessageCallback

def my_callback(topic: str, payload: bytes) -> None:
    print(f"Received {len(payload)} bytes on {topic}")

client.subscribe(
    topic="proto/topic/bidofferv3/AOT",
    callback=my_callback,
)
```

**Callback signature** (`MessageCallback`):

```python
MessageCallback = Callable[[str, bytes], None]
# (topic: str, payload: bytes) -> None
```

**Callback contract**: Callbacks run inline in the MQTT IO thread and
must be non-blocking (<1ms), perform no I/O, acquire no locks, and
spawn no threads.

---

## Multiple Callbacks Per Topic

Multiple callbacks can be registered for the same topic. Each call to
`subscribe()` with the same topic appends the callback to the list:

```python
client.subscribe("proto/topic/bidofferv3/AOT", callback_a)
client.subscribe("proto/topic/bidofferv3/AOT", callback_b)

# Both callback_a and callback_b are invoked for every message on this topic
```

Internally, `_subscriptions` is a `dict[str, list[MessageCallback]]`.
The first `subscribe()` for a new topic creates the list and sends the
MQTT subscribe to the broker (if connected). Subsequent `subscribe()`
calls for the same topic append to the existing list without sending
another MQTT subscribe.

```python
def subscribe(self, topic: str, callback: MessageCallback) -> None:
    with self._sub_lock:
        if topic not in self._subscriptions:
            self._subscriptions[topic] = []
            # Send MQTT subscribe only for NEW topics, only if connected
            with self._state_lock:
                is_connected = self._state == ClientState.CONNECTED
            if is_connected and self._client is not None:
                self._client.subscribe(topic=topic)
        self._subscriptions[topic].append(callback)
```

---

## Unsubscribe

The `unsubscribe()` method removes a topic and **all** its callbacks:

```python
client.unsubscribe("proto/topic/bidofferv3/AOT")
```

There is no way to remove a single callback from a topic -- unsubscribe
removes the entire topic entry from `_subscriptions` and sends an MQTT
unsubscribe to the broker (if connected).

```python
def unsubscribe(self, topic: str) -> None:
    with self._sub_lock:
        if topic in self._subscriptions:
            del self._subscriptions[topic]
            with self._state_lock:
                is_connected = self._state == ClientState.CONNECTED
            if is_connected and self._client is not None:
                self._client.unsubscribe(topic=topic)
```

Unsubscribing a topic that was never subscribed is a silent no-op.

---

## Subscription Persistence and Replay

Subscriptions are stored in the `_subscriptions` dict, which serves as
the **source of truth**. On every successful connection (initial or
reconnect), `_on_connect(rc=0)` replays all subscriptions:

```python
# In _on_connect(rc=0):
with self._sub_lock:
    topics = list(self._subscriptions.keys())
for topic in topics:
    client.subscribe(topic=topic)
```

Because the client uses `clean_session=True`, the MQTT broker does not
persist subscriptions across connections. The client-side dict ensures
subscriptions survive reconnects without requiring the caller to
re-subscribe manually.

---

## QoS 0 and clean_session=True

### QoS 0 (At-Most-Once)

All subscriptions use QoS 0 (the paho default when no `qos` argument is
passed to `client.subscribe(topic)`). This means:

- No acknowledgment from broker to client.
- Messages may be lost during network instability.
- Lowest possible latency -- no ACK round-trip.

This is the correct choice for real-time market data where **freshness
is more important than reliability**. Stale data is worse than missing
data.

### clean_session=True

The paho client is created with `clean_session=True`:

```python
client = mqtt.Client(
    clean_session=True,
    transport="websockets",
)
```

Implications:

- The broker does not store subscriptions for this client across disconnects.
- No queued messages are delivered on reconnect.
- Each connection starts with a clean slate.
- The client-side `_subscriptions` dict handles persistence via replay.

---

## Message Routing (Hot Path)

The `_on_message()` method is the hot path, running inline in the MQTT
IO thread. The routing pipeline is:

```text
MQTT message arrives
  |
  v
Generation check: generation != _client_generation?
  |-- YES --> reject (stale message from old client), return
  |-- NO  --> continue
  |
  v
Increment _messages_received (under _counter_lock)
  |
  v
Callback lookup: _subscriptions.get(topic)
  |-- None --> no callbacks registered, message silently dropped
  |-- list --> iterate callbacks
  |
  v
For each callback in list:
  try:
    callback(topic, payload)
  except Exception:
    Increment _callback_errors (under _counter_lock)
    Log exception
    Continue to next callback (isolation)
```

Key design decisions in the hot path:

- **Generation check first**: Rejects stale messages before any work.
- **Counter lock (~50ns)**: Thread-safe increment via `_counter_lock`.
- **No subscription lock for reads**: `dict.get()` is CPython GIL-safe
  for reads, avoiding lock contention on the hot path.
- **Per-callback try/except**: A failing callback does not prevent other
  callbacks for the same topic from executing.

Source code:

```python
def _on_message(self, client, userdata, msg, generation):
    # Reject messages from stale client instances
    if generation != self._client_generation:
        return

    with self._counter_lock:
        self._messages_received += 1

    topic = msg.topic
    callbacks = self._subscriptions.get(topic)
    if callbacks is not None:
        for cb in callbacks:
            try:
                cb(topic, msg.payload)
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
                logger.exception("Callback error for topic %s", topic)
```

---

## When to Subscribe

### Before connect() (during INIT)

Subscriptions are stored in the `_subscriptions` dict and replayed when
`_on_connect(rc=0)` fires after the connection is established.

```python
client = SettradeMQTTClient(config=config)
client.subscribe("proto/topic/bidofferv3/AOT", callback)  # Stored, not sent yet
client.connect()
# _on_connect(rc=0) replays the subscription to the broker
```

### During CONNECTED

The MQTT subscribe is sent to the broker immediately, and the
subscription is stored for future replay.

```python
# Client is already CONNECTED
client.subscribe("proto/topic/bidofferv3/PTT", callback)
# MQTT subscribe sent immediately + stored in _subscriptions
```

### During RECONNECTING

The subscription is stored in `_subscriptions` but no MQTT subscribe is
sent (since there is no active connection). It will be replayed on the
next successful `_on_connect(rc=0)`.

```python
# Client is RECONNECTING
client.subscribe("proto/topic/bidofferv3/KBANK", callback)
# Stored only -- will be replayed when connection is restored
```

---

## Adapter Integration

The `BidOfferAdapter` in `infra/settrade_adapter.py` provides a
higher-level subscription API that handles topic construction
and protobuf parsing.

```python
from infra.settrade_adapter import BidOfferAdapter, BidOfferAdapterConfig
from infra.settrade_mqtt import SettradeMQTTClient, MQTTClientConfig

# Setup
mqtt_config = MQTTClientConfig(
    app_id="my_app",
    app_secret="my_secret",
    app_code="my_code",
    broker_id="my_broker",
)
client = SettradeMQTTClient(config=mqtt_config)

events = []
adapter = BidOfferAdapter(
    config=BidOfferAdapterConfig(),
    mqtt_client=client,
    on_event=events.append,
)

# Subscribe via adapter -- builds topic and registers _on_message
adapter.subscribe("AOT")
# Internally calls: client.subscribe("proto/topic/bidofferv3/AOT", adapter._on_message)

adapter.subscribe("ptt")
# Symbol normalized to uppercase "PTT"
# Internally calls: client.subscribe("proto/topic/bidofferv3/PTT", adapter._on_message)
```

The adapter:

1. Normalizes the symbol to uppercase.
2. Constructs the full topic: `f"proto/topic/bidofferv3/{symbol}"`.
3. Calls `self._mqtt_client.subscribe(topic=topic, callback=self._on_message)`.
4. Tracks subscribed symbols in `self._subscribed_symbols` (with dedup).
5. On each message, parses the BidOfferV3 protobuf and emits a
   `BestBidAsk` or `FullBidOffer` event via the `on_event` callback.

---

## Test Coverage Mapping

| Behavior | Test Case |
| --- | --- |
| Subscribe registers callback | `TestSubscription::test_subscribe_registers_callback` |
| Subscribe sends MQTT subscribe when connected | `TestSubscription::test_subscribe_sends_mqtt_subscribe_when_connected` |
| Multiple callbacks per topic | `TestSubscription::test_subscribe_multiple_callbacks_same_topic` |
| Subscribe during RECONNECTING stores in dict | `TestSubscription::test_subscribe_during_reconnecting` |
| Unsubscribe removes topic and callbacks | `TestSubscription::test_unsubscribe_removes_topic` |
| Unsubscribe nonexistent topic is no-op | `TestSubscription::test_unsubscribe_nonexistent_topic` |
| Replay subscriptions on connect | `TestSubscription::test_replay_subscriptions_on_connect` |
| Dispatch to correct callback | `TestMessageDispatch::test_dispatch_to_correct_callback` |
| Dispatch to multiple callbacks | `TestMessageDispatch::test_dispatch_multiple_callbacks` |
| Unknown topic is no-op | `TestMessageDispatch::test_dispatch_unknown_topic_is_noop` |
| Callback isolation (failing callback) | `TestMessageDispatch::test_callback_isolation` |
| Callback error increments counter | `TestMessageDispatch::test_callback_error_increments_counter` |
| Stale generation rejected | `TestMessageDispatch::test_stale_generation_rejected` |
| Messages received counter | `TestMessageDispatch::test_messages_received_counter` |

---

## Implementation Reference

Source: `infra/settrade_mqtt.py`

- `MessageCallback` type alias -- `Callable[[str, bytes], None]`
- `subscribe()` -- Registers callback, sends MQTT subscribe if connected
- `unsubscribe()` -- Removes topic and all callbacks
- `_on_message()` -- Hot path: generation check, counter, callback dispatch
- `_on_connect()` -- Subscription replay from `_subscriptions` dict

Source: `infra/settrade_adapter.py`

- `_TOPIC_PREFIX` -- `"proto/topic/bidofferv3/"`
- `BidOfferAdapter.subscribe()` -- Builds topic, registers `_on_message`
- `BidOfferAdapter._on_message()` -- Parses protobuf, emits events

---

## Related Documents

- [Client Lifecycle](./client_lifecycle.md) -- State machine and transitions
- [Reconnect Strategy](./reconnect_strategy.md) -- Replay on reconnect, generation check
- [Authentication and Token](./authentication_and_token.md) -- Auth flow
