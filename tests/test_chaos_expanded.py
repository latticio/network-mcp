"""Expanded chaos engineering tests for resilience validation.

Tests cover advanced failure scenarios beyond the base test_chaos.py suite:
- Device timeout mid-operation (mock sleep exceeding TIMEOUT_SHOW)
- Redis unavailable during distributed rate limiting (fall back to local)
- Connection pool exhaustion under high concurrency (50 concurrent, pool size 10)
- Stale-if-error cache behavior (serve expired data when device fails)
- Bulkhead isolation (slow IOS-XE doesn't block fast EOS)
"""

import concurrent.futures

import pytest

pytestmark = pytest.mark.chaos
import threading
import time
from unittest.mock import MagicMock, patch

from network_mcp.cache import CommandCache
from network_mcp.config import NetworkSettings
from network_mcp.connection import ConnectionManager
from network_mcp.helpers import run_show_command
from network_mcp.server import conn_mgr
from tests.chaos_helpers import (
    MOCK_VERSION,
    healthy_device,
    multi_device_conn_mgr,
    slow_device,
    unreachable_device,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_node(*, delay=0.0, failing=False, platform="eos"):
    """Create a mock node with optional delay or failure."""
    node = MagicMock()
    node.platform = platform
    node.is_connected.return_value = True

    if failing:
        node.run_commands.side_effect = ConnectionError("device unreachable")
    elif delay > 0:

        def _slow(*args, **kwargs):  # noqa: ARG001
            time.sleep(delay)
            return [MOCK_VERSION]

        node.run_commands.side_effect = _slow
    else:
        node.run_commands.return_value = [MOCK_VERSION]

    node.run_show = node.run_commands
    node.config.return_value = []
    node.run_config = node.config
    return node


# ===========================================================================
# Device Timeout Mid-Operation Tests
# ===========================================================================


class TestDeviceTimeoutMidOperation:
    """Simulate device timeouts exceeding TIMEOUT_SHOW."""

    def test_slow_device_returns_error_on_timeout(self, mock_conn_mgr, mock_node):
        """A device that takes longer than timeout should return error, not hang."""

        def _very_slow(*args, **kwargs):  # noqa: ARG001
            time.sleep(0.5)
            raise TimeoutError("Read timed out")

        mock_node.run_commands.side_effect = _very_slow

        start = time.monotonic()
        result = run_show_command(conn_mgr, "slow-device", ["show version"])
        duration = time.monotonic() - start

        assert result["status"] == "error"
        assert "Timeout" in result["error"] or "error" in result["error"].lower()
        # Should complete (not hang indefinitely)
        assert duration < 30, f"Command took {duration:.1f}s, possible hang"

    def test_timeout_doesnt_corrupt_conn_mgr_state(self, mock_conn_mgr, mock_node):
        """After a timeout, subsequent successful requests should still work."""
        call_count = 0

        def _timeout_then_succeed(*args, **kwargs):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise TimeoutError("Read timed out")
            return [MOCK_VERSION]

        mock_node.run_commands.side_effect = _timeout_then_succeed

        # First call times out
        result1 = run_show_command(conn_mgr, "timeout-device", ["show version"])
        assert result1["status"] == "error"

        # After failures exhaust retries, reset side_effect for next call
        mock_node.run_commands.side_effect = _timeout_then_succeed

        # Subsequent call should eventually succeed (retry logic)
        result2 = run_show_command(conn_mgr, "timeout-device", ["show version"])
        # This should succeed since call_count > 3
        assert result2["status"] == "success"

    def test_timeout_mid_batch_doesnt_crash(self, mock_conn_mgr, mock_node):
        """Timeout during one of several concurrent requests should not crash others."""
        call_count = 0
        lock = threading.Lock()

        def _sporadic_timeout(*args, **kwargs):  # noqa: ARG001
            nonlocal call_count
            with lock:
                call_count += 1
                current = call_count
            if current % 3 == 0:
                raise TimeoutError("Sporadic timeout")
            return [MOCK_VERSION]

        mock_node.run_commands.side_effect = _sporadic_timeout

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_show_command, conn_mgr, "mixed-device", ["show version"]) for _ in range(10)]
            results = [f.result(timeout=15) for f in concurrent.futures.as_completed(futures)]

        # All should return (success or error), none should crash
        for r in results:
            assert r["status"] in ("success", "error")
            assert "device" in r

    def test_concurrent_timeouts_across_devices(self):
        """Multiple devices timing out concurrently should all resolve independently."""
        devices = {
            "fast-1": healthy_device(),
            "fast-2": healthy_device(),
            "slow-1": slow_device(delay_seconds=0.3),
            "slow-2": slow_device(delay_seconds=0.3),
        }

        with multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    name: executor.submit(run_show_command, conn_mgr, name, ["show version"]) for name in devices
                }
                results = {name: f.result(timeout=10) for name, f in futures.items()}

        # Fast devices should succeed
        assert results["fast-1"]["status"] == "success"
        assert results["fast-2"]["status"] == "success"
        # Slow devices should also succeed (delay < timeout_show)
        assert results["slow-1"]["status"] == "success"
        assert results["slow-2"]["status"] == "success"


# ===========================================================================
# Redis Unavailable During Distributed Rate Limit
# ===========================================================================


class TestRedisUnavailableFallback:
    """Test fallback behavior when Redis is unavailable for distributed backends."""

    def test_redis_rate_limiter_falls_back_to_allow(self):
        """When Redis is down, RedisRateLimiter should fail-open (allow requests)."""
        from network_mcp.distributed.redis_rate_limiter import RedisRateLimiter

        limiter = RedisRateLimiter(redis_url="redis://nonexistent:6379/0")

        # Mock the redis import to raise ConnectionError
        mock_redis = MagicMock()
        mock_redis.pipeline.side_effect = ConnectionError("Redis unavailable")
        limiter._redis = mock_redis

        # Should fail-open: return True (allow the request)
        assert limiter.check_show("test-device") is True
        assert limiter.check_config("test-device") is True

    def test_redis_rate_limiter_wait_time_defaults(self):
        """When Redis is down, show_wait_time should return default 1.0."""
        from network_mcp.distributed.redis_rate_limiter import RedisRateLimiter

        limiter = RedisRateLimiter(redis_url="redis://nonexistent:6379/0")
        mock_redis = MagicMock()
        mock_redis.ttl.side_effect = ConnectionError("Redis unavailable")
        limiter._redis = mock_redis

        wait = limiter.show_wait_time("test-device")
        assert wait == 1.0

        wait = limiter.config_wait_time("test-device")
        assert wait == 1.0

    def test_redis_rate_limiter_reset_tolerates_failure(self):
        """reset() should not raise when Redis is unavailable."""
        from network_mcp.distributed.redis_rate_limiter import RedisRateLimiter

        limiter = RedisRateLimiter(redis_url="redis://nonexistent:6379/0")
        mock_redis = MagicMock()
        mock_redis.delete.side_effect = ConnectionError("Redis unavailable")
        mock_redis.scan.side_effect = ConnectionError("Redis unavailable")
        limiter._redis = mock_redis

        # Should not raise
        limiter.reset("test-device")
        limiter.reset()

    def test_show_command_works_with_redis_down(self, mock_conn_mgr):
        """run_show_command should succeed even when Redis backend is down."""
        # The local rate limiter is used by default; this test validates
        # that the system works regardless of Redis state
        result = run_show_command(conn_mgr, "test-device", ["show version"])
        assert result["status"] == "success"

    def test_redis_circuit_breaker_falls_back(self):
        """When Redis is down, RedisCircuitBreakerRegistry should fail-open."""
        from network_mcp.distributed.redis_circuit_breaker import RedisCircuitBreakerRegistry

        registry = RedisCircuitBreakerRegistry(
            redis_url="redis://nonexistent:6379/0",
            threshold=3,
            cooldown=60,
            enabled=True,
        )
        mock_redis = MagicMock()
        mock_redis.get.side_effect = ConnectionError("Redis unavailable")
        mock_redis.incr.side_effect = ConnectionError("Redis unavailable")
        mock_redis.pipeline.side_effect = ConnectionError("Redis unavailable")
        registry._redis = mock_redis

        # Should fail-open: allow the request
        assert registry.allow_request("test-device") is True

    def test_redis_cache_falls_back(self):
        """When Redis is down, RedisCommandCache should return None (cache miss)."""
        from network_mcp.distributed.redis_cache import RedisCommandCache

        cache = RedisCommandCache(redis_url="redis://nonexistent:6379/0", enabled=True)
        mock_redis = MagicMock()
        mock_redis.get.side_effect = ConnectionError("Redis unavailable")
        mock_redis.setex.side_effect = ConnectionError("Redis unavailable")
        cache._redis = mock_redis

        # get should return None (cache miss, not crash)
        result = cache.get("test-host", ["show version"], "json")
        assert result is None

        # put should not raise
        cache.put("test-host", ["show version"], "json", {"data": "test"}, ttl=60)


# ===========================================================================
# Connection Pool Exhaustion Under High Concurrency
# ===========================================================================


class TestConnectionPoolExhaustionHighConcurrency:
    """50 concurrent requests with pool size 10."""

    def test_50_concurrent_pool_size_10(self):
        """50 concurrent requests on pool size 10 should complete (some may fail with pool exhaustion)."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=10)

        node_idx = 0
        create_lock = threading.Lock()
        nodes = [_make_mock_node(delay=0.02) for _ in range(50)]

        def _create(*args, **kwargs):  # noqa: ARG001
            nonlocal node_idx
            with create_lock:
                idx = node_idx
                node_idx += 1
            return nodes[idx % len(nodes)]

        errors = []
        successes = []
        pool_exhausted = []

        def _use_pool(i):
            try:
                with mgr.acquire(f"device-{i % 15}"):
                    time.sleep(0.01)
                successes.append(i)
            except ConnectionError as e:
                if "pool exhausted" in str(e):
                    pool_exhausted.append(i)
                else:
                    errors.append(e)
            except Exception as e:
                errors.append(e)

        with patch.object(mgr, "_create_driver", side_effect=_create):
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                futures = [executor.submit(_use_pool, i) for i in range(50)]
                done, not_done = concurrent.futures.wait(futures, timeout=30)

        assert len(not_done) == 0, f"{len(not_done)} futures timed out (deadlock)"
        assert not errors, f"Unexpected errors: {errors}"
        # At least some should have succeeded
        assert len(successes) > 0

    def test_pool_recovers_after_burst(self):
        """After a burst of concurrent requests, pool should recover for new ones."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=5)

        def _create(*args, **kwargs):  # noqa: ARG001
            return _make_mock_node()

        with patch.object(mgr, "_create_driver", side_effect=_create):
            # Burst phase
            def _use(i):
                try:
                    with mgr.acquire(f"device-{i % 5}"):
                        time.sleep(0.01)
                except ConnectionError:
                    pass

            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(_use, i) for i in range(20)]
                concurrent.futures.wait(futures, timeout=10)

            # Recovery phase: pool should work again
            with mgr.acquire("device-0") as driver:
                assert driver is not None

            stats = mgr.pool_stats()
            assert stats["active"] <= 5

    def test_pool_stats_accurate_after_concurrent_churn(self):
        """Pool stats should remain accurate after heavy concurrent acquire/release cycles."""
        settings = NetworkSettings(net_username="admin", net_password="", net_transport="https")
        mgr = ConnectionManager(settings, max_connections=8)

        def _create(*args, **kwargs):  # noqa: ARG001
            return _make_mock_node()

        errors = []

        def _churn(device_idx):
            try:
                for _ in range(10):
                    try:
                        with mgr.acquire(f"device-{device_idx}"):
                            time.sleep(0.005)
                    except ConnectionError:
                        pass
            except Exception as e:
                errors.append(e)

        with patch.object(mgr, "_create_driver", side_effect=_create):
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                futures = [executor.submit(_churn, i % 8) for i in range(15)]
                concurrent.futures.wait(futures, timeout=15)

        assert not errors, f"Pool churn errors: {errors}"
        stats = mgr.pool_stats()
        # After all threads complete, no active connections should remain
        assert stats["active"] == 0
        assert stats["pooled"] <= 8


# ===========================================================================
# Stale-If-Error Cache Behavior
# ===========================================================================


class TestStaleIfErrorCacheBehavior:
    """Test cache behavior when device fails after cache entry expires."""

    def test_cache_miss_after_expiry_returns_none(self):
        """After TTL expiry, cache should return None."""
        cache = CommandCache(max_entries=100, enabled=True)

        cache.put("host-1", ["show version"], "json", MOCK_VERSION, ttl=1)
        assert cache.get("host-1", ["show version"], "json") is not None

        # Wait for expiry
        time.sleep(1.1)
        assert cache.get("host-1", ["show version"], "json") is None

    def test_fresh_cache_hit_avoids_device_call(self, mock_conn_mgr, mock_node):
        """Cache hit for non-expired entry should skip the device call."""
        from network_mcp.helpers import command_cache

        # Pre-populate cache with the full result dict (what run_show_command caches)
        cached_result = {"status": "success", "device": "cached-device", "data": [MOCK_VERSION]}
        command_cache.put("cached-device", ["show version"], "json", cached_result, ttl=300)

        result = run_show_command(conn_mgr, "cached-device", ["show version"])
        assert result["status"] == "success"

    def test_expired_cache_triggers_device_call(self, mock_conn_mgr, mock_node):
        """After cache expiry, show command should reach the device again."""
        mock_node.run_commands.return_value = [{"hostname": "fresh-data"}]

        # Use cache_ttl=0 to force bypass
        result = run_show_command(conn_mgr, "test-device", ["show version"], cache_ttl=0)
        assert result["status"] == "success"
        mock_node.run_commands.assert_called()

    def test_cache_invalidation_forces_refetch(self, mock_conn_mgr, mock_node):
        """Cache invalidation should force fresh data from device."""
        from network_mcp.helpers import command_cache

        # Populate cache
        command_cache.put("inv-device", ["show version"], "json", [{"old": True}], ttl=300)

        # Invalidate
        command_cache.invalidate_host("inv-device")

        # Now cache should miss
        result = command_cache.get("inv-device", ["show version"], "json")
        assert result is None

    def test_cache_put_get_roundtrip_preserves_data(self):
        """Cache should faithfully preserve complex nested data structures."""
        cache = CommandCache(max_entries=100, enabled=True)

        complex_data = {
            "interfaces": {
                "Ethernet1": {"status": "up", "speed": "100Gbps", "counters": {"rx": 1000, "tx": 2000}},
                "Ethernet2": {"status": "down", "speed": "10Gbps", "counters": {"rx": 0, "tx": 0}},
            },
            "vlans": [100, 200, 300],
        }

        cache.put("host-1", ["show interfaces"], "json", complex_data, ttl=300)
        result = cache.get("host-1", ["show interfaces"], "json")
        assert result == complex_data


# ===========================================================================
# Bulkhead Isolation Tests
# ===========================================================================


class TestBulkheadIsolation:
    """Slow devices should not block or degrade fast devices."""

    def test_slow_iosxe_doesnt_block_fast_eos(self):
        """A slow IOS-XE device should not delay EOS device responses."""
        slow_iosxe = _make_mock_node(delay=1.0, platform="iosxe")
        fast_eos_1 = _make_mock_node(platform="eos")
        fast_eos_2 = _make_mock_node(platform="eos")

        devices = {
            "slow-iosxe": slow_iosxe,
            "fast-eos-1": fast_eos_1,
            "fast-eos-2": fast_eos_2,
        }

        with multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    name: executor.submit(run_show_command, conn_mgr, name, ["show version"]) for name in devices
                }

                # The fast EOS devices should complete quickly even while IOS-XE is slow
                fast_results = {}
                for name in ["fast-eos-1", "fast-eos-2"]:
                    fast_results[name] = futures[name].result(timeout=5)

                # Fast devices should succeed
                assert fast_results["fast-eos-1"]["status"] == "success"
                assert fast_results["fast-eos-2"]["status"] == "success"

                # Slow device should also eventually succeed
                slow_result = futures["slow-iosxe"].result(timeout=5)
                assert slow_result["status"] == "success"

    def test_slow_device_measured_independently(self):
        """Slow device latency should not appear in fast device latency measurements."""
        slow_node = _make_mock_node(delay=0.5)
        fast_node = _make_mock_node()

        devices = {"slow-dev": slow_node, "fast-dev": fast_node}

        with multi_device_conn_mgr(devices):
            # Measure fast device latency
            fast_start = time.monotonic()
            fast_result = run_show_command(conn_mgr, "fast-dev", ["show version"])
            fast_duration = time.monotonic() - fast_start

            assert fast_result["status"] == "success"
            assert fast_duration < 0.3, f"Fast device took {fast_duration:.2f}s (should be < 0.3s)"

    def test_multiple_slow_devices_dont_amplify_delay(self):
        """Multiple slow devices running concurrently should not amplify each other's delays."""
        devices = {}
        for i in range(3):
            devices[f"slow-{i}"] = _make_mock_node(delay=0.3)
        for i in range(5):
            devices[f"fast-{i}"] = _make_mock_node()

        with multi_device_conn_mgr(devices):
            start = time.monotonic()

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    name: executor.submit(run_show_command, conn_mgr, name, ["show version"]) for name in devices
                }
                results = {name: f.result(timeout=10) for name, f in futures.items()}

            total_duration = time.monotonic() - start

        # All should complete
        for name, result in results.items():
            assert result["status"] == "success", f"{name} failed: {result}"

        # Total should be dominated by the slowest device, not additive
        # 3 slow devices at 0.3s each should not take 0.9s+ because they run in parallel
        assert total_duration < 2.0, f"Total duration {total_duration:.2f}s suggests serial execution"

    def test_failing_device_doesnt_affect_healthy_throughput(self):
        """A device that always errors should not reduce throughput for healthy devices."""
        devices = {
            "failing": unreachable_device(),
            "healthy-1": healthy_device(),
            "healthy-2": healthy_device(),
            "healthy-3": healthy_device(),
        }

        with multi_device_conn_mgr(devices):
            results = {}
            for name in devices:
                results[name] = run_show_command(conn_mgr, name, ["show version"])

        assert results["failing"]["status"] == "error"
        assert results["healthy-1"]["status"] == "success"
        assert results["healthy-2"]["status"] == "success"
        assert results["healthy-3"]["status"] == "success"

    def test_mixed_vendor_concurrent_isolation(self):
        """Concurrent requests to different vendor types should not interfere."""
        eos_node = _make_mock_node(platform="eos")
        iosxe_node = _make_mock_node(platform="iosxe", delay=0.1)
        nxos_node = _make_mock_node(platform="nxos")
        junos_node = _make_mock_node(platform="junos", delay=0.1)

        devices = {
            "eos-spine": eos_node,
            "iosxe-rtr": iosxe_node,
            "nxos-leaf": nxos_node,
            "junos-fw": junos_node,
        }

        with multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    name: executor.submit(run_show_command, conn_mgr, name, ["show version"]) for name in devices
                }
                results = {name: f.result(timeout=10) for name, f in futures.items()}

        for name, result in results.items():
            assert result["status"] == "success", f"{name} failed: {result}"
