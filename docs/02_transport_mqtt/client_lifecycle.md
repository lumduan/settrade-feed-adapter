# Client Lifecycle

MQTT client connection state machine and lifecycle management.

---

## State Machine

```
INIT
  ↓ connect()
CONNECTING
  ↓ on_connect
CONNECTED
  ↓ on_disconnect (not shutdown)
RECONNECTING
  ↓ reconnect()
CONNECTING
  ↓ on_connect
CONNECTED
  ↓ shutdown()
SHUTDOWN (terminal)
```

---

## State Descriptions

### INIT
- Client created but `connect()` not yet called
- No MQTT connection established
- Safe to configure

### CONNECTING
- Authentication complete
- MQTT connection in progress
- Waiting for broker acknowledgment

### CONNECTED
- MQTT connection established
- Subscriptions active
- Messages being dispatched

### RECONNECTING
- Connection lost
- Background thread attempting reconnect
- Exponential backoff in progress
- Generation incremented (stale message protection)

### SHUTDOWN
- Terminal state
- Clean disconnect completed
- No further reconnects allowed

---

## State Transitions

| From | Event | To | Actions |
|------|-------|-----|---------|
| INIT → CONNECTING | `connect()` | CONNECTING | Authenticate, start MQTT |
| CONNECTING → CONNECTED | `on_connect` | CONNECTED | Subscribe topics, epoch++ |
| CONNECTED → RECONNECTING | `on_disconnect` | RECONNECTING | generation++, spawn reconnect |
| RECONNECTING → CONNECTING | `reconnect()` | CONNECTING | Attempt connection |
| * → SHUTDOWN | `shutdown()` | SHUTDOWN | Stop loop, disconnect |

---

## Lifecycle Example

```python
from infra.settrade_mqtt import SettradeMQTTClient, MQTTClientConfig

# 1. Create (INIT state)
config = MQTTClientConfig(
    app_id="...",
    app_secret="...",
    app_code="...",
    broker_id="...",
)
client = SettradeMQTTClient(config=config)

# 2. Connect (INIT → CONNECTING → CONNECTED)
client.connect()

# 3. Use
client.subscribe("proto/topic/bidofferv3/AOT", callback)

# 4. Automatic reconnect on disconnect
# (CONNECTED → RECONNECTING → CONNECTING → CONNECTED)

# 5. Shutdown (→ SHUTDOWN)
client.shutdown()
```

---

## Implementation Reference

See [infra/settrade_mqtt.py](../../infra/settrade_mqtt.py):
- `ClientState` enum (lines 50-62)
- `connect()` method
- `_on_connect()` callback
- `_on_disconnect()` callback
- `shutdown()` method

---

## Test Coverage

- `test_settrade_mqtt.py::TestStateMachine::test_initial_state`
- `test_settrade_mqtt.py::TestStateMachine::test_connect_transitions_to_connecting`
- `test_settrade_mqtt.py::TestStateMachine::test_on_connect_success_transitions_to_connected`
- `test_settrade_mqtt.py::TestStateMachine::test_on_disconnect_transitions_to_reconnecting`
- `test_settrade_mqtt.py::TestStateMachine::test_shutdown_transitions_to_shutdown`

---

## Next Steps

- **[Authentication and Token](./authentication_and_token.md)** — Auth flow
- **[Reconnect Strategy](./reconnect_strategy.md)** — Auto-reconnect
- **[State Machines](../01_system_overview/state_machines.md)** — Detailed state diagrams
