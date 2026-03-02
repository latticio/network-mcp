"""Tests for the structured error code taxonomy and its integration with helpers."""

from contextlib import contextmanager
from unittest.mock import patch

import pyeapi.eapilib

from network_mcp.errors import ERROR_METADATA, ErrorCategory, ErrorCode, classify_exception, make_error_response
from network_mcp.helpers import handle_tool_errors, run_config_command, run_show_command
from network_mcp.observability import metrics_collector
from network_mcp.server import conn_mgr

# ---------------------------------------------------------------------------
# 1. ErrorCode enum coverage
# ---------------------------------------------------------------------------


class TestErrorCodeEnum:
    """All 13 error codes exist and have metadata entries."""

    def test_all_codes_have_metadata(self):
        for code in ErrorCode:
            assert code in ERROR_METADATA, f"{code} missing from ERROR_METADATA"

    def test_metadata_count_matches_enum(self):
        assert len(ERROR_METADATA) == len(ErrorCode)

    def test_all_categories_valid(self):
        for code, meta in ERROR_METADATA.items():
            assert meta["category"] in ErrorCategory, f"{code} has invalid category"

    def test_all_codes_have_required_fields(self):
        required = {"category", "is_retryable", "http_status", "retry_after_seconds", "description"}
        for code, meta in ERROR_METADATA.items():
            assert required.issubset(meta.keys()), f"{code} missing fields: {required - meta.keys()}"


# ---------------------------------------------------------------------------
# 2. make_error_response()
# ---------------------------------------------------------------------------


class TestMakeErrorResponse:
    """make_error_response returns correct dict structure."""

    def test_basic_structure(self):
        resp = make_error_response(ErrorCode.CONNECTION_ERROR, "spine-01", "something failed")
        assert resp["status"] == "error"
        assert resp["device"] == "spine-01"
        assert resp["error"] == "something failed"
        assert resp["error_code"] == "CONNECTION_ERROR"
        assert resp["error_category"] == "transient"
        assert resp["is_retryable"] is True

    def test_retry_after_present_for_retryable(self):
        resp = make_error_response(ErrorCode.TIMEOUT_ERROR, "leaf-01", "timed out")
        assert "retry_after_seconds" in resp
        assert resp["retry_after_seconds"] == 10

    def test_retry_after_absent_for_permanent(self):
        resp = make_error_response(ErrorCode.COMMAND_ERROR, "leaf-01", "bad command")
        assert "retry_after_seconds" not in resp

    def test_extra_kwargs_merged(self):
        resp = make_error_response(ErrorCode.RATE_LIMITED, "host-1", "slow down", custom_field="abc")
        assert resp["custom_field"] == "abc"

    def test_extra_kwargs_override_retry_after(self):
        resp = make_error_response(ErrorCode.RATE_LIMITED, "host-1", "slow down", retry_after_seconds=99)
        assert resp["retry_after_seconds"] == 99


# ---------------------------------------------------------------------------
# 3. classify_exception()
# ---------------------------------------------------------------------------


class TestClassifyException:
    """Each exception type maps to the expected ErrorCode."""

    def test_pyeapi_connection_error(self):
        exc = pyeapi.eapilib.ConnectionError("https", "conn refused")
        assert classify_exception(exc) == ErrorCode.CONNECTION_ERROR

    def test_pyeapi_command_error(self):
        exc = pyeapi.eapilib.CommandError(1000, "invalid command", commands=["show bogus"])
        assert classify_exception(exc) == ErrorCode.COMMAND_ERROR

    def test_timeout_error(self):
        assert classify_exception(TimeoutError("timed out")) == ErrorCode.TIMEOUT_ERROR

    def test_connection_refused_error(self):
        assert classify_exception(ConnectionRefusedError("refused")) == ErrorCode.DEVICE_UNREACHABLE

    def test_permission_error(self):
        assert classify_exception(PermissionError("denied")) == ErrorCode.AUTH_ERROR

    def test_fallback_internal_error(self):
        assert classify_exception(RuntimeError("oops")) == ErrorCode.INTERNAL_ERROR
        assert classify_exception(ValueError("bad")) == ErrorCode.INTERNAL_ERROR


# ---------------------------------------------------------------------------
# 4. Backward compatibility — error responses retain status/device/error
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Error responses retain the original status, device, and error fields."""

    def test_make_error_response_has_legacy_fields(self):
        resp = make_error_response(ErrorCode.INTERNAL_ERROR, "sw-01", "kaboom")
        assert resp["status"] == "error"
        assert resp["device"] == "sw-01"
        assert resp["error"] == "kaboom"

    def test_all_error_codes_produce_legacy_fields(self):
        for code in ErrorCode:
            resp = make_error_response(code, "host", "msg")
            assert resp["status"] == "error"
            assert resp["device"] == "host"
            assert resp["error"] == "msg"


# ---------------------------------------------------------------------------
# 5. is_retryable correctness
# ---------------------------------------------------------------------------


class TestRetryableClassification:
    """Retryable codes and non-retryable codes are correctly classified."""

    RETRYABLE = {
        ErrorCode.CONNECTION_ERROR,
        ErrorCode.TIMEOUT_ERROR,
        ErrorCode.RATE_LIMITED,
        ErrorCode.DEVICE_UNREACHABLE,
        ErrorCode.CIRCUIT_OPEN,
        ErrorCode.BULKHEAD_TIMEOUT,
        ErrorCode.INTERNAL_ERROR,
    }

    NOT_RETRYABLE = {
        ErrorCode.COMMAND_ERROR,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.RBAC_DENIED,
        ErrorCode.CONFIG_REJECTED,
        ErrorCode.READ_ONLY_MODE,
        ErrorCode.ACCESS_DENIED,
        ErrorCode.AUTH_ERROR,
        ErrorCode.OUTPUT_TOO_LARGE,
        ErrorCode.SERVER_SHUTTING_DOWN,
    }

    def test_retryable_codes(self):
        for code in self.RETRYABLE:
            meta = ERROR_METADATA[code]
            assert meta["is_retryable"] is True, f"{code} should be retryable"

    def test_non_retryable_codes(self):
        for code in self.NOT_RETRYABLE:
            meta = ERROR_METADATA[code]
            assert meta["is_retryable"] is False, f"{code} should not be retryable"

    def test_all_codes_covered(self):
        assert self.RETRYABLE | self.NOT_RETRYABLE == set(ErrorCode)


# ---------------------------------------------------------------------------
# 6. Error codes in run_show_command()
# ---------------------------------------------------------------------------


class TestRunShowCommandErrorCodes:
    """run_show_command() returns error_code in error responses."""

    def test_connection_error_has_error_code(self, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("https", "refused")

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with patch.object(conn_mgr, "acquire", side_effect=_mock_acquire):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])

        assert result["status"] == "error"
        assert result["error_code"] == "CONNECTION_ERROR"
        assert result["is_retryable"] is True
        assert result["device"] == "spine-01"

    def test_command_error_has_error_code(self, mock_node):
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

    def test_general_exception_has_error_code(self, mock_node):
        mock_node.run_commands.side_effect = RuntimeError("unexpected")

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with patch.object(conn_mgr, "acquire", side_effect=_mock_acquire):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])

        assert result["error_code"] == "INTERNAL_ERROR"
        assert result["is_retryable"] is True

    def test_circuit_breaker_open_has_error_code(self):
        from network_mcp.helpers import circuit_breaker_registry

        # Enable circuit breaker and force it open
        orig_enabled = circuit_breaker_registry._enabled
        circuit_breaker_registry._enabled = True
        try:
            breaker = circuit_breaker_registry.get("spine-01")
            for _ in range(breaker._threshold):
                breaker.record_failure()

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["error_code"] == "CIRCUIT_OPEN"
            assert result["is_retryable"] is True
        finally:
            circuit_breaker_registry._enabled = orig_enabled

    def test_rate_limited_has_error_code(self, mock_node):
        from network_mcp.helpers import device_rate_limiter

        # Exhaust the show rate limiter for this host
        with (
            patch.object(device_rate_limiter, "check_show", return_value=False),
            patch.object(device_rate_limiter, "check_show_with_backoff", return_value=False),
            patch.object(device_rate_limiter, "show_wait_time", return_value=1.5),
        ):
            result = run_show_command(conn_mgr, "spine-01", ["show version"])

        assert result["error_code"] == "RATE_LIMITED"
        assert result["is_retryable"] is True
        assert "retry_after_seconds" in result


# ---------------------------------------------------------------------------
# 7. Error codes in run_config_command()
# ---------------------------------------------------------------------------


class TestRunConfigCommandErrorCodes:
    """run_config_command() returns error_code in error responses."""

    def test_read_only_mode_has_error_code(self):
        with patch("network_mcp.helpers.check_read_only", return_value="Write operations are disabled."):
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")

        assert result["error_code"] == "READ_ONLY_MODE"
        assert result["is_retryable"] is False

    def test_deny_list_has_error_code(self):
        with patch("network_mcp.helpers.check_read_only", return_value=None):
            result = run_config_command(conn_mgr, "spine-01", ["reload"], "reload_device")

        assert result["error_code"] == "CONFIG_REJECTED"
        assert result["is_retryable"] is False

    def test_connection_error_has_error_code(self, mock_node):
        mock_node.config.side_effect = pyeapi.eapilib.ConnectionError("https", "refused")

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with (
            patch("network_mcp.helpers.check_read_only", return_value=None),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "create_vlan")

        assert result["error_code"] == "CONNECTION_ERROR"
        assert result["is_retryable"] is True

    def test_command_error_has_error_code(self, mock_node):
        mock_node.config.side_effect = pyeapi.eapilib.CommandError(1000, "invalid command", commands=["bad config"])

        @contextmanager
        def _mock_acquire(host):
            yield mock_node

        with (
            patch("network_mcp.helpers.check_read_only", return_value=None),
            patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
        ):
            result = run_config_command(conn_mgr, "spine-01", ["bad config"], "bad_action")

        assert result["error_code"] == "COMMAND_ERROR"
        assert result["is_retryable"] is False


# ---------------------------------------------------------------------------
# 8. Error codes in handle_tool_errors()
# ---------------------------------------------------------------------------


class TestHandleToolErrors:
    """handle_tool_errors() decorator includes error_code in error dicts."""

    def test_connection_error(self):
        @handle_tool_errors
        def my_tool(host):
            raise pyeapi.eapilib.ConnectionError("https", "refused")

        result = my_tool("spine-01")
        assert result["status"] == "error"
        assert result["error_code"] == "CONNECTION_ERROR"
        assert result["is_retryable"] is True
        assert result["device"] == "spine-01"

    def test_command_error(self):
        @handle_tool_errors
        def my_tool(host):
            raise pyeapi.eapilib.CommandError(1000, "bad cmd", commands=["show x"])

        result = my_tool("spine-01")
        assert result["error_code"] == "COMMAND_ERROR"
        assert result["is_retryable"] is False

    def test_timeout_error(self):
        @handle_tool_errors
        def my_tool(host):
            raise TimeoutError("timed out")

        result = my_tool("spine-01")
        assert result["error_code"] == "TIMEOUT_ERROR"
        assert result["is_retryable"] is True

    def test_general_exception(self):
        @handle_tool_errors
        def my_tool(host):
            raise RuntimeError("oops")

        result = my_tool("spine-01")
        assert result["error_code"] == "INTERNAL_ERROR"
        assert result["is_retryable"] is True

    def test_backward_compat_fields_preserved(self):
        @handle_tool_errors
        def my_tool(host):
            raise pyeapi.eapilib.ConnectionError("https", "refused")

        result = my_tool("leaf-01")
        assert result["status"] == "error"
        assert result["device"] == "leaf-01"
        assert "error" in result
        assert isinstance(result["error"], str)


# ---------------------------------------------------------------------------
# 9. retry_after_seconds presence
# ---------------------------------------------------------------------------


class TestRetryAfterSeconds:
    """retry_after_seconds is present for retryable errors, absent for permanent."""

    def test_retryable_has_retry_after(self):
        retryable_codes = [c for c in ErrorCode if ERROR_METADATA[c]["is_retryable"]]
        for code in retryable_codes:
            resp = make_error_response(code, "host", "msg")
            assert "retry_after_seconds" in resp, f"{code} should have retry_after_seconds"

    def test_permanent_has_no_retry_after(self):
        permanent_codes = [c for c in ErrorCode if not ERROR_METADATA[c]["is_retryable"]]
        for code in permanent_codes:
            resp = make_error_response(code, "host", "msg")
            assert "retry_after_seconds" not in resp, f"{code} should not have retry_after_seconds"


# ---------------------------------------------------------------------------
# 10. MetricsCollector error_code tracking
# ---------------------------------------------------------------------------


class TestMetricsCollectorErrorCode:
    """MetricsCollector.record_tool_call() tracks error_code when provided."""

    def test_error_code_tracked(self):
        metrics_collector.record_tool_call("some_tool", "error", 100.0, error_code="CONNECTION_ERROR")
        metrics = metrics_collector.get_metrics_json()
        assert "CONNECTION_ERROR" in metrics["error_types"]
        assert metrics["error_types"]["CONNECTION_ERROR"] == 1

    def test_error_code_increments(self):
        metrics_collector.record_tool_call("t1", "error", 50.0, error_code="TIMEOUT_ERROR")
        metrics_collector.record_tool_call("t2", "error", 60.0, error_code="TIMEOUT_ERROR")
        metrics = metrics_collector.get_metrics_json()
        assert metrics["error_types"]["TIMEOUT_ERROR"] == 2

    def test_no_error_code_no_tracking(self):
        metrics_collector.record_tool_call("t1", "success", 50.0)
        metrics = metrics_collector.get_metrics_json()
        assert metrics["error_types"] == {}

    def test_mixed_error_codes(self):
        metrics_collector.record_tool_call("t1", "error", 50.0, error_code="CONNECTION_ERROR")
        metrics_collector.record_tool_call("t2", "error", 60.0, error_code="COMMAND_ERROR")
        metrics_collector.record_tool_call("t3", "error", 70.0, error_code="CONNECTION_ERROR")
        metrics = metrics_collector.get_metrics_json()
        assert metrics["error_types"]["CONNECTION_ERROR"] == 2
        assert metrics["error_types"]["COMMAND_ERROR"] == 1
