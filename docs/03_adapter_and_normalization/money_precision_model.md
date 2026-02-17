# Money Precision Model

Float precision contract for price representation.

---

## Money to Float Conversion

Settrade protobuf uses `Money` type:

```protobuf
message Money {
  int64 units = 1;  // Whole units
  int32 nanos = 2;  // Nanoseconds (billionths)
}
```

**Conversion**:
```python
price: float = money.units + money.nanos * 1e-9
```

---

## Precision Guarantees

### IEEE 754 Double (Python float)

- **Significand**: 53 bits (15-17 decimal digits)
- **Range**: ~±10^308
- **Smallest positive**: ~10^-324

### Typical Stock Prices

Thai stock prices typically range:
- **Min**: 0.01 THB
- **Max**: ~10,000 THB
- **Precision**: 0.01 THB (2 decimal places)

**Conclusion**: IEEE 754 double provides **more than sufficient** precision.

---

## Precision Example

```python
# Example: 125.75 THB
money = Money(units=125, nanos=750_000_000)
price = 125 + 750_000_000 * 1e-9
# → 125.75 (exact)

# Example: 25.50 THB
money = Money(units=25, nanos=500_000_000)
price = 25 + 500_000_000 * 1e-9
# → 25.5 (exact)
```

---

## Float Comparison Contract

**NEVER compare floats with `==`**:

```python
# ❌ BAD
if event.bid == 125.75:
    pass

# ✅ GOOD
if abs(event.bid - 125.75) < 1e-9:
    pass
```

**Rationale**: IEEE 754 arithmetic may introduce tiny errors (e.g., `0.1 + 0.2 = 0.30000000000000004`).

---

## Why Not Decimal?

The official SDK uses `Decimal` for exact decimal arithmetic:

```python
from decimal import Decimal

price = Decimal(units) + Decimal(nanos) / Decimal("1_000_000_000")
```

**Trade-offs**:

| Aspect | float | Decimal |
|--------|-------|---------|
| **Speed** | Fast (native CPU) | Slow (Python object) |
| **Precision** | 53-bit binary | Arbitrary decimal |
| **Memory** | 8 bytes | ~28 bytes + overhead |
| **Use Case** | Real-time trading | Accounting, auditing |

**Decision**: Use `float` for **speed** in hot path. Precision is sufficient for market data.

---

## Precision Loss Scenarios

### Scenario 1: Very Large + Very Small

```python
# Large price + tiny adjustment
price = 1_000_000.0 + 0.0000000001
# → 1000000.0 (tiny value lost)
```

**Impact**: Negligible for stock prices (max ~10,000 THB).

### Scenario 2: Repeated Operations

```python
total = 0.0
for _ in range(1_000_000):
    total += 0.01
# → 9999.999999999831 (accumulates rounding error)
```

**Mitigation**: Avoid accumulating float arithmetic. Use integer arithmetic when possible.

---

## Best Practices

1. **Compare with tolerance**:
   ```python
   if abs(price1 - price2) < 1e-9:
       # Equal within tolerance
   ```

2. **Format for display**:
   ```python
   print(f"{event.bid:.2f}")  # → "125.75"
   ```

3. **Avoid accumulation**:
   ```python
   # ❌ BAD
   total = sum(event.bid for event in events)
   
   # ✅ BETTER (if precision critical)
   from decimal import Decimal
   total = sum(Decimal(str(event.bid)) for event in events)
   ```

4. **Document assumptions**:
   ```python
   # Assumes prices < 100,000 THB and precision 0.01 THB
   ```

---

## Test Coverage

- `test_settrade_adapter.py::TestNormalization::test_money_conversion_accuracy`

**Test verifies**:
```python
assert abs(result - expected) < 1e-9
```

---

## When to Use Decimal

Use `Decimal` if:
- Auditing or accounting (exact decimal required)
- Regulatory requirements (financial precision)
- Accumulating thousands of small values
- Displaying to end users (exact cents)

**Not needed** for:
- Real-time market data ingestion
- Latency-sensitive hot paths
- Internal calculations with tolerance

---

## Implementation Reference

See [infra/settrade_adapter.py](../../infra/settrade_adapter.py):
- Money conversion in `_on_raw_message()`

See [core/events.py](../../core/events.py):
- Event models use `float` for prices

---

## Summary

✅ **float is sufficient** for Thai stock price range  
✅ **Faster than Decimal** (native CPU operations)  
✅ **15-17 decimal digits precision** (more than needed)  
⚠️ **Use tolerance for comparisons** (`abs(a - b) < 1e-9`)  
⚠️ **Avoid accumulation** of many small floats  

---

## Next Steps

- **[Parsing Pipeline](./parsing_pipeline.md)** — Protobuf parsing
- **[Normalization Contract](./normalization_contract.md)** — Data rules
- **[Event Contract](../04_event_models/event_contract.md)** — Event models
