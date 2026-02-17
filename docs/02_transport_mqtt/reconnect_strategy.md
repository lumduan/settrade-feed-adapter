# Reconnect Strategy

Automatic connection recovery with exponential backoff.

---

## Overview

The MQTT client automatically recovers from network disconnects using:
- **Exponential backoff**: Prevents reconnect storms
- **Generation counter**: Rejects stale messages
- **Connection epoch**: Allows strategy to detect reconnects

---

## Reconnect Trigger Conditions

Reconnect is triggered when:
1. **Network disconnect** (unexpected)
2. **Broker disconnect** (maintenance, restart)
3. **Token refresh** (proactive, before expiry)
4. **Connection failure** (during initial connect)

Reconnect is **NOT** triggered when:
- Clean disconnect via `shutdown()`
- Already in `RECONNECTING` state

---

## Exponential Backoff

```python
# Initial attempt
delay = reconnect_min_delay  # e.g., 1 second

# Subsequent attempts
delay = min(delay * 2, reconnect_max_delay)

# Example progression:
# Attempt 1: 1s
# Attempt 2: 2s
# Attempt 3: 4s
# Attempt 4: 8s
# Attempt 5: 16s (capped at max_delay)
```

**Why exponential backoff?**
- Prevents overwhelming broker during outages
- Gives network time to recover
- Reduces client-side resource usage

---

## Generation Counter

**Purpose**: Prevent stale messages from old connection being dispatched.

### How It Works

```python
# Connection 1
generation = 1
msg1 arrives → generation == 1 → dispatch

# Disconnect
on_disconnect() → generation = 2

# msg1 arrives late (network delay)
msg1 arrives → generation != 2 → REJECT

# Connection 2
generation = 2
msg2 arrives → generation == 2 → dispatch
```

### Test Coverage
- `test_settrade_mqtt.py::TestMessageDispatch::test_stale_generation_rejected`

---

## Connection Epoch

**Purpose**: Allow strategy code to detect reconnects and clear state.

### How It Works

```python
# Initial connection
connection_epoch = 0

# First reconnect
on_connect() → connection_epoch = 1

# Strategy detection
for event in dispatcher.poll():
    if event.connection_epoch != last_epoch:
        print(f"Reconnect detected: {event.connection_epoch}")
        clear_strategy_state()
        last_epoch = event.connection_epoch
```

---

## Reconnect Loop Implementation

```python
def _reconnect_loop(self):
    """Background thread that attempts reconnection with backoff."""
    delay = self._config.reconnect_min_delay
    
    while self._state == ClientState.RECONNECTING:
        time.sleep(delay)
        
        try:
            # Attempt reconnect
            self._client.reconnect()
            # If successful, on_connect() will transition to CONNECTED
            break
        except Exception as e:
            logger.error(f"Reconnect failed: {e}")
            # Exponential backoff
            delay = min(delay * 2, self._config.reconnect_max_delay)
    
    # Thread exits when:
    # - Reconnect succeeds (state → CONNECTED)
    # - Shutdown called (state → SHUTDOWN)
```

---

## Reconnect Guarantees

### 1. No Duplicate Reconnect Threads

**Test**: `test_settrade_mqtt.py::TestReconnect::test_schedule_reconnect_prevents_duplicates`

**Mechanism**: State check before spawning thread.

### 2. Reconnect Blocked After Shutdown

**Test**: `test_settrade_mqtt.py::TestReconnect::test_schedule_reconnect_blocked_after_shutdown`

**Mechanism**: `if state == SHUTDOWN: return`

### 3. Stale Messages Rejected

**Test**: `test_settrade_mqtt.py::TestMessageDispatch::test_stale_generation_rejected`

**Mechanism**: Generation counter mismatch → discard message.

---

## Configuration

```python
config = MQTTClientConfig(
    reconnect_min_delay=1.0,    # Start with 1 second
    reconnect_max_delay=16.0,   # Cap at 16 seconds
)
```

**Tuning Recommendations**:
- **Low latency requirements**: `min=0.5s, max=8s`
- **High availability**: `min=1s, max=30s`
- **Unstable network**: `min=2s, max=60s`

---

## Subscription Replay

On successful reconnect, all subscriptions are automatically replayed:

```python
def _on_connect(self, client, userdata, flags, rc):
    # Replay all subscriptions
    for topic in self._subscriptions.keys():
        self._client.subscribe(topic, qos=0)
```

---

## Monitoring

Track reconnect behavior via metrics:

```python
stats = client.stats()
print(f"Reconnects: {stats.reconnect_count}")
```

Alert if:
- `reconnect_count > 5` in 1 minute: Network instability
- `reconnect_count > 20` in 5 minutes: Persistent issue

---

## Implementation Reference

See [infra/settrade_mqtt.py](../../infra/settrade_mqtt.py):
- `_on_disconnect()` method
- `_schedule_reconnect()` method
- `_reconnect_loop()` method
- `ClientState` enum

---

## Test Coverage

- `test_settrade_mqtt.py::TestReconnect::test_on_disconnect_unexpected_triggers_reconnect`
- `test_settrade_mqtt.py::TestReconnect::test_schedule_reconnect_prevents_duplicates`
- `test_settrade_mqtt.py::TestReconnect::test_reconnect_loop_retries_on_failure`
- `test_settrade_mqtt.py::TestReconnect::test_reconnect_increments_count`

---

## Next Steps

- **[Client Lifecycle](./client_lifecycle.md)** — State machine
- **[Subscription Model](./subscription_model.md)** — Topic management
- **[State Machines](../01_system_overview/state_machines.md)** — Detailed diagrams
