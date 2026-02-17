# Logging Policy

Structured logging standards and best practices.

---

## Overview

**Logging policy** defines:
- ✅ **What** to log (events, errors, state changes)
- ✅ **How** to log (structured format, levels)
- ✅ **When** to log (frequency, conditions)
- ✅ **Where** to log (stdout, files, external systems)

---

## Log Levels

### DEBUG

**Purpose**: Detailed diagnostic information for development.

**When**: Development, debugging, troubleshooting

**Example**:
```python
logger.debug("Received MQTT message", extra={"topic": topic, "size": len(payload)})
```

**Production**: Usually disabled (high volume)

---

### INFO

**Purpose**: General informational messages about system state.

**When**: Normal operations, state transitions

**Example**:
```python
logger.info("MQTT connected", extra={"broker": "mqtt.settrade.com", "port": 8883})
```

**Production**: Enabled (low volume)

---

### WARNING

**Purpose**: Unexpected but recoverable conditions.

**When**: Overflow, stale symbols, retry attempts

**Example**:
```python
logger.warning(
    "Dispatcher overflow",
    extra={
        "queue_size": dispatcher._queue.qsize(),
        "maxsize": dispatcher._maxsize,
        "overflow_count": dispatcher._overflow_count,
    }
)
```

**Production**: Enabled (always investigate)

---

### ERROR

**Purpose**: Error conditions requiring attention.

**When**: Parse failures, connection errors, exceptions

**Example**:
```python
logger.error(
    "Failed to parse message",
    extra={
        "topic": topic,
        "error": str(e),
        "traceback": traceback.format_exc(),
    }
)
```

**Production**: Enabled (always investigate)

---

### CRITICAL

**Purpose**: System-level failures requiring immediate action.

**When**: Unrecoverable errors, system shutdown

**Example**:
```python
logger.critical(
    "Cannot connect to MQTT broker after max retries",
    extra={
        "broker": broker_url,
        "retry_count": max_retries,
    }
)
```

**Production**: Enabled (page on-call)

---

## Structured Logging

### Format

**Standard**: JSON format with `extra` fields.

```python
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields
        if hasattr(record, "extra"):
            log_data.update(record.extra)
        
        return json.dumps(log_data)

# Configure
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())

logger = logging.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel(logging.INFO)
```

---

### Example Output

```json
{
  "timestamp": "2025-02-14T14:13:20.123Z",
  "level": "WARNING",
  "logger": "core.dispatcher",
  "message": "Dispatcher overflow",
  "queue_size": 9850,
  "maxsize": 10000,
  "overflow_count": 1543
}
```

---

## What to Log

### MQTT Events

**Connection**:
```python
logger.info("MQTT connected", extra={"broker": broker, "client_id": client_id})
```

**Disconnection**:
```python
logger.warning("MQTT disconnected", extra={"reason_code": rc})
```

**Reconnection**:
```python
logger.info("MQTT reconnecting", extra={"attempt": attempt, "max_retries": max_retries})
```

**Subscription**:
```python
logger.info("Subscribed to symbol", extra={"symbol": symbol, "topic": topic})
```

---

### Dispatcher Events

**Overflow**:
```python
logger.warning(
    "Dispatcher overflow",
    extra={
        "queue_size": dispatcher._queue.qsize(),
        "maxsize": dispatcher._maxsize,
        "overflow_count": dispatcher._overflow_count,
    }
)
```

**Queue fill warning**:
```python
fill_ratio = dispatcher._queue.qsize() / dispatcher._maxsize
if fill_ratio > 0.8:
    logger.warning(
        "Queue filling up",
        extra={
            "fill_ratio": fill_ratio,
            "queue_size": dispatcher._queue.qsize(),
            "maxsize": dispatcher._maxsize,
        }
    )
```

---

### Feed Health Events

**Feed stale**:
```python
if not feed_health.is_alive():
    logger.error("Feed is stale", extra={"global_timeout": global_timeout})
```

**Symbol stale**:
```python
if not feed_health.is_symbol_alive(symbol):
    logger.warning("Symbol is stale", extra={"symbol": symbol, "symbol_timeout": symbol_timeout})
```

---

### Adapter Events

**Parse error**:
```python
try:
    message = BestBidAskMessage.FromString(payload)
except Exception as e:
    logger.error(
        "Failed to parse message",
        extra={
            "topic": topic,
            "payload_size": len(payload),
            "error": str(e),
        }
    )
```

**Normalization error**:
```python
try:
    event = normalize_best_bid_ask(message)
except Exception as e:
    logger.error(
        "Failed to normalize message",
        extra={
            "symbol": message.symbol,
            "error": str(e),
        }
    )
```

---

## What NOT to Log

### ❌ High-Frequency Events

**Don't log every event** (creates excessive volume):
```python
# ❌ BAD: Logs 1000+ times per second
for event in dispatcher.poll():
    logger.debug(f"Processing event: {event}")  # TOO MUCH
    process_event(event)
```

**Instead**: Log summary metrics periodically:
```python
# ✅ GOOD: Log every 1000 events
event_count = 0
for event in dispatcher.poll():
    event_count += 1
    if event_count % 1000 == 0:
        logger.info("Processed events", extra={"count": event_count})
    process_event(event)
```

---

### ❌ Sensitive Data

**Don't log credentials or tokens**:
```python
# ❌ BAD: Logs API token
logger.info("Connecting to MQTT", extra={"token": api_token})

# ✅ GOOD: Log without token
logger.info("Connecting to MQTT", extra={"broker": broker})
```

---

### ❌ Personal Data

**Don't log PII** (personal identifiable information):
```python
# ❌ BAD: Logs user details
logger.info("User transaction", extra={"user_id": user_id, "amount": amount})

# ✅ GOOD: Hash or omit PII
logger.info("Transaction", extra={"hashed_user": hash(user_id), "amount": amount})
```

---

## Logging Configuration

### Python logging

```python
import logging
import sys

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),  # Console output
        logging.FileHandler("feed_adapter.log"),  # File output
    ]
)

# Module-specific logger
logger = logging.getLogger(__name__)

# Example usage
logger.info("System started", extra={"version": "1.0.0"})
```

---

### Production Configuration

**Structured JSON logging**:
```python
import logging
import json
import sys

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **getattr(record, "__dict__", {}),  # Extra fields
        })

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())

logging.root.addHandler(handler)
logging.root.setLevel(logging.INFO)
```

**Environment-based level**:
```python
import os

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.root.setLevel(getattr(logging, log_level))
```

---

## Log Rotation

### File-based logging with rotation

```python
import logging
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    "feed_adapter.log",
    maxBytes=100 * 1024 * 1024,  # 100 MB
    backupCount=5,  # Keep 5 old files
)

handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logger = logging.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel(logging.INFO)
```

---

## External Logging Systems

### Elasticsearch + Kibana

**Ship logs to Elasticsearch**:
```python
from cmreslogging.handlers import CMRESHandler

handler = CMRESHandler(
    hosts=[{"host": "elasticsearch.local", "port": 9200}],
    auth_type=CMRESHandler.AuthType.NO_AUTH,
    es_index_name="feed-adapter",
)

logger = logging.getLogger(__name__)
logger.addHandler(handler)
```

---

### CloudWatch Logs (AWS)

```python
import watchtower

handler = watchtower.CloudWatchLogHandler(
    log_group="feed-adapter",
    stream_name="production",
)

logger = logging.getLogger(__name__)
logger.addHandler(handler)
```

---

## Implementation Reference

See:
- [core/dispatcher.py](../../core/dispatcher.py) — Dispatcher logging
- [core/feed_health.py](../../core/feed_health.py) — Feed health logging
- [infra/settrade_mqtt.py](../../infra/settrade_mqtt.py) — MQTT logging

---

## Next Steps

- **[Metrics Reference](./metrics_reference.md)** — All metrics
- **[Benchmark Guide](./benchmark_guide.md)** — Performance measurement
- **[Performance Targets](./performance_targets.md)** — Target latencies
