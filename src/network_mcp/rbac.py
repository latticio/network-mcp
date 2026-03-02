"""Role-Based Access Control and multi-tenancy for network-mcp HTTP transport.

Maps JWT scope claims to tool access permissions. RBAC is opt-in via
NET_RBAC_ENABLED=true and only applies to HTTP transport (stdio is trusted).

Scope hierarchy:
  - network:admin implies network:write and network:read
  - network:write implies network:read
  - network:audit is independent (no implied hierarchy)

Multi-tenancy:
  extract_tenant_id() pulls tenant identification from JWT claims,
  supporting explicit tenant_id, Auth0 org_id, and email-based sub claims.
"""

import fnmatch
import logging

logger = logging.getLogger("network-mcp.rbac")


# --- Tool name pattern -> required scope ---
# Patterns are matched using fnmatch (Unix shell-style wildcards).
# More specific patterns are checked first (ordering matters for first-match).

TOOL_SCOPES: dict[str, str] = {
    # EOS read operations
    "eos_get_*": "network:read",
    "eos_list_*": "network:read",
    "eos_diff_*": "network:read",
    "eos_detect_*": "network:read",
    "eos_check_*": "network:read",
    "eos_ping_*": "network:read",
    "eos_traceroute_*": "network:read",
    "eos_validate_*": "network:read",
    "eos_run_anta_*": "network:read",
    "eos_diagnose_*": "network:read",
    "eos_troubleshoot_*": "network:read",
    "eos_export_*": "network:read",
    "eos_pre_change_*": "network:read",
    "eos_post_change_*": "network:read",
    "eos_compare_*": "network:read",
    "eos_cache_*": "network:read",
    "eos_server_*": "network:read",
    "eos_gnmi_get": "network:read",
    "eos_gnmi_capabilities": "network:read",
    "eos_gnmi_subscribe_*": "network:read",
    # EOS write operations
    "eos_create_*": "network:write",
    "eos_rename_*": "network:write",
    "eos_configure_*": "network:write",
    "eos_push_*": "network:write",
    "eos_save_*": "network:write",
    "eos_add_to_*": "network:write",
    "eos_commit_*": "network:write",
    "eos_abort_*": "network:write",
    "eos_confirm_*": "network:write",
    "eos_enable_*": "network:write",
    "eos_apply_*": "network:write",
    "eos_shutdown_*": "network:write",
    "eos_no_shutdown_*": "network:write",
    "eos_assign_*": "network:write",
    "eos_gnmi_set": "network:write",
    # EOS admin/destructive operations
    "eos_delete_*": "network:admin",
    "eos_rollback_*": "network:admin",
    "eos_remove_*": "network:admin",
    # EOS meta-tools (progressive discovery)
    "eos_load_tool_category": "network:read",
    # Compliance/audit
    "eos_compliance_*": "network:audit",
    # API key management tools (admin-only, must precede generic net_create_* pattern)
    "net_create_api_key": "network:admin",
    "net_list_api_keys": "network:admin",
    "net_revoke_api_key": "network:admin",
    "net_rotate_api_key": "network:admin",
    # Server administration
    "config_reload": "network:admin",
    # Vendor-agnostic (net_*) read tools
    "net_get_*": "network:read",
    "net_diagnose_*": "network:read",
    "net_troubleshoot_*": "network:read",
    "net_validate_*": "network:read",
    "net_export_*": "network:read",
    # Vendor-agnostic (net_*) write tools
    "net_create_*": "network:write",
    "net_rename_*": "network:write",
    "net_assign_*": "network:write",
    "net_configure_*": "network:write",
    "net_save_*": "network:write",
    "net_push_*": "network:write",
    # Vendor-agnostic (net_*) admin/destructive tools
    "net_delete_*": "network:admin",
    # Cisco NX-OS
    "nxos_get_*": "network:read",
    "nxos_create_*": "network:write",
    "nxos_delete_*": "network:admin",
    # Cisco IOS-XE
    "iosxe_get_*": "network:read",
    "iosxe_create_*": "network:write",
    "iosxe_delete_*": "network:admin",
    # Juniper JunOS
    "junos_get_*": "network:read",
    "junos_create_*": "network:write",
    "junos_delete_*": "network:admin",
    # Generic write/admin patterns (catch-all for any vendor)
    "*_create_*": "network:write",
    "*_delete_*": "network:admin",
    "*_set_*": "network:write",
    "*_commit_*": "network:write",
    "*_abort_*": "network:write",
}

# Predefined roles (convenience mapping — scopes are the authority, not roles)
ROLES: dict[str, list[str]] = {
    "viewer": ["network:read"],
    "operator": ["network:read", "network:write"],
    "admin": ["network:read", "network:write", "network:admin"],
    "auditor": ["network:read", "network:audit"],
}

# Scope hierarchy: higher scopes imply lower ones
_SCOPE_HIERARCHY: dict[str, set[str]] = {
    "network:admin": {"network:admin", "network:write", "network:read"},
    "network:write": {"network:write", "network:read"},
    "network:read": {"network:read"},
    "network:audit": {"network:audit"},
}


def get_required_scope(tool_name: str) -> str | None:
    """Match a tool name against TOOL_SCOPES patterns.

    Returns the required scope string, or None if no scope is required
    (tool is accessible to all authenticated users).

    Uses fnmatch for glob-style pattern matching. First matching pattern wins.
    """
    for pattern, scope in TOOL_SCOPES.items():
        if fnmatch.fnmatch(tool_name, pattern):
            return scope
    return None


def expand_scopes(scopes: set[str]) -> set[str]:
    """Expand scopes using the hierarchy (e.g., network:admin -> {admin, write, read})."""
    expanded: set[str] = set()
    for scope in scopes:
        if scope in _SCOPE_HIERARCHY:
            expanded |= _SCOPE_HIERARCHY[scope]
        else:
            expanded.add(scope)
    return expanded


def extract_scopes_from_token(token_claims: dict) -> set[str]:
    """Extract scope set from JWT claims.

    Supports:
    - 'scope' claim as space-separated string (OAuth2 standard per RFC 6749)
    - 'scopes' claim as list (common in custom JWT implementations)
    - 'permissions' claim as list (Auth0 style)
    """
    scopes: set[str] = set()

    # OAuth2 standard: space-separated 'scope' string
    scope_str = token_claims.get("scope")
    if isinstance(scope_str, str) and scope_str.strip():
        scopes.update(scope_str.split())

    # List-based 'scopes' claim
    scopes_list = token_claims.get("scopes")
    if isinstance(scopes_list, list):
        scopes.update(s for s in scopes_list if isinstance(s, str))

    # Auth0-style 'permissions' claim
    permissions = token_claims.get("permissions")
    if isinstance(permissions, list):
        scopes.update(p for p in permissions if isinstance(p, str))

    return scopes


def check_authorization(tool_name: str, token_claims: dict) -> tuple[bool, str]:
    """Check if the token has the required scope for the tool.

    Uses feature flags to conditionally bypass RBAC per tenant when the
    'rbac' feature flag is disabled for that tenant.

    Args:
        tool_name: The MCP tool name being called.
        token_claims: JWT claims dict (must contain scope/scopes/permissions).

    Returns:
        (authorized, message) — authorized is True if access is granted.
        On failure, message contains the missing scope and user's scopes.
    """
    # M3: Check feature flags — if RBAC feature flag is explicitly defined
    # and disabled for this tenant, skip RBAC checks
    tenant_id = extract_tenant_id(token_claims)
    try:
        from network_mcp.feature_flags import get_feature_flag, is_feature_enabled

        rbac_flag = get_feature_flag("rbac")
        if rbac_flag is not None and not is_feature_enabled("rbac", tenant_id=tenant_id):
            return True, "RBAC feature disabled for tenant"
    except Exception:
        logger.debug("Feature flag check failed, proceeding with normal RBAC", exc_info=True)

    required = get_required_scope(tool_name)
    if required is None:
        # No scope requirement — accessible to all authenticated users
        return True, "No scope required"

    user_scopes = extract_scopes_from_token(token_claims)
    effective_scopes = expand_scopes(user_scopes)

    if required in effective_scopes:
        return True, f"Authorized: has '{required}' scope"

    sorted_scopes = sorted(user_scopes)
    return (
        False,
        f"Forbidden: tool '{tool_name}' requires scope '{required}'. Your scopes: {sorted_scopes}",
    )


class TenantDeviceFilter:
    """Filters device access by tenant ID.

    Devices with a non-empty ``tenants`` list are restricted to the listed
    tenants.  Devices with an empty list (or not in the inventory at all)
    are accessible to every tenant for backward compatibility.
    """

    def __init__(self, inventory: dict) -> None:
        self._device_tenants: dict[str, set[str]] = {}
        for name, device in inventory.items():
            # Support both raw dicts and DeviceCredentials (pydantic model)
            if isinstance(device, dict):
                tenants = device.get("tenants", [])
            else:
                tenants = getattr(device, "tenants", [])
            self._device_tenants[name] = set(tenants) if tenants else set()

    def is_allowed(self, device: str, tenant_id: str | None) -> bool:
        """Return True if *tenant_id* may access *device*."""
        if not tenant_id:
            return True  # no tenant = no restriction (backward compat)
        allowed = self._device_tenants.get(device, set())
        if not allowed:
            return True  # empty tenants list = accessible to all
        return tenant_id in allowed


def extract_tenant_id(token_claims: dict) -> str | None:
    """Extract tenant identifier from JWT claims.

    Supports:
    - 'tenant_id' claim (explicit)
    - 'org_id' claim (Auth0 organizations)
    - 'sub' claim prefix before '@' (email-based)
    - None if no tenant information available

    Returns:
        Tenant ID string, or None for single-tenant mode.
    """
    # Explicit tenant_id claim
    tenant_id = token_claims.get("tenant_id")
    if isinstance(tenant_id, str) and tenant_id.strip():
        return tenant_id.strip()

    # Auth0 organization ID
    org_id = token_claims.get("org_id")
    if isinstance(org_id, str) and org_id.strip():
        return org_id.strip()

    # Email-based: extract domain from sub claim (user@tenant.com -> tenant.com)
    sub = token_claims.get("sub")
    if isinstance(sub, str) and "@" in sub:
        domain = sub.split("@", 1)[1]
        if domain.strip():
            return domain.strip()

    return None
