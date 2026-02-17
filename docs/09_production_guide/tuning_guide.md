# Tuning Guide

Parameter optimization for production deployments.

---

## Overview

This guide covers the key tunable parameters across all components and
provides formulas and recommendations for production sizing.

---

## Dispatcher: maxlen

**Source:** `core/dispatcher.py` -- `DispatcherConfig.maxlen`

**Default:** `100_000`

**Constraint:** Must be > 0

### Sizing Formula

```text
maxlen = peak_rate * max_processing_time * safety_factor
```

Where:

- `peak_rate` = peak messages per second (e.g., 10,000 msg/s during market open)
- `max_processing_time` = worst-case time for your strategy to process a batch (e.g., 5 seconds during a full portfolio rebalance)
- `safety_factor` = headroom multiplier (recommend 2x)

### Example

```text
peak_rate = 10,000 msg/s
max_processing_time = 5 seconds
safety_factor = 2

maxlen = 10,000 * 5 * 2 = 100,000
```

### Trade-offs

| maxlen | Memory | Drop Risk | Latency Impact |
| --- | --- | --- | --- |
| Small (1,000) | Low | High -- drops during brief stalls | Events always fresh |
| Default (100,000) | Moderate (~10MB for BestBidAsk) | Low for most strategies | May process slightly stale data during bursts |
| Large (1,000,000) | High (~100MB) | Very low | Risk of processing very stale data if consumer is slow |

---

## Dispatcher: EMA Alpha

**Source:** `core/dispatcher.py` -- `DispatcherConfig.ema_alpha`

**Default:** `0.01`

**Constraint:** `0 < ema_alpha <= 1`

The EMA smoothing factor controls how quickly the drop rate signal responds
to changes. The half-life in messages is approximately `ln(2) / alpha`.

| Alpha | Half-Life (messages) | Behavior |
| --- | --- | --- |
| `0.001` | ~693 | Very smooth; slow to detect drop bursts |
| `0.01` (default) | ~69 | Good balance of smoothness and responsiveness |
| `0.1` | ~7 | Fast reaction; noisy signal |
| `1.0` | 1 | No smoothing; raw drop indicator |

### When to Adjust

- **Lower alpha** (e.g., 0.001) if you see false-positive drop warnings from brief transient bursts
- **Higher alpha** (e.g., 0.05) if you need faster detection of sustained drop episodes

---

## Dispatcher: drop_warning_threshold

**Source:** `core/dispatcher.py` -- `DispatcherConfig.drop_warning_threshold`

**Default:** `0.01` (1%)

**Constraint:** `0 < drop_warning_threshold <= 1`

When the EMA drop rate exceeds this threshold, a warning is logged. When it
recovers below, an info-level recovery is logged.

| Threshold | Meaning |
| --- | --- |
| `0.001` (0.1%) | Very strict -- warns on rare drops |
| `0.01` (1%, default) | Warns when ~1% of pushes are dropping |
| `0.05` (5%) | Relaxed -- only warns on significant overflow |

---

## Feed Health: max_gap_seconds

**Source:** `core/feed_health.py` -- `FeedHealthConfig.max_gap_seconds`

**Default:** `5.0`

**Constraint:** Must be > 0

### Recommendations

| Market Condition | Recommended Value |
| --- | --- |
| Active trading hours, liquid symbols | 5.0 seconds |
| Pre-market / after-hours | 30.0 seconds |
| Illiquid symbols | Use `per_symbol_max_gap` override |

### Per-Symbol Override

For symbols with different activity patterns, use `per_symbol_max_gap`:

```python
config = FeedHealthConfig(
    max_gap_seconds=5.0,          # global default
    per_symbol_max_gap={
        "RARE": 60.0,             # illiquid -- 60s threshold
        "ILLIQUID": 30.0,         # low volume -- 30s threshold
    },
)
```

Symbols not in the override dictionary use the global `max_gap_seconds`.

---

## MQTT: Reconnect Delays

**Source:** `infra/settrade_mqtt.py` -- `MQTTClientConfig`

| Parameter | Default | Description |
| --- | --- | --- |
| `reconnect_min_delay` | `1.0` | Minimum backoff delay in seconds |
| `reconnect_max_delay` | `30.0` | Maximum backoff delay in seconds |
| `token_refresh_before_exp_seconds` | `100` | Seconds before token expiry to trigger controlled reconnect |

### Reconnect Trade-offs

- **Lower min_delay** (e.g., 0.5s): Faster reconnect after brief outages, but higher broker load during sustained outages
- **Higher max_delay** (e.g., 60s): Reduced broker load during sustained outages, but longer recovery time
- **Higher token_refresh_before_exp_seconds** (e.g., 300s): More time margin for token refresh, but more frequent reconnects

### Backoff Behavior

The reconnect loop doubles the delay on each failure:

```text
attempt 1: 1.0s * jitter(0.8-1.2) = ~0.8-1.2s
attempt 2: 2.0s * jitter          = ~1.6-2.4s
attempt 3: 4.0s * jitter          = ~3.2-4.8s
...
attempt N: min(delay * 2^N, max_delay) * jitter
```

---

## MQTT: Keepalive

**Source:** `infra/settrade_mqtt.py` -- `MQTTClientConfig.keepalive`

**Default:** `30` seconds

**Constraint:** 5 to 300 seconds

Lower values detect dead connections faster but generate more network traffic.
The default of 30 seconds is appropriate for most deployments.

---

## Poll Batch Size (max_events)

**Source:** `core/dispatcher.py` -- `Dispatcher.poll(max_events)`

**Default:** `100`

The `max_events` parameter on `poll()` controls how many events are consumed
per call.

| Batch Size | Throughput | Per-Event Latency |
| --- | --- | --- |
| Small (1-10) | Lower | Lower -- each event processed immediately |
| Medium (50-100, default) | Good balance | Good balance |
| Large (500-1000) | Higher -- amortizes call overhead | Higher -- batch must complete before next poll |

### Batch Size Trade-offs

- **Smaller batches** for latency-sensitive strategies that need to react to each event quickly
- **Larger batches** for throughput-oriented strategies that process events in bulk

---

## Adapter: full_depth

**Source:** `infra/settrade_adapter.py` -- `BidOfferAdapterConfig.full_depth`

**Default:** `False`

| Mode | Event Type | Objects Per Message | Use Case |
| --- | --- | --- | --- |
| `False` (default) | `BestBidAsk` | Minimal | Low-latency strategies |
| `True` | `FullBidOffer` | ~46 (4 tuples + 40 values) | Depth-of-book strategies |

`FullBidOffer` creates significantly more GC pressure. Do not use for
sub-100us latency strategies.

---

## Related Pages

- [Deployment Checklist](./deployment_checklist.md) -- pre-launch verification
- [Failure Playbook](./failure_playbook.md) -- troubleshooting
- [Metrics Reference](../07_observability/metrics_reference.md) -- monitoring
