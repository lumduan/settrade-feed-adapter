# settrade-feed-adapter

A market data ingestion layer tailored for Settrade Open API — designed for reliable real-time feed processing, normalization, and delivery.

## What It Does

settrade-feed-adapter provides:

- **Real-time feed ingestion** from Settrade Open API via MQTT transport
- **Event normalization** into strongly-typed models (`BestBidAsk`, `FullBidOffer`, etc.)
- **Back-pressure-aware dispatcher** with bounded queues and drop policies
- **Feed livelihood tracking** per symbol and globally
- **Observability APIs** (metrics, logging, health)
- **Test-backed guarantees** for concurrency, ordering, and error isolation

## Quick Links

All detailed documentation is maintained under the `docs/` directory:

### For Newcomers

Start with:

- [What Is This?](docs/00_getting_started/what_is_this.md) — Overview and design goals
- [Quickstart Guide](docs/00_getting_started/quickstart.md) — Get running quickly
- [Mental Model](docs/00_getting_started/mental_model.md) — Conceptual explanation

### Architecture & Design

- [System Architecture](docs/01_system_overview/architecture.md)
- [Data Flow](docs/01_system_overview/data_flow.md)
- [Threading and Concurrency](docs/01_system_overview/threading_and_concurrency.md)

### Transport & Connectivity

- [MQTT Authentication and Token](docs/02_transport_mqtt/authentication_and_token.md)
- [Reconnect Strategy](docs/02_transport_mqtt/reconnect_strategy.md)
- [Subscription Model](docs/02_transport_mqtt/subscription_model.md)

### Adapter & Normalization

- [Parsing Pipeline](docs/03_adapter_and_normalization/parsing_pipeline.md)
- [Normalization Contract](docs/03_adapter_and_normalization/normalization_contract.md)

### Event Models

- [Event Contract](docs/04_event_models/event_contract.md)
- [Best Bid-Ask](docs/04_event_models/best_bid_ask.md)
- [Full Bid-Offer](docs/04_event_models/full_bid_offer.md)

### Dispatcher & Backpressure

- [Queue Model](docs/05_dispatcher_and_backpressure/queue_model.md)
- [Backpressure and Overflow Policy](docs/05_dispatcher_and_backpressure/overflow_policy.md)

### Feed Liveness

- [Global Liveness](docs/06_feed_liveness/global_liveness.md)
- [Per Symbol Liveness](docs/06_feed_liveness/per_symbol_liveness.md)

### Observability

- [Metrics Reference](docs/07_observability/metrics_reference.md)
- [Logging Policy](docs/07_observability/logging_policy.md)

### Testing & Production

- [Concurrency Guarantees](docs/08_testing_and_guarantees/concurrency_guarantees.md)
- [Failure Scenarios](docs/08_testing_and_guarantees/failure_scenarios.md)
- [Production Checklist](docs/09_production_guide/deployment_checklist.md)

### Glossary

- [Glossary](docs/glossary.md)

## Examples

See the `examples/` directory for ready-to-run usage examples:

- `example_bidoffer.py`
- `example_feed_health.py`

## Running Tests

```bash
uv run pytest tests -v
```

## License

This project is licensed under the MIT License.
