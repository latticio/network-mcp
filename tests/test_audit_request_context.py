"""Tests for audit logging with request context enrichment."""

import json

from network_mcp.audit import AuditLogger
from network_mcp.middleware import clear_request_context, set_request_context


class TestAuditRequestContext:
    def test_audit_includes_request_id_when_context_set(self, capfd):
        logger = AuditLogger(logger_name="test-audit-ctx-1")
        try:
            request_id = set_request_context(user="admin", scopes=["network:read"], tenant_id="tenant-a")
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
            assert data["request_id"] == request_id
            assert data["user"] == "admin"
            assert data["tenant_id"] == "tenant-a"
        finally:
            clear_request_context()

    def test_audit_works_without_context(self, capfd):
        """Audit entries should work when no request context is set — auto-generates correlation ID."""
        clear_request_context()
        logger = AuditLogger(logger_name="test-audit-ctx-2")
        logger.log_tool_call(
            tool_name="get_vlans",
            device="leaf-01",
            parameters={"host": "leaf-01"},
            result_status="success",
            duration_ms=15.0,
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        # Correlation ID is auto-generated when no context is set
        assert data["request_id"] is not None
        assert isinstance(data["request_id"], str)
        assert data["correlation_id"] == data["request_id"]
        assert data["user"] == "anonymous"
        assert data["event"] == "tool_invocation"

    def test_audit_request_id_auto_generated_after_clear(self, capfd):
        """After clearing context, a new correlation ID is auto-generated."""
        try:
            set_request_context(user="admin", scopes=[])
            clear_request_context()
            logger = AuditLogger(logger_name="test-audit-ctx-3")
            logger.log_tool_call(
                tool_name="get_device_info",
                device="spine-01",
                parameters={},
                result_status="success",
                duration_ms=5.0,
            )
            captured = capfd.readouterr()
            log_line = captured.err.strip()
            data = json.loads(log_line)
            # Correlation ID is auto-generated after clearing context
            assert data["request_id"] is not None
            assert isinstance(data["request_id"], str)
            assert data["correlation_id"] == data["request_id"]
        finally:
            clear_request_context()

    def test_audit_explicit_user_overrides_context(self, capfd):
        """When user/tenant are explicitly passed, they should override context values."""
        try:
            set_request_context(user="context-user", scopes=[], tenant_id="context-tenant")
            logger = AuditLogger(logger_name="test-audit-ctx-4")
            logger.log_tool_call(
                tool_name="get_device_info",
                device="spine-01",
                parameters={},
                result_status="success",
                duration_ms=5.0,
                user="explicit-user",
                tenant_id="explicit-tenant",
            )
            captured = capfd.readouterr()
            log_line = captured.err.strip()
            data = json.loads(log_line)
            # Explicit values should override context
            assert data["user"] == "explicit-user"
            assert data["tenant_id"] == "explicit-tenant"
            # But request_id always comes from context
            assert data["request_id"] is not None
        finally:
            clear_request_context()
