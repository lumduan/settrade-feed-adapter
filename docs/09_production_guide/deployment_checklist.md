# Deployment Checklist

Pre-launch validation for production deployment.

---

## Pre-Deployment Validation

### 1. Configuration Review

- [ ] **Credentials Configured**
  - `app_id`, `app_secret`, `app_code`, `broker_id` set correctly
  - Credentials stored securely (environment variables, secrets manager)
  - No hardcoded credentials in code

- [ ] **Base URL Configured**
  - Production: `None` (uses SDK default)
  - Sandbox/UAT: `https://open-api-test.settrade.com`

- [ ] **Queue Size Appropriate**
  - `maxlen` based on: message rate × expected processing latency
  - Default 100K events = ~10 seconds at 10K msg/s
  - Adjust based on load testing results

- [ ] **Reconnect Settings Reviewed**
  - `reconnect_min_delay`: 1-5 seconds (default: 1s)
  - `reconnect_max_delay`: 16-60 seconds (default: 16s)
  - `token_refresh_before_exp_seconds`: 300+ seconds (default: 300s)

- [ ] **Feed Health Thresholds Set**
  - `global_gap_ms`: Based on expected market activity (default: 5000ms)
  - Per-symbol gaps configured if needed
  - Alerts configured for feed death

---

### 2. Testing Validation

- [ ] **All Unit Tests Pass**
  ```bash
  uv run pytest tests -v
  ```
  Expected: 301 tests passed

- [ ] **Sandbox Integration Test**
  - Connect to sandbox environment
  - Subscribe to test symbols
  - Receive events for 60+ seconds
  - Verify reconnect recovery
  - Check all metrics populated

- [ ] **Load Test Completed**
  - Test with expected message rate
  - Monitor queue depth over time
  - Verify no drops under normal load
  - Measure end-to-end latency (P50, P95, P99)

- [ ] **Reconnect Test Passed**
  - Force disconnect (kill network)
  - Verify automatic reconnection
  - Verify connection_epoch increments
  - Verify no stale messages dispatched

---

### 3. Monitoring Setup

- [ ] **Metrics Collection Configured**
  - `dispatcher.stats()` polled periodically
  - `client.stats()` polled periodically
  - `feed_health.is_feed_dead()` checked regularly

- [ ] **Logging Configured**
  - Log level set appropriately (INFO or WARNING for production)
  - Structured logging enabled
  - Log aggregation configured (e.g., CloudWatch, ELK)

- [ ] **Alerts Configured**
  - `parse_errors > 0`: Critical
  - `callback_errors > 0`: Warning
  - `total_dropped > 0`: Warning
  - `is_feed_dead == True`: Critical
  - `reconnect_count > 5/min`: Warning

---

### 4. Performance Validation

- [ ] **Latency Benchmarked**
  - Parse + normalize latency measured
  - End-to-end latency measured
  - Results documented and within expectations

- [ ] **Memory Usage Profiled**
  - Peak memory usage measured
  - No memory leaks detected
  - GC pressure acceptable

- [ ] **CPU Usage Measured**
  - CPU usage under load documented
  - No CPU saturation under normal conditions

---

### 5. Error Handling Verified

- [ ] **Parse Error Test**
  - Inject malformed payload
  - Verify error counted and logged
  - Verify adapter continues processing

- [ ] **Callback Error Test**
  - Inject exception in callback
  - Verify error counted and logged
  - Verify MQTT client continues processing

- [ ] **Queue Overflow Test**
  - Push beyond maxlen
  - Verify drop-oldest behavior
  - Verify exact drop count

---

### 6. Failure Recovery Tested

- [ ] **Reconnect Recovery**
  - Simulate network disconnect
  - Verify automatic reconnect with backoff
  - Verify generation prevents stale messages

- [ ] **Token Refresh**
  - Wait for token near expiration
  - Verify proactive disconnect and refresh
  - Verify reconnect with new token

- [ ] **Broker Unavailable**
  - Stop broker or block connection
  - Verify reconnect loop continues
  - Verify graceful handling

---

### 7. Documentation Complete

- [ ] **Runbook Created**
  - Deployment steps documented
  - Rollback procedure defined
  - Troubleshooting guide available

- [ ] **Operational Procedures**
  - Startup procedure documented
  - Shutdown procedure documented
  - Restart procedure documented

- [ ] **Contact Information**
  - On-call engineers listed
  - Escalation path defined
  - Broker contact information available

---

## Deployment Steps

### 1. Environment Setup

```bash
# Set environment variables
export SETTRADE_APP_ID=your_app_id
export SETTRADE_APP_SECRET=your_app_secret
export SETTRADE_APP_CODE=your_app_code
export SETTRADE_BROKER_ID=your_broker_id

# For production
# (no SETTRADE_BASE_URL needed)

# For sandbox
export SETTRADE_BASE_URL=https://open-api-test.settrade.com
```

### 2. Install Dependencies

```bash
uv pip install -r requirements.txt
```

### 3. Run Tests

```bash
uv run pytest tests -v
```

Expected output:
```
============================= 301 passed in X.XXs ==============================
```

### 4. Start Application

```bash
python your_strategy.py
```

### 5. Verify Startup

Check logs for:
- [ ] "MQTT client connected" or similar
- [ ] Subscriptions acknowledged
- [ ] Events flowing (messages_received > 0)
- [ ] No errors in first 60 seconds

### 6. Monitor Metrics

```python
# In your monitoring loop
stats = dispatcher.stats()
print(f"Pushed: {stats.total_pushed}")
print(f"Dropped: {stats.total_dropped}")
print(f"Queue: {stats.queue_len}/{stats.maxlen}")
```

---

## Post-Deployment Validation

### First Hour

- [ ] No parse errors
- [ ] No callback errors
- [ ] No queue drops
- [ ] Reconnect count = 0 (unless expected)
- [ ] Feed health = alive

### First Day

- [ ] Latency within expected range
- [ ] Memory usage stable
- [ ] CPU usage acceptable
- [ ] No unexpected reconnects
- [ ] All subscribed symbols receiving updates

### First Week

- [ ] No production incidents
- [ ] Performance metrics stable
- [ ] Error rates at baseline
- [ ] Operational procedures validated

---

## Rollback Procedure

If deployment fails:

1. **Stop Application**
   ```bash
   # Graceful shutdown
   client.shutdown()
   ```

2. **Revert to Previous Version**
   ```bash
   git checkout <previous-tag>
   ```

3. **Restart Previous Version**
   ```bash
   python your_strategy_old.py
   ```

4. **Verify Rollback**
   - Check logs for successful startup
   - Verify events flowing
   - Monitor for 5 minutes

5. **Post-Mortem**
   - Document failure reason
   - Create issue/ticket
   - Plan fix and re-deployment

---

## Production Tuning

Based on load testing results:

### High Message Rate (>10K msg/s)

- Increase `maxlen` to 500K or 1M
- Increase polling frequency (reduce sleep time)
- Increase `poll(max_events)` batch size
- Consider multi-process architecture

### Low Latency Requirements (<100µs P99)

- Optimize processing logic (profile with cProfile)
- Disable GC during market hours (if safe)
- Use PyPy for JIT compilation (note: requires explicit locking)
- Consider separate process for ingestion

### High Availability Requirements

- Deploy multiple instances with different credentials
- Implement health check endpoint
- Configure load balancer / failover
- Set up automated restarts on failure

---

## Next Steps

- **[Tuning Guide](./tuning_guide.md)** — Configuration optimization
- **[Failure Playbook](./failure_playbook.md)** — Troubleshooting procedures
- **[Metrics Reference](../07_observability/metrics_reference.md)** — Monitoring details
