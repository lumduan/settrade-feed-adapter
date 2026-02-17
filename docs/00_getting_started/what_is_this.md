# What Is This?

## Overview

**settrade-feed-adapter** is a lightweight MQTT-based market data ingestion layer for the Settrade Open API, engineered for **low-latency algorithmic trading systems**.

### Core Purpose

This adapter provides **direct control** over the complete MQTT → Protobuf → Event pipeline without relying on the official SDK's abstraction layer.

### Target Audience

🟢 **Newcomers**: Understand the flow in under 15 minutes  
🔵 **Experienced developers**: Instantly locate contracts / invariants / edge cases  
🔴 **Maintainers**: Clearly see design guarantees from test coverage  

---

## Three Core Principles

### 1. Transport Reliability

- Auto-reconnect with exponential backoff
- Token refresh before expiration
- Generation-based stale message rejection
- Clean shutdown with connection epoch tracking

### 2. Data Correctness

- Strongly-typed Pydantic event models (not dictionaries)
- Direct protobuf field access (no `.to_dict()` conversion)
- Comprehensive input validation and normalization
- Float precision contract for price comparisons

### 3. Delivery Control

- Bounded queue with explicit drop-oldest backpressure
- Single-producer, single-consumer (SPSC) concurrency model
- No hidden thread pools or buffering
- Visible overflow metrics and health monitoring

---

## Design Guarantees

✅ **Zero hidden threading** — Single MQTT IO thread + Strategy thread  
✅ **Deterministic event flow** — MQTT → Adapter → Queue → Strategy  
✅ **Explicit backpressure** — Drop-oldest policy with exact drop counting  
✅ **Type safety** — Pydantic models with frozen=True for immutability  
✅ **Reconnect safety** — Generation prevents stale event dispatch  
✅ **Observable** — Comprehensive metrics with zero external dependencies  

---

## What This Is NOT

❌ **NOT a trading framework** — You implement strategy logic  
❌ **NOT an order execution system** — Use official SDK for orders  
❌ **NOT a data storage solution** — You own persistence  
❌ **NOT a backtesting engine** — Build on top if needed  
❌ **NOT HFT-ready** — Co-located exchange feeds required for ultra-low latency  

---

## Architecture Diagram

```
┌────────────────────────────────────────────────────────────────┐
│                     Settrade Open API                          │
│                   (MQTT over WebSocket+TLS)                    │
└─────────────────────────┬──────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│              SettradeMQTTClient (Phase 1)                       │
│  • WebSocket+TLS transport                                      │
│  • Token auth + auto-refresh                                    │
│  • Reconnect with exponential backoff                           │
│  • Generation-based stale message rejection                     │
└─────────────────────────┬───────────────────────────────────────┘
                          │ binary protobuf
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│              BidOfferAdapter (Phase 2)                          │
│  • Protobuf parse (betterproto)                                 │
│  • Normalize → BestBidAsk / FullBidOffer                        │
│  • Error isolation (parse errors don't crash)                   │
│  • Direct field access (no .to_dict())                          │
└─────────────────────────┬───────────────────────────────────────┘
                          │ typed events
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Dispatcher (Phase 3)                           │
│  • Bounded deque (maxlen=100K default)                          │
│  • Drop-oldest backpressure                                     │
│  • EMA drop rate health monitoring                              │
│  • Lock-free push/poll (SPSC)                                   │
└─────────────────────────┬───────────────────────────────────────┘
                          │ batch polling
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Your Strategy Code                             │
│  • dispatcher.poll(max_events=100)                              │
│  • Process events in batch                                      │
│  • Implement your logic                                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Facts

| Aspect | Value |
|--------|-------|
| **Programming Language** | Python 3.11+ |
| **Transport** | MQTT over WebSocket Secure (WSS) |
| **Serialization** | Protobuf (betterproto) |
| **Concurrency** | Threading (paho-mqtt) |
| **Type System** | Pydantic v2 models |
| **Performance** | ~1.1-1.3x faster than SDK (parse only) |
| **Primary Value** | Architectural control, not raw speed |
| **Test Coverage** | 301 test cases across 6 test files |

---

## When to Use This Adapter

✅ **Use this if you need:**
- Explicit control over message parsing and event flow
- Strongly-typed events for safer integration
- Custom backpressure handling for high-frequency data
- Foundation for building custom trading infrastructure
- Measurable pipeline overhead for optimization
- Easier testing and replay mechanisms

❌ **Use the official SDK if you need:**
- Convenience and simplicity
- Official support and updates
- Quick prototyping without pipeline control
- Integration with SDK's order execution API

---

## Next Steps

1. **[Quickstart Guide](./quickstart.md)** — Get running in 5 minutes
2. **[Mental Model](./mental_model.md)** — Understand the conceptual flow
3. **[System Overview](../01_system_overview/architecture.md)** — Deep dive into architecture
