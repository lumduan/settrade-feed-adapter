# Phase 1: MQTT Transport Implementation Plan

**Feature:** Low-Latency MQTT Feed Adapter - Phase 1: MQTT Transport
**Branch:** `feature/phase1-mqtt-transport`
**Created:** 2026-02-12
**Status:** Complete
**Completed:** 2026-02-12
**Depends On:** Phase 0 (Complete)

---

## Table of Contents

1. [Overview](#overview)
2. [AI Prompt](#ai-prompt)
3. [Scope](#scope)
4. [Design Decisions](#design-decisions)
5. [State Machine](#state-machine)
6. [Implementation Steps](#implementation-steps)
7. [Callback Contract](#callback-contract)
8. [Metrics & Observability](#metrics--observability)
9. [File Changes](#file-changes)
10. [Success Criteria](#success-criteria)

---

## Overview

### Purpose

Phase 1 implements the core MQTT transport layer for the Settrade Feed Adapter. This is the foundation for all subsequent phases (adapter, dispatcher, examples). It provides:

1. **Direct MQTT connection** to the Settrade Open API broker via WebSocket+SSL (port 443)
2. **Token-based authentication** using credentials fetched via the Settrade REST API
3. **Topic subscription management** with callback-based message dispatch
4. **Auto-reconnect** with exponential backoff and jitter on unexpected disconnection
5. **Token refresh** via controlled reconnect before expiration (no live header mutation)

### Parent Plan Reference

This implementation is part of the larger plan documented in:
- `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md`

### Key Deliverables

1. **`infra/settrade_mqtt.py`** — `SettradeMQTTClient` class with full MQTT transport logic
2. **`infra/__init__.py`** — Package initialization with public exports
3. **`tests/test_settrade_mqtt.py`** — Comprehensive unit tests with mocked dependencies
4. **`tests/__init__.py`** — Test package initialization
5. **Updated `pyproject.toml`** — Test dependencies added
6. **This plan document** — Phase 1 implementation plan

---

## AI Prompt

The following prompt was used to generate this implementation:

```
You are tasked with planning and implementing Phase 1: MQTT Transport for the Settrade Feed Adapter project. Follow these steps:

1. **Branch & Planning**
   - Create a new git branch for this task before making any changes.
   - Carefully read:
     - `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md` (focus on Phase 1: MQTT Transport)
     - `docs/plan/low-latency-mqtt-feed-adapter/_sample.md` (plan format reference)
   - Draft a detailed implementation plan (including this prompt) as a markdown file in `docs/plan/low-latency-mqtt-feed-adapter/`, following the format in `_sample.md`.

2. **Implementation**
   - Implement all requirements for Phase 1 as described in your plan:
     - Connect to Settrade MQTT broker via WebSocket+SSL (port 443)
     - Authenticate using token fetched via REST API
     - Set Authorization header and connect path as specified
     - Implement subscribe(topic, callback) and on_message dispatch
     - Add auto-reconnect with exponential backoff
     - Add token refresh before expiration
     - Follow all architectural and documentation standards

3. **Documentation & Plan Updates**
   - Save the implementation plan (including this prompt) as a markdown file in `docs/plan/low-latency-mqtt-feed-adapter/`.
   - When implementation is complete, update `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md` with completion notes, date, and any issues encountered.

4. **Pull Request**
   - Create a PR with a detailed commit message and PR description, following `.github/instructions/git-commit.instructions.md`.
   - The PR must include:
     - Summary of changes
     - List of files added/modified
     - Technical and user benefits
     - Testing performed and results
     - Any issues or notes from implementation

Files for reference:
- `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md`
- `docs/plan/low-latency-mqtt-feed-adapter/_sample.md`
- `.github/instructions/core-architectrual-principles.instructions.md`
- `.github/instructions/documentation-standards.instructions.md`
- `.github/instructions/git-commit.instructions.md`
- `.github/prompts/Prompt-Engineer.prompt.md`

Expected deliverables:
- A markdown implementation plan for Phase 1: MQTT Transport, saved at `docs/plan/low-latency-mqtt-feed-adapter/`, including this prompt
- Implemented MQTT transport logic as specified in the plan
- Updated main plan file with completion notes, date, and any issues
- A PR to GitHub with a detailed commit message and PR description per project standards
```

---

## Scope

### In Scope (Phase 1)

| Component | Description | Status |
|-----------|-------------|--------|
| `SettradeMQTTClient` class | Full MQTT transport with WSS + TLS + token auth | Complete |
| `MQTTClientConfig` model | Pydantic configuration with Field constraints | Complete |
| `ClientState` enum | Connection state machine (INIT → CONNECTED → SHUTDOWN) | Complete |
| Client generation ID | Prevents stale callbacks from old client instances | Complete |
| Token authentication | Login via SDK Context + fetch dispatcher token | Complete |
| Subscribe/unsubscribe | Topic subscription with callback dispatch + replay on reconnect | Complete |
| Auto-reconnect | Exponential backoff + jitter, state guard, no duplicate threads | Complete |
| Token refresh | Controlled reconnect before expiration (no live header mutation) | Complete |
| Callback isolation | Per-callback try/except in hot path | Complete |
| Statistics | Enhanced metrics with timestamps and state tracking | Complete |
| Unit tests | 53 tests with mocked SDK and paho-mqtt, all passing | Complete |
| Integration test | Sandbox smoke test (scripts/test_mqtt_connection.py) | Complete |
| Plan document | This implementation plan | Complete |

### Out of Scope (Future Phases)

- Protobuf parsing and adapter logic (Phase 2)
- Dispatcher and event queue (Phase 3)
- Example scripts (Phase 4)
- README and documentation updates (Phase 5)

---

## Design Decisions

### 1. Synchronous Threading Model (Intentional Deviation from Async-First)

**Decision:** Use synchronous paho-mqtt with threading rather than async/await.

**Rationale:** The project's architectural principles require async-first I/O, but the PLAN.md explicitly designed a threading model for performance:
- The MQTT message callback runs **inline** in the IO thread with zero scheduling overhead
- No event loop dispatch latency (critical for <200us target)
- `paho-mqtt` 1.6.1 (the SDK's pinned dependency) is synchronous
- The hot path (`on_message` → callback) has zero async overhead

This deviation is justified and documented in PLAN.md §Concurrency Model.

### 2. SDK Context for Authentication

**Decision:** Use `settrade_v2.context.Context` for login and token management.

**Rationale:**
- Reuses the SDK's battle-tested ECDSA signing and token refresh logic
- Avoids reimplementing the authentication protocol
- `Context.request()` auto-refreshes access tokens when near expiry
- Keeps the adapter focused on transport, not auth

### 3. Manual Reconnect with State Guard (Not paho Built-in)

**Decision:** Implement manual reconnection in a background thread with a `_reconnecting` flag guarded by a lock to prevent duplicate reconnect threads.

**Rationale:**
- paho's built-in reconnect reuses the same credentials (stale token)
- We need to refresh the access token and fetch a fresh dispatcher token
- Manual reconnect gives full control over the reconnect flow
- **State guard** prevents multiple `on_disconnect` events from spawning duplicate reconnect threads
- Background thread avoids blocking the MQTT IO thread

**Implementation:**
```python
def _schedule_reconnect(self) -> None:
    with self._reconnect_lock:
        if self._reconnecting or self._state == ClientState.SHUTDOWN:
            return
        self._reconnecting = True
        self._state = ClientState.RECONNECTING
    # Spawn reconnect thread...

def _reconnect_loop(self) -> None:
    try:
        # ... reconnect attempts with backoff + jitter ...
    finally:
        with self._reconnect_lock:
            self._reconnecting = False
```

### 4. Token Refresh via Controlled Reconnect (No Live Header Mutation)

**Decision:** Token refresh triggers a full controlled reconnect cycle (disconnect → re-auth → new client → connect). No attempt to update WSS headers on a live connection.

**Rationale:**
- paho-mqtt does **not** support updating WebSocket headers after connection is established
- Attempting `ws_set_options()` on a live connection has no effect
- The broker will drop the connection when the token expires regardless
- A **controlled reconnect** is the only safe approach

**Flow:**
```
Token about to expire:
  1. loop_stop() — stop MQTT IO thread
  2. disconnect() — clean disconnect
  3. Refresh access token (Context.refresh() via _fetch_host_token auto-refresh)
  4. Fetch new dispatcher token (GET /api/dispatcher/v3/{broker_id}/token)
  5. Create new MQTT client with fresh headers (increments generation ID)
  6. connect() + loop_start()
  7. Replay all subscriptions (on_connect callback)
```

**Token refresh also calls `_schedule_reconnect()`** and relies on the same state guard to prevent two concurrent reconnect flows (e.g., timer + network drop happening simultaneously).

**Note:** Controlled reconnect may cause brief downtime (~1-3s) if refresh coincides with network instability. This is acceptable for market data feeds where freshness > reliability.

### 5. Lock-Free Hot Path with Callback Isolation

**Decision:** No locks in `on_message` callback. Each callback is wrapped in `try/except` to prevent one faulty callback from killing the MQTT IO thread.

**Rationale:**
- `dict.get()` is thread-safe in CPython (GIL guarantees atomic lookup)
- `on_message` only reads the subscriptions dict, never mutates it
- Subscription changes (subscribe/unsubscribe) are rare and use a lock
- **Callback isolation** is critical: a bug in one callback must not crash the entire feed
- Errors are counted in `_callback_errors` for monitoring

```python
def _on_message(self, client, userdata, msg):
    self._messages_received += 1
    callbacks = self._subscriptions.get(msg.topic)
    if callbacks is not None:
        for cb in callbacks:
            try:
                cb(msg.topic, msg.payload)
            except Exception:
                self._callback_errors += 1
                logger.exception("Callback error for topic %s", msg.topic)
```

### 6. Explicit Subscription Replay on Reconnect

**Decision:** The `_subscriptions` dict is the **single source of truth**. On every `on_connect` event, ALL subscriptions are replayed from this dict.

**Rationale:**
- MQTT broker loses all subscription state when using `clean_session=True`
- After reconnect (new client instance), no topics are subscribed
- Replay logic in `on_connect` ensures all topics are restored automatically
- No subscription is ever silently lost
- **Subscribing during RECONNECTING** will update the source-of-truth dict and will be replayed on the next successful `on_connect`

```python
def _on_connect(self, client, userdata, flags, rc):
    if rc == 0:
        self._state = ClientState.CONNECTED
        # Replay ALL subscriptions from source of truth
        with self._sub_lock:
            for topic in self._subscriptions:
                client.subscribe(topic)
```

### 7. Infinite Reconnect with Bounded Backoff + Jitter

**Decision:** Reconnect attempts are infinite (no max attempts) with bounded exponential backoff (max 30s) and random jitter (±20%).

**Rationale:**
- For trading infrastructure, the feed must always attempt to recover
- A max-attempt limit would leave the system permanently disconnected
- Bounded backoff prevents aggressive reconnect storms
- **Jitter** (`random.uniform(0.8, 1.2)`) prevents multiple instances from reconnecting in sync (thundering herd)
- Backoff **resets to minimum** after successful reconnect
- Shutdown event provides the only exit from the reconnect loop

### 8. Client Generation ID

**Decision:** Each new MQTT client instance increments a `_client_generation` counter. Callbacks check the generation to ignore messages from stale client instances.

**Rationale:**
- During reconnect, there is a brief window where the old client's IO thread may still fire callbacks
- The new client starts with a new generation ID
- `on_message` checks that the current generation matches before dispatching
- Prevents stale data from an old connection being processed after a new one is established

### 9. clean_session=True Semantics

**Decision:** Always use `clean_session=True` for MQTT connections.

**Implications:**
- **No QoS persistence** — Broker does not retain undelivered QoS 1/2 messages
- **No message replay** — Messages missed during disconnect are permanently lost
- **At-most-once semantics** — Each message is delivered at most once
- **Freshness over reliability** — This is correct for real-time market data where stale data is worthless
- Subscription state is not persisted — replay logic in `on_connect` handles this

### 10. State Transition Atomicity

**Decision:** Use `_state_lock` (a `threading.Lock`) for all state mutations. State is used for both observability and decision logic (reconnect guard, shutdown prevention).

**Rationale:**
- State is mutated by multiple threads: MQTT IO thread (`on_connect`, `on_disconnect`), reconnect thread, token refresh timer, main thread (`shutdown`)
- Without a lock, concurrent mutations could lead to inconsistent state
- The lock is only held for state reads/writes (nanoseconds) — zero hot-path impact

### 11. Connect Timeout

**Decision:** Use paho's `connect()` with a reasonable timeout. If `connect()` hangs (DNS resolution, TLS handshake), the reconnect thread will be stuck.

**Mitigation:** paho-mqtt `connect()` uses socket-level timeouts. We rely on the OS default TCP connect timeout (~30-75s). For additional safety, the reconnect loop checks `_shutdown_event` between attempts.

---

## State Machine

### Connection States

```
┌──────────┐
│   INIT   │─────── connect() ───────► CONNECTING
└──────────┘                           │
                                       │ on_connect (rc=0)
                                       ▼
                                  ┌───────────┐
                          ┌──────│ CONNECTED  │◄──── on_connect (rc=0)
                          │      └───────────┘
                          │           │
                          │           │ on_disconnect (rc!=0)
                          │           │ OR token refresh timer
                          │           ▼
                          │    ┌──────────────┐
                          │    │ RECONNECTING │──── backoff + jitter loop
                          │    └──────────────┘
                          │           │
                          │           │ success → CONNECTED
                          │           │ failure → retry with backoff
                          │
                          │ shutdown()
                          ▼
                     ┌──────────┐
                     │ SHUTDOWN │
                     └──────────┘
```

### State Transitions

| From | To | Trigger | Thread |
|------|----|---------|--------|
| `INIT` | `CONNECTING` | `connect()` called | Main |
| `CONNECTING` | `CONNECTED` | `on_connect` with `rc=0` | MQTT IO |
| `CONNECTING` | `RECONNECTING` | `on_connect` with `rc!=0` | MQTT IO |
| `CONNECTED` | `RECONNECTING` | `on_disconnect` with `rc!=0` | MQTT IO |
| `CONNECTED` | `RECONNECTING` | Token refresh timer triggers controlled reconnect | Token refresh |
| `RECONNECTING` | `CONNECTED` | Successful reconnect + `on_connect` `rc=0` | MQTT IO (new) |
| `RECONNECTING` | `RECONNECTING` | Failed attempt → backoff + jitter → retry | Reconnect |
| Any | `SHUTDOWN` | `shutdown()` called | Main |

### State Guards

- **`_state_lock`** (threading.Lock): All state mutations are atomic
- **`_reconnecting` flag** (guarded by `_reconnect_lock`): Prevents duplicate reconnect threads
- **`_client_generation`**: Prevents stale callbacks from old client instances
- **`_shutdown_event`**: Provides clean exit from all background threads
- **Backoff reset**: After successful reconnect, delay resets to `reconnect_min_delay`

---

## Implementation Steps

### Step 1: Project Setup

- Add test dependencies to `pyproject.toml`: `pytest`, `pytest-cov`
- Create `infra/__init__.py` package
- Create `tests/__init__.py` package

### Step 2: State Machine, Configuration, and Type Definitions

**File:** `infra/settrade_mqtt.py`

```python
class ClientState(str, Enum):
    INIT = "INIT"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    SHUTDOWN = "SHUTDOWN"

MessageCallback = Callable[[str, bytes], None]

class MQTTClientConfig(BaseModel):
    app_id: str
    app_secret: str
    app_code: str
    broker_id: str
    port: int = 443
    keepalive: int = 30
    reconnect_min_delay: float = 1.0   # Seconds
    reconnect_max_delay: float = 30.0  # Seconds
    token_refresh_before_exp_seconds: int = 100
```

### Step 3: SettradeMQTTClient Initialization

Key instance variables:
- `_config` — Pydantic configuration
- `_ctx` — SDK Context for authentication
- `_client` — paho MQTT client
- `_host`, `_token`, `_token_type` — Connection credentials
- `_expired_at` — Token expiration timestamp
- `_subscriptions` — Source-of-truth subscription dict
- `_sub_lock` — Lock for subscription mutations
- `_state` — Current ClientState
- `_state_lock` — Lock for state transitions
- `_reconnecting` — Flag to prevent duplicate reconnect threads
- `_reconnect_lock` — Lock for reconnect flag
- `_client_generation` — Counter for client instances
- `_shutdown_event` — Threading event for clean shutdown
- Counters: `_messages_received`, `_callback_errors`, `_reconnect_count`
- Timestamps: `_last_connect_ts`, `_last_disconnect_ts`

### Step 4: Authentication Flow

1. `_login()` — Create SDK `Context` and call `login()`
2. `_fetch_host_token()` — GET `/api/dispatcher/v3/{broker_id}/token` (auto-refreshes access token)
3. Extract `hosts[0]` and `token` from response

### Step 5: MQTT Client Creation with Generation ID

```python
def _create_mqtt_client(self) -> mqtt.Client:
    # Clean up previous client
    if self._client is not None:
        self._client.loop_stop()
        self._client.disconnect()

    self._client_generation += 1
    generation = self._client_generation

    client = mqtt.Client(clean_session=True, transport="websockets")
    client.tls_set()
    client.ws_set_options(
        headers={"Authorization": f"{self._token_type} {self._token}"},
        path=f"/api/dispatcher/v3/{self._config.broker_id}/mqtt",
    )

    # Bind callbacks with generation check
    client.on_connect = self._on_connect
    client.on_disconnect = self._on_disconnect
    client.on_message = lambda c, u, m: self._on_message(c, u, m, generation)

    return client
```

### Step 6: Message Dispatch (Hot Path) with Callback Isolation + Generation Check

```python
def _on_message(self, client, userdata, msg, generation):
    if generation != self._client_generation:
        return  # Ignore stale messages from old client
    self._messages_received += 1
    callbacks = self._subscriptions.get(msg.topic)
    if callbacks is not None:
        for cb in callbacks:
            try:
                cb(msg.topic, msg.payload)
            except Exception:
                self._callback_errors += 1
                logger.exception("Callback error for topic %s", msg.topic)
```

### Step 7: Auto-Reconnect with State Guard, Backoff + Jitter

```python
def _schedule_reconnect(self) -> None:
    with self._reconnect_lock:
        if self._reconnecting:
            return
        with self._state_lock:
            if self._state == ClientState.SHUTDOWN:
                return
            self._state = ClientState.RECONNECTING
        self._reconnecting = True

    thread = Thread(target=self._reconnect_loop, daemon=True, name="mqtt-reconnect")
    thread.start()

def _reconnect_loop(self) -> None:
    delay = self._config.reconnect_min_delay
    try:
        while not self._shutdown_event.is_set():
            try:
                self._fetch_host_token()
                client = self._create_mqtt_client()
                client.connect(host=self._host, port=self._config.port, ...)
                client.loop_start()
                self._client = client
                self._reconnect_count += 1
                return  # Success — backoff resets implicitly (next reconnect starts fresh)
            except Exception:
                jittered_delay = delay * random.uniform(0.8, 1.2)
                self._shutdown_event.wait(timeout=jittered_delay)
                delay = min(delay * 2, self._config.reconnect_max_delay)
    finally:
        with self._reconnect_lock:
            self._reconnecting = False
```

### Step 8: Token Refresh via Controlled Reconnect

- Background timer monitors `expired_at` timestamp
- When token is within `token_refresh_before_exp_seconds` of expiry:
  - Calls `_schedule_reconnect()` which uses the same state guard
  - Prevents duplicate reconnect if timer and network drop collide
- `_reconnect_loop()` always calls `_fetch_host_token()` which auto-refreshes access token via `Context.request()`

### Step 9: Graceful Shutdown

```python
def shutdown(self) -> None:
    with self._state_lock:
        self._state = ClientState.SHUTDOWN
    self._shutdown_event.set()  # All background threads exit
    # Reconnect loop checks shutdown and exits
    if self._client is not None:
        self._client.loop_stop()  # Stop IO thread first
        self._client.disconnect()  # Then disconnect
```

### Step 10: Unit Tests

- Configuration validation (defaults, constraints)
- State machine transitions
- Subscription management (add, remove, multiple callbacks, replay)
- Subscribe during RECONNECTING (added to source-of-truth, replayed on connect)
- Message dispatch (correct routing, callback isolation, error counting)
- Generation ID check (stale message rejection)
- Reconnect state guard (no duplicate threads)
- Backoff with jitter
- Shutdown safety (no reconnect after shutdown)
- Statistics tracking

---

## Callback Contract

### Rules for Message Callbacks

Callbacks registered via `subscribe(topic, callback)` **MUST** follow these rules:

1. **Non-blocking** — Callback must return quickly (<1ms). Heavy processing must be delegated downstream (e.g., `deque.append()` for Phase 3 dispatcher).
2. **No I/O** — No network calls, no disk writes, no database queries inside the callback.
3. **No locks** — No mutex acquisition inside the callback (risk of deadlock with MQTT IO thread).
4. **No thread spawn** — No `threading.Thread().start()` per message (this is what the SDK does wrong).
5. **Exception-safe** — Callbacks are isolated (`try/except` per callback), but exceptions are logged and counted. A consistently failing callback should be investigated.

### Signature

```python
def my_callback(topic: str, payload: bytes) -> None:
    """
    Args:
        topic: MQTT topic string (e.g., "proto/topic/bidofferv3/AOT")
        payload: Raw binary message payload (protobuf bytes)
    """
    ...
```

### Backpressure Limitation

This transport layer does **not** implement backpressure. If a callback is slow (>1ms), messages will queue in paho-mqtt's internal buffer, and the IO thread will block. The Phase 3 Dispatcher addresses this by providing a bounded `deque` that downstream consumers poll independently.

For Phase 1, it is the callback's responsibility to be fast. The adapter layer (Phase 2) will `deque.append()` which is <10us.

---

## Metrics & Observability

### Counters

| Metric | Type | Description |
|--------|------|-------------|
| `messages_received` | Counter | Total MQTT messages received |
| `callback_errors` | Counter | Callback exceptions caught (per-callback isolation) |
| `reconnect_count` | Counter | Total successful reconnections |

### Timestamps

| Metric | Type | Description |
|--------|------|-------------|
| `last_connect_ts` | float | `time.time()` of last successful connect |
| `last_disconnect_ts` | float | `time.time()` of last disconnect event |

### State

| Metric | Type | Description |
|--------|------|-------------|
| `state` | ClientState | Current connection state |
| `connected` | bool | Convenience flag (`state == CONNECTED`) |

### Access Pattern

```python
stats = client.stats()
# → {
#     "state": "CONNECTED",
#     "connected": True,
#     "messages_received": 150432,
#     "callback_errors": 0,
#     "reconnect_count": 1,
#     "last_connect_ts": 1739347200.0,
#     "last_disconnect_ts": 1739347100.0,
# }
```

---

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `infra/__init__.py` | CREATE | Package init with public exports |
| `infra/settrade_mqtt.py` | CREATE | MQTT transport: SettradeMQTTClient, MQTTClientConfig, ClientState |
| `tests/__init__.py` | CREATE | Test package init |
| `tests/test_settrade_mqtt.py` | CREATE | Unit tests for MQTT transport |
| `pyproject.toml` | MODIFY | Add pytest, pytest-cov dev dependencies |
| `docs/plan/low-latency-mqtt-feed-adapter/phase1-mqtt-transport.md` | CREATE | This plan document |
| `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md` | MODIFY | Phase 1 completion notes |

---

## Success Criteria

### Transport

- [x] MQTT client connects via WebSocket+SSL on port 443
- [x] Authentication uses token fetched via REST API
- [x] Authorization header and connect path set correctly
- [x] `subscribe(topic, callback)` registers and activates subscriptions
- [x] `on_message` dispatches to correct callbacks by topic
- [x] Multiple callbacks per topic supported
- [x] Subscriptions replayed on every reconnect (`_subscriptions` is source of truth)
- [x] Subscribe during RECONNECTING updates source-of-truth and replays on next connect

### Reliability

- [x] Auto-reconnect triggers on unexpected disconnect
- [x] Exponential backoff with jitter: 1s → 2s → 4s → ... → max 30s (±20%)
- [x] Backoff resets to min delay on successful reconnect
- [x] Reconnect state guard prevents duplicate reconnect threads
- [x] Token refresh via controlled reconnect (no live header mutation)
- [x] Token refresh timer and disconnect use same state guard (no dual reconnect)
- [x] Fresh credentials fetched on each reconnect attempt
- [x] Infinite reconnect with bounded backoff (no max attempts, explicitly documented)
- [x] Graceful shutdown prevents reconnect after shutdown

### Safety

- [x] Callback isolation: per-callback `try/except` in hot path
- [x] Client generation ID prevents stale callbacks from old instances
- [x] Callback contract documented (non-blocking, no I/O, no locks)
- [x] Backpressure limitation documented
- [x] Shutdown race condition prevented (state lock + shutdown event)
- [x] clean_session=True semantics documented (at-most-once, freshness > reliability)
- [x] State transitions are atomic (protected by `_state_lock`)

### Code Quality

- [x] State machine with explicit states (INIT, CONNECTING, CONNECTED, RECONNECTING, SHUTDOWN)
- [x] Complete type annotations on all public methods
- [x] Pydantic model for configuration with Field constraints
- [x] Comprehensive docstrings per documentation standards
- [x] No bare `except:` clauses
- [x] Structured logging (no print statements)

### Testing

- [x] Unit tests with mocked SDK Context and paho-mqtt Client (53 tests)
- [x] Tests for config validation, subscribe/unsubscribe, message dispatch
- [x] Tests for reconnect state guard, callback isolation, generation ID
- [x] Tests for backoff reset, jitter, and shutdown safety
- [x] All tests pass
- [x] Integration smoke test verified against Settrade sandbox (AOT bid/offer)

---

## Completion Notes

### Summary

Phase 1 MQTT Transport is fully implemented and verified. The `SettradeMQTTClient` connects to the Settrade MQTT broker via WebSocket+SSL, authenticates using the SDK's `Context` + `dispatch(Option(...))` pattern, and dispatches messages to registered callbacks.

### Issues Encountered

1. **SDK API mismatch**: `Context.request()` takes `(method, endpoint_full_url)`, not `(method, path=...)`. Resolved by using the SDK's `Option` + `dispatch()` pattern which also provides auto-refresh.
2. **SANDBOX detection**: The SDK maps `broker_id="SANDBOX"` → `"098"` and sets `environment="uat"` internally. Replicated this logic in `_login()`.
3. **Token refresh test timing**: Initial test design conflicted with shutdown event. Resolved with `side_effect=shutdown_after_call` pattern.

### Test Results

- **Unit tests**: 53 tests, all passing (0.31s)
- **Integration test**: Connected to `stream-test.settrade.com`, subscribed to `proto/topic/bidofferv3/AOT`, received 1 message (290 bytes), clean shutdown.

---

**Document Version:** 1.3
**Author:** AI Agent
**Status:** Complete
**Completed:** 2026-02-12
