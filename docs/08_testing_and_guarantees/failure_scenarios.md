# Failure Scenarios

Comprehensive error handling coverage.

---

## Overview

This document catalogs all failure scenarios covered by tests and explains the error isolation strategy.

---

## Transport Layer Failures

### 1. Network Disconnect

**Scenario**: MQTT connection drops unexpectedly.

**Behavior**:
- `on_disconnect()` callback triggered
- State transitions to `RECONNECTING`
- Generation counter incremented
- Reconnect loop spawned with exponential backoff

**Test Coverage**:
- `test_settrade_mqtt.py::TestReconnect::test_on_disconnect_unexpected_triggers_reconnect`

**Recovery**: Automatic reconnect, connection epoch incremented on success.

---

### 2. Authentication Failure

**Scenario**: Invalid credentials or token expired.

**Behavior**:
- `login()` raises exception
- Exception propagated to caller
- State remains `INIT`

**Test Coverage**:
- `test_settrade_mqtt.py::TestTokenRefresh::test_fetch_host_token_raises_without_login`

**Recovery**: Manual - fix credentials and retry `connect()`.

---

### 3. Connection Timeout

**Scenario**: Broker unreachable or network issue.

**Behavior**:
- `connect()` times out (paho-mqtt default: 60s)
- Exception raised
- State remains `CONNECTING`

**Recovery**: Retry `connect()` or check network connectivity.

---

### 4. Reconnect Storm

**Scenario**: Rapid disconnect/reconnect cycles.

**Behavior**:
- Exponential backoff prevents tight loop
- `reconnect_min_delay` → `reconnect_max_delay` (e.g., 1s → 16s)
- Only one reconnect thread runs at a time

**Test Coverage**:
- `test_settrade_mqtt.py::TestReconnect::test_schedule_reconnect_prevents_duplicates`

**Recovery**: Automatic, backoff prevents overwhelming broker.

---

### 5. Token Expiration

**Scenario**: Token expires during connection.

**Behavior**:
- Proactive disconnect before expiration (`token_refresh_before_exp_seconds`)
- Fetch new token
- Reconnect with new credentials

**Test Coverage**:
- `test_settrade_mqtt.py::TestTokenRefresh::test_token_refresh_triggers_reconnect`

**Recovery**: Automatic token refresh.

---

## Adapter Layer Failures

### 6. Parse Error

**Scenario**: Malformed protobuf payload.

**Behavior**:
- `BidOfferV3().parse(payload)` raises exception
- Exception caught in adapter
- `parse_errors` counter incremented
- Error logged
- Continue processing (no crash)

**Test Coverage**:
- `test_settrade_adapter.py::TestErrorHandling::test_parse_error_logged_and_counted`

**Recovery**: Automatic - skip bad message, continue.

---

### 7. Invalid Money Field

**Scenario**: Money field has invalid `units` or `nanos`.

**Behavior**:
- Conversion to float may produce `inf` or `nan`
- Pydantic validation rejects invalid float
- Treated as parse error

**Recovery**: Automatic - skip bad message, log error.

---

### 8. Missing Required Field

**Scenario**: Protobuf message missing required field.

**Behavior**:
- betterproto returns default value (0, empty string, etc.)
- Event constructed with default value
- **No error** (proto3 semantics)

**Note**: proto3 has no concept of "required" fields.

---

## Dispatcher Layer Failures

### 9. Queue Overflow

**Scenario**: Events arrive faster than strategy can consume.

**Behavior**:
- Queue reaches `maxlen`
- `deque.append()` automatically drops oldest
- `total_dropped` counter incremented
- EMA drop rate updated
- Warning logged if threshold exceeded

**Test Coverage**:
- `test_dispatcher.py::TestOverflowDrops::test_drop_at_maxlen_boundary`
- `test_dispatcher.py::TestOverflowDrops::test_drop_count_matches_evicted_events`

**Recovery**: Increase polling frequency, optimize processing,or increase maxlen.

---

### 10. poll() Called with Zero Events

**Scenario**: `dispatcher.poll(max_events=0)`.

**Behavior**:
- Pydantic validation raises `ValidationError`
- Exception propagated to caller

**Test Coverage**:
- `test_dispatcher.py::TestInputValidation::test_poll_zero_raises_value_error`

**Recovery**: Fix caller code.

---

## Strategy Layer Failures

### 11. Callback Exception

**Scenario**: User callback raises exception.

**Behavior**:
- Exception caught in MQTT client
- `callback_errors` counter incremented
- Error logged
- Continue processing other callbacks

**Test Coverage**:
- `test_settrade_mqtt.py::TestMessageDispatch::test_callback_isolation`
- `test_settrade_mqtt.py::TestMessageDispatch::test_callback_error_increments_counter`

**Recovery**: Automatic - fix callback code, errors isolated.

---

### 12. Processing Too Slow

**Scenario**: Strategy processing slower than event arrival rate.

**Behavior**:
- Queue fills up
- Drop-oldest policy activated
- `total_dropped` increments

**Indicators**:
- `dispatcher.stats().total_dropped > 0`
- `dispatcher.stats().queue_len == maxlen`
- EMA drop rate rising

**Recovery**: Optimize processing, increase polling frequency, or offload to worker threads.

---

## Feed Health Failures

### 13. Feed Silence

**Scenario**: No messages received for > `gap_ms`.

**Behavior**:
- `is_feed_dead()` returns `True`
- Strategy can detect and alert

**Test Coverage**:
- `test_feed_health.py::TestGlobalLiveness::test_is_feed_dead_boundary`

**Recovery**: Check broker connection, verify subscriptions.

---

### 14. Symbol Stale

**Scenario**: Specific symbol has no updates for > `per_symbol_gap`.

**Behavior**:
- `stale_symbols()` returns list including that symbol
- Strategy can detect per-symbol staleness

**Test Coverage**:
- `test_feed_health.py::TestPerSymbolTracking::test_per_symbol_gap_override`

**Recovery**: Check symbol subscription, verify market hours.

---

## Configuration Failures

### 15. Invalid maxlen

**Scenario**: `maxlen <= 0`.

**Behavior**:
- Pydantic validation raises `ValidationError`
- Construction fails

**Test Coverage**:
- `test_dispatcher.py::TestDispatcherConfig::test_maxlen_zero_rejected`
- `test_dispatcher.py::TestDispatcherConfig::test_maxlen_negative_rejected`

**Recovery**: Fix configuration.

---

### 16. Invalid Reconnect Delays

**Scenario**: `reconnect_min_delay > reconnect_max_delay`.

**Behavior**:
- Pydantic validation raises `ValidationError`
- Construction fails

**Test Coverage**:
- `test_settrade_mqtt.py::TestMQTTClientConfig::test_reconnect_min_delay_constraint`

**Recovery**: Fix configuration.

---

## State Machine Violations

### 17. connect() Called After connect()

**Scenario**: `client.connect()` called twice.

**Behavior**:
- Validation rejects (state != INIT)
- Raises `ValueError`

**Test Coverage**:
- `test_settrade_mqtt.py::TestStateMachine::test_connect_rejects_non_init_state`

**Recovery**: Check state before calling `connect()`.

---

### 18. Reconnect After Shutdown

**Scenario**: Network disconnect after `shutdown()`.

**Behavior**:
- `on_disconnect()` checks state
- If state == SHUTDOWN, no reconnect

**Test Coverage**:
- `test_settrade_mqtt.py::TestReconnect::test_schedule_reconnect_blocked_after_shutdown`

**Recovery**: None - shutdown is terminal.

---

## Error Isolation Strategy

### Layer Boundaries

```
┌─────────────────────────────┐
│  Strategy Error             │ → Your responsibility
└─────────────────────────────┘

┌─────────────────────────────┐
│  Dispatcher Overflow        │ → Drop oldest + counter++
└─────────────────────────────┘

┌─────────────────────────────┐
│  Parse Error (Adapter)      │ → Log + parse_errors++
└─────────────────────────────┘

┌─────────────────────────────┐
│  Callback Error (MQTT)      │ → Log + callback_errors++
└─────────────────────────────┘

┌─────────────────────────────┐
│  Network Disconnect         │ → Auto-reconnect loop
└─────────────────────────────┘
```

**Key Principle**: Errors **never propagate** across layer boundaries.

---

## Monitoring Recommendations

### Counters to Watch

1. **`parse_errors`**: Should be zero. Non-zero indicates protocol changes or corruption.
2. **`callback_errors`**: Indicates bugs in strategy code.
3. **`total_dropped`**: Indicates backpressure. Strategy too slow.
4. **`reconnect_count`**: High value indicates network instability.

### Alerts to Configure

- **Parse errors > 0**: Protocol issue or corruption
- **Drop rate EMA > threshold**: Backpressure
- **Feed dead**: No messages for > gap_ms
- **Reconnect storm**: > 5 reconnects in 1 minute

---

## Next Steps

- **[Invariants Defined by Tests](./invariants_defined_by_tests.md)** — Design guarantees
- **[Failure Playbook](../09_production_guide/failure_playbook.md)** — Troubleshooting guide
- **[Metrics Reference](../07_observability/metrics_reference.md)** — All metrics documented
