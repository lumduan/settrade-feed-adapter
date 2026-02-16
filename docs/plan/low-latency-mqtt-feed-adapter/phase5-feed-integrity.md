# Phase 5: Feed Integrity & Silent Gap Mitigation Implementation Plan

**Feature:** Market Data Ingestion Layer - Phase 5: Feed Integrity & Silent Gap Mitigation
**Branch:** `feature/phase5-feed-integrity`
**Created:** 2026-02-16
**Status:** Complete
**Completed:** 2026-02-16
**Depends On:** Phase 4 (Complete)

---

## Table of Contents

1. [Overview](#overview)
2. [AI Prompt](#ai-prompt)
3. [Scope](#scope)
4. [Design Decisions](#design-decisions)
5. [Implementation Steps](#implementation-steps)
6. [File Changes](#file-changes)
7. [Success Criteria](#success-criteria)

---

## Overview

### Purpose

Phase 5 adds operational integrity monitoring to the Settrade Feed Adapter. While Phases 1-4 focused on performance and architecture (fast, typed, explicit pipeline), Phase 5 focuses on **production awareness and risk visibility**.

Settrade MQTT market data has specific characteristics that create operational risk:

- **Snapshot-based** — Each message is a full snapshot, not incremental delta
- **No exchange sequence IDs** — Cannot detect gaps at exchange level
- **No replay capability** — Missed messages during disconnect are unrecoverable
- **QoS 0 (at-most-once delivery)** — MQTT may drop messages under load

**This phase does NOT change protocol behavior.** It adds monitoring and mitigation mechanisms to provide operational visibility.

Evolution: From **"Low-latency adapter"** → **"Production-aware ingestion layer"**

### Key Deliverables

1. **`core/feed_health.py`** — `FeedHealthMonitor` with two-tier detection (global feed silence + per-symbol liveness), per-symbol gap overrides, startup-aware state, monotonic timestamps only
2. **`core/events.py`** — Add `connection_epoch` field and `is_auction()` helper using `BidAskFlag` enum
3. **`core/dispatcher.py`** — Add configurable EMA-based drop-rate tracking, `health()` method with lifetime counters
4. **`infra/settrade_mqtt.py`** — Add `_reconnect_epoch` counter, increment **after** resubscription in `_on_connect` (reconnects only)
5. **`infra/settrade_adapter.py`** — Propagate `connection_epoch` from MQTT client to events
6. **`examples/example_feed_health.py`** — Production guard rail pattern with real-world scenarios
7. **`tests/test_feed_health.py`** — Unit tests for feed health monitor (monotonic time mocking)
8. **`tests/test_dispatcher.py`** — Additional tests for EMA drop-rate and `health()`
9. **`tests/test_events.py`** — Tests for `connection_epoch` and `is_auction()`
10. **Updated `README.md`** — Silent gap documentation with drop-prone-point diagram
11. **Updated `PLAN.md`** — Phase 5 completion notes

### Parent Plan Reference

This implementation is part of the larger plan documented in:
- `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md`

---

## AI Prompt

The following prompt was used to generate this implementation:

```
Implement Phase 5: Feed Integrity & Silent Gap Mitigation for the Settrade Feed Adapter project.

1. Create a new git branch for this task.
2. Carefully read:
   - `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md` (focus on Phase 5: Feed Integrity & Silent Gap Mitigation)
   - `docs/plan/low-latency-mqtt-feed-adapter/phase4-benchmark-validation.md` (last implementation)
3. Draft a detailed implementation plan for Phase 5 in markdown, saved as `docs/plan/low-latency-mqtt-feed-adapter/phase5-feed-integrity.md`, following the format in `docs/plan/low-latency-mqtt-feed-adapter/phase1-mqtt-transport.md`. Include this prompt in the plan.
4. Implement all deliverables for Phase 5:
   - Add `FeedHealthMonitor` in `core/feed_health.py` with monotonic timestamp tracking per symbol, liveness detection, and API methods (`is_stale`, `stale_symbols`, `last_seen_gap_ms`)
   - Add EMA-based drop-rate tracking in `Dispatcher` (`core/dispatcher.py`) and expose `health()` method
   - Add `connection_epoch` field to event models (`core/events.py`), increment on reconnect (`infra/settrade_mqtt.py`), and propagate in adapter (`infra/settrade_adapter.py`)
   - Add `is_auction()` helper to event models for market period awareness
   - Add feed integrity metrics and guard rail examples in `examples/example_feed_health.py`
   - Write unit tests for all new features (`tests/test_feed_health.py`, `tests/test_dispatcher.py`)
   - Update README.md with explicit silent gap documentation and production guard rail example
5. Update documentation files with completion notes, date, issues, and checked items:
   - `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md`
   - `docs/plan/low-latency-mqtt-feed-adapter/phase5-feed-integrity.md`
6. Create a PR to GitHub with detailed commit and PR messages per `.github/instructions/git-commit.instructions.md`, including:
   - Summary of changes
   - List of files added/modified
   - Technical and user benefits
   - Testing performed and results
   - Any issues or notes from implementation

Files for reference:
- `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md`
- `docs/plan/low-latency-mqtt-feed-adapter/phase4-benchmark-validation.md`
- `docs/plan/low-latency-mqtt-feed-adapter/phase1-mqtt-transport.md`
- `.github/instructions/`
- All relevant source and test files

Expected deliverables:
- Markdown implementation plan for Phase 5 at `docs/plan/low-latency-mqtt-feed-adapter/phase5-feed-integrity.md` (including this prompt)
- Fully implemented and tested Feed Integrity & Silent Gap Mitigation features
- Updated documentation files with completion notes and checked items
- PR to GitHub with detailed commit and PR messages
```

---

## Scope

### In Scope (Phase 5)

| Component | Description | Status |
|-----------|-------------|--------|
| `FeedHealthMonitor` | Two-tier detection: global feed silence + per-symbol liveness (monotonic) | Complete |
| `FeedHealthConfig` | Config with `max_gap_seconds`, `per_symbol_max_gap` overrides | Complete |
| Startup-aware state | `is_feed_dead()` returns `False` before first event (unknown, not dead) | Complete |
| `connection_epoch` field | Reconnect awareness in event models (default 0) | Complete |
| `is_auction()` helper | Market period detection using `BidAskFlag` enum (no magic numbers) | Complete |
| `reconnect_epoch` counter | Counter in `SettradeMQTTClient`, increment after resubscription, reconnects only | Complete |
| Adapter propagation | `BidOfferAdapter` attaches `connection_epoch` to events | Complete |
| EMA drop-rate tracking | Configurable EMA alpha (default 0.01) in `Dispatcher.push()` | Complete |
| `Dispatcher.health()` | Health metrics: `drop_rate_ema`, `queue_utilization`, `total_dropped`, `total_pushed` | Complete |
| Drop warning threshold | Configurable warning threshold (default 0.01) in `DispatcherConfig` | Complete |
| Guard rail example | `examples/example_feed_health.py` with real-world scenarios | Complete |
| Feed health tests | `tests/test_feed_health.py` with monotonic time mocking | Complete |
| Dispatcher EMA tests | Additional tests in `tests/test_dispatcher.py` | Complete |
| Event model tests | Tests for `connection_epoch` and `is_auction()` in `tests/test_events.py` | Complete |
| README update | Silent gap documentation with drop-prone diagram | Complete |
| Plan updates | PLAN.md and this document with completion notes | Complete |

### Out of Scope (Future Phases)

- Cross-feed validation (requires historical storage)
- Anomaly detection (requires statistical models)
- Tick-by-tick persistence (requires separate logging system)
- Exchange-level sequence validation (protocol limitation)
- Prometheus/StatsD integration (optional future enhancement)
- Stagnant book detection (price unchanged tracking — future phase)

---

## Design Decisions

### 1. Monotonic Timestamps Only for Liveness Detection

**Decision:** Use `time.perf_counter_ns()` (monotonic) for all time-based stale detection, never `time.time()` (wall clock).

**Rationale:**
- Wall clock can jump due to NTP adjustments, causing false stale detection
- Especially relevant in the Thai market where some devices aggressively sync NTP
- Monotonic clock never goes backwards — immune to NTP drift
- Events already carry `recv_mono_ns` from Phase 2, so no new capture needed

### 2. Two-Tier Feed Health Detection

**Decision:** Implement both global feed silence and per-symbol liveness detection.

| Layer | Purpose | Method |
|-------|---------|--------|
| Global | Feed-wide silence detection | `is_feed_dead()` |
| Per-symbol | Per-symbol liquidity gap detection | `is_stale(symbol)` |

**Rationale:**
- If MQTT disconnects and reconnect fails → no events arrive → strategy needs a "entire feed is down" signal
- Some strategies subscribe to a single symbol — if illiquid, per-symbol stale may give false positives
- Global feed silence provides a stronger signal than per-symbol checks alone

### 3. Startup-Aware State (`is_feed_dead()` Edge Case)

**Decision:** `is_feed_dead()` returns `False` before the first event is received. Provide `has_ever_received()` for callers that need to distinguish "unknown" from "healthy".

**Rationale:**
- At system boot before market open: no events yet → `is_feed_dead() = True` would incorrectly cause the strategy to pause trading all day
- Before receiving any event, the state is "unknown" not "dead"
- Separate method `has_ever_received() -> bool` allows callers to check if monitoring has started
- After the first event, `is_feed_dead()` uses normal monotonic gap logic

**Implementation:**
```python
def is_feed_dead(self) -> bool:
    if self._global_last_event_mono_ns is None:
        return False  # Unknown state, not dead
    gap_ns = time.perf_counter_ns() - self._global_last_event_mono_ns
    return gap_ns > (self._config.max_gap_seconds * 1_000_000_000)

def has_ever_received(self) -> bool:
    return self._global_last_event_mono_ns is not None
```

### 4. Per-Symbol Gap Override

**Decision:** Support per-symbol `max_gap_seconds` override via `per_symbol_max_gap: dict[str, float]`.

**Rationale:**
- Different symbols have different activity patterns:
  - PTT ticks every ~50ms during market hours
  - Illiquid stocks may go 30+ minutes without ticks
- A global gap=5 seconds → illiquid symbols falsely marked stale
- Per-symbol override allows appropriate thresholds per symbol

**Configuration:**
```python
FeedHealthConfig(
    max_gap_seconds=5.0,
    per_symbol_max_gap={"RARE": 60.0},
)
```

Note: Use `Field(default_factory=dict)` to avoid mutable default trap.

### 5. `is_stale()` Semantic Clarity for Unknown Symbols

**Decision:** `is_stale(symbol)` returns `False` for never-seen symbols. Provide `has_seen(symbol) -> bool` for callers that need to distinguish "not tracked" from "healthy".

**Rationale:**
- Mixing "never seen" and "stale" semantics creates ambiguity
- A symbol that was never subscribed should not be reported as "stale"
- `has_seen(symbol)` allows callers to check if the symbol has been tracked
- This is cleaner than raising an exception (which would require try/except in poll loops)

### 6. EMA Alpha and Drop Warning Threshold Configurable

**Decision:** Make EMA alpha and drop warning threshold configurable via `DispatcherConfig`.

**Configuration:**
```python
DispatcherConfig(
    maxlen=100_000,
    ema_alpha=0.01,           # ~100-message half-life, user-tunable
    drop_warning_threshold=0.01,  # 1% drop rate warning
)
```

**Rationale:**
- `alpha=0.01` is good for typical throughput but may not suit all workloads
- At 100k msg/s, half-life of 100 messages → very rapid decay
- HFT strategy might tolerate only 0.1%; retail infrastructure might tolerate 5%
- Configurable thresholds make the dispatcher reusable across workloads

### 7. EMA-Based Drop Rate with Lifetime Counters in `health()`

**Decision:** Use EMA for real-time signal AND expose lifetime counters.

**`health()` return value:**
```python
{
    "drop_rate_ema": float,      # Smoothed drop rate (0.0 = no drops)
    "queue_utilization": float,  # len(queue) / maxlen
    "total_dropped": int,        # Cumulative drops (for forensics)
    "total_pushed": int,         # Cumulative pushes (for forensics)
}
```

### 8. Connection Epoch Increment Timing

**Decision:** Increment `_reconnect_epoch` **after** full resubscription in `_on_connect`, only for reconnects (not initial connect).

**Detection:** Use `self._last_connect_ts > 0` to determine if this is a reconnect (previously connected at least once).

**Rationale:**
- Initial connection: epoch stays 0 (no data was missed)
- First reconnect: epoch becomes 1 (data may have been missed)
- Incrementing after subscription replay guarantees subscriptions are active when strategy sees new epoch

### 9. `is_auction()` Uses `BidAskFlag` Enum

**Decision:** Use `BidAskFlag.ATO` and `BidAskFlag.ATC` constants instead of raw integers.

**Implementation:**
```python
def is_auction(self) -> bool:
    return (
        self.bid_flag in (BidAskFlag.ATO, BidAskFlag.ATC)
        or self.ask_flag in (BidAskFlag.ATO, BidAskFlag.ATC)
    )
```

### 10. `perf_counter_ns()` Reuse Optimization

**Decision:** Accept optional `now_ns` parameter in `is_stale()`, `is_feed_dead()`, and `stale_symbols()` to allow callers to reuse a single `time.perf_counter_ns()` call across multiple checks.

**Rationale:**
- `perf_counter_ns()` costs ~30-80ns per call
- In a strategy loop checking 10+ symbols per poll, this adds up
- Optional parameter — callers who don't care can omit it (default calls internally)

```python
def is_stale(self, symbol: str, now_ns: int | None = None) -> bool:
    now = now_ns or time.perf_counter_ns()
    ...
```

### 11. FeedHealthMonitor Thread Safety

**Decision:** Explicitly document as NOT thread-safe, single consumer thread only.

**Rationale:**
- Dict updates are not truly atomic under concurrent writes
- Designed to be called from the strategy/main thread poll loop
- Consistent with SPSC pattern of the dispatcher

### 12. Signal Exposure, Not Enforcement

**Decision:** The adapter exposes health signals but does not enforce trading decisions.

**Rationale:**
- Strategy decides what to do with signals
- Different strategies have different tolerance thresholds
- Separation of concerns — monitoring vs decision-making

---

## Implementation Steps

### Step 1: Add `connection_epoch` and `is_auction()` to Event Models

**File:** `core/events.py`

- Add `connection_epoch: int = Field(default=0, ge=0, ...)` to `BestBidAsk` and `FullBidOffer`
- Add `is_auction() -> bool` method using `BidAskFlag` enum to both models
- Update docstrings and module doc

### Step 2: Add `reconnect_epoch` to SettradeMQTTClient

**File:** `infra/settrade_mqtt.py`

- Add `_reconnect_epoch: int = 0` instance variable
- Increment in `_on_connect()` **after** subscription replay, only when `self._last_connect_ts > 0` (reconnect, not initial connect)
- Add `reconnect_epoch` property for external access

### Step 3: Propagate `connection_epoch` in BidOfferAdapter

**File:** `infra/settrade_adapter.py`

- Pass `connection_epoch=self._mqtt_client.reconnect_epoch` in both `_parse_best_bid_ask()` and `_parse_full_bid_offer()` `model_construct()` calls

### Step 4: Add EMA Drop-Rate Tracking to Dispatcher

**File:** `core/dispatcher.py`

- Add `ema_alpha: float = Field(default=0.01, ...)` and `drop_warning_threshold: float = Field(default=0.01, ...)` to `DispatcherConfig`
- Add `_drop_rate_ema: float = 0.0` instance variable
- Update `push()` to compute EMA after drop detection
- Add warning log when `drop_rate_ema > threshold` (using configurable threshold)
- Add `health() -> dict[str, float | int]` method
- Reset `_drop_rate_ema` in `clear()`

### Step 5: Create FeedHealthMonitor

**File:** `core/feed_health.py`

- `FeedHealthConfig(BaseModel)` with:
  - `max_gap_seconds: float = 5.0`
  - `per_symbol_max_gap: dict[str, float] = Field(default_factory=dict)`
- `FeedHealthMonitor`:
  - Two-tier: `_global_last_event_mono_ns` + `_last_event_mono_ns` per-symbol dict
  - Startup-aware: `is_feed_dead()` returns `False` before first event
  - API: `on_event()`, `is_stale()`, `stale_symbols()`, `last_seen_gap_ms()`, `is_feed_dead()`, `has_ever_received()`, `has_seen()`
  - Optional `now_ns` parameter for `perf_counter_ns()` reuse
  - Thread safety documented as NOT thread-safe

### Step 6: Create Feed Health Example

**File:** `examples/example_feed_health.py`

- Full pipeline with `FeedHealthMonitor`
- Three guard rail patterns:
  1. `is_feed_dead()` → pause trading
  2. `health()["drop_rate_ema"] > threshold` → reduce size
  3. `connection_epoch` change → reinitialize state
- Auction period detection via `is_auction()`

### Step 7: Write Unit Tests

**Files:** `tests/test_feed_health.py`, `tests/test_dispatcher.py`, `tests/test_events.py`

### Step 8: Update README and Plan Documents

---

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `core/feed_health.py` | CREATE | Two-tier feed health monitor (global + per-symbol, startup-aware, monotonic only) |
| `core/events.py` | MODIFY | Add `connection_epoch: int` field and `is_auction()` method |
| `core/dispatcher.py` | MODIFY | Add configurable EMA drop-rate tracking and `health()` with lifetime counters |
| `core/__init__.py` | MODIFY | Export `FeedHealthMonitor`, `FeedHealthConfig` |
| `infra/settrade_mqtt.py` | MODIFY | Add `_reconnect_epoch` counter (after resubscription, reconnects only) |
| `infra/settrade_adapter.py` | MODIFY | Propagate `connection_epoch` from MQTT client to events |
| `examples/example_feed_health.py` | CREATE | Production guard rail pattern with real-world scenarios |
| `tests/test_feed_health.py` | CREATE | Unit tests for two-tier feed health monitor |
| `tests/test_dispatcher.py` | MODIFY | Add tests for EMA drop-rate, `health()`, configurable thresholds |
| `tests/test_events.py` | MODIFY | Add tests for `connection_epoch` and `is_auction()` |
| `README.md` | MODIFY | Expand Feed Health section with diagram and production examples |
| `docs/plan/.../PLAN.md` | MODIFY | Phase 5 completion notes |
| `docs/plan/.../phase5-feed-integrity.md` | CREATE+MODIFY | This plan document |

---

## Success Criteria

### Functional Requirements

- [x] All events include `connection_epoch` field (default 0)
- [x] `FeedHealthMonitor` detects stale feeds per symbol using **monotonic timestamps only**
- [x] `is_feed_dead()` detects global feed silence, returns `False` before first event (startup-aware)
- [x] `has_ever_received()` distinguishes "unknown" from "healthy"
- [x] Per-symbol gap override via `per_symbol_max_gap` config
- [x] `is_stale()` returns `False` for never-seen symbols, `has_seen()` for distinction
- [x] `connection_epoch` increments on MQTT reconnect **after** subscription replay (not initial connect)
- [x] `dispatcher.health()` returns EMA drop-rate, queue utilization, AND lifetime counters
- [x] EMA alpha and drop warning threshold are configurable via `DispatcherConfig`
- [x] `is_auction()` uses `BidAskFlag` enum (no magic numbers)
- [x] Optional `now_ns` parameter for `perf_counter_ns()` reuse in hot loops
- [x] Strategy guard rail example demonstrates feed dead, drop rate, and reconnect handling
- [x] README includes ASCII diagram showing drop-prone points in data flow

### Architectural Requirements

- [x] No locks added to hot path (minimal latency impact)
- [x] EMA update is O(1) with no memory growth (single float per dispatcher)
- [x] Monotonic timestamps prevent NTP-related false positives
- [x] Per-symbol tracking with configurable gap thresholds
- [x] Explicit signal exposure (no hidden enforcement logic)
- [x] FeedHealthMonitor thread safety explicitly documented (single consumer thread only)

### Testing Requirements

- [x] Unit tests for `FeedHealthMonitor` with monotonic time manipulation
- [x] Unit tests for `is_feed_dead()` startup state (returns False before first event)
- [x] Unit tests for `has_ever_received()` and `has_seen()`
- [x] Unit tests for per-symbol gap override
- [x] Unit tests for EMA drop-rate calculation with known sequences
- [x] Unit tests for configurable EMA alpha and drop warning threshold
- [x] Unit tests for `health()` including lifetime counters
- [x] Unit tests for `is_auction()` with different flag combinations (including mixed)
- [x] Unit tests for `connection_epoch` field in event models
- [x] Test that wall clock jump does NOT trigger false stale detection
- [x] All existing tests continue to pass (no regressions)

---

**Document Version:** 1.2
**Author:** AI Agent
**Status:** Complete
