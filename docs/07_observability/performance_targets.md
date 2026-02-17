# Performance Targets

Target latencies, pass criteria, and benchmark limitations.

Source: `scripts/benchmark_utils.py` -- `format_comparison_table()`

---

## Overview

Performance targets define the minimum acceptable improvement of the custom
feed adapter over the official Settrade SDK. These targets are validated by
the benchmark infrastructure using synthetic payloads.

---

## Measured Percentiles

The benchmark measures three latency percentiles:

| Percentile | Meaning |
| --- | --- |
| P50 (median) | Typical message latency |
| P95 | Latency under moderate load |
| P99 | Tail latency -- worst-case for most messages |

All latencies are reported in **microseconds** (us). The underlying
measurements use `time.perf_counter_ns()` for nanosecond precision.

---

## Performance Target: P99 Improvement

The primary pass/fail criterion:

```text
P99 improvement >= 3x over SDK
```

This is computed as:

```text
p99_ratio = sdk_mean_p99_us / adapter_mean_p99_us
PASS if p99_ratio >= 3.0
```

The comparison table in `format_comparison_table()` reports this as:

```text
PERFORMANCE TARGET VALIDATION
----------------------------------------------------------------------
  P99 improvement >= 3x:  5.23x  [PASS]
```

---

## Stability Criteria

A benchmark result is considered **stable** when:

```text
mean_p99 > 0 AND (stddev_p99 / mean_p99) < 0.15
```

That is, the standard deviation of P99 latency across runs must be less than
15% of the mean P99 latency. Unstable results are flagged in the comparison
output and should not be used for pass/fail decisions.

For stable results:

- Use `num_runs >= 3` at minimum (default)
- Use `num_runs >= 5` for CI gating
- Stddev uses sample standard deviation (`n-1` divisor via `statistics.stdev`)

---

## What the Benchmark Measures

The adapter benchmark measures the **exact** parse path used in production:

```text
BidOfferV3().parse(payload)
    -> direct field access
    -> inline Money conversion (units + nanos * 1e-9)
    -> BestBidAsk.model_construct(...)
```

This isolates the cost of:

- Protobuf deserialization (`betterproto .parse()`)
- Field extraction (direct attribute access)
- Money-to-float conversion (inline arithmetic)
- Event model construction (`model_construct()` -- no validation)

---

## What the Benchmark Does NOT Measure

The benchmark has inherent limitations:

- **No network latency** -- synthetic payloads are pre-built `bytes` objects
- **No MQTT overhead** -- no paho-mqtt callback dispatch
- **No queue contention** -- no dispatcher push/poll
- **No broker throttling** -- no rate limiting from the Settrade broker
- **No live burst behavior** -- synthetic payloads have uniform variation
- **Hardware-specific** -- results vary across CPUs, memory, and Python versions

The benchmark isolates **parse + normalization cost only**. Production
end-to-end latency will be higher due to network, MQTT, and queue overhead.

---

## Comparison Table Format

The `format_comparison_table()` function produces an ASCII table comparing
SDK and adapter results:

```text
======================================================================
BENCHMARK RESULTS -- Settrade Feed Adapter vs Official SDK
======================================================================
Environment:  darwin, CPython 3.12.0, 10 CPU
Symbol:       AOT
Messages:     10,000 per run
Warmup:       1,000 (discarded)
Runs:         3
Stability:    SDK=stable, Adapter=stable
======================================================================

Metric                           SDK      Adapter    Improvement
----------------------------------------------------------------------
P50 latency (us)                42.0         8.0   5.25x faster
P95 latency (us)                80.0        15.0   5.33x faster
P99 latency (us)               120.0        22.0   5.45x faster
  P99 stddev (us)               +5.0        +1.2
CPU per core (%)                 3.2         0.8          -75%
GC gen-0 collections              12           2          -83%
Throughput (msg/s)            23,000     120,000   5.22x faster
```

---

## Related Pages

- [Benchmark Guide](./benchmark_guide.md) -- how to run benchmarks
- [Tuning Guide](../09_production_guide/tuning_guide.md) -- production optimization
