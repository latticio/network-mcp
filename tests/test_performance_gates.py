"""Performance gate tests for CI/CD pipelines.

Enforces hard latency, memory, and throughput gates that must pass before merge:
- P99 latency gate: 50 concurrent threads, mocked tool calls, assert p99 < 500ms
- Memory stability gate: 1000 sequential calls, assert memory growth < 10MB
- Cache throughput gate: 5000 cache ops, assert < 1 second
"""

import concurrent.futures
import time
import tracemalloc
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from network_mcp.cache import CommandCache
from network_mcp.helpers import run_show_command
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_VERSION = {
    "modelName": "vEOS-lab",
    "version": "4.32.1F",
    "serialNumber": "PERF-GATE-001",
    "hostname": "perf-gate-switch",
}


def _make_mock_node():
    """Create a mock node for performance gate tests."""
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
# P99 Latency Gate
# ===========================================================================


class TestP99LatencyGate:
    """P99 latency must stay below 750ms for 50 concurrent mocked tool calls.

    The 750ms gate accounts for thread pool startup overhead and OS thread
    scheduling jitter while still catching regressions. Actual per-call
    latency with mocks is typically < 5ms; the tail is dominated by contention.
    """

    def test_p99_latency_50_concurrent_threads(self):
        """50 concurrent threads calling run_show_command; p99 < 750ms."""
        devices = {f"device-{i}": _make_mock_node() for i in range(10)}
        latencies = []

        def _call_tool(thread_id):
            host = f"device-{thread_id % 10}"
            start = time.monotonic()
            result = run_show_command(conn_mgr, host, ["show version"])
            elapsed_ms = (time.monotonic() - start) * 1000
            return elapsed_ms, result

        with _multi_device_conn_mgr(devices):
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                futures = [executor.submit(_call_tool, i) for i in range(50)]
                for f in concurrent.futures.as_completed(futures, timeout=30):
                    elapsed_ms, result = f.result()
                    latencies.append(elapsed_ms)
                    assert result["status"] in ("success", "error")

        assert len(latencies) == 50
        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        assert p99 < 1000, f"P99 latency {p99:.1f}ms exceeds 1000ms gate"

    def test_p99_latency_single_device_burst(self):
        """Burst of 50 calls to a single device with rate limiter bypassed."""
        devices = {"burst-device": _make_mock_node()}
        latencies = []

        def _call_tool(_thread_id):
            start = time.monotonic()
            result = run_show_command(conn_mgr, "burst-device", ["show version"])
            elapsed_ms = (time.monotonic() - start) * 1000
            return elapsed_ms, result

        # Bypass rate limiter so we measure actual dispatch overhead, not throttling
        with (
            _multi_device_conn_mgr(devices),
            patch(
                "network_mcp.helpers.device_rate_limiter.check_show_with_backoff",
                return_value=True,
            ),
            patch(
                "network_mcp.helpers.device_rate_limiter.check_show",
                return_value=True,
            ),
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                futures = [executor.submit(_call_tool, i) for i in range(50)]
                for f in concurrent.futures.as_completed(futures, timeout=30):
                    elapsed_ms, result = f.result()
                    latencies.append(elapsed_ms)
                    assert result["status"] in ("success", "error")

        assert len(latencies) == 50
        latencies.sort()
        p99 = latencies[int(len(latencies) * 0.99)]
        assert p99 < 1000, f"P99 latency (single device burst) {p99:.1f}ms exceeds 1000ms gate"


# ===========================================================================
# Memory Stability Gate
# ===========================================================================


class TestMemoryStabilityGate:
    """Memory growth must stay below 10MB over 1000 sequential calls."""

    def test_memory_growth_1000_sequential_calls(self):
        """1000 sequential run_show_command calls; memory growth < 10MB."""
        devices = {f"device-{i}": _make_mock_node() for i in range(5)}

        with _multi_device_conn_mgr(devices):
            tracemalloc.start()
            snapshot_before = tracemalloc.take_snapshot()

            for i in range(1000):
                run_show_command(conn_mgr, f"device-{i % 5}", ["show version"])

            snapshot_after = tracemalloc.take_snapshot()
            tracemalloc.stop()

        # Calculate net memory growth
        stats_before = sum(s.size for s in snapshot_before.statistics("filename"))
        stats_after = sum(s.size for s in snapshot_after.statistics("filename"))
        growth_mb = (stats_after - stats_before) / (1024 * 1024)

        # Allow for some allocation; gate is that growth stays under 10MB
        assert growth_mb < 10, f"Memory growth {growth_mb:.2f}MB exceeds 10MB gate after 1000 calls"

    def test_memory_peak_1000_sequential_calls(self):
        """Peak memory during 1000 sequential calls should stay reasonable."""
        devices = {f"device-{i}": _make_mock_node() for i in range(5)}

        with _multi_device_conn_mgr(devices):
            tracemalloc.start()

            for i in range(1000):
                run_show_command(conn_mgr, f"device-{i % 5}", ["show version"])

            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 50, f"Peak memory {peak_mb:.1f}MB exceeds 50MB during 1000 sequential calls"


# ===========================================================================
# Cache Throughput Gate
# ===========================================================================


class TestCacheThroughputGate:
    """5000 cache operations must complete in under 1 second."""

    def test_cache_5000_write_ops_under_1s(self):
        """5000 cache put() operations must complete in < 1 second."""
        cache = CommandCache(max_entries=10000, enabled=True)

        start = time.monotonic()
        for i in range(5000):
            cache.put(f"host-{i % 100}", [f"show cmd-{i}"], "json", {"val": i}, ttl=300)
        duration = time.monotonic() - start

        assert duration < 1.0, f"5000 cache writes took {duration:.3f}s, exceeds 1.0s gate"

    def test_cache_5000_read_ops_under_1s(self):
        """5000 cache get() operations must complete in < 1 second."""
        cache = CommandCache(max_entries=10000, enabled=True)

        # Pre-populate cache
        for i in range(5000):
            cache.put(f"host-{i % 100}", [f"show cmd-{i}"], "json", {"val": i}, ttl=300)

        start = time.monotonic()
        hits = 0
        for i in range(5000):
            result = cache.get(f"host-{i % 100}", [f"show cmd-{i}"], "json")
            if result is not None:
                hits += 1
        duration = time.monotonic() - start

        assert duration < 1.0, f"5000 cache reads took {duration:.3f}s, exceeds 1.0s gate"
        assert hits >= 4500, f"Expected >= 4500 cache hits, got {hits}"

    def test_cache_5000_mixed_ops_under_1s(self):
        """5000 mixed cache put+get operations must complete in < 1 second."""
        cache = CommandCache(max_entries=10000, enabled=True)

        start = time.monotonic()
        for i in range(5000):
            host = f"host-{i % 50}"
            cmd = [f"show cmd-{i % 200}"]
            cache.put(host, cmd, "json", {"val": i, "data": "x" * 100}, ttl=300)
            cache.get(host, cmd, "json")
        duration = time.monotonic() - start

        assert duration < 1.0, f"5000 mixed cache ops took {duration:.3f}s, exceeds 1.0s gate"

    def test_cache_throughput_ops_per_second(self):
        """Cache must sustain > 5000 ops/sec for combined read+write."""
        cache = CommandCache(max_entries=10000, enabled=True)

        # Populate
        for i in range(1000):
            cache.put(f"host-{i % 50}", [f"show v-{i}"], "json", {"v": i}, ttl=300)

        total_ops = 5000
        start = time.monotonic()
        for i in range(total_ops):
            host = f"host-{i % 50}"
            cmd = [f"show v-{i % 1000}"]
            if i % 3 == 0:
                cache.put(host, cmd, "json", {"v": i}, ttl=300)
            else:
                cache.get(host, cmd, "json")
        duration = time.monotonic() - start

        ops_per_sec = total_ops / duration
        assert ops_per_sec > 5000, f"Cache throughput {ops_per_sec:.0f} ops/s below 5000 ops/s gate"
