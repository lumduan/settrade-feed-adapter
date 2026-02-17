# Failure Playbook

Operational troubleshooting guide for production issues.

---

## Drop Rate Rising

**Symptom:** `dispatcher.health().drop_rate_ema` is above threshold, warning
log appears: `Drop rate EMA X.XXXX exceeds threshold X.XXXX`.

**Diagnosis:**

1. Check `dispatcher.stats().queue_len` -- is the queue consistently full?
2. Check consumer throughput -- is the strategy processing events fast enough?
3. Check message rate -- has the market entered a high-activity period?

**Actions:**

- **Immediate:** Increase `maxlen` in `DispatcherConfig` to absorb bursts.
- **Short-term:** Optimize strategy processing time to consume events faster.
- **Long-term:** Profile the strategy loop to find bottlenecks. Consider
  reducing subscribed symbols or switching from `FullBidOffer` to `BestBidAsk`.

**Metrics to watch:**

```python
health = dispatcher.health()
health.drop_rate_ema        # should trend toward 0
health.queue_utilization    # should stay below 0.8

stats = dispatcher.stats()
stats.total_dropped         # cumulative drops
stats.queue_len             # current depth
```

---

## Feed Declared Dead

**Symptom:** `monitor.is_feed_dead()` returns `True`.

**Diagnosis:**

1. Check `mqtt_client.stats()["state"]` -- is the client still `CONNECTED`?
2. Check `mqtt_client.stats()["messages_received"]` -- is the count still
   increasing?
3. Check market hours -- is the exchange currently open?
4. Check `adapter.subscribed_symbols` -- are symbols still subscribed?

**Actions:**

- **If state is not CONNECTED:** Wait for auto-reconnect. Check
  `reconnect_count` to confirm reconnect attempts are happening.
- **If state is CONNECTED but no messages:** Verify subscriptions are active.
  Check if the exchange is in a halt or non-trading period.
- **If market is closed:** Increase `max_gap_seconds` for after-hours
  monitoring, or accept that `is_feed_dead()` will return `True` outside
  trading hours.

**Metrics to watch:**

```python
mqtt_stats = mqtt_client.stats()
mqtt_stats["state"]              # should be "CONNECTED"
mqtt_stats["messages_received"]  # should be increasing

monitor.has_ever_received()      # True if feed was ever active
monitor.stale_symbols()          # which symbols are stale
```

---

## Reconnect Storm

**Symptom:** `reconnect_count` increasing rapidly, repeated log entries for
`Reconnect attempt` and `Reconnect attempt failed`.

**Diagnosis:**

1. Check credential validity -- have API keys expired or been revoked?
2. Check network connectivity -- can the host reach the Settrade API?
3. Check broker status -- is the Settrade service experiencing an outage?
4. Look at the exception in `Reconnect attempt failed` logs for specific
   error details.

**Actions:**

- **If credentials invalid:** Update `app_id`, `app_secret`, `app_code`, or
  `broker_id` and restart.
- **If network unstable:** Increase `reconnect_max_delay` to reduce retry
  frequency and broker load.
- **If broker outage:** Wait for service recovery. The exponential backoff
  will automatically reduce retry frequency up to `reconnect_max_delay`.

**Metrics to watch:**

```python
mqtt_stats = mqtt_client.stats()
mqtt_stats["reconnect_count"]      # total reconnect attempts
mqtt_stats["last_connect_ts"]      # when was last successful connect
mqtt_stats["last_disconnect_ts"]   # when was last disconnect
```

---

## Parse Errors Spiking

**Symptom:** `adapter.stats()["parse_errors"]` increasing, log entries for
`Failed to parse BidOfferV3` (first 10 with stack trace) or
`Parse errors ongoing: N total` (every 1000th).

**Diagnosis:**

1. Check the stack trace from the first few errors -- what exception is thrown?
2. Check if the protobuf schema has changed (SDK update, broker-side change).
3. Check if non-BidOfferV3 messages are arriving on the subscribed topics.

**Actions:**

- **If protobuf incompatibility:** Update the `settrade_v2` package to match
  the broker's protobuf schema.
- **If wrong message type:** Verify subscription topics are correct
  (`proto/topic/bidofferv3/{symbol}`).
- **If corrupted payloads:** Check network path for packet corruption.
  Monitor `mqtt_client.stats()["callback_errors"]` at the transport level.

**Metrics to watch:**

```python
adapter_stats = adapter.stats()
adapter_stats["parse_errors"]      # should be 0
adapter_stats["callback_errors"]   # should be 0
adapter_stats["messages_parsed"]   # should be increasing
```

---

## Callback Errors Spiking

**Symptom:** `adapter.stats()["callback_errors"]` increasing.

**Diagnosis:**

1. Check the stack trace from the first few errors -- the `on_event` callback
   (typically `dispatcher.push()`) is failing.
2. Common cause: an exception in the strategy code that `push()` calls.

**Actions:**

- Review the `on_event` callback implementation. Ensure it does not raise
  exceptions.
- If the callback is `dispatcher.push()`, check that the dispatcher has not
  been cleared or replaced mid-stream.

---

## High Queue Utilization Without Drops

**Symptom:** `dispatcher.health().queue_utilization` approaching 1.0 but
`total_dropped` is still 0.

**Diagnosis:**

The queue is filling up but has not yet overflowed. This is a warning sign
that drops are imminent.

**Actions:**

- Increase poll frequency or batch size in the strategy.
- Increase `maxlen` as a buffer.
- Profile the strategy to identify processing bottlenecks.

---

## Stale Individual Symbols

**Symptom:** `monitor.stale_symbols()` returns specific symbols while
`is_feed_dead()` returns `False`.

**Diagnosis:**

1. The overall feed is alive but specific symbols have stopped updating.
2. Check if the symbol is illiquid (normal behavior during low-activity periods).
3. Check if the symbol is in a trading halt.

**Actions:**

- **If illiquid:** Add the symbol to `per_symbol_max_gap` with a longer
  threshold.
- **If unexpected:** Verify the symbol is subscribed via
  `adapter.subscribed_symbols`.
- **If trading halt:** Accept as normal behavior; the symbol will recover when
  trading resumes.

---

## Related Pages

- [Deployment Checklist](./deployment_checklist.md) -- initial setup verification
- [Tuning Guide](./tuning_guide.md) -- parameter optimization
- [Failure Scenarios](../08_testing_and_guarantees/failure_scenarios.md) -- how errors are handled internally
- [Logging Policy](../07_observability/logging_policy.md) -- understanding log output
