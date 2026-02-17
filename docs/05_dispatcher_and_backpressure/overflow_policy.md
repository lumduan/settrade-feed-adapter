# Overflow Policy

The Dispatcher uses a **drop-oldest** overflow policy. When the queue is at
capacity, the oldest (front) event is automatically evicted to make room for the
newest event. This is NOT a fail-fast / reject-newest design.

## How Drop-Oldest Works

`collections.deque(maxlen=N)` enforces its bounded size automatically. When
`append()` is called on a full deque, the element at the left (oldest) end is
discarded and the new element is added at the right (newest) end.

The Dispatcher's `push()` method wraps this with precise bookkeeping:

```python
def push(self, event: T) -> None:
    if len(self._queue) == self._maxlen:
        self._total_dropped += 1
        dropped = 1.0
    else:
        dropped = 0.0

    self._queue.append(event)   # deque(maxlen) auto-evicts oldest if full
    self._total_pushed += 1

    # EMA update follows (see health_and_ema.md)
    self._drop_rate_ema = (
        self._ema_alpha * dropped
        + (1.0 - self._ema_alpha) * self._drop_rate_ema
    )
```

Key points:

- The length check happens **before** the append, so `_total_dropped` is
  incremented exactly once per eviction.
- `_total_pushed` is incremented for **every** call to `push()`, whether or not
  an older event was dropped.
- The drop count is **exact** -- it matches the number of evicted events
  precisely.

## No Drop After poll() Frees Space

Once `poll()` removes events from the front of the queue, the length drops below
`maxlen`. Subsequent calls to `push()` will not trigger a drop until the queue
fills again. The drop-oldest policy only activates at the capacity boundary.

## Timeline Example

The following diagram shows a dispatcher with `maxlen=3`:

```text
Step   Operation        Queue (front..back)        Dropped?   Total Dropped
----   ---------        -------------------        --------   -------------
 1     push(A)          [A]                        no         0
 2     push(B)          [A, B]                     no         0
 3     push(C)          [A, B, C]  (full)          no         0
 4     push(D)          [B, C, D]  (A evicted)     yes        1
 5     push(E)          [C, D, E]  (B evicted)     yes        2
 6     poll(2)          [E]        (C, D returned)  --        2
 7     push(F)          [E, F]                     no         2
 8     push(G)          [E, F, G]  (full)          no         2
 9     push(H)          [F, G, H]  (E evicted)     yes        3
```

At step 4 the queue is full, so `A` (the oldest) is evicted. At step 6 the poll
removes two items, bringing the length down to 1. Steps 7 and 8 do not drop
because there is room. Step 9 drops again because the queue has refilled.

## maxlen=1 Edge Case

When `maxlen=1`, the deque can hold exactly one event. Every push after the
first evicts the single stored event:

```text
Step   Operation   Queue    Dropped?
----   ---------   -----    --------
 1     push(A)     [A]      no
 2     push(B)     [B]      yes  (A evicted)
 3     push(C)     [C]      yes  (B evicted)
 4     poll(1)     []        --
 5     push(D)     [D]      no
```

This is a valid configuration for scenarios where only the most recent event
matters (e.g. a latest-price snapshot).

## Why Drop-Oldest

Drop-oldest suits market-data feeds because stale prices lose value quickly. If
the consumer falls behind, it is better to skip outdated quotes and resume with
fresh data than to reject incoming updates or block the producer. The strategy
thread always sees the most recent events available.

Compared to a fail-fast reject-newest policy:

| Property | Drop-Oldest (this implementation) | Reject-Newest |
| --- | --- | --- |
| Newest data preserved | Yes -- always in queue | No -- rejected when full |
| Oldest data preserved | No -- evicted first | Yes -- stays in queue |
| Producer blocks | Never | Never |
| Best for | Real-time feeds, latest price | Audit logs, ordered replay |

## Counter Invariant

Regardless of how many drops occur, the fundamental invariant holds:

```text
total_pushed - total_dropped - total_polled == len(queue)
```

Tests verify this relationship after every combination of push, poll, drop, and
clear operations, including concurrent push/poll from separate threads.

## Test Coverage

Tests confirm:

- FIFO ordering for surviving events
- Drop at `maxlen` boundary (oldest evicted, not newest)
- Drop count matches evicted events exactly
- No drop after `poll()` frees space
- `maxlen=1`: every push after the first drops
- `clear()` resets drop counter to zero
- Invariant holds after all operations
- Concurrent push/poll is safe
