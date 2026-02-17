# BestBidAsk

Top-of-book (Level 1) market data event. This is the default event type
produced by `BidOfferAdapter`.

---

## Field Reference

| Field | Type | Constraint | Description |
| --- | --- | --- | --- |
| `symbol` | `str` | `min_length=1` | Stock symbol, e.g. `"AOT"` |
| `bid` | `float` | none | Best bid price |
| `ask` | `float` | none | Best ask price |
| `bid_vol` | `int` | `ge=0` | Volume at best bid |
| `ask_vol` | `int` | `ge=0` | Volume at best ask |
| `bid_flag` | `BidAskFlag` | enum | Bid session flag (UNDEFINED/NORMAL/ATO/ATC) |
| `ask_flag` | `BidAskFlag` | enum | Ask session flag |
| `recv_ts` | `int` | `ge=0` | Wall-clock nanosecond timestamp (`time.time_ns()`) |
| `recv_mono_ns` | `int` | `ge=0` | Monotonic nanosecond timestamp (`time.perf_counter_ns()`) |
| `connection_epoch` | `int` | `default=0, ge=0` | Reconnect version counter |

---

## Usage Example

```python
from core.events import BestBidAsk, BidAskFlag

event = BestBidAsk(
    symbol="AOT",
    bid=25.50,
    ask=25.75,
    bid_vol=1000,
    ask_vol=500,
    bid_flag=BidAskFlag.NORMAL,
    ask_flag=BidAskFlag.NORMAL,
    recv_ts=1739500000000000000,
    recv_mono_ns=123456789,
)

print(f"{event.symbol}: {event.bid} x {event.bid_vol} / "
      f"{event.ask} x {event.ask_vol}")

if event.is_auction():
    print("Auction period -- prices may be zero")
```

---

## Price Semantics

### Negative prices are allowed

The `bid` and `ask` fields have no lower bound. Negative prices can
occur in derivatives or other edge cases. Pydantic does not reject them:

```python
event = BestBidAsk(symbol="FUT", bid=-1.5, ask=-1.0, ...)
# valid -- no ValidationError
```

### Zero prices during ATO/ATC

During auction periods (At-The-Opening, At-The-Close), the exchange
typically sends zero prices. Strategy code should check `is_auction()`
before interpreting price data:

```python
event = BestBidAsk(
    symbol="AOT",
    bid=0.0,
    ask=0.0,
    bid_vol=0,
    ask_vol=0,
    bid_flag=BidAskFlag.ATO,
    ask_flag=BidAskFlag.ATO,
    recv_ts=1739500000000000000,
    recv_mono_ns=100000000,
)

assert event.is_auction() is True
# Do not use bid/ask for spread calculation here
```

### bid > ask is allowed

There is no cross-validation between `bid` and `ask`. The model does not
reject a bid price that exceeds the ask price. This can happen during
auction periods or due to exchange-specific behavior:

```python
event = BestBidAsk(symbol="AOT", bid=26.0, ask=25.5, ...)
# valid -- no ValidationError
```

---

## Volume Constraints

`bid_vol` and `ask_vol` must be non-negative integers (`ge=0`). Negative
volumes raise a `ValidationError` during regular construction:

```python
BestBidAsk(symbol="AOT", bid=25.5, ask=26.0,
           bid_vol=-100, ask_vol=500, ...)
# raises ValidationError
```

---

## is_auction() Method

Returns `True` if either `bid_flag` or `ask_flag` is `ATO` (2) or
`ATC` (3):

```python
event = BestBidAsk(..., bid_flag=BidAskFlag.ATC, ask_flag=BidAskFlag.NORMAL, ...)
assert event.is_auction() is True   # one side is enough
```

---

## Hot-Path Construction with model_construct()

In the MQTT adapter, events are built with `model_construct()` to skip
Pydantic validation and reduce latency:

```python
event = BestBidAsk.model_construct(
    symbol=symbol,
    bid=bid_price,
    ask=ask_price,
    bid_vol=bid_volume,
    ask_vol=ask_volume,
    bid_flag=raw_bid_flag,       # int, not BidAskFlag
    ask_flag=raw_ask_flag,       # int, not BidAskFlag
    recv_ts=ts,
    recv_mono_ns=mono,
    connection_epoch=epoch,
)
```

When constructed this way:

- No type coercion runs -- `bid_flag` is stored as a plain `int`, not a
  `BidAskFlag` member.
- No constraint checks run -- negative timestamps or volumes would not
  be caught.
- `connection_epoch` has no default -- it must be passed explicitly.
- `is_auction()` still works correctly because `IntEnum` comparison
  accepts plain `int` values.

Use `model_construct()` only when the data source is trusted (i.e., the
protobuf adapter layer). Use regular construction for tests and any
external or untrusted data.

---

## Immutability

The model is frozen. Any field assignment after construction raises a
`ValidationError`:

```python
event = BestBidAsk(symbol="AOT", bid=25.5, ...)
event.bid = 30.0   # raises ValidationError
```

See [Event Contract](./event_contract.md) for additional model guarantees
(hashability, equality by value, extra field rejection).

---

## Related Pages

- [Event Contract](./event_contract.md) -- shared model guarantees
- [FullBidOffer](./full_bid_offer.md) -- full 10-level depth book
- [Timestamp and Epoch](./timestamp_and_epoch.md) -- dual timestamp and reconnect semantics
