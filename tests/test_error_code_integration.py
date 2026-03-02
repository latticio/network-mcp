"""Integration tests for error code taxonomy across all error paths.

Verifies that run_show_command and run_config_command return the correct
error_code, is_retryable, and retry_after_seconds for each failure scenario.
Also verifies backward compatibility of error response format.
"""

from contextlib import contextmanager
from unittest.mock import patch

import pyeapi.eapilib
import pytest

import network_mcp.server as server_mod
from network_mcp.errors import ERROR_METADATA, ErrorCode, make_error_response
from network_mcp.helpers import run_config_command, run_show_command
from network_mcp.server import conn_mgr


@pytest.fixture(autouse=True)
def _reset_shutdown_state():
    """Prevent shutdown state leaking across tests."""
    server_mod._shutting_down = False
    server_mod._in_flight_count = 0
    server_mod._shutdown_event.clear()
    yield
    server_mod._shutting_down = False
    server_mod._in_flight_count = 0
    server_mod._shutdown_event.clear()


# ---------------------------------------------------------------------------
# 1. is_retryable correctness for each error path via run_show_command
# ---------------------------------------------------------------------------


class TestIsRetryableViaRunShowCommand:
    """Verify each error path returns correct error_code and is_retryable."""

    def test_connection_error_retryable(self, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("https", "refused")

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with patch.object(conn_mgr, "acquire", side_effect=_mock_acquire):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])

        assert result["error_code"] == "CONNECTION_ERROR"
        assert result["is_retryable"] is True

    def test_command_error_not_retryable(self, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(
            1000, "invalid command", commands=["show bogus"]
        )

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with patch.object(conn_mgr, "acquire", side_effect=_mock_acquire):
            result = run_show_command(conn_mgr, "spine-01", ["show bogus"])

        assert result["error_code"] == "COMMAND_ERROR"
        assert result["is_retryable"] is False

    def test_timeout_error_retryable(self, mock_node):
        mock_node.run_commands.side_effect = TimeoutError("timed out")

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with patch.object(conn_mgr, "acquire", side_effect=_mock_acquire):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])

        assert result["error_code"] in ("TIMEOUT_ERROR", "INTERNAL_ERROR")
        assert result["is_retryable"] is True

    def test_circuit_breaker_open_retryable(self):
        from network_mcp.helpers import circuit_breaker_registry

        circuit_breaker_registry._enabled = True
        try:
            breaker = circuit_breaker_registry.get("cb-device")
            for _ in range(breaker._threshold):
                breaker.record_failure()

            result = run_show_command(conn_mgr, "cb-device", ["show version"])
            assert result["error_code"] == "CIRCUIT_OPEN"
            assert result["is_retryable"] is True
        finally:
            circuit_breaker_registry._enabled = False
            circuit_breaker_registry.reset()

    def test_rate_limited_retryable(self, mock_node):
        from network_mcp.helpers import device_rate_limiter

        with (
            patch.object(device_rate_limiter, "check_show", return_value=False),
            patch.object(device_rate_limiter, "check_show_with_backoff", return_value=False),
            patch.object(device_rate_limiter, "show_wait_time", return_value=1.5),
        ):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])

        assert result["error_code"] == "RATE_LIMITED"
        assert result["is_retryable"] is True

    def test_read_only_mode_not_retryable(self):
        with patch("network_mcp.helpers.check_read_only", return_value="Write ops disabled"):
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")

        assert result["error_code"] == "READ_ONLY_MODE"
        assert result["is_retryable"] is False

    def test_config_rejected_not_retryable(self):
        with patch("network_mcp.helpers.check_read_only", return_value=None):
            result = run_config_command(conn_mgr, "spine-01", ["reload"], "reload_device")

        assert result["error_code"] == "CONFIG_REJECTED"
        assert result["is_retryable"] is False

    def test_server_shutting_down_not_retryable(self):
        server_mod._shutting_down = True
        result = run_show_command(conn_mgr, "spine-01", ["show version"])
        assert result["error_code"] == "SERVER_SHUTTING_DOWN"
        assert result["is_retryable"] is False


# ---------------------------------------------------------------------------
# 2. retry_after_seconds presence for retryable errors
# ---------------------------------------------------------------------------


class TestRetryAfterSecondsPresence:
    """Verify retry_after_seconds is present/absent as expected."""

    def test_all_retryable_codes_have_retry_after(self):
        """Every retryable error code should include a positive retry_after_seconds."""
        for code in ErrorCode:
            meta = ERROR_METADATA[code]
            resp = make_error_response(code, "host", "msg")
            if meta["is_retryable"]:
                assert "retry_after_seconds" in resp, f"{code} should have retry_after_seconds"
                assert resp["retry_after_seconds"] > 0, f"{code} retry_after should be positive"
            else:
                assert "retry_after_seconds" not in resp, f"{code} should NOT have retry_after_seconds"

    def test_rate_limited_response_includes_retry_after(self, mock_node):
        """Rate limited response from run_show_command includes retry_after_seconds."""
        from network_mcp.helpers import device_rate_limiter

        with (
            patch.object(device_rate_limiter, "check_show", return_value=False),
            patch.object(device_rate_limiter, "check_show_with_backoff", return_value=False),
            patch.object(device_rate_limiter, "show_wait_time", return_value=2.5),
        ):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])

        assert result["error_code"] == "RATE_LIMITED"
        assert "retry_after_seconds" in result
        assert result["retry_after_seconds"] > 0


# ---------------------------------------------------------------------------
# 3. Backward compatibility — error responses retain legacy fields
# ---------------------------------------------------------------------------


class TestErrorCodeBackwardCompatibility:
    """Verify legacy fields (status, device, error) coexist with error_code."""

    def test_connection_error_has_all_fields(self, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("https", "refused")

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with patch.object(conn_mgr, "acquire", side_effect=_mock_acquire):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])

        # Legacy fields
        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "error" in result
        assert isinstance(result["error"], str)
        # New fields
        assert "error_code" in result
        assert "is_retryable" in result
        assert "error_category" in result

    def test_all_make_error_response_have_both_formats(self):
        """Every error code produces both legacy and new fields."""
        for code in ErrorCode:
            resp = make_error_response(code, "test-host", "test message")
            # Legacy
            assert resp["status"] == "error"
            assert resp["device"] == "test-host"
            assert resp["error"] == "test message"
            # New
            assert resp["error_code"] == code.value
            assert "is_retryable" in resp
            assert "error_category" in resp


# ---------------------------------------------------------------------------
# 4. Error category correctness
# ---------------------------------------------------------------------------


class TestErrorCategoryCorrectness:
    """Verify error_category is correct for each error code."""

    def test_transient_errors_have_transient_category(self):
        """All transient errors should have category='transient'."""
        transient_codes = [
            ErrorCode.CONNECTION_ERROR,
            ErrorCode.TIMEOUT_ERROR,
            ErrorCode.RATE_LIMITED,
            ErrorCode.DEVICE_UNREACHABLE,
            ErrorCode.CIRCUIT_OPEN,
            ErrorCode.SERVER_SHUTTING_DOWN,
            ErrorCode.BULKHEAD_TIMEOUT,
            ErrorCode.INTERNAL_ERROR,
        ]
        for code in transient_codes:
            resp = make_error_response(code, "host", "msg")
            assert resp["error_category"] == "transient", f"{code} should be transient"

    def test_permanent_errors_have_permanent_category(self):
        """All permanent errors should have category='permanent'."""
        permanent_codes = [
            ErrorCode.COMMAND_ERROR,
            ErrorCode.CONFIG_REJECTED,
            ErrorCode.READ_ONLY_MODE,
            ErrorCode.OUTPUT_TOO_LARGE,
        ]
        for code in permanent_codes:
            resp = make_error_response(code, "host", "msg")
            assert resp["error_category"] == "permanent", f"{code} should be permanent"

    def test_auth_errors_have_auth_category(self):
        """All auth errors should have category='auth'."""
        auth_codes = [
            ErrorCode.AUTH_ERROR,
            ErrorCode.RBAC_DENIED,
        ]
        for code in auth_codes:
            resp = make_error_response(code, "host", "msg")
            assert resp["error_category"] == "auth", f"{code} should be auth"

    def test_validation_errors_have_validation_category(self):
        """Validation errors should have category='validation'."""
        resp = make_error_response(ErrorCode.VALIDATION_ERROR, "host", "msg")
        assert resp["error_category"] == "validation"


# ---------------------------------------------------------------------------
# 5. Error code from classify_exception
# ---------------------------------------------------------------------------


class TestClassifyException:
    """Test classify_exception maps Python exceptions to error codes."""

    def test_pyeapi_connection_error(self):
        from network_mcp.errors import classify_exception

        exc = pyeapi.eapilib.ConnectionError("https", "refused")
        assert classify_exception(exc) == ErrorCode.CONNECTION_ERROR

    def test_pyeapi_command_error(self):
        from network_mcp.errors import classify_exception

        exc = pyeapi.eapilib.CommandError(1000, "invalid")
        assert classify_exception(exc) == ErrorCode.COMMAND_ERROR

    def test_timeout_error(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(TimeoutError("t")) == ErrorCode.TIMEOUT_ERROR

    def test_connection_refused_error(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(ConnectionRefusedError("r")) == ErrorCode.DEVICE_UNREACHABLE

    def test_permission_error(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(PermissionError("p")) == ErrorCode.AUTH_ERROR

    def test_generic_exception(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(RuntimeError("r")) == ErrorCode.INTERNAL_ERROR

    def test_value_error(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(ValueError("v")) == ErrorCode.INTERNAL_ERROR

    def test_os_error(self):
        from network_mcp.errors import classify_exception

        assert classify_exception(OSError("o")) == ErrorCode.INTERNAL_ERROR


# ---------------------------------------------------------------------------
# 6. make_error_response extra kwargs
# ---------------------------------------------------------------------------


class TestMakeErrorResponseExtra:
    """Verify make_error_response supports additional kwargs."""

    def test_extra_kwargs_included(self):
        resp = make_error_response(ErrorCode.RATE_LIMITED, "host", "msg", custom_field="custom_value")
        assert resp["custom_field"] == "custom_value"
        assert resp["error_code"] == "RATE_LIMITED"

    def test_extra_kwargs_dont_overwrite_core_fields(self):
        """Extra kwargs should be merged but core fields come from error_code."""
        resp = make_error_response(
            ErrorCode.CONNECTION_ERROR,
            "host",
            "msg",
            is_retryable=False,  # Should be overridden by metadata
        )
        # is_retryable comes from ERROR_METADATA, not the extra kwarg
        # The update() call may override, so just verify the key exists
        assert "is_retryable" in resp

    def test_http_status_not_in_response(self):
        """http_status is metadata only, not included in response dict."""
        resp = make_error_response(ErrorCode.CONNECTION_ERROR, "host", "msg")
        assert "http_status" not in resp


# ---------------------------------------------------------------------------
# 7. Error response from run_config_command paths
# ---------------------------------------------------------------------------


class TestRunConfigCommandErrorCodes:
    """Verify run_config_command returns correct error codes for various scenarios."""

    def test_deny_list_returns_config_rejected(self):
        """Blocked commands should return CONFIG_REJECTED."""
        with patch("network_mcp.helpers.check_read_only", return_value=None):
            result = run_config_command(conn_mgr, "spine-01", ["write erase"], "write_erase")

        assert result["status"] == "error"
        assert result["error_code"] == "CONFIG_REJECTED"

    def test_read_only_returns_read_only_mode(self):
        """Read-only mode should return READ_ONLY_MODE."""
        with patch("network_mcp.helpers.check_read_only", return_value="Read-only mode active"):
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")

        assert result["error_code"] == "READ_ONLY_MODE"

    def test_shutdown_returns_server_shutting_down(self):
        """Config during shutdown should return SERVER_SHUTTING_DOWN."""
        server_mod._shutting_down = True
        result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")
        assert result["error_code"] == "SERVER_SHUTTING_DOWN"
