"""Bounded event dispatcher for decoupling MQTT producer from strategy consumer.

This module provides the ``Dispatcher`` — a thread-safe, bounded event queue
backed by ``collections.deque(maxlen)``. It sits between the adapter layer
(Phase 2) and the strategy engine, enabling the MQTT IO thread to push
normalized events without blocking, while the strategy thread polls events
in batches.

Architecture note:
    The dispatcher is the **only synchronisation point** between the MQTT IO
    thread and the strategy thread. Both ``push()`` and ``poll()`` are
    lock-free, relying on CPython's GIL for atomic ``deque.append()`` and
    ``deque.popleft()`` operations.

SPSC contract:
    This dispatcher is **strictly single-producer, single-consumer (SPSC)**.
    Any change to the threading model (multi-producer, multi-consumer)
    invalidates all safety guarantees. If you need MPMC, replace with
    ``threading.Queue`` or add explicit locking.

Thread ownership:
    - ``push(event)`` — MQTT IO thread only (single producer).
    - ``poll(max_events)`` — Strategy/main thread only (single consumer).
    - ``clear()`` — Main thread only (not concurrent with push/poll).
    - ``stats()`` — Any thread (eventually consistent, lock-free reads).

Counter contract (single-writer, multi-reader):
    - ``_total_pushed`` / ``_total_dropped`` — written by push thread only.
    - ``_total_polled`` — written by poll thread only.
    - No two threads ever write to the same counter.
    - Reads from other threads see eventually-consistent values.
    - CPython ``int`` reads are atomic (single bytecode instruction).

Stats consistency:
    Stats are eventually consistent — not transactional. All counter reads
    and ``queue_len`` may reflect slightly different points in time. Under
    quiescent conditions (no concurrent push/poll), the invariant
    ``total_pushed - total_dropped - total_polled == queue_len`` holds
    exactly.

Backpressure policy:
    Drop-oldest. When the queue is full, ``deque.append()`` automatically
    evicts the oldest item. Stale market data is worthless — new data
    always wins. Drops are counted via pre-append length check.

CPython assumption:
    This design relies on CPython's GIL guaranteeing atomic
    ``deque.append()`` and ``deque.popleft()``. This is **not guaranteed**
    on PyPy, GraalPy, or nogil Python. If migrating away from CPython,
    replace ``deque`` with ``threading.Queue`` or a lock-protected buffer.

Example:
    >>> from core.dispatcher import Dispatcher, DispatcherConfig
    >>> dispatcher = Dispatcher(config=DispatcherConfig(maxlen=1000))
    >>> dispatcher.push("event_1")
    >>> dispatcher.push("event_2")
    >>> events = dispatcher.poll(max_events=10)
    >>> len(events)
    2
    >>> events[0]
    'event_1'
    >>> dispatcher.stats().total_pushed
    2
"""

import collections
import logging
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

logger: logging.Logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic event type
# ---------------------------------------------------------------------------

T = TypeVar("T")
"""Type variable for events stored in the dispatcher.

Allows type-safe usage:
    ``Dispatcher[BestBidAsk]`` ensures push/poll types are consistent.
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class DispatcherConfig(BaseModel):
    """Configuration for :class:`Dispatcher`.

    Attributes:
        maxlen: Maximum number of events the queue can hold. When the
            queue is full, ``push()`` causes the oldest event to be
            evicted (drop-oldest policy). Must be greater than zero.
        ema_alpha: Smoothing factor for exponential moving average of
            drop rate. Smaller values → smoother signal with slower
            response. Default 0.01 (~100-message half-life).
        drop_warning_threshold: Drop rate EMA threshold that triggers
            a warning log in ``push()``. Default 0.01 (1%).

    Example:
        >>> config = DispatcherConfig(maxlen=50_000)
        >>> config.maxlen
        50000
        >>> DispatcherConfig()  # default
        DispatcherConfig(maxlen=100000)
    """

    maxlen: int = Field(
        default=100_000,
        gt=0,
        description=(
            "Maximum queue length. Oldest events are dropped when exceeded. "
            "Default 100,000 ~ 10 seconds at 10K msg/s."
        ),
    )
    ema_alpha: float = Field(
        default=0.01,
        gt=0.0,
        le=1.0,
        description=(
            "EMA smoothing factor for drop rate. "
            "Default 0.01 (~100-message half-life)."
        ),
    )
    drop_warning_threshold: float = Field(
        default=0.01,
        gt=0.0,
        le=1.0,
        description=(
            "Drop rate EMA threshold for warning log. "
            "Default 0.01 (1% drop rate)."
        ),
    )


# ---------------------------------------------------------------------------
# Stats Model
# ---------------------------------------------------------------------------


class DispatcherStats(BaseModel):
    """Immutable snapshot of dispatcher statistics.

    Returned by :meth:`Dispatcher.stats`. All fields are read-only
    (frozen model).

    Consistency:
        All values are eventually consistent. Under concurrent access,
        counters and ``queue_len`` may reflect slightly different points
        in time. Under quiescent conditions (no concurrent push/poll),
        the invariant ``total_pushed - total_dropped - total_polled ==
        queue_len`` holds exactly.

    Attributes:
        total_pushed: Total events pushed into the queue, including
            those that caused an oldest event to be dropped.
        total_polled: Total events consumed via ``poll()``.
        total_dropped: Total events dropped due to queue overflow.
            Each drop means the oldest event was evicted by a new push.
        queue_len: Current number of events in the queue. Eventually
            consistent with the counter values.
        maxlen: Configured maximum queue length.

    Example:
        >>> stats = dispatcher.stats()
        >>> stats.total_pushed
        150432
        >>> stats.total_dropped
        0
        >>> stats.queue_len
        2
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_pushed: int = Field(
        ge=0,
        description="Total events pushed (including those that caused drops).",
    )
    total_polled: int = Field(
        ge=0,
        description="Total events consumed via poll().",
    )
    total_dropped: int = Field(
        ge=0,
        description="Events dropped due to queue overflow (oldest evicted).",
    )
    queue_len: int = Field(
        ge=0,
        description="Current events in queue (eventually consistent).",
    )
    maxlen: int = Field(
        gt=0,
        description="Configured maximum queue length.",
    )


# ---------------------------------------------------------------------------
# Health Model
# ---------------------------------------------------------------------------


class DispatcherHealth(BaseModel):
    """Immutable snapshot of dispatcher health metrics.

    Returned by :meth:`Dispatcher.health`. Provides real-time drop
    rate (EMA-smoothed) and lifetime counters for forensic analysis.

    Attributes:
        drop_rate_ema: Smoothed drop rate. 0.0 means no drops,
            1.0 means every push drops. Updated on each ``push()``.
        queue_utilization: Current queue fill ratio. 0.0 = empty,
            1.0 = full.
        total_dropped: Cumulative drops since last ``clear()``.
        total_pushed: Cumulative pushes since last ``clear()``.

    Example:
        >>> h = dispatcher.health()
        >>> h.drop_rate_ema
        0.0
        >>> h.queue_utilization
        0.02
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    drop_rate_ema: float = Field(
        ge=0.0,
        description="Smoothed drop rate (EMA). 0.0 = no drops.",
    )
    queue_utilization: float = Field(
        ge=0.0,
        le=1.0,
        description="Queue fill ratio: len(queue) / maxlen.",
    )
    total_dropped: int = Field(
        ge=0,
        description="Cumulative drops since last clear().",
    )
    total_pushed: int = Field(
        ge=0,
        description="Cumulative pushes since last clear().",
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher(Generic[T]):
    """Bounded event queue decoupling MQTT producer from strategy consumer.

    Backed by ``collections.deque(maxlen)`` for automatic drop-oldest
    backpressure. The MQTT IO thread pushes events via ``push()``, and
    the strategy thread consumes them via ``poll()``.

    **Strictly SPSC** (single-producer, single-consumer). Any change to
    the threading model invalidates safety guarantees.

    Thread safety:
        - ``push()`` — lock-free, single-writer counters. MQTT thread only.
        - ``poll()`` — lock-free, single-writer counter. Strategy thread only.
        - ``clear()`` — main thread only, not concurrent with push/poll.
        - ``stats()`` — lock-free, eventually consistent. Any thread.

    Drop detection:
        Checks ``len(queue) == maxlen`` before ``append()``. The
        ``deque(maxlen)`` contract guarantees that when the queue is
        full, ``append()`` evicts the oldest item before inserting
        the new one. The pre-check is safe under SPSC because only
        the push thread calls ``append()``.

    Performance:
        - ``push()`` — ~200-400ns (len + append + two counter increments)
        - ``poll()`` — ~100-300ns per event (truthiness check + popleft)
        - No locks, no exceptions in hot paths

    Args:
        config: Dispatcher configuration. Defaults to
            ``DispatcherConfig()`` with ``maxlen=100_000``.

    Example:
        >>> from core.dispatcher import Dispatcher, DispatcherConfig
        >>>
        >>> # Create typed dispatcher
        >>> dispatcher: Dispatcher[BestBidAsk] = Dispatcher(
        ...     config=DispatcherConfig(maxlen=50_000),
        ... )
        >>>
        >>> # Push events (from MQTT IO thread)
        >>> dispatcher.push(event)
        >>>
        >>> # Poll events (from strategy thread)
        >>> for event in dispatcher.poll(max_events=100):
        ...     process(event)
        >>>
        >>> # Check health
        >>> stats = dispatcher.stats()
        >>> if stats.total_dropped > 0:
        ...     logger.warning("Queue overflow: %d drops", stats.total_dropped)
    """

    def __init__(self, config: DispatcherConfig | None = None) -> None:
        self._config: DispatcherConfig = config or DispatcherConfig()
        self._maxlen: int = self._config.maxlen
        self._queue: collections.deque[T] = collections.deque(
            maxlen=self._maxlen,
        )

        # Counters — single-writer, multi-reader (no lock needed)
        # _total_pushed, _total_dropped: written by push thread only
        # _total_polled: written by poll thread only
        self._total_pushed: int = 0
        self._total_polled: int = 0
        self._total_dropped: int = 0

        # EMA drop-rate tracking — push thread only
        self._ema_alpha: float = self._config.ema_alpha
        self._drop_warning_threshold: float = (
            self._config.drop_warning_threshold
        )
        self._drop_rate_ema: float = 0.0
        self._warned_drop_rate: bool = False

        logger.info(
            "Dispatcher created with maxlen=%d",
            self._maxlen,
        )

    # ------------------------------------------------------------------
    # Hot Path: Push (MQTT IO thread)
    # ------------------------------------------------------------------

    def push(self, event: T) -> None:
        """Append an event to the queue.

        **HOT PATH** — called from the MQTT IO thread inline in the
        ``on_message`` callback. Must be non-blocking with no locks.

        Drop detection:
            Checks ``len(queue) == maxlen`` before ``append()``.
            When the deque is full, ``append()`` evicts the oldest
            item atomically (deque(maxlen) contract). The pre-check
            is safe under SPSC because only this thread appends.

        EMA tracking:
            Updates the drop-rate EMA after each push:
            ``ema = alpha * sample + (1 - alpha) * ema``
            where ``sample`` is 1.0 on drop, 0.0 otherwise.
            Logs a warning when EMA crosses the configured threshold.

        Counter contract:
            ``_total_pushed``, ``_total_dropped``, and
            ``_drop_rate_ema`` are single-writer (push thread only).
            No lock required.

        Args:
            event: The event to push. Typed as ``T`` for generic
                dispatcher usage (e.g., ``Dispatcher[BestBidAsk]``).

        Example:
            >>> dispatcher.push(best_bid_ask_event)
            >>> dispatcher.stats().total_pushed
            1
        """
        dropped: float = 0.0
        if len(self._queue) == self._maxlen:
            self._total_dropped += 1
            dropped = 1.0
        self._queue.append(event)
        self._total_pushed += 1

        # EMA: ema = alpha * sample + (1 - alpha) * ema
        alpha: float = self._ema_alpha
        self._drop_rate_ema = alpha * dropped + (1.0 - alpha) * self._drop_rate_ema

        if self._drop_rate_ema > self._drop_warning_threshold:
            if not self._warned_drop_rate:
                logger.warning(
                    "Drop rate EMA %.4f exceeds threshold %.4f",
                    self._drop_rate_ema,
                    self._drop_warning_threshold,
                )
                self._warned_drop_rate = True
        elif self._warned_drop_rate:
            logger.info(
                "Drop rate EMA %.4f recovered below threshold %.4f",
                self._drop_rate_ema,
                self._drop_warning_threshold,
            )
            self._warned_drop_rate = False

    # ------------------------------------------------------------------
    # Consumer Path: Poll (Strategy thread)
    # ------------------------------------------------------------------

    def poll(self, max_events: int = 100) -> list[T]:
        """Consume up to ``max_events`` from the queue.

        Called from the strategy/main thread. Returns immediately with
        whatever events are available (non-blocking). If the queue is
        empty, returns an empty list.

        Uses a bounded ``for`` loop with truthiness break (no exception
        for control flow) to minimise function call overhead in the hot
        consumer path.

        Counter contract:
            ``_total_polled`` is single-writer (poll thread only).
            No lock required.

        Args:
            max_events: Maximum number of events to consume in one
                batch. Must be greater than zero. Defaults to 100.

        Returns:
            List of events in FIFO order. May contain fewer than
            ``max_events`` if the queue had fewer items. Empty list
            if the queue was empty.

        Raises:
            ValueError: If ``max_events`` is not greater than zero.

        Example:
            >>> events = dispatcher.poll(max_events=50)
            >>> for event in events:
            ...     print(event.symbol, event.bid, event.ask)
        """
        if max_events <= 0:
            raise ValueError(f"max_events must be > 0, got {max_events}")

        events: list[T] = []
        for _ in range(max_events):
            if not self._queue:
                break
            events.append(self._queue.popleft())
        self._total_polled += len(events)
        return events

    # ------------------------------------------------------------------
    # Queue Management
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear the queue and reset all counters.

        Use during MQTT reconnection, trading halts, symbol
        resubscription, or error recovery to discard stale events
        and start fresh.

        Must be called from the main thread only. **NOT** safe to
        call concurrently with ``push()`` or ``poll()``. Ensure the
        MQTT client is disconnected or the adapter is paused before
        calling.

        Example:
            >>> dispatcher.clear()
            >>> dispatcher.stats().queue_len
            0
            >>> dispatcher.stats().total_pushed
            0
        """
        remaining: int = len(self._queue)
        if remaining > 0:
            logger.warning(
                "Dispatcher clearing %d remaining events",
                remaining,
            )
        self._queue.clear()
        self._total_pushed = 0
        self._total_polled = 0
        self._total_dropped = 0
        self._drop_rate_ema = 0.0
        self._warned_drop_rate = False
        logger.info("Dispatcher cleared: queue and counters reset")

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def stats(self) -> DispatcherStats:
        """Return a snapshot of dispatcher statistics.

        Lock-free, eventually consistent. All counter reads and
        ``queue_len`` are individually atomic (CPython int read),
        but may reflect slightly different points in time under
        concurrent access. Under quiescent conditions, the invariant
        ``total_pushed - total_dropped - total_polled == queue_len``
        holds exactly.

        Can be called from any thread.

        Returns:
            Frozen :class:`DispatcherStats` with current counter values.

        Example:
            >>> stats = dispatcher.stats()
            >>> stats.total_pushed
            150432
            >>> stats.total_dropped
            0
        """
        return DispatcherStats(
            total_pushed=self._total_pushed,
            total_polled=self._total_polled,
            total_dropped=self._total_dropped,
            queue_len=len(self._queue),
            maxlen=self._maxlen,
        )

    # ------------------------------------------------------------------
    # Health (strategy thread)
    # ------------------------------------------------------------------

    def health(self) -> DispatcherHealth:
        """Return health metrics for feed integrity monitoring.

        Provides real-time drop rate (EMA-smoothed) and lifetime
        counters for forensic analysis. Intended for strategy-side
        guard rails.

        Lock-free, eventually consistent (same guarantees as
        :meth:`stats`).

        Returns:
            Frozen :class:`DispatcherHealth` with current health values.

        Example:
            >>> h = dispatcher.health()
            >>> if h.drop_rate_ema > 0.01:
            ...     logger.warning("High drop rate: %.4f", h.drop_rate_ema)
        """
        return DispatcherHealth(
            drop_rate_ema=self._drop_rate_ema,
            queue_utilization=len(self._queue) / self._maxlen,
            total_dropped=self._total_dropped,
            total_pushed=self._total_pushed,
        )

    # ------------------------------------------------------------------
    # Internal: Invariant Check (for testing)
    # ------------------------------------------------------------------

    def _invariant_ok(self) -> bool:
        """Check the internal consistency invariant.

        Under quiescent conditions (no concurrent ``push()`` or
        ``poll()``), this must return ``True``. Under concurrent
        access, the invariant holds approximately due to eventual
        consistency.

        Invariant:
            ``total_pushed - total_dropped - total_polled == queue_len``

        Returns:
            ``True`` if the invariant holds, ``False`` otherwise.

        Example:
            >>> dispatcher.push("a")
            >>> dispatcher.push("b")
            >>> dispatcher._invariant_ok()
            True
            >>> dispatcher.poll(max_events=1)
            ['a']
            >>> dispatcher._invariant_ok()
            True
        """
        return (
            self._total_pushed - self._total_dropped - self._total_polled
            == len(self._queue)
        )
