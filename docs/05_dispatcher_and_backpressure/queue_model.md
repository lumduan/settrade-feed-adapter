# Queue Model

The Dispatcher is a bounded, single-producer / single-consumer (SPSC) FIFO queue
built on `collections.deque(maxlen)`. It decouples the MQTT IO thread (producer)
from the strategy thread (consumer) so that message ingestion never blocks on
downstream processing.

## Bounded Deque

```python
from core.dispatcher import Dispatcher, DispatcherConfig

cfg = DispatcherConfig(maxlen=50_000)
dispatcher: Dispatcher[BestBidAsk] = Dispatcher(config=cfg)
```

The constructor creates a `collections.deque(maxlen=maxlen)` and initialises
three counters to zero: `_total_pushed`, `_total_polled`, and `_total_dropped`.
It also initialises the drop-rate EMA to `0.0`.

The default `maxlen` is **100 000** entries (`DispatcherConfig.maxlen` with
`gt=0`).

## FIFO Ordering

Events are appended to the right end of the deque via `push()` and consumed from
the left end via `poll()`. This guarantees strict first-in, first-out delivery
for all events that survive the overflow policy.

## Core API

| Method | Thread | Description |
| --- | --- | --- |
| `push(event)` | MQTT IO thread | Append one event. If the queue is at capacity the oldest event is auto-evicted (see overflow policy). |
| `poll(max_events=100)` | Strategy thread | Pop up to `max_events` items from the front. Returns `list[T]`. Raises `ValueError` if `max_events <= 0`. |
| `clear()` | Main thread | Clear the deque and reset **all** counters and the EMA to zero. |
| `stats()` | Any thread | Return a frozen `DispatcherStats` snapshot. |
| `health()` | Any thread | Return a frozen `DispatcherHealth` snapshot with drop-rate EMA and queue utilisation. |

## push(event) -- Hot Path

`push()` is designed to be called on the MQTT IO thread at high frequency. Its
operation is:

1. Check if `len(queue) == maxlen`. If so, increment `_total_dropped` and set
   `dropped = 1.0`.
2. Call `queue.append(event)`. The `deque(maxlen)` automatically evicts the
   oldest element when full.
3. Increment `_total_pushed`.
4. Update the drop-rate EMA (see `health_and_ema.md`).

## poll(max_events=100)

`poll()` pops up to `max_events` items from the front of the deque using
`popleft()`. It raises `ValueError` if `max_events <= 0`. If the queue is empty
it returns an empty list. After the loop, `_total_polled` is incremented by the
number of items actually returned.

```python
events: list[BestBidAsk] = dispatcher.poll(max_events=200)
for e in events:
    strategy.on_tick(e)
```

## clear()

`clear()` is a lifecycle method intended for use on the main thread (e.g. during
reconnect). It clears the deque and resets every piece of mutable state:

- `_total_pushed = 0`
- `_total_polled = 0`
- `_total_dropped = 0`
- `_drop_rate_ema = 0.0`

After `clear()`, the dispatcher behaves as if freshly constructed.

## DispatcherStats

`stats()` returns a frozen Pydantic model:

```python
class DispatcherStats(BaseModel, frozen=True, extra="forbid"):
    total_pushed: int    # ge=0
    total_polled: int    # ge=0
    total_dropped: int   # ge=0
    queue_len: int       # ge=0
    maxlen: int          # gt=0
```

The snapshot is lock-free and eventually consistent.

## SPSC Thread-Ownership Contract

The dispatcher relies on strict thread ownership rather than mutexes:

| Role | Thread | Writable state |
| --- | --- | --- |
| Producer | MQTT IO thread | `_total_pushed`, `_total_dropped`, `_drop_rate_ema` |
| Consumer | Strategy thread | `_total_polled` |
| Lifecycle | Main thread | `clear()` (not concurrent with push/poll) |
| Observer | Any thread | Reads only -- `stats()`, `health()` |

Each counter has exactly one writer. CPython int reads are atomic, so observer
threads always see a valid (though potentially slightly stale) value without
locks.

## Invariant

After every operation the following relationship holds:

```text
total_pushed - total_dropped - total_polled == len(queue)
```

The private method `_invariant_ok()` checks this condition and is exercised
extensively in tests.

## Memory

The queue stores **references** to event objects, not copies. Memory consumption
is O(N) where N is `maxlen`. With the default of 100 000 entries this is well
within normal memory budgets for typical market-data objects.

## Generic Typing

The dispatcher is generic over the event type:

```python
from core.dispatcher import Dispatcher

bid_ask_q: Dispatcher[BestBidAsk] = Dispatcher()
trade_q: Dispatcher[TradeEvent] = Dispatcher()
```

This lets type checkers verify that producers and consumers agree on the event
type flowing through the queue.

## DispatcherConfig

```python
class DispatcherConfig(BaseModel):
    maxlen: int = 100_000          # gt=0
    ema_alpha: float = 0.01        # gt=0.0, le=1.0
    drop_warning_threshold: float = 0.01  # gt=0.0, le=1.0
```

All fields are validated by Pydantic on construction.

## Test Coverage

Tests confirm:

- FIFO ordering is preserved for non-dropped events
- Drop at `maxlen` boundary (oldest evicted)
- Drop count matches evicted events exactly
- No drop after `poll()` frees space
- `maxlen=1` edge case: every push after the first drops
- `clear()` resets everything
- `poll(0)` and `poll(-1)` raise `ValueError`
- Concurrent push/poll from separate threads is safe
- Invariant holds after all operations
