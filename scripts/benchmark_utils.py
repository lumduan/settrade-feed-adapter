"""Shared benchmark utilities for latency measurement and comparison.

This module provides the core infrastructure for benchmarking the Settrade
Feed Adapter against the official SDK. It includes:

- Realistic synthetic payload generation with per-message variation
- Linear-interpolation percentile calculation (matches numpy default)
- GC stabilization and measurement utilities
- CPU normalization per core
- Multi-run aggregation with mean ± stddev confidence intervals
- Formatted ASCII comparison table output

Design principles:
    - Pure Python — no numpy or external benchmark dependencies
    - Pydantic models for all config and result data structures
    - Realistic payloads built via protobuf SerializeToString()
    - Per-message variation to defeat branch predictor / CPU cache effects
    - GC enabled by default for realistic measurement
    - CPU normalized by os.cpu_count() for meaningful per-core percentage

Percentile method:
    Linear interpolation between adjacent sorted ranks. Matches
    ``numpy.percentile(method='linear')`` — the industry standard.
    Algorithm: ``k = (n-1) * p; f = floor(k); c = ceil(k);
    result = sorted[f] + (sorted[c] - sorted[f]) * (k - f)``

Confidence intervals:
    Each benchmark runs ``num_runs`` iterations internally (default 3).
    For production CI gating, ``num_runs >= 5`` is recommended for
    stable stddev estimates with sample-size correction (n-1 divisor).

Example:
    >>> from scripts.benchmark_utils import (
    ...     build_synthetic_payloads,
    ...     calculate_latency_stats,
    ...     BenchmarkConfig,
    ... )
    >>> payloads = build_synthetic_payloads(symbol="AOT", count=100)
    >>> len(payloads)
    100
    >>> stats = calculate_latency_stats([1000, 2000, 3000, 4000, 5000])
    >>> stats.p50_us
    3.0
"""

import gc
import math
import os
import statistics
import sys
import time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from settrade_v2.pb.bidofferv3_pb2 import BidOfferV3, BidOfferV3BidAskFlag
from settrade_v2.pb.google.type import Money


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BenchmarkMode(str, Enum):
    """Benchmark execution mode.

    Attributes:
        SYNTHETIC: Use synthetic protobuf payloads (reproducible, no
            credentials needed). Isolates parse + normalize cost.
        LIVE: Use live market data from Settrade sandbox (future).
            Requires credentials and market hours.
    """

    SYNTHETIC = "SYNTHETIC"
    LIVE = "LIVE"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class BenchmarkConfig(BaseModel):
    """Configuration for benchmark execution.

    Attributes:
        num_messages: Total number of messages to process per run
            (including warmup). Must be greater than warmup_count.
        warmup_count: Number of initial messages to discard from
            latency statistics. CPython 3.11+ adaptive specialization
            requires ~1000 iterations for stable bytecode.
        num_runs: Number of internal benchmark iterations for
            confidence intervals. Mean ± stddev reported. For
            production CI gating, >= 5 is recommended for stable
            stddev estimates.
        symbol: Stock symbol for synthetic payload generation.
        mode: Benchmark execution mode (SYNTHETIC or LIVE).
        gc_disabled: If True, disable GC during measurement.
            Default False (realistic mode — GC enabled).
        tracemalloc_enabled: If True, enable tracemalloc for
            per-message allocation counting. Adds ~10% overhead.

    Example:
        >>> config = BenchmarkConfig(num_messages=10000)
        >>> config.warmup_count
        1000
    """

    num_messages: int = Field(
        default=10_000,
        gt=0,
        description="Total messages per run (including warmup)",
    )
    warmup_count: int = Field(
        default=1_000,
        ge=0,
        description=(
            "Messages to discard from stats (warmup). "
            "CPython 3.11+ needs ~1000 for adaptive specialization."
        ),
    )
    num_runs: int = Field(
        default=3,
        gt=0,
        description=(
            "Number of internal iterations for confidence intervals. "
            ">= 5 recommended for production CI gating."
        ),
    )
    symbol: str = Field(
        default="AOT",
        min_length=1,
        description="Stock symbol for synthetic payloads",
    )
    mode: BenchmarkMode = Field(
        default=BenchmarkMode.SYNTHETIC,
        description="Benchmark mode: SYNTHETIC (default) or LIVE (future)",
    )
    gc_disabled: bool = Field(
        default=False,
        description=(
            "Disable GC during measurement. Default False (realistic). "
            "Use True for allocation isolation debugging."
        ),
    )
    tracemalloc_enabled: bool = Field(
        default=False,
        description=(
            "Enable tracemalloc for allocation counting. "
            "Adds ~10%% overhead — do not use for latency measurement."
        ),
    )

    @model_validator(mode="after")
    def validate_warmup_less_than_messages(self) -> "BenchmarkConfig":
        """Ensure warmup_count < num_messages so measurements are non-empty."""
        if self.warmup_count >= self.num_messages:
            raise ValueError(
                f"warmup_count ({self.warmup_count}) must be less than "
                f"num_messages ({self.num_messages})"
            )
        return self


# ---------------------------------------------------------------------------
# Result Models
# ---------------------------------------------------------------------------


class LatencyStats(BaseModel):
    """Percentile latency statistics in microseconds.

    Calculated using linear interpolation between adjacent sorted
    ranks, matching ``numpy.percentile(method='linear')``.

    Attributes:
        p50_us: Median (50th percentile) latency in microseconds.
        p95_us: 95th percentile latency in microseconds.
        p99_us: 99th percentile latency in microseconds.
        min_us: Minimum observed latency in microseconds.
        max_us: Maximum observed latency in microseconds.
        mean_us: Arithmetic mean latency in microseconds.
        stddev_us: Standard deviation of latency in microseconds.

    Example:
        >>> stats = LatencyStats(
        ...     p50_us=42.0, p95_us=80.0, p99_us=120.0,
        ...     min_us=30.0, max_us=200.0, mean_us=50.0, stddev_us=15.0,
        ... )
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    p50_us: float = Field(description="P50 (median) latency in microseconds")
    p95_us: float = Field(description="P95 latency in microseconds")
    p99_us: float = Field(description="P99 latency in microseconds")
    min_us: float = Field(description="Minimum latency in microseconds")
    max_us: float = Field(description="Maximum latency in microseconds")
    mean_us: float = Field(description="Mean latency in microseconds")
    stddev_us: float = Field(description="Stddev of latency in microseconds")


class RunResult(BaseModel):
    """Result from a single benchmark run.

    Attributes:
        latency: Latency percentile statistics.
        gc_collections: Generation-0 GC collection count delta.
        alloc_blocks_delta: ``sys.getallocatedblocks()`` delta.
            Note: this includes interpreter-internal allocations and
            is approximate. Useful for relative comparison, not
            absolute allocation counting.
        cpu_percent: CPU usage normalized per core.
        throughput_msg_per_sec: Messages processed per second.
        tracemalloc_allocs_per_msg: Allocations per message (None if
            tracemalloc disabled).
        num_measured: Number of messages in latency statistics
            (after warmup exclusion).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    latency: LatencyStats = Field(description="Latency statistics")
    gc_collections: int = Field(
        ge=0,
        description="Gen-0 GC collections during measurement",
    )
    alloc_blocks_delta: int = Field(
        description=(
            "sys.getallocatedblocks() delta during measurement. "
            "Includes interpreter allocations (approximate)."
        ),
    )
    cpu_percent: float = Field(
        ge=0.0,
        description="CPU usage normalized per core (%%)",
    )
    throughput_msg_per_sec: float = Field(
        gt=0.0,
        description="Messages processed per second",
    )
    tracemalloc_net_blocks_per_msg: float | None = Field(
        default=None,
        description=(
            "Net memory block delta per message via tracemalloc "
            "(None if disabled). This is the net block growth, not "
            "total allocation calls — freed blocks are subtracted."
        ),
    )
    num_measured: int = Field(
        gt=0,
        description="Messages measured (after warmup exclusion)",
    )


class BenchmarkResult(BaseModel):
    """Aggregated benchmark result across multiple runs.

    Reports mean ± stddev for key metrics. If stddev exceeds 15%
    of the mean for P99 latency, the result is flagged as unstable.

    Attributes:
        label: Human-readable benchmark label (e.g., "SDK", "Adapter").
        config: Benchmark configuration used.
        runs: Individual run results.
        mean_p50_us: Mean P50 latency across runs.
        mean_p95_us: Mean P95 latency across runs.
        mean_p99_us: Mean P99 latency across runs.
        stddev_p99_us: Stddev of P99 latency across runs.
        mean_cpu_percent: Mean CPU usage across runs.
        mean_gc_collections: Mean GC collections across runs.
        mean_alloc_blocks_delta: Mean allocation blocks delta.
        mean_throughput: Mean throughput across runs.
        is_stable: True if mean_p99 > 0 and stddev P99 < 15% of mean P99.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(description="Benchmark label (e.g., 'SDK', 'Adapter')")
    config: BenchmarkConfig = Field(description="Configuration used")
    runs: list[RunResult] = Field(description="Per-run results")
    mean_p50_us: float = Field(description="Mean P50 latency (us)")
    mean_p95_us: float = Field(description="Mean P95 latency (us)")
    mean_p99_us: float = Field(description="Mean P99 latency (us)")
    stddev_p99_us: float = Field(description="Stddev P99 latency (us)")
    mean_cpu_percent: float = Field(description="Mean CPU per core (%%)")
    mean_gc_collections: float = Field(description="Mean gen-0 GC collections")
    mean_alloc_blocks_delta: float = Field(
        description=(
            "Mean sys.getallocatedblocks() delta. "
            "Includes interpreter allocations (approximate)."
        ),
    )
    mean_throughput: float = Field(description="Mean msg/s throughput")
    is_stable: bool = Field(
        description="True if mean_p99 > 0 and stddev P99 < 15%% of mean P99",
    )


# ---------------------------------------------------------------------------
# Percentile Calculation (Linear Interpolation)
# ---------------------------------------------------------------------------


def calculate_percentile(sorted_values: list[float], percentile: float) -> float:
    """Calculate percentile using linear interpolation.

    Matches ``numpy.percentile(method='linear')`` — the industry
    standard. Interpolates between the two nearest ranks when the
    desired percentile falls between data points.

    Algorithm:
        ``k = (n - 1) * percentile``
        ``f = floor(k), c = ceil(k)``
        ``result = sorted[f] + (sorted[c] - sorted[f]) * (k - f)``

    Args:
        sorted_values: Pre-sorted list of values (ascending).
            Must not be empty.
        percentile: Percentile to compute, in range [0.0, 1.0].
            E.g., 0.99 for P99.

    Returns:
        Interpolated percentile value.

    Raises:
        ValueError: If sorted_values is empty or percentile is
            out of [0.0, 1.0] range.

    Example:
        >>> calculate_percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5)
        3.0
        >>> calculate_percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.99)
        4.96
    """
    if not sorted_values:
        raise ValueError("sorted_values must not be empty")
    if not 0.0 <= percentile <= 1.0:
        raise ValueError(f"percentile must be in [0.0, 1.0], got {percentile}")

    n: int = len(sorted_values)
    if n == 1:
        return sorted_values[0]

    k: float = (n - 1) * percentile
    f: int = math.floor(k)
    c: int = math.ceil(k)

    if f == c:
        return sorted_values[f]

    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


# ---------------------------------------------------------------------------
# Latency Statistics
# ---------------------------------------------------------------------------


def calculate_latency_stats(latencies_ns: list[int]) -> LatencyStats:
    """Compute latency statistics from nanosecond measurements.

    Sorts the latencies, calculates linear-interpolation percentiles,
    and converts all values from nanoseconds to microseconds.

    Args:
        latencies_ns: List of latency measurements in nanoseconds.
            Must contain at least one value.

    Returns:
        :class:`LatencyStats` with all values in microseconds.

    Raises:
        ValueError: If latencies_ns is empty.

    Example:
        >>> stats = calculate_latency_stats([1000, 2000, 3000, 4000, 5000])
        >>> stats.p50_us
        3.0
        >>> stats.min_us
        1.0
    """
    if not latencies_ns:
        raise ValueError("latencies_ns must not be empty")

    sorted_ns: list[int] = sorted(latencies_ns)
    sorted_floats: list[float] = [float(x) for x in sorted_ns]
    mean_ns: float = statistics.mean(sorted_floats)
    stddev_ns: float = (
        statistics.stdev(sorted_floats) if len(sorted_floats) > 1 else 0.0
    )

    ns_to_us: float = 1e-3  # nanoseconds → microseconds

    return LatencyStats(
        p50_us=calculate_percentile(
            sorted_values=sorted_floats, percentile=0.50,
        ) * ns_to_us,
        p95_us=calculate_percentile(
            sorted_values=sorted_floats, percentile=0.95,
        ) * ns_to_us,
        p99_us=calculate_percentile(
            sorted_values=sorted_floats, percentile=0.99,
        ) * ns_to_us,
        min_us=sorted_floats[0] * ns_to_us,
        max_us=sorted_floats[-1] * ns_to_us,
        mean_us=mean_ns * ns_to_us,
        stddev_us=stddev_ns * ns_to_us,
    )


# ---------------------------------------------------------------------------
# Synthetic Payload Generation
# ---------------------------------------------------------------------------


def build_synthetic_payloads(symbol: str, count: int) -> list[bytes]:
    """Generate realistic BidOfferV3 protobuf payloads with variation.

    Each payload is built by constructing a fully-populated BidOfferV3
    message with all 10 bid/ask levels, realistic ``Money`` nested
    objects, flags, and symbol. **Prices and volumes vary per message**
    to defeat CPU branch predictor and cache effects.

    Payloads are independently serialized via ``bytes(msg)``
    (betterproto serialization), producing unique ``bytes`` objects.
    This prevents reference-reuse cache effects that would make
    benchmark results unrealistically optimistic.

    Args:
        symbol: Stock symbol (e.g., ``"AOT"``).
        count: Number of payloads to generate. Must be > 0.

    Returns:
        List of serialized protobuf ``bytes``, each a unique object.

    Raises:
        ValueError: If count <= 0.

    Example:
        >>> payloads = build_synthetic_payloads("AOT", 5)
        >>> len(payloads)
        5
        >>> payloads[0] != payloads[1]  # varied
        True
    """
    if count <= 0:
        raise ValueError(f"count must be > 0, got {count}")

    payloads: list[bytes] = []
    for i in range(count):
        # Vary prices and volumes to defeat branch predictor / cache
        price_offset: int = i % 5
        vol_offset: int = i % 100

        msg: BidOfferV3 = BidOfferV3(
            symbol=symbol,
            # 10 bid levels with variation
            bid_price1=Money(units=25 + price_offset, nanos=500_000_000),
            bid_price2=Money(units=25 + price_offset, nanos=250_000_000),
            bid_price3=Money(units=25 + price_offset, nanos=0),
            bid_price4=Money(units=24 + price_offset, nanos=750_000_000),
            bid_price5=Money(units=24 + price_offset, nanos=500_000_000),
            bid_price6=Money(units=24 + price_offset, nanos=250_000_000),
            bid_price7=Money(units=24 + price_offset, nanos=0),
            bid_price8=Money(units=23 + price_offset, nanos=750_000_000),
            bid_price9=Money(units=23 + price_offset, nanos=500_000_000),
            bid_price10=Money(units=23 + price_offset, nanos=250_000_000),
            # 10 ask levels with variation
            ask_price1=Money(units=26 + price_offset, nanos=0),
            ask_price2=Money(units=26 + price_offset, nanos=250_000_000),
            ask_price3=Money(units=26 + price_offset, nanos=500_000_000),
            ask_price4=Money(units=26 + price_offset, nanos=750_000_000),
            ask_price5=Money(units=27 + price_offset, nanos=0),
            ask_price6=Money(units=27 + price_offset, nanos=250_000_000),
            ask_price7=Money(units=27 + price_offset, nanos=500_000_000),
            ask_price8=Money(units=27 + price_offset, nanos=750_000_000),
            ask_price9=Money(units=28 + price_offset, nanos=0),
            ask_price10=Money(units=28 + price_offset, nanos=250_000_000),
            # 10 bid volumes with variation
            bid_volume1=1000 + vol_offset,
            bid_volume2=800 + vol_offset,
            bid_volume3=600 + vol_offset,
            bid_volume4=400 + vol_offset,
            bid_volume5=200 + vol_offset,
            bid_volume6=150 + vol_offset,
            bid_volume7=100 + vol_offset,
            bid_volume8=80 + vol_offset,
            bid_volume9=50 + vol_offset,
            bid_volume10=30 + vol_offset,
            # 10 ask volumes with variation
            ask_volume1=900 + vol_offset,
            ask_volume2=700 + vol_offset,
            ask_volume3=500 + vol_offset,
            ask_volume4=350 + vol_offset,
            ask_volume5=250 + vol_offset,
            ask_volume6=180 + vol_offset,
            ask_volume7=120 + vol_offset,
            ask_volume8=90 + vol_offset,
            ask_volume9=60 + vol_offset,
            ask_volume10=40 + vol_offset,
            # Flags
            bid_flag=BidOfferV3BidAskFlag.NORMAL,
            ask_flag=BidOfferV3BidAskFlag.NORMAL,
        )
        payloads.append(bytes(msg))

    return payloads


# ---------------------------------------------------------------------------
# GC Measurement
# ---------------------------------------------------------------------------


class GCBaseline:
    """Captured GC state before a benchmark measurement.

    Attributes:
        gen0_collections: Generation-0 collection count at capture.
        alloc_blocks: ``sys.getallocatedblocks()`` at capture.
        gc_was_enabled: Whether GC was enabled at capture time.

    Example:
        >>> baseline = capture_gc_baseline(gc_disabled=False)
        >>> baseline.gen0_collections >= 0
        True
    """

    __slots__ = ("gen0_collections", "alloc_blocks", "gc_was_enabled")

    def __init__(
        self,
        gen0_collections: int,
        alloc_blocks: int,
        gc_was_enabled: bool,
    ) -> None:
        self.gen0_collections: int = gen0_collections
        self.alloc_blocks: int = alloc_blocks
        self.gc_was_enabled: bool = gc_was_enabled


def capture_gc_baseline(gc_disabled: bool = False) -> GCBaseline:
    """Capture GC baseline state before measurement.

    Always calls ``gc.collect()`` first to clear prior garbage
    (imports, setup, previous run). Optionally disables GC for
    isolation debugging.

    Args:
        gc_disabled: If True, disable automatic GC. Default False
            (keep GC enabled for realistic measurement).

    Returns:
        :class:`GCBaseline` with captured state.

    Example:
        >>> baseline = capture_gc_baseline()
        >>> baseline.gen0_collections >= 0
        True
    """
    gc.collect()  # Clear import/setup garbage
    gc_was_enabled: bool = gc.isenabled()

    if gc_disabled:
        gc.disable()

    gen0_collections: int = gc.get_stats()[0]["collections"]
    alloc_blocks: int = sys.getallocatedblocks()

    return GCBaseline(
        gen0_collections=gen0_collections,
        alloc_blocks=alloc_blocks,
        gc_was_enabled=gc_was_enabled,
    )


def measure_gc_delta(baseline: GCBaseline) -> tuple[int, int]:
    """Compute GC metrics delta since baseline and restore GC state.

    Symmetrically restores GC to the state it was in before
    :func:`capture_gc_baseline` was called.

    Args:
        baseline: Baseline state from :func:`capture_gc_baseline`.

    Returns:
        Tuple of (gen0_collections_delta, alloc_blocks_delta).

    Example:
        >>> baseline = capture_gc_baseline()
        >>> gc_delta, alloc_delta = measure_gc_delta(baseline)
        >>> gc_delta >= 0
        True
    """
    gen0_now: int = gc.get_stats()[0]["collections"]
    alloc_now: int = sys.getallocatedblocks()

    # Symmetrically restore GC state to pre-baseline condition
    if baseline.gc_was_enabled:
        gc.enable()
    else:
        gc.disable()

    gc_delta: int = gen0_now - baseline.gen0_collections
    alloc_delta: int = alloc_now - baseline.alloc_blocks

    return gc_delta, alloc_delta


# ---------------------------------------------------------------------------
# CPU Measurement
# ---------------------------------------------------------------------------


def measure_cpu_percent(
    process_time_start: float,
    process_time_end: float,
    wall_time_start: float,
    wall_time_end: float,
) -> float:
    """Calculate CPU usage normalized per core.

    Accepts both start and end timestamps to avoid timing drift
    from delayed function calls.

    Formula: ``(process_delta / wall_delta) * 100 / cpu_count``

    This gives a meaningful "fraction of one core" percentage.
    Currently safe for single-threaded benchmarks. Must revisit
    if multi-threading is added.

    Args:
        process_time_start: ``time.process_time()`` at measurement start.
        process_time_end: ``time.process_time()`` at measurement end.
        wall_time_start: ``time.perf_counter()`` at measurement start.
        wall_time_end: ``time.perf_counter()`` at measurement end.

    Returns:
        CPU usage as a percentage (0-100 per core).
    """
    process_delta: float = process_time_end - process_time_start
    wall_delta: float = wall_time_end - wall_time_start

    if wall_delta <= 0:
        return 0.0

    cpu_count: int = os.cpu_count() or 1
    return (process_delta / wall_delta) * 100.0 / cpu_count


# ---------------------------------------------------------------------------
# Run Aggregation
# ---------------------------------------------------------------------------


def aggregate_runs(
    label: str,
    config: BenchmarkConfig,
    runs: list[RunResult],
) -> BenchmarkResult:
    """Aggregate multiple run results into a single benchmark result.

    Computes mean ± stddev for P99 latency. Flags results as
    unstable if mean_p99 <= 0 or if stddev exceeds 15% of mean.

    Args:
        label: Benchmark label (e.g., "SDK", "Adapter").
        config: Benchmark configuration used.
        runs: List of individual :class:`RunResult` instances.

    Returns:
        Aggregated :class:`BenchmarkResult` with confidence metrics.

    Raises:
        ValueError: If runs is empty.
    """
    if not runs:
        raise ValueError("runs must not be empty")

    p50_values: list[float] = [r.latency.p50_us for r in runs]
    p95_values: list[float] = [r.latency.p95_us for r in runs]
    p99_values: list[float] = [r.latency.p99_us for r in runs]
    cpu_values: list[float] = [r.cpu_percent for r in runs]
    gc_values: list[float] = [float(r.gc_collections) for r in runs]
    alloc_values: list[float] = [float(r.alloc_blocks_delta) for r in runs]
    throughput_values: list[float] = [r.throughput_msg_per_sec for r in runs]

    mean_p99: float = statistics.mean(p99_values)
    stddev_p99: float = (
        statistics.stdev(p99_values) if len(p99_values) > 1 else 0.0
    )
    # Stable requires positive mean AND stddev < 15% of mean
    is_stable: bool = mean_p99 > 0 and (stddev_p99 / mean_p99) < 0.15

    return BenchmarkResult(
        label=label,
        config=config,
        runs=runs,
        mean_p50_us=statistics.mean(p50_values),
        mean_p95_us=statistics.mean(p95_values),
        mean_p99_us=mean_p99,
        stddev_p99_us=stddev_p99,
        mean_cpu_percent=statistics.mean(cpu_values),
        mean_gc_collections=statistics.mean(gc_values),
        mean_alloc_blocks_delta=statistics.mean(alloc_values),
        mean_throughput=statistics.mean(throughput_values),
        is_stable=is_stable,
    )


# ---------------------------------------------------------------------------
# Comparison Table Formatting
# ---------------------------------------------------------------------------


def format_comparison_table(
    sdk: BenchmarkResult,
    adapter: BenchmarkResult,
) -> str:
    """Generate formatted ASCII comparison table.

    Computes improvement ratios for latencies (SDK/Adapter) and
    reduction percentages for CPU and GC metrics.

    Args:
        sdk: SDK baseline benchmark result.
        adapter: Adapter benchmark result.

    Returns:
        Formatted multi-line string with comparison table.

    Example:
        >>> table = format_comparison_table(sdk_result, adapter_result)
        >>> "P99 latency" in table
        True
    """
    lines: list[str] = []

    lines.append("=" * 70)
    lines.append("BENCHMARK RESULTS — Settrade Feed Adapter vs Official SDK")
    lines.append("=" * 70)
    lines.append(
        f"Environment:  {sys.platform}, CPython {sys.version.split()[0]}, "
        f"{os.cpu_count()} CPU"
    )
    lines.append(f"Symbol:       {sdk.config.symbol}")
    lines.append(f"Messages:     {sdk.config.num_messages:,} per run")
    lines.append(f"Warmup:       {sdk.config.warmup_count:,} (discarded)")
    lines.append(f"Runs:         {sdk.config.num_runs}")

    stability_sdk: str = "stable" if sdk.is_stable else "UNSTABLE"
    stability_adp: str = "stable" if adapter.is_stable else "UNSTABLE"
    lines.append(
        f"Stability:    SDK={stability_sdk}, Adapter={stability_adp}"
    )
    lines.append("=" * 70)
    lines.append("")

    # Header
    lines.append(
        f"{'Metric':<28} {'SDK':>12} {'Adapter':>12} {'Improvement':>14}"
    )
    lines.append("-" * 70)

    # Latency rows
    def _ratio(sdk_val: float, adp_val: float) -> str:
        if adp_val > 0:
            r: float = sdk_val / adp_val
            return f"{r:.2f}x faster"
        return "N/A"

    def _reduction(sdk_val: float, adp_val: float) -> str:
        if sdk_val > 0:
            pct: float = (1 - adp_val / sdk_val) * 100
            return f"-{pct:.0f}%"
        return "N/A"

    lines.append(
        f"{'P50 latency (us)':<28} {sdk.mean_p50_us:>12.1f} "
        f"{adapter.mean_p50_us:>12.1f} "
        f"{_ratio(sdk.mean_p50_us, adapter.mean_p50_us):>14}"
    )
    lines.append(
        f"{'P95 latency (us)':<28} {sdk.mean_p95_us:>12.1f} "
        f"{adapter.mean_p95_us:>12.1f} "
        f"{_ratio(sdk.mean_p95_us, adapter.mean_p95_us):>14}"
    )
    lines.append(
        f"{'P99 latency (us)':<28} {sdk.mean_p99_us:>12.1f} "
        f"{adapter.mean_p99_us:>12.1f} "
        f"{_ratio(sdk.mean_p99_us, adapter.mean_p99_us):>14}"
    )
    lines.append(
        f"{'  P99 stddev (us)':<28} "
        f"{'±' + f'{sdk.stddev_p99_us:.1f}':>12} "
        f"{'±' + f'{adapter.stddev_p99_us:.1f}':>12} "
        f"{'':>14}"
    )
    lines.append(
        f"{'CPU per core (%)':<28} {sdk.mean_cpu_percent:>12.1f} "
        f"{adapter.mean_cpu_percent:>12.1f} "
        f"{_reduction(sdk.mean_cpu_percent, adapter.mean_cpu_percent):>14}"
    )
    lines.append(
        f"{'GC gen-0 collections':<28} {sdk.mean_gc_collections:>12.0f} "
        f"{adapter.mean_gc_collections:>12.0f} "
        f"{_reduction(sdk.mean_gc_collections, adapter.mean_gc_collections):>14}"
    )
    lines.append(
        f"{'Alloc blocks delta':<28} {sdk.mean_alloc_blocks_delta:>12.0f} "
        f"{adapter.mean_alloc_blocks_delta:>12.0f} "
        f"{_reduction(sdk.mean_alloc_blocks_delta, adapter.mean_alloc_blocks_delta):>14}"
    )
    lines.append(
        f"{'Throughput (msg/s)':<28} {sdk.mean_throughput:>12,.0f} "
        f"{adapter.mean_throughput:>12,.0f} "
        f"{_ratio(adapter.mean_throughput, sdk.mean_throughput):>14}"
    )

    lines.append("")
    lines.append("=" * 70)

    # Performance target validation
    p99_ratio: float = (
        sdk.mean_p99_us / adapter.mean_p99_us if adapter.mean_p99_us > 0 else 0
    )
    lines.append("PERFORMANCE TARGET VALIDATION")
    lines.append("-" * 70)
    p99_pass: str = "PASS" if p99_ratio >= 3.0 else "FAIL"
    lines.append(f"  P99 improvement >= 3x:  {p99_ratio:.2f}x  [{p99_pass}]")

    lines.append("")
    lines.append("=" * 70)
    lines.append("")
    lines.append("Benchmark Limitations:")
    lines.append("  - Synthetic payloads (no network latency)")
    lines.append("  - Does not measure broker throttling or live burst behavior")
    lines.append("  - Isolates parse + normalization cost only")
    lines.append("  - Results may vary across hardware and Python versions")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON Serialization
# ---------------------------------------------------------------------------


def result_to_json(result: BenchmarkResult) -> str:
    """Serialize BenchmarkResult to JSON string.

    Args:
        result: Benchmark result to serialize.

    Returns:
        JSON string representation.
    """
    return result.model_dump_json(indent=2)


def result_from_json(json_str: str) -> BenchmarkResult:
    """Deserialize BenchmarkResult from JSON string.

    Args:
        json_str: JSON string to deserialize.

    Returns:
        Deserialized :class:`BenchmarkResult`.
    """
    return BenchmarkResult.model_validate_json(json_str)
