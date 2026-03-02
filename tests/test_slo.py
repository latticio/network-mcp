"""Tests for SLO/SLI framework with error budgets and health check deep validation."""

import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from network_mcp.observability import MetricsCollector
from network_mcp.slo import (
    DEFAULT_SLOS,
    SLO_DEFINITIONS,
    ErrorBudgetCalculator,
    SLITracker,
    SLODefinition,
    SLOEvaluator,
    SLOMetricType,
    SLOResult,
    _reset,
    check_slos,
    deep_health_check,
    get_sli_tracker,
    get_slo_violations,
    init_sli_tracker,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def collector():
    """Return a fresh MetricsCollector instance."""
    return MetricsCollector()


@pytest.fixture
def tracker():
    """Return a fresh SLITracker."""
    return SLITracker(window_seconds=3600)


@pytest.fixture(autouse=True)
def _reset_sli_tracker():
    """Reset the module-level SLI tracker singleton between tests."""
    _reset()
    yield
    _reset()


# ===========================================================================
# NEW SLI/SLO Framework Tests
# ===========================================================================


# ---------------------------------------------------------------------------
# 1. SLITracker.record_latency — P99 and P95 calculations
# ---------------------------------------------------------------------------


class TestSLITrackerLatency:
    def test_p99_with_100_samples(self, tracker):
        """Record 100 latency measurements, verify P99 calculation."""
        for i in range(100):
            tracker.record_latency("test_tool", float(i) / 100.0)  # 0.0 to 0.99s
        p99 = tracker.get_latency_p99()
        # P99 of [0.0..0.99] should be ~0.99
        assert p99 >= 0.98
        assert p99 <= 1.0

    def test_p95_with_100_samples(self, tracker):
        """Record 100 latency measurements, verify P95 calculation."""
        for i in range(100):
            tracker.record_latency("test_tool", float(i) / 100.0)
        p95 = tracker.get_latency_p95()
        # P95 of [0.0..0.99] should be ~0.95
        assert p95 >= 0.94
        assert p95 <= 0.96

    def test_p99_single_sample(self, tracker):
        tracker.record_latency("tool", 1.5)
        assert tracker.get_latency_p99() == 1.5

    def test_p95_single_sample(self, tracker):
        tracker.record_latency("tool", 2.0)
        assert tracker.get_latency_p95() == 2.0


# ---------------------------------------------------------------------------
# 2. SLITracker.record_outcome — error rate calculation
# ---------------------------------------------------------------------------


class TestSLITrackerOutcome:
    def test_error_rate_with_known_failures(self, tracker):
        """Record 1000 outcomes (990 success, 10 failure), verify ~1.0% error rate."""
        for _ in range(990):
            tracker.record_outcome("test_tool", True)
        for _ in range(10):
            tracker.record_outcome("test_tool", False)
        error_rate = tracker.get_error_rate()
        assert abs(error_rate - 1.0) < 0.01  # ~1.0%

    def test_error_rate_all_success(self, tracker):
        for _ in range(100):
            tracker.record_outcome("tool", True)
        assert tracker.get_error_rate() == 0.0

    def test_error_rate_all_failure(self, tracker):
        for _ in range(100):
            tracker.record_outcome("tool", False)
        assert tracker.get_error_rate() == 100.0

    def test_total_requests(self, tracker):
        for _ in range(50):
            tracker.record_outcome("tool", True)
        assert tracker.get_total_requests() == 50


# ---------------------------------------------------------------------------
# 3. SLITracker.record_health_check — availability calculation
# ---------------------------------------------------------------------------


class TestSLITrackerHealthCheck:
    def test_availability_all_pass(self, tracker):
        for _ in range(100):
            tracker.record_health_check(True)
        assert tracker.get_availability() == 100.0

    def test_availability_some_failures(self, tracker):
        for _ in range(90):
            tracker.record_health_check(True)
        for _ in range(10):
            tracker.record_health_check(False)
        avail = tracker.get_availability()
        assert abs(avail - 90.0) < 0.01

    def test_availability_all_fail(self, tracker):
        for _ in range(10):
            tracker.record_health_check(False)
        assert tracker.get_availability() == 0.0


# ---------------------------------------------------------------------------
# 4. SLITracker window pruning
# ---------------------------------------------------------------------------


class TestSLITrackerWindowPruning:
    def test_old_latency_excluded(self):
        """Measurements older than the window are excluded from calculations."""
        tracker = SLITracker(window_seconds=10)
        # Record a measurement, then mock time to make it old
        tracker.record_latency("tool", 5.0)
        # Manually adjust the timestamp to be old
        with tracker._lock:
            old_ts = time.monotonic() - 20  # 20 seconds ago, window is 10
            tracker._latency_samples[-1] = (old_ts, "tool", 5.0)

        # The old sample should be excluded
        assert tracker.get_latency_p99() == 0.0

    def test_old_outcomes_excluded(self):
        tracker = SLITracker(window_seconds=10)
        tracker.record_outcome("tool", False)
        with tracker._lock:
            old_ts = time.monotonic() - 20
            tracker._outcome_samples[-1] = (old_ts, "tool", False)

        # Old failure should be excluded — no data means 0.0% error rate
        assert tracker.get_error_rate() == 0.0

    def test_old_health_checks_excluded(self):
        tracker = SLITracker(window_seconds=10)
        tracker.record_health_check(False)
        with tracker._lock:
            old_ts = time.monotonic() - 20
            tracker._health_check_samples[-1] = (old_ts, False)

        # Old failure should be excluded — no data means 100.0% available
        assert tracker.get_availability() == 100.0

    def test_custom_window_override(self, tracker):
        """get_latency_p99(window_seconds=X) overrides the default window."""
        tracker.record_latency("tool", 1.0)
        # Very large window should include the sample
        assert tracker.get_latency_p99(window_seconds=9999) == 1.0
        # Zero-second window should exclude everything
        assert tracker.get_latency_p99(window_seconds=0) == 0.0


# ---------------------------------------------------------------------------
# 5. SLITracker thread safety
# ---------------------------------------------------------------------------


class TestSLITrackerThreadSafety:
    def test_concurrent_recording(self, tracker):
        """Run 10 threads recording concurrently, verify no crashes and correct counts."""
        errors = []

        def record_worker(thread_id):
            try:
                for i in range(100):
                    tracker.record_latency(f"tool_{thread_id}", float(i) * 0.01)
                    tracker.record_outcome(f"tool_{thread_id}", i % 10 != 0)
                    tracker.record_health_check(i % 20 != 0)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # 10 threads x 100 outcomes each = 1000 total
        assert tracker.get_total_requests() == 1000


# ---------------------------------------------------------------------------
# 6. ErrorBudgetCalculator.calculate — within budget
# ---------------------------------------------------------------------------


class TestErrorBudgetWithinBudget:
    def test_within_budget(self):
        """Verify budget calculation when within budget."""
        calc = ErrorBudgetCalculator(slo_target=99.5, window_seconds=3600)
        result = calc.calculate(current_value=99.8, total_requests=1000)

        assert result["slo_target"] == 99.5
        assert result["current_value"] == 99.8
        assert result["budget_total"] == 0.5
        assert result["budget_remaining"] == 0.3
        assert result["is_breached"] is False
        assert result["burn_rate"] < 1.0
        assert result["total_requests"] == 1000
        assert result["allowed_failures"] == 5  # 0.5% of 1000
        assert result["actual_failures"] == 2  # 0.2% of 1000

    def test_exactly_at_budget(self):
        """When current_value == slo_target, budget is exactly consumed but not breached."""
        calc = ErrorBudgetCalculator(slo_target=99.5, window_seconds=3600)
        result = calc.calculate(current_value=99.5, total_requests=1000)

        assert result["is_breached"] is False
        assert result["budget_remaining"] == 0.0
        assert result["burn_rate"] == 1.0


# ---------------------------------------------------------------------------
# 7. ErrorBudgetCalculator burn rate
# ---------------------------------------------------------------------------


class TestErrorBudgetBurnRate:
    def test_burn_rate_2x(self):
        """When consuming budget 2x faster than sustainable, burn_rate should be ~2.0."""
        calc = ErrorBudgetCalculator(slo_target=99.5, window_seconds=3600)
        # budget_total = 0.5%, consuming 1.0% (2x the budget)
        result = calc.calculate(current_value=99.0, total_requests=1000)

        assert result["burn_rate"] == 2.0
        assert result["is_breached"] is True

    def test_burn_rate_zero_when_perfect(self):
        calc = ErrorBudgetCalculator(slo_target=99.5, window_seconds=3600)
        result = calc.calculate(current_value=100.0, total_requests=1000)

        assert result["burn_rate"] == 0.0
        assert result["is_breached"] is False
        assert result["budget_remaining"] == 0.5

    def test_breached_scenario(self):
        """Verify is_breached is True when consumed > total budget."""
        calc = ErrorBudgetCalculator(slo_target=99.0, window_seconds=3600)
        # budget_total = 1.0%, current = 98.0% means consumed = 2.0% > 1.0%
        result = calc.calculate(current_value=98.0, total_requests=1000)

        assert result["is_breached"] is True
        assert result["budget_remaining"] == 0.0
        assert result["burn_rate"] == 2.0


# ---------------------------------------------------------------------------
# 8. SLOEvaluator.evaluate_all
# ---------------------------------------------------------------------------


class TestSLOEvaluatorEvaluateAll:
    def test_evaluate_all_structure(self, tracker):
        """Create evaluator with DEFAULT_SLOS, record some measurements, verify output structure."""
        # Record some data
        for _ in range(50):
            tracker.record_latency("tool", 0.5)
            tracker.record_outcome("tool", True)
            tracker.record_health_check(True)

        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        result = evaluator.evaluate_all()

        assert "overall_status" in result
        assert "slos" in result
        assert result["overall_status"] in ("compliant", "breached")

        # Should have entries for all 3 default SLOs
        assert len(result["slos"]) == 3
        for _name, slo_result in result["slos"].items():
            assert "name" in slo_result
            assert "description" in slo_result
            assert "metric_type" in slo_result
            assert "error_budget" in slo_result
            assert "is_breached" in slo_result["error_budget"]

    def test_evaluate_all_compliant_when_healthy(self, tracker):
        """All SLOs should be compliant when all measurements are successful."""
        for _ in range(100):
            tracker.record_latency("tool", 0.1)  # Well under 5s threshold
            tracker.record_outcome("tool", True)
            tracker.record_health_check(True)

        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        result = evaluator.evaluate_all()

        assert result["overall_status"] == "compliant"


# ---------------------------------------------------------------------------
# 9. SLOEvaluator.evaluate — single SLO
# ---------------------------------------------------------------------------


class TestSLOEvaluatorEvaluateSingle:
    def test_evaluate_latency_slo(self, tracker):
        """Evaluate just 'tool_call_latency_p99'."""
        for _ in range(100):
            tracker.record_latency("tool", 0.5)
            tracker.record_outcome("tool", True)

        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        result = evaluator.evaluate("tool_call_latency_p99")

        assert result["name"] == "tool_call_latency_p99"
        assert result["metric_type"] == "latency"
        assert result["threshold_value"] == 5.0
        assert result["error_budget"]["is_breached"] is False

    def test_evaluate_error_rate_slo(self, tracker):
        for _ in range(100):
            tracker.record_outcome("tool", True)

        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        result = evaluator.evaluate("tool_call_error_rate")

        assert result["name"] == "tool_call_error_rate"
        assert result["metric_type"] == "error_rate"
        assert result["error_budget"]["is_breached"] is False

    def test_evaluate_unknown_slo_raises(self, tracker):
        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        with pytest.raises(KeyError):
            evaluator.evaluate("nonexistent_slo")


# ---------------------------------------------------------------------------
# 10. SLO breached
# ---------------------------------------------------------------------------


class TestSLOBreached:
    def test_error_rate_slo_breached(self, tracker):
        """Record enough failures to breach the error rate SLO."""
        # error_rate SLO: target_percentage=99.9 → budget_total=0.1%
        # Record 50% failures → way over budget
        for _ in range(50):
            tracker.record_outcome("tool", True)
        for _ in range(50):
            tracker.record_outcome("tool", False)

        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        result = evaluator.evaluate("tool_call_error_rate")

        assert result["error_budget"]["is_breached"] is True

    def test_latency_slo_breached(self, tracker):
        """Record latencies over threshold to breach the latency SLO."""
        # Latency SLO: 99.5% of requests must be under 5s
        # If all requests are 10s, 0% are under 5s → breached
        for _ in range(100):
            tracker.record_latency("tool", 10.0)  # All over 5s
            tracker.record_outcome("tool", True)

        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        result = evaluator.evaluate("tool_call_latency_p99")

        assert result["error_budget"]["is_breached"] is True

    def test_evaluate_all_breached_status(self, tracker):
        """When any SLO is breached, overall_status should be 'breached'."""
        for _ in range(50):
            tracker.record_outcome("tool", True)
        for _ in range(50):
            tracker.record_outcome("tool", False)

        evaluator = SLOEvaluator(DEFAULT_SLOS, tracker)
        result = evaluator.evaluate_all()

        assert result["overall_status"] == "breached"


# ---------------------------------------------------------------------------
# 11. SLI recording in run_show_command
# ---------------------------------------------------------------------------


class TestSLIRecordingInRunShowCommand:
    def test_sli_recorded_on_show_command(self, mock_conn_mgr):
        """Mock conn_mgr.get_node(), call run_show_command(), verify SLI tracker has data."""
        from network_mcp.helpers import run_show_command
        from network_mcp.server import conn_mgr

        # Initialize the SLI tracker
        sli_tracker = init_sli_tracker()

        # Set up the mock to return valid data
        mock_conn_mgr.return_value.run_commands.return_value = [{"status": "ok"}]

        result = run_show_command(conn_mgr, "test-device", ["show version"])
        assert result["status"] == "success"

        # Verify SLI data was recorded
        assert sli_tracker.get_total_requests() == 1
        assert sli_tracker.get_latency_p99() > 0
        assert sli_tracker.get_error_rate() == 0.0


# ---------------------------------------------------------------------------
# 12. Health endpoint includes SLO status
# ---------------------------------------------------------------------------


class TestHealthEndpointSLOStatus:
    def test_health_includes_slo_status_when_enabled(self):
        """Call get_server_health(), verify slo_status key exists when SLO is enabled."""
        from network_mcp.observability import get_server_health

        # Initialize SLI tracker
        sli_tracker = init_sli_tracker()
        sli_tracker.record_outcome("tool", True)
        sli_tracker.record_latency("tool", 0.1)

        # Build mock dependencies
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.pool_stats.return_value = {"active": 0, "pooled": 0, "max": 10}

        mock_cache = MagicMock()
        mock_cache.stats.return_value = {"enabled": True, "entries": 0, "hit_rate": 0.0, "memory_mb": 0.0}

        mock_cb_registry = MagicMock()
        mock_cb_registry.stats.return_value = {"enabled": False, "open_circuits": []}

        mock_metrics = MetricsCollector()

        health = get_server_health(mock_conn_mgr, mock_cache, mock_cb_registry, mock_metrics)

        assert "slo_status" in health
        assert "overall_status" in health["slo_status"]
        assert "slos" in health["slo_status"]

    def test_health_no_slo_status_without_tracker(self):
        """When SLI tracker is not initialized, slo_status should not appear."""
        from network_mcp.observability import get_server_health

        # _reset() in autouse fixture ensures tracker is None
        mock_conn_mgr = MagicMock()
        mock_conn_mgr.pool_stats.return_value = {"active": 0, "pooled": 0, "max": 10}

        mock_cache = MagicMock()
        mock_cache.stats.return_value = {"enabled": True, "entries": 0, "hit_rate": 0.0, "memory_mb": 0.0}

        mock_cb_registry = MagicMock()
        mock_cb_registry.stats.return_value = {"enabled": False, "open_circuits": []}

        mock_metrics = MetricsCollector()

        health = get_server_health(mock_conn_mgr, mock_cache, mock_cb_registry, mock_metrics)

        assert "slo_status" not in health


# ---------------------------------------------------------------------------
# 13. Deep health check
# ---------------------------------------------------------------------------


class TestDeepHealthCheck:
    def test_deep_check_runs_show_version(self):
        """Mock conn_mgr and inventory, verify deep check runs show version and records result."""
        sli_tracker = init_sli_tracker()

        mock_driver = MagicMock()
        mock_driver.run_show.return_value = [{"version": "4.32.1F"}]

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.list_devices.return_value = ["spine-01"]

        @contextmanager
        def mock_acquire(host):
            yield mock_driver

        mock_conn_mgr.acquire = mock_acquire

        result = deep_health_check(mock_conn_mgr)

        assert result["deep_check"] == "pass"
        assert result["device"] == "spine-01"
        assert result["latency_ms"] >= 0
        mock_driver.run_show.assert_called_once_with(["show version"])
        assert sli_tracker.get_availability() == 100.0

    def test_deep_check_failure(self):
        """Verify deep check handles failures and records them."""
        sli_tracker = init_sli_tracker()

        mock_driver = MagicMock()
        mock_driver.run_show.side_effect = ConnectionError("unreachable")

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.list_devices.return_value = ["leaf-01"]

        @contextmanager
        def mock_acquire(host):
            yield mock_driver

        mock_conn_mgr.acquire = mock_acquire

        result = deep_health_check(mock_conn_mgr)

        assert result["deep_check"] == "fail"
        assert result["device"] == "leaf-01"
        assert "error" in result
        assert sli_tracker.get_availability() == 0.0

    def test_deep_check_no_devices(self):
        """When no devices in inventory, deep check should be skipped."""
        init_sli_tracker()

        mock_conn_mgr = MagicMock()
        mock_conn_mgr.list_devices.return_value = []

        result = deep_health_check(mock_conn_mgr)

        assert result["deep_check"] == "skipped"
        assert result["device"] is None


# ---------------------------------------------------------------------------
# 14. Empty tracker — sensible defaults
# ---------------------------------------------------------------------------


class TestEmptyTracker:
    def test_p99_empty(self, tracker):
        assert tracker.get_latency_p99() == 0.0

    def test_p95_empty(self, tracker):
        assert tracker.get_latency_p95() == 0.0

    def test_error_rate_empty(self, tracker):
        assert tracker.get_error_rate() == 0.0

    def test_availability_empty(self, tracker):
        assert tracker.get_availability() == 100.0

    def test_total_requests_empty(self, tracker):
        assert tracker.get_total_requests() == 0


# ---------------------------------------------------------------------------
# Module-level singleton tests
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    def test_get_sli_tracker_returns_none_before_init(self):
        assert get_sli_tracker() is None

    def test_init_creates_tracker(self):
        tracker = init_sli_tracker(window_seconds=1800)
        assert tracker is not None
        assert tracker._window_seconds == 1800

    def test_init_returns_same_instance(self):
        t1 = init_sli_tracker()
        t2 = init_sli_tracker()
        assert t1 is t2

    def test_reset_clears_singleton(self):
        init_sli_tracker()
        assert get_sli_tracker() is not None
        _reset()
        assert get_sli_tracker() is None


# ---------------------------------------------------------------------------
# DEFAULT_SLOS definitions
# ---------------------------------------------------------------------------


class TestDefaultSLOs:
    def test_three_default_slos(self):
        assert len(DEFAULT_SLOS) == 3

    def test_latency_slo(self):
        slo = next(s for s in DEFAULT_SLOS if s.name == "tool_call_latency_p99")
        assert slo.metric_type == SLOMetricType.LATENCY
        assert slo.target_percentage == 99.5
        assert slo.threshold_value == 5.0
        assert slo.window_seconds == 3600

    def test_error_rate_slo(self):
        slo = next(s for s in DEFAULT_SLOS if s.name == "tool_call_error_rate")
        assert slo.metric_type == SLOMetricType.ERROR_RATE
        assert slo.target_percentage == 99.9
        assert slo.threshold_value == 1.0

    def test_availability_slo(self):
        slo = next(s for s in DEFAULT_SLOS if s.name == "health_check_availability")
        assert slo.metric_type == SLOMetricType.AVAILABILITY
        assert slo.target_percentage == 99.99
        assert slo.window_seconds == 86400


# ===========================================================================
# LEGACY API Tests (backward compatibility)
# ===========================================================================

# ---------------------------------------------------------------------------
# Legacy SLO definitions
# ---------------------------------------------------------------------------


class TestLegacySLODefinitions:
    def test_definitions_exist(self):
        assert len(SLO_DEFINITIONS) == 6

    def test_all_definitions_have_names(self):
        for slo in SLO_DEFINITIONS:
            assert slo.name
            assert slo.description
            assert slo.target > 0

    def test_definition_names_are_unique(self):
        names = [slo.name for slo in SLO_DEFINITIONS]
        assert len(names) == len(set(names))

    def test_success_rate_target(self):
        slo = next(s for s in SLO_DEFINITIONS if s.name == "tool_call_success_rate")
        assert slo.target == 0.999

    def test_read_latency_target(self):
        slo = next(s for s in SLO_DEFINITIONS if s.name == "tool_call_latency_p99_read")
        assert slo.target == 5.0

    def test_write_latency_target(self):
        slo = next(s for s in SLO_DEFINITIONS if s.name == "tool_call_latency_p99_write")
        assert slo.target == 10.0

    def test_connection_availability_target(self):
        slo = next(s for s in SLO_DEFINITIONS if s.name == "connection_availability")
        assert slo.target == 0.9995

    def test_cache_hit_ratio_target(self):
        slo = next(s for s in SLO_DEFINITIONS if s.name == "cache_hit_ratio")
        assert slo.target == 0.60

    def test_audit_completeness_target(self):
        slo = next(s for s in SLO_DEFINITIONS if s.name == "audit_completeness")
        assert slo.target == 1.0

    def test_default_window(self):
        for slo in SLO_DEFINITIONS:
            assert slo.window_minutes == 60


# ---------------------------------------------------------------------------
# Legacy SLOResult dataclass
# ---------------------------------------------------------------------------


class TestLegacySLOResult:
    def test_result_fields(self):
        r = SLOResult(name="test", target=0.99, actual=0.995, met=True, message="OK")
        assert r.name == "test"
        assert r.target == 0.99
        assert r.actual == 0.995
        assert r.met is True
        assert r.message == "OK"

    def test_result_default_message(self):
        r = SLOResult(name="test", target=0.99, actual=0.5, met=False)
        assert r.message == ""


# ---------------------------------------------------------------------------
# Legacy check_slos with no data
# ---------------------------------------------------------------------------


class TestLegacyCheckSLOsNoData:
    def test_all_slos_pass_with_no_data(self, collector):
        results = check_slos(collector)
        assert len(results) == 6
        for r in results:
            assert r.met is True, f"SLO {r.name} should pass with no data"

    def test_all_results_have_correct_types(self, collector):
        results = check_slos(collector)
        for r in results:
            assert isinstance(r, SLOResult)
            assert isinstance(r.name, str)
            assert isinstance(r.target, float)
            assert isinstance(r.actual, float)
            assert isinstance(r.met, bool)


# ---------------------------------------------------------------------------
# Legacy success rate SLO
# ---------------------------------------------------------------------------


class TestLegacySuccessRatePassing:
    def test_all_success(self, collector):
        for _ in range(1000):
            collector.record_tool_call("test", "success", 100.0)
        results = check_slos(collector)
        sr = next(r for r in results if r.name == "tool_call_success_rate")
        assert sr.met is True
        assert sr.actual == 1.0

    def test_just_above_threshold(self, collector):
        for _ in range(999):
            collector.record_tool_call("test", "success", 100.0)
        collector.record_tool_call("test", "error", 100.0)
        results = check_slos(collector)
        sr = next(r for r in results if r.name == "tool_call_success_rate")
        assert sr.met is True
        assert sr.actual == 0.999


class TestLegacySuccessRateFailing:
    def test_too_many_errors(self, collector):
        for _ in range(990):
            collector.record_tool_call("test", "success", 100.0)
        for _ in range(10):
            collector.record_tool_call("test", "error", 100.0)
        results = check_slos(collector)
        sr = next(r for r in results if r.name == "tool_call_success_rate")
        assert sr.met is False
        assert sr.actual < 0.999

    def test_all_errors(self, collector):
        for _ in range(10):
            collector.record_tool_call("test", "error", 100.0)
        results = check_slos(collector)
        sr = next(r for r in results if r.name == "tool_call_success_rate")
        assert sr.met is False
        assert sr.actual == 0.0


# ---------------------------------------------------------------------------
# Legacy latency SLO
# ---------------------------------------------------------------------------


class TestLegacyLatencySLO:
    def test_read_latency_passing(self, collector):
        for _ in range(100):
            collector.record_tool_call("run_show_command", "success", 1000.0)
        results = check_slos(collector)
        lr = next(r for r in results if r.name == "tool_call_latency_p99_read")
        assert lr.met is True
        assert lr.actual <= 5.0

    def test_read_latency_failing(self, collector):
        for _ in range(95):
            collector.record_tool_call("run_show_command", "success", 100.0)
        for _ in range(5):
            collector.record_tool_call("run_show_command", "success", 8000.0)
        results = check_slos(collector)
        lr = next(r for r in results if r.name == "tool_call_latency_p99_read")
        assert lr.met is False
        assert lr.actual > 5.0

    def test_write_latency_passing(self, collector):
        for _ in range(100):
            collector.record_tool_call("run_config_command", "success", 3000.0)
        results = check_slos(collector)
        lw = next(r for r in results if r.name == "tool_call_latency_p99_write")
        assert lw.met is True
        assert lw.actual <= 10.0

    def test_write_latency_failing(self, collector):
        for _ in range(95):
            collector.record_tool_call("run_config_command", "success", 100.0)
        for _ in range(5):
            collector.record_tool_call("run_config_command", "success", 15000.0)
        results = check_slos(collector)
        lw = next(r for r in results if r.name == "tool_call_latency_p99_write")
        assert lw.met is False


# ---------------------------------------------------------------------------
# Legacy connection availability SLO
# ---------------------------------------------------------------------------


class TestLegacyConnectionAvailabilitySLO:
    def test_no_pool_configured(self, collector):
        results = check_slos(collector)
        ca = next(r for r in results if r.name == "connection_availability")
        assert ca.met is True

    def test_pool_mostly_available(self, collector):
        collector.set_connection_pool_stats(pool_size=100, active=0)
        results = check_slos(collector)
        ca = next(r for r in results if r.name == "connection_availability")
        assert ca.met is True
        assert ca.actual == 1.0

    def test_pool_exhausted(self, collector):
        collector.set_connection_pool_stats(pool_size=100, active=100)
        results = check_slos(collector)
        ca = next(r for r in results if r.name == "connection_availability")
        assert ca.met is False
        assert ca.actual == 0.0


# ---------------------------------------------------------------------------
# Legacy cache hit ratio SLO
# ---------------------------------------------------------------------------


class TestLegacyCacheHitRatioSLO:
    def test_no_cache_operations(self, collector):
        results = check_slos(collector)
        ch = next(r for r in results if r.name == "cache_hit_ratio")
        assert ch.met is True

    def test_high_hit_ratio(self, collector):
        for _ in range(80):
            collector.record_cache_hit()
        for _ in range(20):
            collector.record_cache_miss()
        results = check_slos(collector)
        ch = next(r for r in results if r.name == "cache_hit_ratio")
        assert ch.met is True
        assert ch.actual == 0.8

    def test_low_hit_ratio(self, collector):
        for _ in range(30):
            collector.record_cache_hit()
        for _ in range(70):
            collector.record_cache_miss()
        results = check_slos(collector)
        ch = next(r for r in results if r.name == "cache_hit_ratio")
        assert ch.met is False
        assert ch.actual == 0.3


# ---------------------------------------------------------------------------
# Legacy audit completeness SLO
# ---------------------------------------------------------------------------


class TestLegacyAuditCompletenessSLO:
    def test_always_met(self, collector):
        results = check_slos(collector)
        ac = next(r for r in results if r.name == "audit_completeness")
        assert ac.met is True
        assert ac.actual == 1.0


# ---------------------------------------------------------------------------
# Legacy get_slo_violations
# ---------------------------------------------------------------------------


class TestLegacyGetSLOViolations:
    def test_no_violations_with_no_data(self, collector):
        violations = get_slo_violations(collector)
        assert len(violations) == 0

    def test_returns_only_violations(self, collector):
        for _ in range(900):
            collector.record_tool_call("test", "success", 100.0)
        for _ in range(100):
            collector.record_tool_call("test", "error", 100.0)
        violations = get_slo_violations(collector)
        assert len(violations) >= 1
        violation_names = [v.name for v in violations]
        assert "tool_call_success_rate" in violation_names
        for v in violations:
            assert v.met is False

    def test_multiple_violations(self, collector):
        for _ in range(900):
            collector.record_tool_call("test", "success", 100.0)
        for _ in range(100):
            collector.record_tool_call("test", "error", 100.0)
        for _ in range(10):
            collector.record_cache_hit()
        for _ in range(90):
            collector.record_cache_miss()
        violations = get_slo_violations(collector)
        violation_names = [v.name for v in violations]
        assert "tool_call_success_rate" in violation_names
        assert "cache_hit_ratio" in violation_names


# ---------------------------------------------------------------------------
# Legacy SLODefinition dataclass
# ---------------------------------------------------------------------------


class TestLegacySLODefinitionDataclass:
    def test_custom_window(self):
        slo = SLODefinition(name="test", description="Test SLO", target=0.99, window_minutes=30)
        assert slo.window_minutes == 30

    def test_custom_unit(self):
        slo = SLODefinition(name="test", description="Test", target=5.0, unit="seconds")
        assert slo.unit == "seconds"

    def test_default_values(self):
        slo = SLODefinition(name="test", description="Test", target=0.99)
        assert slo.window_minutes == 60
        assert slo.unit == ""
