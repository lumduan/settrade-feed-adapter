# FullBidOffer

Full 10-level order book (Level 2) market data event model.

---

## Overview

`FullBidOffer` provides **complete depth-of-market** data:
- 10 price levels on bid side (buy orders)
- 10 price levels on ask side (sell orders)
- Volumes at each price level
- Session flags (NORMAL/ATO/ATC)

**Note**: Not all symbols provide 10 levels. Some may only have 5 or fewer levels with remaining slots filled with zeros.

---

## Field Reference

### symbol: str
Stock ticker symbol (uppercase normalized).

---

### bid_prices: tuple[float, ...]
10 bid prices (best to worst).

**Example**: `(25.50, 25.25, 25.00, 24.75, ..., 0.0)`

**Length**: Exactly 10 elements

**Order**: Descending (highest first)

**Validation**: Must be tuple (immutable)

**Empty levels**: Represented as `0.0`

---

### ask_prices: tuple[float, ...]
10 ask prices (best to worst).

**Example**: `(25.75, 26.00, 26.25, 26.50, ..., 0.0)`

**Length**: Exactly 10 elements

**Order**: Ascending (lowest first)

**Validation**: Must be tuple (immutable)

**Empty levels**: Represented as `0.0`

---

### bid_volumes: tuple[int, ...]
10 bid volumes (shares at each price level).

**Example**: `(1000, 500, 300, ..., 0)`

**Length**: Exactly 10 elements

**Validation**: Must be tuple of integers

**Empty levels**: Represented as `0`

---

### ask_volumes: tuple[int, ...]
10 ask volumes (shares at each price level).

**Example**: `(500, 800, 1200, ..., 0)`

**Length**: Exactly 10 elements

**Validation**: Must be tuple of integers

**Empty levels**: Represented as `0`

---

### bid_flag: int
Market session flag for bid side.

**Values**:
- `0` = UNDEFINED
- `1` = NORMAL (continuous trading)
- `2` = ATO (At-The-Opening auction)
- `3` = ATC (At-The-Close auction)

---

### ask_flag: int
Market session flag for ask side.

**Values**: Same as `bid_flag`

---

### recv_ts: int
Wall clock timestamp (nanoseconds since Unix epoch).

See [BestBidAsk](./best_bid_ask.md#recv_ts-int) for details.

---

### recv_mono_ns: int
Monotonic timestamp (nanoseconds).

See [BestBidAsk](./best_bid_ask.md#recv_mono_ns-int) for details.

---

### connection_epoch: int
Reconnect counter (increments on each reconnect).

See [BestBidAsk](./best_bid_ask.md#connection_epoch-int) for details.

---

## Data Characteristics

### Tuple Immutability

All price/volume sequences are **Python tuples** (not lists).

**Why tuples?**
- ✅ Immutable (consistent with `frozen=True` Pydantic model)
- ✅ Hashable (enables caching and deduplication)
- ✅ Slightly faster than lists for fixed-size data

```python
event = FullBidOffer(
    bid_prices=(25.50, 25.25, ...),  # ✅ Tuple
    bid_volumes=(1000, 500, ...),    # ✅ Tuple
)

# ❌ Raises ValidationError
event = FullBidOffer(
    bid_prices=[25.50, 25.25, ...],  # ❌ List not allowed
)
```

---

### Level Semantics

**Best prices** (Level 1):
```python
best_bid = event.bid_prices[0]  # Highest bid
best_ask = event.ask_prices[0]  # Lowest ask
```

**Empty levels**:
```python
# If only 3 bid levels active:
bid_prices = (25.50, 25.25, 25.00, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
bid_volumes = (1000, 500, 300, 0, 0, 0, 0, 0, 0, 0)
```

**Contract**: If price is `0.0`, volume should also be `0`.

---

## Example Usage

```python
from core.events import FullBidOffer, BidAskFlag

event = FullBidOffer(
    symbol="AOT",
    bid_prices=(25.50, 25.25, 25.00, 24.75, 24.50, 0.0, 0.0, 0.0, 0.0, 0.0),
    ask_prices=(25.75, 26.00, 26.25, 26.50, 26.75, 0.0, 0.0, 0.0, 0.0, 0.0),
    bid_volumes=(1000, 500, 300, 200, 100, 0, 0, 0, 0, 0),
    ask_volumes=(500, 800, 1200, 1000, 600, 0, 0, 0, 0, 0),
    bid_flag=BidAskFlag.NORMAL,
    ask_flag=BidAskFlag.NORMAL,
    recv_ts=1739500000000000000,
    recv_mono_ns=123456789,
    connection_epoch=0,
)

# Access best prices (Level 1)
print(f"Best bid: {event.bid_prices[0]} x {event.bid_volumes[0]}")
print(f"Best ask: {event.ask_prices[0]} x {event.ask_volumes[0]}")

# Calculate total bid volume (all levels)
total_bid_vol = sum(event.bid_volumes)
print(f"Total bid volume: {total_bid_vol}")

# Iterate active levels
for i, (price, vol) in enumerate(zip(event.bid_prices, event.bid_volumes)):
    if price == 0.0:
        break  # No more active levels
    print(f"Bid level {i+1}: {price} x {vol}")
```

---

## Performance Notes

### Tuple vs List

**Tuple advantages**:
- ~5-10% faster creation (C-level optimization)
- Hashable (enables caching strategies)
- Immutable (thread-safe reads)

**Tuple disadvantages**:
- Cannot modify in-place (must create new tuple)

**Verdict**: Tuples are appropriate here since events are immutable.

---

### Memory Layout

Each `FullBidOffer` event:
- 10 bid prices × 8 bytes = 80 bytes
- 10 ask prices × 8 bytes = 80 bytes
- 10 bid volumes × 8 bytes = 80 bytes
- 10 ask volumes × 8 bytes = 80 bytes
- String + metadata ≈ 120 bytes

**Total**: ~440 bytes per event

**Queue impact**: 10,000 events ≈ 4.4 MB

---

## Conversion to BestBidAsk

**Need top-of-book only?** Extract Level 1:

```python
def to_best_bid_ask(full: FullBidOffer) -> BestBidAsk:
    return BestBidAsk(
        symbol=full.symbol,
        bid=full.bid_prices[0],
        ask=full.ask_prices[0],
        bid_vol=full.bid_volumes[0],
        ask_vol=full.ask_volumes[0],
        bid_flag=full.bid_flag,
        ask_flag=full.ask_flag,
        recv_ts=full.recv_ts,
        recv_mono_ns=full.recv_mono_ns,
        connection_epoch=full.connection_epoch,
    )
```

---

## Implementation Reference

See [core/events.py](../../core/events.py):
- `FullBidOffer` class definition
- Tuple field validators
- Pydantic configuration

---

## Test Coverage

24 tests in `test_events.py::TestFullBidOffer`

Key tests:
- Tuple validation (reject lists)
- Length validation (exactly 10 elements)
- Immutability
- Hashability
- Equality

---

## Next Steps

- **[BestBidAsk](./best_bid_ask.md)** — Top-of-book reference
- **[Event Contract](./event_contract.md)** — Model specifications
- **[Timestamp and Epoch](./timestamp_and_epoch.md)** — Timestamp semantics
