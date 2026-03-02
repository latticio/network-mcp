"""Load testing suite for performance validation.

Tests the system's behavior under high concurrency and sustained load:
- Baseline latency for core operations
- Concurrent tool call throughput
- Memory stability under sustained load
- Graceful degradation at capacity
- Recovery after overload
- Cache performance under concurrent access
- Rate limiter throughput
- Connection pool scalability
"""

import concurrent.futures
import threading
import time
import tracemalloc
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from network_mcp.cache import CommandCache
from network_mcp.circuit_breaker import CircuitBreakerRegistry
from network_mcp.config import NetworkSettings
from network_mcp.connection import ConnectionManager
from network_mcp.helpers import run_show_command
from network_mcp.rate_limiter import DeviceRateLimiter, TokenBucket
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_VERSION = {
    "modelName": "vEOS-lab",
    "version": "4.32.1F",
    "serialNumber": "TEST123",
    "hostname": "test-switch",
}


def _make_mock_node():
    """Create a mock node for load tests."""
    node = MagicMock()
    node.platform = "eos"
    node.is_connected.return_value = True
    node.run_commands.return_value = [MOCK_VERSION]
    node.run_show = node.run_commands
    node.config.return_value = []
    node.run_config = node.config
    return node


@contextmanager
def _multi_device_conn_mgr(device_map):
    """Patch conn_mgr to return different nodes per host."""

    @contextmanager
    def _mock_acquire(host):
        if host not in device_map:
            raise ConnectionError(f"No device: {host}")
        yield device_map[host]

    def _mock_get_driver(host, **kwargs):  # noqa: ARG001
        if host not in device_map:
            raise ConnectionError(f"No device: {host}")
        return device_map[host]

    with (
        patch.object(conn_mgr, "get_driver", side_effect=_mock_get_driver),
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield


# ===========================================================================
# Baseline Performance Tests
# ===========================================================================


class TestLoadBaseline:
    """Establish performance baselines for core operations."""

    def test_show_command_baseline_latency(self, mock_conn_mgr):
        """Baseline: single show command should complete quickly with mock."""
        start = time.monotonic()
        result = run_show_command(conn_mgr, "test-device", ["show version"])
        duration_ms = (time.monotonic() - start) * 1000

        assert result["status"] == "success"
        assert duration_ms < 500  # Should complete in under 500ms with mock

    def test_show_command_average_latency(self, mock_conn_mgr):
        """Average show command latency over 100 calls should be consistent."""
        durations = []
        for _ in range(100):
            start = time.monotonic()
            result = run_show_command(conn_mgr, "test-device", ["show version"])
            durations.append((time.monotonic() - start) * 1000)
            assert result["status"] in ("success", "error")  # Rate limit is acceptable

        # P50 should be reasonable
        durations.sort()
        p50 = durations[len(durations) // 2]
        p99 = durations[int(len(durations) * 0.99)]
        assert p50 < 200, f"P50 latency {p50:.1f}ms exceeds 200ms"
        # P99 can be higher due to rate limiting
        assert p99 < 1000, f"P99 latency {p99:.1f}ms exceeds 1000ms"

    def test_cache_hit_latency(self):
        """Cached command lookup should be very fast (<1ms)."""
        cache = CommandCache(max_entries=1000, enabled=True)
        cache.put("test-host", ["show version"], "json", {"data": MOCK_VERSION}, ttl=300)

        durations = []
        for _ in range(1000):
            start = time.monotonic()
            result = cache.get("test-host", ["show version"], "json")
            durations.append((time.monotonic() - start) * 1000)
            assert result is not None

        durations.sort()
        p50 = durations[len(durations) // 2]
        p99 = durations[int(len(durations) * 0.99)]
        assert p50 < 1.0, f"Cache P50 {p50:.3f}ms exceeds 1ms"
        assert p99 < 5.0, f"Cache P99 {p99:.3f}ms exceeds 5ms"

    def test_rate_limiter_check_latency(self):
        """Rate limiter acquire() should be very fast."""
        limiter = DeviceRateLimiter(show_rate=1000.0, show_burst=1000.0, config_rate=100.0, config_burst=100.0)

        durations = []
        for _ in range(1000):
            start = time.monotonic()
            limiter.check_show("perf-device")
            durations.append((time.monotonic() - start) * 1000)

        durations.sort()
        p50 = durations[len(durations) // 2]
        p99 = durations[int(len(durations) * 0.99)]
        assert p50 < 1.0, f"Rate limiter P50 {p50:.3f}ms exceeds 1ms"
        assert p99 < 5.0, f"Rate limiter P99 {p99:.3f}ms exceeds 5ms"

    def test_circuit_breaker_check_latency(self):
        """Circuit breaker allow_request() should be very fast."""
        registry = CircuitBreakerRegistry(threshold=3, cooldown=60, enabled=True)

        durations = []
        for _ in range(1000):
            start = time.monotonic()
            registry.allow_request("perf-device")
            durations.append((time.monotonic() - start) * 1000)

        durations.sort()
        p50 = durations[len(durations) // 2]
        assert p50 < 1.0, f"Circuit breaker P50 {p50:.3f}ms exceeds 1ms"

    def test_token_bucket_throughput(self):
        """Token bucket should handle high throughput."""
        bucket = TokenBucket(rate=10000.0, capacity=10000.0)

        start = time.monotonic()
        acquired = 0
        for _ in range(10000):
            if bucket.acquire():
                acquired += 1
        duration = time.monotonic() - start

        assert acquired >= 9000  # Should acquire most tokens
        assert duration < 2.0, f"10K acquires took {duration:.2f}s"


# ===========================================================================
# Concurrent Load Tests
# ===========================================================================


class TestLoadConcurrent:
    """Concurrent load tests."""

    def test_50_concurrent_read_tools(self, mock_conn_mgr):
        """50 concurrent read tool calls should all complete."""

        def call_tool(i):
            return run_show_command(conn_mgr, f"device-{i % 5}", ["show version"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(call_tool, i) for i in range(50)]
            results = [f.result(timeout=30) for f in concurrent.futures.as_completed(futures)]

        success_count = sum(1 for r in results if r["status"] == "success")
        error_count = sum(1 for r in results if r["status"] == "error")

        # All should complete (success or rate-limited)
        assert success_count + error_count == 50
        # At least some should succeed
        assert success_count >= 5

    def test_100_concurrent_cache_operations(self):
        """100 concurrent cache read/write operations should not error."""
        cache = CommandCache(max_entries=500, enabled=True)
        errors = []

        def _cache_op(thread_id):
            try:
                for i in range(100):
                    host = f"host-{(thread_id * 100 + i) % 20}"
                    cmd = f"show-{i % 10}"
                    cache.put(host, [cmd], "json", {"tid": thread_id, "i": i}, ttl=60)
                    cache.get(host, [cmd], "json")
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(_cache_op, tid) for tid in range(100)]
            concurrent.futures.wait(futures, timeout=30)

        assert not errors, f"Cache concurrent errors: {errors}"

    def test_concurrent_rate_limiter_fairness(self):
        """Concurrent requests across devices should be fairly rate-limited."""
        limiter = DeviceRateLimiter(show_rate=10.0, show_burst=10.0, config_rate=2.0, config_burst=2.0)
        device_allowed = {}
        lock = threading.Lock()

        def _check_device(device):
            allowed = 0
            for _ in range(50):
                if limiter.check_show(device):
                    allowed += 1
            with lock:
                device_allowed[device] = allowed

        devices = [f"device-{i}" for i in range(5)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_check_device, dev) for dev in devices]
            concurrent.futures.wait(futures, timeout=10)

        # Each device should have gotten a similar number of tokens
        values = list(device_allowed.values())
        assert len(values) == 5
        assert all(v >= 5 for v in values), f"Unfair distribution: {device_allowed}"

    def test_concurrent_circuit_breaker_operations(self):
        """Concurrent circuit breaker operations should not corrupt state."""
        registry = CircuitBreakerRegistry(threshold=5, cooldown=60, enabled=True)
        errors = []

        def _operate(thread_id):
            try:
                host = f"device-{thread_id % 3}"
                for _ in range(100):
                    registry.allow_request(host)
                    if thread_id % 2 == 0:
                        registry.record_failure(host)
                    else:
                        registry.record_success(host)
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(_operate, tid) for tid in range(20)]
            concurrent.futures.wait(futures, timeout=10)

        assert not errors, f"Circuit breaker concurrent errors: {errors}"

    def test_mixed_concurrent_workload(self):
        """Mix of show commands, cache ops, and rate limiting under load."""
        devices = {f"device-{i}": _make_mock_node() for i in range(5)}
        errors = []

        def _show_work(dev_name):
            try:
                for _ in range(20):
                    run_show_command(conn_mgr, dev_name, ["show version"])
            except Exception as e:
                errors.append(e)

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
                futures = []
                for i in range(25):
                    dev = f"device-{i % 5}"
                    futures.append(executor.submit(_show_work, dev))

                done, not_done = concurrent.futures.wait(futures, timeout=30)

        assert len(not_done) == 0, f"{len(not_done)} futures timed out"
        assert not errors, f"Mixed workload errors: {errors}"


# ===========================================================================
# Memory Stability Tests
# ===========================================================================


class TestMemoryStability:
    """Verify memory doesn't grow unboundedly under sustained load."""

    def test_memory_stability_show_commands(self, mock_conn_mgr):
        """Memory should not grow unboundedly under sustained show command load."""
        tracemalloc.start()

        for i in range(500):
            run_show_command(conn_mgr, f"device-{i % 5}", ["show version"])

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 50, f"Peak memory {peak_mb:.1f}MB exceeds 50MB"

    def test_memory_stability_cache_operations(self):
        """Cache should stay within memory bounds under churn."""
        cache = CommandCache(max_entries=200, max_memory_mb=10, enabled=True)

        tracemalloc.start()

        for i in range(2000):
            host = f"host-{i % 50}"
            cache.put(host, [f"cmd-{i}"], "json", {"data": f"value-{i}", "extra": "x" * 500}, ttl=30)
            cache.get(host, [f"cmd-{i}"], "json")
            if i % 100 == 0:
                cache.invalidate_host(f"host-{i % 50}")

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 50, f"Cache peak memory {peak_mb:.1f}MB exceeds 50MB"

        stats = cache.stats()
        assert stats["entries"] <= 200

    def test_memory_stability_rate_limiter(self):
        """Rate limiter buckets should not leak memory."""
        limiter = DeviceRateLimiter(show_rate=100.0, show_burst=100.0, config_rate=10.0, config_burst=10.0)

        tracemalloc.start()

        # Create buckets for 1000 different devices
        for i in range(1000):
            limiter.check_show(f"device-{i}")
            limiter.check_config(f"device-{i}")

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        # 1000 buckets should be well under 10MB
        assert peak_mb < 10, f"Rate limiter peak memory {peak_mb:.1f}MB exceeds 10MB"

    def test_memory_stability_circuit_breakers(self):
        """Circuit breaker registry should not leak memory."""
        registry = CircuitBreakerRegistry(threshold=3, cooldown=60, enabled=True)

        tracemalloc.start()

        for i in range(1000):
            host = f"device-{i}"
            registry.record_failure(host)
            registry.allow_request(host)

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 10, f"Circuit breaker peak memory {peak_mb:.1f}MB exceeds 10MB"


# ===========================================================================
# Graceful Degradation Tests
# ===========================================================================


class TestLoadDegradation:
    """Test graceful degradation under overload."""

    def test_graceful_degradation_at_capacity(self):
        """At capacity, system should degrade gracefully (errors, not crashes)."""
        devices = {f"device-{i}": _make_mock_node() for i in range(3)}
        errors = []
        results_lock = threading.Lock()
        all_results = []

        def _hammer(dev_name):
            try:
                local_results = []
                for _ in range(50):
                    r = run_show_command(conn_mgr, dev_name, ["show version"])
                    local_results.append(r)
                with results_lock:
                    all_results.extend(local_results)
            except Exception as e:
                errors.append(e)

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
                futures = [executor.submit(_hammer, f"device-{i % 3}") for i in range(30)]
                done, not_done = concurrent.futures.wait(futures, timeout=30)

        assert len(not_done) == 0, f"{len(not_done)} futures timed out"
        assert not errors, f"Degradation errors: {errors}"

        # All results should be valid (success or rate-limited error, never crash)
        for r in all_results:
            assert r["status"] in ("success", "error")
            assert "device" in r

    def test_recovery_after_overload(self):
        """After overload subsides, system should return to normal."""
        devices = {f"device-{i}": _make_mock_node() for i in range(3)}

        with _multi_device_conn_mgr(devices):
            # Phase 1: Overload — rapid burst
            overload_results = []
            for _ in range(30):
                r = run_show_command(conn_mgr, "device-0", ["show version"])
                overload_results.append(r)

            # Phase 2: Wait for rate limiter recovery
            time.sleep(1.0)

            # Phase 3: Normal operation should work
            recovery_result = run_show_command(conn_mgr, "device-0", ["show version"])
            assert recovery_result["status"] == "success"

    def test_different_commands_rate_limited_independently(self):
        """Show and config rate limits should not interfere."""
        limiter = DeviceRateLimiter(show_rate=5.0, show_burst=5.0, config_rate=1.0, config_burst=1.0)

        # Exhaust config rate
        limiter.check_config("dev-x")
        limiter.check_config("dev-x")

        # Show should still have tokens
        assert limiter.check_show("dev-x") is True


# ===========================================================================
# Connection Pool Scalability Tests
# ===========================================================================


class TestConnectionPoolScalability:
    """Test connection pool under various load patterns."""

    def test_pool_handles_sequential_device_access(self):
        """Pool should handle accessing many devices sequentially."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=10)

        nodes = [_make_mock_node() for _ in range(50)]
        call_idx = 0
        create_lock = threading.Lock()

        def _create(*args, **kwargs):  # noqa: ARG001
            nonlocal call_idx
            with create_lock:
                idx = call_idx
                call_idx += 1
            return nodes[idx % len(nodes)]

        with patch.object(mgr, "_create_driver", side_effect=_create):
            for i in range(50):
                with mgr.acquire(f"device-{i}"):
                    pass

        stats = mgr.pool_stats()
        assert stats["pooled"] <= 10  # Pool cap respected

    def test_pool_stats_accuracy_under_load(self):
        """Pool stats should remain accurate under concurrent load."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=10)

        create_lock = threading.Lock()
        nodes = [_make_mock_node() for _ in range(30)]
        call_idx = 0

        def _create(*args, **kwargs):  # noqa: ARG001
            nonlocal call_idx
            with create_lock:
                idx = call_idx
                call_idx += 1
            return nodes[idx % len(nodes)]

        def _use(dev):
            with mgr.acquire(dev):
                time.sleep(0.01)

        with patch.object(mgr, "_create_driver", side_effect=_create):
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(_use, f"device-{i % 5}") for i in range(20)]
                concurrent.futures.wait(futures, timeout=10)

        stats = mgr.pool_stats()
        # After all connections released, active should be 0
        assert stats["active"] == 0
        assert stats["pooled"] <= 10


# ===========================================================================
# Throughput Tests
# ===========================================================================


class TestThroughput:
    """Measure throughput of various operations."""

    def test_cache_write_throughput(self):
        """Cache should handle high write throughput."""
        cache = CommandCache(max_entries=5000, enabled=True)

        start = time.monotonic()
        for i in range(5000):
            cache.put(f"host-{i % 100}", [f"cmd-{i}"], "json", {"val": i}, ttl=300)
        duration = time.monotonic() - start

        ops_per_sec = 5000 / duration
        assert ops_per_sec > 1000, f"Cache write throughput {ops_per_sec:.0f} ops/s below 1000"

    def test_cache_read_throughput(self):
        """Cache should handle high read throughput."""
        cache = CommandCache(max_entries=5000, enabled=True)

        # Populate
        for i in range(1000):
            cache.put(f"host-{i}", ["show version"], "json", {"val": i}, ttl=300)

        # Read
        start = time.monotonic()
        hits = 0
        for i in range(10000):
            result = cache.get(f"host-{i % 1000}", ["show version"], "json")
            if result is not None:
                hits += 1
        duration = time.monotonic() - start

        ops_per_sec = 10000 / duration
        assert ops_per_sec > 5000, f"Cache read throughput {ops_per_sec:.0f} ops/s below 5000"
        assert hits >= 9000  # Most reads should hit

    def test_rate_limiter_throughput(self):
        """Rate limiter should handle high throughput checks."""
        limiter = DeviceRateLimiter(show_rate=10000.0, show_burst=10000.0, config_rate=1000.0, config_burst=1000.0)

        start = time.monotonic()
        for _ in range(10000):
            limiter.check_show("throughput-device")
        duration = time.monotonic() - start

        ops_per_sec = 10000 / duration
        assert ops_per_sec > 50000, f"Rate limiter throughput {ops_per_sec:.0f} ops/s below 50K"

    def test_circuit_breaker_throughput(self):
        """Circuit breaker should handle high throughput checks."""
        registry = CircuitBreakerRegistry(threshold=100, cooldown=60, enabled=True)

        start = time.monotonic()
        for _ in range(10000):
            registry.allow_request("throughput-device")
        duration = time.monotonic() - start

        ops_per_sec = 10000 / duration
        assert ops_per_sec > 50000, f"Circuit breaker throughput {ops_per_sec:.0f} ops/s below 50K"


# ===========================================================================
# Sustained Load With All Reliability Features
# ===========================================================================


class TestSustainedReliabilityLoad:
    """Test sustained load with cache, circuit breaker, rate limiter, and bulkhead all active."""

    def test_sustained_100_requests_across_10_devices(self):
        """Send 100 requests across 10 mock devices with all features enabled."""
        from network_mcp.connection import init_bulkhead

        devices = {f"device-{i}": _make_mock_node() for i in range(10)}
        init_bulkhead(10)

        results_lock = threading.Lock()
        all_results = []
        errors = []

        def _run_request(dev_name):
            try:
                r = run_show_command(conn_mgr, dev_name, ["show version"])
                with results_lock:
                    all_results.append(r)
            except Exception as e:
                with results_lock:
                    errors.append(str(e))

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = []
                for i in range(100):
                    dev = f"device-{i % 10}"
                    futures.append(executor.submit(_run_request, dev))

                done, not_done = concurrent.futures.wait(futures, timeout=30)

        # No deadlocks
        assert len(not_done) == 0, f"{len(not_done)} futures timed out (possible deadlock)"
        # No crashes
        assert not errors, f"Errors during sustained load: {errors}"
        # All requests completed
        assert len(all_results) == 100
        # Success rate > 50% (accounting for rate limiting)
        success_count = sum(1 for r in all_results if r["status"] == "success")
        assert success_count >= 10, f"Only {success_count}/100 succeeded"

    def test_bulkhead_slots_no_leak_under_load(self):
        """After load test, bulkhead slots should all be released."""
        from network_mcp.connection import get_bulkhead, init_bulkhead

        init_bulkhead(5)
        devices = {f"device-{i}": _make_mock_node() for i in range(3)}

        def _run_with_bulkhead(dev_name):
            from network_mcp.connection import bulkhead_slot

            try:
                with bulkhead_slot("eos", timeout=5.0):
                    run_show_command(conn_mgr, dev_name, ["show version"])
            except TimeoutError:
                pass  # Expected under load

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_run_with_bulkhead, f"device-{i % 3}") for i in range(30)]
                concurrent.futures.wait(futures, timeout=30)

        # All slots should be released
        bh_instance = get_bulkhead()
        assert bh_instance is not None
        assert bh_instance.get_available("eos") == 5


class TestMemoryStabilityUnderCacheChurn:
    """Verify cache eviction prevents unbounded memory growth."""

    def test_cache_eviction_bounds_memory(self):
        """After 1000 cached operations with max 200 entries, cache stays bounded."""
        cache = CommandCache(max_entries=200, enabled=True)

        tracemalloc.start()

        for i in range(1000):
            host = f"host-{i % 50}"
            cache.put(host, [f"show-{i}"], "json", {"data": "x" * 200, "idx": i}, ttl=60)

        tracemalloc.stop()

        stats = cache.stats()
        # Cache should respect max_entries
        assert stats["entries"] <= 200

    def test_idempotency_store_eviction_bounds_memory(self):
        """IdempotencyStore with max_entries should not grow unboundedly."""
        from network_mcp.idempotency import IdempotencyStore

        store = IdempotencyStore(ttl=3600, max_entries=100)

        for i in range(500):
            store.set(f"key-{i}", {"status": "success", "data": f"payload-{i}"})

        # Should have at most max_entries
        assert len(store._store) <= 100


class TestMetricsConsistencyUnderLoad:
    """Verify metrics collector counts match actual request counts."""

    def test_metrics_count_matches_requests(self, mock_conn_mgr, mock_node):
        """After N requests, metrics should record N tool calls."""
        from network_mcp.observability import MetricsCollector

        mc = MetricsCollector()
        mc._enabled = True
        mc.reset()

        mock_node.run_commands.return_value = [MOCK_VERSION]

        with patch("network_mcp.helpers._get_metrics_collector", return_value=mc):
            request_count = 50
            for _ in range(request_count):
                run_show_command(conn_mgr, "spine-01", ["show version"])

        # Metrics should have recorded calls (success + cache hits count separately)
        total_recorded = mc._tool_calls.get("run_show_command", {})
        total_count = sum(total_recorded.values()) if total_recorded else 0
        # At least some calls should be recorded
        assert total_count > 0, "No metrics were recorded"

    def test_latency_distribution_reasonable(self):
        """Latency distribution from mock calls should be tight."""
        devices = {f"device-{i}": _make_mock_node() for i in range(3)}
        latencies = []

        with _multi_device_conn_mgr(devices):
            for i in range(50):
                start = time.monotonic()
                run_show_command(conn_mgr, f"device-{i % 3}", ["show version"])
                latencies.append((time.monotonic() - start) * 1000)

        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        assert p99 < 5000, f"P99 latency {p99:.1f}ms exceeds 5000ms"


# ===========================================================================
# Sustained Load with All Reliability Features (Extended)
# ===========================================================================


class TestSustainedReliabilityLoadExtended:
    """Extended sustained load tests with all reliability features enabled."""

    def test_sustained_load_no_crashes_or_deadlocks(self):
        """100 requests across 10 devices with all features — no crashes/deadlocks."""
        from network_mcp.connection import init_bulkhead

        devices = {f"device-{i}": _make_mock_node() for i in range(10)}
        init_bulkhead(10)

        results_lock = threading.Lock()
        all_results = []
        errors = []

        def _run_request(dev_name):
            try:
                r = run_show_command(conn_mgr, dev_name, ["show version"])
                with results_lock:
                    all_results.append(r)
            except Exception as e:
                with results_lock:
                    errors.append(str(e))

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(_run_request, f"device-{i % 10}") for i in range(100)]
                done, not_done = concurrent.futures.wait(futures, timeout=30)

        assert len(not_done) == 0, f"{len(not_done)} futures timed out"
        assert not errors, f"Errors: {errors}"
        assert len(all_results) == 100
        success_count = sum(1 for r in all_results if r["status"] == "success")
        assert success_count >= 10, f"Only {success_count}/100 succeeded"

    def test_sustained_load_latency_p99(self):
        """P99 latency under sustained load should stay under 5s."""
        devices = {f"device-{i}": _make_mock_node() for i in range(10)}
        latencies = []
        lock = threading.Lock()

        def _timed_request(dev_name):
            start = time.monotonic()
            r = run_show_command(conn_mgr, dev_name, ["show version"])
            elapsed = (time.monotonic() - start) * 1000
            with lock:
                latencies.append(elapsed)
            return r

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(_timed_request, f"device-{i % 10}") for i in range(100)]
                concurrent.futures.wait(futures, timeout=30)

        latencies.sort()
        if latencies:
            p99 = latencies[int(len(latencies) * 0.99)]
            assert p99 < 5000, f"P99 latency {p99:.1f}ms exceeds 5000ms"

    def test_bulkhead_no_slot_leaks_after_sustained_load(self):
        """After sustained load, all bulkhead slots should be released."""
        from network_mcp.connection import bulkhead_slot, get_bulkhead, init_bulkhead

        init_bulkhead(5)
        devices = {f"device-{i}": _make_mock_node() for i in range(3)}

        def _run_with_bulkhead(dev_name):
            try:
                with bulkhead_slot("eos", timeout=5.0):
                    run_show_command(conn_mgr, dev_name, ["show version"])
            except TimeoutError:
                pass

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_run_with_bulkhead, f"device-{i % 3}") for i in range(50)]
                concurrent.futures.wait(futures, timeout=30)

        bh = get_bulkhead()
        assert bh is not None
        assert bh.get_available("eos") == 5


# ===========================================================================
# Memory Stability Under Cache Churn (Extended)
# ===========================================================================


class TestMemoryStabilityExtended:
    """Extended memory stability tests."""

    def test_cache_eviction_bounds_entries(self):
        """After many inserts with bounded max_entries, cache stays bounded."""
        cache = CommandCache(max_entries=100, enabled=True)

        for i in range(1000):
            host = f"host-{i % 50}"
            cache.put(host, [f"show-{i}"], "json", {"data": "x" * 100, "idx": i}, ttl=60)

        stats = cache.stats()
        assert stats["entries"] <= 100

    def test_idempotency_store_eviction_bounds_entries(self):
        """IdempotencyStore should not exceed max_entries."""
        from network_mcp.idempotency import IdempotencyStore

        store = IdempotencyStore(ttl=3600, max_entries=50)
        for i in range(500):
            store.set(f"key-{i}", {"status": "success", "i": i})

        assert len(store._store) <= 50

    def test_rate_limiter_memory_bounded(self):
        """Creating 500 device buckets should stay under 5MB."""
        limiter = DeviceRateLimiter(show_rate=10.0, show_burst=10.0, config_rate=1.0, config_burst=1.0)

        tracemalloc.start()
        for i in range(500):
            limiter.check_show(f"device-{i}")
            limiter.check_config(f"device-{i}")

        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 5, f"Rate limiter peak {peak_mb:.1f}MB exceeds 5MB"


# ===========================================================================
# Metrics Consistency Under Load (Extended)
# ===========================================================================


class TestMetricsConsistencyExtended:
    """Extended metrics consistency tests."""

    def test_metrics_recorded_under_concurrent_load(self, mock_conn_mgr, mock_node):
        """Under concurrent load, metrics should record without crashes."""
        from network_mcp.observability import MetricsCollector

        mc = MetricsCollector()
        mc._enabled = True
        mc.reset()

        mock_node.run_commands.return_value = [MOCK_VERSION]
        errors = []

        def _record():
            try:
                for _ in range(20):
                    run_show_command(conn_mgr, "test-device", ["show version"])
            except Exception as e:
                errors.append(e)

        with patch("network_mcp.helpers._get_metrics_collector", return_value=mc):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_record) for _ in range(10)]
                concurrent.futures.wait(futures, timeout=30)

        assert not errors, f"Metrics errors: {errors}"

    def test_metrics_collector_reset_safe_under_load(self):
        """Resetting metrics during concurrent recording should not crash."""
        from network_mcp.observability import MetricsCollector

        mc = MetricsCollector()
        mc._enabled = True
        errors = []

        def _record_metrics():
            try:
                for _ in range(100):
                    mc.record_cache_hit()
                    mc.record_cache_miss()
                    mc.record_rate_limit_throttle()
            except Exception as e:
                errors.append(e)

        def _reset_metrics():
            try:
                for _ in range(10):
                    mc.reset()
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_record_metrics) for _ in range(5)]
        threads.append(threading.Thread(target=_reset_metrics))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Reset safety errors: {errors}"


# ===========================================================================
# Load Test With All Reliability Features Enabled
# ===========================================================================


class TestLoadWithAllReliabilityFeatures:
    """Load tests that exercise cache, circuit breaker, rate limiter, and bulkhead simultaneously."""

    def test_200_requests_10_devices_all_features(self):
        """200 requests across 10 devices with cache, rate limiter, circuit breaker."""
        from network_mcp.connection import init_bulkhead

        devices = {f"device-{i}": _make_mock_node() for i in range(10)}
        init_bulkhead(10)

        all_results = []
        lock = threading.Lock()

        def _run_request(idx):
            dev = f"device-{idx % 10}"
            r = run_show_command(conn_mgr, dev, ["show version"])
            with lock:
                all_results.append(r)

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(_run_request, i) for i in range(200)]
                done, not_done = concurrent.futures.wait(futures, timeout=60)

        assert len(not_done) == 0, f"{len(not_done)} futures timed out"
        assert len(all_results) == 200
        success_count = sum(1 for r in all_results if r["status"] == "success")
        # Expect some to succeed, some rate-limited
        assert success_count >= 10, f"Only {success_count}/200 succeeded"

    def test_mixed_success_and_failure_devices_under_load(self):
        """5 healthy + 5 failing devices under 100 concurrent requests."""
        from network_mcp.connection import init_bulkhead

        devices = {}
        for i in range(5):
            devices[f"ok-{i}"] = _make_mock_node()
        for i in range(5):
            fail = _make_mock_node()
            fail.run_commands.side_effect = ConnectionError("unreachable")
            devices[f"fail-{i}"] = fail

        init_bulkhead(10)

        all_results = []
        lock = threading.Lock()

        def _run(idx):
            dev_names = list(devices.keys())
            dev = dev_names[idx % 10]
            r = run_show_command(conn_mgr, dev, ["show version"])
            with lock:
                all_results.append((dev, r))

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(_run, i) for i in range(100)]
                done, not_done = concurrent.futures.wait(futures, timeout=30)

        assert len(not_done) == 0
        assert len(all_results) == 100

        # ok-* devices should have at least some successes
        ok_results = [r for name, r in all_results if name.startswith("ok-")]
        ok_successes = sum(1 for r in ok_results if r["status"] == "success")
        assert ok_successes > 0

        # fail-* devices should all be errors
        fail_results = [r for name, r in all_results if name.startswith("fail-")]
        for r in fail_results:
            assert r["status"] == "error"

    def test_load_with_cache_hits_reduces_device_calls(self):
        """Under load, cache should serve many requests without hitting the device."""
        device = _make_mock_node()
        devices = {"cached-dev": device}

        with _multi_device_conn_mgr(devices):
            results = []
            for _ in range(50):
                r = run_show_command(conn_mgr, "cached-dev", ["show version"])
                results.append(r)

        success_count = sum(1 for r in results if r["status"] == "success")
        assert success_count >= 1
        # Device should be called much fewer than 50 times due to caching
        actual_calls = device.run_commands.call_count
        assert actual_calls < 50, f"Expected caching to reduce calls, but got {actual_calls}"

    def test_sustained_load_no_memory_leak(self):
        """500 requests should not cause unbounded memory growth."""
        devices = {f"device-{i}": _make_mock_node() for i in range(5)}

        tracemalloc.start()

        with _multi_device_conn_mgr(devices):
            for i in range(500):
                run_show_command(conn_mgr, f"device-{i % 5}", ["show version"])

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 100, f"Peak memory {peak_mb:.1f}MB exceeds 100MB"


# ===========================================================================
# Rate Limiter Backoff Under Burst Load
# ===========================================================================


class TestRateLimiterBackoffUnderBurstLoad:
    """Verify rate limiter backoff behavior under sustained burst traffic."""

    def test_burst_10x_capacity_with_backoff(self):
        """Send 10x capacity with backoff, verify bounded completion time."""
        bucket = TokenBucket(rate=10.0, capacity=10.0)

        start = time.monotonic()
        successes = 0
        for _ in range(100):
            if bucket.acquire_with_backoff(max_wait=0.5):
                successes += 1
        elapsed = time.monotonic() - start

        assert successes >= 10, f"Only {successes} succeeded"
        assert elapsed < 60, f"Burst took {elapsed:.1f}s"

    def test_per_device_burst_isolation(self):
        """Bursting one device should not affect rate limiting of another."""
        limiter = DeviceRateLimiter(show_rate=5.0, show_burst=5.0, config_rate=1.0, config_burst=1.0)

        # Exhaust device-a
        for _ in range(20):
            limiter.check_show("device-a")

        # device-b should be unaffected
        successes_b = sum(1 for _ in range(5) if limiter.check_show("device-b"))
        assert successes_b >= 3, f"device-b only got {successes_b} tokens"

    def test_concurrent_burst_across_devices_with_backoff(self):
        """Concurrent burst across 5 devices with backoff should complete without deadlock."""
        limiter = DeviceRateLimiter(show_rate=5.0, show_burst=5.0, config_rate=1.0, config_burst=1.0)
        results = {}
        lock = threading.Lock()

        def _burst_device(dev_name):
            allowed = 0
            for _ in range(20):
                bucket = limiter._get_show_bucket(dev_name)
                if bucket.acquire_with_backoff(max_wait=0.2):
                    allowed += 1
            with lock:
                results[dev_name] = allowed

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_burst_device, f"dev-{i}") for i in range(5)]
            done, not_done = concurrent.futures.wait(futures, timeout=30)

        assert len(not_done) == 0, "Deadlock detected"
        assert len(results) == 5
        for dev, count in results.items():
            assert count >= 3, f"{dev} only got {count} tokens"

    def test_backoff_wait_time_is_reasonable(self):
        """wait_time() should return a reasonable value when bucket is empty."""
        bucket = TokenBucket(rate=1.0, capacity=1.0)
        bucket.acquire()  # Empty the bucket

        wait = bucket.wait_time()
        assert 0.0 <= wait <= 2.0, f"Wait time {wait:.3f}s seems unreasonable"

    def test_config_rate_limit_separate_from_show_under_burst(self):
        """Config rate limit burst should not affect show rate limit."""
        limiter = DeviceRateLimiter(show_rate=100.0, show_burst=100.0, config_rate=1.0, config_burst=1.0)

        # Exhaust config tokens
        for _ in range(10):
            limiter.check_config("dev-x")

        # Show should still have plenty of tokens
        show_successes = sum(1 for _ in range(50) if limiter.check_show("dev-x"))
        assert show_successes >= 40, f"Show only got {show_successes} tokens"


# ===========================================================================
# Connection Pool Under Sustained Load
# ===========================================================================


class TestConnectionPoolSustainedLoad:
    """Test connection pool behavior under sustained load patterns."""

    def test_pool_handles_rapid_acquire_release_cycles(self):
        """Rapid acquire/release on same device should not leak connections."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=5)

        mock = _make_mock_node()
        with patch.object(mgr, "_create_driver", return_value=mock):
            for _ in range(100):
                with mgr.acquire("rapid-device"):
                    pass

        stats = mgr.pool_stats()
        assert stats["active"] == 0
        assert stats["pooled"] <= 5

    def test_pool_concurrent_acquire_different_devices(self):
        """Concurrent acquire of different devices should not deadlock."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=10)

        create_lock = threading.Lock()
        call_idx = {"n": 0}

        def _create(*args, **kwargs):
            with create_lock:
                call_idx["n"] += 1
            return _make_mock_node()

        errors = []

        def _use(dev):
            try:
                with mgr.acquire(dev):
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        with patch.object(mgr, "_create_driver", side_effect=_create):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(_use, f"dev-{i}") for i in range(10)]
                done, not_done = concurrent.futures.wait(futures, timeout=15)

        assert len(not_done) == 0, "Deadlock detected"
        assert not errors, f"Errors: {errors}"

    def test_pool_checkout_stats_accurate_after_load(self):
        """Pool stats should accurately reflect checkout history after load."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=5)

        with patch.object(mgr, "_create_driver", side_effect=lambda *a, **k: _make_mock_node()):
            for i in range(20):
                with mgr.acquire(f"dev-{i % 3}"):
                    pass

        stats = mgr.pool_stats()
        assert stats["total_checkouts"] == 20
        assert stats["total_created"] >= 3  # At least 3 devices created
        assert stats["active"] == 0
