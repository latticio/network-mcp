"""Tests for Redis-backed distributed coordination across multiple instances.

Validates that two simulated server instances sharing the same Redis backend
can coordinate rate limiting, caching, circuit breaking, and locking
consistently without data loss or interference.
"""

import json
import threading

import pytest

from network_mcp.distributed.redis_cache import RedisCommandCache
from network_mcp.distributed.redis_circuit_breaker import RedisCircuitBreakerRegistry
from network_mcp.distributed.redis_rate_limiter import RedisRateLimiter

# ---------------------------------------------------------------------------
# Shared in-memory Redis mock
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal in-memory Redis mock that supports the operations used by the
    distributed backends.  Thread-safe via a lock so concurrent-write tests
    produce deterministic results.
    """

    def __init__(self):
        self._data: dict[str, str] = {}
        self._ttls: dict[str, int] = {}
        self._lock = threading.Lock()

    # -- string operations --

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: str) -> bool:
        with self._lock:
            self._data[key] = value
            return True

    def setex(self, key: str, ttl: int, value: str) -> bool:
        with self._lock:
            self._data[key] = value
            self._ttls[key] = ttl
            return True

    def delete(self, *keys: str) -> int:
        with self._lock:
            count = 0
            for k in keys:
                if k in self._data:
                    del self._data[k]
                    self._ttls.pop(k, None)
                    count += 1
            return count

    def incr(self, key: str) -> int:
        with self._lock:
            val = int(self._data.get(key, 0)) + 1
            self._data[key] = str(val)
            return val

    def expire(self, key: str, seconds: int) -> bool:
        with self._lock:
            self._ttls[key] = seconds
            return True

    def ttl(self, key: str) -> int:
        with self._lock:
            if key not in self._data:
                return -2
            return self._ttls.get(key, -1)

    def scan(self, cursor: int, match: str = "*", count: int = 100) -> tuple[int, list[str]]:
        import fnmatch

        with self._lock:
            matched = [k for k in self._data if fnmatch.fnmatch(k, match)]
        return (0, matched)

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    """A pipeline that executes commands immediately against the FakeRedis store."""

    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._commands: list[tuple[str, tuple]] = []

    def incr(self, key: str):
        self._commands.append(("incr", (key,)))
        return self

    def expire(self, key: str, seconds: int):
        self._commands.append(("expire", (key, seconds)))
        return self

    def execute(self) -> list:
        results = []
        for cmd, args in self._commands:
            results.append(getattr(self._redis, cmd)(*args))
        self._commands.clear()
        return results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def shared_redis():
    """A single FakeRedis instance shared by two simulated server instances."""
    return FakeRedis()


@pytest.fixture
def cache_pair(shared_redis):
    """Two RedisCommandCache instances sharing the same Redis."""
    a = RedisCommandCache(enabled=True)
    b = RedisCommandCache(enabled=True)
    a._redis = shared_redis
    b._redis = shared_redis
    return a, b


@pytest.fixture
def rate_limiter_pair(shared_redis):
    """Two RedisRateLimiter instances sharing the same Redis."""
    a = RedisRateLimiter(show_rate=5.0, config_rate=1.0)
    b = RedisRateLimiter(show_rate=5.0, config_rate=1.0)
    a._redis = shared_redis
    b._redis = shared_redis
    return a, b


@pytest.fixture
def circuit_breaker_pair(shared_redis):
    """Two RedisCircuitBreakerRegistry instances sharing the same Redis."""
    a = RedisCircuitBreakerRegistry(threshold=3, cooldown=60, enabled=True)
    b = RedisCircuitBreakerRegistry(threshold=3, cooldown=60, enabled=True)
    a._redis = shared_redis
    b._redis = shared_redis
    return a, b


# ===================================================================
# 1. Multi-instance coordination — rate limiting
# ===================================================================


class TestMultiInstanceRateLimiting:
    """Instance A acquires rate limit tokens -> Instance B sees the updated count."""

    def test_shared_token_count(self, rate_limiter_pair):
        a, b = rate_limiter_pair
        # Instance A uses 3 tokens
        for _ in range(3):
            a.check_show("spine-01")

        # Instance B uses 2 more -> total 5 (at limit)
        for _ in range(2):
            b.check_show("spine-01")

        # Next request (6th) should be denied for both instances
        assert a.check_show("spine-01") is False
        assert b.check_show("spine-01") is False

    def test_different_hosts_independent(self, rate_limiter_pair):
        a, b = rate_limiter_pair
        # A uses all tokens on host-a
        for _ in range(6):
            a.check_show("host-a")
        # B can still use host-b
        assert b.check_show("host-b") is True

    def test_config_rate_shared(self, rate_limiter_pair):
        a, b = rate_limiter_pair
        # config_rate=1.0, so second call should be denied
        assert a.check_config("spine-01") is True
        assert b.check_config("spine-01") is False


# ===================================================================
# 2. Distributed cache consistency
# ===================================================================


class TestDistributedCacheConsistency:
    """Instance A caches data -> Instance B reads the same key -> same result."""

    def test_cache_read_by_other_instance(self, cache_pair):
        a, b = cache_pair
        data = {"status": "success", "data": [{"version": "4.28.3F"}]}
        a.put("spine-01", ["show version"], "json", data, 60)
        result = b.get("spine-01", ["show version"])
        assert result == data

    def test_cache_miss_before_put(self, cache_pair):
        _, b = cache_pair
        result = b.get("spine-01", ["show version"])
        assert result is None

    def test_invalidate_host_cross_instance(self, cache_pair):
        a, b = cache_pair
        data = {"status": "success", "data": "test"}
        a.put("spine-01", ["show version"], "json", data, 60)
        a.put("spine-01", ["show interfaces"], "json", data, 60)
        # Instance B invalidates
        removed = b.invalidate_host("spine-01")
        assert removed == 2
        # Instance A can no longer read
        assert a.get("spine-01", ["show version"]) is None

    def test_clear_cross_instance(self, cache_pair):
        a, b = cache_pair
        a.put("spine-01", ["show version"], "json", {"d": 1}, 60)
        b.put("leaf-01", ["show version"], "json", {"d": 2}, 60)
        b.clear()
        assert a.get("spine-01", ["show version"]) is None
        assert b.get("leaf-01", ["show version"]) is None


# ===================================================================
# 3. Leader election / distributed lock semantics
# ===================================================================


class TestDistributedLockSemantics:
    """Test exclusive access patterns via circuit breaker state.

    The circuit breaker registry serves as a distributed state store.
    We test that one instance's state changes are visible to the other.
    """

    def test_state_visible_cross_instance(self, circuit_breaker_pair):
        a, b = circuit_breaker_pair
        # Instance A records failures until circuit opens
        a.record_failure("spine-01")
        a.record_failure("spine-01")
        a.record_failure("spine-01")
        # Instance B sees the open circuit
        assert b.allow_request("spine-01") is False

    def test_success_resets_for_both(self, circuit_breaker_pair):
        a, b = circuit_breaker_pair
        # Open the circuit
        for _ in range(3):
            a.record_failure("spine-01")
        assert b.allow_request("spine-01") is False

        # Instance B records success (simulate half-open check succeeded)
        # First force state to half_open to allow record_success to reset
        import time

        state = {
            "state": "half_open",
            "failures": 3,
            "last_failure": time.time() - 120,
            "cooldown": 60,
            "threshold": 3,
        }
        b._redis.set("netmcp:circuit:spine-01", json.dumps(state))
        b.record_success("spine-01")
        # Instance A now sees it as healthy
        assert a.allow_request("spine-01") is True

    def test_setnx_style_exclusive_lock(self, shared_redis):
        """Simulate a simple distributed lock using Redis SET with NX semantics.

        While not a full leader election, this validates the pattern used by
        distributed lock algorithms.
        """
        # First caller gets the lock
        existing = shared_redis.get("netmcp:leader")
        assert existing is None
        shared_redis.set("netmcp:leader", "instance-a")

        # Second caller sees the lock is taken
        existing = shared_redis.get("netmcp:leader")
        assert existing == "instance-a"

        # First caller releases
        shared_redis.delete("netmcp:leader")
        existing = shared_redis.get("netmcp:leader")
        assert existing is None

        # Second caller acquires
        shared_redis.set("netmcp:leader", "instance-b")
        assert shared_redis.get("netmcp:leader") == "instance-b"


# ===================================================================
# 4. Concurrent writes
# ===================================================================


class TestConcurrentWrites:
    """10 threads simultaneously write to the same Redis key via the distributed
    backend. Verify no data loss (atomic operations produce consistent final state).
    """

    def test_concurrent_rate_limit_increments(self, shared_redis):
        """10 threads each call check_show once. The Redis counter should reach 10."""
        rl = RedisRateLimiter(show_rate=100.0)  # High limit so nothing is denied
        rl._redis = shared_redis

        errors: list[Exception] = []

        def worker():
            try:
                rl.check_show("spine-01")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # The counter key should have value "10"
        raw = shared_redis.get("netmcp:ratelimit:spine-01:show")
        assert int(raw) == 10

    def test_concurrent_cache_puts(self, shared_redis):
        """10 threads each put a different value for different hosts.
        All values must be retrievable.
        """
        cache = RedisCommandCache(enabled=True)
        cache._redis = shared_redis

        errors: list[Exception] = []

        def worker(idx):
            try:
                host = f"host-{idx}"
                data = {"idx": idx}
                cache.put(host, ["show version"], "json", data, 60)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        for i in range(10):
            result = cache.get(f"host-{i}", ["show version"])
            assert result == {"idx": i}

    def test_concurrent_circuit_breaker_failures(self, shared_redis):
        """10 threads record failures concurrently. Final failure count should be 10."""
        reg = RedisCircuitBreakerRegistry(threshold=20, cooldown=60, enabled=True)
        reg._redis = shared_redis

        errors: list[Exception] = []

        def worker():
            try:
                reg.record_failure("spine-01")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        state = json.loads(shared_redis.get("netmcp:circuit:spine-01"))
        assert state["failures"] == 10


# ===================================================================
# 5. Key namespace isolation
# ===================================================================


class TestKeyNamespaceIsolation:
    """Different key prefixes / tenants must not interfere with each other."""

    def test_cache_different_prefixes(self, shared_redis):
        tenant_a = RedisCommandCache(enabled=True, key_prefix="tenant_a:cache")
        tenant_b = RedisCommandCache(enabled=True, key_prefix="tenant_b:cache")
        tenant_a._redis = shared_redis
        tenant_b._redis = shared_redis

        data_a = {"tenant": "A", "version": "4.28"}
        data_b = {"tenant": "B", "version": "4.30"}

        tenant_a.put("spine-01", ["show version"], "json", data_a, 60)
        tenant_b.put("spine-01", ["show version"], "json", data_b, 60)

        assert tenant_a.get("spine-01", ["show version"]) == data_a
        assert tenant_b.get("spine-01", ["show version"]) == data_b

    def test_rate_limiter_different_prefixes(self, shared_redis):
        tenant_a = RedisRateLimiter(show_rate=2.0, key_prefix="tenant_a:rl")
        tenant_b = RedisRateLimiter(show_rate=2.0, key_prefix="tenant_b:rl")
        tenant_a._redis = shared_redis
        tenant_b._redis = shared_redis

        # Tenant A exhausts its limit
        tenant_a.check_show("spine-01")
        tenant_a.check_show("spine-01")
        assert tenant_a.check_show("spine-01") is False  # over limit

        # Tenant B still has its own independent limit
        assert tenant_b.check_show("spine-01") is True

    def test_circuit_breaker_different_prefixes(self, shared_redis):
        tenant_a = RedisCircuitBreakerRegistry(threshold=2, cooldown=60, enabled=True, key_prefix="tenant_a:cb")
        tenant_b = RedisCircuitBreakerRegistry(threshold=2, cooldown=60, enabled=True, key_prefix="tenant_b:cb")
        tenant_a._redis = shared_redis
        tenant_b._redis = shared_redis

        # Open circuit for tenant A
        tenant_a.record_failure("spine-01")
        tenant_a.record_failure("spine-01")
        assert tenant_a.allow_request("spine-01") is False

        # Tenant B's circuit is still healthy
        assert tenant_b.allow_request("spine-01") is True

    def test_cache_invalidate_only_affects_own_prefix(self, shared_redis):
        tenant_a = RedisCommandCache(enabled=True, key_prefix="tenant_a:cache")
        tenant_b = RedisCommandCache(enabled=True, key_prefix="tenant_b:cache")
        tenant_a._redis = shared_redis
        tenant_b._redis = shared_redis

        tenant_a.put("spine-01", ["show version"], "json", {"a": 1}, 60)
        tenant_b.put("spine-01", ["show version"], "json", {"b": 2}, 60)

        # Invalidate tenant A only
        tenant_a.invalidate_host("spine-01")
        assert tenant_a.get("spine-01", ["show version"]) is None
        assert tenant_b.get("spine-01", ["show version"]) == {"b": 2}

    def test_cache_clear_only_affects_own_prefix(self, shared_redis):
        tenant_a = RedisCommandCache(enabled=True, key_prefix="tenant_a:cache")
        tenant_b = RedisCommandCache(enabled=True, key_prefix="tenant_b:cache")
        tenant_a._redis = shared_redis
        tenant_b._redis = shared_redis

        tenant_a.put("spine-01", ["show version"], "json", {"a": 1}, 60)
        tenant_b.put("spine-01", ["show version"], "json", {"b": 2}, 60)

        tenant_a.clear()
        assert tenant_a.get("spine-01", ["show version"]) is None
        assert tenant_b.get("spine-01", ["show version"]) == {"b": 2}

    def test_same_prefix_same_namespace(self, shared_redis):
        """Two instances with the same prefix share the same data (expected behaviour)."""
        inst_a = RedisCommandCache(enabled=True, key_prefix="shared:cache")
        inst_b = RedisCommandCache(enabled=True, key_prefix="shared:cache")
        inst_a._redis = shared_redis
        inst_b._redis = shared_redis

        inst_a.put("spine-01", ["show version"], "json", {"shared": True}, 60)
        assert inst_b.get("spine-01", ["show version"]) == {"shared": True}
