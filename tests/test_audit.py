"""Tests for audit logging."""

import json
from unittest.mock import patch

from network_mcp.audit import AuditLogger, log_tool_invocation


class TestAuditLogger:
    def test_log_tool_call(self, capfd):
        logger = AuditLogger(logger_name="test-audit-1")
        logger.log_tool_call(
            tool_name="get_device_info",
            device="spine-01",
            parameters={"host": "spine-01"},
            result_status="success",
            duration_ms=42.5,
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert data["event"] == "tool_invocation"
        assert data["tool"] == "get_device_info"
        assert data["device"] == "spine-01"
        assert data["result_status"] == "success"
        assert data["duration_ms"] == 42.5

    def test_log_tool_call_with_error(self, capfd):
        logger = AuditLogger(logger_name="test-audit-2")
        logger.log_tool_call(
            tool_name="get_device_info",
            device="spine-01",
            parameters={},
            result_status="error",
            duration_ms=10.0,
            error="Connection refused",
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert data["result_status"] == "error"
        assert data["error"] == "Connection refused"

    def test_redacts_sensitive_params(self, capfd):
        logger = AuditLogger(logger_name="test-audit-3")
        logger.log_tool_call(
            tool_name="test_tool",
            device="test",
            parameters={"password": "secret123", "host": "visible"},
            result_status="success",
            duration_ms=1.0,
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert data["parameters"]["password"] == "***REDACTED***"
        assert data["parameters"]["host"] == "visible"

    def test_redacts_commands(self, capfd):
        logger = AuditLogger(logger_name="test-audit-4")
        logger.log_tool_call(
            tool_name="push_config",
            device="test",
            parameters={"commands": ["username admin secret 0 mypassword"]},
            result_status="success",
            duration_ms=1.0,
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert "mypassword" not in str(data["parameters"]["commands"])


class TestLogToolInvocation:
    def test_convenience_function_success(self, capfd):
        # Use a fresh logger so capfd can capture its output
        fresh_logger = AuditLogger(logger_name="test-audit-conv-1")
        with patch("network_mcp.audit.audit_logger", fresh_logger):
            result = {"status": "success", "device": "test", "data": {}}
            log_tool_invocation("test_tool", "test-host", {}, result, 5.0)
        captured = capfd.readouterr()
        assert "test_tool" in captured.err

    def test_convenience_function_error(self, capfd):
        fresh_logger = AuditLogger(logger_name="test-audit-conv-2")
        with patch("network_mcp.audit.audit_logger", fresh_logger):
            result = {"status": "error", "device": "test", "error": "Connection refused"}
            log_tool_invocation("test_tool", "test-host", {}, result, 5.0)
        captured = capfd.readouterr()
        assert "Connection refused" in captured.err
