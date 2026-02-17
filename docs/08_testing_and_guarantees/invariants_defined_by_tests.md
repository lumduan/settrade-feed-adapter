# Invariants Defined by Tests

System invariants backed by comprehensive test coverage (301 tests).

---

## Overview

This document lists all design guarantees (invariants) that are enforced by the test suite. Each invariant is backed by specific test cases.

---

## Dispatcher Invariants

### 1. Queue Invariant Always Holds

**Invariant**:
```python
total_pushed - total_dropped - total_polled == queue_len
```

**Test Coverage**:
- `test_dispatcher.py::TestInvariant::test_invariant_after_push`
- `test_dispatcher.py::TestInvariant::test_invariant_after_poll`
- `test_dispatcher.py::TestInvariant::test_invariant_after_overflow`
- `test_dispatcher.py::TestInvariant::test_invariant_after_mixed_operations`

**Guarantee**: Under quiescent conditions (no concurrent operations), this invariant holds **exactly**. During concurrent operations, it holds **eventually** (lock-free reads).

---

### 2. FIFO Ordering Preserved

**Invariant**: Events are polled in the same order they were pushed.

**Test Coverage**:
- `test_dispatcher.py::TestPushPoll::test_fifo_ordering`

**Guarantee**: First In, First Out (FIFO) order is strictly maintained.

---

### 3. Drop Count is Exact

**Invariant**: `total_dropped` exactly equals the number of events evicted due to queue overflow.

**Test Coverage**:
- `test_dispatcher.py::TestOverflowDrops::test_drop_count_matches_evicted_events`
- `test_dispatcher.py::TestOverflowDrops::test_multiple_sequential_drops`

**Guarantee**: Not sampled or approximate — exactly correct.

---

### 4. Drop-Oldest Policy Enforced

**Invariant**: When queue is full, the oldest event is evicted.

**Test Coverage**:
- `test_dispatcher.py::TestOverflowDrops::test_drop_oldest_evicted`
- `test_dispatcher.py::TestOverflowDrops::test_drop_at_maxlen_boundary`

**Guarantee**: Newest data always wins.

---

### 5. maxlen ≥ 1

**Invariant**: Queue maxlen must be at least 1.

**Test Coverage**:
- `test_dispatcher.py::TestDispatcherConfig::test_maxlen_zero_rejected`
- `test_dispatcher.py::TestDispatcherConfig::test_maxlen_negative_rejected`

**Guarantee**: Configuration validation prevents invalid values.

---

### 6. poll(max_events) ≥ 1

**Invariant**: Cannot poll zero or negative events.

**Test Coverage**:
- `test_dispatcher.py::TestInputValidation::test_poll_zero_raises_value_error`
- `test_dispatcher.py::TestInputValidation::test_poll_negative_raises_value_error`

**Guarantee**: API contract enforced at Pydantic level.

---

## MQTT Client Invariants

### 7. No Duplicate Reconnect Loops

**Invariant**: Only one reconnect thread runs at a time.

**Test Coverage**:
- `test_settrade_mqtt.py::TestReconnect::test_schedule_reconnect_prevents_duplicates`

**Guarantee**: State check before spawning thread.

---

### 8. Reconnect Blocked After Shutdown

**Invariant**: After `shutdown()`, no reconnect attempts occur.

**Test Coverage**:
- `test_settrade_mqtt.py::TestReconnect::test_schedule_reconnect_blocked_after_shutdown`
- `test_settrade_mqtt.py::TestReconnect::test_on_disconnect_ignored_after_shutdown`

**Guarantee**: Terminal state enforcement.

---

### 9. Shutdown is Idempotent

**Invariant**: Multiple `shutdown()` calls are safe.

**Test Coverage**:
- `test_settrade_mqtt.py::TestStateMachine::test_shutdown_is_idempotent`
- `test_settrade_mqtt.py::TestShutdown::test_shutdown_sets_event`

**Guarantee**: State check prevents double-shutdown.

---

### 10. Generation Prevents Stale Messages

**Invariant**: Messages from old connections are never dispatched after reconnect.

**Test Coverage**:
- `test_settrade_mqtt.py::TestMessageDispatch::test_stale_generation_rejected`

**Guarantee**: Generation counter incremented on reconnect, checked on dispatch.

---

### 11. Connection Epoch Increments on Reconnect

**Invariant**: `connection_epoch` increments by exactly 1 on each reconnect.

**Test Coverage**:
- `test_settrade_mqtt.py::TestStateMachine::test_on_connect_success_transitions_to_connected`

**Guarantee**: Event stamping allows reconnect detection.

---

## Event Model Invariants

### 12. Events are Immutable

**Invariant**: All event fields are frozen (`frozen=True`).

**Test Coverage**:
- `test_events.py::TestBestBidAsk::test_frozen_immutability`
- `test_events.py::TestFullBidOffer::test_frozen_immutability`

**Guarantee**: Pydantic field assignment raises `ValidationError`.

---

### 13. Events are Hashable

**Invariant**: All events can be used as dict keys or in sets.

**Test Coverage**:
- `test_events.py::TestBestBidAsk::test_hashable`
- `test_events.py::TestFullBidOffer::test_hashable`

**Guarantee**: `__hash__` implemented correctly.

---

### 14. Events are Equatable

**Invariant**: Events with identical fields compare equal.

**Test Coverage**:
- `test_events.py::TestBestBidAsk::test_equality`
- `test_events.py::TestFullBidOffer::test_equality`

**Guarantee**: `__eq__` implemented correctly.

---

### 15. Extra Fields Rejected

**Invariant**: Cannot add unknown fields to events.

**Test Coverage**:
- `test_events.py::TestBestBidAsk::test_extra_fields_rejected`
- `test_events.py::TestFullBidOffer::test_extra_fields_rejected`

**Guarantee**: Pydantic `extra='forbid'` enforcement.

---

### 16. recv_mono_ns Must Be Non-Negative

**Invariant**: Monotonic timestamp cannot be negative.

**Test Coverage**:
- `test_events.py::TestBestBidAsk::test_recv_mono_ns_negative_rejected`
- `test_events.py::TestFullBidOffer::test_recv_mono_ns_negative_rejected`

**Guarantee**: Field validation enforced.

---

## Adapter Invariants

### 17. Parse Errors Isolated

**Invariant**: Parse error does not crash adapter or MQTT client.

**Test Coverage**:
- `test_settrade_adapter.py::TestErrorHandling::test_parse_error_logged_and_counted`

**Guarantee**: Exception caught, counter incremented, continue.

---

### 18. Exactly One Counter Increment Per Error

**Invariant**: Each parse error increments `parse_errors` by exactly 1.

**Test Coverage**:
- `test_settrade_adapter.py::TestErrorHandling::test_parse_error_logged_and_counted`

**Guarantee**: Counter incremented inside exception handler.

---

### 19. Symbol Normalized to Uppercase

**Invariant**: All symbols are converted to uppercase.

**Test Coverage**:
- `test_settrade_adapter.py::TestNormalization::test_symbol_uppercase_normalization`

**Guarantee**: `.upper()` called on every symbol.

---

### 20. Negative Prices Allowed

**Invariant**: Negative prices are not rejected (can occur in edge cases).

**Test Coverage**:
- `test_settrade_adapter.py::TestNormalization::test_negative_price_supported`

**Guarantee**: No validation constraint on price sign.

---

### 21. Zero Price Allowed in Auction

**Invariant**: Price = 0 is valid during ATO/ATC.

**Test Coverage**:
- `test_settrade_adapter.py::TestNormalization::test_zero_price_in_auction`

**Guarantee**: Auction semantics honored.

---

## Feed Health Invariants

### 22. Negative Delta Clamped to Zero

**Invariant**: Time delta cannot be negative (monotonic logic).

**Test Coverage**:
- `test_feed_health.py::TestGlobalLiveness::test_negative_delta_handled_gracefully`

**Guarantee**: `max(0, delta)` logic.

---

### 23. Feed Death Boundary Behavior

**Invariant**: Feed declared dead when `time_since_last_update > gap_ms`.

**Test Coverage**:
- `test_feed_health.py::TestGlobalLiveness::test_is_feed_dead_boundary`

**Guarantee**: Exact boundary condition `>` (not `>=`).

---

### 24. Per-Symbol Gap Override

**Invariant**: Symbol-specific gap threshold overrides global threshold.

**Test Coverage**:
- `test_feed_health.py::TestPerSymbolTracking::test_per_symbol_gap_override`

**Guarantee**: Dictionary lookup with fallback.

---

### 25. Purge Removes Symbol

**Invariant**: Purged symbol no longer tracked.

**Test Coverage**:
- `test_feed_health.py::TestPerSymbolTracking::test_purge_symbol`

**Guarantee**: Dictionary key deletion.

---

### 26. Reset Clears All State

**Invariant**: `reset()` removes all symbol tracking.

**Test Coverage**:
- `test_feed_health.py::TestGlobalLiveness::test_reset_clears_last_update`

**Guarantee**: Dictionary cleared, global timestamp reset.

---

## Concurrency Invariants

### 27. Concurrent Push/Poll is Safe

**Invariant**: No data corruption when push and poll happen concurrently.

**Test Coverage**:
- `test_dispatcher.py::TestConcurrency::test_concurrent_push_poll`

**Guarantee**: CPython GIL makes `deque.append()` and `deque.popleft()` atomic.

---

### 28. Callback Errors Isolated

**Invariant**: Callback exception does not crash MQTT client.

**Test Coverage**:
- `test_settrade_mqtt.py::TestMessageDispatch::test_callback_isolation`
- `test_settrade_mqtt.py::TestMessageDispatch::test_callback_error_increments_counter`

**Guarantee**: Exception caught, counter incremented, continue.

---

## Benchmark Invariants

### 29. Payloads Vary (No CPU Cache Bias)

**Invariant**: Synthetic payloads have per-message variation.

**Test Coverage**:
- `test_benchmark_utils.py::TestBuildSyntheticPayloads::test_payloads_vary`

**Guarantee**: Price values cycle through 5 patterns.

---

### 30. Percentile Calculation Matches Known Distribution

**Invariant**: Percentile calculation produces correct results.

**Test Coverage**:
- `test_benchmark_utils.py::TestCalculatePercentile::test_matches_known_distribution`

**Guarantee**: Linear interpolation algorithm verified.

---

## Summary

**Total Invariants**: 30+  
**Total Test Cases**: 301  
**All Invariants Hold**: ✅

---

## Next Steps

- **[Concurrency Guarantees](./concurrency_guarantees.md)** — Thread safety details
- **[Failure Scenarios](./failure_scenarios.md)** — Error handling coverage
- **[Performance Targets](../07_observability/performance_targets.md)** — Benchmark expectations
