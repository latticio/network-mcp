"""Tests for Redis-backed distributed circuit breaker."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.distributed.redis_circuit_breaker import RedisCircuitBreakerRegistry, _RedisCircuitBreakerView


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    r = MagicMock()
    r.from_url = MagicMock(return_value=r)
    return r


@pytest.fixture
def registry(mock_redis):
    """Create a RedisCircuitBreakerRegistry with mocked Redis."""
    reg = RedisCircuitBreakerRegistry(
        redis_url="redis://localhost:6379/0",
        threshold=3,
        cooldown=60,
        enabled=True,
    )
    reg._redis = mock_redis
    return reg


@pytest.fixture
def disabled_registry(mock_redis):
    """Create a disabled RedisCircuitBreakerRegistry."""
    reg = RedisCircuitBreakerRegistry(enabled=False)
    reg._redis = mock_redis
    return reg


def _make_state(state="healthy", failures=0, last_failure=0.0, cooldown=60, threshold=3):
    return json.dumps(
        {
            "state": state,
            "failures": failures,
            "last_failure": last_failure,
            "cooldown": cooldown,
            "threshold": threshold,
        }
    )


class TestRedisCircuitBreakerInit:
    def test_default_params(self):
        reg = RedisCircuitBreakerRegistry()
        assert reg._threshold == 3
        assert reg._base_cooldown == 60
        assert reg._enabled is False
        assert reg._redis is None

    def test_custom_params(self):
        reg = RedisCircuitBreakerRegistry(
            redis_url="redis://custom:6380/1",
            threshold=5,
            cooldown=120,
            enabled=True,
            key_prefix="custom:cb",
        )
        assert reg._threshold == 5
        assert reg._base_cooldown == 120
        assert reg._enabled is True
        assert reg._key_prefix == "custom:cb"

    def test_lazy_connection(self):
        mock_redis_mod = MagicMock()
        mock_client = MagicMock()
        mock_redis_mod.from_url.return_value = mock_client
        with patch.dict("sys.modules", {"redis": mock_redis_mod}):
            reg = RedisCircuitBreakerRegistry(redis_url="redis://test:6379/0")
            assert reg._redis is None
            reg._get_redis()
            mock_redis_mod.from_url.assert_called_once()


class TestRedisCircuitBreakerEnabled:
    def test_enabled_property(self, registry):
        assert registry.enabled is True

    def test_disabled_property(self, disabled_registry):
        assert disabled_registry.enabled is False


class TestRedisCircuitBreakerAllowRequest:
    def test_healthy_allows(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("healthy")
        assert registry.allow_request("spine-01") is True

    def test_open_denies(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("open", last_failure=time.time(), cooldown=60)
        assert registry.allow_request("spine-01") is False

    def test_half_open_allows(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("half_open")
        assert registry.allow_request("spine-01") is True

    def test_disabled_always_allows(self, disabled_registry, mock_redis):
        mock_redis.get.return_value = _make_state("open")
        assert disabled_registry.allow_request("spine-01") is True

    def test_missing_state_allows(self, registry, mock_redis):
        mock_redis.get.return_value = None
        assert registry.allow_request("spine-01") is True

    def test_redis_failure_fail_open(self, registry, mock_redis):
        mock_redis.get.side_effect = ConnectionError("Redis down")
        assert registry.allow_request("spine-01") is True

    def test_open_transitions_to_half_open_after_cooldown(self, registry, mock_redis):
        # Cooldown has elapsed
        mock_redis.get.return_value = _make_state("open", last_failure=time.time() - 120, cooldown=60)
        assert registry.allow_request("spine-01") is True
        # Verify state was written as half_open
        written = json.loads(mock_redis.set.call_args[0][1])
        assert written["state"] == "half_open"


class TestRedisCircuitBreakerRecordSuccess:
    def test_resets_to_healthy(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("half_open", failures=3)
        registry.record_success("spine-01")
        written = json.loads(mock_redis.set.call_args[0][1])
        assert written["state"] == "healthy"
        assert written["failures"] == 0
        assert written["cooldown"] == 60  # reset to base

    def test_noop_when_disabled(self, disabled_registry, mock_redis):
        disabled_registry.record_success("spine-01")
        mock_redis.get.assert_not_called()

    def test_redis_failure_silent(self, registry, mock_redis):
        mock_redis.get.side_effect = ConnectionError("Redis down")
        registry.record_success("spine-01")  # Should not raise


class TestRedisCircuitBreakerRecordFailure:
    def test_increments_failure_count(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("healthy", failures=1)
        registry.record_failure("spine-01")
        written = json.loads(mock_redis.set.call_args[0][1])
        assert written["failures"] == 2
        assert written["state"] == "healthy"  # Still below threshold

    def test_opens_circuit_at_threshold(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("healthy", failures=2)
        registry.record_failure("spine-01")
        written = json.loads(mock_redis.set.call_args[0][1])
        assert written["failures"] == 3
        assert written["state"] == "open"

    def test_half_open_reopens_with_doubled_cooldown(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("half_open", failures=3, cooldown=60)
        registry.record_failure("spine-01")
        written = json.loads(mock_redis.set.call_args[0][1])
        assert written["state"] == "open"
        assert written["cooldown"] == 120  # doubled

    def test_cooldown_capped_at_max(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("half_open", failures=3, cooldown=200)
        registry.record_failure("spine-01")
        written = json.loads(mock_redis.set.call_args[0][1])
        assert written["cooldown"] == 300  # capped at _MAX_COOLDOWN

    def test_noop_when_disabled(self, disabled_registry, mock_redis):
        disabled_registry.record_failure("spine-01")
        mock_redis.get.assert_not_called()

    def test_redis_failure_silent(self, registry, mock_redis):
        mock_redis.get.side_effect = ConnectionError("Redis down")
        registry.record_failure("spine-01")  # Should not raise


class TestRedisCircuitBreakerGet:
    def test_returns_view_object(self, registry):
        view = registry.get("spine-01")
        assert isinstance(view, _RedisCircuitBreakerView)
        assert view.host == "spine-01"

    def test_view_consecutive_failures(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("open", failures=5)
        view = registry.get("spine-01")
        assert view.consecutive_failures == 5


class TestRedisCircuitBreakerStats:
    def test_stats_with_devices(self, registry, mock_redis):
        mock_redis.scan.return_value = (0, ["netmcp:circuit:spine-01", "netmcp:circuit:leaf-01"])
        mock_redis.get.side_effect = [
            _make_state("healthy", failures=0),
            _make_state("open", failures=3, last_failure=time.time()),
        ]
        stats = registry.stats()
        assert stats["enabled"] is True
        assert stats["total_breakers"] == 2
        assert "leaf-01" in stats["open_circuits"]

    def test_stats_empty(self, registry, mock_redis):
        mock_redis.scan.return_value = (0, [])
        stats = registry.stats()
        assert stats["total_breakers"] == 0
        assert stats["open_circuits"] == []

    def test_stats_redis_failure(self, registry, mock_redis):
        mock_redis.scan.side_effect = ConnectionError("Redis down")
        stats = registry.stats()
        assert stats["total_breakers"] == 0


class TestRedisCircuitBreakerGetOpenCircuits:
    def test_returns_open_hosts(self, registry, mock_redis):
        mock_redis.scan.return_value = (0, ["netmcp:circuit:spine-01", "netmcp:circuit:leaf-01"])
        mock_redis.get.side_effect = [
            _make_state("healthy"),
            _make_state("open", last_failure=time.time()),
        ]
        result = registry.get_open_circuits()
        assert "leaf-01" in result
        assert "spine-01" not in result

    def test_redis_failure(self, registry, mock_redis):
        mock_redis.scan.side_effect = ConnectionError("Redis down")
        assert registry.get_open_circuits() == []


class TestRedisCircuitBreakerReset:
    def test_reset_single_host(self, registry, mock_redis):
        registry.reset("spine-01")
        mock_redis.delete.assert_called_once_with("netmcp:circuit:spine-01")

    def test_reset_all(self, registry, mock_redis):
        mock_redis.scan.return_value = (0, ["netmcp:circuit:h1", "netmcp:circuit:h2"])
        registry.reset()
        mock_redis.delete.assert_called_once()

    def test_reset_redis_failure(self, registry, mock_redis):
        mock_redis.delete.side_effect = ConnectionError("Redis down")
        registry.reset("spine-01")  # Should not raise


class TestRedisCircuitBreakerView:
    def test_allow_request(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("healthy")
        view = registry.get("spine-01")
        assert view.allow_request() is True

    def test_record_success(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("half_open", failures=3)
        view = registry.get("spine-01")
        view.record_success()
        written = json.loads(mock_redis.set.call_args[0][1])
        assert written["state"] == "healthy"

    def test_record_failure(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("healthy", failures=2)
        view = registry.get("spine-01")
        view.record_failure()
        written = json.loads(mock_redis.set.call_args[0][1])
        assert written["failures"] == 3

    def test_to_dict(self, registry, mock_redis):
        mock_redis.get.return_value = _make_state("healthy", failures=1)
        view = registry.get("spine-01")
        d = view.to_dict()
        assert d["host"] == "spine-01"
        assert d["state"] == "healthy"
        assert d["consecutive_failures"] == 1
