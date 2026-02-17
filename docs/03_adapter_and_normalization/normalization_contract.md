# Normalization Contract

Rules that define what the adapter accepts, rejects, and transforms before
constructing event objects.

---

## Symbol Normalization

All symbols are normalized to **uppercase** at the point of subscription and
inside the parsing path.

### On Subscribe / Unsubscribe

`subscribe(symbol)` and `unsubscribe(symbol)` both call `.upper()` before any
further processing. This means:

```python
adapter.subscribe("aot")   # stored as "AOT", topic = "proto/topic/bidofferv3/AOT"
adapter.subscribe("AOT")   # duplicate -- silently skipped
adapter.subscribe("Ptt")   # stored as "PTT"
```

### Duplicate Subscription Handling

If the normalized symbol is already in the subscribed set, the call is
**silently skipped** -- no error, no second MQTT subscription. The
`subscribed_symbols` property returns a `frozenset[str]` of currently active
symbols.

### MQTT Topic Format

The topic is built from the uppercase symbol:

```text
proto/topic/bidofferv3/{SYMBOL}
```

For example, subscribing to `"aot"` produces the topic
`proto/topic/bidofferv3/AOT`.

---

## What We Accept

### Lowercase Symbols

Converted to uppercase automatically. Tests confirm `"aot"` becomes `"AOT"`.

### Negative Prices

Negative bid and ask prices are **allowed**. There is no lower-bound constraint
on the `bid` or `ask` float fields in either `BestBidAsk` or `FullBidOffer`.

Rationale: derivatives and corporate-action edge cases can produce negative
indicative prices.

### Zero Prices During Auction

A price of `0.0` is valid. During ATO (At-The-Opening) and ATC
(At-The-Close) sessions, bid and ask prices may be zero until matching occurs.

### Bid Greater Than Ask

The adapter does **not** enforce `bid <= ask`. Crossed or locked books are
passed through to the callback without modification.

### String-to-Int Coercion

Pydantic's coercion allows string representations of integers (e.g., `"1"`) to
be accepted for integer fields. This is verified by tests.

### Negative Volumes in FullBidOffer

`FullBidOffer` does **not** enforce `ge=0` on its volume tuples. Negative
volumes at individual levels are passed through.

---

## What We Reject

### Empty Symbols

`BestBidAsk` and `FullBidOffer` both declare `symbol` with `min_length=1`.
An empty string will fail Pydantic validation. (Note: `model_construct` in the
hot path skips this check, so the protobuf source is trusted to provide a
non-empty symbol.)

### Negative Volumes on BestBidAsk

`BestBidAsk.bid_vol` and `BestBidAsk.ask_vol` are declared with `ge=0`.
A negative volume will raise a validation error through normal Pydantic
construction. In the hot path (`model_construct`), this constraint is not
enforced -- the protobuf source is trusted.

### Negative Timestamps

`recv_ts` and `recv_mono_ns` are declared with `ge=0` on both event types.

### Extra Fields

Both `BestBidAsk` and `FullBidOffer` are configured with `extra="forbid"`.
Passing an unexpected field name through normal construction raises a
validation error.

---

## Flag Normalization

`BidAskFlag` is an `IntEnum` with four defined values:

| Value | Name | Meaning |
| --- | --- | --- |
| 0 | UNDEFINED | No session information |
| 1 | NORMAL | Continuous trading session |
| 2 | ATO | At-The-Opening auction |
| 3 | ATC | At-The-Close auction |

The adapter converts the protobuf enum to a Python int:

```python
bid_flag = int(msg.bid_flag)
ask_flag = int(msg.ask_flag)
```

### Auction Detection

Both `BestBidAsk` and `FullBidOffer` expose an `is_auction()` method that
returns `True` when either flag is `ATO` or `ATC`. This delegates to a shared
`_is_auction()` helper in `core/events.py`.

---

## Timestamp Capture

Two timestamps are captured at the very top of `_on_message`, **before** any
parsing work:

```python
recv_ts = time.time_ns()              # wall-clock nanoseconds
recv_mono_ns = time.perf_counter_ns() # monotonic nanoseconds
```

Additionally, the current MQTT reconnect epoch is read:

```python
connection_epoch = self._mqtt_client.reconnect_epoch
```

| Field | Clock | Purpose |
| --- | --- | --- |
| `recv_ts` | `time.time_ns()` | Correlation with exchange timestamps and external logs |
| `recv_mono_ns` | `time.perf_counter_ns()` | Latency measurement (immune to NTP adjustments) |
| `connection_epoch` | Adapter-managed counter | Detect whether events span a reconnect boundary |

---

## Frozen Models

Both event types are declared with `frozen=True`. Once constructed, no field
can be mutated. This is important for thread safety since events may be read
from a different thread than the MQTT IO thread that created them.

---

## Summary Table

| Input condition | Behavior |
| --- | --- |
| Lowercase symbol | Converted to uppercase |
| Duplicate subscription | Silently skipped |
| Negative price | Allowed (no lower bound) |
| Zero price | Allowed (ATO/ATC) |
| Bid > Ask | Allowed (crossed book) |
| String-valued integer field | Coerced to int by Pydantic |
| Empty symbol | Rejected (`min_length=1`) |
| Negative volume (BestBidAsk) | Rejected (`ge=0`) |
| Negative volume (FullBidOffer) | Allowed |
| Negative timestamp | Rejected (`ge=0`) |
| Extra fields on event | Rejected (`extra="forbid"`) |

---

## Implementation Reference

- Symbol normalization: `BidOfferAdapter.subscribe` / `unsubscribe` in
  `infra/settrade_adapter.py`
- Flag conversion: `int(msg.bid_flag)` inside `_parse_best_bid_ask` /
  `_parse_full_bid_offer`
- Event models: `core/events.py` -- `BestBidAsk`, `FullBidOffer`, `BidAskFlag`

---

## Related Documents

- [Parsing Pipeline](./parsing_pipeline.md) -- how bytes become events
- [Money Precision Model](./money_precision_model.md) -- float precision trade-offs
- [Error Isolation Model](./error_isolation_model.md) -- what happens when normalization fails
