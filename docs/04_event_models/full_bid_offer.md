# FullBidOffer

Full 10-level order book (Level 2) market data event. Produced by
`BidOfferAdapter` when `full_depth=True` is set in `BidOfferAdapterConfig`.

---

## Enabling Full Depth

By default the adapter emits `BestBidAsk` events (Level 1 only). To
receive `FullBidOffer` events, set `full_depth=True` in the adapter
configuration:

```python
config = BidOfferAdapterConfig(
    full_depth=True,
    # ... other settings
)
```

---

## Field Reference

| Field | Type | Constraint | Description |
| --- | --- | --- | --- |
| `symbol` | `str` | `min_length=1` | Stock symbol, e.g. `"AOT"` |
| `bid_prices` | `tuple[float, ...]` | exactly 10 elements | 10 bid prices, index 0 = best bid |
| `ask_prices` | `tuple[float, ...]` | exactly 10 elements | 10 ask prices, index 0 = best ask |
| `bid_volumes` | `tuple[int, ...]` | exactly 10 elements | 10 bid volumes |
| `ask_volumes` | `tuple[int, ...]` | exactly 10 elements | 10 ask volumes |
| `bid_flag` | `BidAskFlag` | enum | Bid session flag (UNDEFINED/NORMAL/ATO/ATC) |
| `ask_flag` | `BidAskFlag` | enum | Ask session flag |
| `recv_ts` | `int` | `ge=0` | Wall-clock nanosecond timestamp (`time.time_ns()`) |
| `recv_mono_ns` | `int` | `ge=0` | Monotonic nanosecond timestamp (`time.perf_counter_ns()`) |
| `connection_epoch` | `int` | `default=0, ge=0` | Reconnect version counter |

---

## Tuple Length: Exactly 10

Every price and volume tuple must contain exactly 10 elements. Pydantic
enforces this with `min_length=10, max_length=10`:

```python
# Too few elements -- raises ValidationError
FullBidOffer(symbol="AOT",
             bid_prices=(25.5, 25.25, 25.0),   # only 3
             ...)

# Too many elements -- raises ValidationError
FullBidOffer(symbol="AOT",
             bid_prices=(25.5,) * 11,           # 11 elements
             ...)
```

Unused levels are filled with `0.0` (prices) or `0` (volumes):

```python
bid_prices=(25.50, 25.25, 25.00, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
bid_volumes=(1000, 500, 300, 0, 0, 0, 0, 0, 0, 0)
```

---

## Level 1 Matches BestBidAsk

The first element of each tuple corresponds to the top-of-book data in
a `BestBidAsk` event for the same symbol and timestamp:

```python
full = FullBidOffer(symbol="AOT", bid_prices=(25.50, 25.25, ...), ...)

# These match the equivalent BestBidAsk fields:
best_bid   = full.bid_prices[0]     # == BestBidAsk.bid
best_ask   = full.ask_prices[0]     # == BestBidAsk.ask
best_bvol  = full.bid_volumes[0]    # == BestBidAsk.bid_vol
best_avol  = full.ask_volumes[0]    # == BestBidAsk.ask_vol
```

To convert a `FullBidOffer` to a `BestBidAsk`:

```python
from core.events import BestBidAsk

bba = BestBidAsk(
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

## Deep Immutability

The model uses tuples (not lists) for all price and volume sequences.
Combined with `frozen=True`, this provides deep immutability -- there is
no way to mutate the event after construction:

```python
event = FullBidOffer(symbol="AOT", bid_prices=(25.5, ...), ...)

event.bid_prices = (30.0, ...)       # raises ValidationError (frozen)
event.bid_prices[0] = 30.0           # raises TypeError (tuple)
```

This is important for thread safety: events can be shared between
threads without synchronization.

---

## Negative Volumes Are Allowed

Unlike `BestBidAsk` where `bid_vol` and `ask_vol` have a `ge=0`
constraint, the tuple elements inside `FullBidOffer` have no per-element
validation. Negative volumes within the tuples will pass validation:

```python
event = FullBidOffer(
    symbol="AOT",
    bid_volumes=(1000, -500, 300, 0, 0, 0, 0, 0, 0, 0),
    ...
)
# valid -- no ValidationError on individual tuple elements
```

This is a deliberate design choice: adding per-element validation to
40 tuple elements would add significant overhead to the validated
construction path. Strategy code should handle unexpected negative
values defensively.

---

## Performance Caveat

Each `FullBidOffer` message allocates approximately 46 Python objects:
4 tuples plus 40 float/int element objects. At high message rates this
creates measurable GC pressure.

**Not intended for sub-100us strategies.** If your strategy requires
ultra-low latency, use `BestBidAsk` (the default) which allocates far
fewer objects.

| Mode | Objects per message |
| --- | --- |
| `BestBidAsk` (default) | ~10 |
| `FullBidOffer` (`full_depth=True`) | ~46 |

---

## Usage Example

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

# Iterate active bid levels
for i in range(10):
    price = event.bid_prices[i]
    vol = event.bid_volumes[i]
    if price == 0.0:
        break
    print(f"  Level {i+1}: {price} x {vol}")

# Total depth volume
total_bid = sum(event.bid_volumes)
total_ask = sum(event.ask_volumes)
print(f"Total bid volume: {total_bid}, ask volume: {total_ask}")

# Auction check
if event.is_auction():
    print("Auction period -- depth data may not reflect continuous trading")
```

---

## Related Pages

- [BestBidAsk](./best_bid_ask.md) -- top-of-book event (lower latency)
- [Event Contract](./event_contract.md) -- shared model guarantees
- [Timestamp and Epoch](./timestamp_and_epoch.md) -- dual timestamp and reconnect semantics
