# Authentication and Token Management

Token-based authentication and automatic token refresh.

---

## Authentication Flow

```
1. Create Context (settrade_v2.Context)
   ↓
2. Login (context.login())
   → Returns: host, token, expired_at
   ↓
3. Configure MQTT Client
   - host: broker address
   - username: app_code
   - password: token
   ↓
4. Connect to broker (WebSocket+TLS)
   ↓
5. Monitor token expiration
   ↓
6. Proactive refresh before expiry
   → Disconnect, re-login, reconnect
```

---

## Token Lifecycle

### Initial Authentication

```python
from settrade_v2.context import Context, Option

# Create context
ctx = Context(
    host_url=base_url,
    app_id=app_id,
    app_secret=app_secret,
    app_code=app_code,
    broker_id=broker_id,
)

# Login
login_response = ctx.login()

# Extract credentials
host = login_response.hosts[0]
token = login_response.token
expired_at = login_response.expired_at  # Unix timestamp
```

### Token Expiration Monitoring

The client monitors token expiration and proactively refreshes:

```python
time_until_expiry = expired_at - time.time()

if time_until_expiry < token_refresh_before_exp_seconds:
    # Proactive refresh
    client._schedule_token_refresh()
```

---

## Token Refresh Strategy

**Proactive Refresh** (before expiration):
- Default: 300 seconds before expiry
- Triggers controlled disconnect
- Re-authenticates with broker
- Reconnects with new token
- No message loss (reconnect handles it)

**Why Proactive?**
- Avoids mid-stream disconnect
- Allows graceful transition
- Prevents authentication errors

---

## Configuration

```python
config = MQTTClientConfig(
    app_id="...",
    app_secret="...",
    app_code="...",
    broker_id="...",
    token_refresh_before_exp_seconds=300,  # 5 minutes before expiry
)
```

---

## Implementation Reference

See [infra/settrade_mqtt.py](../../infra/settrade_mqtt.py):
- `_fetch_host_and_token()` method
- `_schedule_token_refresh()` method
- Token expiration monitoring logic

---

## Test Coverage

- `test_settrade_mqtt.py::TestTokenRefresh::test_fetch_host_token_syncs_expired_at`
- `test_settrade_mqtt.py::TestTokenRefresh::test_fetch_host_token_sets_host_and_token`
- `test_settrade_mqtt.py::TestTokenRefresh::test_token_refresh_triggers_reconnect`

---

## Security Considerations

1. **Store credentials securely**
   - Use environment variables or secrets manager
   - Never hardcode in source code

2. **app_secret is sensitive**
   - Base64-encoded ECDSA private key
   - Treat as highly confidential

3. **Token is ephemeral**
   - Short-lived (typically 30-60 minutes)
   - Automatically refreshed
   - Single use per connection

---

## Next Steps

- **[Client Lifecycle](./client_lifecycle.md)** — State machine
- **[Reconnect Strategy](./reconnect_strategy.md)** — Connection recovery
