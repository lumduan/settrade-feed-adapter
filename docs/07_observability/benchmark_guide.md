# Benchmark Guide

How to measure and compare feed adapter performance.

Source: `scripts/benchmark_utils.py`, `scripts/benchmark_adapter.py`,
`scripts/benchmark_compare.py`, `scripts/benchmark_parallel.py`

---

## Overview

The benchmark infrastructure measures the parse-and-normalize latency of the
custom adapter versus the official SDK. It uses synthetic protobuf payloads,
linear-interpolation percentiles, and multi-run aggregation with stability
checks.

---

## Benchmark Modes

```python
from scripts.benchmark_utils import BenchmarkMode

BenchmarkMode.SYNTHETIC   # Default -- reproducible, no credentials needed
BenchmarkMode.LIVE        # Future -- requires credentials and market hours
```

| Mode | Description | Use Case |
| --- | --- | --- |
| `SYNTHETIC` | Pre-built protobuf payloads with per-message variation | CI, regression, comparison |
| `LIVE` | Live market data from Settrade sandbox | Future use |

---

## Synthetic Payloads

`build_synthetic_payloads(symbol, count)` generates realistic `BidOfferV3`
protobuf messages with:

- All 10 bid/ask price levels populated with `Money(units, nanos)` objects
- All 10 bid/ask volume levels populated
- `BidOfferV3BidAskFlag.NORMAL` flags
- **Per-message variation** to defeat CPU branch predictor and cache effects:
  - `price_offset = i % 5` varies price units
  - `vol_offset = i % 100` varies volumes

Each payload is independently serialized via `bytes(msg)`, producing unique
`bytes` objects. This prevents reference-reuse cache effects.

```python
from scripts.benchmark_utils import build_synthetic_payloads

payloads = build_synthetic_payloads("AOT", count=10_000)
len(payloads)          # 10000
payloads[0] != payloads[1]   # True (varied)
```

---

## Warmup

The first `warmup_count` messages (default 1000) are discarded from latency
statistics. This accounts for CPython 3.11+ adaptive specialization, which
requires approximately 1000 iterations to stabilize bytecode optimizations.

```python
from scripts.benchmark_utils import BenchmarkConfig

config = BenchmarkConfig(
    num_messages=10_000,   # total messages per run
    warmup_count=1_000,    # discarded from stats
)
# Measured messages = 10_000 - 1_000 = 9_000
```

The `warmup_count` must be strictly less than `num_messages` (enforced by a
Pydantic `model_validator`).

---

## Percentile Calculation

Percentiles use **linear interpolation** between adjacent sorted ranks,
matching `numpy.percentile(method='linear')`.

Algorithm:

```text
k = (n - 1) * percentile
f = floor(k)
c = ceil(k)
result = sorted[f] + (sorted[c] - sorted[f]) * (k - f)
```

```python
from scripts.benchmark_utils import calculate_percentile

calculate_percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.50)   # 3.0
calculate_percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.99)   # 4.96
```

The `calculate_latency_stats()` function computes P50, P95, P99, min, max,
mean, and stddev from a list of nanosecond latency measurements, returning
all values in microseconds.

---

## GC Measurement

The benchmark captures GC baseline before measurement and computes the delta
after:

- `capture_gc_baseline(gc_disabled=False)` -- runs `gc.collect()` to clear
  prior garbage, then captures gen-0 collection count and
  `sys.getallocatedblocks()`
- `measure_gc_delta(baseline)` -- returns `(gen0_collections_delta,
  alloc_blocks_delta)` and symmetrically restores GC state

```python
from scripts.benchmark_utils import capture_gc_baseline, measure_gc_delta

baseline = capture_gc_baseline(gc_disabled=False)
# ... run benchmark ...
gc_delta, alloc_delta = measure_gc_delta(baseline)
```

By default, GC is **enabled** during measurement for realistic results. Set
`gc_disabled=True` in `BenchmarkConfig` for allocation isolation debugging.

---

## CPU Measurement

CPU usage is normalized per core:

```text
cpu_percent = (process_time_delta / wall_time_delta) * 100 / cpu_count
```

This gives a meaningful "fraction of one core" percentage. Uses
`time.process_time()` for process CPU time and `time.perf_counter()` for wall
time. `os.cpu_count()` provides the core count (falls back to 1 if unknown).

---

## Multi-Run Aggregation

Each benchmark executes `num_runs` iterations (default 3). The `aggregate_runs()`
function computes mean and stddev across runs:

```python
from scripts.benchmark_utils import aggregate_runs

result = aggregate_runs(
    label="Adapter",
    config=config,
    runs=[run1, run2, run3],
)
result.mean_p99_us       # mean P99 latency across runs
result.stddev_p99_us     # stddev of P99 latency
result.is_stable         # True if stable
```

### Stability Check

A result is considered **stable** when:

```text
mean_p99 > 0 AND (stddev_p99 / mean_p99) < 0.15
```

That is, the standard deviation of P99 latency must be less than 15% of the
mean P99 latency. Unstable results are flagged in the comparison output.

For production CI gating, `num_runs >= 5` is recommended for reliable stddev
estimates.

---

## Benchmark Scripts

### benchmark_adapter.py

Measures the custom adapter parse path:

```bash
python -m scripts.benchmark_adapter
python -m scripts.benchmark_adapter --num-messages 50000 --num-runs 5
python -m scripts.benchmark_adapter --tracemalloc
```

Outputs JSON-formatted `BenchmarkResult` to stdout, progress to stderr.

### benchmark_compare.py

Runs both SDK and adapter benchmarks and produces a formatted comparison table:

```bash
python -m scripts.benchmark_compare
```

### benchmark_parallel.py

Runs benchmarks with parallel symbol processing:

```bash
python -m scripts.benchmark_parallel
```

---

## BenchmarkConfig Reference

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `num_messages` | `int` | `10_000` | Total messages per run (including warmup) |
| `warmup_count` | `int` | `1_000` | Messages discarded from stats |
| `num_runs` | `int` | `3` | Internal iterations for confidence intervals |
| `symbol` | `str` | `"AOT"` | Stock symbol for synthetic payloads |
| `mode` | `BenchmarkMode` | `SYNTHETIC` | Benchmark execution mode |
| `gc_disabled` | `bool` | `False` | Disable GC during measurement |
| `tracemalloc_enabled` | `bool` | `False` | Enable tracemalloc (adds ~10% overhead) |

---

## JSON Serialization

Results can be serialized and deserialized for comparison across runs:

```python
from scripts.benchmark_utils import result_to_json, result_from_json

json_str = result_to_json(result)
restored = result_from_json(json_str)
```

---

## Related Pages

- [Performance Targets](./performance_targets.md) -- target latencies and pass criteria
- [Metrics Reference](./metrics_reference.md) -- runtime metrics
