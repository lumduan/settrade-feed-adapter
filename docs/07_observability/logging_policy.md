# Logging Policy

How the feed adapter uses Python stdlib logging, including rate limiting in
hot paths.

---

## Overview

All logging uses Python's standard `logging` module. Each module creates its
own logger via `logging.getLogger(__name__)`. No third-party logging libraries
are used.

---

## Rate-Limited Hot-Path Logging

The adapter's `_on_message()` callback runs inline in the MQTT IO thread at
high message rates. Logging every error at full verbosity would cause log
storms that overwhelm the system. The adapter uses a two-tier rate-limiting
strategy.

### Tier 1: First 10 Errors (Full Stack Trace)

The first 10 errors of each type (`parse_errors`, `callback_errors`) are
logged with `logger.exception()`, which includes the full stack trace. This
provides detailed diagnostics for initial debugging.

```python
# From infra/settrade_adapter.py
_LOG_FIRST_N: int = 10

if count <= _LOG_FIRST_N:
    logger.exception(
        "Failed to parse BidOfferV3 on %s (%d/%d)",
        topic, count, _LOG_FIRST_N,
    )
```

### Tier 2: Every 1000th Error (Summary Only)

After the first 10 errors, only every 1000th occurrence is logged at
`logger.error()` level (no stack trace). This keeps the log volume bounded
while still providing ongoing visibility.

```python
_LOG_EVERY_N: int = 1000

elif count % _LOG_EVERY_N == 0:
    logger.error(
        "Parse errors ongoing: %d total (topic=%s)",
        count, topic,
    )
```

### Error Isolation

Parse errors and callback errors have separate counters and separate logging
methods (`_log_parse_error`, `_log_callback_error`). A parse failure does not
increment the callback error counter, and vice versa.

---

## Dispatcher Logging

The dispatcher logs at two key points in the push path:

### Drop Rate Warning

When the EMA-smoothed drop rate crosses the configured
`drop_warning_threshold` (default 0.01 = 1%), a single warning is logged.
The warning is not repeated until the rate recovers and crosses the threshold
again.

```python
# From core/dispatcher.py
if self._drop_rate_ema > self._drop_warning_threshold:
    if not self._warned_drop_rate:
        logger.warning(
            "Drop rate EMA %.4f exceeds threshold %.4f",
            self._drop_rate_ema, self._drop_warning_threshold,
        )
        self._warned_drop_rate = True
```

### Drop Rate Recovery

When the EMA drops back below the threshold after a warning was issued, an
info-level log records the recovery:

```python
elif self._warned_drop_rate:
    logger.info(
        "Drop rate EMA %.4f recovered below threshold %.4f",
        self._drop_rate_ema, self._drop_warning_threshold,
    )
    self._warned_drop_rate = False
```

### Lifecycle Events

- `Dispatcher created with maxlen=%d` -- INFO at construction
- `Dispatcher clearing %d remaining events` -- WARNING when `clear()` discards events
- `Dispatcher cleared: queue and counters reset` -- INFO after `clear()`

---

## Transport (MQTT) Logging

### Connection Events

- `Authenticated with Settrade API (broker=%s)` -- INFO after successful login
- `Fetched MQTT host: %s` -- INFO after dispatcher token fetch
- `MQTT client started, connecting to %s:%d` -- INFO at initial connect
- `Connected to MQTT broker at %s` -- INFO on successful `on_connect`
- `Replayed subscription: %s` -- INFO for each topic replayed on reconnect
- `Reconnect epoch incremented to %d` -- INFO after reconnect subscription replay

### Disconnection Events

- `Disconnected from MQTT broker (clean)` -- INFO for clean disconnect (rc=0)
- `Unexpected MQTT disconnect (rc=%d)` -- WARNING for unexpected disconnect

### Reconnection Events

- `Reconnect attempt (delay=%.1fs)` -- INFO at each reconnect attempt
- `Reconnect TCP success (total=%d, gen=%d)` -- INFO on successful TCP reconnect
- `Reconnect attempt failed` -- logged with `logger.exception()` (full trace)

### Token Refresh

- `Token near expiry, triggering controlled reconnect` -- INFO when refresh is needed

### Shutdown Summary

- `Shutting down MQTT client` -- INFO at shutdown start
- `MQTT client shut down (messages=%d, errors=%d, reconnects=%d)` -- INFO summary at shutdown with lifetime counters

### Subscription Events

- `Subscribed to topic: %s` -- INFO when MQTT subscribe is sent
- `Unsubscribed from topic: %s` -- INFO when MQTT unsubscribe is sent

---

## Adapter Logging

- `BidOfferAdapter subscribed to %s` -- INFO on symbol subscription
- `BidOfferAdapter unsubscribed from %s` -- INFO on symbol unsubscription
- `BidOfferAdapter already subscribed to %s, skipping` -- DEBUG for duplicate subscription

---

## Logger Names

Each module uses `logging.getLogger(__name__)`, producing these logger names:

| Logger Name | Module |
| --- | --- |
| `core.dispatcher` | Dispatcher queue and EMA |
| `infra.settrade_mqtt` | MQTT transport, reconnect, token refresh |
| `infra.settrade_adapter` | BidOffer adapter, parse/callback errors |

---

## Configuration Recommendations

```python
import logging

# See all feed adapter logs
logging.basicConfig(level=logging.INFO)

# Silence dispatcher EMA warnings during testing
logging.getLogger("core.dispatcher").setLevel(logging.ERROR)

# Debug MQTT connection issues
logging.getLogger("infra.settrade_mqtt").setLevel(logging.DEBUG)
```

---

## Related Pages

- [Metrics Reference](./metrics_reference.md) -- numeric counters and gauges
- [Failure Playbook](../09_production_guide/failure_playbook.md) -- interpreting error logs
