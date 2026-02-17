# Event Contract

Pydantic event model specifications and contracts.

---

## Overview

All events are **immutable Pydantic models** with these properties:
- ✅ **Frozen** (`frozen=True`) — Cannot modify fields
- ✅ **Hashable** — Can be used as dict keys or in sets
- ✅ **Equatable** — Events with identical fields compare equal
- ✅ **Validated** — Pydantic enforces types and constraints
- ✅ **Strict** — Extra fields rejected (`extra='forbid'`)

---

## Event Models

### BestBidAsk
Top-of-book (Level 1) market data.

**Fields**:
- `symbol: str` — Stock ticker (uppercase)
- `bid: float` — Best bid price
- `ask: float` — Best ask price
- `bid_vol: int` — Best bid volume
- `ask_vol: int` — Best ask volume
- `bid_flag: int` — Bid session flag (1=NORMAL, 2=ATO, 3=ATC)
- `ask_flag: int` — Ask session flag
- `recv_ts: int` — Wall clock timestamp (nanoseconds)
- `recv_mono_ns: int` — Monotonic timestamp (nanoseconds)
- `connection_epoch: int` — Reconnect counter (default 0)

### FullBidOffer
Full 10-level order book (Level 2).

**Fields**:
- `symbol: str`
- `bid_prices: tuple[float, ...]` — 10 bid prices
- `ask_prices: tuple[float, ...]` — 10 ask prices
- `bid_volumes: tuple[int, ...]` — 10 bid volumes
- `ask_volumes: tuple[int, ...]` — 10 ask volumes
- `bid_flag: int`
- `ask_flag: int`
- `recv_ts: int`
- `recv_mono_ns: int`
- `connection_epoch: int`

---

## Model Contracts

### Immutability

**Contract**: All fields are frozen and cannot be modified.

```python
event = BestBidAsk(symbol="AOT", bid=25.5, ...)

# ❌ Raises ValidationError
event.bid = 26.0
```

**Test**: `test_events.py::TestBestBidAsk::test_frozen_immutability`

---

### Hashability

**Contract**: Events can be used as dict keys or in sets.

```python
event1 = BestBidAsk(...)
event2 = BestBidAsk(...)

# ✅ Works
event_set = {event1, event2}
event_dict = {event1: "data"}
```

**Test**: `test_events.py::TestBestBidAsk::test_hashable`

---

### Equality

**Contract**: Events with identical field values compare equal.

```python
event1 = BestBidAsk(symbol="AOT", bid=25.5, ...)
event2 = BestBidAsk(symbol="AOT", bid=25.5, ...)

assert event1 == event2  # ✅ True
```

**Test**: `test_events.py::TestBestBidAsk::test_equality`

---

### Extra Fields Rejected

**Contract**: Cannot add unknown fields.

```python
# ❌ Raises ValidationError
event = BestBidAsk(
    symbol="AOT",
    bid=25.5,
    extra_field="invalid",  # Not allowed
)
```

**Test**: `test_events.py::TestBestBidAsk::test_extra_fields_rejected`

---

### Field Validation

**Contract**: Field values must pass Pydantic validation.

```python
# ❌ recv_mono_ns must be >= 0
event = BestBidAsk(..., recv_mono_ns=-1)  # Raises ValidationError
```

**Test**: `test_events.py::TestBestBidAsk::test_recv_mono_ns_negative_rejected`

---

## Hot Path Construction

**Normal** (with validation):
```python
event = BestBidAsk(symbol="AOT", bid=25.5, ...)
# Pydantic validation runs
```

**Hot path** (skip validation):
```python
event = BestBidAsk.model_construct(symbol="AOT", bid=25.5, ...)
# Pydantic validation skipped
```

⚠️ **Warning**: Only use `model_construct()` when data is pre-validated (trusted source).

---

## Helper Methods

### is_auction()

**Contract**: Returns `True` if bid or ask is in auction (ATO/ATC).

```python
event = BestBidAsk(..., bid_flag=BidAskFlag.ATO, ...)
assert event.is_auction()  # True
```

**Test**: `test_events.py::TestBestBidAsk::test_is_auction`

---

## Implementation Reference

See [core/events.py](../../core/events.py):
- `BestBidAsk` model (lines ~100-150)
- `FullBidOffer` model (lines ~180-230)
- `BidAskFlag` enum (lines ~70-90)

---

## Test Coverage

**BestBidAsk** (24 tests):
- Creation, validation, immutability
- Hashability, equality
- Field constraints
- Helper methods

**FullBidOffer** (24 tests):
- Same coverage as BestBidAsk
- Tuple field validation

---

## Next Steps

- **[BestBidAsk](./best_bid_ask.md)** — Detailed field reference
- **[FullBidOffer](./full_bid_offer.md)** — Full depth reference
- **[Timestamp and Epoch](./timestamp_and_epoch.md)** — Timestamp semantics
