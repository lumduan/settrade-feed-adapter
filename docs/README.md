# settrade-feed-adapter Documentation

Technical documentation for the settrade-feed-adapter market data ingestion
layer.

---

## Documentation Navigation

### For Newcomers

Start here to understand the system:

1. [What Is This?](./00_getting_started/what_is_this.md) -- Overview and design guarantees
2. [Quickstart Guide](./00_getting_started/quickstart.md) -- Get running in 5 minutes
3. [Mental Model](./00_getting_started/mental_model.md) -- Conceptual understanding

### For Experienced Developers

Find contracts, invariants, and edge cases:

- [Event Contract](./04_event_models/event_contract.md) -- Event model specifications
- [Normalization Contract](./03_adapter_and_normalization/normalization_contract.md) -- Data transformation rules
- [Queue Model](./05_dispatcher_and_backpressure/queue_model.md) -- Dispatcher internals
- [Reconnect Strategy](./02_transport_mqtt/reconnect_strategy.md) -- Connection recovery
- [Invariants Defined by Tests](./08_testing_and_guarantees/invariants_defined_by_tests.md) -- Design guarantees

### For Maintainers

See design guarantees backed by test coverage:

- [Testing and Guarantees](./08_testing_and_guarantees/) -- 301 test cases, all invariants
- [Concurrency Guarantees](./08_testing_and_guarantees/concurrency_guarantees.md) -- Thread safety contracts
- [Failure Scenarios](./08_testing_and_guarantees/failure_scenarios.md) -- Error handling coverage
- [Performance Targets](./07_observability/performance_targets.md) -- Benchmark methodology

---

## Directory Structure

```text
docs/
├── 00_getting_started/
│   ├── what_is_this.md
│   ├── quickstart.md
│   └── mental_model.md
│
├── 01_system_overview/
│   ├── architecture.md
│   ├── data_flow.md
│   ├── threading_and_concurrency.md
│   └── state_machines.md
│
├── 02_transport_mqtt/
│   ├── client_lifecycle.md
│   ├── authentication_and_token.md
│   ├── reconnect_strategy.md
│   └── subscription_model.md
│
├── 03_adapter_and_normalization/
│   ├── parsing_pipeline.md
│   ├── normalization_contract.md
│   ├── money_precision_model.md
│   └── error_isolation_model.md
│
├── 04_event_models/
│   ├── event_contract.md
│   ├── best_bid_ask.md
│   ├── full_bid_offer.md
│   └── timestamp_and_epoch.md
│
├── 05_dispatcher_and_backpressure/
│   ├── queue_model.md
│   ├── overflow_policy.md
│   └── health_and_ema.md
│
├── 06_feed_liveness/
│   ├── global_liveness.md
│   ├── per_symbol_liveness.md
│   └── gap_semantics.md
│
├── 07_observability/
│   ├── metrics_reference.md
│   ├── logging_policy.md
│   ├── benchmark_guide.md
│   └── performance_targets.md
│
├── 08_testing_and_guarantees/
│   ├── invariants_defined_by_tests.md
│   ├── concurrency_guarantees.md
│   └── failure_scenarios.md
│
├── 09_production_guide/
│   ├── deployment_checklist.md
│   ├── tuning_guide.md
│   └── failure_playbook.md
│
├── glossary.md
│
└── plan/                        # Original design docs (archived)
    └── low-latency-mqtt-feed-adapter/
        ├── PLAN.md
        ├── phase1-mqtt-transport.md
        ├── phase2-bidoffer-adapter.md
        ├── phase3-dispatcher.md
        ├── phase4-benchmark-validation.md
        └── phase5-feed-integrity.md
```

---

## Quick Reference

| Task | Documentation |
| --- | --- |
| Getting started | [Quickstart Guide](./00_getting_started/quickstart.md) |
| Understanding architecture | [Architecture Overview](./01_system_overview/architecture.md) |
| Event model fields | [Event Contract](./04_event_models/event_contract.md) |
| Handling reconnects | [Reconnect Strategy](./02_transport_mqtt/reconnect_strategy.md) |
| Monitoring feed health | [Global Liveness](./06_feed_liveness/global_liveness.md) |
| All available metrics | [Metrics Reference](./07_observability/metrics_reference.md) |
| Performance tuning | [Tuning Guide](./09_production_guide/tuning_guide.md) |
| Troubleshooting | [Failure Playbook](./09_production_guide/failure_playbook.md) |
| Understanding tests | [Invariants](./08_testing_and_guarantees/invariants_defined_by_tests.md) |
| Looking up a term | [Glossary](./glossary.md) |

---

## Test Coverage Summary

**Total tests:** 301 across 6 test files.

| Test File | Tests | Coverage Area |
| --- | --- | --- |
| `test_benchmark_utils.py` | 46 | Percentile, stats, payloads, config, GC, CPU, aggregation, formatting, JSON |
| `test_dispatcher.py` | 113 | Config, stats, health, init, push/poll, overflow/drops, clear, invariant, input validation, thread safety, stress, EMA |
| `test_events.py` | 48 | BidAskFlag, BestBidAsk, FullBidOffer -- frozen, extra rejected, validation, hashable, equality, coercion, auction, epoch |
| `test_feed_health.py` | 25 | Config, startup state, global liveness, per-symbol, last_seen_gap_ms, lifecycle, multiple symbols |
| `test_settrade_adapter.py` | 36 | Config, money_to_float, subscription, parsing, error isolation, rate-limited logging, stats, end-to-end |
| `test_settrade_mqtt.py` | 33 | Config, state machine, subscription, message dispatch, reconnect, token refresh, stats, generation, shutdown |

Key invariants tested:

- Dispatcher accounting invariant: `total_pushed - total_dropped - total_polled == queue_len`
- Concurrent push/poll is safe (CPython GIL)
- Generation prevents stale message dispatch
- No duplicate reconnect loops
- Shutdown is idempotent
- Parse errors isolated from callback errors
- Drop count matches evicted events exactly
- FIFO ordering preserved
- EMA decays without drops

---

## Design Principles

### Transport Reliability

Auto-reconnect with exponential backoff and jitter. Token refresh via
controlled reconnect before expiry. Generation-based stale message rejection.

### Data Correctness

Strongly-typed frozen Pydantic models (`BestBidAsk`, `FullBidOffer`). Direct
protobuf field access (no `.to_dict()`). Inline Money conversion
(`units + nanos * 1e-9`, no `Decimal`). Event construction via
`model_construct()` in the hot path.

### Delivery Control

Bounded `deque(maxlen)` queue with drop-oldest backpressure. EMA-smoothed
drop rate tracking with configurable warning threshold.

### Error Isolation

Parse errors and callback errors tracked in separate counters. Rate-limited
logging prevents log storms (first 10 with stack trace, then every 1000th).
No error propagation across layers.

### Observability

All metrics exposed via `stats()` and `health()` methods. Python stdlib
logging only. No external monitoring dependencies.

---

## Source Code Layout

| Path | Description |
| --- | --- |
| `core/events.py` | `BestBidAsk`, `FullBidOffer`, `BidAskFlag` event models |
| `core/dispatcher.py` | Bounded event queue with drop-oldest policy and EMA |
| `core/feed_health.py` | `FeedHealthMonitor` with global and per-symbol liveness |
| `infra/settrade_mqtt.py` | `SettradeMQTTClient` MQTT transport with auto-reconnect |
| `infra/settrade_adapter.py` | `BidOfferAdapter` protobuf parser and normalizer |
| `scripts/benchmark_utils.py` | Benchmark infrastructure (payloads, percentiles, aggregation) |
| `scripts/benchmark_adapter.py` | Adapter benchmark script |
| `scripts/benchmark_compare.py` | SDK vs adapter comparison script |
| `scripts/benchmark_parallel.py` | Parallel benchmark script |
| `tests/` | 301 tests across 6 files |

---

## Running Tests

```bash
uv run pytest tests -v
```

---

## Related Resources

- [Glossary](./glossary.md) -- terminology reference
- [Original Design Plans](./plan/low-latency-mqtt-feed-adapter/PLAN.md) -- archived design documents
