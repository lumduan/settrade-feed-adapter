# Error Isolation Model

Error handling strategy and isolation boundaries.

---

## Error Isolation Principle

**Errors in one layer NEVER crash another layer.**

```
┌─────────────────────────────┐
│  Strategy Error             │ → Your responsibility
└─────────────────────────────┘

┌─────────────────────────────┐
│  Parse Error (Adapter)      │ → Log + parse_errors++
└─────────────────────────────┘

┌─────────────────────────────┐
│  Callback Error (MQTT)      │ → Log + callback_errors++
└─────────────────────────────┘

┌─────────────────────────────┐
│  Network Disconnect         │ → Auto-reconnect
└─────────────────────────────┘
```

---

## Parse Error Isolation

### Behavior

```python
try:
    msg = BidOfferV3().parse(payload)
    event = normalize(msg)
    dispatcher.push(event)
except Exception as e:
    self._stats.parse_errors += 1
    logger.error(f"Parse error for {topic}: {e}")
    # Continue (no crash, no propagation)
```

### Guarantees

✅ Parse error increments `parse_errors` **exactly once**  
✅ Error is logged with context  
✅ Adapter continues processing next message  
✅ MQTT client unaffected  

**Test**: `test_settrade_adapter.py::TestErrorHandling::test_parse_error_logged_and_counted`

---

## Callback Error Isolation

### Behavior

```python
for callback in self._callbacks[topic]:
    try:
        callback(topic, payload, recv_ts, recv_mono_ns)
    except Exception as e:
        self._stats.callback_errors += 1
        logger.error(f"Callback error: {e}")
        # Continue with next callback
```

### Guarantees

✅ Callback error increments `callback_errors` **exactly once**  
✅ Error is logged with context  
✅ Other callbacks still invoked  
✅ MQTT client unaffected  

**Test**: `test_settrade_mqtt.py::TestMessageDispatch::test_callback_isolation`

---

## Exactly One Counter Increment

**Contract**: Each error increments its counter by **exactly 1**, not 0, not 2+.

### Why This Matters

```python
# ✅ CORRECT
try:
    risky_operation()
except Exception as e:
    self._parse_errors += 1  # Exactly once
    logger.error(e)

# ❌ WRONG (double counting)
try:
    risky_operation()
except Exception as e:
    self._parse_errors += 1
    handle_error()  # ← This also increments counter!
```

**Test**: Verifies counter value **before** and **after** error.

---

## Rate-Limited Logging

To prevent log spam:

```python
# Log parse errors with rate limit
if self._parse_errors % 100 == 1:
    logger.warning(
        f"Parse errors: {self._parse_errors} "
        f"(logging every 100th error)"
    )
```

**Not yet implemented** but recommended for production.

---

## Error Types

### Recoverable Errors (Isolated)

- **Parse error**: Malformed protobuf
- **Callback error**: Exception in user code
- **Network disconnect**: Auto-reconnect handles it

**Action**: Log, count, continue

### Non-Recoverable Errors (Propagated)

- **Authentication failure**: Invalid credentials
- **Configuration error**: Invalid maxlen, negative delay
- **Resource exhaustion**: Out of memory

**Action**: Raise exception, let caller handle

---

## Monitoring Parse Errors

```python
# Check for parse errors
stats = adapter.stats()

if stats.parse_errors > 0:
    print(f"WARNING: {stats.parse_errors} parse errors detected")
    # Alert or investigate
```

**Alert if**:
- `parse_errors > 0` (should be zero normally)
- `parse_errors` increasing rapidly

**Possible causes**:
- Protocol version change
- Data corruption
- Broker sends invalid data

---

## Monitoring Callback Errors

```python
# Check for callback errors
stats = client.stats()

if stats.callback_errors > 0:
    print(f"WARNING: {stats.callback_errors} callback errors")
    # Fix strategy code
```

**Alert if**:
- `callback_errors > 0` (indicates bug in strategy)

**Possible causes**:
- Exception in strategy code
- Null pointer / attribute error
- Logic error

---

## Error Propagation Boundaries

### Layer 1: MQTT Client

**Catches**:
- Network errors (auto-reconnect)
- Callback exceptions (isolate)

**Propagates**:
- Authentication failures
- Configuration errors

---

### Layer 2: Adapter

**Catches**:
- Parse errors (isolate)
- Normalization errors (isolate)

**Propagates**:
- Configuration errors

---

### Layer 3: Dispatcher

**Catches**:
- Queue overflow (drop-oldest)

**Propagates**:
- Invalid configuration (maxlen <= 0)
- Invalid poll (max_events <= 0)

---

### Layer 4: Strategy

**Catches**:
- Your responsibility

**Propagates**:
- Your choice

---

## Implementation Reference

See [infra/settrade_adapter.py](../../infra/settrade_adapter.py):
- `_on_raw_message()` method (parse error handling)

See [infra/settrade_mqtt.py](../../infra/settrade_mqtt.py):
- `_on_message()` method (callback error handling)

---

## Test Coverage

- `test_settrade_adapter.py::TestErrorHandling::test_parse_error_logged_and_counted`
- `test_settrade_mqtt.py::TestMessageDispatch::test_callback_isolation`
- `test_settrade_mqtt.py::TestMessageDispatch::test_callback_error_increments_counter`

---

## Best Practices

1. **Log with context**:
   ```python
   logger.error(f"Parse error for {topic}: {e}", exc_info=True)
   ```

2. **Increment counter first**:
   ```python
   self._parse_errors += 1
   logger.error(...)  # ← After counter
   ```

3. **Continue processing**:
   ```python
   except Exception as e:
       # ... handle error ...
       return  # Exit callback, don't crash thread
   ```

4. **Monitor counters**:
   ```python
   # Periodic check
   if time.time() - last_check > 60:
       check_error_counters()
   ```

---

## Next Steps

- **[Parsing Pipeline](./parsing_pipeline.md)** — Protobuf parsing
- **[Normalization Contract](./normalization_contract.md)** — Data rules
- **[Failure Scenarios](../08_testing_and_guarantees/failure_scenarios.md)** — Error coverage
