# Failure Scenarios

How the feed adapter handles errors at each layer.

---

## Overview

The feed adapter is designed so that no single failure takes down the system.
Each error is counted, logged (with rate limiting), and isolated so that
processing continues for subsequent messages.

---

## Parse Error

**Trigger:** Corrupted or incompatible protobuf payload arrives on a
BidOfferV3 topic.

**Behavior:**

1. `BidOfferV3().parse(payload)` raises an exception.
2. `_parse_errors` counter increments by 1.
3. Error is logged via `_log_parse_error()` (rate-limited: first 10 with full
   stack trace via `logger.exception()`, then every 1000th at `logger.error()`).
4. The `_on_message` callback returns early.
5. `_callback_errors` is NOT incremented.
6. `_messages_parsed` is NOT incremented.
7. The adapter continues processing the next message.

**Source:** `infra/settrade_adapter.py` -- `_on_message()` lines 392-395

---

## Callback Error

**Trigger:** The downstream `on_event` callback raises an exception (e.g.,
dispatcher is full, strategy code throws).

**Behavior:**

1. Protobuf parsing succeeds (event is constructed).
2. `self._on_event(event)` raises an exception.
3. `_callback_errors` counter increments by 1.
4. Error is logged via `_log_callback_error()` (same rate-limiting as parse errors).
5. The `_on_message` callback returns early.
6. `_parse_errors` is NOT incremented.
7. `_messages_parsed` is NOT incremented.
8. The adapter continues processing the next message.

**Source:** `infra/settrade_adapter.py` -- `_on_message()` lines 400-403

---

## Reconnect on Unexpected Disconnect

**Trigger:** MQTT broker drops the connection unexpectedly (`on_disconnect`
with `rc != 0`).

**Behavior:**

1. `_last_disconnect_ts` is recorded.
2. If state is `SHUTDOWN`, the disconnect is ignored.
3. Otherwise, `_schedule_reconnect()` is called.
4. The `_reconnecting` flag under `_reconnect_lock` prevents duplicate
   reconnect threads.
5. `_reconnect_loop()` runs in a daemon thread with exponential backoff:
   - Starts at `reconnect_min_delay` (default 1.0s).
   - Doubles each failed attempt up to `reconnect_max_delay` (default 30.0s).
   - Each delay is jittered by +/-20% (`random.uniform(0.8, 1.2)`).
6. Each attempt: fetches fresh host/token, creates a new MQTT client (new
   generation), and calls `connect()`.
7. On TCP success, `_reconnect_count` increments and the loop exits.
8. State transitions to `CONNECTED` only in `on_connect` (not in the
   reconnect loop), ensuring MQTT-level authentication is confirmed.
9. After `on_connect` with `rc=0`, all subscriptions are replayed from the
   source-of-truth dictionary.
10. `_reconnect_epoch` increments by 1 after subscription replay (not on
    initial connection).

**Source:** `infra/settrade_mqtt.py` -- `_schedule_reconnect()`, `_reconnect_loop()`

---

## Token Refresh

**Trigger:** The access token is approaching expiry (within
`token_refresh_before_exp_seconds`, default 100 seconds).

**Behavior:**

1. The `_token_refresh_check` background thread detects the upcoming expiry.
2. It calls `_schedule_reconnect()` to trigger a controlled reconnect.
3. The reconnect flow fetches a fresh token via `_fetch_host_token()` (which
   calls the SDK's `dispatch()`, auto-refreshing the access token).
4. A new MQTT client is created with fresh WebSocket headers containing the
   new token.
5. The controlled reconnect may cause brief downtime (~1-3s).

This uses the same reconnect guard as disconnect-triggered reconnects,
preventing dual reconnect flows if a timer and network drop coincide.

**Source:** `infra/settrade_mqtt.py` -- `_token_refresh_check()`

---

## Feed Silence

**Trigger:** The MQTT connection is alive but no messages arrive (silent
broker, exchange closed, subscription lost).

**Behavior:**

1. `FeedHealthMonitor.on_event()` stops being called.
2. `is_feed_dead()` returns `True` once the gap exceeds `max_gap_seconds`.
3. `stale_symbols()` returns symbols whose individual gaps exceed their
   thresholds.
4. Strategy code can detect this and take action (pause trading, alert ops).

The feed health monitor does not trigger reconnects automatically. It is a
diagnostic tool -- the strategy decides what action to take.

**Source:** `core/feed_health.py` -- `is_feed_dead()`, `stale_symbols()`

---

## Queue Overflow (Drop Burst)

**Trigger:** Events arrive faster than the strategy can consume them (the
queue fills to `maxlen`).

**Behavior:**

1. `Dispatcher.push()` checks `len(queue) == maxlen`.
2. If full, `_total_dropped` increments by 1.
3. `deque.append()` evicts the oldest item automatically (drop-oldest policy).
4. The new event is inserted at the tail.
5. The EMA drop rate is updated: `ema = alpha * 1.0 + (1 - alpha) * ema`.
6. If the EMA crosses `drop_warning_threshold` (default 0.01), a warning is
   logged once.
7. When the EMA recovers below the threshold, an info-level recovery is logged.

Stale market data is worthless -- new data always wins. The drop-oldest policy
ensures the consumer always sees the freshest data.

**Source:** `core/dispatcher.py` -- `push()`

---

## Shutdown During Reconnect

**Trigger:** `shutdown()` is called while `_reconnect_loop()` is running.

**Behavior:**

1. `shutdown()` sets state to `SHUTDOWN` under `_state_lock`.
2. `_shutdown_event.set()` signals all background threads.
3. `_reconnect_loop()` checks `self._shutdown_event.is_set()` at the top of
   each retry iteration and exits the loop.
4. The `finally` block clears `_reconnecting = False`.
5. `shutdown()` stops the MQTT IO loop and disconnects.

The reconnect loop uses `_shutdown_event.wait(timeout=delay)` instead of
`time.sleep()`, so it responds immediately to shutdown signals without
waiting for the backoff delay to expire.

**Source:** `infra/settrade_mqtt.py` -- `_reconnect_loop()`, `shutdown()`

---

## MQTT Connection Failure (on_connect with rc != 0)

**Trigger:** MQTT-level authentication fails after TCP connect succeeds.

**Behavior:**

1. `on_connect` fires with `rc != 0`.
2. State does NOT transition to `CONNECTED`.
3. `_schedule_reconnect()` is called to retry with fresh credentials.

This is why state transitions to `CONNECTED` only happen in `on_connect` --
TCP connect success does not guarantee MQTT-level authentication success.

**Source:** `infra/settrade_mqtt.py` -- `_on_connect()`

---

## Summary Table

| Failure | Detection | Recovery | Continues? |
| --- | --- | --- | --- |
| Parse error | `parse_errors` counter | Rate-limited logging | Yes |
| Callback error | `callback_errors` counter | Rate-limited logging | Yes |
| Unexpected disconnect | `on_disconnect` callback | Auto-reconnect with backoff | Yes |
| Token expiry | Background timer | Controlled reconnect | Yes |
| Feed silence | `is_feed_dead()` | Strategy-driven | Yes |
| Queue overflow | `total_dropped`, `drop_rate_ema` | Drop-oldest, EMA warning | Yes |
| Shutdown during reconnect | `_shutdown_event` | Clean exit | Terminal |

---

## Related Pages

- [Invariants Defined by Tests](./invariants_defined_by_tests.md) -- tested guarantees
- [Concurrency Guarantees](./concurrency_guarantees.md) -- thread safety
- [Failure Playbook](../09_production_guide/failure_playbook.md) -- operational response
