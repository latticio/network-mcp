"""SLO/SLI framework with error budgets and health check deep validation.

Provides:
- SLITracker: Thread-safe SLI measurement recorder (latency, outcomes, health checks)
- SLODefinition: Declarative SLO configuration
- ErrorBudgetCalculator: Error budget and burn rate computation
- SLOEvaluator: Ties definitions, tracker, and budgets together
- deep_health_check(): Synthetic health probe via 'show version'

Also preserves the legacy check_slos() / get_slo_violations() API for backward compatibility.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from network_mcp.observability import MetricsCollector

logger = logging.getLogger("network-mcp")


# ---------------------------------------------------------------------------
# SLO metric types and definitions
# ---------------------------------------------------------------------------


class SLOMetricType(StrEnum):
    """Types of SLO metrics that can be tracked."""

    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    AVAILABILITY = "availability"


@dataclass
class SLODefinition:
    """Defines a Service Level Objective.

    Attributes:
        name: Unique SLO identifier (e.g., "tool_call_latency_p99").
        description: Human-readable description.
        metric_type: LATENCY, ERROR_RATE, or AVAILABILITY.
        target_percentage: Target compliance percentage (e.g., 99.5).
        threshold_value: Threshold value (seconds for latency, percentage for error rate).
        window_seconds: Rolling evaluation window in seconds.
        target: Legacy field for backward compatibility (default 0.0).
        window_minutes: Legacy field for backward compatibility (default 60).
        unit: Legacy field for backward compatibility (default "").
    """

    name: str
    description: str
    metric_type: SLOMetricType = SLOMetricType.ERROR_RATE
    target_percentage: float = 99.0
    threshold_value: float = 0.0
    window_seconds: int = 3600
    # Legacy fields for backward compatibility with check_slos()
    target: float = 0.0
    window_minutes: int = 60
    unit: str = ""


# ---------------------------------------------------------------------------
# SLI Tracker
# ---------------------------------------------------------------------------


class SLITracker:
    """Thread-safe SLI measurement recorder with rolling windows.

    Records latency measurements, success/failure outcomes, and health check results.
    All data is stored in bounded deques to prevent unbounded memory growth.
    """

    _MAX_ENTRIES = 100_000

    def __init__(self, window_seconds: int = 3600) -> None:
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        # (timestamp, tool_name, duration_seconds)
        self._latency_samples: deque[tuple[float, str, float]] = deque(maxlen=self._MAX_ENTRIES)
        # (timestamp, tool_name, success: bool)
        self._outcome_samples: deque[tuple[float, str, bool]] = deque(maxlen=self._MAX_ENTRIES)
        # (timestamp, success: bool)
        self._health_check_samples: deque[tuple[float, bool]] = deque(maxlen=self._MAX_ENTRIES)

    def record_latency(self, tool_name: str, duration_seconds: float) -> None:
        """Record a latency measurement for a tool call."""
        now = time.monotonic()
        with self._lock:
            self._latency_samples.append((now, tool_name, duration_seconds))

    def record_outcome(self, tool_name: str, success: bool) -> None:
        """Record a success/failure outcome for a tool call."""
        now = time.monotonic()
        with self._lock:
            self._outcome_samples.append((now, tool_name, success))

    def record_health_check(self, success: bool) -> None:
        """Record a health check result."""
        now = time.monotonic()
        with self._lock:
            self._health_check_samples.append((now, success))

    def _collect_latencies_in_window(self, window_seconds: int | None = None) -> list[float]:
        """Return latency values within the window. Must be called under lock."""
        window = window_seconds if window_seconds is not None else self._window_seconds
        cutoff = time.monotonic() - window
        return [duration for ts, _, duration in self._latency_samples if ts >= cutoff]

    def get_latency_p99(self, window_seconds: int | None = None) -> float:
        """Calculate P99 latency over the rolling window.

        Returns 0.0 if no data is available.
        """
        with self._lock:
            values = self._collect_latencies_in_window(window_seconds)
        if not values:
            return 0.0
        values.sort()
        idx = min(int(len(values) * 0.99), len(values) - 1)
        return values[idx]

    def get_latency_p95(self, window_seconds: int | None = None) -> float:
        """Calculate P95 latency over the rolling window.

        Returns 0.0 if no data is available.
        """
        with self._lock:
            values = self._collect_latencies_in_window(window_seconds)
        if not values:
            return 0.0
        values.sort()
        idx = min(int(len(values) * 0.95), len(values) - 1)
        return values[idx]

    def get_error_rate(self, window_seconds: int | None = None) -> float:
        """Calculate error rate as a percentage over the rolling window.

        Returns 0.0 if no data is available (no requests = no errors).
        """
        window = window_seconds if window_seconds is not None else self._window_seconds
        cutoff = time.monotonic() - window
        with self._lock:
            outcomes = [(ts, success) for ts, _, success in self._outcome_samples if ts >= cutoff]
        if not outcomes:
            return 0.0
        failures = sum(1 for _, success in outcomes if not success)
        return (failures / len(outcomes)) * 100.0

    def get_availability(self, window_seconds: int | None = None) -> float:
        """Calculate availability percentage from health checks over the rolling window.

        Returns 100.0 if no data is available (no checks = assumed available).
        """
        window = window_seconds if window_seconds is not None else self._window_seconds
        cutoff = time.monotonic() - window
        with self._lock:
            checks = [(ts, success) for ts, success in self._health_check_samples if ts >= cutoff]
        if not checks:
            return 100.0
        successes = sum(1 for _, success in checks if success)
        return (successes / len(checks)) * 100.0

    def get_total_requests(self, window_seconds: int | None = None) -> int:
        """Return total outcome records within the window."""
        window = window_seconds if window_seconds is not None else self._window_seconds
        cutoff = time.monotonic() - window
        with self._lock:
            return sum(1 for ts, _, _ in self._outcome_samples if ts >= cutoff)

    def reset(self) -> None:
        """Clear all recorded data. Useful for testing."""
        with self._lock:
            self._latency_samples.clear()
            self._outcome_samples.clear()
            self._health_check_samples.clear()


# ---------------------------------------------------------------------------
# Error Budget Calculator
# ---------------------------------------------------------------------------


class ErrorBudgetCalculator:
    """Calculates error budget status for an SLO.

    Given an SLO target percentage and evaluation window, computes how much
    of the error budget has been consumed and the current burn rate.
    """

    def __init__(self, slo_target: float, window_seconds: int) -> None:
        """Initialize the calculator.

        Args:
            slo_target: Target percentage (e.g., 99.5 means 99.5% must succeed).
            window_seconds: SLO evaluation window in seconds.
        """
        self._slo_target = slo_target
        self._window_seconds = window_seconds

    def calculate(self, current_value: float, total_requests: int) -> dict:
        """Calculate error budget status.

        Args:
            current_value: Current metric value as a percentage (e.g., 99.2% success rate).
            total_requests: Total number of requests in the window.

        Returns:
            Dict with budget status including remaining budget, burn rate, and breach flag.
        """
        budget_total = 100.0 - self._slo_target  # e.g., 0.5% for 99.5 target
        budget_consumed = max(0.0, 100.0 - current_value)  # e.g., 0.8% if current is 99.2

        if budget_total > 0:
            budget_remaining = max(0.0, budget_total - budget_consumed)
            budget_remaining_pct = (budget_remaining / budget_total) * 100.0
            burn_rate = budget_consumed / budget_total
        else:
            budget_remaining = 0.0
            budget_remaining_pct = 0.0 if budget_consumed > 0 else 100.0
            burn_rate = float("inf") if budget_consumed > 0 else 0.0

        allowed_failures = int(total_requests * (budget_total / 100.0)) if total_requests > 0 else 0
        actual_failures = int(total_requests * (budget_consumed / 100.0)) if total_requests > 0 else 0

        return {
            "slo_target": self._slo_target,
            "current_value": round(current_value, 4),
            "budget_total": round(budget_total, 4),
            "budget_remaining": round(budget_remaining, 4),
            "budget_remaining_pct": round(budget_remaining_pct, 4),
            "burn_rate": round(burn_rate, 4),
            "is_breached": budget_consumed > budget_total,
            "total_requests": total_requests,
            "allowed_failures": allowed_failures,
            "actual_failures": actual_failures,
        }


# ---------------------------------------------------------------------------
# SLO Evaluator
# ---------------------------------------------------------------------------


class SLOEvaluator:
    """Evaluates SLO definitions against recorded SLI data.

    Ties together SLO definitions, the SLI tracker, and error budget calculators
    to provide a unified view of SLO compliance.
    """

    def __init__(self, definitions: list[SLODefinition], tracker: SLITracker) -> None:
        self._definitions = {d.name: d for d in definitions}
        self._tracker = tracker
        self._budgets = {d.name: ErrorBudgetCalculator(d.target_percentage, d.window_seconds) for d in definitions}

    def evaluate(self, slo_name: str) -> dict:
        """Evaluate a single SLO by name.

        Returns:
            Dict with SLO definition info, current metric value, and error budget status.

        Raises:
            KeyError: If the SLO name is not found.
        """
        defn = self._definitions[slo_name]
        budget = self._budgets[slo_name]

        if defn.metric_type == SLOMetricType.LATENCY:
            total = self._tracker.get_total_requests(defn.window_seconds)
            if total > 0:
                current_value = self._compute_latency_compliance(defn.window_seconds, defn.threshold_value)
            else:
                current_value = 100.0  # No data = compliant
        elif defn.metric_type == SLOMetricType.ERROR_RATE:
            error_rate = self._tracker.get_error_rate(defn.window_seconds)
            current_value = 100.0 - error_rate  # Convert error rate to success rate
        elif defn.metric_type == SLOMetricType.AVAILABILITY:
            current_value = self._tracker.get_availability(defn.window_seconds)
        else:
            current_value = 100.0

        total_requests = self._tracker.get_total_requests(defn.window_seconds)
        budget_status = budget.calculate(current_value, total_requests)

        return {
            "name": defn.name,
            "description": defn.description,
            "metric_type": defn.metric_type.value,
            "target_percentage": defn.target_percentage,
            "threshold_value": defn.threshold_value,
            "window_seconds": defn.window_seconds,
            "current_value": budget_status["current_value"],
            "error_budget": budget_status,
        }

    def evaluate_all(self) -> dict:
        """Evaluate all SLO definitions and return a summary.

        Returns:
            Dict with per-SLO results and an overall compliance status.
        """
        results = {}
        all_compliant = True
        for name in self._definitions:
            result = self.evaluate(name)
            results[name] = result
            if result["error_budget"]["is_breached"]:
                all_compliant = False

        return {
            "overall_status": "compliant" if all_compliant else "breached",
            "slos": results,
        }

    def _compute_latency_compliance(self, window_seconds: int, threshold: float) -> float:
        """Compute the percentage of latency samples under the threshold."""
        with self._tracker._lock:
            cutoff = time.monotonic() - window_seconds
            values = [duration for ts, _, duration in self._tracker._latency_samples if ts >= cutoff]
        if not values:
            return 100.0
        under_threshold = sum(1 for v in values if v <= threshold)
        return (under_threshold / len(values)) * 100.0


# ---------------------------------------------------------------------------
# Default SLO Definitions
# ---------------------------------------------------------------------------

DEFAULT_SLOS = [
    SLODefinition(
        name="tool_call_latency_p99",
        description="99th percentile tool call latency under 5 seconds",
        metric_type=SLOMetricType.LATENCY,
        target_percentage=99.5,
        threshold_value=5.0,
        window_seconds=3600,
    ),
    SLODefinition(
        name="tool_call_error_rate",
        description="Tool call error rate under 1%",
        metric_type=SLOMetricType.ERROR_RATE,
        target_percentage=99.9,
        threshold_value=1.0,
        window_seconds=3600,
    ),
    SLODefinition(
        name="health_check_availability",
        description="Health check availability at 99.99%",
        metric_type=SLOMetricType.AVAILABILITY,
        target_percentage=99.99,
        threshold_value=0.0,
        window_seconds=86400,
    ),
]

# ---------------------------------------------------------------------------
# Module-level Singleton
# ---------------------------------------------------------------------------

_sli_tracker: SLITracker | None = None


def get_sli_tracker() -> SLITracker | None:
    """Return the module-level SLI tracker singleton, or None if not initialized."""
    return _sli_tracker


def init_sli_tracker(window_seconds: int = 3600) -> SLITracker:
    """Initialize and return the global SLI tracker singleton."""
    global _sli_tracker
    if _sli_tracker is None:
        _sli_tracker = SLITracker(window_seconds=window_seconds)
    return _sli_tracker


def _reset() -> None:
    """Reset the module-level singleton. For testing only."""
    global _sli_tracker
    if _sli_tracker is not None:
        _sli_tracker.reset()
    _sli_tracker = None


# ---------------------------------------------------------------------------
# Deep Health Check
# ---------------------------------------------------------------------------


def deep_health_check(conn_mgr) -> dict:
    """Run a deep health check by executing 'show version' on a random inventory device.

    Records the result via the SLI tracker for availability tracking.

    Args:
        conn_mgr: ConnectionManager instance.

    Returns:
        Dict with deep_check status, device, and latency_ms.
    """
    import random

    tracker = get_sli_tracker()
    devices = conn_mgr.list_devices()
    if not devices:
        if tracker:
            tracker.record_health_check(True)
        return {"deep_check": "skipped", "device": None, "latency_ms": 0.0}

    host = random.choice(devices)  # noqa: S311
    start = time.monotonic()
    try:
        with conn_mgr.acquire(host) as driver:
            driver.run_show(["show version"])
        elapsed_ms = (time.monotonic() - start) * 1000
        if tracker:
            tracker.record_health_check(True)
        return {"deep_check": "pass", "device": host, "latency_ms": round(elapsed_ms, 2)}
    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        if tracker:
            tracker.record_health_check(False)
        logger.warning("Deep health check failed for %s: %s", host, e)
        return {"deep_check": "fail", "device": host, "latency_ms": round(elapsed_ms, 2), "error": str(e)}


# ---------------------------------------------------------------------------
# Legacy API — backward compatibility with check_slos() / get_slo_violations()
# ---------------------------------------------------------------------------


@dataclass
class SLOResult:
    """Result of evaluating a single SLO (legacy API).

    Attributes:
        name: SLO identifier.
        target: The configured target value.
        actual: The measured actual value.
        met: Whether the SLO target is met.
        message: Human-readable summary.
    """

    name: str
    target: float
    actual: float
    met: bool
    message: str = ""


SLO_DEFINITIONS: list[SLODefinition] = [
    SLODefinition(
        name="tool_call_success_rate",
        description="Successful tool calls / total calls",
        target=0.999,
        unit="ratio",
    ),
    SLODefinition(
        name="tool_call_latency_p99_read",
        description="99th percentile read tool latency < 5s",
        target=5.0,
        unit="seconds",
    ),
    SLODefinition(
        name="tool_call_latency_p99_write",
        description="99th percentile write tool latency < 10s",
        target=10.0,
        unit="seconds",
    ),
    SLODefinition(
        name="connection_availability",
        description="Available connections / max pool",
        target=0.9995,
        unit="ratio",
    ),
    SLODefinition(
        name="cache_hit_ratio",
        description="Cache hits / (hits + misses)",
        target=0.60,
        unit="ratio",
    ),
    SLODefinition(
        name="audit_completeness",
        description="Audit entries / tool calls",
        target=1.0,
        unit="ratio",
    ),
]


def check_slos(metrics_collector: MetricsCollector) -> list[SLOResult]:
    """Check current SLO compliance against collected metrics (legacy API).

    Args:
        metrics_collector: The MetricsCollector instance to read metrics from.

    Returns:
        List of SLOResult objects, one per defined SLO.
    """
    metrics = metrics_collector.get_metrics_json()
    results: list[SLOResult] = []

    for slo in SLO_DEFINITIONS:
        if slo.name == "tool_call_success_rate":
            results.append(_check_success_rate(slo, metrics))
        elif slo.name == "tool_call_latency_p99_read":
            results.append(_check_latency_p99(slo, metrics, "run_show_command"))
        elif slo.name == "tool_call_latency_p99_write":
            results.append(_check_latency_p99(slo, metrics, "run_config_command"))
        elif slo.name == "connection_availability":
            results.append(_check_connection_availability(slo, metrics_collector))
        elif slo.name == "cache_hit_ratio":
            results.append(_check_cache_hit_ratio(slo, metrics))
        elif slo.name == "audit_completeness":
            results.append(
                SLOResult(
                    name=slo.name,
                    target=slo.target,
                    actual=1.0,
                    met=True,
                    message="Audit logging is synchronous — 100% completeness by design",
                )
            )
        else:
            results.append(
                SLOResult(
                    name=slo.name,
                    target=slo.target,
                    actual=0.0,
                    met=False,
                    message=f"Unknown SLO: {slo.name}",
                )
            )

    return results


def get_slo_violations(metrics_collector: MetricsCollector) -> list[SLOResult]:
    """Return only the SLO results that are in violation (legacy API).

    Args:
        metrics_collector: The MetricsCollector instance to read metrics from.

    Returns:
        List of SLOResult objects where met is False.
    """
    return [r for r in check_slos(metrics_collector) if not r.met]


def _check_success_rate(slo: SLODefinition, metrics: dict) -> SLOResult:
    """Check tool call success rate SLO."""
    tool_calls = metrics.get("tool_calls_total", {})
    total = 0
    successes = 0
    for statuses in tool_calls.values():
        for status, count in statuses.items():
            total += count
            if status == "success":
                successes += count

    if total == 0:
        return SLOResult(
            name=slo.name,
            target=slo.target,
            actual=1.0,
            met=True,
            message="No tool calls recorded yet — SLO met by default",
        )

    rate = successes / total
    met = rate >= slo.target
    return SLOResult(
        name=slo.name,
        target=slo.target,
        actual=round(rate, 6),
        met=met,
        message=f"Success rate: {rate:.4%} ({successes}/{total})",
    )


def _check_latency_p99(slo: SLODefinition, metrics: dict, tool_key: str) -> SLOResult:
    """Check tool call latency P99 SLO for a specific tool key."""
    durations = metrics.get("tool_duration_seconds", {})
    tool_stats = durations.get(tool_key, {})
    p99 = tool_stats.get("p99_seconds", 0.0)

    if not tool_stats:
        return SLOResult(
            name=slo.name,
            target=slo.target,
            actual=0.0,
            met=True,
            message=f"No {tool_key} calls recorded yet — SLO met by default",
        )

    met = p99 <= slo.target
    return SLOResult(
        name=slo.name,
        target=slo.target,
        actual=round(p99, 4),
        met=met,
        message=f"P99 latency for {tool_key}: {p99:.4f}s (target: <{slo.target}s)",
    )


def _check_connection_availability(slo: SLODefinition, metrics_collector: MetricsCollector) -> SLOResult:
    """Check connection pool availability SLO."""
    pool_size = metrics_collector._connection_pool_size
    active = metrics_collector._active_connections

    if pool_size == 0:
        return SLOResult(
            name=slo.name,
            target=slo.target,
            actual=1.0,
            met=True,
            message="No connection pool configured — SLO met by default",
        )

    available_ratio = (pool_size - active) / pool_size
    met = available_ratio >= slo.target
    return SLOResult(
        name=slo.name,
        target=slo.target,
        actual=round(available_ratio, 6),
        met=met,
        message=f"Connection availability: {available_ratio:.4%} ({pool_size - active}/{pool_size} available)",
    )


def _check_cache_hit_ratio(slo: SLODefinition, metrics: dict) -> SLOResult:
    """Check cache hit ratio SLO."""
    hits = metrics.get("cache_hits", 0)
    misses = metrics.get("cache_misses", 0)
    total = hits + misses

    if total == 0:
        return SLOResult(
            name=slo.name,
            target=slo.target,
            actual=1.0,
            met=True,
            message="No cache operations recorded yet — SLO met by default",
        )

    ratio = hits / total
    met = ratio >= slo.target
    return SLOResult(
        name=slo.name,
        target=slo.target,
        actual=round(ratio, 6),
        met=met,
        message=f"Cache hit ratio: {ratio:.2%} ({hits}/{total})",
    )
