# Benchmark Guide

Performance measurement and benchmarking methodology.

---

## Overview

This guide explains how to:
- ✅ Run benchmarks correctly
- ✅ Interpret benchmark results
- ✅ Compare adapter vs SDK performance
- ✅ Identify performance regressions

---

## Benchmark Scripts

### scripts/benchmark_adapter.py

**Purpose**: Measure adapter parse + normalize performance.

**Usage**:
```bash
uv run python scripts/benchmark_adapter.py
```

**What it measures**:
- Protobuf parsing (betterproto)
- Normalization to Pydantic models
- Memory allocation overhead

**Output**:
```
Adapter Benchmark (BestBidAsk)
==============================
Iterations: 10,000
Total time: 0.0823 sec
Per-message: 8.23 µs
Throughput: 121,507 msg/sec
```

---

### scripts/benchmark_sdk.py

**Purpose**: Measure SDK parse + normalize performance.

**Usage**:
```bash
uv run python scripts/benchmark_sdk.py
```

**What it measures**:
- SDK protobuf parsing (protobuf)
- SDK normalization
- Memory allocation overhead

**Output**:
```
SDK Benchmark (BestBidAsk)
==========================
Iterations: 10,000
Total time: 0.0934 sec
Per-message: 9.34 µs
Throughput: 107,066 msg/sec
```

---

### scripts/benchmark_compare.py

**Purpose**: Direct apples-to-apples comparison.

**Usage**:
```bash
uv run python scripts/benchmark_compare.py
```

**What it measures**:
- Both adapter and SDK with same payload
- Statistical comparison (mean, median, p50, p99)

**Output**:
```
Parse + Normalize Comparison (BestBidAsk)
==========================================
Adapter: 8.23 µs (mean), 8.10 µs (median), 9.50 µs (p99)
SDK:     9.34 µs (mean), 9.20 µs (median), 10.80 µs (p99)

Speedup: 1.13x faster (adapter)
```

---

### scripts/benchmark_parallel.py

**Purpose**: Measure parallel processing performance (multi-threaded).

**Usage**:
```bash
uv run python scripts/benchmark_parallel.py
```

**What it measures**:
- Throughput with multiple consumer threads
- GIL contention impact
- Dispatcher overhead

**Output**:
```
Parallel Benchmark (4 threads)
==============================
Total events: 40,000
Total time: 0.5234 sec
Throughput: 76,428 msg/sec
```

---

## Running Benchmarks

### Prerequisites

```bash
# Ensure dependencies installed
uv pip sync

# Ensure tests pass first
uv run python -m pytest tests/ -v
```

---

### Standard Benchmark Run

```bash
# 1. Adapter performance
uv run python scripts/benchmark_adapter.py

# 2. SDK performance
uv run python scripts/benchmark_sdk.py

# 3. Direct comparison
uv run python scripts/benchmark_compare.py

# 4. Parallel performance
uv run python scripts/benchmark_parallel.py
```

---

### Custom Iterations

```bash
# Run 100,000 iterations for more statistical confidence
uv run python scripts/benchmark_adapter.py --iterations 100000

# Quick test (1,000 iterations)
uv run python scripts/benchmark_adapter.py --iterations 1000
```

---

### Message Type Selection

```bash
# BestBidAsk (default)
uv run python scripts/benchmark_adapter.py --message-type best_bid_ask

# FullBidOffer (larger payload)
uv run python scripts/benchmark_adapter.py --message-type full_bid_offer
```

---

## Interpreting Results

### Throughput vs Latency

**Throughput**: Messages per second (higher is better)
-Formula**: `1 / latency_per_message`

**Latency**: Time per message (lower is better)
- **Formula**: `total_time / iterations`

**Example**:
```
Latency: 8.23 µs per message
Throughput: 1 / 0.00000823 = 121,507 msg/sec
```

---

### Percentiles

**p50 (median)**: 50% of samples are faster
**p99**: 99% of samples are faster (tail latency)

**Why p99 matters**: Represents worst-case behavior (important for latency-sensitive systems).

**Example**:
```
Mean: 8.23 µs
Median (p50): 8.10 µs  ← Typical case
p99: 9.50 µs            ← Worst case (1% of samples)
```

---

### Speedup Calculation

**Formula**: `speedup = time_baseline / time_optimized`

**Example**:
```
SDK time: 9.34 µs
Adapter time: 8.23 µs

Speedup = 9.34 / 8.23 = 1.13x faster
```

**Interpretation**:
- `> 1.0x`: Adapter is faster
- `< 1.0x`: SDK is faster
- `~1.0x`: No significant difference

---

## Performance Targets

### BestBidAsk

**Target latency**: < 10 µs per message (parse + normalize)

**Target throughput**: > 100,000 msg/sec (single thread)

**Current performance**: ~8 µs, ~121,000 msg/sec ✅

---

### FullBidOffer

**Target latency**: < 20 µs per message (parse + normalize)

**Target throughput**: > 50,000 msg/sec (single thread)

**Current performance**: ~15 µs, ~66,000 msg/sec ✅

---

## Benchmarking Best Practices

### 1. Warm-up Runs

**Problem**: First iterations slower due to cold caches, JIT compilation.

**Solution**: Run warm-up iterations before measurement.

```python
import time

# Warm-up
for _ in range(1000):
    parse_and_normalize(payload)

# Actual benchmark
start = time.perf_counter()
for _ in range(10000):
    parse_and_normalize(payload)
duration = time.perf_counter() - start
```

---

### 2. Multiple Runs

**Problem**: Single run may be affected by CPU throttling, background processes.

**Solution**: Run multiple times, report median.

```python
import statistics

runs = []
for _ in range(10):
    start = time.perf_counter()
    for _ in range(10000):
        parse_and_normalize(payload)
    runs.append(time.perf_counter() - start)

median_time = statistics.median(runs)
```

---

### 3. Isolate CPU

**Problem**: Background processes interfere with measurements.

**Solution**: Run on dedicated CPU core (Linux).

```bash
# Run on CPU core 0
taskset -c 0 uv run python scripts/benchmark_adapter.py
```

---

### 4. Disable CPU Frequency Scaling

**Problem**: CPU governor throttles performance.

**Solution**: Set CPU to performance mode (Linux).

```bash
# Requires root
sudo cpupower frequency-set -g performance

# Run benchmark
uv run python scripts/benchmark_adapter.py

# Reset to default
sudo cpupower frequency-set -g powersave
```

---

## Comparing Results

### Baseline vs Optimized

**Process**:
1. Run baseline benchmark
2. Make optimization
3. Run optimized benchmark
4. Calculate speedup

**Example**:
```bash
# Baseline (before optimization)
uv run python scripts/benchmark_adapter.py > baseline.txt

# Apply optimization (edit code)

# Optimized (after optimization)
uv run python scripts/benchmark_adapter.py > optimized.txt

# Compare
diff baseline.txt optimized.txt
```

---

### Regression Detection

**Process**:
1. Run benchmarks on main branch
2. Switch to feature branch
3. Run benchmarks again
4. Compare results

**Alert if**:
- Latency increased > 10%
- Throughput decreased > 10%

**Example**:
```bash
# Main branch
git checkout main
uv run python scripts/benchmark_adapter.py > main_results.txt

# Feature branch
git checkout feature/optimization
uv run python scripts/benchmark_adapter.py > feature_results.txt

# Compare
python scripts/benchmark_compare_files.py main_results.txt feature_results.txt
```

---

## CI/CD Integration

### GitHub Actions Benchmark

```yaml
name: Benchmark

on: [pull_request]

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run benchmarks
        run: |
          uv pip sync
          uv run python scripts/benchmark_adapter.py
          uv run python scripts/benchmark_sdk.py
          uv run python scripts/benchmark_compare.py
      - name: Check for regressions
        run: |
          # Compare with baseline (stored artifact)
          python scripts/check_regression.py
```

---

## Profiling

### cProfile

**Purpose**: Identify hotspots in code.

```bash
python -m cProfile -o profile.stats scripts/benchmark_adapter.py
```

**Analyze**:
```python
import pstats

stats = pstats.Stats("profile.stats")
stats.sort_stats("cumulative")
stats.print_stats(10)  # Top 10 functions
```

---

### py-spy (sampling profiler)

**Purpose**: Low-overhead profiling in production.

```bash
# Install
pip install py-spy

# Profile running process
py-spy top --pid <process_id>

# Generate flame graph
py-spy record -o profile.svg -- python scripts/benchmark_adapter.py
```

---

## Implementation Reference

See [scripts/](../../scripts/) folder:
- `benchmark_adapter.py` — Adapter benchmarks
- `benchmark_sdk.py` — SDK benchmarks
- `benchmark_compare.py` — Direct comparison
- `benchmark_parallel.py` — Multi-threaded benchmarks
- `benchmark_utils.py` — Shared utilities

---

## Next Steps

- **[Performance Targets](./performance_targets.md)** — Target latencies
- **[Metrics Reference](./metrics_reference.md)** — All metrics
- **[Tuning Guide](../09_production_guide/tuning_guide.md)** — Performance tuning
