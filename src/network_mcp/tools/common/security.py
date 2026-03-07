"""Vendor-agnostic security visibility tools.

Provides ACL retrieval, AAA status, SNMP configuration (with credential
redaction), active user sessions, and Control Plane Policing (CoPP) policy
across Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from network_mcp.drivers.base import NotSupportedError, make_not_supported_response
from network_mcp.helpers import READ_ONLY, handle_tool_errors
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")

# Pattern to match SNMP community strings and other secret values in structured data
_SECRET_KEYS = re.compile(
    r"(community|secret|password|key|auth.?key|priv.?key|shared.?secret)",
    re.IGNORECASE,
)


def _redact_secrets(data: Any, _depth: int = 0) -> Any:
    """Recursively redact values whose keys look like secrets.

    Applies to community strings, shared secrets, password hashes, and
    TACACS/RADIUS keys in structured driver output.  Limits recursion
    depth to 20 to prevent stack overflow on pathological input.
    """
    if _depth > 20:
        return data
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(key, str) and _SECRET_KEYS.search(key):
                result[key] = "***"
            else:
                result[key] = _redact_secrets(value, _depth + 1)
        return result
    if isinstance(data, list):
        return [_redact_secrets(item, _depth + 1) for item in data]
    return data


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_acls(host: str) -> dict:
    """Get access control lists from any vendor device.

    Returns ACL names, types (standard/extended), and rules.
    On JunOS, returns firewall filters (equivalent to ACLs).
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        acls = driver.get_acls()
    except NotSupportedError:
        return make_not_supported_response("get_acls", host, driver.vendor, driver.platform)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"acls": acls, "acl_count": len(acls)},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_aaa_status(host: str) -> dict:
    """Get AAA authentication/authorization configuration from any vendor device.

    Returns configured authentication methods (local, RADIUS, TACACS+),
    server groups, and status.  Shared secrets are automatically redacted.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        aaa = driver.get_aaa_status()
    except NotSupportedError:
        return make_not_supported_response("get_aaa_status", host, driver.vendor, driver.platform)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": _redact_secrets(aaa),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_snmp_config(host: str) -> dict:
    """Get SNMP configuration from any vendor device.

    Returns communities (with strings redacted), SNMPv3 users, and trap hosts.
    Community strings and auth/priv keys are replaced with '***' for security.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        snmp = driver.get_snmp_config()
    except NotSupportedError:
        return make_not_supported_response("get_snmp_config", host, driver.vendor, driver.platform)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": _redact_secrets(snmp),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_users(host: str) -> dict:
    """Get active user sessions on any vendor device.

    Returns username, source IP, session type (SSH/console/API), and idle time.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        sessions = driver.get_user_sessions()
    except NotSupportedError:
        return make_not_supported_response("get_user_sessions", host, driver.vendor, driver.platform)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"sessions": sessions, "session_count": len(sessions)},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_copp_policy(host: str) -> dict:
    """Get Control Plane Policing policy from any vendor device.

    Returns CoPP classes, rate limits, and statistics.
    Not all platforms support CoPP — returns not_supported if unavailable.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        copp = driver.get_copp_policy()
    except NotSupportedError:
        return make_not_supported_response("get_copp_policy", host, driver.vendor, driver.platform)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": copp,
    }
