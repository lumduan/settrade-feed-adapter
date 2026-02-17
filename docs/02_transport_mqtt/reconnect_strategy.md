# Reconnect Strategy

Automatic connection recovery with exponential backoff, client generation
tracking, and connection epoch signaling in `SettradeMQTTClient`.

---

## Overview

The MQTT client automatically recovers from connection failures using a
background reconnect loop. The strategy creates an entirely **new** MQTT
client on each reconnect (not `client.reconnect()`), fetches fresh
credentials, and uses generation IDs to reject stale messages from
previous client instances.

---

## Trigger Conditions

Reconnect **is triggered** when:

1. **Unexpected disconnect** -- `_on_disconnect(rc!=0)` fires, indicating
   the broker or network dropped the connection.
2. **Connect failure** -- `_on_connect(rc!=0)` fires, indicating the MQTT
   broker rejected the connection (e.g., bad credentials).
3. **Token near expiry** -- `_token_refresh_check()` detects the token is
   within `token_refresh_before_exp_seconds` (default 100s) of expiry and
   triggers a controlled reconnect to obtain fresh credentials.

Reconnect is **NOT triggered** when:

- **Clean disconnect** (`rc=0`) -- `_on_disconnect(rc=0)` logs the event
  but does not schedule reconnect. This occurs during normal `shutdown()`.
- **Already reconnecting** -- `_schedule_reconnect()` checks the
  `_reconnecting` flag under `_reconnect_lock` and returns immediately if
  a reconnect thread is already running.
- **After shutdown** -- `_schedule_reconnect()` checks for `SHUTDOWN` state
  and returns without spawning a thread.

---

## New Client Creation (Not client.reconnect())

A critical design decision: the reconnect loop creates an entirely **new**
paho MQTT client rather than calling `client.reconnect()` on the existing
one.

```python
def _reconnect_loop(self) -> None:
    delay = self._config.reconnect_min_delay
    try:
        while not self._shutdown_event.is_set():
            try:
                self._fetch_host_token()                    # Fresh credentials
                new_client = self._create_mqtt_client()     # NEW client instance
                new_client.connect(
                    host=self._host,
                    port=self._config.port,
                    keepalive=self._config.keepalive,
                )
                new_client.loop_start()
                self._client = new_client
                # ... increment reconnect_count ...
                return
            except Exception:
                jittered_delay = delay * random.uniform(0.8, 1.2)
                self._shutdown_event.wait(timeout=jittered_delay)
                delay = min(delay * 2, self._config.reconnect_max_delay)
    finally:
        with self._reconnect_lock:
            self._reconnecting = False
```

**Why new client instead of reconnect()?**

- `client.reconnect()` reuses the old WebSocket headers, which still
  contain the expired token. Creating a new client allows setting fresh
  `Authorization` headers via `ws_set_options()`.
- `_create_mqtt_client()` increments `_client_generation`, enabling stale
  message rejection (see below).
- The old client is cleaned up (loop_stop + disconnect) inside
  `_create_mqtt_client()` before the new one is created.

**Important**: After `_reconnect_loop` achieves TCP-level connect, the
state remains RECONNECTING. Transition to CONNECTED only happens when
`_on_connect(rc=0)` fires, confirming MQTT-level authentication.

---

## Exponential Backoff with Jitter

Failed reconnect attempts use exponential backoff with random jitter
to prevent thundering herd problems when many clients reconnect
simultaneously after a broker outage.

**Algorithm**:

```text
delay = reconnect_min_delay          (default: 1.0s)

On each failed attempt:
  jittered_delay = delay * random.uniform(0.8, 1.2)
  wait(jittered_delay)               (via _shutdown_event.wait)
  delay = min(delay * 2, reconnect_max_delay)
```

**Example progression** (with `min_delay=1.0`, `max_delay=30.0`):

```text
Attempt 1:  delay=1.0s   jittered=0.8-1.2s
Attempt 2:  delay=2.0s   jittered=1.6-2.4s
Attempt 3:  delay=4.0s   jittered=3.2-4.8s
Attempt 4:  delay=8.0s   jittered=6.4-9.6s
Attempt 5:  delay=16.0s  jittered=12.8-19.2s
Attempt 6:  delay=30.0s  jittered=24.0-36.0s  (capped at max_delay)
Attempt 7+: delay=30.0s  jittered=24.0-36.0s  (stays at cap)
```

The wait uses `self._shutdown_event.wait(timeout=jittered_delay)` rather
than `time.sleep()`, so a `shutdown()` call immediately unblocks the wait
and exits the loop.

**Configuration**:

```python
config = MQTTClientConfig(
    reconnect_min_delay=1.0,   # ge=0.1, starting backoff delay
    reconnect_max_delay=30.0,  # ge=1.0, backoff cap
)
```

---

## Client Generation (_client_generation)

### Generation Purpose

The client generation is an integer counter that prevents stale messages
from old MQTT client instances from being dispatched to callbacks after
a reconnect.

### Generation Mechanism

1. `_create_mqtt_client()` increments `_client_generation` each time it
   creates a new paho client.
2. The `on_message` lambda captures the generation at creation time via
   closure:

   ```python
   client.on_message = lambda c, u, m: self._on_message(
       client=c, userdata=u, msg=m,
       generation=generation,  # Captured at _create_mqtt_client time
   )
   ```

3. `_on_message()` (the hot path) checks the generation before processing:

   ```python
   def _on_message(self, client, userdata, msg, generation):
       if generation != self._client_generation:
           return  # Reject stale message
       # ... process message ...
   ```

### Example Scenario

```text
1. Client created (generation=1)
2. Messages arrive with generation=1 -> dispatched normally
3. Network drops, reconnect starts
4. New client created (generation=2)
5. Late message from old client arrives with generation=1
6. generation(1) != _client_generation(2) -> REJECTED
7. Messages from new client arrive with generation=2 -> dispatched
```

This prevents a subtle bug where messages from the old connection's IO
thread arrive after the new connection is established, potentially
delivering duplicate or out-of-order data.

---

## Connection Epoch (_reconnect_epoch)

### Epoch Purpose

The connection epoch allows downstream strategy code to detect when a
reconnect has occurred so it can clear cached state (e.g., last-known
prices, sequence counters).

### Epoch Mechanism

1. `_reconnect_epoch` starts at 0.
2. In `_on_connect(rc=0)`, after all subscriptions are replayed, the
   epoch is incremented -- but **only for reconnects**, not the initial
   connection.
3. Reconnect detection uses `_last_connect_ts`: if it is > 0, this is
   a reconnect (the initial connect sets it from 0).

```python
def _on_connect(self, client, userdata, flags, rc):
    if rc == 0:
        is_reconnect = self._last_connect_ts > 0  # 0 = initial connect

        # ... set CONNECTED, record timestamp ...

        # Replay ALL subscriptions
        with self._sub_lock:
            topics = list(self._subscriptions.keys())
        for topic in topics:
            client.subscribe(topic=topic)

        # Increment epoch AFTER replay, only for reconnects
        if is_reconnect:
            self._reconnect_epoch += 1
```

### Why After Subscription Replay?

The epoch is incremented **after** subscriptions are replayed to ensure
that by the time a consumer sees a new epoch value on an incoming event,
all subscriptions are guaranteed to be active. This prevents a race where
the consumer sees epoch=1 but subscriptions have not yet been re-sent
to the broker.

### Strategy Usage

```python
# In event consumer / strategy code
last_epoch = 0

def on_event(event):
    global last_epoch
    if event.connection_epoch != last_epoch:
        # Reconnect detected -- clear stale state
        clear_order_book_cache()
        last_epoch = event.connection_epoch
    process_event(event)
```

The `BidOfferAdapter` reads `self._mqtt_client.reconnect_epoch` on each
message and includes it in the emitted `BestBidAsk` / `FullBidOffer`
event as the `connection_epoch` field.

---

## Reconnect Guard: No Duplicate Threads

The `_schedule_reconnect()` method uses a `_reconnecting` boolean flag
under `_reconnect_lock` to ensure at most one reconnect thread runs at
a time.

```python
def _schedule_reconnect(self) -> None:
    with self._reconnect_lock:
        if self._reconnecting:
            return                      # Another thread already reconnecting
        with self._state_lock:
            if self._state == ClientState.SHUTDOWN:
                return                  # No reconnect after shutdown
            self._state = ClientState.RECONNECTING
        self._reconnecting = True

    thread = threading.Thread(
        target=self._reconnect_loop,
        daemon=True,
        name="mqtt-reconnect",
    )
    thread.start()
```

The `_reconnecting` flag is cleared in the `finally` block of
`_reconnect_loop`, ensuring it is always released regardless of how the
loop exits (success, shutdown, or exception).

This guard prevents duplicate reconnect threads when:

- Multiple `_on_disconnect` callbacks fire in rapid succession.
- Token refresh timer and network disconnect coincide.
- `_on_connect(rc!=0)` fires while already reconnecting.

---

## Subscription Replay on Connect

Every successful connection (initial or reconnect) replays all
subscriptions from the `_subscriptions` dict in `_on_connect(rc=0)`:

```python
# In _on_connect(rc=0):
with self._sub_lock:
    topics = list(self._subscriptions.keys())
for topic in topics:
    client.subscribe(topic=topic)
```

Because the client uses `clean_session=True`, the broker does not
persist subscriptions across connections. The client-side `_subscriptions`
dict is the source of truth, and every `_on_connect` replays the full
set.

This means:

- Subscriptions registered before `connect()` are replayed on initial
  connect.
- Subscriptions registered during RECONNECTING are replayed on the next
  successful connect.
- Subscriptions registered during CONNECTED are sent immediately and
  also stored for future replay.

---

## Full Reconnect Sequence

```text
1. Trigger: _on_disconnect(rc!=0) or _on_connect(rc!=0) or token refresh
       |
2. _schedule_reconnect()
       |  Check _reconnecting flag (under _reconnect_lock)
       |  Check SHUTDOWN state
       |  Set state = RECONNECTING
       |  Set _reconnecting = True
       |  Spawn mqtt-reconnect daemon thread
       |
3. _reconnect_loop() [background thread]
       |
       |  while not shutdown:
       |    try:
       |      _fetch_host_token()        -- fresh host + dispatcher token
       |      _create_mqtt_client()      -- new paho client, generation++
       |      client.connect()           -- TCP connect
       |      loop_start()               -- start IO loop
       |      _reconnect_count++
       |      return (success)
       |    except:
       |      wait(delay * jitter)       -- via _shutdown_event.wait()
       |      delay = min(delay*2, max)  -- exponential backoff
       |
       |  finally: _reconnecting = False
       |
4. _on_connect(rc=0) [MQTT IO thread]
       |  state = CONNECTED
       |  Record _last_connect_ts
       |  Replay all subscriptions
       |  If reconnect: _reconnect_epoch++
```

---

## Test Coverage Mapping

| Behavior | Test Case |
| --- | --- |
| `_schedule_reconnect` sets RECONNECTING state | `TestReconnect::test_schedule_reconnect_sets_state` |
| No duplicate reconnect threads | `TestReconnect::test_schedule_reconnect_prevents_duplicates` |
| Reconnect blocked after shutdown | `TestReconnect::test_schedule_reconnect_blocked_after_shutdown` |
| Clean disconnect does not reconnect | `TestReconnect::test_on_disconnect_clean_does_not_reconnect` |
| Unexpected disconnect triggers reconnect | `TestReconnect::test_on_disconnect_unexpected_triggers_reconnect` |
| `_on_connect(rc!=0)` triggers reconnect | `TestReconnect::test_on_connect_failure_triggers_reconnect` |
| `_reconnect_loop` clears flag on success | `TestReconnect::test_reconnect_loop_clears_flag_on_success` |
| `_reconnect_loop` clears flag on shutdown | `TestReconnect::test_reconnect_loop_clears_flag_on_shutdown` |
| `_reconnect_loop` retries with backoff | `TestReconnect::test_reconnect_loop_retries_on_failure` |
| Reconnect increments count | `TestReconnect::test_reconnect_increments_count` |
| Disconnect ignored after shutdown | `TestReconnect::test_on_disconnect_ignored_after_shutdown` |
| Stale generation rejected | `TestMessageDispatch::test_stale_generation_rejected` |
| Generation increments on create | `TestClientGeneration::test_generation_increments_on_create` |
| Sequential generation IDs | `TestClientGeneration::test_successive_creates_increment_sequentially` |
| Subscription replay on connect | `TestSubscription::test_replay_subscriptions_on_connect` |
| Token refresh triggers reconnect | `TestTokenRefresh::test_token_refresh_triggers_reconnect` |

---

## Configuration Reference

```python
config = MQTTClientConfig(
    reconnect_min_delay=1.0,    # Starting backoff delay (ge=0.1)
    reconnect_max_delay=30.0,   # Backoff cap (ge=1.0)
)
```

| Field | Default | Constraints | Description |
| --- | --- | --- | --- |
| `reconnect_min_delay` | `1.0` | `ge=0.1` | Starting backoff delay in seconds |
| `reconnect_max_delay` | `30.0` | `ge=1.0` | Maximum backoff delay in seconds |
| `token_refresh_before_exp_seconds` | `100` | `ge=10` | Seconds before token expiry to trigger controlled reconnect |

---

## Monitoring

Track reconnect behavior via the `stats()` method:

```python
stats = client.stats()
print(f"Reconnect count: {stats['reconnect_count']}")
print(f"Reconnect epoch: {stats['reconnect_epoch']}")
print(f"Last connect:    {stats['last_connect_ts']}")
print(f"Last disconnect: {stats['last_disconnect_ts']}")
```

---

## Implementation Reference

Source: `infra/settrade_mqtt.py`

- `_schedule_reconnect()` -- Guard logic, thread spawning
- `_reconnect_loop()` -- Backoff loop, new client creation
- `_create_mqtt_client()` -- Generation increment, old client cleanup
- `_on_connect()` -- Subscription replay, epoch increment
- `_on_disconnect()` -- Trigger condition check
- `_token_refresh_check()` -- Token expiry monitoring

---

## Related Documents

- [Client Lifecycle](./client_lifecycle.md) -- State machine and all transitions
- [Authentication and Token](./authentication_and_token.md) -- Token refresh flow
- [Subscription Model](./subscription_model.md) -- Replay semantics
