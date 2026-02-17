# Deployment Checklist

Pre-launch validation for production deployment.

---

## Before Deployment

### 1. Verify Credentials

- [ ] `app_id` is correct for the target environment
- [ ] `app_secret` is the base64-encoded ECDSA key (padding auto-corrected by the client)
- [ ] `app_code` matches the registered application
- [ ] `broker_id` is set correctly (`"SANDBOX"` for UAT, actual broker ID for production)

```python
from infra.settrade_mqtt import MQTTClientConfig

config = MQTTClientConfig(
    app_id="your_app_id",
    app_secret="your_app_secret",
    app_code="your_app_code",
    broker_id="your_broker_id",
)
```

### 2. Test with SANDBOX First

Always validate against the Settrade sandbox environment before going to
production:

```python
config = MQTTClientConfig(
    app_id="...",
    app_secret="...",
    app_code="...",
    broker_id="SANDBOX",   # Uses UAT environment, broker_id resolved to "098"
)
```

The `base_url` override can also be used explicitly:

```python
config = MQTTClientConfig(
    app_id="...",
    app_secret="...",
    app_code="...",
    broker_id="098",
    base_url="https://open-api-test.settrade.com",
)
```

### 3. Configure Dispatcher maxlen

Set `maxlen` based on your expected message rate and strategy processing time:

```python
from core.dispatcher import DispatcherConfig

config = DispatcherConfig(
    maxlen=100_000,   # default; ~10 seconds at 10K msg/s
)
```

See the [Tuning Guide](./tuning_guide.md) for sizing formulas.

### 4. Configure Feed Health Thresholds

Set `max_gap_seconds` based on expected message frequency. Configure
`per_symbol_max_gap` for illiquid symbols:

```python
from core.feed_health import FeedHealthConfig

config = FeedHealthConfig(
    max_gap_seconds=5.0,
    per_symbol_max_gap={"RARE": 60.0},
)
```

### 5. Configure Reconnect Delays

Adjust backoff parameters based on your network environment:

```python
config = MQTTClientConfig(
    # ...credentials...
    reconnect_min_delay=1.0,    # default; minimum backoff
    reconnect_max_delay=30.0,   # default; maximum backoff
    token_refresh_before_exp_seconds=100,  # default; refresh 100s before expiry
)
```

### 6. Set Up Logging

Configure Python logging to capture feed adapter events:

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# Optional: reduce noise from specific loggers
logging.getLogger("core.dispatcher").setLevel(logging.WARNING)
```

---

## After Deployment

### 7. Monitor Key Metrics

Periodically check the following:

```python
# Transport health
mqtt_stats = mqtt_client.stats()
assert mqtt_stats["state"] == "CONNECTED"
assert mqtt_stats["callback_errors"] == 0

# Adapter health
adapter_stats = adapter.stats()
assert adapter_stats["parse_errors"] == 0
assert adapter_stats["callback_errors"] == 0

# Dispatcher health
dispatcher_health = dispatcher.health()
assert dispatcher_health.drop_rate_ema < 0.01
assert dispatcher_health.queue_utilization < 0.5

# Feed liveness
assert not monitor.is_feed_dead()
assert len(monitor.stale_symbols()) == 0
```

### 8. Verify Subscriptions

Confirm all expected symbols are subscribed:

```python
expected = {"AOT", "PTT", "CPALL"}
actual = adapter.subscribed_symbols
assert expected == actual
```

### 9. Check for Drops

After running for a few minutes, verify no events are being dropped:

```python
stats = dispatcher.stats()
assert stats.total_dropped == 0, f"Drops detected: {stats.total_dropped}"
```

If drops occur, increase `maxlen` or optimize your strategy processing time.

---

## Shutdown Procedure

```python
# 1. Stop accepting new events
mqtt_client.shutdown()

# 2. Drain remaining events from the dispatcher
remaining = dispatcher.poll(max_events=dispatcher.stats().queue_len + 1)

# 3. Log final stats
print(f"Total parsed: {adapter.stats()['messages_parsed']}")
print(f"Total dropped: {dispatcher.stats().total_dropped}")
print(f"Total reconnects: {mqtt_client.stats()['reconnect_count']}")
```

---

## Related Pages

- [Tuning Guide](./tuning_guide.md) -- parameter optimization
- [Failure Playbook](./failure_playbook.md) -- troubleshooting production issues
- [Metrics Reference](../07_observability/metrics_reference.md) -- all available metrics
