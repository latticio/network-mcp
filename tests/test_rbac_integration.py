"""End-to-end RBAC integration tests: JWT token -> middleware scope check -> RBAC authorization.

Verifies that middleware.py and rbac.py use the same scope namespace (network:*)
so that tokens actually pass authorization when both are enabled.

Also exercises the full pipeline: scope hierarchy, denial, anonymous access,
tenant isolation, and the _rbac_call_tool wrapper from server.py.
"""

import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from network_mcp.auth import JWT_AVAILABLE
from network_mcp.middleware import SCOPE_ADMIN, SCOPE_READ, SCOPE_WRITE, check_scope
from network_mcp.rbac import (
    ROLES,
    TenantDeviceFilter,
    check_authorization,
    expand_scopes,
    extract_tenant_id,
)

pytestmark = pytest.mark.skipif(not JWT_AVAILABLE, reason="PyJWT not installed")

if JWT_AVAILABLE:
    import jwt as pyjwt

# Test constants
TEST_SECRET = "rbac-integration-test-secret"
TEST_ISSUER = "https://auth.example.com"
TEST_AUDIENCE = "network-mcp"


def _make_jwt(scopes: str) -> str:
    """Create a signed JWT with the given space-separated scopes."""
    payload = {
        "sub": "test-user",
        "scope": scopes,
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return pyjwt.encode(payload, TEST_SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Helpers for claims construction
# ---------------------------------------------------------------------------


def _claims(scope_str: str = "", **extra: str) -> dict:
    """Build a minimal JWT claims dict with the given space-separated scopes."""
    claims: dict = {"scope": scope_str}
    claims.update(extra)
    return claims


def _admin_claims(**extra: str) -> dict:
    return _claims("network:admin", **extra)


def _write_claims(**extra: str) -> dict:
    return _claims("network:read network:write", **extra)


def _read_claims(**extra: str) -> dict:
    return _claims("network:read", **extra)


def _audit_claims(**extra: str) -> dict:
    return _claims("network:read network:audit", **extra)


# ---------------------------------------------------------------------------
# Original tests: middleware/RBAC alignment
# ---------------------------------------------------------------------------


class TestMiddlewareRBACAlignment:
    """Verify middleware scope constants match rbac.py scope strings."""

    def test_scope_constants_match_rbac_namespace(self):
        """Middleware SCOPE_READ/WRITE/ADMIN must use 'network:' prefix to match rbac.py."""
        assert SCOPE_READ == "network:read"
        assert SCOPE_WRITE == "network:write"
        assert SCOPE_ADMIN == "network:admin"

    def test_middleware_read_scope_recognized_by_rbac(self):
        """A token with network:read (from middleware) is accepted by rbac for read tools."""
        claims = {"scope": SCOPE_READ}
        authorized, _ = check_authorization("eos_get_vlans", claims)
        assert authorized is True

    def test_middleware_write_scope_recognized_by_rbac(self):
        """A token with network:write (from middleware) is accepted by rbac for write tools."""
        claims = {"scope": f"{SCOPE_READ} {SCOPE_WRITE}"}
        authorized, _ = check_authorization("eos_create_vlan", claims)
        assert authorized is True

    def test_middleware_admin_scope_recognized_by_rbac(self):
        """A token with network:admin (from middleware) is accepted by rbac for all tools."""
        claims = {"scope": SCOPE_ADMIN}
        for tool in ["eos_get_vlans", "eos_create_vlan", "eos_delete_vlan"]:
            authorized, _ = check_authorization(tool, claims)
            assert authorized is True, f"Admin scope should grant access to {tool}"


class TestEndToEndTokenFlow:
    """Full JWT -> AccessToken -> middleware check_scope -> rbac check_authorization."""

    def test_read_token_passes_read_tool(self):
        """JWT with network:read passes both middleware and RBAC for a read tool."""
        from network_mcp.auth import JWTTokenVerifier

        verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        token_str = _make_jwt("network:read")

        # Step 1: Verify JWT and extract scopes
        parsed = verifier.verify_token(token_str)
        assert parsed is not None
        assert "network:read" in parsed.scopes

        # Step 2: Middleware scope check (check_scope)
        scope_err = check_scope(parsed, SCOPE_READ)
        assert scope_err is None

        # Step 3: RBAC authorization
        claims = {"scope": " ".join(parsed.scopes)}
        authorized, _ = check_authorization("eos_get_vlans", claims)
        assert authorized is True

    def test_read_token_denied_for_write_tool(self):
        """JWT with network:read is denied by both middleware and RBAC for a write tool."""
        from network_mcp.auth import JWTTokenVerifier

        verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        token_str = _make_jwt("network:read")

        parsed = verifier.verify_token(token_str)
        assert parsed is not None

        # Middleware denies
        scope_err = check_scope(parsed, SCOPE_WRITE)
        assert scope_err is not None
        assert "Insufficient scope" in scope_err

        # RBAC also denies
        claims = {"scope": " ".join(parsed.scopes)}
        authorized, msg = check_authorization("eos_create_vlan", claims)
        assert authorized is False
        assert "network:write" in msg

    def test_admin_token_grants_all_access(self):
        """JWT with network:admin passes middleware and RBAC for read, write, and admin tools."""
        from network_mcp.auth import JWTTokenVerifier

        verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        token_str = _make_jwt("network:admin")

        parsed = verifier.verify_token(token_str)
        assert parsed is not None

        # Middleware allows read and write
        assert check_scope(parsed, SCOPE_READ) is None
        assert check_scope(parsed, SCOPE_WRITE) is None

        # RBAC allows read, write, and admin tools
        claims = {"scope": " ".join(parsed.scopes)}
        for tool in ["eos_get_vlans", "eos_create_vlan", "eos_delete_vlan"]:
            authorized, _ = check_authorization(tool, claims)
            assert authorized is True, f"Admin should access {tool}"

    def test_scope_expansion_consistency(self):
        """expand_scopes for network:admin includes all scopes middleware checks against."""
        expanded = expand_scopes({"network:admin"})
        assert SCOPE_READ in expanded
        assert SCOPE_WRITE in expanded
        assert SCOPE_ADMIN in expanded


# ---------------------------------------------------------------------------
# Test 1: Scope hierarchy — network:admin grants all
# ---------------------------------------------------------------------------


class TestScopeHierarchyAdminGrantsAll:
    """network:admin scope should grant access to read, write, and admin tools."""

    def test_admin_scope_grants_read_access(self):
        """network:admin scope should grant access to network:read tools."""
        authorized, msg = check_authorization("eos_get_vlans", _admin_claims())
        assert authorized is True
        assert "Authorized" in msg

    def test_admin_scope_grants_write_access(self):
        """network:admin scope should grant access to network:write tools."""
        authorized, msg = check_authorization("eos_create_vlan", _admin_claims())
        assert authorized is True

    def test_admin_scope_grants_admin_access(self):
        """network:admin scope should grant access to network:admin tools."""
        authorized, msg = check_authorization("eos_delete_checkpoint", _admin_claims())
        assert authorized is True

    def test_admin_scope_grants_rollback_access(self):
        """network:admin scope should grant access to rollback (admin) tools."""
        authorized, msg = check_authorization("eos_rollback_to_checkpoint", _admin_claims())
        assert authorized is True

    def test_admin_scope_grants_remove_access(self):
        """network:admin scope should grant access to remove (admin) tools."""
        authorized, msg = check_authorization("eos_remove_vlan", _admin_claims())
        assert authorized is True

    def test_admin_hierarchy_expands_correctly(self):
        """Expanding network:admin should produce admin + write + read."""
        expanded = expand_scopes({"network:admin"})
        assert expanded == {"network:admin", "network:write", "network:read"}

    def test_admin_scope_across_all_vendor_read_tools(self):
        """Admin can access read tools for all vendors."""
        read_tools = [
            "eos_get_vlans",
            "net_get_device_info",
            "nxos_get_vpc_status",
            "iosxe_get_interfaces",
            "junos_get_config",
        ]
        for tool in read_tools:
            authorized, _ = check_authorization(tool, _admin_claims())
            assert authorized is True, f"Admin should access read tool {tool}"

    def test_admin_scope_across_all_vendor_write_tools(self):
        """Admin can access write tools for all vendors."""
        write_tools = [
            "eos_create_vlan",
            "nxos_create_vrf",
            "iosxe_create_acl",
            "junos_create_policy",
        ]
        for tool in write_tools:
            authorized, _ = check_authorization(tool, _admin_claims())
            assert authorized is True, f"Admin should access write tool {tool}"

    def test_admin_scope_across_all_vendor_admin_tools(self):
        """Admin can access admin/destructive tools for all vendors."""
        admin_tools = [
            "eos_delete_vlan",
            "nxos_delete_vrf",
            "iosxe_delete_acl",
            "junos_delete_policy",
        ]
        for tool in admin_tools:
            authorized, _ = check_authorization(tool, _admin_claims())
            assert authorized is True, f"Admin should access admin tool {tool}"

    def test_admin_does_not_imply_audit(self):
        """network:admin does NOT imply network:audit (independent hierarchy)."""
        authorized, msg = check_authorization("eos_compliance_check", _admin_claims())
        assert authorized is False
        assert "network:audit" in msg


# ---------------------------------------------------------------------------
# Test 2: Scope denial
# ---------------------------------------------------------------------------


class TestScopeDenial:
    """Verify that insufficient scopes are correctly denied."""

    def test_read_scope_denied_write_tool(self):
        """network:read scope should be denied access to write tools."""
        authorized, msg = check_authorization("eos_create_vlan", _read_claims())
        assert authorized is False
        assert "network:write" in msg
        assert "eos_create_vlan" in msg

    def test_read_scope_denied_admin_tool(self):
        """network:read scope should be denied access to admin tools."""
        authorized, msg = check_authorization("eos_delete_checkpoint", _read_claims())
        assert authorized is False
        assert "network:admin" in msg

    def test_read_scope_denied_rollback(self):
        """network:read scope should be denied access to rollback (admin) tools."""
        authorized, msg = check_authorization("eos_rollback_to_checkpoint", _read_claims())
        assert authorized is False
        assert "network:admin" in msg

    def test_write_scope_denied_admin_tool(self):
        """network:write scope should be denied access to admin tools."""
        authorized, msg = check_authorization("eos_delete_vlan", _write_claims())
        assert authorized is False
        assert "network:admin" in msg

    def test_write_scope_denied_rollback(self):
        """network:write scope should be denied rollback access."""
        authorized, msg = check_authorization("eos_rollback_to_checkpoint", _write_claims())
        assert authorized is False

    def test_empty_scopes_denied_for_any_mapped_tool(self):
        """Empty scopes should be denied for every tool in TOOL_SCOPES."""
        claims = _claims("")
        for tool, expected_scope in [
            ("eos_get_vlans", "network:read"),
            ("eos_create_vlan", "network:write"),
            ("eos_delete_vlan", "network:admin"),
            ("eos_compliance_check", "network:audit"),
        ]:
            authorized, msg = check_authorization(tool, claims)
            assert authorized is False, f"Empty scopes should not access {tool}"
            assert expected_scope in msg

    def test_audit_scope_denied_write_tool(self):
        """network:audit scope alone should not grant write access."""
        claims = _claims("network:audit")
        authorized, msg = check_authorization("eos_create_vlan", claims)
        assert authorized is False

    def test_read_scope_denied_gnmi_set(self):
        """network:read should be denied gNMI set (write) access."""
        authorized, msg = check_authorization("eos_gnmi_set", _read_claims())
        assert authorized is False
        assert "network:write" in msg

    def test_denial_message_is_actionable(self):
        """Denial message should include tool name, required scope, and user's scopes."""
        authorized, msg = check_authorization("eos_create_vlan", _read_claims())
        assert not authorized
        assert "eos_create_vlan" in msg
        assert "network:write" in msg
        assert "network:read" in msg  # user's scope listed


# ---------------------------------------------------------------------------
# Test 3: Anonymous access when auth disabled
# ---------------------------------------------------------------------------


class TestAnonymousAccessAuthDisabled:
    """When AUTH_ENABLED=false, RBAC is not wired, so all tools are accessible."""

    def test_anonymous_access_unmatched_tool(self):
        """When AUTH_ENABLED=false, the _rbac_call_tool wrapper is never installed.

        Tools not matched by TOOL_SCOPES require no scope — always authorized.
        """
        authorized, msg = check_authorization("unknown_tool", {})
        assert authorized is True
        assert "No scope required" in msg

    def test_unmatched_tool_accessible_without_scopes(self):
        """A tool not in TOOL_SCOPES is accessible to anyone (even empty claims)."""
        authorized, msg = check_authorization("completely_custom_tool", _claims(""))
        assert authorized is True

    def test_unmatched_tool_accessible_with_any_scope(self):
        """A tool not in TOOL_SCOPES is accessible regardless of scopes."""
        authorized, msg = check_authorization("my_custom_tool", _admin_claims())
        assert authorized is True

    def test_rbac_check_authorization_is_pure_function(self):
        """check_authorization works in isolation without server state.

        When auth_enabled=False, the server module's conditional block never
        executes, so mcp.call_tool remains the original function. The RBAC
        functions themselves are stateless and testable in isolation.
        """
        from network_mcp.rbac import check_authorization as ca

        authorized, _ = ca("eos_get_vlans", _read_claims())
        assert authorized is True


# ---------------------------------------------------------------------------
# Test 4: Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    """Tenant ID in JWT should restrict device access via TenantDeviceFilter."""

    @pytest.fixture
    def inventory(self):
        """Sample inventory with tenant restrictions."""
        return {
            "spine-01": {"tenants": ["acme", "globex"]},
            "spine-02": {"tenants": ["acme"]},
            "leaf-01": {"tenants": ["globex"]},
            "shared-switch": {"tenants": []},  # accessible to all
            "legacy-device": {},  # no tenants key → accessible to all
        }

    @pytest.fixture
    def tenant_filter(self, inventory):
        return TenantDeviceFilter(inventory)

    def test_tenant_allowed_on_assigned_device(self, tenant_filter):
        """Tenant should be allowed on devices assigned to them."""
        assert tenant_filter.is_allowed("spine-01", "acme") is True
        assert tenant_filter.is_allowed("spine-01", "globex") is True
        assert tenant_filter.is_allowed("spine-02", "acme") is True

    def test_tenant_denied_on_unassigned_device(self, tenant_filter):
        """Tenant should be denied on devices NOT assigned to them."""
        assert tenant_filter.is_allowed("spine-02", "globex") is False
        assert tenant_filter.is_allowed("leaf-01", "acme") is False

    def test_shared_device_accessible_to_all_tenants(self, tenant_filter):
        """Devices with empty tenants list should be accessible to all."""
        assert tenant_filter.is_allowed("shared-switch", "acme") is True
        assert tenant_filter.is_allowed("shared-switch", "globex") is True
        assert tenant_filter.is_allowed("shared-switch", "unknown") is True

    def test_legacy_device_accessible_to_all_tenants(self, tenant_filter):
        """Devices without tenants key should be accessible to all."""
        assert tenant_filter.is_allowed("legacy-device", "acme") is True
        assert tenant_filter.is_allowed("legacy-device", "globex") is True

    def test_no_tenant_id_allows_all_devices(self, tenant_filter):
        """When tenant_id is None, all devices should be accessible."""
        assert tenant_filter.is_allowed("spine-01", None) is True
        assert tenant_filter.is_allowed("spine-02", None) is True
        assert tenant_filter.is_allowed("leaf-01", None) is True

    def test_unknown_device_accessible_to_all(self, tenant_filter):
        """Unknown device (not in inventory) should be accessible to all."""
        assert tenant_filter.is_allowed("unknown-device", "acme") is True

    def test_extract_tenant_id_explicit(self):
        """Extract tenant_id from explicit claim."""
        claims = {"tenant_id": "acme"}
        assert extract_tenant_id(claims) == "acme"

    def test_extract_tenant_id_auth0_org(self):
        """Extract tenant_id from Auth0 org_id claim."""
        claims = {"org_id": "org_12345"}
        assert extract_tenant_id(claims) == "org_12345"

    def test_extract_tenant_id_email_sub(self):
        """Extract tenant_id from email-based sub claim."""
        claims = {"sub": "admin@acme.com"}
        assert extract_tenant_id(claims) == "acme.com"

    def test_extract_tenant_id_none_when_missing(self):
        """Return None when no tenant info in claims."""
        claims = {"scope": "network:read"}
        assert extract_tenant_id(claims) is None

    def test_extract_tenant_id_priority(self):
        """Explicit tenant_id takes priority over org_id and sub."""
        claims = {
            "tenant_id": "explicit-tenant",
            "org_id": "org-tenant",
            "sub": "user@email-tenant.com",
        }
        assert extract_tenant_id(claims) == "explicit-tenant"

    def test_tenant_isolation_combined_with_scope_check(self, tenant_filter):
        """End-to-end: scope check + tenant isolation."""
        # Acme operator trying to read from acme's device → allowed
        claims = _read_claims(tenant_id="acme")
        authorized, _ = check_authorization("eos_get_vlans", claims)
        assert authorized is True
        assert tenant_filter.is_allowed("spine-01", "acme") is True

        # Acme operator trying to read from globex's device → scope OK but tenant denied
        authorized, _ = check_authorization("eos_get_vlans", claims)
        assert authorized is True  # scope check passes
        assert tenant_filter.is_allowed("leaf-01", "acme") is False  # tenant filter denies


# ---------------------------------------------------------------------------
# Test 5: _rbac_call_tool wrapper end-to-end
# ---------------------------------------------------------------------------


@dataclass
class MockMCPAccessToken:
    """Minimal stand-in for mcp.server.auth.provider.AccessToken."""

    token: str = "test-jwt-token"
    client_id: str = "test-client"
    scopes: list[str] | None = None
    expires_at: int | None = None

    def __post_init__(self):
        if self.scopes is None:
            self.scopes = ["network:read"]


class TestRbacCallToolWrapper:
    """Test the _rbac_call_tool wrapper from server.py end-to-end.

    Reconstructs the wrapper logic to exercise the full pipeline:
    get_access_token → check_authorization → tenant isolation → call_tool.
    """

    def _build_wrapper(self, *, tenant_filter=None):
        """Build a fresh _rbac_call_tool wrapper for testing.

        Reconstructs the wrapper from server.py without requiring the server
        module's settings to have RBAC enabled.
        """
        import network_mcp.rbac as rbac_mod
        from network_mcp.middleware import clear_request_context

        original_call_tool = AsyncMock(return_value=[{"status": "success"}])
        audit_logger = MagicMock()
        audit_logger.log_auth_failure = MagicMock()
        _tenant_filter = tenant_filter

        async def _rbac_call_tool(name, arguments):
            """Replica of server.py wrapper for isolated testing."""
            clear_request_context()
            try:
                token = _rbac_call_tool._get_token()
                if token is not None:
                    token_claims = {
                        "scope": " ".join(token.scopes) if token.scopes else "",
                        "client_id": token.client_id,
                    }
                    authorized, message = rbac_mod.check_authorization(name, token_claims)
                    if not authorized:
                        audit_logger.log_auth_failure(
                            tool_name=name,
                            reason=message,
                            user=token.client_id,
                            attempted_scope=rbac_mod.get_required_scope(name),
                            device=arguments.get("host") if arguments else None,
                        )
                        return [{"status": "error", "error": message}]

                    if _tenant_filter is not None:
                        tenant_id = rbac_mod.extract_tenant_id(token_claims)
                        host = arguments.get("host") if arguments else None
                        if host and not _tenant_filter.is_allowed(host, tenant_id):
                            msg = f"Tenant '{tenant_id}' does not have access to device '{host}'"
                            audit_logger.log_auth_failure(
                                tool_name=name,
                                reason=msg,
                                user=token.client_id,
                                device=host,
                            )
                            return [{"status": "error", "error": msg}]

                return await original_call_tool(name, arguments)
            finally:
                clear_request_context()

        _rbac_call_tool._get_token = MagicMock(return_value=None)
        _rbac_call_tool._original = original_call_tool
        _rbac_call_tool._audit_logger = audit_logger
        return _rbac_call_tool

    async def test_authorized_read_passes_through(self):
        """Authorized read call should pass through to original call_tool."""
        wrapper = self._build_wrapper()
        token = MockMCPAccessToken(scopes=["network:read"])
        wrapper._get_token.return_value = token

        result = await wrapper("eos_get_vlans", {"host": "spine-01"})

        assert result == [{"status": "success"}]
        wrapper._original.assert_awaited_once_with("eos_get_vlans", {"host": "spine-01"})

    async def test_authorized_admin_passes_through(self):
        """Admin-scoped token should access any tool."""
        wrapper = self._build_wrapper()
        token = MockMCPAccessToken(scopes=["network:admin"])
        wrapper._get_token.return_value = token

        result = await wrapper("eos_delete_vlan", {"host": "spine-01"})

        assert result == [{"status": "success"}]
        wrapper._original.assert_awaited_once()

    async def test_denied_returns_error_response(self):
        """Denied call should return error and NOT call original tool."""
        wrapper = self._build_wrapper()
        token = MockMCPAccessToken(scopes=["network:read"])
        wrapper._get_token.return_value = token

        result = await wrapper("eos_create_vlan", {"host": "spine-01"})

        assert len(result) == 1
        assert result[0]["status"] == "error"
        assert "network:write" in result[0]["error"]
        wrapper._original.assert_not_awaited()

    async def test_denied_logs_auth_failure(self):
        """Denied call should log an auth failure to audit logger."""
        wrapper = self._build_wrapper()
        token = MockMCPAccessToken(scopes=["network:read"], client_id="denied-client")
        wrapper._get_token.return_value = token

        await wrapper("eos_create_vlan", {"host": "spine-01"})

        wrapper._audit_logger.log_auth_failure.assert_called_once()
        call_kwargs = wrapper._audit_logger.log_auth_failure.call_args
        assert call_kwargs.kwargs["tool_name"] == "eos_create_vlan"
        assert call_kwargs.kwargs["user"] == "denied-client"
        assert call_kwargs.kwargs["device"] == "spine-01"

    async def test_no_token_passes_through(self):
        """When no token is present (stdio transport), call passes through."""
        wrapper = self._build_wrapper()
        wrapper._get_token.return_value = None

        result = await wrapper("eos_get_vlans", {"host": "spine-01"})

        assert result == [{"status": "success"}]
        wrapper._original.assert_awaited_once()

    async def test_tenant_isolation_allows_assigned_device(self):
        """Tenant filter should allow access to assigned devices."""
        inventory = {
            "spine-01": {"tenants": ["acme"]},
            "leaf-01": {"tenants": ["globex"]},
        }
        tenant_filter = TenantDeviceFilter(inventory)
        wrapper = self._build_wrapper(tenant_filter=tenant_filter)
        token = MockMCPAccessToken(scopes=["network:read"])
        wrapper._get_token.return_value = token

        with patch("network_mcp.rbac.extract_tenant_id", return_value="acme"):
            result = await wrapper("eos_get_vlans", {"host": "spine-01"})

        assert result == [{"status": "success"}]
        wrapper._original.assert_awaited_once()

    async def test_tenant_isolation_denies_unassigned_device(self):
        """Tenant filter should deny access to devices not assigned to tenant."""
        inventory = {
            "spine-01": {"tenants": ["acme"]},
            "leaf-01": {"tenants": ["globex"]},
        }
        tenant_filter = TenantDeviceFilter(inventory)
        wrapper = self._build_wrapper(tenant_filter=tenant_filter)
        token = MockMCPAccessToken(scopes=["network:read"], client_id="globex-client")
        wrapper._get_token.return_value = token

        with patch("network_mcp.rbac.extract_tenant_id", return_value="globex"):
            result = await wrapper("eos_get_vlans", {"host": "spine-01"})

        assert len(result) == 1
        assert result[0]["status"] == "error"
        assert "does not have access" in result[0]["error"]
        wrapper._original.assert_not_awaited()

    async def test_tenant_isolation_denial_logs_audit(self):
        """Tenant denial should log an auth failure."""
        inventory = {"spine-01": {"tenants": ["acme"]}}
        tenant_filter = TenantDeviceFilter(inventory)
        wrapper = self._build_wrapper(tenant_filter=tenant_filter)
        token = MockMCPAccessToken(scopes=["network:read"], client_id="bad-tenant-client")
        wrapper._get_token.return_value = token

        with patch("network_mcp.rbac.extract_tenant_id", return_value="evil-corp"):
            await wrapper("eos_get_vlans", {"host": "spine-01"})

        wrapper._audit_logger.log_auth_failure.assert_called_once()
        call_kwargs = wrapper._audit_logger.log_auth_failure.call_args
        assert call_kwargs.kwargs["device"] == "spine-01"
        assert call_kwargs.kwargs["user"] == "bad-tenant-client"

    async def test_context_cleared_on_success(self):
        """Request context should be cleared after successful call."""
        from network_mcp.middleware import get_current_user

        wrapper = self._build_wrapper()
        token = MockMCPAccessToken(scopes=["network:read"])
        wrapper._get_token.return_value = token

        await wrapper("eos_get_vlans", {"host": "spine-01"})

        assert get_current_user() == "anonymous"

    async def test_context_cleared_on_denial(self):
        """Request context should be cleared even when access is denied."""
        from network_mcp.middleware import get_current_user

        wrapper = self._build_wrapper()
        token = MockMCPAccessToken(scopes=["network:read"])
        wrapper._get_token.return_value = token

        await wrapper("eos_delete_vlan", {"host": "spine-01"})

        assert get_current_user() == "anonymous"

    async def test_write_scope_hierarchy_in_wrapper(self):
        """network:write scope should grant read access through the wrapper."""
        wrapper = self._build_wrapper()
        token = MockMCPAccessToken(scopes=["network:write"])
        wrapper._get_token.return_value = token

        result = await wrapper("eos_get_vlans", {"host": "spine-01"})

        assert result == [{"status": "success"}]
        wrapper._original.assert_awaited_once()


# ---------------------------------------------------------------------------
# Cross-cutting: scope format interoperability
# ---------------------------------------------------------------------------


class TestScopeFormatInteroperability:
    """Verify scope extraction works across OAuth2, Auth0, and list formats."""

    def test_oauth2_space_separated_scope(self):
        """Standard OAuth2 space-separated scope string."""
        claims = {"scope": "network:read network:write"}
        authorized, _ = check_authorization("eos_create_vlan", claims)
        assert authorized is True

    def test_auth0_permissions_list(self):
        """Auth0-style permissions list."""
        claims = {"permissions": ["network:read", "network:write"]}
        authorized, _ = check_authorization("eos_create_vlan", claims)
        assert authorized is True

    def test_scopes_list_format(self):
        """Custom JWT with scopes as a list."""
        claims = {"scopes": ["network:admin"]}
        authorized, _ = check_authorization("eos_delete_vlan", claims)
        assert authorized is True

    def test_mixed_scope_formats_merged(self):
        """When multiple scope formats are present, they should be merged."""
        claims = {
            "scope": "network:read",
            "permissions": ["network:write"],
        }
        authorized, _ = check_authorization("eos_create_vlan", claims)
        assert authorized is True

    def test_scope_hierarchy_with_auth0_format(self):
        """Admin scope in Auth0 format should still expand through hierarchy."""
        claims = {"permissions": ["network:admin"]}
        authorized, _ = check_authorization("eos_get_vlans", claims)
        assert authorized is True
        authorized, _ = check_authorization("eos_create_vlan", claims)
        assert authorized is True
        authorized, _ = check_authorization("eos_delete_vlan", claims)
        assert authorized is True


# ---------------------------------------------------------------------------
# Cross-cutting: role-based scenarios
# ---------------------------------------------------------------------------


class TestRoleBasedScenarios:
    """End-to-end scenarios using predefined ROLES."""

    def test_viewer_role_full_scenario(self):
        """Viewer can read, cannot write or delete."""
        claims = {"scope": " ".join(ROLES["viewer"])}
        assert check_authorization("eos_get_vlans", claims)[0] is True
        assert check_authorization("eos_list_config_sessions", claims)[0] is True
        assert check_authorization("net_get_device_info", claims)[0] is True
        assert check_authorization("eos_create_vlan", claims)[0] is False
        assert check_authorization("eos_delete_vlan", claims)[0] is False

    def test_operator_role_full_scenario(self):
        """Operator can read and write, cannot delete or audit."""
        claims = {"scope": " ".join(ROLES["operator"])}
        assert check_authorization("eos_get_vlans", claims)[0] is True
        assert check_authorization("eos_create_vlan", claims)[0] is True
        assert check_authorization("eos_configure_interface_description", claims)[0] is True
        assert check_authorization("eos_delete_vlan", claims)[0] is False
        assert check_authorization("eos_compliance_check", claims)[0] is False

    def test_admin_role_full_scenario(self):
        """Admin can read, write, and delete (but not audit)."""
        claims = {"scope": " ".join(ROLES["admin"])}
        assert check_authorization("eos_get_vlans", claims)[0] is True
        assert check_authorization("eos_create_vlan", claims)[0] is True
        assert check_authorization("eos_delete_vlan", claims)[0] is True
        assert check_authorization("eos_rollback_to_checkpoint", claims)[0] is True
        # Admin does NOT imply audit
        assert check_authorization("eos_compliance_check", claims)[0] is False

    def test_auditor_role_full_scenario(self):
        """Auditor can read and audit, cannot write or delete."""
        claims = {"scope": " ".join(ROLES["auditor"])}
        assert check_authorization("eos_get_vlans", claims)[0] is True
        assert check_authorization("eos_compliance_check", claims)[0] is True
        assert check_authorization("eos_create_vlan", claims)[0] is False
        assert check_authorization("eos_delete_vlan", claims)[0] is False
