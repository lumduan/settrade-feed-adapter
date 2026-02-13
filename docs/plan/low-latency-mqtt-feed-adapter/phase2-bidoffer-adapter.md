# Phase 2: BidOffer Adapter Implementation Plan

**Feature:** Low-Latency MQTT Feed Adapter - Phase 2: Adapter for BidOffer
**Branch:** `feature/phase2-bidoffer-adapter`
**Created:** 2026-02-13
**Status:** Complete (2026-02-13)
**Depends On:** Phase 1 (Complete)

---

## Table of Contents

1. [Overview](#overview)
2. [AI Prompt](#ai-prompt)
3. [Scope](#scope)
4. [Design Decisions](#design-decisions)
5. [Hot Path Allocation Strategy](#hot-path-allocation-strategy)
6. [Float Precision Contract](#float-precision-contract)
7. [Thread Ownership Contract](#thread-ownership-contract)
8. [Data Models](#data-models)
9. [Implementation Steps](#implementation-steps)
10. [Callback Contract](#callback-contract)
11. [Metrics & Observability](#metrics--observability)
12. [File Changes](#file-changes)
13. [Success Criteria](#success-criteria)

---

## Overview

### Purpose

Phase 2 implements the BidOffer adapter layer that sits between the MQTT transport (Phase 1) and the dispatcher (Phase 3). It is responsible for:

1. **Protobuf parsing** — Parse `BidOfferV3` binary messages using betterproto's `.parse()` method
2. **Money conversion** — Convert `Money(units, nanos)` to `float` via fast integer arithmetic (`units + nanos * 1e-9`), avoiding SDK's `Decimal` overhead
3. **Event normalization** — Transform raw protobuf fields into clean Pydantic event models (`BestBidAsk`, `FullBidOffer`)
4. **Symbol subscription management** — Subscribe to per-symbol topics (`proto/topic/bidofferv3/{symbol}`) via the MQTT client
5. **Event forwarding** — Push normalized events to a callback (dispatcher in Phase 3)

### Parent Plan Reference

This implementation is part of the larger plan documented in:
- `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md`

### Key Deliverables

1. **`core/__init__.py`** — Core package initialization with public exports
2. **`core/events.py`** — Pydantic event models (`BestBidAsk`, `FullBidOffer`, `BidAskFlag`)
3. **`infra/settrade_adapter.py`** — `BidOfferAdapter` with protobuf parsing and event normalization
4. **`tests/test_events.py`** — Unit tests for event models
5. **`tests/test_settrade_adapter.py`** — Unit tests for adapter with mocked protobuf
6. **Updated `infra/__init__.py`** — Add adapter exports
7. **This plan document** — Phase 2 implementation plan

---

## AI Prompt

The following prompt was used to generate this implementation:

```
Implement Phase2

1. Carefully read the following documentation files:
   - docs/plan/low-latency-mqtt-feed-adapter/PLAN.md (focus on Phase 2: Adapter for BidOffer)
   - docs/plan/low-latency-mqtt-feed-adapter/phase1-mqtt-transport.md (for implementation and plan format reference)

2. Create a new git branch for this task, following project naming conventions.

3. Before coding, develop a detailed implementation plan for Phase 2: Adapter for BidOffer. Your plan must:
   - Be written in markdown and saved to docs/plan/low-latency-mqtt-feed-adapter/
   - Follow the format used in phase1-mqtt-transport.md
   - Include the full prompt you used for the AI agent as a section in the plan

4. Implement the BidOffer adapter according to the plan, ensuring:
   - Full type safety (explicit type annotations everywhere)
   - All data structures use Pydantic models with field descriptions and constraints
   - All I/O is async/await (no synchronous I/O)
   - Comprehensive error handling with typed exceptions, retry logic, and structured logging
   - Complete and consistent docstrings for all public functions, including parameter types, return types, exceptions, and usage examples
   - All configuration uses Pydantic Settings
   - All code follows the import organization and architectural standards in .github/instructions/

5. Update docs/plan/low-latency-mqtt-feed-adapter/PLAN.md:
   - Mark Phase 2 as completed, with date and any notes on issues or testing outcomes

6. When the implementation is complete:
   - Ensure all tests pass and code quality checks are satisfied
   - Create a pull request to GitHub with a detailed commit message and PR message, following the project's commit standards (see .github/instructions/git-commit.instructions.md)
   - Include a summary of what was done, files changed, benefits, and any testing or validation performed

Files for reference:
- docs/plan/low-latency-mqtt-feed-adapter/PLAN.md
- docs/plan/low-latency-mqtt-feed-adapter/phase1-mqtt-transport.md
- .github/instructions/
- All relevant source and test files

Expected deliverables:
- New git branch for Phase 2
- Markdown plan for Phase 2 in docs/plan/low-latency-mqtt-feed-adapter/
- Fully implemented and tested BidOffer adapter
- Updated PLAN.md with completion notes
- Comprehensive PR with detailed commit and PR messages
```

---

## Scope

### In Scope (Phase 2)

| Component | Description | Status |
|-----------|-------------|--------|
| `BestBidAsk` model | Pydantic model for top-of-book bid/ask snapshot | Pending |
| `FullBidOffer` model | Pydantic model for full 10-level depth book | Pending |
| `BidAskFlag` enum | IntEnum matching protobuf `BidOfferV3BidAskFlag` | Pending |
| `money_to_float()` | Fast Money → float conversion (no Decimal) | Pending |
| `BidOfferAdapter` class | Adapter subscribing to BidOfferV3 topics, parsing protobuf, forwarding events | Pending |
| `BidOfferAdapterConfig` model | Pydantic config for adapter (full_depth mode) | Pending |
| Unit tests for events | Pydantic model creation, immutability, validation | Pending |
| Unit tests for adapter | Protobuf parsing, event forwarding, error handling, stats | Pending |
| Plan document | This implementation plan | In Progress |

### Out of Scope (Future Phases)

- Dispatcher and event queue (Phase 3)
- Example scripts (Phase 4)
- README and documentation updates (Phase 5)
- Other adapters (PriceInfo, Candlestick, ExchangeInfo)

---

## Design Decisions

### 1. Pydantic Models for Event Types (Project Standard Compliance)

**Decision:** Use Pydantic `BaseModel` with `frozen=True` for event models instead of `@dataclass(slots=True)`.

**Rationale:** The project's architectural principles require all data structures to use Pydantic models with field descriptions and constraints. The original PLAN.md designed `@dataclass(slots=True)` for hot-path performance, but Pydantic v2's Rust-based core makes model construction fast enough for the <200us latency target.

**Critical:** All hot-path construction MUST use `model_construct()` to skip validation entirely. See [Hot Path Allocation Strategy](#hot-path-allocation-strategy).

### 2. Callback-Based Event Forwarding (Phase 3 Decoupling)

**Decision:** The adapter accepts a generic callback `Callable[[BestBidAsk | FullBidOffer], None]` rather than depending on a specific dispatcher class.

**Rationale:**
- Phase 3 (Dispatcher) is not yet implemented
- The callback pattern allows the adapter to work with any consumer: a dispatcher's `push()` method, a deque's `append()`, or a simple print function
- When Phase 3 is implemented, `dispatcher.push` will be passed as the callback
- This matches the PLAN.md's architecture where the adapter feeds events into the dispatcher

### 3. Dual Event Mode (BestBidAsk vs FullBidOffer)

**Decision:** The adapter supports two modes via the `full_depth` config parameter:
- `full_depth=False` (default): Produces `BestBidAsk` (top-of-book only) — minimal allocation, fastest
- `full_depth=True`: Produces `FullBidOffer` (all 10 levels) — more data, higher allocation

**Rationale:** Most trading strategies only need top-of-book (best bid/ask). Full depth is needed for order book reconstruction or market microstructure analysis. Making this configurable avoids unnecessary object creation for the common case.

**Performance caveat:** FullDepth mode allocates 4 tuples + 40 float objects per message. At high message rates this creates significant GC pressure. **FullDepth mode is not intended for sub-100us strategies.** Use BestBidAsk (default) for ultra-low-latency requirements.

### 4. Integer Arithmetic for Money Conversion (Zero Decimal)

**Decision:** Convert `Money(units, nanos)` to `float` via `units + nanos * 1e-9`.

**Rationale:** The SDK uses `Decimal(units) + Decimal(nanos) / Decimal("1_000_000_000")` which allocates 3 Decimal objects per price field. Our approach uses a single float multiplication and addition — two CPU instructions with no heap allocation. For BestBidAsk (2 prices), this saves 6 Decimal allocations per message. For FullBidOffer (20 prices), this saves 60 Decimal allocations per message.

**Precision risk:** See [Float Precision Contract](#float-precision-contract).

### 5. BidAskFlag as IntEnum (Typed Convenience)

**Decision:** Define a `BidAskFlag(IntEnum)` in `core/events.py` that mirrors the protobuf `BidOfferV3BidAskFlag` enum.

**Rationale:**
- Gives downstream consumers a typed way to check flags without importing protobuf modules
- IntEnum is interchangeable with `int` so `BestBidAsk.bid_flag` works with both `== 1` and `== BidAskFlag.NORMAL`
- Avoids coupling strategy code to the SDK's protobuf definitions

### 6. Separated Error Isolation: Parse vs Callback

**Decision:** Use two separate `try/except` blocks in `_on_message` — one for protobuf parsing, one for the event callback.

**Rationale:** If both errors are caught in a single `try` block, a callback failure (downstream bug) would be incorrectly counted as a `parse_error`. Separating them gives precise observability:
- `parse_errors` — protobuf deserialization failures (data issue)
- `callback_errors` — downstream consumer failures (logic issue)

This is critical for production debugging: knowing whether dropped events are caused by bad data vs a buggy strategy.

```python
# CORRECT — separated error isolation
try:
    msg = BidOfferV3().parse(payload)
    event = BestBidAsk.model_construct(...)
except Exception:
    self._parse_errors += 1
    return

try:
    self._on_event(event)
except Exception:
    self._callback_errors += 1
```

### 7. Lock-Free Hot Path Counters

**Decision:** No locks on counter increments in the hot path. Use a lock only in `stats()` for consistent reads.

**Rationale:** CPython's GIL guarantees atomic integer increment (`STORE_FAST` + `BINARY_ADD`). Adding a lock (~80-120ns per acquire/release) at 50k msg/sec is measurable overhead. Instead:
- `_on_message` increments counters with bare `+= 1` (GIL-atomic in CPython)
- `stats()` acquires `_counter_lock` and reads all counters in a consistent snapshot
- Slight read inaccuracy (stats may see partially-updated counters) is acceptable for metrics

**CPython dependency:** This relies on CPython's GIL. Document same assumption as Phase 1's deque atomicity.

### 8. Dual Timestamps: Wall Clock + Monotonic

**Decision:** Event models include both `recv_ts` (`time.time_ns()`) and `recv_mono_ns` (`time.perf_counter_ns()`).

**Rationale:**
- `time.time_ns()` — Wall clock. Subject to NTP adjustment and system clock jumps. Useful for correlating events with external timestamps (exchange time, logs).
- `time.perf_counter_ns()` — Monotonic. Never goes backwards. Correct for latency measurement (`t2 - t1`).

Using only `time.time_ns()` for latency measurement would produce incorrect results during NTP adjustments. Using only `time.perf_counter_ns()` would make it impossible to correlate with wall-clock events.

### 9. Explicit Field Unroll in FullDepth (No getattr)

**Decision:** Use explicit field access for all 10 bid/ask price and volume levels instead of `getattr(msg, f"bid_price{i}")` loops.

**Rationale:** `getattr` + f-string formatting in a loop introduces per-iteration overhead:
- f-string allocation: `f"bid_price{i}"` creates a new string object each iteration
- Dynamic attribute lookup: `getattr()` does a dict lookup + string hash per call
- 40 iterations per message (10 prices × 2 sides + 10 volumes × 2 sides)

Explicit unroll trades code elegance for speed — correct for a hot path. The field names are fixed by the protobuf schema and will not change without a version bump.

```python
# CORRECT — explicit unroll (no dynamic allocation)
bid_prices: tuple[float, ...] = (
    msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
    msg.bid_price2.units + msg.bid_price2.nanos * 1e-9,
    msg.bid_price3.units + msg.bid_price3.nanos * 1e-9,
    # ... all 10 levels
)

# WRONG — dynamic lookup in hot path
bid_prices = tuple(
    getattr(msg, f"bid_price{i}").units
    + getattr(msg, f"bid_price{i}").nanos * 1e-9
    for i in range(1, 11)
)
```

### 10. Protobuf Instance Reuse (Future Optimization)

**Decision:** Create a new `BidOfferV3()` instance per message for now. Document reuse as a future optimization.

**Rationale:** betterproto's `.parse()` populates the instance in-place and returns `self`. In theory, reusing a single instance (`self._msg = BidOfferV3()` → `self._msg.parse(payload)`) would eliminate one allocation per message. However:
- Correctness risk: betterproto may accumulate state across parses if fields are not fully overwritten
- Thread safety: if the MQTT client ever changes threading model, shared state is dangerous
- Marginal gain: `BidOfferV3()` construction is ~1-2us — small relative to total parse time

**Future:** After Phase 2 is stable and benchmarked, profile whether `BidOfferV3()` construction is a measurable bottleneck. If so, test instance reuse with betterproto's specific version to verify no state leakage.

### 11. Logging Rate Limit Strategy

**Decision:** Document that repeated callback/parse errors may spam logs. Rate limiting is deferred to Phase 5 (observability).

**Rationale:** In the current implementation, `logger.exception()` is called on every error. If a callback bug causes a continuous error loop (e.g., 50k errors/sec), this will overwhelm the log system.

**Mitigation (current):** The separated error counters (`parse_errors`, `callback_errors`) provide a quick way to detect error storms via `stats()` without relying on log output.

**Future (Phase 5):** Implement rate-limited logging for hot-path errors. Pattern:
```python
# Future: log first N errors per window, then suppress
if self._callback_errors < 10 or self._callback_errors % 1000 == 0:
    logger.exception("Event callback error for %s (total=%d)", topic, self._callback_errors)
```

---

## Hot Path Allocation Strategy

### Critical Rule: `model_construct()` Only

All event creation in the hot path (`_on_message`) MUST use `model_construct()`:

```python
# CORRECT — hot path (no validation, no conversion, no alias mapping)
event = BestBidAsk.model_construct(
    symbol=msg.symbol,
    bid=msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
    ask=msg.ask_price1.units + msg.ask_price1.nanos * 1e-9,
    bid_vol=msg.bid_volume1,
    ask_vol=msg.ask_volume1,
    bid_flag=int(msg.bid_flag),
    ask_flag=int(msg.ask_flag),
    recv_ts=time.time_ns(),
    recv_mono_ns=time.perf_counter_ns(),
)

# WRONG — DO NOT USE in hot path (triggers full Pydantic validation)
event = BestBidAsk(
    symbol=msg.symbol,
    bid=money_to_float(msg.bid_price1),
    ...
)
```

### What `model_construct()` Skips

- No field validation (type checking, constraints)
- No `list → tuple` conversion
- No intermediate `__dict__` creation (frozen model)
- No field alias mapping
- No dynamic attribute lookup
- No `__init__` overhead (direct `__dict__` assignment)

### Allocation Budget per Message (BestBidAsk Mode)

| Allocation | Count | Size | Notes |
|-----------|-------|------|-------|
| `BidOfferV3()` | 1 | ~600 bytes | betterproto message instance |
| `BestBidAsk` instance | 1 | ~200 bytes | Pydantic model (frozen) |
| float objects | 2 | ~56 bytes | bid + ask prices |
| **Total per message** | **~4 objects** | **~856 bytes** | |

### Allocation Budget per Message (FullBidOffer Mode)

| Allocation | Count | Size | Notes |
|-----------|-------|------|-------|
| `BidOfferV3()` | 1 | ~600 bytes | betterproto message instance |
| `FullBidOffer` instance | 1 | ~300 bytes | Pydantic model (frozen) |
| float objects | 20 | ~560 bytes | 10 bid + 10 ask prices |
| tuple objects | 4 | ~400 bytes | bid_prices, ask_prices, bid_volumes, ask_volumes |
| int objects | 20 | ~560 bytes | 10 bid + 10 ask volumes |
| **Total per message** | **~46 objects** | **~2,420 bytes** | **Not for sub-100us strategies** |

### What We Avoid (SDK Overhead)

- No `Decimal` allocation (SDK: 3 Decimal objects per Money field x 20 fields = 60 allocations)
- No `.to_dict()` (SDK: allocates dict + string keys for every field)
- No `Casing.SNAKE` string transformation
- No `include_default_values` iteration
- No intermediate dict before final object
- No `getattr()` / f-string in loops (explicit field unroll)

---

## Float Precision Contract

### Precision Characteristics

IEEE 754 double precision provides ~15-17 significant decimal digits. For SET market prices:

| Price Range | Tick Size | Float Precision | Status |
|-------------|-----------|-----------------|--------|
| 0.01 - 1.99 | 0.01 | 15+ digits | Safe |
| 2.00 - 4.98 | 0.02 | 15+ digits | Safe |
| 5.00 - 99.75 | 0.25 | Exact | Safe |
| 100.00 - 999.00 | 0.50 | Exact | Safe |

### Known Risk: Downstream Comparison

Float arithmetic can produce rounding artifacts:

```python
# DANGER: may fail due to float representation
assert (100.01 - 100.00) == 0.01  # Could be 0.009999999...

# CORRECT: use tolerance for price comparison
from math import isclose
assert isclose(price_a, price_b, abs_tol=1e-9)
```

### Downstream Contract

**All downstream strategy code MUST compare prices using tolerance, not exact equality.**

Recommended pattern:
```python
PRICE_TOLERANCE: float = 1e-9

def prices_equal(a: float, b: float) -> bool:
    return abs(a - b) < PRICE_TOLERANCE
```

### Optional: Raw Money Preservation

For strategies requiring exact arithmetic (arbitrage, spread calculation), the `FullBidOffer` model can be extended in future to include raw `units`/`nanos` fields. This is not implemented in Phase 2 but is architecturally straightforward.

---

## Thread Ownership Contract

### Thread Model

```
Main Thread
  |- Create adapter: BidOfferAdapter(config, client, cb)
  |- Subscribe symbols: adapter.subscribe("AOT")
  |- Read stats: adapter.stats()
  |- Unsubscribe: adapter.unsubscribe("AOT")

MQTT IO Thread (paho loop_start)
  |- Receives binary messages
  |- Calls adapter._on_message() inline
  |   |- Parse protobuf
  |   |- model_construct() -> event
  |   |- on_event(event) callback
  |- Counter increments (lock-free, GIL-atomic)
```

### Rules

1. **`subscribe()` and `unsubscribe()`** — MUST be called from the main thread. These methods modify the internal `_subscribed_symbols` set and call `mqtt_client.subscribe()` which mutates paho's internal state. Calling from multiple threads is unsafe.

2. **`_on_message()`** — Called exclusively from the MQTT IO thread via paho's message dispatch. Never call directly from application code.

3. **`stats()`** — Thread-safe. Can be called from any thread. Acquires `_counter_lock` for a consistent read snapshot.

4. **`on_event` callback** — Runs in the MQTT IO thread. The callback MUST be non-blocking, perform no I/O, acquire no locks, and be thread-safe for writes (e.g., `deque.append()`).

### Subscription Lifecycle

```
Main thread:  adapter.subscribe("AOT")   # Before or after MQTT connected
              adapter.subscribe("PTT")   # Add more symbols anytime
              ...
              adapter.unsubscribe("AOT") # Remove symbol
```

Subscriptions can be added at any time. The underlying `SettradeMQTTClient.subscribe()` handles the CONNECTED vs RECONNECTING cases (stores in source-of-truth dict, replays on reconnect).

---

## Data Models

### BestBidAsk (Top-of-Book Event)

```python
class BestBidAsk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str          # Stock symbol (e.g., "AOT")
    bid: float           # Best bid price (bid_price1)
    ask: float           # Best ask price (ask_price1)
    bid_vol: int         # Best bid volume (bid_volume1)
    ask_vol: int         # Best ask volume (ask_volume1)
    bid_flag: int        # 0=UNDEFINED, 1=NORMAL, 2=ATO, 3=ATC
    ask_flag: int        # 0=UNDEFINED, 1=NORMAL, 2=ATO, 3=ATC
    recv_ts: int         # time.time_ns() wall clock at MQTT receive
    recv_mono_ns: int    # time.perf_counter_ns() monotonic at MQTT receive
```

### FullBidOffer (Full Depth Event)

```python
class FullBidOffer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    bid_prices: tuple[float, ...]    # 10 bid prices (index 0 = best)
    ask_prices: tuple[float, ...]    # 10 ask prices (index 0 = best)
    bid_volumes: tuple[int, ...]     # 10 bid volumes
    ask_volumes: tuple[int, ...]     # 10 ask volumes
    bid_flag: int
    ask_flag: int
    recv_ts: int                     # time.time_ns() wall clock
    recv_mono_ns: int                # time.perf_counter_ns() monotonic
```

### Money Conversion (Hot Path)

```python
def money_to_float(money: MoneyLike) -> float:
    """Convert betterproto Money to float. No Decimal allocation."""
    return money.units + money.nanos * 1e-9
```

---

## Implementation Steps

### Step 1: Create Core Package

- Create `core/__init__.py` with public exports
- Create `core/events.py` with event models

### Step 2: Event Models

**File:** `core/events.py`

- `BidAskFlag(IntEnum)` — UNDEFINED=0, NORMAL=1, ATO=2, ATC=3
- `BestBidAsk(BaseModel)` — Top-of-book with frozen config, dual timestamps
- `FullBidOffer(BaseModel)` — Full 10-level depth with frozen config, dual timestamps

### Step 3: BidOfferAdapter Configuration

**File:** `infra/settrade_adapter.py`

```python
class BidOfferAdapterConfig(BaseModel):
    full_depth: bool = Field(
        default=False,
        description=(
            "Produce FullBidOffer (10 levels) instead of BestBidAsk. "
            "WARNING: FullDepth mode allocates ~46 objects per message "
            "and is not intended for sub-100us strategies."
        ),
    )
```

### Step 4: BidOfferAdapter Implementation

**File:** `infra/settrade_adapter.py`

Key methods:
- `__init__(config, mqtt_client, on_event)` — Store references, init counters
- `subscribe(symbol)` — Build topic, register with MQTT client (main thread only)
- `unsubscribe(symbol)` — Remove topic from MQTT client (main thread only)
- `_on_message(topic, payload)` — HOT PATH: parse → normalize → forward
- `stats()` — Return counter/state snapshot (thread-safe with lock)

### Step 5: Hot Path Implementation (Separated Error Isolation)

```python
def _on_message(self, topic: str, payload: bytes) -> None:
    """HOT PATH — runs inline in MQTT IO thread."""
    recv_ts: int = time.time_ns()
    recv_mono_ns: int = time.perf_counter_ns()

    # Phase 1: Parse protobuf and create event (isolated)
    try:
        msg: BidOfferV3 = BidOfferV3().parse(payload)
        if self._config.full_depth:
            event: FullBidOffer = self._parse_full_bid_offer(msg, recv_ts, recv_mono_ns)
        else:
            event: BestBidAsk = self._parse_best_bid_ask(msg, recv_ts, recv_mono_ns)
    except Exception:
        self._parse_errors += 1  # GIL-atomic
        logger.exception("Failed to parse BidOfferV3 on %s", topic)
        return

    # Phase 2: Forward event to callback (isolated)
    try:
        self._on_event(event)
    except Exception:
        self._callback_errors += 1  # GIL-atomic
        logger.exception("Event callback error for %s", topic)
        return

    self._messages_parsed += 1  # GIL-atomic, only on full success
```

### Step 6: BestBidAsk Parse (model_construct, direct field access)

```python
def _parse_best_bid_ask(
    self,
    msg: BidOfferV3,
    recv_ts: int,
    recv_mono_ns: int,
) -> BestBidAsk:
    return BestBidAsk.model_construct(
        symbol=msg.symbol,
        bid=msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
        ask=msg.ask_price1.units + msg.ask_price1.nanos * 1e-9,
        bid_vol=msg.bid_volume1,
        ask_vol=msg.ask_volume1,
        bid_flag=int(msg.bid_flag),
        ask_flag=int(msg.ask_flag),
        recv_ts=recv_ts,
        recv_mono_ns=recv_mono_ns,
    )
```

### Step 7: FullBidOffer Parse (model_construct, explicit unroll — no getattr)

```python
def _parse_full_bid_offer(
    self,
    msg: BidOfferV3,
    recv_ts: int,
    recv_mono_ns: int,
) -> FullBidOffer:
    return FullBidOffer.model_construct(
        symbol=msg.symbol,
        bid_prices=(
            msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
            msg.bid_price2.units + msg.bid_price2.nanos * 1e-9,
            msg.bid_price3.units + msg.bid_price3.nanos * 1e-9,
            msg.bid_price4.units + msg.bid_price4.nanos * 1e-9,
            msg.bid_price5.units + msg.bid_price5.nanos * 1e-9,
            msg.bid_price6.units + msg.bid_price6.nanos * 1e-9,
            msg.bid_price7.units + msg.bid_price7.nanos * 1e-9,
            msg.bid_price8.units + msg.bid_price8.nanos * 1e-9,
            msg.bid_price9.units + msg.bid_price9.nanos * 1e-9,
            msg.bid_price10.units + msg.bid_price10.nanos * 1e-9,
        ),
        ask_prices=(
            msg.ask_price1.units + msg.ask_price1.nanos * 1e-9,
            msg.ask_price2.units + msg.ask_price2.nanos * 1e-9,
            msg.ask_price3.units + msg.ask_price3.nanos * 1e-9,
            msg.ask_price4.units + msg.ask_price4.nanos * 1e-9,
            msg.ask_price5.units + msg.ask_price5.nanos * 1e-9,
            msg.ask_price6.units + msg.ask_price6.nanos * 1e-9,
            msg.ask_price7.units + msg.ask_price7.nanos * 1e-9,
            msg.ask_price8.units + msg.ask_price8.nanos * 1e-9,
            msg.ask_price9.units + msg.ask_price9.nanos * 1e-9,
            msg.ask_price10.units + msg.ask_price10.nanos * 1e-9,
        ),
        bid_volumes=(
            msg.bid_volume1,
            msg.bid_volume2,
            msg.bid_volume3,
            msg.bid_volume4,
            msg.bid_volume5,
            msg.bid_volume6,
            msg.bid_volume7,
            msg.bid_volume8,
            msg.bid_volume9,
            msg.bid_volume10,
        ),
        ask_volumes=(
            msg.ask_volume1,
            msg.ask_volume2,
            msg.ask_volume3,
            msg.ask_volume4,
            msg.ask_volume5,
            msg.ask_volume6,
            msg.ask_volume7,
            msg.ask_volume8,
            msg.ask_volume9,
            msg.ask_volume10,
        ),
        bid_flag=int(msg.bid_flag),
        ask_flag=int(msg.ask_flag),
        recv_ts=recv_ts,
        recv_mono_ns=recv_mono_ns,
    )
```

### Step 8: stats() with Lock for Consistent Reads

```python
def stats(self) -> dict[str, object]:
    """Return adapter statistics. Thread-safe.

    Acquires _counter_lock to snapshot all counters atomically.
    Can be called from any thread.
    """
    with self._counter_lock:
        return {
            "subscribed_symbols": sorted(self._subscribed_symbols),
            "messages_parsed": self._messages_parsed,
            "parse_errors": self._parse_errors,
            "callback_errors": self._callback_errors,
            "full_depth": self._config.full_depth,
        }
```

### Step 9: Update Package Exports

**File:** `infra/__init__.py`

Add `BidOfferAdapter`, `BidOfferAdapterConfig` to exports.

### Step 10: Unit Tests — Event Models

**File:** `tests/test_events.py`

- BestBidAsk creation and field access
- BestBidAsk immutability (frozen model)
- BestBidAsk rejects extra fields
- BestBidAsk dual timestamps
- FullBidOffer creation with tuples
- FullBidOffer immutability
- BidAskFlag enum values and int interchangeability

### Step 11: Unit Tests — Adapter

**File:** `tests/test_settrade_adapter.py`

- money_to_float with positive, zero, and fractional values
- BidOfferAdapter initialization
- Subscribe creates MQTT subscription with correct topic
- Subscribe multiple symbols
- Unsubscribe removes MQTT subscription
- on_message produces BestBidAsk (default mode) via model_construct
- on_message produces FullBidOffer (full_depth mode) via model_construct
- Parse error increments `parse_errors` (not `callback_errors`)
- Callback error increments `callback_errors` (not `parse_errors`)
- Stats method returns expected keys with lock-protected snapshot
- End-to-end: mock MQTT message → event callback

---

## Callback Contract

### Event Callback Rules

The `on_event` callback passed to `BidOfferAdapter` **MUST** follow these rules:

1. **Non-blocking** — Must return quickly (<1ms). This callback runs inside the MQTT IO thread via the adapter's `_on_message`.
2. **No I/O** — No network, disk, or database operations.
3. **No locks** — No mutex acquisition (risk of deadlock with MQTT IO thread).
4. **Thread-safe** — Called from the MQTT IO thread, not the main thread.

### Typical Callback

In Phase 3, the callback will be `dispatcher.push()` which does `deque.append()` (~10us).

```python
# Phase 3 usage:
adapter = BidOfferAdapter(
    config=BidOfferAdapterConfig(),
    mqtt_client=client,
    on_event=dispatcher.push,
)

# Or for simple testing:
events: list[BestBidAsk] = []
adapter = BidOfferAdapter(
    config=BidOfferAdapterConfig(),
    mqtt_client=client,
    on_event=events.append,
)
```

---

## Metrics & Observability

### Counters

| Metric | Type | Description |
|--------|------|-------------|
| `messages_parsed` | Counter | Protobuf messages successfully parsed AND forwarded |
| `parse_errors` | Counter | Protobuf parse/normalization failures (data issue) |
| `callback_errors` | Counter | Downstream event callback failures (logic issue) |

### Error Counter Semantics

- A message increments **exactly one** of: `messages_parsed`, `parse_errors`, or `callback_errors`
- `parse_errors` — the protobuf payload was malformed or field extraction failed
- `callback_errors` — the event was created correctly but `on_event()` raised
- `messages_parsed` — full success: parsed + forwarded without error

### Logging Rate Limit (Future)

Repeated callback/parse errors may produce heavy log output. Current implementation logs every error. In Phase 5 (observability), rate-limited logging will be added:
```python
# Future: log first N errors, then every Nth
if self._callback_errors < 10 or self._callback_errors % 1000 == 0:
    logger.exception(...)
```

### Access Pattern

```python
stats = adapter.stats()
# -> {
#     "subscribed_symbols": ["AOT", "PTT"],
#     "messages_parsed": 50231,
#     "parse_errors": 0,
#     "callback_errors": 0,
#     "full_depth": False,
# }
```

---

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `core/__init__.py` | CREATE | Core package init with public exports |
| `core/events.py` | CREATE | Event models: BestBidAsk, FullBidOffer, BidAskFlag |
| `infra/settrade_adapter.py` | CREATE | BidOfferAdapter with protobuf parsing |
| `infra/__init__.py` | MODIFY | Add adapter and config exports |
| `tests/test_events.py` | CREATE | Unit tests for event models |
| `tests/test_settrade_adapter.py` | CREATE | Unit tests for adapter |
| `docs/plan/low-latency-mqtt-feed-adapter/phase2-bidoffer-adapter.md` | CREATE | This plan document |
| `docs/plan/low-latency-mqtt-feed-adapter/PLAN.md` | MODIFY | Phase 2 completion notes |

---

## Success Criteria

### Adapter

- [x] BidOfferAdapter subscribes to correct MQTT topic pattern (`proto/topic/bidofferv3/{symbol}`)
- [x] Protobuf parsing uses `BidOfferV3().parse(payload)` — no `.to_dict()`
- [x] Money conversion uses `units + nanos * 1e-9` — no Decimal
- [x] BestBidAsk event produced correctly in default mode via `model_construct()`
- [x] FullBidOffer event produced correctly in full_depth mode via `model_construct()`
- [x] Parse errors counted in `parse_errors`, not `callback_errors`
- [x] Callback errors counted in `callback_errors`, not `parse_errors`
- [x] Event callback invoked with normalized event on every successful parse
- [x] Subscribe/unsubscribe manage MQTT topics correctly

### Data Models

- [x] BestBidAsk is a frozen Pydantic model with all fields typed
- [x] FullBidOffer is a frozen Pydantic model with tuple fields
- [x] Both models include dual timestamps (`recv_ts` + `recv_mono_ns`)
- [x] BidAskFlag IntEnum matches protobuf enum values (0-3)
- [x] Field descriptions and constraints present on all fields
- [x] `model_construct()` used exclusively in hot path

### Performance

- [x] No locks in hot path (counter increments are GIL-atomic)
- [x] No Decimal allocation in money conversion
- [x] No `.to_dict()` or intermediate dict creation
- [x] No validation overhead in hot path (model_construct)
- [x] No `getattr()` / f-string in FullDepth hot path (explicit unroll)
- [x] FullDepth allocation caveat documented
- [x] Protobuf instance reuse noted as future optimization

### Code Quality

- [x] Complete type annotations on all functions and variables
- [x] Comprehensive docstrings with Args, Returns, Raises, Examples
- [x] Import organization follows project standards
- [x] No bare `except:` clauses
- [x] Structured logging (no print statements)
- [x] Thread ownership documented
- [x] Float precision contract documented
- [x] Logging rate-limit strategy documented for future

### Testing

- [x] Unit tests for all event models (creation, immutability, validation)
- [x] Unit tests for money_to_float (positive, zero, fractional)
- [x] Unit tests for adapter (subscribe, unsubscribe, parse, stats)
- [x] Unit tests for both event modes (BestBidAsk, FullBidOffer)
- [x] Unit tests for separated error paths (parse_error vs callback_error)
- [x] Unit tests for stats() returning consistent snapshot
- [x] All tests pass

---

**Document Version:** 1.0
**Author:** AI Agent
**Status:** Complete

### Testing Summary

- **126 total tests** (73 Phase 2 + 53 Phase 1), all passing
- **test_events.py** — 32 tests covering: enum values, model creation, frozen immutability, deep immutability, extra rejection, validation constraints, model_construct bypass proof, negative prices, flag boundaries, hashability, equality, type coercion
- **test_settrade_adapter.py** — 41 tests covering: config defaults, money_to_float edge cases, subscription management (including duplicates/idempotency), BestBidAsk parsing with dual timestamps, FullBidOffer 10-level parsing, negative price propagation, separated error isolation, mixed error/success sequences, rate-limited logging behavior, stats consistency, end-to-end callback flow
