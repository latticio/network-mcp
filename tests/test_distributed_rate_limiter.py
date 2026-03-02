"""Tests for Redis-backed distributed rate limiter."""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.distributed.redis_rate_limiter import RedisRateLimiter


@pytest.fixture
def mock_redis():
    """Create a mock Redis client with pipeline support."""
    r = MagicMock()
    pipe = MagicMock()
    r.pipeline.return_value = pipe
    r.from_url = MagicMock(return_value=r)
    return r, pipe


@pytest.fixture
def limiter(mock_redis):
    """Create a RedisRateLimiter with mocked Redis."""
    r, pipe = mock_redis
    rl = RedisRateLimiter(redis_url="redis://localhost:6379/0", show_rate=5.0, config_rate=1.0)
    rl._redis = r
    return rl


class TestRedisRateLimiterInit:
    def test_default_params(self):
        rl = RedisRateLimiter()
        assert rl._redis_url == "redis://localhost:6379/0"
        assert rl._show_rate == 5.0
        assert rl._config_rate == 1.0
        assert rl._redis is None

    def test_custom_params(self):
        rl = RedisRateLimiter(
            redis_url="redis://custom:6380/1",
            show_rate=10.0,
            config_rate=2.0,
            show_burst=20.0,
            config_burst=4.0,
            key_prefix="custom:rl",
        )
        assert rl._redis_url == "redis://custom:6380/1"
        assert rl._show_rate == 10.0
        assert rl._config_rate == 2.0
        assert rl._key_prefix == "custom:rl"

    def test_lazy_connection(self):
        rl = RedisRateLimiter()
        assert rl._redis is None

    def test_get_redis_creates_connection(self):
        mock_redis_mod = MagicMock()
        mock_client = MagicMock()
        mock_redis_mod.from_url.return_value = mock_client
        with patch.dict("sys.modules", {"redis": mock_redis_mod}):
            rl = RedisRateLimiter(redis_url="redis://test:6379/0")
            result = rl._get_redis()
            mock_redis_mod.from_url.assert_called_once_with("redis://test:6379/0", decode_responses=True)
            assert result is mock_client

    def test_get_redis_reuses_connection(self):
        mock_redis_mod = MagicMock()
        mock_client = MagicMock()
        mock_redis_mod.from_url.return_value = mock_client
        with patch.dict("sys.modules", {"redis": mock_redis_mod}):
            rl = RedisRateLimiter()
            rl._get_redis()
            rl._get_redis()
            assert mock_redis_mod.from_url.call_count == 1


class TestRedisRateLimiterCheckShow:
    def test_allow_within_rate(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.return_value = [3, True]  # count=3, expire result
        assert limiter.check_show("spine-01") is True

    def test_deny_over_rate(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.return_value = [6, True]  # count=6 > show_rate=5
        assert limiter.check_show("spine-01") is False

    def test_at_exact_rate_allows(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.return_value = [5, True]  # count == show_rate
        assert limiter.check_show("spine-01") is True

    def test_first_request_allowed(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.return_value = [1, True]
        assert limiter.check_show("spine-01") is True

    def test_redis_failure_fail_open(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.side_effect = ConnectionError("Redis down")
        assert limiter.check_show("spine-01") is True


class TestRedisRateLimiterCheckConfig:
    def test_allow_within_rate(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.return_value = [1, True]  # count=1 <= config_rate=1
        assert limiter.check_config("spine-01") is True

    def test_deny_over_rate(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.return_value = [2, True]  # count=2 > config_rate=1
        assert limiter.check_config("spine-01") is False

    def test_redis_failure_fail_open(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.side_effect = ConnectionError("Redis down")
        assert limiter.check_config("spine-01") is True


class TestRedisRateLimiterWaitTime:
    def test_show_wait_time_with_ttl(self, limiter, mock_redis):
        r, _ = mock_redis
        r.ttl.return_value = 1
        assert limiter.show_wait_time("spine-01") == 1.0

    def test_show_wait_time_no_key(self, limiter, mock_redis):
        r, _ = mock_redis
        r.ttl.return_value = -2  # key doesn't exist
        assert limiter.show_wait_time("spine-01") == 1.0

    def test_config_wait_time_with_ttl(self, limiter, mock_redis):
        r, _ = mock_redis
        r.ttl.return_value = 1
        assert limiter.config_wait_time("spine-01") == 1.0

    def test_config_wait_time_redis_failure(self, limiter, mock_redis):
        r, _ = mock_redis
        r.ttl.side_effect = ConnectionError("Redis down")
        assert limiter.config_wait_time("spine-01") == 1.0


class TestRedisRateLimiterReset:
    def test_reset_single_host(self, limiter, mock_redis):
        r, _ = mock_redis
        limiter.reset("spine-01")
        assert r.delete.call_count == 2  # show + config keys

    def test_reset_all_hosts(self, limiter, mock_redis):
        r, _ = mock_redis
        r.scan.return_value = (0, ["netmcp:ratelimit:h1:show", "netmcp:ratelimit:h2:config"])
        limiter.reset()
        r.delete.assert_called_once()

    def test_reset_all_no_keys(self, limiter, mock_redis):
        r, _ = mock_redis
        r.scan.return_value = (0, [])
        limiter.reset()
        r.delete.assert_not_called()

    def test_reset_redis_failure(self, limiter, mock_redis):
        r, _ = mock_redis
        r.delete.side_effect = ConnectionError("Redis down")
        # Should not raise
        limiter.reset("spine-01")


class TestRedisRateLimiterKeyPrefix:
    def test_custom_key_prefix(self, mock_redis):
        r, pipe = mock_redis
        pipe.execute.return_value = [1, True]
        rl = RedisRateLimiter(key_prefix="prod:rl")
        rl._redis = r
        rl.check_show("spine-01")
        pipe.incr.assert_called_with("prod:rl:spine-01:show")

    def test_default_key_prefix(self, mock_redis):
        r, pipe = mock_redis
        pipe.execute.return_value = [1, True]
        rl = RedisRateLimiter()
        rl._redis = r
        rl.check_show("spine-01")
        pipe.incr.assert_called_with("netmcp:ratelimit:spine-01:show")


class TestRedisRateLimiterDifferentHosts:
    def test_independent_host_counters(self, limiter, mock_redis):
        _, pipe = mock_redis
        pipe.execute.return_value = [1, True]
        assert limiter.check_show("host-a") is True

        pipe.execute.return_value = [6, True]
        assert limiter.check_show("host-b") is False

        # Verify different keys were used
        calls = pipe.incr.call_args_list
        assert calls[-2][0][0] == "netmcp:ratelimit:host-a:show"
        assert calls[-1][0][0] == "netmcp:ratelimit:host-b:show"
