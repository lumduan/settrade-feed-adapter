# Data Flow

End-to-end trace of a market data message through the pipeline.

---

## Complete Message Flow

Let's trace a single BidOfferV3 message from the broker to your strategy.

---

## Step 1: Broker Sends Message

```
Settrade Open API Broker
  ↓
Publishes to: proto/topic/bidofferv3/AOT
Format: BidOfferV3 protobuf (binary)
Payload size: ~500-1000 bytes (full 10-level book)
```

---

## Step 2: SettradeMQTTClient Receives

```python
# Inside paho-mqtt IO thread
def _on_message(
    client: mqtt.Client,
    userdata: Any,
    msg: mqtt.MQTTMessage,
) -> None:
    # Capture timestamps IMMEDIATELY
    recv_ts: int = time.time_ns()           # Wall clock
    recv_mono_ns: int = time.perf_counter_ns()  # Monotonic
    
    topic: str = msg.topic
    payload: bytes = msg.payload
    
    # Increment counter
    self._stats.messages_received += 1
    
    # Check generation (stale message rejection)
    if self._generation != current_generation:
        logger.warning("Stale message rejected")
        return
    
    # Dispatch to registered callbacks
    for callback in self._callbacks[topic]:
        try:
            callback(topic, payload, recv_ts, recv_mono_ns)
        except Exception as e:
            self._stats.callback_errors += 1
            logger.error(f"Callback error: {e}")
```

**Key Points**:
- Timestamps captured **first** (minimize latency)
- Generation check prevents stale messages after reconnect
- Errors isolated (callback exception doesn't crash MQTT client)

---

## Step 3: BidOfferAdapter Parses

```python
def _on_raw_message(
    topic: str,
    payload: bytes,
    recv_ts: int,
    recv_mono_ns: int,
) -> None:
    try:
        # 1. Parse protobuf
        msg: BidOfferV3 = BidOfferV3().parse(payload)
        
        # 2. Extract symbol
        symbol: str = msg.symbol.upper()  # Normalize to uppercase
        
        # 3. Convert Money to float (hot path)
        bid: float = msg.bid_price1.units + msg.bid_price1.nanos * 1e-9
        ask: float = msg.ask_price1.units + msg.ask_price1.nanos * 1e-9
        bid_vol: int = msg.bid_volume1
        ask_vol: int = msg.ask_volume1
        bid_flag: int = int(msg.bid_flag)
        ask_flag: int = int(msg.ask_flag)
        
        # 4. Construct event (skip validation for speed)
        event = BestBidAsk.model_construct(
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_vol=bid_vol,
            ask_vol=ask_vol,
            bid_flag=bid_flag,
            ask_flag=ask_flag,
            recv_ts=recv_ts,
            recv_mono_ns=recv_mono_ns,
            connection_epoch=self._mqtt_client.connection_epoch,
        )
        
        # 5. Push to dispatcher
        self._dispatcher.push(event)
        self._stats.messages_parsed += 1
        
    except Exception as e:
        self._stats.parse_errors += 1
        logger.error(f"Parse error for {topic}: {e}")
```

**Key Points**:
- Direct field access (no `.to_dict()`)
- `model_construct()` skips Pydantic validation (hot path optimization)
- Parse errors isolated (increment counter, continue)
- Connection epoch stamped on every event

---

## Step 4: Dispatcher Pushes

```python
def push(self, event: T) -> None:
    # Check for overflow BEFORE append
    will_drop: bool = len(self._queue) == self._config.maxlen
    
    # Atomic append (CPython GIL guarantee)
    self._queue.append(event)
    
    # Update counters
    self._total_pushed += 1
    
    if will_drop:
        self._total_dropped += 1
        
        # Update EMA drop rate
        self._ema_drop_rate = (
            self._config.ema_alpha
            + (1.0 - self._config.ema_alpha) * self._ema_drop_rate
        )
        
        # Log warning if threshold exceeded
        if self._ema_drop_rate > self._config.drop_warning_threshold:
            logger.warning(
                f"Drop rate EMA exceeded threshold: "
                f"{self._ema_drop_rate:.4f} > {self._config.drop_warning_threshold}"
            )
    else:
        # Decay EMA when no drop
        self._ema_drop_rate *= (1.0 - self._config.ema_alpha)
```

**Key Points**:
- Drop detection **before** append (pre-check length)
- EMA drop rate updated on every push
- Drop count is **exact** (not sampled)
- Lock-free (relies on GIL)

---

## Step 5: Strategy Polls

```python
# In your strategy thread (main thread or dedicated thread)
events: list[BestBidAsk] = dispatcher.poll(max_events=100)

# Update counter
self._total_polled += len(events)

# Return events
return events
```

**Implementation**:
```python
def poll(self, max_events: int = 100) -> list[T]:
    result: list[T] = []
    
    for _ in range(min(max_events, len(self._queue))):
        try:
            event = self._queue.popleft()
            result.append(event)
        except IndexError:
            # Queue empty (race condition)
            break
    
    self._total_polled += len(result)
    return result
```

**Key Points**:
- Batch polling (reduce overhead)
- Lock-free popleft()
- Race-safe (handles queue empty gracefully)

---

## Step 6: Strategy Processes

```python
for event in events:
    # Check for reconnect
    if event.connection_epoch != last_epoch:
        logger.info(f"Reconnect detected: epoch {event.connection_epoch}")
        clear_state()
        last_epoch = event.connection_epoch
    
    # Measure latency
    now_ns = time.time_ns()
    latency_us = (now_ns - event.recv_ts) / 1_000
    
    # Check for auction period
    if event.is_auction():
        logger.debug(f"{event.symbol} in auction")
        continue
    
    # Process event
    handle_best_bid_ask(event)
```

---

## Timeline Example

```
t=0ns       Broker sends message
            ↓
t=100us     Message arrives at client (network latency)
            ↓
t=101us     MQTT callback triggered
            recv_ts = time.time_ns()
            recv_mono_ns = time.perf_counter_ns()
            ↓
t=105us     Protobuf parsing starts
            ↓
t=110us     Parsing complete
            ↓
t=112us     BestBidAsk.model_construct() called
            ↓
t=115us     Event constructed
            ↓
t=116us     dispatcher.push(event)
            ↓
t=117us     Event in queue
            
            [Event sits in queue...]
            
t=500us     Strategy calls dispatcher.poll()
            ↓
t=502us     Event returned to strategy
            ↓
t=505us     Strategy processes event
```

**Measured Latencies**:
- **Parse + normalize**: `t=115us - t=105us = 10us`
- **Queue wait**: `t=500us - t=117us = 383us`
- **End-to-end**: `t=505us - t=101us = 404us`

**Key Insight**: Queue wait time dominates end-to-end latency. Adjust polling frequency to reduce wait time.

---

## Backpressure Flow

### Normal Flow (Queue Not Full)

```
Push → Append → Success
Queue: [E1][E2][E3]...[EN] (len < maxlen)
```

### Overflow Flow (Queue Full)

```
Push → Check length (== maxlen) → will_drop = True
       ↓
    Append (deque auto-drops oldest)
       ↓
    [E1] dropped
       ↓
    _total_dropped++
       ↓
    EMA update
       ↓
    Log warning (if threshold exceeded)

Queue: [E2][E3]...[EN][NEW] (len == maxlen)
```

---

## Error Propagation

### Parse Error

```
payload → BidOfferV3().parse(payload) → RAISES
                                          ↓
                                      Exception caught
                                          ↓
                                      parse_errors++
                                          ↓
                                      Log error
                                          ↓
                                      Continue (no crash)
```

### Callback Error (Strategy Error)

```
event → your_callback(event) → RAISES
                                 ↓
                            Exception caught
                                 ↓
                            callback_errors++
                                 ↓
                            Log error
                                 ↓
                            Continue (no crash)
```

**Guarantee**: Errors **never propagate** across layer boundaries.

---

## Reconnect Flow

```
[Normal Operation]
  CONNECTED
  messages_received++
        ↓
  (Network disconnect)
        ↓
  on_disconnect() → state = RECONNECTING
        ↓
  generation++  (invalidate in-flight messages)
        ↓
  _reconnect_loop():
    while state == RECONNECTING:
      wait(backoff_delay)
      try:
        reconnect()
        ↓
      on_connect() → state = CONNECTED
                  → connection_epoch++
                  → resubscribe to all topics
  
[Resume Normal Operation]
  CONNECTED
  messages_received++
  (all new events have connection_epoch=N+1)
```

**Key**: Events from old connection are rejected via generation check.

---

## Next Steps

- **[Threading and Concurrency](./threading_and_concurrency.md)** — Concurrency guarantees
- **[State Machines](./state_machines.md)** — State transition diagrams
- **[Parsing Pipeline](../03_adapter_and_normalization/parsing_pipeline.md)** — Protobuf parsing details
