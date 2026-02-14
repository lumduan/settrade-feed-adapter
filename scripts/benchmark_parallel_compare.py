import multiprocessing as mp
import subprocess
import sys
import json
import statistics
import re


SYMBOLS = ["AOT", "AOTH26"]

NUM_MESSAGES = 70000
WARMUP = 10000
NUM_RUNS = 5


def extract_json_from_output(output: str):
    """
    Extract last JSON object from stdout
    """
    matches = re.findall(r"\{.*\}", output, re.DOTALL)
    if not matches:
        raise RuntimeError("No JSON found in benchmark output")
    return json.loads(matches[-1])


def run_and_capture(module_name: str, symbol: str, result_dict):
    cmd = [
        sys.executable,
        "-m",
        module_name,
        "--num-messages", str(NUM_MESSAGES),
        "--warmup", str(WARMUP),
        "--num-runs", str(NUM_RUNS),
        "--symbol", symbol,
        "--gc-disabled",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        raise RuntimeError(f"{module_name} failed")

    result_dict[symbol] = extract_json_from_output(proc.stdout)


def aggregate(results):
    return {
        "p50": statistics.mean(r["mean_p50_us"] for r in results.values()),
        "p95": statistics.mean(r["mean_p95_us"] for r in results.values()),
        "p99": statistics.mean(r["mean_p99_us"] for r in results.values()),
        "throughput": statistics.mean(r["mean_throughput"] for r in results.values()),
    }


def print_table(sdk_stats, adapter_stats):
    print("\n" + "=" * 72)
    print("PARALLEL BENCHMARK COMPARISON (2 SYMBOLS)")
    print("=" * 72)
    print(f"{'Metric':<25} {'SDK':>12} {'Adapter':>12} {'Improvement':>15}")
    print("-" * 72)

    def ratio(a, b):
        return a / b if b != 0 else 0

    print(f"{'P50 (us)':<25}"
          f"{sdk_stats['p50']:>12.1f}"
          f"{adapter_stats['p50']:>12.1f}"
          f"{ratio(sdk_stats['p50'], adapter_stats['p50']):>14.2f}x")

    print(f"{'P95 (us)':<25}"
          f"{sdk_stats['p95']:>12.1f}"
          f"{adapter_stats['p95']:>12.1f}"
          f"{ratio(sdk_stats['p95'], adapter_stats['p95']):>14.2f}x")

    print(f"{'P99 (us)':<25}"
          f"{sdk_stats['p99']:>12.1f}"
          f"{adapter_stats['p99']:>12.1f}"
          f"{ratio(sdk_stats['p99'], adapter_stats['p99']):>14.2f}x")

    print(f"{'Throughput (msg/s)':<25}"
          f"{sdk_stats['throughput']:>12.0f}"
          f"{adapter_stats['throughput']:>12.0f}"
          f"{ratio(adapter_stats['throughput'], sdk_stats['throughput']):>14.2f}x")

    print("=" * 72)
    print()


def run_parallel(module_name: str):
    manager = mp.Manager()
    result_dict = manager.dict()
    processes = []

    for symbol in SYMBOLS:
        p = mp.Process(
            target=run_and_capture,
            args=(module_name, symbol, result_dict),
        )
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

    return aggregate(dict(result_dict))


if __name__ == "__main__":
    print("\nRunning SDK (parallel)...")
    sdk_stats = run_parallel("scripts.benchmark_sdk")

    print("\nRunning Adapter (parallel)...")
    adapter_stats = run_parallel("scripts.benchmark_adapter")

    print_table(sdk_stats, adapter_stats)