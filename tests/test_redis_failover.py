"""Tests for Redis failover behaviour — graceful degradation to local state.

Validates that each Redis-backed component (cache, rate limiter, circuit breaker)
handles Redis outages, timeouts, data corruption, and recovery without blocking
the main thread or losing data integrity.
"""

import json
import logging
from unittest.mock import MagicMock

import pytest

from network_mcp.distributed.redis_cache import RedisCommandCache
from network_mcp.distributed.redis_circuit_breaker import RedisCircuitBreakerRegistry
from network_mcp.distributed.redis_rate_limiter import RedisRateLimiter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis_client():
    """Create a bare mock Redis client."""
    r = MagicMock()
    pipe = MagicMock()
    r.pipeline.return_value = pipe
    return r, pipe


@pytest.fixture
def redis_cache(mock_redis_client):
    """RedisCommandCache with an injected mock Redis client."""
    r, _ = mock_redis_client
    cache = RedisCommandCache(enabled=True)
    cache._redis = r
    return cache


@pytest.fixture
def redis_rate_limiter(mock_redis_client):
    """RedisRateLimiter with an injected mock Redis client."""
    r, pipe = mock_redis_client
    rl = RedisRateLimiter(show_rate=5.0, config_rate=1.0)
    rl._redis = r
    return rl


@pytest.fixture
def redis_circuit_breaker(mock_redis_client):
    """RedisCircuitBreakerRegistry with an injected mock Redis client."""
    r, _ = mock_redis_client
    reg = RedisCircuitBreakerRegistry(threshold=3, cooldown=60, enabled=True)
    reg._redis = r
    return reg


# ===================================================================
# 1. Redis goes down during operation
# ===================================================================


class TestRedisGoesDownDuringOperation:
    """Mock Redis to raise ConnectionError mid-operation.

    The system must fall back to safe local defaults:
    - cache.get returns None (cache miss)
    - rate_limiter.check_show returns True (fail-open)
    - circuit_breaker.allow_request returns True (fail-open)
    """

    def test_cache_get_falls_back_on_connection_error(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.get.side_effect = ConnectionError("Redis down")
        result = redis_cache.get("spine-01", ["show version"])
        assert result is None
        assert redis_cache._misses == 1

    def test_cache_put_silent_on_connection_error(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.setex.side_effect = ConnectionError("Redis down")
        # Should not raise
        redis_cache.put("spine-01", ["show version"], "json", {"data": "v"}, 30)

    def test_rate_limiter_fail_open_on_connection_error(self, redis_rate_limiter, mock_redis_client):
        _, pipe = mock_redis_client
        pipe.execute.side_effect = ConnectionError("Redis down")
        assert redis_rate_limiter.check_show("spine-01") is True

    def test_rate_limiter_config_fail_open_on_connection_error(self, redis_rate_limiter, mock_redis_client):
        _, pipe = mock_redis_client
        pipe.execute.side_effect = ConnectionError("Redis down")
        assert redis_rate_limiter.check_config("spine-01") is True

    def test_circuit_breaker_fail_open_on_connection_error(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.get.side_effect = ConnectionError("Redis down")
        assert redis_circuit_breaker.allow_request("spine-01") is True

    def test_cache_get_logs_warning_on_failure(self, redis_cache, mock_redis_client, caplog):
        r, _ = mock_redis_client
        r.get.side_effect = ConnectionError("Redis down")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_cache.get("spine-01", ["show version"])
        assert any("Redis cache get failed" in rec.message for rec in caplog.records)

    def test_rate_limiter_logs_warning_on_failure(self, redis_rate_limiter, mock_redis_client, caplog):
        _, pipe = mock_redis_client
        pipe.execute.side_effect = ConnectionError("Redis down")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_rate_limiter.check_show("spine-01")
        assert any("Redis rate limiter unavailable" in rec.message for rec in caplog.records)

    def test_circuit_breaker_record_failure_silent(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.get.side_effect = ConnectionError("Redis down")
        redis_circuit_breaker.record_failure("spine-01")  # Should not raise

    def test_circuit_breaker_record_success_silent(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.get.side_effect = ConnectionError("Redis down")
        redis_circuit_breaker.record_success("spine-01")  # Should not raise


# ===================================================================
# 2. Redis recovers after outage
# ===================================================================


class TestRedisRecovery:
    """After a simulated failure, Redis succeeds again.

    Verify the system resumes normal operation with no stale state.
    """

    def test_cache_resumes_after_outage(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        # First: failure
        r.get.side_effect = ConnectionError("Redis down")
        result = redis_cache.get("spine-01", ["show version"])
        assert result is None

        # Second: recovery — return valid data
        cached = {"status": "success", "data": [{"version": "4.28"}]}
        r.get.side_effect = None
        r.get.return_value = json.dumps(cached)
        result = redis_cache.get("spine-01", ["show version"])
        assert result == cached
        assert redis_cache._hits == 1

    def test_rate_limiter_resumes_after_outage(self, redis_rate_limiter, mock_redis_client):
        _, pipe = mock_redis_client
        # First: failure — fail open
        pipe.execute.side_effect = ConnectionError("Redis down")
        assert redis_rate_limiter.check_show("spine-01") is True

        # Recovery
        pipe.execute.side_effect = None
        pipe.execute.return_value = [3, True]  # count 3, within rate
        assert redis_rate_limiter.check_show("spine-01") is True

    def test_rate_limiter_enforces_after_recovery(self, redis_rate_limiter, mock_redis_client):
        """After recovery, the rate limiter must enforce limits (no stale tokens)."""
        _, pipe = mock_redis_client
        # During outage — all requests pass (fail-open)
        pipe.execute.side_effect = ConnectionError("Redis down")
        assert redis_rate_limiter.check_show("spine-01") is True

        # After recovery — counter shows over-limit
        pipe.execute.side_effect = None
        pipe.execute.return_value = [6, True]  # over limit
        assert redis_rate_limiter.check_show("spine-01") is False

    def test_circuit_breaker_resumes_after_outage(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        # Failure — fail open
        r.get.side_effect = ConnectionError("Redis down")
        assert redis_circuit_breaker.allow_request("spine-01") is True

        # Recovery — healthy state
        r.get.side_effect = None
        r.get.return_value = json.dumps(
            {
                "state": "healthy",
                "failures": 0,
                "last_failure": 0.0,
                "cooldown": 60,
                "threshold": 3,
            }
        )
        assert redis_circuit_breaker.allow_request("spine-01") is True

    def test_cache_no_stale_entries_after_recovery(self, redis_cache, mock_redis_client):
        """After recovery, a cache miss returns None (not previously failed data)."""
        r, _ = mock_redis_client
        # Failure
        r.get.side_effect = ConnectionError("Redis down")
        redis_cache.get("spine-01", ["show version"])

        # Recovery — key expired while Redis was down
        r.get.side_effect = None
        r.get.return_value = None
        result = redis_cache.get("spine-01", ["show version"])
        assert result is None
        # Misses incremented for both failures
        assert redis_cache._misses == 2


# ===================================================================
# 3. Redis timeout
# ===================================================================


class TestRedisTimeout:
    """Mock Redis to raise TimeoutError on GET/SET.

    The system must fall back to safe defaults without blocking.
    """

    def test_cache_get_timeout_returns_none(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.get.side_effect = TimeoutError("Redis timeout")
        result = redis_cache.get("spine-01", ["show version"])
        assert result is None

    def test_cache_put_timeout_silent(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.setex.side_effect = TimeoutError("Redis timeout")
        redis_cache.put("spine-01", ["show version"], "json", {"data": "v"}, 30)

    def test_rate_limiter_timeout_fail_open(self, redis_rate_limiter, mock_redis_client):
        _, pipe = mock_redis_client
        pipe.execute.side_effect = TimeoutError("Redis timeout")
        assert redis_rate_limiter.check_show("spine-01") is True

    def test_circuit_breaker_timeout_fail_open(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.get.side_effect = TimeoutError("Redis timeout")
        assert redis_circuit_breaker.allow_request("spine-01") is True

    def test_cache_invalidate_host_timeout(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.scan.side_effect = TimeoutError("Redis timeout")
        result = redis_cache.invalidate_host("spine-01")
        assert result == 0

    def test_cache_clear_timeout(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.scan.side_effect = TimeoutError("Redis timeout")
        result = redis_cache.clear()
        assert result == 0

    def test_circuit_breaker_stats_timeout(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.scan.side_effect = TimeoutError("Redis timeout")
        stats = redis_circuit_breaker.stats()
        assert stats["total_breakers"] == 0

    def test_rate_limiter_wait_time_timeout(self, redis_rate_limiter, mock_redis_client):
        r, _ = mock_redis_client
        r.ttl.side_effect = TimeoutError("Redis timeout")
        assert redis_rate_limiter.show_wait_time("spine-01") == 1.0


# ===================================================================
# 4. Redis data corruption
# ===================================================================


class TestRedisDataCorruption:
    """Mock Redis GET to return invalid/unparseable data.

    The system must treat corrupted data as a cache miss or use defaults.
    """

    def test_cache_get_invalid_json_returns_none(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.get.return_value = "not-valid-json{{{{"
        result = redis_cache.get("spine-01", ["show version"])
        assert result is None
        assert redis_cache._misses == 1

    def test_cache_get_empty_string_returns_none(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.get.return_value = ""
        result = redis_cache.get("spine-01", ["show version"])
        # json.loads("") raises JSONDecodeError -> caught -> None
        assert result is None

    def test_circuit_breaker_corrupted_state_uses_defaults(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.get.return_value = "not-json"
        # _get_state catches the exception and returns default healthy state
        assert redis_circuit_breaker.allow_request("spine-01") is True

    def test_circuit_breaker_missing_fields_in_json(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        # Valid JSON but missing expected fields
        r.get.return_value = json.dumps({"unexpected": "data"})
        # _get_state returns what json.loads gives, _check_transition will KeyError -> caught at top level
        # allow_request has its own try/except, so it falls back to True (fail-open)
        assert redis_circuit_breaker.allow_request("spine-01") is True

    def test_cache_get_binary_data_returns_none(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.get.return_value = b"\x00\x01\x02\xff"
        result = redis_cache.get("spine-01", ["show version"])
        # Depending on decode_responses, this may be a string or bytes — both are invalid JSON
        assert result is None

    def test_circuit_breaker_stats_corrupted_data(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.scan.return_value = (0, ["netmcp:circuit:spine-01"])
        r.get.return_value = "not-json"
        # stats() has its own try/except
        stats = redis_circuit_breaker.stats()
        # Corrupted data — no valid breakers parsed
        assert stats["total_breakers"] == 0


# ===================================================================
# 5. Graceful degradation metrics
# ===================================================================


class TestGracefulDegradationMetrics:
    """When Redis fails, verify WARNING logs are emitted as fallback indicators.

    The Redis backends use logging.warning() as their degradation signal rather
    than a dedicated counter. This test verifies those log messages appear
    consistently for each component type.
    """

    def test_cache_get_failure_emits_warning(self, redis_cache, mock_redis_client, caplog):
        r, _ = mock_redis_client
        r.get.side_effect = ConnectionError("Redis unavailable")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_cache.get("spine-01", ["show version"])
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) >= 1
        assert any("Redis cache get failed" in w.message for w in warnings)

    def test_cache_put_failure_emits_warning(self, redis_cache, mock_redis_client, caplog):
        r, _ = mock_redis_client
        r.setex.side_effect = ConnectionError("Redis unavailable")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_cache.put("spine-01", ["show version"], "json", {"d": 1}, 30)
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) >= 1
        assert any("Redis cache put failed" in w.message for w in warnings)

    def test_rate_limiter_failure_emits_warning(self, redis_rate_limiter, mock_redis_client, caplog):
        _, pipe = mock_redis_client
        pipe.execute.side_effect = ConnectionError("Redis unavailable")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_rate_limiter.check_show("spine-01")
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) >= 1
        assert any("Redis rate limiter unavailable" in w.message for w in warnings)

    def test_circuit_breaker_failure_emits_warning(self, redis_circuit_breaker, mock_redis_client, caplog):
        r, _ = mock_redis_client
        r.get.side_effect = ConnectionError("Redis unavailable")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_circuit_breaker.record_failure("spine-01")
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) >= 1
        assert any("Redis circuit breaker" in w.message for w in warnings)

    def test_multiple_failures_emit_multiple_warnings(self, redis_cache, mock_redis_client, caplog):
        r, _ = mock_redis_client
        r.get.side_effect = ConnectionError("Redis unavailable")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_cache.get("spine-01", ["show version"])
            redis_cache.get("spine-01", ["show interfaces"])
            redis_cache.get("leaf-01", ["show version"])
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) == 3

    def test_cache_invalidate_failure_emits_warning(self, redis_cache, mock_redis_client, caplog):
        r, _ = mock_redis_client
        r.scan.side_effect = ConnectionError("Redis unavailable")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_cache.invalidate_host("spine-01")
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) >= 1

    def test_circuit_breaker_get_open_circuits_failure_emits_warning(
        self, redis_circuit_breaker, mock_redis_client, caplog
    ):
        r, _ = mock_redis_client
        r.scan.side_effect = ConnectionError("Redis unavailable")
        with caplog.at_level(logging.WARNING, logger="network-mcp"):
            redis_circuit_breaker.get_open_circuits()
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert len(warnings) >= 1


# ===================================================================
# 6. OSError / generic exceptions
# ===================================================================


class TestGenericExceptionHandling:
    """Ensure OSError and RuntimeError are also caught gracefully."""

    def test_cache_get_os_error(self, redis_cache, mock_redis_client):
        r, _ = mock_redis_client
        r.get.side_effect = OSError("Network unreachable")
        result = redis_cache.get("spine-01", ["show version"])
        assert result is None

    def test_rate_limiter_os_error(self, redis_rate_limiter, mock_redis_client):
        _, pipe = mock_redis_client
        pipe.execute.side_effect = OSError("Network unreachable")
        assert redis_rate_limiter.check_show("spine-01") is True

    def test_circuit_breaker_os_error(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.get.side_effect = OSError("Network unreachable")
        assert redis_circuit_breaker.allow_request("spine-01") is True

    def test_rate_limiter_reset_os_error(self, redis_rate_limiter, mock_redis_client):
        r, _ = mock_redis_client
        r.delete.side_effect = OSError("Network unreachable")
        redis_rate_limiter.reset("spine-01")  # Should not raise

    def test_circuit_breaker_reset_os_error(self, redis_circuit_breaker, mock_redis_client):
        r, _ = mock_redis_client
        r.delete.side_effect = OSError("Network unreachable")
        redis_circuit_breaker.reset("spine-01")  # Should not raise
