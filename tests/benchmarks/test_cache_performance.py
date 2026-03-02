"""Cache performance benchmarks.

Measures cache hit/miss latency and the throughput improvement from caching.
Uses isolated CommandCache instances to ensure benchmark independence.
"""

from network_mcp.cache import CommandCache
from network_mcp.helpers import command_cache


def test_benchmark_cache_miss(benchmark, bench_mock_conn_mgr):
    """Benchmark tool call with cache miss (cold cache, first call).

    Each iteration starts with a fresh cache, forcing a full device query.
    Measures the worst-case path: cache lookup (miss) + device call + cache store.
    """
    from network_mcp.tools.device import eos_get_device_info

    def _call_with_cold_cache():
        command_cache.clear()
        return eos_get_device_info("spine-01")

    result = benchmark(_call_with_cold_cache)
    assert result["status"] == "success"


def test_benchmark_cache_hit(benchmark, bench_mock_conn_mgr):
    """Benchmark tool call with cache hit (warm cache, repeated calls).

    Pre-populates the cache, then benchmarks repeated calls that hit the cache.
    This should be significantly faster than cache miss since no device call occurs.
    """
    from network_mcp.tools.device import eos_get_device_info

    # Warm the cache with an initial call
    command_cache.clear()
    initial = eos_get_device_info("spine-01")
    assert initial["status"] == "success"

    # Now benchmark repeated calls (should all hit cache)
    result = benchmark(eos_get_device_info, "spine-01")
    assert result["status"] == "success"


def test_benchmark_cache_put_get_cycle(benchmark):
    """Benchmark raw cache put + get cycle (no device interaction).

    Measures the overhead of the cache data structure itself: key hashing,
    lock acquisition, TTL checking, LRU tracking.
    """
    cache = CommandCache(max_entries=500, max_memory_mb=50, enabled=True)
    test_data = {"status": "success", "device": "bench-host", "data": [{"version": "4.32.1F"}]}

    def _put_and_get():
        cache.put("bench-host", ["show version"], "json", test_data, ttl=300)
        result = cache.get("bench-host", ["show version"], "json")
        return result

    result = benchmark(_put_and_get)
    assert result is not None
    assert result["status"] == "success"


def test_benchmark_cache_lookup_miss_only(benchmark):
    """Benchmark cache lookup on a miss (no put, empty cache).

    Measures the minimal overhead of a cache miss: key construction, lock,
    dict lookup, miss counter increment.
    """
    cache = CommandCache(max_entries=500, max_memory_mb=50, enabled=True)

    result = benchmark(cache.get, "nonexistent-host", ["show version"], "json")
    assert result is None
