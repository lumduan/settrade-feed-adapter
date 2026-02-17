# Money Precision Model

How the adapter converts Settrade protobuf `Money` values to Python floats,
and the precision guarantees that downstream code can rely on.

---

## Conversion Formula

Settrade encodes prices using a protobuf `Money` type with two fields:

```protobuf
message Money {
  int64 units = 1;   // whole-number part
  int32 nanos = 2;   // billionths (0 to 999_999_999)
}
```

The adapter converts this to a Python `float`:

```python
price = units + nanos * 1e-9
```

### Examples

```python
Money(units=25,  nanos=500_000_000)  # -> 25.5
Money(units=100, nanos=250_000_000)  # -> 100.25
Money(units=0,   nanos=10_000_000)   # -> 0.01
Money(units=-3,  nanos=0)            # -> -3.0  (negative prices allowed)
```

---

## Why `float`, Not `Decimal`

The hot path uses IEEE 754 `float` rather than `decimal.Decimal`.

| Aspect | `float` | `Decimal` |
| --- | --- | --- |
| Representation | 64-bit IEEE 754 binary | Arbitrary-precision decimal |
| Significant digits | 15--17 | Unlimited (configurable) |
| Memory | 8 bytes (C-level) | ~28 bytes + Python object overhead |
| Arithmetic speed | Native CPU instruction | Python-level software emulation |
| Allocation per price | None (unboxed in CPython for local vars) | 1 Python object |

In the hot path, `FullBidOffer` already allocates ~46 objects per message.
Using `Decimal` for 20 price fields would add 20 more allocations and convert
every arithmetic operation from a single CPU instruction into a Python method
call.

**Decision**: use `float` for speed.  Precision is more than sufficient for
market-data prices (see below).

---

## IEEE 754 Precision Analysis

A Python `float` (64-bit double) provides:

- **53 bits** of significand (mantissa)
- **15 to 17 significant decimal digits**

Thai equity prices typically range from 0.01 THB to ~10,000 THB with tick
sizes down to 0.01 THB. The worst-case scenario is a large `units` value
combined with a small `nanos` value:

```python
# Worst realistic case: units = 99_999, nanos = 1
price = 99_999 + 1 * 1e-9
# Stored as 99999.000000001
# float can represent this: 99999.00000000100...  (15+ digits available)
```

For any price under 1,000,000 with nano-second precision, IEEE 754 double has
no representable-number gap larger than ~1e-10. This is well below any
meaningful tick size.

---

## Downstream Comparison Contract

Because `float` arithmetic can introduce tiny rounding errors, downstream code
**must not** compare prices with `==`.

```python
# WRONG -- may fail due to floating-point representation
if event.bid == 125.75:
    ...

# CORRECT -- compare within tolerance
if abs(event.bid - 125.75) < 1e-9:
    ...
```

The recommended tolerance is `1e-9` (one nanosecond in Money terms). This
matches the resolution of the `nanos` field and is small enough that no two
distinct representable prices will fall within the tolerance of each other for
prices under 1,000,000.

---

## The `money_to_float` Utility

A standalone helper function is provided for tests and external code:

```python
def money_to_float(money) -> float:
    return money.units + money.nanos * 1e-9
```

This function is **not** called in the hot path.  The hot path inlines the
same arithmetic directly to avoid function-call overhead:

```python
bid = msg.bid_price1.units + msg.bid_price1.nanos * 1e-9
```

---

## When to Use `Decimal` Instead

Use `decimal.Decimal` when:

- Accumulating sums of many prices (rounding errors compound)
- Performing accounting or P&L calculations where exact decimal cents matter
- Displaying prices to end users where `125.7499999999` is unacceptable
- Regulatory requirements mandate exact decimal representation

For these cases, convert at the boundary:

```python
from decimal import Decimal

exact_bid = Decimal(str(event.bid))        # or
exact_bid = Decimal(event.bid).quantize(Decimal("0.01"))
```

---

## Precision Loss Scenarios

### Large Units + Tiny Nanos

```python
price = 1_000_000 + 1 * 1e-9
# -> 1000000.000000001  (representable, but at the edge of precision)
```

For units above ~10^7, the least-significant nanos digit may be lost.  This is
far beyond realistic Thai equity prices.

### Accumulated Arithmetic

```python
total = 0.0
for _ in range(1_000_000):
    total += 0.01
# -> 9999.999999999831  (off by ~1.7e-10)
```

Mitigation: do not accumulate float prices in tight loops.  Use integer
arithmetic (e.g., satang) or `Decimal` for aggregation.

---

## Implementation Reference

- Hot-path inline conversion: `infra/settrade_adapter.py` --
  `_parse_best_bid_ask`, `_parse_full_bid_offer`
- Utility function: `money_to_float()` in `infra/settrade_adapter.py`
- Event model price fields: `core/events.py` -- `bid: float`, `ask: float`,
  `bid_prices: tuple[float, ...]`, `ask_prices: tuple[float, ...]`

---

## Related Documents

- [Parsing Pipeline](./parsing_pipeline.md) -- where Money conversion happens in the flow
- [Normalization Contract](./normalization_contract.md) -- full list of accepted/rejected values
- [Error Isolation Model](./error_isolation_model.md) -- what happens if conversion fails
