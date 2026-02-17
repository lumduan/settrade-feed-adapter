# Normalization Contract

Data transformation rules and validation.

---

## Symbol Normalization

**Rule**: All symbols converted to uppercase.

```python
symbol = msg.symbol.upper()
```

**Test**: `test_settrade_adapter.py::TestNormalization::test_symbol_uppercase_normalization`

**Examples**:
- `"aot"` → `"AOT"`
- `"Ptt"` → `"PTT"`
- `"AOT"` → `"AOT"` (no change)

---

## Price Normalization

### Negative Prices Allowed

**Rule**: Negative prices are **not rejected**.

**Test**: `test_settrade_adapter.py::TestNormalization::test_negative_price_supported`

**Rationale**: Edge cases (derivatives, corporate actions) may have negative prices.

### Zero Prices Allowed in Auction

**Rule**: Price = 0 is valid during ATO/ATC sessions.

**Test**: `test_settrade_adapter.py::TestNormalization::test_zero_price_in_auction`

**Rationale**: During auction, bid/ask prices are zero until matching.

---

## Volume Normalization

**Rule**: Volumes are non-negative integers.

Protobuf guarantees `int64` type, no additional validation needed.

---

## Flag Normalization

**Rule**: Flags are converted to int and validated against `BidAskFlag` enum.

```python
bid_flag: int = int(msg.bid_flag)
ask_flag: int = int(msg.ask_flag)
```

**Valid values**:
- `0` = UNDEFINED
- `1` = NORMAL
- `2` = ATO (At-The-Opening)
- `3` = ATC (At-The-Close)

---

## Timestamp Capture

**Rule**: Timestamps captured **immediately** on message receipt.

```python
recv_ts: int = time.time_ns()           # Wall clock
recv_mono_ns: int = time.perf_counter_ns()  # Monotonic
```

**Why two timestamps?**
- `recv_ts`: For correlation with external logs, exchange timestamps
- `recv_mono_ns`: For latency measurement (NTP-safe)

---

## Connection Epoch Stamping

**Rule**: Every event stamped with current `connection_epoch`.

```python
event = BestBidAsk.model_construct(
    # ...
    connection_epoch=self._mqtt_client.connection_epoch,
)
```

**Benefit**: Strategy can detect reconnects.

---

## What We Accept vs Reject

### Accepted

✅ Negative prices  
✅ Zero prices (in auction)  
✅ Lowercase symbols (normalized to uppercase)  
✅ Any int flag value (coerced to enum if valid)  
✅ Missing optional fields (protobuf defaults)  

### Rejected

❌ Malformed protobuf (parse exception)  
❌ Invalid Money structure (units/nanos mismatch)  
❌ Non-finite floats (`inf`, `nan`) from Pydantic validation  

---

## String-to-Int Coercion

**Rule**: BidAskFlag enum values are integers, but protobuf may provide enum name strings in some SDKs.

Our approach:
```python
bid_flag: int = int(msg.bid_flag)
```

This handles both integer and integer-like values.

---

## Implementation Reference

See [infra/settrade_adapter.py](../../infra/settrade_adapter.py):
- Symbol normalization: `.upper()` call
- Money conversion: `units + nanos * 1e-9`
- Flag conversion: `int(msg.bid_flag)`

---

## Test Coverage

- `test_settrade_adapter.py::TestNormalization::test_symbol_uppercase_normalization`
- `test_settrade_adapter.py::TestNormalization::test_negative_price_supported`
- `test_settrade_adapter.py::TestNormalization::test_zero_price_in_auction`
- `test_settrade_adapter.py::TestNormalization::test_money_conversion_accuracy`

---

## Edge Cases Handled

1. **Empty symbol**: Allowed (protobuf default), but unlikely in practice
2. **Extremely large volumes**: Handled by int64 range (up to 2^63-1)
3. **Float precision**: See [Money Precision Model](./money_precision_model.md)
4. **Auction periods**: Zero prices explicitly allowed

---

## Next Steps

- **[Parsing Pipeline](./parsing_pipeline.md)** — Protobuf parsing
- **[Money Precision Model](./money_precision_model.md)** — Float precision
- **[Error Isolation Model](./error_isolation_model.md)** — Error handling
