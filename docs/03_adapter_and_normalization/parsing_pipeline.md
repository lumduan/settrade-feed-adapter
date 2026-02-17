# Parsing Pipeline

Protobuf decoding, normalization, and event construction inside the
`BidOfferAdapter._on_message` hot path.

---

## End-to-End Flow

```text
protobuf bytes (from MQTT)
  |
  v
BidOfferV3().parse(payload)          # deserialize protobuf
  |
  v
validate / normalize fields          # uppercase symbol, Money -> float, int(flag)
  |
  v
BestBidAsk.model_construct(...)      # or FullBidOffer.model_construct(...)
  |                                  # (no Pydantic validation -- hot path)
  v
on_event(event)                      # EventCallback invoked
```

Every message that reaches `_on_message` follows this exact sequence.
Timestamps (`recv_ts`, `recv_mono_ns`) and the current `connection_epoch` are
captured **before** protobuf parsing begins, so they reflect true arrival time.

---

## Two Output Modes

The adapter produces one of two event types, controlled by
`BidOfferAdapterConfig.full_depth`:

| Config | Event type | Content |
| --- | --- | --- |
| `full_depth=False` (default) | `BestBidAsk` | Top-of-book: single bid/ask price, volume, flag |
| `full_depth=True` | `FullBidOffer` | 10-level book: tuples of 10 prices and 10 volumes per side |

The adapter constructor accepts `on_event: EventCallback` where
`EventCallback = Callable[[BidOfferEvent], None]` and
`BidOfferEvent = Union[BestBidAsk, FullBidOffer]`.

---

## Hot-Path Optimizations

### 1. `model_construct` Instead of Normal Construction

Normal Pydantic construction runs validators on every field:

```python
# Slow -- validators fire for every field
event = BestBidAsk(
    symbol=symbol,
    bid=bid,
    ask=ask,
    bid_vol=bid_vol,
    ask_vol=ask_vol,
    bid_flag=bid_flag,
    ask_flag=ask_flag,
    recv_ts=recv_ts,
    recv_mono_ns=recv_mono_ns,
    connection_epoch=connection_epoch,
)
```

The hot path uses `model_construct`, which skips all validation:

```python
# Fast -- no validation overhead
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
    connection_epoch=connection_epoch,
)
```

This is safe because the protobuf message is the trusted data source and the
adapter performs its own inline normalization before constructing the event.

### 2. Inline Money Conversion (No Function Call)

A utility function `money_to_float(money)` exists for tests and external code,
but the hot path does **not** call it. Instead it inlines the arithmetic:

```python
bid = msg.bid_price1.units + msg.bid_price1.nanos * 1e-9
```

This avoids function-call overhead on every price field.

### 3. Direct Protobuf Field Access (No `.to_dict()`)

The Settrade SDK example converts the protobuf to a dictionary first:

```python
# SDK approach -- allocates a dict
msg_dict = BidOfferV3().parse(payload).to_dict(casing=betterproto.Casing.SNAKE)
bid = msg_dict["bid_price1"]["units"] + ...
```

The adapter accesses fields directly on the protobuf object:

```python
# Our approach -- no dict allocation
msg = BidOfferV3().parse(payload)
bid = msg.bid_price1.units + msg.bid_price1.nanos * 1e-9
```

---

## BestBidAsk Parsing (Default Mode)

```python
msg = BidOfferV3().parse(payload)

event = BestBidAsk.model_construct(
    symbol=msg.symbol.upper(),
    bid=msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
    ask=msg.ask_price1.units + msg.ask_price1.nanos * 1e-9,
    bid_vol=msg.bid_volume1,
    ask_vol=msg.ask_volume1,
    bid_flag=int(msg.bid_flag),
    ask_flag=int(msg.ask_flag),
    recv_ts=recv_ts,
    recv_mono_ns=recv_mono_ns,
    connection_epoch=self._mqtt_client.reconnect_epoch,
)
```

This allocates only the protobuf parse result and the single frozen Pydantic
model -- no intermediate containers.

---

## FullBidOffer Parsing (full_depth=True)

When `full_depth=True`, the adapter builds tuples of 10 price levels per side.
All 10 levels are **explicitly unrolled** -- there is no loop, no `getattr`,
and no dynamic field-name construction:

```python
bid_prices = (
    msg.bid_price1.units  + msg.bid_price1.nanos  * 1e-9,
    msg.bid_price2.units  + msg.bid_price2.nanos  * 1e-9,
    msg.bid_price3.units  + msg.bid_price3.nanos  * 1e-9,
    msg.bid_price4.units  + msg.bid_price4.nanos  * 1e-9,
    msg.bid_price5.units  + msg.bid_price5.nanos  * 1e-9,
    msg.bid_price6.units  + msg.bid_price6.nanos  * 1e-9,
    msg.bid_price7.units  + msg.bid_price7.nanos  * 1e-9,
    msg.bid_price8.units  + msg.bid_price8.nanos  * 1e-9,
    msg.bid_price9.units  + msg.bid_price9.nanos  * 1e-9,
    msg.bid_price10.units + msg.bid_price10.nanos * 1e-9,
)

bid_volumes = (
    msg.bid_volume1,  msg.bid_volume2,  msg.bid_volume3,
    msg.bid_volume4,  msg.bid_volume5,  msg.bid_volume6,
    msg.bid_volume7,  msg.bid_volume8,  msg.bid_volume9,
    msg.bid_volume10,
)

# Same pattern for ask_prices and ask_volumes
```

### Performance Caveat

`FullBidOffer` allocates approximately **46 objects per message**:

- 4 tuples (bid_prices, ask_prices, bid_volumes, ask_volumes)
- 40 float/int values inside those tuples
- 1 FullBidOffer model instance
- 1 protobuf parse result

This is significantly heavier than `BestBidAsk`. Use `full_depth=True` only
when downstream logic genuinely needs the full order book.

---

## Counter Semantics

Every call to `_on_message` results in exactly **one** counter increment:

| Outcome | Counter incremented |
| --- | --- |
| Protobuf parsed and callback succeeded | `_messages_parsed` |
| Protobuf parse or normalization failed | `_parse_errors` |
| Parse succeeded but `on_event` callback raised | `_callback_errors` |

See [Error Isolation Model](./error_isolation_model.md) for details on the
two-phase error handling within `_on_message`.

---

## Implementation Reference

- Adapter: `infra/settrade_adapter.py` -- `BidOfferAdapter._on_message`,
  `_parse_best_bid_ask`, `_parse_full_bid_offer`
- Events: `core/events.py` -- `BestBidAsk`, `FullBidOffer`, `BidAskFlag`
- Utility: `money_to_float()` in `infra/settrade_adapter.py` (for tests; not
  used in the hot path)

---

## Related Documents

- [Normalization Contract](./normalization_contract.md) -- what values are accepted and rejected
- [Money Precision Model](./money_precision_model.md) -- float precision trade-offs
- [Error Isolation Model](./error_isolation_model.md) -- two-phase error handling
