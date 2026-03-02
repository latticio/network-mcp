"""Tests for multi-tenancy: tenant extraction from JWT and per-tenant audit logging."""

import json

from network_mcp.audit import AuditLogger
from network_mcp.middleware import (
    clear_request_context,
    get_current_tenant_id,
    set_request_context,
)
from network_mcp.rbac import extract_tenant_id

# --- extract_tenant_id ---


class TestExtractTenantId:
    """Test tenant extraction from JWT claims."""

    def test_explicit_tenant_id_claim(self):
        claims = {"tenant_id": "acme-corp", "sub": "admin@acme-corp.com"}
        assert extract_tenant_id(claims) == "acme-corp"

    def test_auth0_org_id_claim(self):
        claims = {"org_id": "org_abc123", "sub": "user@example.com"}
        assert extract_tenant_id(claims) == "org_abc123"

    def test_email_based_sub_claim(self):
        claims = {"sub": "admin@acme-corp.com"}
        assert extract_tenant_id(claims) == "acme-corp.com"

    def test_no_tenant_info_returns_none(self):
        claims = {"sub": "service-account", "scope": "network:read"}
        assert extract_tenant_id(claims) is None

    def test_empty_claims_returns_none(self):
        assert extract_tenant_id({}) is None

    def test_tenant_id_takes_priority_over_org_id(self):
        """Explicit tenant_id is checked before org_id."""
        claims = {"tenant_id": "explicit-tenant", "org_id": "org_123"}
        assert extract_tenant_id(claims) == "explicit-tenant"

    def test_org_id_takes_priority_over_sub(self):
        """org_id is checked before email-based sub."""
        claims = {"org_id": "org_xyz", "sub": "user@other-tenant.com"}
        assert extract_tenant_id(claims) == "org_xyz"

    def test_whitespace_tenant_id_ignored(self):
        claims = {"tenant_id": "   ", "sub": "user@fallback.com"}
        assert extract_tenant_id(claims) == "fallback.com"

    def test_whitespace_org_id_ignored(self):
        claims = {"org_id": "  ", "sub": "user@fallback.com"}
        assert extract_tenant_id(claims) == "fallback.com"

    def test_non_string_tenant_id_ignored(self):
        claims = {"tenant_id": 12345, "sub": "user@example.com"}
        assert extract_tenant_id(claims) == "example.com"

    def test_non_string_org_id_ignored(self):
        claims = {"org_id": ["org_list"], "sub": "user@example.com"}
        assert extract_tenant_id(claims) == "example.com"

    def test_sub_without_at_sign_returns_none(self):
        claims = {"sub": "plain-subject"}
        assert extract_tenant_id(claims) is None

    def test_tenant_id_stripped(self):
        claims = {"tenant_id": "  acme  "}
        assert extract_tenant_id(claims) == "acme"

    def test_sub_domain_stripped(self):
        claims = {"sub": "user@ example.com "}
        assert extract_tenant_id(claims) == "example.com"


# --- Middleware tenant context ---


class TestMiddlewareTenantContext:
    """Test tenant ID propagation through request context."""

    def test_set_and_get_tenant_id(self):
        set_request_context(user="admin@acme.com", scopes=["network:read"], tenant_id="acme")
        assert get_current_tenant_id() == "acme"
        clear_request_context()

    def test_default_tenant_id_is_none(self):
        clear_request_context()
        assert get_current_tenant_id() is None

    def test_clear_resets_tenant_id(self):
        set_request_context(user="admin", scopes=[], tenant_id="tenant-1")
        clear_request_context()
        assert get_current_tenant_id() is None

    def test_set_context_without_tenant_id(self):
        set_request_context(user="admin", scopes=["network:read"])
        assert get_current_tenant_id() is None
        clear_request_context()


# --- Audit log tenant_id ---


class TestAuditTenantId:
    """Test that audit log entries include tenant_id."""

    def test_audit_log_includes_tenant_id(self, capfd):
        logger = AuditLogger(logger_name="test-audit-tenant-1")
        logger.log_tool_call(
            tool_name="eos_get_vlans",
            device="spine-01",
            parameters={"host": "spine-01"},
            result_status="success",
            duration_ms=15.0,
            user="admin@acme-corp.com",
            tenant_id="acme-corp",
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert data["tenant_id"] == "acme-corp"
        assert data["user"] == "admin@acme-corp.com"

    def test_audit_log_tenant_id_none_single_tenant(self, capfd):
        logger = AuditLogger(logger_name="test-audit-tenant-2")
        logger.log_tool_call(
            tool_name="eos_get_vlans",
            device="spine-01",
            parameters={"host": "spine-01"},
            result_status="success",
            duration_ms=10.0,
            user="admin",
            tenant_id=None,
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert data["tenant_id"] is None

    def test_audit_log_picks_up_tenant_from_context(self, capfd):
        """When tenant_id is not passed explicitly, it's read from request context."""
        set_request_context(user="operator@tenant-x.com", scopes=["network:read"], tenant_id="tenant-x")
        try:
            logger = AuditLogger(logger_name="test-audit-tenant-3")
            logger.log_tool_call(
                tool_name="eos_get_device_info",
                device="leaf-01",
                parameters={"host": "leaf-01"},
                result_status="success",
                duration_ms=20.0,
            )
            captured = capfd.readouterr()
            log_line = captured.err.strip()
            data = json.loads(log_line)
            assert data["tenant_id"] == "tenant-x"
            assert data["user"] == "operator@tenant-x.com"
        finally:
            clear_request_context()

    def test_audit_log_no_context_tenant_is_none(self, capfd):
        """Without auth context, tenant_id defaults to None."""
        clear_request_context()
        logger = AuditLogger(logger_name="test-audit-tenant-4")
        logger.log_tool_call(
            tool_name="eos_get_vlans",
            device="spine-01",
            parameters={},
            result_status="success",
            duration_ms=5.0,
        )
        captured = capfd.readouterr()
        log_line = captured.err.strip()
        data = json.loads(log_line)
        assert data["tenant_id"] is None
