"""Tests for RBAC (Role-Based Access Control) module."""

from network_mcp.rbac import (
    ROLES,
    TOOL_SCOPES,
    check_authorization,
    expand_scopes,
    extract_scopes_from_token,
    get_required_scope,
)

# --- get_required_scope ---


class TestGetRequiredScope:
    """Test tool name -> scope mapping via glob patterns."""

    def test_eos_get_returns_read(self):
        assert get_required_scope("eos_get_vlans") == "network:read"

    def test_eos_get_interface_detail_returns_read(self):
        assert get_required_scope("eos_get_interface_detail") == "network:read"

    def test_eos_list_returns_read(self):
        assert get_required_scope("eos_list_config_sessions") == "network:read"

    def test_eos_validate_returns_read(self):
        assert get_required_scope("eos_validate_bgp") == "network:read"

    def test_eos_diagnose_returns_read(self):
        assert get_required_scope("eos_diagnose_bgp_session") == "network:read"

    def test_eos_create_returns_write(self):
        assert get_required_scope("eos_create_vlan") == "network:write"

    def test_eos_configure_returns_write(self):
        assert get_required_scope("eos_configure_interface_description") == "network:write"

    def test_eos_push_returns_write(self):
        assert get_required_scope("eos_push_config_commands") == "network:write"

    def test_eos_commit_returns_write(self):
        assert get_required_scope("eos_commit_config_session") == "network:write"

    def test_eos_save_returns_write(self):
        assert get_required_scope("eos_save_running_config") == "network:write"

    def test_eos_delete_returns_admin(self):
        assert get_required_scope("eos_delete_vlan") == "network:admin"

    def test_eos_delete_checkpoint_returns_admin(self):
        assert get_required_scope("eos_delete_config_checkpoint") == "network:admin"

    def test_eos_rollback_returns_admin(self):
        assert get_required_scope("eos_rollback_to_checkpoint") == "network:admin"

    def test_eos_compliance_returns_audit(self):
        assert get_required_scope("eos_compliance_check") == "network:audit"

    def test_net_get_returns_read(self):
        assert get_required_scope("net_get_device_info") == "network:read"

    def test_net_diagnose_returns_read(self):
        assert get_required_scope("net_diagnose_bgp_session") == "network:read"

    def test_net_troubleshoot_returns_read(self):
        assert get_required_scope("net_troubleshoot_interface") == "network:read"

    def test_net_validate_returns_read(self):
        assert get_required_scope("net_validate_fabric") == "network:read"

    def test_nxos_get_returns_read(self):
        assert get_required_scope("nxos_get_vpc_status") == "network:read"

    def test_eos_gnmi_get_returns_read(self):
        assert get_required_scope("eos_gnmi_get") == "network:read"

    def test_eos_gnmi_set_returns_write(self):
        assert get_required_scope("eos_gnmi_set") == "network:write"

    def test_eos_shutdown_returns_write(self):
        assert get_required_scope("eos_shutdown_interface") == "network:write"

    def test_eos_no_shutdown_returns_write(self):
        assert get_required_scope("eos_no_shutdown_interface") == "network:write"

    def test_eos_ping_returns_read(self):
        assert get_required_scope("eos_ping_from_device") == "network:read"

    def test_eos_export_returns_read(self):
        assert get_required_scope("eos_export_report") == "network:read"

    def test_unknown_tool_returns_none(self):
        assert get_required_scope("unknown_tool") is None

    def test_completely_unmatched_returns_none(self):
        assert get_required_scope("some_random_function") is None

    def test_eos_load_tool_category_returns_read(self):
        assert get_required_scope("eos_load_tool_category") == "network:read"

    def test_eos_cache_clear_returns_read(self):
        assert get_required_scope("eos_cache_clear") == "network:read"

    def test_eos_server_health_returns_read(self):
        assert get_required_scope("eos_server_health") == "network:read"

    def test_generic_create_pattern(self):
        """Generic *_create_* pattern catches vendor tools without explicit mapping."""
        assert get_required_scope("vendor_create_something") == "network:write"

    def test_generic_delete_pattern(self):
        """Generic *_delete_* pattern catches vendor tools without explicit mapping."""
        assert get_required_scope("vendor_delete_something") == "network:admin"


# --- extract_scopes_from_token ---


class TestExtractScopesFromToken:
    """Test JWT scope extraction from various claim formats."""

    def test_oauth2_scope_string(self):
        claims = {"scope": "network:read network:write"}
        result = extract_scopes_from_token(claims)
        assert result == {"network:read", "network:write"}

    def test_scopes_list(self):
        claims = {"scopes": ["network:read"]}
        result = extract_scopes_from_token(claims)
        assert result == {"network:read"}

    def test_permissions_list(self):
        claims = {"permissions": ["network:read", "network:audit"]}
        result = extract_scopes_from_token(claims)
        assert result == {"network:read", "network:audit"}

    def test_empty_scope_string(self):
        claims = {"scope": ""}
        result = extract_scopes_from_token(claims)
        assert result == set()

    def test_whitespace_scope_string(self):
        claims = {"scope": "   "}
        result = extract_scopes_from_token(claims)
        assert result == set()

    def test_missing_all_claims(self):
        claims = {"sub": "user@example.com"}
        result = extract_scopes_from_token(claims)
        assert result == set()

    def test_multiple_claim_formats_merged(self):
        """All claim formats are merged when present."""
        claims = {
            "scope": "network:read",
            "scopes": ["network:write"],
            "permissions": ["network:audit"],
        }
        result = extract_scopes_from_token(claims)
        assert result == {"network:read", "network:write", "network:audit"}

    def test_non_string_scope_ignored(self):
        claims = {"scope": 12345}
        result = extract_scopes_from_token(claims)
        assert result == set()

    def test_non_list_scopes_ignored(self):
        claims = {"scopes": "not-a-list"}
        result = extract_scopes_from_token(claims)
        assert result == set()

    def test_non_string_items_in_list_ignored(self):
        claims = {"scopes": ["network:read", 123, None]}
        result = extract_scopes_from_token(claims)
        assert result == {"network:read"}


# --- expand_scopes ---


class TestExpandScopes:
    """Test scope hierarchy expansion."""

    def test_admin_expands_to_all(self):
        result = expand_scopes({"network:admin"})
        assert result == {"network:admin", "network:write", "network:read"}

    def test_write_expands_to_read(self):
        result = expand_scopes({"network:write"})
        assert result == {"network:write", "network:read"}

    def test_read_stays_read(self):
        result = expand_scopes({"network:read"})
        assert result == {"network:read"}

    def test_audit_is_independent(self):
        result = expand_scopes({"network:audit"})
        assert result == {"network:audit"}

    def test_unknown_scope_preserved(self):
        result = expand_scopes({"custom:scope"})
        assert result == {"custom:scope"}

    def test_multiple_scopes_expanded(self):
        result = expand_scopes({"network:write", "network:audit"})
        assert result == {"network:write", "network:read", "network:audit"}


# --- check_authorization ---


class TestCheckAuthorization:
    """Test the authorization check combining scope matching and extraction."""

    def test_authorized_read_tool_with_read_scope(self):
        claims = {"scope": "network:read"}
        authorized, msg = check_authorization("eos_get_vlans", claims)
        assert authorized is True

    def test_authorized_write_tool_with_write_scope(self):
        claims = {"scope": "network:read network:write"}
        authorized, msg = check_authorization("eos_create_vlan", claims)
        assert authorized is True

    def test_authorized_admin_tool_with_admin_scope(self):
        claims = {"scope": "network:admin"}
        authorized, msg = check_authorization("eos_delete_vlan", claims)
        assert authorized is True

    def test_denied_write_tool_with_read_only_scope(self):
        claims = {"scope": "network:read"}
        authorized, msg = check_authorization("eos_create_vlan", claims)
        assert authorized is False
        assert "network:write" in msg
        assert "eos_create_vlan" in msg

    def test_denied_admin_tool_with_write_scope(self):
        claims = {"scope": "network:write"}
        authorized, msg = check_authorization("eos_delete_vlan", claims)
        assert authorized is False
        assert "network:admin" in msg

    def test_denied_audit_tool_without_audit_scope(self):
        claims = {"scope": "network:read"}
        authorized, msg = check_authorization("eos_compliance_check", claims)
        assert authorized is False
        assert "network:audit" in msg

    def test_authorized_audit_tool_with_audit_scope(self):
        claims = {"scope": "network:read network:audit"}
        authorized, msg = check_authorization("eos_compliance_check", claims)
        assert authorized is True

    def test_admin_scope_grants_write_access(self):
        """Admin scope implies write via hierarchy expansion."""
        claims = {"scope": "network:admin"}
        authorized, msg = check_authorization("eos_create_vlan", claims)
        assert authorized is True

    def test_admin_scope_grants_read_access(self):
        """Admin scope implies read via hierarchy expansion."""
        claims = {"scope": "network:admin"}
        authorized, msg = check_authorization("eos_get_vlans", claims)
        assert authorized is True

    def test_write_scope_grants_read_access(self):
        """Write scope implies read via hierarchy expansion."""
        claims = {"scope": "network:write"}
        authorized, msg = check_authorization("eos_get_vlans", claims)
        assert authorized is True

    def test_unknown_tool_always_authorized(self):
        """Tools not in TOOL_SCOPES are accessible to all authenticated users."""
        claims = {"scope": ""}
        authorized, msg = check_authorization("unknown_tool", claims)
        assert authorized is True

    def test_empty_scopes_denied_for_mapped_tool(self):
        claims = {"scope": ""}
        authorized, msg = check_authorization("eos_get_vlans", claims)
        assert authorized is False

    def test_auth0_permissions_format(self):
        claims = {"permissions": ["network:read", "network:write"]}
        authorized, msg = check_authorization("eos_create_vlan", claims)
        assert authorized is True

    def test_scopes_list_format(self):
        claims = {"scopes": ["network:read"]}
        authorized, msg = check_authorization("eos_get_vlans", claims)
        assert authorized is True


# --- ROLES constant ---


class TestRoles:
    """Test predefined role definitions."""

    def test_viewer_role(self):
        assert ROLES["viewer"] == ["network:read"]

    def test_operator_role(self):
        assert ROLES["operator"] == ["network:read", "network:write"]

    def test_admin_role(self):
        assert ROLES["admin"] == ["network:read", "network:write", "network:admin"]

    def test_auditor_role(self):
        assert ROLES["auditor"] == ["network:read", "network:audit"]


# --- TOOL_SCOPES completeness ---


class TestToolScopesCompleteness:
    """Verify TOOL_SCOPES covers major tool patterns."""

    def test_has_eos_get_pattern(self):
        assert "eos_get_*" in TOOL_SCOPES

    def test_has_eos_create_pattern(self):
        assert "eos_create_*" in TOOL_SCOPES

    def test_has_eos_delete_pattern(self):
        assert "eos_delete_*" in TOOL_SCOPES

    def test_has_net_get_pattern(self):
        assert "net_get_*" in TOOL_SCOPES

    def test_has_nxos_get_pattern(self):
        assert "nxos_get_*" in TOOL_SCOPES

    def test_has_compliance_pattern(self):
        assert "eos_compliance_*" in TOOL_SCOPES

    def test_all_scopes_are_strings(self):
        for pattern, scope in TOOL_SCOPES.items():
            assert isinstance(pattern, str), f"Pattern key must be str: {pattern}"
            assert isinstance(scope, str), f"Scope value must be str: {scope}"

    def test_all_scopes_in_network_namespace(self):
        for scope in TOOL_SCOPES.values():
            assert scope.startswith("network:"), f"Scope should use network: namespace: {scope}"


# --- Integration-style tests ---


class TestRBACIntegration:
    """Integration-style tests simulating real tool call authorization scenarios."""

    def test_noc_viewer_can_read(self):
        """NOC viewer with read-only scope can view device info."""
        viewer_claims = {"scope": " ".join(ROLES["viewer"])}
        authorized, _ = check_authorization("eos_get_vlans", viewer_claims)
        assert authorized is True
        authorized, _ = check_authorization("net_get_device_info", viewer_claims)
        assert authorized is True

    def test_noc_viewer_cannot_write(self):
        """NOC viewer cannot make configuration changes."""
        viewer_claims = {"scope": " ".join(ROLES["viewer"])}
        authorized, msg = check_authorization("eos_create_vlan", viewer_claims)
        assert authorized is False

    def test_operator_can_read_and_write(self):
        """Operator can both view and configure."""
        operator_claims = {"scope": " ".join(ROLES["operator"])}
        authorized, _ = check_authorization("eos_get_vlans", operator_claims)
        assert authorized is True
        authorized, _ = check_authorization("eos_create_vlan", operator_claims)
        assert authorized is True

    def test_operator_cannot_delete(self):
        """Operator cannot perform destructive admin operations."""
        operator_claims = {"scope": " ".join(ROLES["operator"])}
        authorized, msg = check_authorization("eos_delete_vlan", operator_claims)
        assert authorized is False

    def test_admin_can_do_everything(self):
        """Admin has full access."""
        admin_claims = {"scope": " ".join(ROLES["admin"])}
        for tool in ["eos_get_vlans", "eos_create_vlan", "eos_delete_vlan"]:
            authorized, _ = check_authorization(tool, admin_claims)
            assert authorized is True, f"Admin should access {tool}"

    def test_auditor_can_read_and_audit(self):
        """Auditor can read and run compliance checks."""
        auditor_claims = {"scope": " ".join(ROLES["auditor"])}
        authorized, _ = check_authorization("eos_get_vlans", auditor_claims)
        assert authorized is True
        authorized, _ = check_authorization("eos_compliance_check", auditor_claims)
        assert authorized is True

    def test_auditor_cannot_write(self):
        """Auditor cannot make configuration changes."""
        auditor_claims = {"scope": " ".join(ROLES["auditor"])}
        authorized, msg = check_authorization("eos_create_vlan", auditor_claims)
        assert authorized is False

    def test_multi_vendor_read_access(self):
        """Read scope grants access to all vendor read tools."""
        claims = {"scope": "network:read"}
        for tool in ["eos_get_vlans", "net_get_device_info", "nxos_get_vpc_status"]:
            authorized, _ = check_authorization(tool, claims)
            assert authorized is True, f"Read scope should access {tool}"

    def test_error_message_includes_tool_and_scope(self):
        """Error messages are actionable — include tool name and required scope."""
        claims = {"scope": "network:read"}
        authorized, msg = check_authorization("eos_create_vlan", claims)
        assert not authorized
        assert "eos_create_vlan" in msg
        assert "network:write" in msg
        assert "network:read" in msg  # user's scope listed
