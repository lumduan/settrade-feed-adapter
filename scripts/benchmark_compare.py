"""Comparison report generator — runs SDK and adapter benchmarks.

Orchestrates both benchmark scripts as separate processes,
collects JSON results, and generates a formatted comparison table
with improvement ratios and performance target validation.

Separate processes ensure:
    - No GC pressure bleeding between benchmarks
    - No adaptive interpreter specialization cross-contamination
    - Each benchmark has full control of CPU cache state

Each benchmark internally excludes warmup from latency stats,
but wall time includes warmup for measurement symmetry.

Usage:
    python -m scripts.benchmark_compare
    python -m scripts.benchmark_compare --num-messages 50000 --num-runs 5
    python -m scripts.benchmark_compare --target-p99-ratio 2.5

Output:
    Formatted comparison table to stdout.
    Exit code 0 if P99 improvement >= target ratio, exit code 1 otherwise.
"""

import argparse
import subprocess
import sys

from scripts.benchmark_utils import (
    BenchmarkResult,
    format_comparison_table,
    result_from_json,
)


def run_benchmark_subprocess(
    module: str,
    num_messages: int,
    warmup: int,
    num_runs: int,
    symbol: str,
    gc_disabled: bool,
) -> BenchmarkResult:
    """Run a benchmark script as a subprocess and parse its JSON output.

    Args:
        module: Python module to run (e.g., ``"scripts.benchmark_sdk"``).
        num_messages: Total messages per run.
        warmup: Warmup messages to discard.
        num_runs: Number of internal runs.
        symbol: Stock symbol for payloads.
        gc_disabled: Whether to disable GC during measurement.

    Returns:
        Parsed :class:`BenchmarkResult` from the subprocess stdout.

    Raises:
        RuntimeError: If the subprocess fails or produces invalid output.
    """
    cmd: list[str] = [
        sys.executable, "-m", module,
        "--num-messages", str(num_messages),
        "--warmup", str(warmup),
        "--num-runs", str(num_runs),
        "--symbol", symbol,
    ]
    if gc_disabled:
        cmd.append("--gc-disabled")

    print(f"Running: {' '.join(cmd)}", file=sys.stderr)

    completed: subprocess.CompletedProcess[str] = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minute timeout
    )

    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
        raise RuntimeError(
            f"{module} failed with exit code {completed.returncode}"
        )

    # Print stderr (progress info) to our stderr
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")

    # Parse JSON from stdout
    json_output: str = completed.stdout.strip()
    if not json_output:
        raise RuntimeError(f"{module} produced no JSON output")

    try:
        return result_from_json(json_str=json_output)
    except Exception as exc:
        raise RuntimeError(
            f"{module} produced invalid JSON: {json_output[:200]}"
        ) from exc


def main() -> None:
    """Run both benchmarks and generate comparison report."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Run SDK and adapter benchmarks, generate comparison",
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
        "--target-p99-ratio",
        type=float,
        default=3.0,
        help="Minimum P99 improvement ratio to pass (default: 3.0)",
    )
    args: argparse.Namespace = parser.parse_args()

    # Run SDK benchmark
    print("=" * 50, file=sys.stderr)
    print("Running SDK baseline benchmark...", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    sdk_result: BenchmarkResult = run_benchmark_subprocess(
        module="scripts.benchmark_sdk",
        num_messages=args.num_messages,
        warmup=args.warmup,
        num_runs=args.num_runs,
        symbol=args.symbol,
        gc_disabled=args.gc_disabled,
    )

    # Run adapter benchmark
    print("", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print("Running adapter benchmark...", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    adapter_result: BenchmarkResult = run_benchmark_subprocess(
        module="scripts.benchmark_adapter",
        num_messages=args.num_messages,
        warmup=args.warmup,
        num_runs=args.num_runs,
        symbol=args.symbol,
        gc_disabled=args.gc_disabled,
    )

    # Generate comparison table
    print("", file=sys.stderr)
    table: str = format_comparison_table(
        sdk=sdk_result,
        adapter=adapter_result,
    )
    print(table)

    # Validate performance target
    if adapter_result.mean_p99_us <= 0:
        p99_ratio: float = float("inf")
    else:
        p99_ratio = sdk_result.mean_p99_us / adapter_result.mean_p99_us

    target: float = args.target_p99_ratio

    if p99_ratio >= target:
        print(
            f"\nPERFORMANCE TARGET MET: {p99_ratio:.2f}x P99 improvement "
            f"(target: >= {target:.1f}x)",
            file=sys.stderr,
        )
        sys.exit(0)
    else:
        print(
            f"\nPERFORMANCE TARGET NOT MET: {p99_ratio:.2f}x P99 improvement "
            f"(target: >= {target:.1f}x)",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
