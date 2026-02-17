# Timestamp and Epoch

Every event carries two timestamps and a connection epoch. This page
explains their semantics and correct usage.

---

## Dual Timestamps

Both `BestBidAsk` and `FullBidOffer` include two timestamp fields,
captured at the moment the MQTT message is received by the adapter:

| Field | Source | Purpose |
| --- | --- | --- |
| `recv_ts` | `time.time_ns()` | Wall-clock time for external correlation |
| `recv_mono_ns` | `time.perf_counter_ns()` | Monotonic time for latency measurement |

Both fields are non-negative integers (`ge=0`). Negative values are
rejected during validated construction.

---

## recv_ts -- Wall-Clock Timestamp

`recv_ts` records nanoseconds since the Unix epoch using `time.time_ns()`.

**Use for:**

- Correlating with exchange timestamps or external log files
- Converting to human-readable datetime
- Absolute time reference

**Do NOT use for latency measurement.** The wall clock is synchronized
via NTP and can jump forward or backward when the system clock is
adjusted. Two consecutive `recv_ts` values may differ by a negative
amount after an NTP correction:

```python
# WRONG -- latency can be negative after NTP adjustment
latency = event_b.recv_ts - event_a.recv_ts
```

**Converting to datetime:**

```python
from datetime import datetime, timezone

ts_seconds = event.recv_ts / 1_000_000_000
dt = datetime.fromtimestamp(ts_seconds, tz=timezone.utc)
```

---

## recv_mono_ns -- Monotonic Timestamp

`recv_mono_ns` records nanoseconds from an arbitrary origin using
`time.perf_counter_ns()`.

**Use for:**

- Measuring processing latency (time from event receipt to action)
- Computing elapsed time between events
- Performance profiling

**Guarantees:**

- Never goes backwards, even across NTP adjustments
- High resolution (sub-microsecond on most platforms)
- Immune to wall-clock corrections

**Not suitable for:**

- Human-readable time display (the origin is arbitrary)
- Cross-process comparison (each process has its own monotonic origin)

**Measuring processing latency:**

```python
import time

def on_event(event):
    # ... process the event ...
    now = time.perf_counter_ns()
    latency_us = (now - event.recv_mono_ns) / 1_000
    print(f"Processing latency: {latency_us:.1f} us")
```

---

## When to Use Which Timestamp

| Use case | recv_ts | recv_mono_ns |
| --- | --- | --- |
| Log correlation | Yes | No |
| Human-readable time | Yes | No |
| Compare with exchange time | Yes | No |
| Latency measurement | No -- NTP can cause jumps | Yes |
| Elapsed time between events | No | Yes |
| Performance profiling | No | Yes |

The rule is straightforward: use `recv_ts` to answer "when did this
happen?" and `recv_mono_ns` to answer "how long did this take?"

---

## Validation

Both timestamps must be non-negative. Negative values are rejected
during regular construction:

```python
BestBidAsk(..., recv_ts=-1, ...)         # raises ValidationError
BestBidAsk(..., recv_mono_ns=-1, ...)    # raises ValidationError
```

Large values are accepted without issue. A nanosecond timestamp for
the year 2100 fits comfortably in a Python `int`:

```python
BestBidAsk(..., recv_ts=4_102_444_800_000_000_000, ...)   # valid
```

---

## connection_epoch

The `connection_epoch` field tracks MQTT connection sessions.

| Property | Value |
| --- | --- |
| Type | `int` |
| Default | `0` |
| Constraint | `ge=0` (negative rejected) |
| Initial connection | `0` |
| After reconnect | incremented by the adapter |

### Semantics

- **Epoch 0** is the initial connection established at startup.
- When the MQTT connection drops and the adapter reconnects, it
  increments the epoch and replays subscriptions. All events from the
  new connection carry the new epoch value.
- The epoch never decreases within a single adapter process lifetime.

### Detecting Reconnects in Strategy Code

A strategy can detect that a reconnect occurred by comparing the
event's epoch against the last seen epoch:

```python
last_epoch = 0

def on_event(event):
    global last_epoch
    if event.connection_epoch != last_epoch:
        # Reconnect detected -- new connection session
        clear_cached_book()
        cancel_pending_orders()
        last_epoch = event.connection_epoch
    process(event)
```

### Why Reconnects Matter

After a reconnect, the adapter replays subscriptions and begins
receiving data on a fresh MQTT session. Strategy code should treat
a new epoch as a signal to:

- Clear any cached market data (order books, last-trade state)
- Invalidate derived calculations that depend on continuity
- Re-evaluate open positions against fresh data

Failing to reset state on reconnect can cause a strategy to act on
stale prices from the previous connection session.

### Negative Epoch Rejected

A negative `connection_epoch` is rejected during validated construction:

```python
BestBidAsk(..., connection_epoch=-1)   # raises ValidationError
```

With `model_construct()`, no validation runs, so the adapter must ensure
it never passes a negative value.

---

## Related Pages

- [Event Contract](./event_contract.md) -- shared model guarantees
- [BestBidAsk](./best_bid_ask.md) -- top-of-book field reference
- [FullBidOffer](./full_bid_offer.md) -- full 10-level depth field reference
