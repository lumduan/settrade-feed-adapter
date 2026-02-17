# Authentication and Token Management

Token-based authentication flow and automatic token refresh for the
`SettradeMQTTClient` in `infra/settrade_mqtt.py`.

---

## Authentication Flow Overview

```text
connect()
  |
  v
_login()
  |  Creates settrade_v2.context.Context with app_id, app_secret, app_code, broker_id
  |  Handles SANDBOX detection (broker_id "SANDBOX" -> uat env + broker_id "098")
  |  Calls ctx.login() to obtain access token
  |  Records ctx.expired_at and ctx.token_type
  |
  v
_fetch_host_token()
  |  GET {base_url}/api/dispatcher/v3/{broker_id}/token
  |  Returns: hosts[] (MQTT broker addresses) + token (dispatcher token)
  |  Selects hosts[0] as MQTT host
  |  Syncs _expired_at from Context (may have been auto-refreshed)
  |
  v
_create_mqtt_client()
  |  Creates paho Client(clean_session=True, transport="websockets")
  |  Sets TLS via client.tls_set()
  |  Sets WebSocket options:
  |    headers: {"Authorization": "{token_type} {token}"}
  |    path: /api/dispatcher/v3/{broker_id}/mqtt
  |
  v
client.connect(host, port=443, keepalive=30)
  |
  v
loop_start()  +  _start_token_refresh_timer()
```

---

## Step 1: Login via _login()

The `_login()` method creates a `settrade_v2.context.Context` and calls
`ctx.login()` to obtain an access token from the Settrade REST API.

```python
def _login(self) -> None:
    broker_id = self._broker_id

    # SANDBOX detection (replicates SDK's _BaseUser behavior)
    if broker_id.upper() == "SANDBOX":
        from settrade_v2.config import config as sdk_config
        sdk_config["environment"] = "uat"
        broker_id = "098"

    self._broker_id = broker_id

    self._ctx = Context(
        app_id=self._config.app_id,
        app_secret=self._config.app_secret,
        app_code=self._config.app_code,
        broker_id=self._broker_id,
    )
    if self._config.base_url is not None:
        self._ctx.base_url = self._config.base_url
    self._ctx.login()
    self._expired_at = self._ctx.expired_at
    self._token_type = self._ctx.token_type
```

### SANDBOX Detection

When `broker_id` is set to `"SANDBOX"` (case-insensitive), the client
replicates the official SDK's behavior:

1. Sets the SDK global config environment to `"uat"`.
2. Replaces `broker_id` with `"098"` (the UAT broker identifier).

This means SANDBOX users do not need to manually configure `base_url` or
know the internal broker ID -- the client handles the translation
automatically.

```python
# User configuration for sandbox
config = MQTTClientConfig(
    app_id="my_app",
    app_secret="my_secret",
    app_code="my_code",
    broker_id="SANDBOX",    # Automatically becomes "098" with uat environment
)
```

### base_url Override

If `base_url` is explicitly set in `MQTTClientConfig`, it overrides the
environment-based URL. Set to `None` (default) for production.

```python
# Explicit UAT URL (alternative to SANDBOX broker_id)
config = MQTTClientConfig(
    app_id="my_app",
    app_secret="my_secret",
    app_code="my_code",
    broker_id="098",
    base_url="https://open-api-test.settrade.com",
)
```

---

## Step 2: Fetch Host and Token via _fetch_host_token()

After login, the client fetches the MQTT broker host and a dispatcher
token via the Settrade REST API.

**Endpoint**: `GET {base_url}/api/dispatcher/v3/{broker_id}/token`

The request is dispatched through the SDK's `Context.dispatch(Option(...))`,
which routes through `send_request()`. This method auto-refreshes the
access token if it is near expiry, so `_expired_at` is re-synced after
the call to stay accurate.

```python
def _fetch_host_token(self) -> None:
    if self._ctx is None:
        raise RuntimeError("Must login before fetching host/token")

    token_url = f"{self._ctx.base_url}/api/dispatcher/v3/{self._broker_id}/token"
    option = Option("GET", token_url)
    resp = self._ctx.dispatch(option)
    data = resp.json()
    hosts = data["hosts"]
    if not hosts:
        raise ValueError("No MQTT hosts returned from dispatcher")

    self._host = hosts[0]
    self._token = data["token"]

    # Sync expired_at -- Context.request() may have auto-refreshed
    self._expired_at = self._ctx.expired_at
```

**Response structure**:

```text
{
    "hosts": ["mqtt-broker-host.settrade.com"],
    "token": "eyJhbGciOiJSUzI1NiIs..."
}
```

The client selects `hosts[0]` as the MQTT broker address. If `hosts` is
empty, a `ValueError` is raised.

---

## Step 3: MQTT Client with Token in WebSocket Headers

The dispatcher token is passed to the MQTT broker via the WebSocket
`Authorization` header -- not via MQTT username/password fields.

```python
def _create_mqtt_client(self) -> mqtt.Client:
    # ... cleanup previous client, increment generation ...

    client = mqtt.Client(
        clean_session=True,
        transport="websockets",
    )
    client.tls_set()
    client.ws_set_options(
        headers={"Authorization": f"{self._token_type} {self._token}"},
        path=f"/api/dispatcher/v3/{self._broker_id}/mqtt",
    )
    # ... bind callbacks ...
    return client
```

The WebSocket path is `/api/dispatcher/v3/{broker_id}/mqtt` and the
connection uses TLS on port 443 (WSS).

---

## app_secret Base64 Padding Auto-Fix

The Settrade API console may provide `app_secret` values without proper
base64 padding. The `MQTTClientConfig` Pydantic model automatically
fixes this via a field validator.

```python
@field_validator("app_secret")
@classmethod
def _validate_base64_padding(cls, v: str) -> str:
    if not v:
        return v
    v = v.strip()                           # Remove whitespace
    padding_needed = (4 - len(v) % 4) % 4   # Calculate missing '=' chars
    if padding_needed > 0:
        v = v + "=" * padding_needed
    return v
```

The validator:

1. Strips leading and trailing whitespace.
2. Calculates how many `=` padding characters are needed to make the
   length divisible by 4.
3. Appends the padding if needed.

This prevents `"Incorrect padding"` errors during SDK authentication
without requiring manual user intervention.

---

## Token Refresh

### Background Monitoring Thread

The `_start_token_refresh_timer()` method launches a daemon thread
(`token-refresh`) that runs `_token_refresh_check()` in a loop.

### Refresh Logic

The check loop monitors `_expired_at` and triggers a controlled reconnect
when the token is within `token_refresh_before_exp_seconds` (default: 100
seconds) of expiry.

```python
def _token_refresh_check(self) -> None:
    while not self._shutdown_event.is_set():
        time_until_refresh = (
            self._expired_at
            - self._config.token_refresh_before_exp_seconds
            - time.time()
        )

        if time_until_refresh <= 0:
            # Token near expiry -- trigger controlled reconnect
            self._schedule_reconnect()
            # Wait for reconnect to complete before checking again
            self._shutdown_event.wait(
                timeout=self._config.reconnect_max_delay,
            )
        else:
            # Check every 60s or when refresh is due, whichever is sooner
            wait_time = min(time_until_refresh, 60.0)
            self._shutdown_event.wait(timeout=wait_time)
```

### Refresh via Shared Reconnect Guard

Token refresh does **not** have its own reconnect path. It calls
`_schedule_reconnect()`, which is the same method used by unexpected
disconnects. This shares the reconnect guard (`_reconnecting` flag +
`_reconnect_lock`), preventing duplicate reconnect threads if a token
refresh timer and a network disconnect fire concurrently.

The reconnect loop (`_reconnect_loop`) then:

1. Fetches a fresh host + token via `_fetch_host_token()` (which
   auto-refreshes the access token through the SDK Context).
2. Creates a new MQTT client with the fresh token in the Authorization header.
3. Connects to the broker.

This approach avoids mutating live WebSocket headers on an existing
connection -- instead, a completely new client is created with fresh
credentials.

---

## Configuration Reference

```python
config = MQTTClientConfig(
    app_id="my_app",                          # Settrade API application ID
    app_secret="my_secret_base64",            # Base64-encoded ECDSA key (auto-padded)
    app_code="my_code",                       # Settrade API application code
    broker_id="my_broker",                    # Broker ID (or "SANDBOX" for UAT)
    base_url=None,                            # None = production, or explicit URL
    token_refresh_before_exp_seconds=100,     # Trigger refresh 100s before expiry (ge=10)
)
```

| Field | Default | Constraints | Description |
| --- | --- | --- | --- |
| `app_id` | (required) | -- | Settrade API application ID |
| `app_secret` | (required) | auto-padded, whitespace trimmed | Base64-encoded ECDSA private key |
| `app_code` | (required) | -- | Settrade API application code |
| `broker_id` | (required) | -- | Broker identifier or `"SANDBOX"` |
| `base_url` | `None` | -- | `None` for production, URL string for UAT |
| `token_refresh_before_exp_seconds` | `100` | `ge=10` | Seconds before token expiry to trigger refresh |

---

## Security Notes

1. **Credential storage**: Store `app_id`, `app_secret`, and `app_code` in
   environment variables or a secrets manager. Never hardcode them in source
   files.

2. **app_secret sensitivity**: The `app_secret` is a base64-encoded ECDSA
   private key. Treat it as highly confidential. It is used to sign
   authentication requests to the Settrade API.

3. **Token ephemerality**: The dispatcher token is short-lived. It is
   automatically refreshed via the background token refresh mechanism
   before it expires.

4. **No live header mutation**: Token refresh works by creating an entirely
   new MQTT client with fresh credentials rather than attempting to update
   headers on an existing WebSocket connection. This avoids race conditions
   and ensures clean authentication state.

5. **TLS enforcement**: All connections use `client.tls_set()` with WSS
   (WebSocket Secure) on port 443. Credentials are never transmitted in
   plaintext.

---

## Test Coverage Mapping

| Behavior | Test Case |
| --- | --- |
| `_fetch_host_token` syncs `_expired_at` | `TestTokenRefresh::test_fetch_host_token_syncs_expired_at` |
| `_fetch_host_token` sets host and token | `TestTokenRefresh::test_fetch_host_token_sets_host_and_token` |
| `_fetch_host_token` raises without login | `TestTokenRefresh::test_fetch_host_token_raises_without_login` |
| `_fetch_host_token` raises on empty hosts | `TestTokenRefresh::test_fetch_host_token_raises_on_empty_hosts` |
| Token refresh triggers reconnect | `TestTokenRefresh::test_token_refresh_triggers_reconnect` |
| app_secret padding (no padding needed) | `TestMQTTClientConfig::test_app_secret_padding_not_needed` |
| app_secret padding (1 char) | `TestMQTTClientConfig::test_app_secret_padding_one_char` |
| app_secret padding (2 chars) | `TestMQTTClientConfig::test_app_secret_padding_two_chars` |
| app_secret padding (3 chars) | `TestMQTTClientConfig::test_app_secret_padding_three_chars` |
| app_secret whitespace trimming | `TestMQTTClientConfig::test_app_secret_whitespace_trimming` |

---

## Implementation Reference

Source: `infra/settrade_mqtt.py`

- `MQTTClientConfig` -- Pydantic config with `_validate_base64_padding` validator
- `_login()` -- Context creation, SANDBOX detection, `ctx.login()`
- `_fetch_host_token()` -- Dispatcher API call for host + token
- `_create_mqtt_client()` -- WebSocket + TLS + Authorization header setup
- `_start_token_refresh_timer()` -- Launches background refresh thread
- `_token_refresh_check()` -- Monitors `_expired_at`, triggers `_schedule_reconnect()`

---

## Related Documents

- [Client Lifecycle](./client_lifecycle.md) -- State machine and transitions
- [Reconnect Strategy](./reconnect_strategy.md) -- How reconnect handles token refresh
- [Subscription Model](./subscription_model.md) -- Topic management
