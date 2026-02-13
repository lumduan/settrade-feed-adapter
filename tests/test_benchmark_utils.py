"""Unit tests for benchmark utilities.

Tests cover:
    - Linear interpolation percentile calculation
    - Latency statistics computation
    - Synthetic payload generation and realism
    - Pydantic model validation and immutability
    - GC baseline capture and restore
    - CPU measurement
    - Run aggregation with stability detection
    - Comparison table formatting
    - JSON serialization roundtrip
"""

import gc
import math

import pytest

from scripts.benchmark_utils import (
    BenchmarkConfig,
    BenchmarkMode,
    BenchmarkResult,
    GCBaseline,
    LatencyStats,
    RunResult,
    aggregate_runs,
    build_synthetic_payloads,
    calculate_latency_stats,
    calculate_percentile,
    capture_gc_baseline,
    format_comparison_table,
    measure_cpu_percent,
    measure_gc_delta,
    result_from_json,
    result_to_json,
)
from settrade_v2.pb.bidofferv3_pb2 import BidOfferV3


# -----------------------------------------------------------------------
# Percentile Calculation
# -----------------------------------------------------------------------


class TestCalculatePercentile:
    """Tests for linear interpolation percentile."""

    def test_p50_odd_count(self) -> None:
        """P50 of [1,2,3,4,5] should be 3.0 (exact middle)."""
        result: float = calculate_percentile(
            sorted_values=[1.0, 2.0, 3.0, 4.0, 5.0],
            percentile=0.5,
        )
        assert result == 3.0

    def test_p50_even_count(self) -> None:
        """P50 of [1,2,3,4] should interpolate to 2.5."""
        result: float = calculate_percentile(
            sorted_values=[1.0, 2.0, 3.0, 4.0],
            percentile=0.5,
        )
        assert result == 2.5

    def test_p99_interpolation(self) -> None:
        """P99 of [1,2,3,4,5] should interpolate near 5.0."""
        result: float = calculate_percentile(
            sorted_values=[1.0, 2.0, 3.0, 4.0, 5.0],
            percentile=0.99,
        )
        # k = (5-1) * 0.99 = 3.96; f=3, c=4
        # result = 4 + (5 - 4) * 0.96 = 4.96
        assert math.isclose(result, 4.96, rel_tol=1e-9)

    def test_p0_returns_first(self) -> None:
        """P0 should return the first element."""
        result: float = calculate_percentile(
            sorted_values=[10.0, 20.0, 30.0],
            percentile=0.0,
        )
        assert result == 10.0

    def test_p100_returns_last(self) -> None:
        """P100 should return the last element."""
        result: float = calculate_percentile(
            sorted_values=[10.0, 20.0, 30.0],
            percentile=1.0,
        )
        assert result == 30.0

    def test_single_element(self) -> None:
        """Single element should be returned for any percentile."""
        assert calculate_percentile(
            sorted_values=[42.0], percentile=0.0,
        ) == 42.0
        assert calculate_percentile(
            sorted_values=[42.0], percentile=0.5,
        ) == 42.0
        assert calculate_percentile(
            sorted_values=[42.0], percentile=1.0,
        ) == 42.0

    def test_two_elements_interpolation(self) -> None:
        """Two elements should interpolate linearly."""
        result: float = calculate_percentile(
            sorted_values=[10.0, 20.0],
            percentile=0.5,
        )
        assert result == 15.0

    def test_empty_raises_value_error(self) -> None:
        """Empty list should raise ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            calculate_percentile(sorted_values=[], percentile=0.5)

    def test_percentile_out_of_range_raises(self) -> None:
        """Percentile outside [0, 1] should raise ValueError."""
        with pytest.raises(ValueError, match="must be in"):
            calculate_percentile(sorted_values=[1.0], percentile=1.1)
        with pytest.raises(ValueError, match="must be in"):
            calculate_percentile(sorted_values=[1.0], percentile=-0.1)

    def test_matches_known_distribution(self) -> None:
        """Verify against hand-calculated values for 10-element list."""
        values: list[float] = [
            float(i) for i in range(1, 11)
        ]  # [1, 2, ..., 10]
        # P50: k = 9 * 0.5 = 4.5 → 5 + (6-5)*0.5 = 5.5
        assert math.isclose(
            calculate_percentile(sorted_values=values, percentile=0.5),
            5.5,
            rel_tol=1e-9,
        )


# -----------------------------------------------------------------------
# Latency Statistics
# -----------------------------------------------------------------------


class TestCalculateLatencyStats:
    """Tests for latency stats computation."""

    def test_basic_stats(self) -> None:
        """Compute stats from known nanosecond values."""
        latencies: list[int] = [1000, 2000, 3000, 4000, 5000]
        stats: LatencyStats = calculate_latency_stats(latencies_ns=latencies)

        assert stats.p50_us == 3.0  # 3000ns → 3.0us
        assert stats.min_us == 1.0
        assert stats.max_us == 5.0
        assert math.isclose(stats.mean_us, 3.0, rel_tol=1e-9)

    def test_p95_p99_conversion(self) -> None:
        """Percentiles are correctly converted from ns to us."""
        # 100 values: 1000, 2000, ..., 100000 ns
        latencies: list[int] = [i * 1000 for i in range(1, 101)]
        stats: LatencyStats = calculate_latency_stats(latencies_ns=latencies)

        # All values should be in microseconds
        assert stats.min_us == 1.0
        assert stats.max_us == 100.0
        assert stats.p95_us > stats.p50_us
        assert stats.p99_us > stats.p95_us

    def test_single_value(self) -> None:
        """Single value should work with zero stddev."""
        stats: LatencyStats = calculate_latency_stats(latencies_ns=[5000])
        assert stats.p50_us == 5.0
        assert stats.stddev_us == 0.0

    def test_empty_raises_value_error(self) -> None:
        """Empty list should raise ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            calculate_latency_stats(latencies_ns=[])

    def test_immutability(self) -> None:
        """LatencyStats should be frozen."""
        stats: LatencyStats = calculate_latency_stats(
            latencies_ns=[1000, 2000],
        )
        with pytest.raises(Exception):
            stats.p50_us = 999.0  # type: ignore[misc]


# -----------------------------------------------------------------------
# Synthetic Payload Generation
# -----------------------------------------------------------------------


class TestBuildSyntheticPayloads:
    """Tests for synthetic protobuf payload generation."""

    def test_correct_count(self) -> None:
        """Generates the requested number of payloads."""
        payloads: list[bytes] = build_synthetic_payloads(
            symbol="AOT", count=5,
        )
        assert len(payloads) == 5

    def test_valid_protobuf(self) -> None:
        """Each payload is valid parseable BidOfferV3."""
        payloads: list[bytes] = build_synthetic_payloads(
            symbol="AOT", count=3,
        )
        for payload in payloads:
            msg: BidOfferV3 = BidOfferV3().parse(payload)
            assert msg.symbol == "AOT"

    def test_all_10_levels_populated(self) -> None:
        """All 10 bid/ask levels have non-zero prices."""
        payloads: list[bytes] = build_synthetic_payloads(
            symbol="PTT", count=1,
        )
        msg: BidOfferV3 = BidOfferV3().parse(payloads[0])

        # Check all 10 bid prices have units > 0
        assert msg.bid_price1.units > 0
        assert msg.bid_price5.units > 0
        assert msg.bid_price10.units > 0

        # Check all 10 ask prices have units > 0
        assert msg.ask_price1.units > 0
        assert msg.ask_price5.units > 0
        assert msg.ask_price10.units > 0

    def test_money_fields_realistic(self) -> None:
        """Money fields have realistic units and nanos."""
        payloads: list[bytes] = build_synthetic_payloads(
            symbol="AOT", count=1,
        )
        msg: BidOfferV3 = BidOfferV3().parse(payloads[0])

        # bid_price1 should be ~25.5 (units=25, nanos=500M)
        assert msg.bid_price1.units >= 20
        assert msg.bid_price1.nanos >= 0

    def test_payloads_vary(self) -> None:
        """Payloads should differ between messages (variation)."""
        payloads: list[bytes] = build_synthetic_payloads(
            symbol="AOT", count=10,
        )
        # First 5 payloads cycle through price_offset 0-4
        # So payload[0] != payload[1]
        assert payloads[0] != payloads[1]

    def test_payloads_cycle_after_5(self) -> None:
        """Payloads cycle every 5 (price_offset = i % 5)."""
        payloads: list[bytes] = build_synthetic_payloads(
            symbol="AOT", count=10,
        )
        # payload[0] and payload[5] have same price_offset but
        # different vol_offset (0 vs 5), so they differ
        assert payloads[0] != payloads[5]

    def test_count_zero_raises(self) -> None:
        """Count <= 0 should raise ValueError."""
        with pytest.raises(ValueError, match="must be > 0"):
            build_synthetic_payloads(symbol="AOT", count=0)
        with pytest.raises(ValueError, match="must be > 0"):
            build_synthetic_payloads(symbol="AOT", count=-1)

    def test_flags_set(self) -> None:
        """Bid/ask flags should be NORMAL (1)."""
        payloads: list[bytes] = build_synthetic_payloads(
            symbol="AOT", count=1,
        )
        msg: BidOfferV3 = BidOfferV3().parse(payloads[0])
        assert int(msg.bid_flag) == 1  # NORMAL
        assert int(msg.ask_flag) == 1  # NORMAL


# -----------------------------------------------------------------------
# Benchmark Config Validation
# -----------------------------------------------------------------------


class TestBenchmarkConfig:
    """Tests for BenchmarkConfig Pydantic model."""

    def test_defaults(self) -> None:
        """Default config has expected values."""
        config: BenchmarkConfig = BenchmarkConfig()
        assert config.num_messages == 10_000
        assert config.warmup_count == 1_000
        assert config.num_runs == 3
        assert config.symbol == "AOT"
        assert config.mode == BenchmarkMode.SYNTHETIC
        assert config.gc_disabled is False
        assert config.tracemalloc_enabled is False

    def test_warmup_must_be_less_than_messages(self) -> None:
        """warmup_count >= num_messages should raise."""
        with pytest.raises(ValueError, match="warmup_count"):
            BenchmarkConfig(num_messages=100, warmup_count=100)
        with pytest.raises(ValueError, match="warmup_count"):
            BenchmarkConfig(num_messages=100, warmup_count=200)

    def test_valid_warmup(self) -> None:
        """warmup_count < num_messages should be accepted."""
        config: BenchmarkConfig = BenchmarkConfig(
            num_messages=100, warmup_count=50,
        )
        assert config.warmup_count == 50

    def test_num_messages_must_be_positive(self) -> None:
        """num_messages <= 0 should raise."""
        with pytest.raises(Exception):
            BenchmarkConfig(num_messages=0)


# -----------------------------------------------------------------------
# Benchmark Mode Enum
# -----------------------------------------------------------------------


class TestBenchmarkMode:
    """Tests for BenchmarkMode enum."""

    def test_synthetic(self) -> None:
        assert BenchmarkMode.SYNTHETIC == "SYNTHETIC"

    def test_live(self) -> None:
        assert BenchmarkMode.LIVE == "LIVE"


# -----------------------------------------------------------------------
# GC Baseline and Delta
# -----------------------------------------------------------------------


class TestGCMeasurement:
    """Tests for GC baseline capture and delta measurement."""

    def test_capture_baseline_returns_valid_state(self) -> None:
        """Baseline should have non-negative gen0 collections."""
        baseline: GCBaseline = capture_gc_baseline(gc_disabled=False)
        assert baseline.gen0_collections >= 0
        assert baseline.alloc_blocks >= 0

    def test_gc_stays_enabled_by_default(self) -> None:
        """GC should remain enabled after baseline capture (default)."""
        was_enabled: bool = gc.isenabled()
        baseline: GCBaseline = capture_gc_baseline(gc_disabled=False)
        assert gc.isenabled() == was_enabled
        measure_gc_delta(baseline=baseline)

    def test_gc_disabled_mode(self) -> None:
        """GC should be disabled during measurement when requested."""
        baseline: GCBaseline = capture_gc_baseline(gc_disabled=True)
        assert not gc.isenabled()
        # Restore
        measure_gc_delta(baseline=baseline)
        # Should be re-enabled (was enabled before)
        assert gc.isenabled()

    def test_delta_non_negative(self) -> None:
        """GC delta should be >= 0."""
        baseline: GCBaseline = capture_gc_baseline(gc_disabled=False)
        gc_delta, alloc_delta = measure_gc_delta(baseline=baseline)
        assert gc_delta >= 0


# -----------------------------------------------------------------------
# CPU Measurement
# -----------------------------------------------------------------------


class TestCPUMeasurement:
    """Tests for CPU percentage calculation."""

    def test_zero_wall_time_returns_zero(self) -> None:
        """Zero wall time should return 0.0 (avoid division by zero)."""
        result: float = measure_cpu_percent(
            process_time_start=1.0,
            process_time_end=2.0,
            wall_time_start=5.0,
            wall_time_end=5.0,  # zero duration
        )
        assert result == 0.0

    def test_known_values(self) -> None:
        """Known process/wall deltas should produce expected CPU%."""
        # 1 second process time, 10 seconds wall time, 1 CPU
        # → 10% per core
        import os

        cpu_count: int = os.cpu_count() or 1
        result: float = measure_cpu_percent(
            process_time_start=0.0,
            process_time_end=1.0,
            wall_time_start=0.0,
            wall_time_end=10.0,
        )
        expected: float = (1.0 / 10.0) * 100.0 / cpu_count
        assert math.isclose(result, expected, rel_tol=1e-9)


# -----------------------------------------------------------------------
# Run Aggregation
# -----------------------------------------------------------------------


class TestAggregateRuns:
    """Tests for multi-run aggregation."""

    def _make_run(
        self, p99_us: float = 100.0, cpu: float = 20.0,
    ) -> RunResult:
        """Create a RunResult with specified P99 and CPU."""
        return RunResult(
            latency=LatencyStats(
                p50_us=50.0,
                p95_us=80.0,
                p99_us=p99_us,
                min_us=10.0,
                max_us=200.0,
                mean_us=60.0,
                stddev_us=20.0,
            ),
            gc_collections=10,
            alloc_blocks_delta=500,
            cpu_percent=cpu,
            throughput_msg_per_sec=10000.0,
            tracemalloc_net_blocks_per_msg=None,
            num_measured=9000,
        )

    def test_single_run(self) -> None:
        """Single run should produce mean == run value."""
        config: BenchmarkConfig = BenchmarkConfig(
            num_messages=10000, warmup_count=100,
        )
        run: RunResult = self._make_run(p99_us=100.0)
        result: BenchmarkResult = aggregate_runs(
            label="Test", config=config, runs=[run],
        )
        assert result.mean_p99_us == 100.0
        assert result.stddev_p99_us == 0.0

    def test_stable_runs(self) -> None:
        """Consistent runs should be flagged as stable."""
        config: BenchmarkConfig = BenchmarkConfig(
            num_messages=10000, warmup_count=100,
        )
        runs: list[RunResult] = [
            self._make_run(p99_us=100.0),
            self._make_run(p99_us=102.0),
            self._make_run(p99_us=101.0),
        ]
        result: BenchmarkResult = aggregate_runs(
            label="Test", config=config, runs=runs,
        )
        assert result.is_stable is True

    def test_unstable_runs(self) -> None:
        """Wildly varying runs should be flagged as unstable."""
        config: BenchmarkConfig = BenchmarkConfig(
            num_messages=10000, warmup_count=100,
        )
        runs: list[RunResult] = [
            self._make_run(p99_us=100.0),
            self._make_run(p99_us=200.0),
            self._make_run(p99_us=300.0),
        ]
        result: BenchmarkResult = aggregate_runs(
            label="Test", config=config, runs=runs,
        )
        assert result.is_stable is False

    def test_empty_runs_raises(self) -> None:
        """Empty runs list should raise ValueError."""
        config: BenchmarkConfig = BenchmarkConfig(
            num_messages=10000, warmup_count=100,
        )
        with pytest.raises(ValueError, match="must not be empty"):
            aggregate_runs(label="Test", config=config, runs=[])

    def test_result_immutable(self) -> None:
        """BenchmarkResult should be frozen."""
        config: BenchmarkConfig = BenchmarkConfig(
            num_messages=10000, warmup_count=100,
        )
        run: RunResult = self._make_run()
        result: BenchmarkResult = aggregate_runs(
            label="Test", config=config, runs=[run],
        )
        with pytest.raises(Exception):
            result.mean_p99_us = 999.0  # type: ignore[misc]


# -----------------------------------------------------------------------
# Comparison Table
# -----------------------------------------------------------------------


class TestFormatComparisonTable:
    """Tests for formatted comparison table output."""

    def _make_result(
        self, label: str, p99: float, cpu: float, gc: float,
    ) -> BenchmarkResult:
        """Create a BenchmarkResult with specified values."""
        config: BenchmarkConfig = BenchmarkConfig(
            num_messages=10000, warmup_count=100,
        )
        run: RunResult = RunResult(
            latency=LatencyStats(
                p50_us=p99 * 0.5,
                p95_us=p99 * 0.8,
                p99_us=p99,
                min_us=p99 * 0.2,
                max_us=p99 * 1.5,
                mean_us=p99 * 0.6,
                stddev_us=p99 * 0.1,
            ),
            gc_collections=int(gc),
            alloc_blocks_delta=1000,
            cpu_percent=cpu,
            throughput_msg_per_sec=10000.0,
            tracemalloc_net_blocks_per_msg=None,
            num_measured=9000,
        )
        return aggregate_runs(label=label, config=config, runs=[run])

    def test_contains_headers(self) -> None:
        """Table should contain expected header text."""
        sdk = self._make_result("SDK", p99=300.0, cpu=40.0, gc=100.0)
        adapter = self._make_result("Adapter", p99=100.0, cpu=15.0, gc=10.0)
        table: str = format_comparison_table(sdk=sdk, adapter=adapter)

        assert "BENCHMARK RESULTS" in table
        assert "P99 latency" in table
        assert "CPU per core" in table
        assert "GC gen-0" in table
        assert "Improvement" in table

    def test_contains_ratios(self) -> None:
        """Table should contain improvement ratios."""
        sdk = self._make_result("SDK", p99=300.0, cpu=40.0, gc=100.0)
        adapter = self._make_result("Adapter", p99=100.0, cpu=15.0, gc=10.0)
        table: str = format_comparison_table(sdk=sdk, adapter=adapter)

        assert "faster" in table

    def test_contains_limitations(self) -> None:
        """Table should include benchmark limitations section."""
        sdk = self._make_result("SDK", p99=300.0, cpu=40.0, gc=100.0)
        adapter = self._make_result("Adapter", p99=100.0, cpu=15.0, gc=10.0)
        table: str = format_comparison_table(sdk=sdk, adapter=adapter)

        assert "Benchmark Limitations" in table
        assert "Synthetic payloads" in table

    def test_performance_target_pass(self) -> None:
        """3x improvement should show PASS."""
        sdk = self._make_result("SDK", p99=300.0, cpu=40.0, gc=100.0)
        adapter = self._make_result("Adapter", p99=100.0, cpu=15.0, gc=10.0)
        table: str = format_comparison_table(sdk=sdk, adapter=adapter)

        assert "PASS" in table

    def test_performance_target_fail(self) -> None:
        """Less than 3x improvement should show FAIL."""
        sdk = self._make_result("SDK", p99=200.0, cpu=40.0, gc=100.0)
        adapter = self._make_result("Adapter", p99=100.0, cpu=15.0, gc=10.0)
        table: str = format_comparison_table(sdk=sdk, adapter=adapter)

        assert "FAIL" in table


# -----------------------------------------------------------------------
# JSON Serialization Roundtrip
# -----------------------------------------------------------------------


class TestJSONSerialization:
    """Tests for JSON serialization and deserialization."""

    def test_roundtrip(self) -> None:
        """Serialize → deserialize should produce equivalent result."""
        config: BenchmarkConfig = BenchmarkConfig(
            num_messages=10000, warmup_count=100,
        )
        run: RunResult = RunResult(
            latency=LatencyStats(
                p50_us=50.0,
                p95_us=80.0,
                p99_us=100.0,
                min_us=10.0,
                max_us=200.0,
                mean_us=60.0,
                stddev_us=20.0,
            ),
            gc_collections=10,
            alloc_blocks_delta=500,
            cpu_percent=20.0,
            throughput_msg_per_sec=10000.0,
            tracemalloc_net_blocks_per_msg=None,
            num_measured=9000,
        )
        original: BenchmarkResult = aggregate_runs(
            label="Test", config=config, runs=[run],
        )

        json_str: str = result_to_json(result=original)
        restored: BenchmarkResult = result_from_json(json_str=json_str)

        assert restored.label == original.label
        assert restored.mean_p99_us == original.mean_p99_us
        assert restored.is_stable == original.is_stable
        assert len(restored.runs) == len(original.runs)
