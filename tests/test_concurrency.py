"""Concurrency and stress tests for connection pool, rate limiter, circuit breaker, and metrics."""

import threading

import pytest

pytestmark = pytest.mark.chaos
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

from network_mcp.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, DeviceHealth
from network_mcp.connection import ConnectionManager
from network_mcp.observability import MetricsCollector
from network_mcp.rate_limiter import DeviceRateLimiter, TokenBucket
from tests.conftest import make_test_settings

# ---------------------------------------------------------------------------
# Test 1: ConnectionManager under concurrent load
# ---------------------------------------------------------------------------


class TestConnectionPoolConcurrentAcquire:
    """50+ concurrent acquire() calls should not deadlock or corrupt state."""

    def test_concurrent_acquire_no_deadlock(self):
        """50 concurrent acquire() calls complete without deadlock or exception."""
        settings = make_test_settings(net_demo_mode=True)
        mgr = ConnectionManager(settings, max_connections=100)

        num_workers = 50
        barrier = threading.Barrier(num_workers)
        errors: list[Exception] = []

        def worker(host_id: int) -> str:
            barrier.wait(timeout=10)
            host = f"device-{host_id % 10}"
            with mgr.acquire(host, verify=False) as driver:
                # Simulate a brief operation
                assert driver is not None
            return host

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, i) for i in range(num_workers)]
            for future in as_completed(futures, timeout=30):
                try:
                    future.result()
                except Exception as exc:
                    errors.append(exc)

        assert errors == [], f"Concurrent acquire raised errors: {errors}"

        # Pool should have at most 10 unique entries (device-0 .. device-9)
        stats = mgr.pool_stats()
        assert stats["pooled"] <= 10
        assert stats["total_checkouts"] == num_workers

    def test_concurrent_acquire_pool_size_within_limit(self):
        """Pool size stays within max_connections even under concurrent load.

        Workers use a shared set of hosts (fewer than max_connections) so that
        multiple workers reuse cached connections and the pool never needs to
        evict an in-use connection.
        """
        settings = make_test_settings(net_demo_mode=True)
        max_conn = 8
        mgr = ConnectionManager(settings, max_connections=max_conn)

        num_workers = 30
        barrier = threading.Barrier(num_workers)
        max_pooled_seen = 0
        max_pooled_lock = threading.Lock()

        def worker(host_id: int) -> None:
            nonlocal max_pooled_seen
            barrier.wait(timeout=10)
            # 6 unique hosts — fits within max_conn=8, so no eviction needed
            host = f"host-{host_id % 6}"
            with mgr.acquire(host, verify=False) as driver:
                assert driver is not None
                # Sample pool size while connections are held
                pooled = mgr.pool_stats()["pooled"]
                with max_pooled_lock:
                    if pooled > max_pooled_seen:
                        max_pooled_seen = pooled
                time.sleep(0.01)

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, i) for i in range(num_workers)]
            for future in as_completed(futures, timeout=30):
                future.result()  # raises if worker failed

        # Pool size never exceeded max_connections at any point
        assert max_pooled_seen <= max_conn
        # After all workers done, pool should have exactly 6 entries
        stats = mgr.pool_stats()
        assert stats["pooled"] == 6


# ---------------------------------------------------------------------------
# Test 2: FIFO eviction under pool exhaustion
# ---------------------------------------------------------------------------


class TestFifoEviction:
    """When pool is full, oldest connection should be evicted."""

    def test_fifo_eviction_on_exhaustion(self):
        """6th connection evicts the 1st when pool max is 5."""
        settings = make_test_settings(net_demo_mode=True)
        mgr = ConnectionManager(settings, max_connections=5)

        # Acquire 5 connections (using get_driver to keep them cached without refcount holds)
        hosts = [f"switch-{i}" for i in range(5)]
        drivers = {}
        for host in hosts:
            drivers[host] = mgr.get_driver(host, verify=False)

        assert mgr.pool_stats()["pooled"] == 5

        # Acquire a 6th — should evict the oldest (switch-0) via FIFO
        mgr.get_driver("switch-5", verify=False)

        assert mgr.pool_stats()["pooled"] == 5  # still at max
        # switch-0 should have been evicted
        assert "switch-0" not in mgr._drivers

    def test_evicted_connection_cleanup_called(self):
        """Evicted driver's close() method is invoked."""
        settings = make_test_settings(net_demo_mode=True)
        mgr = ConnectionManager(settings, max_connections=2)

        # Fill pool
        mgr.get_driver("host-a", verify=False)
        mgr.get_driver("host-b", verify=False)

        # Grab reference to oldest driver before eviction
        first_driver = mgr._drivers["host-a"]

        with patch.object(first_driver, "close") as mock_close:
            mgr.get_driver("host-c", verify=False)
            mock_close.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: Rate limiter under concurrent requests
# ---------------------------------------------------------------------------


class TestRateLimiterConcurrent:
    """Rate limiter should enforce limits under concurrent access."""

    def test_token_bucket_thread_safety(self):
        """TokenBucket under heavy concurrent access never goes negative."""
        bucket = TokenBucket(rate=10.0, capacity=10.0)
        acquired_count = 0
        count_lock = threading.Lock()

        num_workers = 50
        barrier = threading.Barrier(num_workers)

        def worker() -> None:
            nonlocal acquired_count
            barrier.wait(timeout=10)
            if bucket.acquire():
                with count_lock:
                    acquired_count += 1

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker) for _ in range(num_workers)]
            for future in as_completed(futures, timeout=30):
                future.result()

        # Capacity is 10, so at most 10 should be acquired in a near-instant burst
        assert 1 <= acquired_count <= 11  # allow +1 for timing slack

    def test_rate_limiter_enforces_throughput(self):
        """Rate limiter at 2 req/sec should take ~4.5s for 10 requests (after initial burst)."""
        bucket = TokenBucket(rate=2.0, capacity=2.0)

        start = time.monotonic()
        completed = 0

        # Drain 10 requests, waiting when rate limited
        for _ in range(10):
            while not bucket.acquire():
                wt = bucket.wait_time()
                time.sleep(max(wt, 0.01))
            completed += 1

        elapsed = time.monotonic() - start

        assert completed == 10
        # 10 requests at 2/sec: first 2 are instant (burst), remaining 8 need 4 seconds
        # Allow generous tolerance for CI jitter
        assert elapsed >= 3.0, f"Rate limiter too fast: {elapsed:.2f}s for 10 requests at 2/sec"

    def test_device_rate_limiter_concurrent(self):
        """DeviceRateLimiter creates per-host buckets safely under concurrent access."""
        limiter = DeviceRateLimiter(show_rate=100.0, show_burst=100.0)

        num_workers = 30
        barrier = threading.Barrier(num_workers)
        results: list[bool] = []
        results_lock = threading.Lock()

        def worker(i: int) -> None:
            barrier.wait(timeout=10)
            host = f"device-{i % 5}"  # 5 unique hosts
            result = limiter.check_show(host)
            with results_lock:
                results.append(result)

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, i) for i in range(num_workers)]
            for future in as_completed(futures, timeout=30):
                future.result()

        # With high burst, all should succeed
        assert len(results) == num_workers
        assert all(results)
        # Should have created exactly 5 buckets
        assert len(limiter._show_buckets) == 5


# ---------------------------------------------------------------------------
# Test 4: Circuit breaker state transitions
# ---------------------------------------------------------------------------


class TestCircuitBreakerConcurrent:
    """Circuit breaker state transitions under concurrent access."""

    def test_closed_to_open_after_threshold(self):
        """Circuit opens after N consecutive failures."""
        cb = CircuitBreaker("test-host", threshold=5, cooldown=60)

        assert cb.state == DeviceHealth.HEALTHY
        assert cb.allow_request() is True

        # Record 4 failures — still healthy
        for _ in range(4):
            cb.record_failure()
        assert cb.state == DeviceHealth.HEALTHY

        # 5th failure trips the breaker
        cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN
        assert cb.allow_request() is False  # fail-fast

    def test_half_open_recovery(self):
        """OPEN -> HALF_OPEN -> CLOSED recovery path."""
        cb = CircuitBreaker("test-host", threshold=3, cooldown=1)

        # Trip the breaker
        for _ in range(3):
            cb.record_failure()
        assert cb.state == DeviceHealth.CIRCUIT_OPEN

        # Wait for cooldown
        time.sleep(1.1)

        # Should transition to HALF_OPEN when checked
        assert cb.get_state() == DeviceHealth.HALF_OPEN
        assert cb.allow_request() is True

        # Success brings it back to HEALTHY
        cb.record_success()
        assert cb.state == DeviceHealth.HEALTHY

    def test_concurrent_failure_recording(self):
        """Concurrent failure recordings should converge to CIRCUIT_OPEN."""
        cb = CircuitBreaker("test-host", threshold=5, cooldown=60)

        num_workers = 20
        barrier = threading.Barrier(num_workers)

        def worker() -> None:
            barrier.wait(timeout=10)
            cb.record_failure()

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker) for _ in range(num_workers)]
            for future in as_completed(futures, timeout=30):
                future.result()

        # 20 failures > threshold of 5 — must be OPEN
        assert cb.state == DeviceHealth.CIRCUIT_OPEN
        assert cb.consecutive_failures == 20

    def test_registry_concurrent_access(self):
        """CircuitBreakerRegistry creates breakers safely under concurrent access."""
        registry = CircuitBreakerRegistry(threshold=3, cooldown=60, enabled=True)

        num_workers = 30
        barrier = threading.Barrier(num_workers)

        def worker(i: int) -> None:
            barrier.wait(timeout=10)
            host = f"device-{i % 5}"
            registry.record_failure(host)

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, i) for i in range(num_workers)]
            for future in as_completed(futures, timeout=30):
                future.result()

        # Each of 5 hosts gets 6 failures (30/5) — all should be OPEN
        stats = registry.stats()
        assert stats["total_breakers"] == 5
        for breaker_info in stats["breakers"].values():
            assert breaker_info["consecutive_failures"] == 6
            assert breaker_info["state"] == "open"


# ---------------------------------------------------------------------------
# Test 5: Thread safety of metrics collector
# ---------------------------------------------------------------------------


class TestMetricsConcurrent:
    """MetricsCollector should handle concurrent record_tool_call() safely."""

    def test_concurrent_metric_recording(self):
        """100 concurrent metric recordings produce correct totals."""
        collector = MetricsCollector()

        num_workers = 100
        barrier = threading.Barrier(num_workers)

        def worker(i: int) -> None:
            barrier.wait(timeout=10)
            tool = f"tool_{i % 5}"
            collector.record_tool_call(tool, "success", duration_ms=10.0)

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, i) for i in range(num_workers)]
            for future in as_completed(futures, timeout=30):
                future.result()

        metrics = collector.get_metrics_json()
        total_calls = sum(sum(statuses.values()) for statuses in metrics["tool_calls_total"].values())
        assert total_calls == num_workers

        # Each of 5 tools should have exactly 20 calls
        for tool_name in [f"tool_{i}" for i in range(5)]:
            assert metrics["tool_calls_total"][tool_name]["success"] == 20

    def test_concurrent_mixed_metric_operations(self):
        """Mixed metric operations (tool calls, cache hits, rate limit events) are consistent."""
        collector = MetricsCollector()

        num_workers = 60
        barrier = threading.Barrier(num_workers)

        def worker(i: int) -> None:
            barrier.wait(timeout=10)
            bucket = i % 3
            if bucket == 0:
                collector.record_tool_call("test_tool", "success", duration_ms=5.0)
            elif bucket == 1:
                collector.record_cache_hit()
            else:
                collector.record_rate_limit_throttle()

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, i) for i in range(num_workers)]
            for future in as_completed(futures, timeout=30):
                future.result()

        metrics = collector.get_metrics_json()
        assert metrics["tool_calls_total"]["test_tool"]["success"] == 20
        assert metrics["cache_hits"] == 20
        assert metrics["rate_limit_throttled"] == 20
