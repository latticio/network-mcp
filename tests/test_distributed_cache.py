"""Tests for Redis-backed distributed command cache."""

import json
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.distributed.redis_cache import RedisCommandCache


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    r = MagicMock()
    r.from_url = MagicMock(return_value=r)
    return r


@pytest.fixture
def cache(mock_redis):
    """Create a RedisCommandCache with mocked Redis."""
    c = RedisCommandCache(redis_url="redis://localhost:6379/0", enabled=True)
    c._redis = mock_redis
    return c


@pytest.fixture
def disabled_cache(mock_redis):
    """Create a disabled RedisCommandCache."""
    c = RedisCommandCache(enabled=False)
    c._redis = mock_redis
    return c


class TestRedisCommandCacheInit:
    def test_default_params(self):
        c = RedisCommandCache()
        assert c._redis_url == "redis://localhost:6379/0"
        assert c._enabled is True
        assert c._max_entries == 500
        assert c._max_memory_mb == 50
        assert c._redis is None

    def test_custom_params(self):
        c = RedisCommandCache(
            redis_url="redis://custom:6380/1",
            max_entries=1000,
            max_memory_mb=200,
            enabled=False,
            key_prefix="custom:cache",
        )
        assert c._redis_url == "redis://custom:6380/1"
        assert c._max_entries == 1000
        assert c._max_memory_mb == 200
        assert c._enabled is False
        assert c._key_prefix == "custom:cache"

    def test_lazy_connection(self):
        mock_redis_mod = MagicMock()
        mock_client = MagicMock()
        mock_redis_mod.from_url.return_value = mock_client
        with patch.dict("sys.modules", {"redis": mock_redis_mod}):
            c = RedisCommandCache(redis_url="redis://test:6379/0")
            assert c._redis is None
            c._get_redis()
            mock_redis_mod.from_url.assert_called_once()


class TestRedisCommandCacheEnabled:
    def test_enabled_property(self, cache):
        assert cache.enabled is True

    def test_disabled_property(self, disabled_cache):
        assert disabled_cache.enabled is False


class TestRedisCommandCacheGet:
    def test_cache_hit(self, cache, mock_redis):
        cached_data = {"status": "success", "device": "spine-01", "data": [{"version": "4.28"}]}
        mock_redis.get.return_value = json.dumps(cached_data)
        result = cache.get("spine-01", ["show version"])
        assert result == cached_data
        assert cache._hits == 1
        assert cache._misses == 0

    def test_cache_miss(self, cache, mock_redis):
        mock_redis.get.return_value = None
        result = cache.get("spine-01", ["show version"])
        assert result is None
        assert cache._hits == 0
        assert cache._misses == 1

    def test_disabled_returns_none(self, disabled_cache, mock_redis):
        result = disabled_cache.get("spine-01", ["show version"])
        assert result is None
        mock_redis.get.assert_not_called()

    def test_redis_failure_returns_none(self, cache, mock_redis):
        mock_redis.get.side_effect = ConnectionError("Redis down")
        result = cache.get("spine-01", ["show version"])
        assert result is None
        assert cache._misses == 1

    def test_correct_key_generated(self, cache, mock_redis):
        mock_redis.get.return_value = None
        cache.get("spine-01", ["show version", "show hostname"], "json")
        expected_key = "netmcp:cache:spine-01:show version|show hostname:json"
        mock_redis.get.assert_called_once_with(expected_key)


class TestRedisCommandCachePut:
    def test_stores_with_ttl(self, cache, mock_redis):
        data = {"status": "success", "data": [{"vlans": {}}]}
        cache.put("spine-01", ["show vlan"], "json", data, 30)
        expected_key = "netmcp:cache:spine-01:show vlan:json"
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args[0]
        assert call_args[0] == expected_key
        assert call_args[1] == 30
        assert json.loads(call_args[2]) == data

    def test_skip_when_ttl_zero(self, cache, mock_redis):
        cache.put("spine-01", ["show version"], "json", {}, 0)
        mock_redis.setex.assert_not_called()

    def test_skip_when_ttl_negative(self, cache, mock_redis):
        cache.put("spine-01", ["show version"], "json", {}, -1)
        mock_redis.setex.assert_not_called()

    def test_skip_when_disabled(self, disabled_cache, mock_redis):
        disabled_cache.put("spine-01", ["show version"], "json", {}, 30)
        mock_redis.setex.assert_not_called()

    def test_redis_failure_silent(self, cache, mock_redis):
        mock_redis.setex.side_effect = ConnectionError("Redis down")
        cache.put("spine-01", ["show version"], "json", {}, 30)  # Should not raise


class TestRedisCommandCacheInvalidateHost:
    def test_removes_host_keys(self, cache, mock_redis):
        mock_redis.scan.return_value = (0, ["netmcp:cache:spine-01:show version:json"])
        mock_redis.delete.return_value = 1
        result = cache.invalidate_host("spine-01")
        assert result == 1
        mock_redis.delete.assert_called_once()

    def test_no_keys_to_remove(self, cache, mock_redis):
        mock_redis.scan.return_value = (0, [])
        result = cache.invalidate_host("spine-01")
        assert result == 0
        mock_redis.delete.assert_not_called()

    def test_redis_failure(self, cache, mock_redis):
        mock_redis.scan.side_effect = ConnectionError("Redis down")
        result = cache.invalidate_host("spine-01")
        assert result == 0


class TestRedisCommandCacheClear:
    def test_clears_all_keys(self, cache, mock_redis):
        mock_redis.scan.return_value = (0, ["netmcp:cache:h1:cmd:json", "netmcp:cache:h2:cmd:json"])
        mock_redis.delete.return_value = 2
        cache._hits = 10
        cache._misses = 5
        result = cache.clear()
        assert result == 2
        assert cache._hits == 0
        assert cache._misses == 0

    def test_clear_empty_cache(self, cache, mock_redis):
        mock_redis.scan.return_value = (0, [])
        result = cache.clear()
        assert result == 0

    def test_redis_failure(self, cache, mock_redis):
        mock_redis.scan.side_effect = ConnectionError("Redis down")
        result = cache.clear()
        assert result == 0


class TestRedisCommandCacheStats:
    def test_stats_with_entries(self, cache, mock_redis):
        mock_redis.scan.return_value = (0, ["k1", "k2", "k3"])
        cache._hits = 10
        cache._misses = 5
        stats = cache.stats()
        assert stats["enabled"] is True
        assert stats["backend"] == "redis"
        assert stats["entries"] == 3
        assert stats["hits"] == 10
        assert stats["misses"] == 5
        assert stats["hit_rate"] == round(10 / 15, 3)

    def test_stats_empty_cache(self, cache, mock_redis):
        mock_redis.scan.return_value = (0, [])
        stats = cache.stats()
        assert stats["entries"] == 0
        assert stats["hit_rate"] == 0.0

    def test_stats_redis_failure(self, cache, mock_redis):
        mock_redis.scan.side_effect = ConnectionError("Redis down")
        stats = cache.stats()
        assert stats["entries"] == 0


class TestRedisCommandCacheEncoding:
    def test_text_encoding_key(self, cache, mock_redis):
        mock_redis.get.return_value = None
        cache.get("spine-01", ["show running-config"], "text")
        expected_key = "netmcp:cache:spine-01:show running-config:text"
        mock_redis.get.assert_called_once_with(expected_key)

    def test_different_encodings_different_keys(self, cache, mock_redis):
        mock_redis.get.return_value = None
        cache.get("spine-01", ["show version"], "json")
        cache.get("spine-01", ["show version"], "text")
        calls = [c[0][0] for c in mock_redis.get.call_args_list]
        assert calls[0] != calls[1]
        assert calls[0].endswith(":json")
        assert calls[1].endswith(":text")
