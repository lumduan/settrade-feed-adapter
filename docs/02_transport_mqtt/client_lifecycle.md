# Client Lifecycle

MQTT client connection state machine and lifecycle management for
`SettradeMQTTClient` in `infra/settrade_mqtt.py`.

---

## State Machine Diagram

```text
                  connect()
    [INIT] ──────────────────> [CONNECTING]
                                    │
                         _on_connect(rc=0)
                                    │
                                    v
                              [CONNECTED] <─────────────────┐
                               │       │                    │
                  _on_disconnect(rc!=0) │         _on_connect(rc=0)
                               │       │          (subscription replay
                               v       │           + epoch increment)
                          [RECONNECTING]            │
                               │    │               │
                   _reconnect_loop  │               │
                   creates new      │               │
                   client + TCP     ├───────────────┘
                   connect          │
                                    │ _on_connect(rc!=0)
                                    └───────┐
                                            │ _schedule_reconnect()
                                            └──> (retry with backoff)

    Any state ──── shutdown() ────> [SHUTDOWN]  (terminal)
```

Key design point: the `_reconnect_loop` achieves TCP-level connect but does
**not** transition to CONNECTED. State moves to CONNECTED only inside
`_on_connect(rc=0)`, which confirms MQTT-level authentication success.

---

## State Descriptions

### INIT

- Client created via `SettradeMQTTClient(config=config)`.
- `connect()` not yet called.
- No network activity, no background threads.
- Subscriptions may be registered (stored in `_subscriptions` dict for later replay).

### CONNECTING

- Set by `connect()` after the state guard check (`INIT` required).
- Authentication flow in progress: `_login()`, `_fetch_host_token()`, `_create_mqtt_client()`.
- MQTT `client.connect()` called, `loop_start()` initiated.
- Token refresh timer started via `_start_token_refresh_timer()`.
- Waiting for broker CONNACK to arrive via `_on_connect`.

### CONNECTED

- Set **exclusively** in `_on_connect(rc=0)` -- never in `_reconnect_loop`.
- `_last_connect_ts` recorded via `time.time()`.
- All subscriptions from `_subscriptions` dict replayed via `client.subscribe(topic)`.
- If this is a reconnect (`_last_connect_ts` was previously > 0): `_reconnect_epoch` incremented **after** subscription replay completes.
- Messages dispatched to registered callbacks.

### RECONNECTING

- Set by `_schedule_reconnect()` under `_reconnect_lock`.
- Background thread (`mqtt-reconnect`) runs `_reconnect_loop`.
- Loop fetches fresh host/token, creates a **new** MQTT client (not `client.reconnect()`), and attempts TCP connect.
- On TCP success: loop exits, waits for `_on_connect` to fire.
- On failure: exponential backoff with jitter, then retry.
- `_shutdown_event.wait(timeout=jittered_delay)` allows immediate exit on shutdown.

### SHUTDOWN

- Terminal state. Set by `shutdown()`.
- Idempotent: second `shutdown()` call returns immediately.
- `_shutdown_event` is set, unblocking all background threads.
- `loop_stop()` then `disconnect()` called on the paho client.
- Exceptions from `loop_stop()` and `disconnect()` are tolerated (caught and logged at DEBUG).
- No further reconnects possible: `_schedule_reconnect()` checks for SHUTDOWN and returns.

---

## State Transition Table

| From | Trigger | To | Method | Actions |
| --- | --- | --- | --- | --- |
| INIT | `connect()` | CONNECTING | `connect()` | `_login()`, `_fetch_host_token()`, `_create_mqtt_client()`, `client.connect()`, `loop_start()`, `_start_token_refresh_timer()` |
| CONNECTING | CONNACK `rc=0` | CONNECTED | `_on_connect()` | Record `_last_connect_ts`, replay all subscriptions from `_subscriptions` |
| CONNECTING | CONNACK `rc!=0` | RECONNECTING | `_on_connect()` | Call `_schedule_reconnect()` |
| CONNECTED | Unexpected disconnect `rc!=0` | RECONNECTING | `_on_disconnect()` | Record `_last_disconnect_ts`, call `_schedule_reconnect()` |
| CONNECTED | Clean disconnect `rc=0` | (unchanged) | `_on_disconnect()` | Record `_last_disconnect_ts`, log info. No reconnect. |
| CONNECTED | Token near expiry | RECONNECTING | `_token_refresh_check()` | Call `_schedule_reconnect()` (shared guard prevents duplicates) |
| RECONNECTING | `_on_connect(rc=0)` | CONNECTED | `_on_connect()` | Record `_last_connect_ts`, replay subscriptions, increment `_reconnect_epoch` |
| RECONNECTING | `_on_connect(rc!=0)` | RECONNECTING | `_on_connect()` | Call `_schedule_reconnect()` again |
| Any | `shutdown()` | SHUTDOWN | `shutdown()` | Set `_shutdown_event`, `loop_stop()`, `disconnect()` |

---

## Important Invariant: CONNECTED Only in _on_connect

The state transition to CONNECTED happens **only** in `_on_connect(rc=0)`.
After `_reconnect_loop` achieves a successful TCP connect, the state remains
RECONNECTING until the MQTT broker sends CONNACK and paho fires the
`on_connect` callback with `rc=0`. This ensures MQTT-level authentication
is confirmed before the client is declared alive.

From the source code docstring:

> State transitions to CONNECTED only occur inside `on_connect`
> (which runs in the MQTT IO thread). After a successful
> `_reconnect_loop` TCP connect, the state remains RECONNECTING
> until `on_connect` fires with `rc=0`. This is by design --
> TCP connect success does not guarantee MQTT-level authentication.

---

## Lifecycle Example

```python
from infra.settrade_mqtt import SettradeMQTTClient, MQTTClientConfig

# 1. Create client (state: INIT)
config = MQTTClientConfig(
    app_id="my_app",
    app_secret="my_secret",
    app_code="my_code",
    broker_id="my_broker",
)
client = SettradeMQTTClient(config=config)
assert client.state == ClientState.INIT

# 2. Connect (state: INIT -> CONNECTING)
#    Internally: _login() -> _fetch_host_token() -> _create_mqtt_client()
#    Then: client.connect(), loop_start(), _start_token_refresh_timer()
client.connect()

# 3. Broker sends CONNACK (state: CONNECTING -> CONNECTED)
#    Happens asynchronously via _on_connect(rc=0)
#    All pre-registered subscriptions are replayed

# 4. Subscribe to topics (immediate MQTT subscribe when CONNECTED)
def my_callback(topic: str, payload: bytes) -> None:
    print(f"Received {len(payload)} bytes on {topic}")

client.subscribe("proto/topic/bidofferv3/AOT", my_callback)

# 5. Automatic reconnect on unexpected disconnect
#    CONNECTED -> RECONNECTING (via _on_disconnect(rc!=0))
#    Background thread: fetch fresh token, create new client, connect
#    RECONNECTING -> CONNECTED (via _on_connect(rc=0))
#    All subscriptions automatically replayed

# 6. Shutdown (state: -> SHUTDOWN, terminal)
client.shutdown()
assert client.state == ClientState.SHUTDOWN
```

---

## connect() Guard

The `connect()` method enforces that the client is in `INIT` state:

```python
def connect(self) -> None:
    with self._state_lock:
        if self._state != ClientState.INIT:
            raise RuntimeError(
                f"Cannot connect: client is in {self._state} state"
            )
        self._state = ClientState.CONNECTING
```

Calling `connect()` on a client that is already CONNECTING, CONNECTED,
RECONNECTING, or SHUTDOWN raises `RuntimeError`.

---

## shutdown() Idempotency

The `shutdown()` method is idempotent and safe to call from any thread:

```python
def shutdown(self) -> None:
    with self._state_lock:
        if self._state == ClientState.SHUTDOWN:
            return  # Already shut down
        self._state = ClientState.SHUTDOWN
    # ... cleanup ...
```

---

## Test Coverage Mapping

| Transition / Behavior | Test Case |
| --- | --- |
| Initial state is INIT | `TestStateMachine::test_initial_state` |
| `connect()` -> CONNECTING | `TestStateMachine::test_connect_transitions_to_connecting` |
| `_on_connect(rc=0)` -> CONNECTED | `TestStateMachine::test_on_connect_success_transitions_to_connected` |
| `_on_disconnect(rc!=0)` -> RECONNECTING | `TestStateMachine::test_on_disconnect_transitions_to_reconnecting` |
| `shutdown()` -> SHUTDOWN | `TestStateMachine::test_shutdown_transitions_to_shutdown` |
| `shutdown()` is idempotent | `TestStateMachine::test_shutdown_is_idempotent` |
| `connect()` rejects non-INIT state | `TestStateMachine::test_connect_rejects_non_init_state` |
| `_on_connect(rc!=0)` triggers reconnect | `TestReconnect::test_on_connect_failure_triggers_reconnect` |
| Clean disconnect does not reconnect | `TestReconnect::test_on_disconnect_clean_does_not_reconnect` |
| Disconnect ignored after shutdown | `TestReconnect::test_on_disconnect_ignored_after_shutdown` |
| Shutdown calls loop_stop then disconnect | `TestShutdown::test_shutdown_calls_loop_stop_then_disconnect` |
| Shutdown sets event | `TestShutdown::test_shutdown_sets_event` |
| Shutdown tolerates loop_stop exception | `TestShutdown::test_shutdown_tolerates_loop_stop_exception` |
| Shutdown tolerates disconnect exception | `TestShutdown::test_shutdown_tolerates_disconnect_exception` |

---

## Implementation Reference

Source: `infra/settrade_mqtt.py`

- `ClientState` enum (lines 53-68)
- `SettradeMQTTClient.__init__()` -- initial state and instance variables
- `connect()` -- INIT -> CONNECTING, full auth flow
- `_on_connect()` -- CONNECTING/RECONNECTING -> CONNECTED (rc=0) or reconnect (rc!=0)
- `_on_disconnect()` -- triggers reconnect on unexpected disconnect
- `shutdown()` -- any state -> SHUTDOWN (terminal)

---

## Related Documents

- [Authentication and Token](./authentication_and_token.md) -- Auth flow details
- [Reconnect Strategy](./reconnect_strategy.md) -- Backoff, generation, epoch
- [Subscription Model](./subscription_model.md) -- Topic management and replay
