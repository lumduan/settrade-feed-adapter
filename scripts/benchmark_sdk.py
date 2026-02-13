"""SDK baseline benchmark — measures the official settrade_v2 parse path.

This script benchmarks the **exact** parse path used by the official
``settrade_v2.realtime`` SDK:

    ``BidOfferV3().parse(payload).to_dict(casing=Casing.SNAKE, include_default_values=True)``

Including:
    - ``casing=Casing.SNAKE`` — string key transformation overhead
    - ``include_default_values=True`` — forces all fields into the dict
    - ``Money.to_dict()`` → implicit ``Decimal`` conversion inside betterproto

This represents the full overhead path that the adapter eliminates.

Usage:
    python -m scripts.benchmark_sdk
    python -m scripts.benchmark_sdk --num-messages 50000 --num-runs 5
    python -m scripts.benchmark_sdk --tracemalloc

Output:
    JSON-formatted :class:`BenchmarkResult` to stdout.
    Progress and per-run summaries to stderr.
"""

import argparse
import sys
import time
import tracemalloc as _tracemalloc

import betterproto

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


def run_sdk_benchmark(config: BenchmarkConfig) -> RunResult:
    """Execute a single SDK benchmark run.

    Measures the exact SDK parse path:
    ``BidOfferV3().parse(payload).to_dict(casing=SNAKE, include_default_values=True)``

    Warmup messages are processed but excluded from latency statistics.
    Throughput is calculated from measured messages only (excluding
    warmup) for consistency with latency stats.

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

        # ---- EXACT SDK PATH ----
        msg: BidOfferV3 = BidOfferV3().parse(payload)
        _result: dict = msg.to_dict(
            casing=betterproto.Casing.SNAKE,
            include_default_values=True,
        )
        # ---- END SDK PATH ----

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

    # Tracemalloc — net block delta per message (not total allocation calls)
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
    """Run SDK benchmark and output JSON result."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="SDK baseline benchmark for settrade_v2 parse path",
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
        f"SDK Benchmark: {config.num_messages} msgs, "
        f"{config.num_runs} runs, "
        f"warmup={config.warmup_count}",
        file=sys.stderr,
    )

    runs: list[RunResult] = []
    for run_idx in range(config.num_runs):
        print(f"  Run {run_idx + 1}/{config.num_runs}...", file=sys.stderr)
        run_result: RunResult = run_sdk_benchmark(config=config)
        runs.append(run_result)
        print(
            f"    P99={run_result.latency.p99_us:.1f}us, "
            f"GC={run_result.gc_collections}, "
            f"CPU={run_result.cpu_percent:.1f}%",
            file=sys.stderr,
        )

    result = aggregate_runs(label="SDK", config=config, runs=runs)
    print(result_to_json(result=result))


if __name__ == "__main__":
    main()
