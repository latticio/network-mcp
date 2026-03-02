"""Tests for the distributed state factory functions."""

from unittest.mock import patch

from network_mcp.cache import CommandCache
from network_mcp.circuit_breaker import CircuitBreakerRegistry
from network_mcp.distributed.factory import create_cache, create_circuit_breaker, create_rate_limiter
from network_mcp.distributed.redis_cache import RedisCommandCache
from network_mcp.distributed.redis_circuit_breaker import RedisCircuitBreakerRegistry
from network_mcp.distributed.redis_rate_limiter import RedisRateLimiter
from network_mcp.rate_limiter import DeviceRateLimiter


class TestCreateRateLimiter:
    def test_local_backend(self):
        rl = create_rate_limiter(backend="local", show_rate=10.0, config_rate=2.0)
        assert isinstance(rl, DeviceRateLimiter)

    def test_redis_backend(self):
        rl = create_rate_limiter(backend="redis", redis_url="redis://test:6379/0")
        assert isinstance(rl, RedisRateLimiter)

    def test_local_ignores_redis_url(self):
        rl = create_rate_limiter(backend="local", redis_url="redis://test:6379/0", show_rate=5.0)
        assert isinstance(rl, DeviceRateLimiter)

    def test_default_is_local(self):
        rl = create_rate_limiter()
        assert isinstance(rl, DeviceRateLimiter)

    def test_redis_forwards_rates(self):
        rl = create_rate_limiter(backend="redis", redis_url="redis://test:6379/0", show_rate=20.0, config_rate=5.0)
        assert rl._show_rate == 20.0
        assert rl._config_rate == 5.0


class TestCreateCircuitBreaker:
    def test_local_backend(self):
        cb = create_circuit_breaker(backend="local", threshold=5, cooldown=120, enabled=True)
        assert isinstance(cb, CircuitBreakerRegistry)

    def test_redis_backend(self):
        cb = create_circuit_breaker(backend="redis", redis_url="redis://test:6379/0", enabled=True)
        assert isinstance(cb, RedisCircuitBreakerRegistry)

    def test_local_ignores_redis_url(self):
        cb = create_circuit_breaker(backend="local", redis_url="redis://test:6379/0", threshold=3)
        assert isinstance(cb, CircuitBreakerRegistry)

    def test_default_is_local(self):
        cb = create_circuit_breaker()
        assert isinstance(cb, CircuitBreakerRegistry)

    def test_redis_forwards_params(self):
        cb = create_circuit_breaker(
            backend="redis", redis_url="redis://test:6379/0", threshold=5, cooldown=120, enabled=True
        )
        assert cb._threshold == 5
        assert cb._base_cooldown == 120
        assert cb._enabled is True


class TestCreateCache:
    def test_local_backend(self):
        c = create_cache(backend="local", max_entries=1000, max_memory_mb=100, enabled=True)
        assert isinstance(c, CommandCache)

    def test_redis_backend(self):
        c = create_cache(backend="redis", redis_url="redis://test:6379/0", enabled=True)
        assert isinstance(c, RedisCommandCache)

    def test_local_ignores_redis_url(self):
        c = create_cache(backend="local", redis_url="redis://test:6379/0")
        assert isinstance(c, CommandCache)

    def test_default_is_local(self):
        c = create_cache()
        assert isinstance(c, CommandCache)

    def test_redis_forwards_params(self):
        c = create_cache(backend="redis", redis_url="redis://test:6379/0", max_entries=1000, enabled=True)
        assert c._max_entries == 1000
        assert c._enabled is True


class TestConfigIntegration:
    def test_config_settings_exist(self):
        """Verify that the distributed config fields exist on NetworkSettings."""
        from network_mcp.config import NetworkSettings

        s = NetworkSettings()
        assert hasattr(s, "net_distributed_backend")
        assert hasattr(s, "net_redis_url")
        assert s.net_distributed_backend == "local"
        assert s.net_redis_url == "redis://localhost:6379/0"

    @patch.dict("os.environ", {"NET_DISTRIBUTED_BACKEND": "redis", "NET_REDIS_URL": "redis://custom:6380/2"})
    def test_config_from_env(self):
        from network_mcp.config import NetworkSettings

        s = NetworkSettings()
        assert s.net_distributed_backend == "redis"
        assert s.net_redis_url == "redis://custom:6380/2"
