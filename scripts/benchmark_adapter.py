"""Adapter benchmark — measures the custom feed adapter parse path.

This script benchmarks the **exact** parse path used by the custom
``BidOfferAdapter``:

    ``BidOfferV3().parse(payload)`` → direct field access →
    ``BestBidAsk.model_construct(...)`` with inline Money conversion

This path eliminates all SDK overhead:
    - No ``.to_dict()`` — direct field access instead
    - No ``Decimal`` conversion — inline ``units + nanos * 1e-9``
    - No ``casing`` transformation — field names are hardcoded
    - No ``include_default_values`` — only needed fields extracted

Usage:
    python -m scripts.benchmark_adapter
    python -m scripts.benchmark_adapter --num-messages 50000 --num-runs 5
    python -m scripts.benchmark_adapter --tracemalloc

Output:
    JSON-formatted :class:`BenchmarkResult` to stdout.
    Progress and per-run summaries to stderr.
"""

import argparse
import sys
import time
import tracemalloc as _tracemalloc

from core.events import BestBidAsk
from scripts.benchmark_utils import (
    BenchmarkConfig,
    RunResult,
    aggregate_runs,
    build_synthetic_payloads,
    calculate_latency_stats,
    capture_gc_baseline,
    measure_cpu_percent,
    measure_gc_delta,
    result_to_json,
)
from settrade_v2.pb.bidofferv3_pb2 import BidOfferV3


def run_adapter_benchmark(config: BenchmarkConfig) -> RunResult:
    """Execute a single adapter benchmark run.

    Measures the exact adapter parse path:
    ``BidOfferV3().parse(payload)`` → ``BestBidAsk.model_construct(...)``
    with inline Money conversion (``units + nanos * 1e-9``).

    This mirrors the hot path in ``BidOfferAdapter._parse_best_bid_ask()``.

    Warmup messages are processed but excluded from latency statistics.
    Throughput is calculated from measured messages only (excluding
    warmup) for consistency with latency stats.

    Note on fairness:
        The ``bid_flag`` / ``ask_flag`` fields are accessed directly
        without ``int()`` cast to match the adapter's minimal-overhead
        design. The SDK path does not explicitly cast either (``to_dict``
        returns primitives). This keeps the comparison fair.

    Args:
        config: Benchmark configuration.

    Returns:
        :class:`RunResult` with latency, GC, CPU, and throughput metrics.
    """
    payloads: list[bytes] = build_synthetic_payloads(
        symbol=config.symbol,
        count=config.num_messages,
    )

    # Capture GC baseline (gc.collect() clears prior garbage)
    gc_baseline = capture_gc_baseline(gc_disabled=config.gc_disabled)

    # Optional tracemalloc
    tracemalloc_active: bool = config.tracemalloc_enabled
    if tracemalloc_active:
        _tracemalloc.start()
        snap_before = _tracemalloc.take_snapshot()

    latencies_ns: list[int] = []
    process_start: float = time.process_time()
    wall_start: float = time.perf_counter()

    for i, payload in enumerate(payloads):
        t0: int = time.perf_counter_ns()

        # ---- EXACT ADAPTER PATH ----
        # Mirrors BidOfferAdapter._parse_best_bid_ask()
        msg: BidOfferV3 = BidOfferV3().parse(payload)
        _event: BestBidAsk = BestBidAsk.model_construct(
            symbol=msg.symbol,
            bid=msg.bid_price1.units + msg.bid_price1.nanos * 1e-9,
            ask=msg.ask_price1.units + msg.ask_price1.nanos * 1e-9,
            bid_vol=msg.bid_volume1,
            ask_vol=msg.ask_volume1,
            bid_flag=msg.bid_flag,
            ask_flag=msg.ask_flag,
            recv_ts=t0,  # Use t0 as recv_ts (simulates time.time_ns())
            recv_mono_ns=t0,
        )
        # ---- END ADAPTER PATH ----

        t1: int = time.perf_counter_ns()

        # Skip warmup messages from statistics
        if i >= config.warmup_count:
            latencies_ns.append(t1 - t0)

    process_end: float = time.process_time()
    wall_end: float = time.perf_counter()

    # GC delta
    gc_collections, alloc_delta = measure_gc_delta(baseline=gc_baseline)

    # CPU
    cpu_pct: float = measure_cpu_percent(
        process_time_start=process_start,
        process_time_end=process_end,
        wall_time_start=wall_start,
        wall_time_end=wall_end,
    )

    # Throughput — measured messages only (consistent with latency stats)
    wall_duration: float = wall_end - wall_start
    num_measured: int = len(latencies_ns)
    throughput: float = (
        num_measured / wall_duration if wall_duration > 0 else 0.0
    )

    # Tracemalloc — net block delta per message
    tracemalloc_net_blocks: float | None = None
    if tracemalloc_active:
        snap_after = _tracemalloc.take_snapshot()
        stats = snap_after.compare_to(snap_before, "lineno")
        total_net_blocks: int = sum(s.count for s in stats if s.count > 0)
        tracemalloc_net_blocks = (
            total_net_blocks / num_measured if num_measured > 0 else 0.0
        )
        _tracemalloc.stop()

    return RunResult(
        latency=calculate_latency_stats(latencies_ns=latencies_ns),
        gc_collections=gc_collections,
        alloc_blocks_delta=alloc_delta,
        cpu_percent=cpu_pct,
        throughput_msg_per_sec=throughput,
        tracemalloc_net_blocks_per_msg=tracemalloc_net_blocks,
        num_measured=num_measured,
    )


def main() -> None:
    """Run adapter benchmark and output JSON result."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Adapter benchmark for BidOfferAdapter parse path",
    )
    parser.add_argument(
        "--num-messages",
        type=int,
        default=10_000,
        help="Total messages per run (default: 10000)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1_000,
        help="Warmup messages to discard (default: 1000)",
    )
    parser.add_argument(
        "--num-runs",
        type=int,
        default=3,
        help="Number of runs for confidence (default: 3)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="AOT",
        help="Symbol for payload generation (default: AOT)",
    )
    parser.add_argument(
        "--gc-disabled",
        action="store_true",
        help="Disable GC during measurement (isolation mode)",
    )
    parser.add_argument(
        "--tracemalloc",
        action="store_true",
        help="Enable tracemalloc net block counting (~10%% overhead)",
    )
    args: argparse.Namespace = parser.parse_args()

    config: BenchmarkConfig = BenchmarkConfig(
        num_messages=args.num_messages,
        warmup_count=args.warmup,
        num_runs=args.num_runs,
        symbol=args.symbol,
        gc_disabled=args.gc_disabled,
        tracemalloc_enabled=args.tracemalloc,
    )

    print(
        f"Adapter Benchmark: {config.num_messages} msgs, "
        f"{config.num_runs} runs, "
        f"warmup={config.warmup_count}",
        file=sys.stderr,
    )

    runs: list[RunResult] = []
    for run_idx in range(config.num_runs):
        print(f"  Run {run_idx + 1}/{config.num_runs}...", file=sys.stderr)
        run_result: RunResult = run_adapter_benchmark(config=config)
        runs.append(run_result)
        print(
            f"    P99={run_result.latency.p99_us:.1f}us, "
            f"GC={run_result.gc_collections}, "
            f"CPU={run_result.cpu_percent:.1f}%",
            file=sys.stderr,
        )

    result = aggregate_runs(label="Adapter", config=config, runs=runs)
    print(result_to_json(result=result))


if __name__ == "__main__":
    main()
