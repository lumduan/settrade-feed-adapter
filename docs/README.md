# settrade-feed-adapter Documentation

> **📚 Comprehensive technical documentation for the settrade-feed-adapter market data ingestion layer**

---

## 📖 Documentation Navigation

### 🟢 For Newcomers (15-Minute Read)

Start here to understand the system:

1. **[What Is This?](./00_getting_started/what_is_this.md)** — Overview and design guarantees
2. **[Quickstart Guide](./00_getting_started/quickstart.md)** — Get running in 5 minutes
3. **[Mental Model](./00_getting_started/mental_model.md)** — Conceptual understanding

### 🔵 For Experienced Developers

Find contracts, invariants, and edge cases:

- **[Event Contract](./04_event_models/event_contract.md)** — Event model specifications
- **[Normalization Contract](./03_adapter_and_normalization/normalization_contract.md)** — Data transformation rules
- **[Queue Model](./05_dispatcher_and_backpressure/queue_model.md)** — Dispatcher internals
- **[Reconnect Strategy](./02_transport_mqtt/reconnect_strategy.md)** — Connection recovery
- **[Invariants Defined by Tests](./08_testing_and_guarantees/invariants_defined_by_tests.md)** — Design guarantees

### 🔴 For Maintainers

See design guarantees backed by test coverage:

- **[Testing and Guarantees](./08_testing_and_guarantees/)** — 301 test cases, all invariants
- **[Concurrency Guarantees](./08_testing_and_guarantees/concurrency_guarantees.md)** — Thread safety contracts
- **[Failure Scenarios](./08_testing_and_guarantees/failure_scenarios.md)** — Error handling coverage
- **[Performance Targets](./07_observability/performance_targets.md)** — Benchmark methodology

---

## 📂 Documentation Structure

```
docs/
├── 00_getting_started/          # New user onboarding
│   ├── what_is_this.md          # Overview (10 min read)
│   ├── quickstart.md            # Get running (5 min)
│   └── mental_model.md          # Conceptual understanding (15 min)
│
├── 01_system_overview/          # Architecture deep dive
│   ├── architecture.md          # Component-level design
│   ├── data_flow.md             # End-to-end message trace
│   ├── threading_and_concurrency.md  # Concurrency model
│   └── state_machines.md        # State transition diagrams
│
├── 02_transport_mqtt/           # Phase 1: Transport layer
│   ├── client_lifecycle.md      # Connection state machine
│   ├── authentication_and_token.md  # Auth flow
│   ├── reconnect_strategy.md    # Auto-reconnect logic
│   └── subscription_model.md    # Topic subscription
│
├── 03_adapter_and_normalization/  # Phase 2: Parsing
│   ├── parsing_pipeline.md      #Protobuf → Event flow
│   ├── normalization_contract.md  # Data transformation rules
│   ├── money_precision_model.md   # Float precision contract
│   └── error_isolation_model.md   # Error handling
│
├── 04_event_models/             # Event contracts
│   ├── event_contract.md        # Model specifications
│   ├── best_bid_ask.md          # BestBidAsk fields
│   ├── full_bid_offer.md        # FullBidOffer fields
│   └── timestamp_and_epoch.md   # Timestamp semantics
│
├── 05_dispatcher_and_backpressure/  # Phase 3: Queuing
│   ├── queue_model.md           # Deque internals
│   ├── overflow_policy.md       # Drop-oldest strategy
│   └── health_and_ema.md        # EMA drop rate monitoring
│
├── 06_feed_liveness/            # Phase 5: Health monitoring
│   ├── global_liveness.md       # Feed death detection
│   ├── per_symbol_liveness.md   # Per-symbol staleness
│   └── gap_semantics.md         # Gap threshold behavior
│
├── 07_observability/            # Metrics and monitoring
│   ├── metrics_reference.md     # All metrics documented
│   ├── logging_policy.md        # Logging standards
│   ├── benchmark_guide.md       # Benchmark methodology
│   └── performance_targets.md   # Expected performance
│
├── 08_testing_and_guarantees/   # Test-backed contracts
│   ├── invariants_defined_by_tests.md  # Design guarantees
│   ├── concurrency_guarantees.md       # Thread safety
│   └── failure_scenarios.md            # Error cases covered
│
├── 09_production_guide/         # Deployment and operations
│   ├── deployment_checklist.md  # Pre-launch checklist
│   ├── tuning_guide.md          # Configuration tuning
│   └── failure_playbook.md      # Troubleshooting guide
│
├── glossary.md                  # Terminology reference
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

## 🎯 Quick Reference

### Common Tasks

| Task | Documentation |
|------|---------------|
| **Getting Started** | [Quickstart Guide](./00_getting_started/quickstart.md) |
| **Understanding Architecture** | [Architecture Overview](./01_system_overview/architecture.md) |
| **Event Models** | [Event Contract](./04_event_models/event_contract.md) |
| **Handling Reconnects** | [Reconnect Strategy](./02_transport_mqtt/reconnect_strategy.md) |
| **Monitoring Feed Health** | [Feed Liveness](./06_feed_liveness/global_liveness.md) |
| **Performance Tuning** | [Tuning Guide](./09_production_guide/tuning_guide.md) |
| **Troubleshooting** | [Failure Playbook](./09_production_guide/failure_playbook.md) |
| **Understanding Tests** | [Invariants](./08_testing_and_guarantees/invariants_defined_by_tests.md) |

---

## 📊 Test Coverage

- **Total Test Cases**: 301
- **Total Test Code**: 4,684 lines
- **Test Files**: 6
- **Coverage**: All critical paths and edge cases

### Test Files

1. `test_benchmark_utils.py` — Benchmark infrastructure (45 tests)
2. `test_dispatcher.py` — Phase 3 dispatcher (99 tests)
3. `test_events.py` — Event models (48 tests)
4. `test_feed_health.py` — Phase 5 feed monitoring (25 tests)
5. `test_settrade_adapter.py` — Phase 2 adapter (36 tests)
6. `test_settrade_mqtt.py` — Phase 1 transport (48 tests)

### Key Invariants Tested

- ✅ Dispatcher invariant always holds
- ✅ Concurrent push/poll is safe
- ✅ Generation prevents stale messages
- ✅ No duplicate reconnect loops
- ✅ Reconnect blocked after shutdown
- ✅ Shutdown is idempotent
- ✅ Parse errors isolated
- ✅ Callback errors isolated
- ✅ Drop count is exact
- ✅ FIFO ordering preserved

---

## 🚀 Design Principles

### 1. Transport Reliability
- Auto-reconnect with exponential backoff
- Token refresh before expiration
- Generation-based stale message rejection

### 2. Data Correctness
- Strongly-typed Pydantic models
- Direct protobuf access (no `.to_dict()`)
- Comprehensive input validation

### 3. Delivery Control
- Bounded queue with explicit backpressure
- Drop-oldest policy (stale data is worthless)
- Visible overflow metrics

### 4. Error Isolation
- Errors contained within layers
- Comprehensive error counting
- No error propagation across layers

### 5. Observability
- Zero external dependencies
- Comprehensive metrics
- Lock-free stats reads

---

## 📈 Performance Characteristics

**Realistic Performance**: ~1.1-1.3x faster than SDK (parse + normalize only)

**Primary Value**: Architectural control and deterministic event flow, not raw speed

### Measured Latencies (Typical)

| Operation | P50 | P95 | P99 |
|-----------|-----|-----|-----|
| Parse + Normalize | ~10-15µs | ~20-30µs | ~40-60µs |
| Queue Wait | Varies | Varies | Varies |
| End-to-End | ~50-500µs | ~0.5-2ms | ~1-5ms |

*Note: Actual latencies depend on CPU, system load, Python version, and polling frequency.*

---

## 🔗 External Resources

- **Settrade Open API Docs**: https://developer.settrade.com/open-api/
- **Project Repository**: https://github.com/lumduan/settrade-feed-adapter
- **Original README**: ../README.md (root-level)

---

## 🛠️ Maintenance

### Documentation Updates

When updating documentation:
1. Follow the existing structure
2. Update cross-references if files are renamed
3. Run all tests to ensure accuracy: `uv run pytest tests -v`
4. Update this README if new sections are added

### Adding New Sections

To add a new documentation section:
1. Create a folder: `docs/NN_section_name/`
2. Add markdown files with clear headings
3. Update this README's structure section
4. Add cross-references from related docs

---

## 📝 Document Versioning

**Documentation Version**: 1.0.0  
**Last Updated**: 2026-02-17  
**Corresponding Code Version**: Phase 5 Complete

---

## 💡 Tips for Reading

- **Start with [What Is This?](./00_getting_started/what_is_this.md)** if you're new
- **Use the [Glossary](./glossary.md)** for unfamiliar terms
- **Follow cross-references** (links) for deep dives
- **Check [Invariants](./08_testing_and_guarantees/invariants_defined_by_tests.md)** to understand guarantees
- **Reference [Failure Playbook](./09_production_guide/failure_playbook.md)** when troubleshooting

---

## ✨ Key Takeaways

1. **Transport Reliability**: Self-healing MQTT connection
2. **Data Correctness**: Typed, validated events
3. **Delivery Control**: Explicit backpressure
4. **Error Isolation**: Errors never propagate
5. **Observability**: Comprehensive metrics
6. **Test Coverage**: 301 tests, all invariants covered

---

**Happy reading! 📚**
