# BestBidAsk

Top-of-book market data event model.

---

## Field Reference

### symbol: str
Stock ticker symbol (uppercase normalized).

**Example**: `"AOT"`, `"PTT"`

**Validation**: None (string accepted)

**Normalization**: Converted to uppercase by adapter

---

### bid: float
Best bid price (highest buyer price).

**Example**: `25.50`, `100.25`

**Validation**: Must be finite (`not inf`, `not nan`)

**Allow negative**: Yes (edge cases, derivatives)

**Allow zero**: Yes (auction periods)

---

### ask: float
Best ask price (lowest seller price).

**Example**: `25.75`, `100.50`

**Validation**: Must be finite  

**Allow negative**: Yes

**Allow zero**: Yes (auction periods)

---

### bid_vol: int
Best bid volume (shares at bid price).

**Example**: `1000`, `50000`

**Validation**: Non-negative integer (protobuf guarantees)

---

### ask_vol: int
Best ask volume (shares at ask price).

**Example**: `500`, `25000`

**Validation**: Non-negative integer

---

### bid_flag: int
Market session flag for bid side.

**Values**:
- `0` = UNDEFINED
- `1` = NORMAL (continuous trading)
- `2` = ATO (At-The-Opening auction)
- `3` = ATC (At-The-Close auction)

**Note**: During ATO/ATC, prices are typically zero.

---

### ask_flag: int
Market session flag for ask side.

**Values**: Same as `bid_flag`

---

### recv_ts: int
Wall clock timestamp (nanoseconds since Unix epoch).

**Source**: `time.time_ns()` at message receipt

**Use**: Correlation with external logs, exchange timestamps

**Subject to**: NTP adjustment (may jump backwards)

---

### recv_mono_ns: int
Monotonic timestamp (nanoseconds).

**Source**: `time.perf_counter_ns()` at message receipt  

**Use**: Latency measurement

**Guarantee**: Never goes backwards

**Validation**: Must be >= 0

---

### connection_epoch: int
Reconnect counter (increments on each reconnect).

**Initial value**: 0

**Use**: Detect reconnects in strategy code

**Example**:
```python
last_epoch = 0
for event in dispatcher.poll():
    if event.connection_epoch != last_epoch:
        print(f"Reconnect detected! Epoch: {event.connection_epoch}")
        clear_state()
        last_epoch = event.connection_epoch
```

---

## Helper Methods

### is_auction() -> bool

Returns `True` if bid or ask is in auction period (ATO or ATC).

```python
event = BestBidAsk(..., bid_flag=BidAskFlag.ATO, ...)
assert event.is_auction()  # True
```

**Implementation**:
```python
def is_auction(self) -> bool:
    return self.bid_flag in (BidAskFlag.ATO, BidAskFlag.ATC) or \
           self.ask_flag in (BidAskFlag.ATO, BidAskFlag.ATC)
```

---

## Example Usage

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
    connection_epoch=0,
)

# Access fields
print(f"{event.symbol}: bid={event.bid}, ask={event.ask}")

# Check auction
if event.is_auction():
    print("In auction period")

# Compare prices (use tolerance!)
if abs(event.ask - event.bid) < 0.01:
    print("Tight spread")
```

---

## Implementation Reference

See [core/events.py](../../core/events.py):
- `BestBidAsk` class definition
- `is_auction()` method
- Field validators

---

## Test Coverage

24 tests in `test_events.py::TestBestBidAsk`

Key tests:
- Field validation
- Immutability (frozen)
- Hashability
- Equality
- `is_auction()` correctness

---

## Next Steps

- **[Event Contract](./event_contract.md)** — Model specifications
- **[FullBidOffer](./full_bid_offer.md)** — Full 10-level book
- **[Timestamp and Epoch](./timestamp_and_epoch.md)** — Timestamp semantics
