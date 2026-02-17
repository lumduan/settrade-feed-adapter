# Event Contract

Pydantic model guarantees shared by `BestBidAsk` and `FullBidOffer`.

---

## Model Configuration

Both event models use the same Pydantic `ConfigDict`:

```python
model_config = ConfigDict(frozen=True, extra="forbid")
```

This yields four guarantees that strategy code can rely on.

### Immutable (frozen=True)

All fields are read-only after construction. Any attempt to assign a field
raises a `ValidationError`:

```python
event = BestBidAsk(symbol="AOT", bid=25.5, ask=26.0,
                   bid_vol=1000, ask_vol=500,
                   bid_flag=BidAskFlag.NORMAL, ask_flag=BidAskFlag.NORMAL,
                   recv_ts=1739500000000000000, recv_mono_ns=123456789)

event.bid = 30.0   # raises ValidationError
```

### Hashable

Because the models are frozen, they are hashable. You can use events as
dictionary keys or store them in sets:

```python
seen = set()
seen.add(event)           # works
cache = {event: result}   # works
```

### Equality by Value

Two events with identical field values compare equal, regardless of whether
they are the same Python object:

```python
a = BestBidAsk(symbol="AOT", bid=25.5, ...)
b = BestBidAsk(symbol="AOT", bid=25.5, ...)
assert a == b        # True
assert a is not b    # different objects
```

### No Extra Fields (extra="forbid")

Passing a field name that is not part of the schema raises a
`ValidationError`. This protects against typos and schema drift:

```python
# raises ValidationError -- "spread" is not a valid field
BestBidAsk(symbol="AOT", bid=25.5, ask=26.0, spread=0.5, ...)
```

---

## BidAskFlag Enum

```python
class BidAskFlag(IntEnum):
    UNDEFINED = 0
    NORMAL    = 1
    ATO       = 2   # At-The-Opening auction
    ATC       = 3   # At-The-Close auction
```

`BidAskFlag` is an `IntEnum`, so it is interchangeable with `int`:

```python
BidAskFlag.NORMAL == 1       # True
int(BidAskFlag.ATO)          # 2
BidAskFlag(3)                # BidAskFlag.ATC
BidAskFlag(99)               # raises ValueError
```

This interchangeability matters for the hot path -- see the
`model_construct()` section below.

---

## is_auction() Method

Both `BestBidAsk` and `FullBidOffer` expose an `is_auction()` method that
returns `True` when either the bid or ask flag is `ATO` or `ATC`:

```python
event = BestBidAsk(..., bid_flag=BidAskFlag.ATO, ask_flag=BidAskFlag.ATO, ...)
assert event.is_auction() is True

event = BestBidAsk(..., bid_flag=BidAskFlag.NORMAL, ask_flag=BidAskFlag.NORMAL, ...)
assert event.is_auction() is False
```

Internally both models delegate to a shared `_is_auction(bid_flag, ask_flag)`
helper that compares against a constant `_AUCTION_FLAGS` tuple. Because
`BidAskFlag` is an `IntEnum`, the comparison works with both enum members
and plain `int` values.

---

## Construction: Validated vs. Hot Path

### Regular Construction (with validation)

```python
event = BestBidAsk(
    symbol="AOT",
    bid=25.5,
    ask=26.0,
    bid_vol=1000,
    ask_vol=500,
    bid_flag=BidAskFlag.NORMAL,
    ask_flag=BidAskFlag.NORMAL,
    recv_ts=1739500000000000000,
    recv_mono_ns=123456789,
)
```

Pydantic runs all validators: type coercion, range checks (`ge=0`),
length constraints, and `int` to `BidAskFlag` conversion. Use this path
for tests and any data from untrusted sources.

### model_construct() -- Hot Path (no validation)

```python
event = BestBidAsk.model_construct(
    symbol="AOT",
    bid=25.5,
    ask=26.0,
    bid_vol=1000,
    ask_vol=500,
    bid_flag=1,             # stored as int, NOT BidAskFlag
    ask_flag=1,
    recv_ts=1739500000000000000,
    recv_mono_ns=123456789,
    connection_epoch=0,
)
```

`model_construct()` bypasses ALL Pydantic validation. This is used in the
MQTT adapter hot path to avoid allocation and CPU overhead.

**Dangers of model_construct():**

| Concern | What happens |
| --- | --- |
| No type coercion | `bid_flag` stays as `int`, not `BidAskFlag` |
| No range checks | Negative timestamps pass silently |
| No length checks | Tuples with != 10 elements pass silently |
| No extra-field rejection | Typos in field names are silently ignored |
| No default filling | `connection_epoch` must be passed explicitly |

The `is_auction()` method still works correctly with plain `int` flags
because the underlying `_is_auction()` function uses `in` membership
testing against `IntEnum` values, and `2 in (BidAskFlag.ATO, BidAskFlag.ATC)`
evaluates to `True`.

---

## String-to-Int Coercion

During validated construction, Pydantic coerces compatible types
automatically. For example, string values that represent integers are
accepted for `int` fields:

```python
event = BestBidAsk(
    ...,
    recv_ts="1739500000000000000",   # string coerced to int
    recv_mono_ns="123456789",        # string coerced to int
)
```

This coercion does NOT happen with `model_construct()`.

---

## Summary Table

| Property | Guarantee |
| --- | --- |
| Immutable | `frozen=True` -- assignment raises `ValidationError` |
| Hashable | Can be used in `set` and as `dict` key |
| Equality | Value-based -- same fields means `==` is `True` |
| Strict schema | `extra="forbid"` -- unknown fields rejected |
| Auction detection | `is_auction()` on both models |
| Hot-path safe | `model_construct()` skips validation for performance |

---

## Related Pages

- [BestBidAsk](./best_bid_ask.md) -- field reference for top-of-book events
- [FullBidOffer](./full_bid_offer.md) -- field reference for 10-level depth
- [Timestamp and Epoch](./timestamp_and_epoch.md) -- dual timestamp and reconnect semantics
