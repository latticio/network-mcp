"""Tests for deep health checks, liveness, and readiness endpoints."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.health import (
    _check_auth,
    _check_device,
    _check_redis,
    get_deep_health,
    get_liveness,
    get_readiness,
    init_health,
    mark_device_verified,
    mark_modules_loaded,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_health_state():
    """Reset health module state between tests."""
    import network_mcp.health as h

    h._modules_loaded = False
    h._device_verified = False
    h._server_start_time = time.monotonic()
    yield


@pytest.fixture()
def mock_settings():
    """Create a mock NetworkSettings for health check tests."""
    settings = MagicMock()
    settings.net_distributed_backend = "local"
    settings.net_redis_url = "redis://localhost:6379/0"
    settings.auth_enabled = False
    settings.auth_issuer_url = None
    settings.net_verify_connections = False
    return settings


@pytest.fixture()
def mock_conn_mgr():
    """Create a mock ConnectionManager for health check tests."""
    mgr = MagicMock()
    mgr.list_devices.return_value = ["spine-01", "leaf-01"]
    mock_driver = MagicMock()
    mock_driver.run_show.return_value = [{"hostname": "spine-01"}]
    mgr.get_node.return_value = mock_driver
    return mgr


# ---------------------------------------------------------------------------
# Liveness Tests
# ---------------------------------------------------------------------------


class TestLiveness:
    """Tests for the liveness endpoint."""

    def test_liveness_returns_alive(self):
        """Liveness always returns 200 with PID and uptime."""
        result = get_liveness()
        assert result["status"] == "alive"
        assert result["pid"] == os.getpid()
        assert isinstance(result["uptime_seconds"], float)
        assert result["uptime_seconds"] >= 0

    def test_liveness_tracks_uptime(self):
        """Liveness reports increasing uptime."""
        init_health(start_time=time.monotonic() - 100)
        result = get_liveness()
        assert result["uptime_seconds"] >= 99.0

    def test_liveness_never_fails(self):
        """Liveness must never raise an exception."""
        # Even with corrupted state, it should work
        init_health(start_time=0.0)
        result = get_liveness()
        assert result["status"] == "alive"
        assert result["pid"] > 0


# ---------------------------------------------------------------------------
# Readiness Tests
# ---------------------------------------------------------------------------


class TestReadiness:
    """Tests for the readiness endpoint."""

    def test_not_ready_before_module_loading(self, mock_conn_mgr, mock_settings):
        """Before modules load, readiness returns 503 + not_ready."""
        data, code = get_readiness(mock_conn_mgr, mock_settings)
        assert code == 503
        assert data["status"] == "not_ready"
        assert data["modules_loaded"] is False
        assert data["pool_initialized"] is True

    def test_ready_after_module_loading(self, mock_conn_mgr, mock_settings):
        """After modules load, readiness returns 200 + ready."""
        mark_modules_loaded()
        data, code = get_readiness(mock_conn_mgr, mock_settings)
        assert code == 200
        assert data["status"] == "ready"
        assert data["modules_loaded"] is True
        assert data["pool_initialized"] is True

    def test_not_ready_when_verify_connections_and_no_device_verified(self, mock_conn_mgr, mock_settings):
        """With NET_VERIFY_CONNECTIONS=true, not ready until a device is verified."""
        mark_modules_loaded()
        mock_settings.net_verify_connections = True
        data, code = get_readiness(mock_conn_mgr, mock_settings)
        assert code == 503
        assert data["status"] == "not_ready"
        assert data["device_verified"] is False

    def test_ready_when_verify_connections_and_device_verified(self, mock_conn_mgr, mock_settings):
        """With NET_VERIFY_CONNECTIONS=true and device verified, returns ready."""
        mark_modules_loaded()
        mark_device_verified()
        mock_settings.net_verify_connections = True
        data, code = get_readiness(mock_conn_mgr, mock_settings)
        assert code == 200
        assert data["status"] == "ready"
        assert data["device_verified"] is True

    def test_not_ready_when_conn_mgr_none(self, mock_settings):
        """If conn_mgr is None, pool_initialized is False."""
        mark_modules_loaded()
        data, code = get_readiness(None, mock_settings)
        assert code == 503
        assert data["status"] == "not_ready"
        assert data["pool_initialized"] is False


# ---------------------------------------------------------------------------
# Deep Health Tests
# ---------------------------------------------------------------------------


class TestDeepHealth:
    """Tests for the deep health check endpoint."""

    def test_all_checks_pass_healthy(self, mock_conn_mgr, mock_settings):
        """All checks pass -> 200 + healthy."""
        data, code = get_deep_health(mock_conn_mgr, mock_settings)
        assert code == 200
        assert data["status"] == "healthy"
        # Device check passed, Redis/auth skipped
        assert data["checks"]["device"]["status"] == "pass"
        assert data["checks"]["redis"]["status"] == "skip"
        assert data["checks"]["auth"]["status"] == "skip"
        assert "timestamp" in data

    def test_device_unreachable_redis_ok_degraded(self, mock_conn_mgr, mock_settings):
        """Device unreachable but Redis OK -> 200 + degraded."""
        # Device fails
        mock_conn_mgr.get_node.side_effect = ConnectionError("unreachable")
        # Enable Redis
        mock_settings.net_distributed_backend = "redis"

        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        with patch("network_mcp.health.redis", create=True) as mock_redis_module:
            # Patch the import inside _check_redis
            with patch.dict("sys.modules", {"redis": mock_redis_module}):
                mock_redis_module.from_url.return_value = mock_redis
                data, code = get_deep_health(mock_conn_mgr, mock_settings)

        assert code == 200
        assert data["status"] == "degraded"
        assert data["checks"]["device"]["status"] == "fail"
        assert data["checks"]["redis"]["status"] == "pass"

    def test_all_checks_fail_unhealthy(self, mock_conn_mgr, mock_settings):
        """All active checks fail -> 503 + unhealthy."""
        # Device fails
        mock_conn_mgr.get_node.side_effect = ConnectionError("unreachable")
        # Enable auth but make it fail
        mock_settings.auth_enabled = True
        mock_settings.auth_issuer_url = "https://auth.example.com"

        with patch("network_mcp.health.httpx", create=True) as mock_httpx:
            mock_httpx.head.side_effect = Exception("connection refused")
            data, code = get_deep_health(mock_conn_mgr, mock_settings)

        assert code == 503
        assert data["status"] == "unhealthy"
        assert data["checks"]["device"]["status"] == "fail"
        assert data["checks"]["auth"]["status"] == "fail"

    def test_redis_disabled_skipped(self, mock_conn_mgr, mock_settings):
        """Redis disabled -> Redis check skipped, only device check matters."""
        mock_settings.net_distributed_backend = "local"
        data, code = get_deep_health(mock_conn_mgr, mock_settings)
        assert code == 200
        assert data["status"] == "healthy"
        assert data["checks"]["redis"]["status"] == "skip"

    def test_no_devices_in_inventory(self, mock_conn_mgr, mock_settings):
        """No devices in inventory -> device check skipped."""
        mock_conn_mgr.list_devices.return_value = []
        data, code = get_deep_health(mock_conn_mgr, mock_settings)
        assert code == 200
        assert data["status"] == "healthy"
        assert data["checks"]["device"]["status"] == "skip"

    def test_all_checks_skipped_is_healthy(self, mock_settings):
        """When all checks are skipped, report healthy."""
        mock_conn_mgr_empty = MagicMock()
        mock_conn_mgr_empty.list_devices.return_value = []
        mock_settings.net_distributed_backend = "local"
        mock_settings.auth_enabled = False

        data, code = get_deep_health(mock_conn_mgr_empty, mock_settings)
        assert code == 200
        assert data["status"] == "healthy"

    def test_deep_health_timestamp_present(self, mock_conn_mgr, mock_settings):
        """Deep health response includes ISO timestamp."""
        data, code = get_deep_health(mock_conn_mgr, mock_settings)
        assert "timestamp" in data
        # Verify it's parseable as ISO
        from datetime import datetime

        datetime.fromisoformat(data["timestamp"])


# ---------------------------------------------------------------------------
# Individual check function tests
# ---------------------------------------------------------------------------


class TestCheckDevice:
    """Tests for _check_device()."""

    def test_device_reachable(self, mock_conn_mgr):
        result = _check_device(mock_conn_mgr)
        assert result["status"] == "pass"
        assert result["device"] in ["spine-01", "leaf-01"]

    def test_device_unreachable(self, mock_conn_mgr):
        mock_conn_mgr.get_node.side_effect = Exception("timeout")
        result = _check_device(mock_conn_mgr)
        assert result["status"] == "fail"
        assert "timeout" in result["error"]

    def test_no_devices(self, mock_conn_mgr):
        mock_conn_mgr.list_devices.return_value = []
        result = _check_device(mock_conn_mgr)
        assert result["status"] == "skip"


class TestCheckRedis:
    """Tests for _check_redis()."""

    def test_redis_disabled(self, mock_settings):
        mock_settings.net_distributed_backend = "local"
        result = _check_redis(mock_settings)
        assert result["status"] == "skip"

    def test_redis_ping_success(self, mock_settings):
        mock_settings.net_distributed_backend = "redis"
        mock_client = MagicMock()
        mock_client.ping.return_value = True

        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys

            sys.modules["redis"].from_url.return_value = mock_client
            result = _check_redis(mock_settings)

        assert result["status"] == "pass"

    def test_redis_connection_error(self, mock_settings):
        mock_settings.net_distributed_backend = "redis"

        mock_redis_module = MagicMock()
        mock_redis_module.from_url.side_effect = Exception("Connection refused")

        with patch.dict("sys.modules", {"redis": mock_redis_module}):
            result = _check_redis(mock_settings)

        assert result["status"] == "fail"
        assert "Connection refused" in result["error"]


class TestCheckAuth:
    """Tests for _check_auth()."""

    def test_auth_disabled(self, mock_settings):
        mock_settings.auth_enabled = False
        result = _check_auth(mock_settings)
        assert result["status"] == "skip"

    def test_auth_no_issuer_url(self, mock_settings):
        mock_settings.auth_enabled = True
        mock_settings.auth_issuer_url = None
        result = _check_auth(mock_settings)
        assert result["status"] == "fail"
        assert "AUTH_ISSUER_URL" in result["error"]

    def test_auth_reachable_with_httpx(self, mock_settings):
        mock_settings.auth_enabled = True
        mock_settings.auth_issuer_url = "https://auth.example.com"

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.dict("sys.modules", {"httpx": MagicMock()}):
            import sys

            sys.modules["httpx"].head.return_value = mock_response
            result = _check_auth(mock_settings)

        assert result["status"] == "pass"

    def test_auth_server_error(self, mock_settings):
        mock_settings.auth_enabled = True
        mock_settings.auth_issuer_url = "https://auth.example.com"

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.dict("sys.modules", {"httpx": MagicMock()}):
            import sys

            sys.modules["httpx"].head.return_value = mock_response
            result = _check_auth(mock_settings)

        assert result["status"] == "fail"
        assert "500" in result["error"]
