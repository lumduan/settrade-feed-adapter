# State Machines

State transition diagrams for key components.

---

## MQTT Client State Machine

### States

- **INIT**: Client created, `connect()` not yet called
- **CONNECTING**: Auth complete, MQTT connect in progress
- **CONNECTED**: MQTT connected, subscriptions active  
- **RECONNECTING**: Disconnected, background reconnect loop running
- **SHUTDOWN**: Terminal state, no further reconnects

### State Transition Diagram

```
┌──────┐
│ INIT │
└───┬──┘
    │ connect()
    ▼
┌────────────┐
│ CONNECTING │◄────────┐
└─────┬──────┘         │
      │ on_connect     │
      ▼                │
┌───────────┐          │
│ CONNECTED │          │
└─────┬─────┘          │
      │                │
      │ on_disconnect  │ reconnect()
      │ (not shutdown) │
      ▼                │
┌───────────────┐      │
│ RECONNECTING  ├──────┘
└────┬──────────┘
     │ shutdown()
     ▼
┌──────────┐
│ SHUTDOWN │ (terminal)
└──────────┘
```

### Transition Rules

| From | Event | To | Actions |
|------|-------|-----|---------|
| INIT → CONNECTING | `connect()` | CONNECTING | • Authenticate<br>• Start MQTT connection |
| CONNECTING → CONNECTED | `on_connect` | CONNECTED | • Subscribe to topics<br>• connection_epoch++ |
| CONNECTED → RECONNECTING | `on_disconnect` (not shutdown) | RECONNECTING | • generation++<br>• Spawn reconnect thread |
| RECONNECTING → CONNECTING | `reconnect()` in loop | CONNECTING | • Attempt reconnection |
| * → SHUTDOWN | `shutdown()` | SHUTDOWN | • Stop MQTT loop<br>• No further reconnects |

### State Invariants

**CONNECTED**:
- MQTT client is connected
- Subscriptions are active
- Messages are being dispatched

**RECONNECTING**:
- Background thread is running
- Exponential backoff in progress
- No messages dispatched (generation mismatch)

**SHUTDOWN**:
- Terminal state
- No reconnect attempts
- Clean disconnect completed

---

## Dispatcher State (Implicit)

The dispatcher doesn't have an explicit state machine, but maintains an **invariant**:

```
total_pushed - total_dropped - total_polled == queue_len
```

### Invariant Flow

```
[Initial State]
  total_pushed  = 0
  total_dropped = 0
  total_polled  = 0
  queue_len     = 0
  Invariant: 0 - 0 - 0 == 0 ✅

[After push(event)]
  total_pushed  = 1
  total_dropped = 0
  total_polled  = 0
  queue_len     = 1
  Invariant: 1 - 0 - 0 == 1 ✅

[After poll(1)]
  total_pushed  = 1
  total_dropped = 0
  total_polled  = 1
  queue_len     = 0
  Invariant: 1 - 0 - 1 == 0 ✅

[Push to full queue (drop oldest)]
  total_pushed  = 100001
  total_dropped = 1
  total_polled  = 0
  queue_len     = 100000
  Invariant: 100001 - 1 - 0 == 100000 ✅
```

**Test Coverage**: `test_dispatcher.py::test_invariant_always_holds`

---

## Reconnect Loop State Machine

### States

- **IDLE**: No reconnect loop running
- **BACKOFF**: Waiting before next attempt (exponential backoff)
- **ATTEMPTING**: Calling `client.reconnect()`
- **SUCCESS**: Connection restored, loop exits
- **SHUTDOWN**: Shutdown called, loop exits

### State Diagram

```
┌──────┐
│ IDLE │
└───┬──┘
    │ on_disconnect (state != SHUTDOWN)
    ▼
┌─────────┐
│ BACKOFF │◄────────┐
└────┬────┘         │
     │ sleep(delay) │
     ▼              │
┌──────────────┐    │
│ ATTEMPTING   │    │
└────┬─────────┘    │
     │              │
     ├─ reconnect() │
     │  fails       │
     ├──────────────┘
     │
     ├─ reconnect()
     │  succeeds
     ▼
┌─────────┐
│ SUCCESS │ (exit)
└─────────┘

OR

┌─────────┐
│ SHUTDOWN│ (exit)
└─────────┘
```

### Backoff Calculation

```python
delay = min(delay * 2, max_delay)
```

**Example**:
```
Attempt 1: delay = 1s
Attempt 2: delay = 2s
Attempt 3: delay = 4s
Attempt 4: delay = 8s
Attempt 5: delay = 16s (capped at max_delay=16s)
```

---

## Generation Lifecycle

### Purpose

Prevent stale messages from old connection being dispatched after reconnect.

### Lifecycle

```
[Connection 1]
  generation = 1
  Messages dispatched with generation = 1

[Disconnect]
  on_disconnect() → generation = 2

[In-flight messages from Connection 1]
  on_message() → if generation != 2: reject

[Connection 2]
  generation = 2
  Messages dispatched with generation = 2
```

### Test Coverage

`test_settrade_mqtt.py::test_generation_prevents_stale_messages`

```python
# Connection 1
generation = 1
msg1 = simulate_message()  # generation = 1

# Reconnect
client._on_disconnect()
# generation = 2

# msg1 arrives late
client._on_message(msg1)  # Rejected (generation mismatch)
```

---

## Connection Epoch Lifecycle

### Purpose

Allow strategy code to detect reconnects and clear state.

### Lifecycle

```
[Initial Connect]
  connection_epoch = 0
  All events have connection_epoch = 0

[Reconnect 1]
  on_connect() → connection_epoch = 1
  All events have connection_epoch = 1

[Reconnect 2]
  on_connect() → connection_epoch = 2
  All events have connection_epoch = 2
```

### Strategy Pattern

```python
last_epoch = 0

for event in dispatcher.poll():
    if event.connection_epoch != last_epoch:
        print(f"Reconnect detected: {event.connection_epoch}")
        clear_state()
        last_epoch = event.connection_epoch
    
    process_event(event)
```

---

## Feed Health State Machine

### States

- **HEALTHY**: Receiving messages within gap threshold
- **STALE**: No messages for > gap_ms (global or per-symbol)
- **DEAD**: Feed completely silent

### State Transitions

```
┌─────────┐
│ HEALTHY │
└────┬────┘
     │ (time since last update > gap_ms)
     ▼
┌────────┐
│ STALE  │
└────┬───┘
     │ (new message arrives)
     ▼
┌─────────┐
│ HEALTHY │
└─────────┘

     OR

┌────────┐
│ STALE  │
└────┬───┘
     │ (time since last update > global_gap_ms)
     ▼
┌──────┐
│ DEAD │
└──────┘
```

### Test Coverage

- `test_feed_health.py::test_is_feed_dead_boundary`
- `test_feed_health.py::test_per_symbol_gap_override`

---

## EMA Drop Rate State Machine

### States (Implicit)

- **HEALTHY**: `ema_drop_rate < threshold`
- **DEGRADED**: `ema_drop_rate >= threshold`

### State Transitions

```
┌─────────┐
│ HEALTHY │
└────┬────┘
     │ (drops occur frequently)
     │ ema_drop_rate increases
     ▼
┌──────────┐
│ DEGRADED │◄─────┐
└────┬─────┘      │
     │            │ (more drops)
     │            │ ema_drop_rate remains high
     │            │
     └────────────┘

     OR

┌──────────┐
│ DEGRADED │
└────┬─────┘
     │ (no drops)
     │ ema_drop_rate decays
     ▼
┌─────────┐
│ HEALTHY │
└─────────┘
```

### EMA Update Rules

**On drop**:
```python
ema_drop_rate = alpha + (1 - alpha) * ema_drop_rate
```

**On no drop**:
```python
ema_drop_rate = (1 - alpha) * ema_drop_rate
```

**Warning triggered**:
```python
if ema_drop_rate > threshold:
    log.warning("Drop rate exceeded threshold")
```

---

## Next Steps

- **[Client Lifecycle](../02_transport_mqtt/client_lifecycle.md)** — Detailed MQTT lifecycle
- **[Reconnect Strategy](../02_transport_mqtt/reconnect_strategy.md)** — Reconnect implementation
- **[Queue Model](../05_dispatcher_and_backpressure/queue_model.md)** — Dispatcher internals
