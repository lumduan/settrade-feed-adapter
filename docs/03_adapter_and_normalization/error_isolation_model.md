# Error Isolation Model

How `BidOfferAdapter._on_message` isolates parse failures from callback
failures, counts them independently, and rate-limits log output.

---

## Two-Phase Error Isolation

`_on_message` is the MQTT message callback and runs on the **MQTT IO thread**.
It is structured as two distinct try/except phases so that a failure in one
phase never masks or double-counts a failure in the other.

```text
_on_message(topic, payload)
  |
  |-- capture recv_ts, recv_mono_ns, connection_epoch
  |
  |-- Phase 1: PARSE
  |     |  BidOfferV3().parse(payload)
  |     |  normalize fields
  |     |  model_construct -> event
  |     |
  |     +-- on exception -> _parse_errors += 1, log, RETURN
  |
  |-- Phase 2: CALLBACK
  |     |  on_event(event)
  |     |
  |     +-- on exception -> _callback_errors += 1, log, RETURN
  |
  +-- success -> _messages_parsed += 1
```

The key invariant: **exactly one counter is incremented per message**.

---

## Counter Definitions

| Counter | Incremented when | Meaning |
| --- | --- | --- |
| `_messages_parsed` | Both phases succeed | Message fully processed |
| `_parse_errors` | Phase 1 raises any exception | Protobuf decode or normalization failure |
| `_callback_errors` | Phase 1 succeeds, Phase 2 raises | User-supplied `on_event` callback failed |

### Exactly-One Guarantee

Tests verify that after processing a message, the sum of the three counters
increases by exactly one. There is no code path where zero counters or two
counters are incremented for the same message.

```python
before = adapter._messages_parsed + adapter._parse_errors + adapter._callback_errors
adapter._on_message(topic, payload)
after  = adapter._messages_parsed + adapter._parse_errors + adapter._callback_errors
assert after - before == 1
```

### Parse Errors vs Callback Errors

These are tracked separately because they have different root causes and
different remediation paths:

- **Parse errors** indicate a problem with the upstream data source (protocol
  change, corruption, unexpected protobuf schema). The adapter operator needs
  to investigate.
- **Callback errors** indicate a bug in the downstream consumer code (the
  `on_event` function). The strategy developer needs to fix their callback.

---

## Rate-Limited Logging

To prevent log storms when errors occur in bursts (e.g., a corrupted feed
sending thousands of bad messages per second), the adapter uses a two-tier
logging strategy:

### Tier 1: First N Errors (Full Stack Trace)

The first `_LOG_FIRST_N = 10` errors of each type are logged with
`logger.exception(...)`, which includes the full Python stack trace. This
gives developers maximum diagnostic information for the initial occurrences.

### Tier 2: Every Nth Error (Summary Only)

After the first 10 errors, only every `_LOG_EVERY_N = 1000`th error is logged,
at `ERROR` level without a stack trace. This ensures that ongoing problems
remain visible in logs without flooding them.

```text
Error #1   -> logger.exception("...")   # full traceback
Error #2   -> logger.exception("...")   # full traceback
  ...
Error #10  -> logger.exception("...")   # full traceback
Error #11  -> (silent)
  ...
Error #999 -> (silent)
Error #1000 -> logger.error("...")      # summary, no traceback
Error #1001 -> (silent)
  ...
Error #2000 -> logger.error("...")      # summary, no traceback
```

This pattern applies independently to parse errors and callback errors.

---

## Thread Ownership

`_on_message` runs on the **MQTT IO thread** -- the same thread that the
underlying MQTT client uses to deliver incoming messages. This means:

1. `_on_message` must not block for extended periods, or it will stall all
   MQTT message delivery.
2. The counters (`_messages_parsed`, `_parse_errors`, `_callback_errors`) are
   accessed from the IO thread during message processing.
3. The `stats()` method returns a dictionary snapshot of all counters and is
   **thread-safe via locks**, so it can be called from any thread (e.g., a
   monitoring thread or the main thread) without races.

---

## The `stats()` Method

```python
stats = adapter.stats()
# Returns a dict like:
# {
#     "messages_parsed": 14523,
#     "parse_errors": 0,
#     "callback_errors": 2,
#     ...
# }
```

The returned dictionary is a snapshot -- reading it does not block message
processing for more than the time it takes to copy a few integers under a lock.

### Monitoring Guidance

| Condition | Severity | Likely cause |
| --- | --- | --- |
| `parse_errors > 0` | Warning | Upstream protocol change or data corruption |
| `parse_errors` increasing rapidly | Critical | Feed is broken; all messages failing |
| `callback_errors > 0` | Warning | Bug in consumer's `on_event` callback |
| `callback_errors == messages_parsed` | Critical | Callback is fundamentally broken |

---

## Error Types by Phase

### Phase 1 Failures (Parse)

- Malformed protobuf payload (truncated, wrong schema)
- Unexpected field types in the protobuf message
- Any exception during Money-to-float conversion or field extraction

All caught by the Phase 1 `except Exception` block.

### Phase 2 Failures (Callback)

- Unhandled exception in the user-supplied `on_event` function
- For example: `TypeError`, `KeyError`, `AttributeError` in strategy code

All caught by the Phase 2 `except Exception` block.

### Neither Phase (Not Caught Here)

- `KeyboardInterrupt` and `SystemExit` are **not** caught (they inherit from
  `BaseException`, not `Exception`) and will propagate up to terminate the
  process as expected.

---

## Design Rationale

### Why Two Phases Instead of One Try/Except?

A single `try/except` around both parse and callback would make it impossible
to distinguish between adapter bugs (parse) and consumer bugs (callback). The
two-phase design gives operators actionable counters.

### Why Not Re-raise After Counting?

Re-raising would crash the MQTT IO thread, which would halt all message
delivery for every subscribed symbol. The adapter is designed to be resilient:
log, count, and continue.

### Why Rate-Limit Logging?

At 1,000 messages/second, logging every error with a full stack trace would
generate gigabytes of logs per hour and potentially cause the logging
subsystem itself to become a bottleneck. The two-tier approach -- 10 full
traces followed by every 1000th as a summary -- balances diagnostics against
operational safety.

---

## Implementation Reference

- Two-phase handler: `BidOfferAdapter._on_message` in
  `infra/settrade_adapter.py`
- Rate-limit constants: `_LOG_FIRST_N = 10`, `_LOG_EVERY_N = 1000` in
  `infra/settrade_adapter.py`
- Stats method: `BidOfferAdapter.stats()` in `infra/settrade_adapter.py`
- Event models: `core/events.py`

---

## Related Documents

- [Parsing Pipeline](./parsing_pipeline.md) -- the full message processing flow
- [Normalization Contract](./normalization_contract.md) -- what values trigger vs pass validation
- [Money Precision Model](./money_precision_model.md) -- float conversion details
