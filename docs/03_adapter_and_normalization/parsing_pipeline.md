# Parsing Pipeline

Protobuf decoding and event model construction.

---

## Pipeline Flow

```
Raw Binary Payload (bytes)
  ↓
betterproto.parse() → BidOfferV3 protobuf message
  ↓
Extract fields (direct access, no .to_dict())
  ↓
Convert Money to float (units + nanos * 1e-9)
  ↓
Normalize (uppercase symbol, validate ranges)
  ↓
Pydantic model construction (model_construct for speed)
  ↓
BestBidAsk or FullBidOffer event
  ↓
dispatcher.push(event)
```

---

## Hot Path Optimization

### Direct Field Access

**SDK approach** (slow):
```python
msg_dict = BidOfferV3().parse(payload).to_dict(casing=betterproto.Casing.SNAKE)
bid = Decimal(msg_dict["bid_price1"]["units"]) + Decimal(msg_dict["bid_price1"]["nanos"]) / Decimal("1e9")
```

**Our approach** (fast):
```python
msg = BidOfferV3().parse(payload)
bid = msg.bid_price1.units + msg.bid_price1.nanos * 1e-9
```

**Benefit**: No dictionary allocation, direct protobuf field access.

---

### Skip Pydantic Validation

**Normal construction** (slower):
```python
event = BestBidAsk(
    symbol=symbol,
    bid=bid,
    # ... validation runs ...
)
```

**Hot path construction** (faster):
```python
event = BestBidAsk.model_construct(
    symbol=symbol,
    bid=bid,
    # ... validation skipped ...
)
```

**Warning**: Only use `model_construct()` when data is pre-validated (hot path from trusted source).

---

## Money Conversion

Settrade uses protobuf `Money` type:

```protobuf
message Money {
  int64 units = 1;
  int32 nanos = 2;
}
```

**Conversion**:
```python
def money_to_float(money) -> float:
    return money.units + money.nanos * 1e-9
```

**Example**:
- `Money(units=25, nanos=500_000_000)` → `25.5`
- `Money(units=100, nanos=250_000_000)` → `100.25`

---

## Implementation Example

```python
def _on_raw_message(
    topic: str,
    payload: bytes,
    recv_ts: int,
    recv_mono_ns: int,
) -> None:
    try:
        # 1. Parse protobuf
        msg: BidOfferV3 = BidOfferV3().parse(payload)
        
        # 2. Extract symbol
        symbol: str = msg.symbol.upper()
        
        # 3. Convert Money to float
        bid: float = msg.bid_price1.units + msg.bid_price1.nanos * 1e-9
        ask: float = msg.ask_price1.units + msg.ask_price1.nanos * 1e-9
        
        # 4. Extract volumes and flags
        bid_vol: int = msg.bid_volume1
        ask_vol: int = msg.ask_volume1
        bid_flag: int = int(msg.bid_flag)
        ask_flag: int = int(msg.ask_flag)
        
        # 5. Construct event (skip validation)
        event = BestBidAsk.model_construct(
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_vol=bid_vol,
            ask_vol=ask_vol,
            bid_flag=bid_flag,
            ask_flag=ask_flag,
            recv_ts=recv_ts,
            recv_mono_ns=recv_mono_ns,
            connection_epoch=self._mqtt_client.connection_epoch,
        )
        
        # 6. Push to dispatcher
        self._dispatcher.push(event)
        self._stats.messages_parsed += 1
        
    except Exception as e:
        self._stats.parse_errors += 1
        logger.error(f"Parse error: {e}")
```

---

## Error Isolation

Parse errors are **isolated**:
- Exception caught
- `parse_errors` counter incremented
- Error logged
- Continue processing (no crash)

**Test**: `test_settrade_adapter.py::TestErrorHandling::test_parse_error_logged_and_counted`

---

## Full Depth Parsing

For Full 10-level book (`FullBidOffer`):

```python
# Parse all 10 bid levels
bid_prices = tuple(
    msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
    msg.bid_price2.units + msg.bid_price2.nanos * 1e-9,
    # ... up to bid_price10
)

bid_volumes = tuple(
    msg.bid_volume1,
    msg.bid_volume2,
    # ... up to bid_volume10
)
```

---

## Implementation Reference

See [infra/settrade_adapter.py](../../infra/settrade_adapter.py):
- `BidOfferAdapter._on_raw_message()` method
- `_money_to_float()` helper function
- Error handling logic

---

## Test Coverage

- `test_settrade_adapter.py::TestNormalization::test_money_conversion_accuracy`
- `test_settrade_adapter.py::TestNormalization::test_symbol_uppercase_normalization`
- `test_settrade_adapter.py::TestErrorHandling::test_parse_error_logged_and_counted`

---

## Performance

**Typical latency** (parse + normalize):
- P50: ~10-15µs
- P95: ~20-30µs
- P99: ~40-60µs

*Benchmarked on Apple M1, Python 3.12*

---

## Next Steps

- **[Normalization Contract](./normalization_contract.md)** — Data transformation rules
- **[Money Precision Model](./money_precision_model.md)** — Float precision
- **[Error Isolation Model](./error_isolation_model.md)** — Error handling
