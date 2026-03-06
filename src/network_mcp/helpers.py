"""Shared helpers: error handling, input validation, retry, rate limiting, audit, and tool annotation constants."""

import functools
import inspect
import logging
import re
import time
from typing import TYPE_CHECKING

import pyeapi.eapilib
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from network_mcp.audit import log_tool_invocation
from network_mcp.cache import get_default_ttl
from network_mcp.distributed import create_cache, create_circuit_breaker, create_rate_limiter
from network_mcp.errors import ErrorCode, make_error_response
from network_mcp.sanitizer import enforce_dict_output_limit, sanitize_dict_values, sanitize_error_message

if TYPE_CHECKING:
    from network_mcp.connection import ConnectionManager

logger = logging.getLogger("network-mcp")


def _attach_request_id(result: dict) -> dict:
    """Attach the current request/correlation ID to a response dict, if available."""
    from network_mcp.middleware import get_correlation_id

    correlation_id = get_correlation_id()
    if correlation_id:
        result["request_id"] = correlation_id
        result["correlation_id"] = correlation_id
    return result


def check_tenant_device_access(host: str) -> str | None:
    """Check if the current tenant is allowed to access the given device.

    Uses the middleware request context to determine the active tenant, then
    checks the tenant's allowed device patterns (from ``_request_context.tenant_devices``).

    Returns:
        Error message string if access is denied, or None if allowed.
    """
    import fnmatch

    from network_mcp.middleware import get_current_tenant_id

    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return None  # No tenant context (e.g., local stdio) — allow all

    # Check for tenant device filter list set via middleware
    from network_mcp.middleware import _request_context

    tenant_devices: list[str] | None = getattr(_request_context, "tenant_devices", None)
    if tenant_devices is None:
        return None  # No device filter configured — allow all

    for pattern in tenant_devices:
        if fnmatch.fnmatch(host, pattern):
            return None  # Host matches an allowed pattern

    return "Access denied: device not in tenant scope"


# --- Field Filtering & Pagination ---


def filter_fields(data: dict, fields: list[str] | None) -> dict:
    """Filter a dict to only include specified fields. Returns data unchanged if fields is None."""
    if not fields:
        return data
    return {k: v for k, v in data.items() if k in fields}


def paginate_dict(data: dict, limit: int | None = None, offset: int = 0) -> tuple[dict, dict]:
    """Paginate a dict's entries (for dict-of-dicts like interfaces, VLANs, peers).

    Returns (paginated_data, pagination_metadata).
    """
    keys = list(data.keys())
    total = len(keys)
    if offset >= total:
        return {}, {
            "total": total,
            "offset": offset,
            "limit": limit if limit is not None else total,
            "returned": 0,
            "has_more": False,
        }
    end = offset + limit if limit is not None else total
    selected_keys = keys[offset:end]
    paginated = {k: data[k] for k in selected_keys}
    return paginated, {
        "total": total,
        "offset": offset,
        "limit": limit if limit is not None else total,
        "returned": len(selected_keys),
        "has_more": end < total,
    }


def paginate_list(data: list, limit: int | None = None, offset: int = 0) -> tuple[list, dict]:
    """Paginate a list of items (for list-based results like ARP entries, routes).

    Returns (paginated_list, pagination_metadata).
    """
    total = len(data)
    if offset >= total:
        return [], {
            "total": total,
            "offset": offset,
            "limit": limit if limit is not None else total,
            "returned": 0,
            "has_more": False,
        }
    end = offset + limit if limit is not None else total
    page = data[offset:end]
    return page, {
        "total": total,
        "offset": offset,
        "limit": limit if limit is not None else total,
        "returned": len(page),
        "has_more": end < total,
    }


# --- Tool Annotation Constants ---
# Used with @mcp.tool(annotations=...) to hint LLM behavior

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE_SAFE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


# --- Connection Error Advice (L2: extracted from repeated inline strings) ---

CONNECTION_ERROR_ADVICE = (
    "Verify: (1) device is reachable, (2) API access is enabled "
    "(eAPI for EOS, RESTCONF for IOS-XE, NX-API for NX-OS, NETCONF for JunOS), "
    "(3) credentials are correct."
)


# --- Error Handling Decorator ---


def _record_otel_exception(e: Exception) -> None:
    """Record an exception on the current OTel span, if one is active.

    No-op when OpenTelemetry is not installed or no span is recording.
    """
    try:
        from opentelemetry import trace as otel_trace

        current_span = otel_trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.record_exception(e)
            current_span.set_status(otel_trace.StatusCode.ERROR, str(e))
    except ImportError:
        pass


def handle_tool_errors(func):
    """Wrap tool functions to catch exceptions and return standardized error dicts.

    Re-raises ``ToolError`` unchanged (for FastMCP error propagation).
    All other exceptions are classified via ``classify_exception()`` and returned
    as structured error dicts with error_code and is_retryable fields.
    Extracts ``host`` from the function's first positional argument.

    When an OTel span is active, records the exception and sets span status to ERROR.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.keys())
    host_param = params[0] if params else None

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Resolve host from positional or keyword args
        host = "unknown"
        if host_param:
            if args:
                host = args[0]
            elif host_param in kwargs:
                host = kwargs[host_param]
        try:
            return func(*args, **kwargs)
        except ToolError:
            raise  # Let ToolError propagate to FastMCP for proper error handling
        except Exception as e:
            _record_otel_exception(e)
            from network_mcp.errors import classify_exception

            error_code = classify_exception(e)
            return make_error_response(
                error_code,
                host,
                _format_classified_error(error_code, host, e),
            )

    return wrapper


def _format_classified_error(error_code: "ErrorCode", host: str, exc: Exception) -> str:
    """Format an error message based on the classified ErrorCode."""
    msg = sanitize_error_message(str(exc))
    if error_code == ErrorCode.CONNECTION_ERROR or error_code == ErrorCode.DEVICE_UNREACHABLE:
        return f"Connection to {host} failed: {msg}. {CONNECTION_ERROR_ADVICE}"
    if error_code == ErrorCode.COMMAND_ERROR:
        return (
            f"Command error on {host}: {msg}. "
            "Check that the command syntax is correct and supported on this platform version."
        )
    if error_code == ErrorCode.TIMEOUT_ERROR:
        return f"Timeout communicating with {host}: {msg}"
    if error_code == ErrorCode.AUTH_ERROR:
        return f"Authentication error on {host}: {msg}"
    return f"Unexpected error on {host}: {msg}"


# --- Input Validation ---

HOST_PATTERN = re.compile(r"^[a-zA-Z0-9._:-]+$")
DANGEROUS_CLI_CHARS = re.compile(r"[;|!`\n\r\x00]|\$\(|\$\{")


def validate_cli_param(value: str, param_name: str = "parameter") -> str | None:
    """Validate a CLI parameter contains no injection characters. Returns error message if invalid, None if OK."""
    if not value or not value.strip():
        return f"{param_name} cannot be empty"
    if DANGEROUS_CLI_CHARS.search(value):
        return f"{param_name} contains invalid characters"
    return None


def validate_ip_or_hostname(value: str, param_name: str = "address") -> str | None:
    """Validate an IP address or simple hostname. Returns error message if invalid, None if OK."""
    cli_err = validate_cli_param(value, param_name)
    if cli_err:
        return cli_err
    if not HOST_PATTERN.match(value.strip()):
        return f"{param_name} contains invalid characters: {value}"
    return None


def validate_host(host: str) -> str | None:
    """Validate host parameter. Returns error message if invalid, None if OK."""
    if not host or not host.strip():
        return "Host is required and cannot be empty. Provide a hostname, IP address, or inventory device name."
    if len(host) > 253:
        return "Host must be 253 characters or fewer"
    if not HOST_PATTERN.match(host):
        return (
            f"Invalid host '{host}'. Must contain only alphanumeric characters, dots, hyphens, underscores, or colons. "
            "Use an IP address (e.g., '10.0.0.1') or hostname (e.g., 'spine-01')."
        )
    return None


def validate_vlan_id(vlan_id: int) -> str | None:
    """Validate VLAN ID range (1-4094). Returns error message if invalid, None if OK."""
    if vlan_id < 1 or vlan_id > 4094:
        return "VLAN ID must be between 1 and 4094"
    return None


# Arista EOS interface prefixes (subset of MULTI_VENDOR_INTERFACE_PREFIXES)
_EOS_INTERFACE_PREFIXES = ("Ethernet", "Management", "Loopback", "Vlan", "Port-Channel", "Vxlan")

# Multi-vendor interface name prefixes (EOS, IOS-XE, NX-OS, JunOS)
MULTI_VENDOR_INTERFACE_PREFIXES = (
    # Arista EOS
    *_EOS_INTERFACE_PREFIXES,
    # Cisco IOS-XE
    "GigabitEthernet",
    "TenGigabitEthernet",
    "TwentyFiveGigE",
    "FortyGigabitEthernet",
    "HundredGigE",
    "FastEthernet",
    "Serial",
    "Tunnel",
    "BDI",
    # Cisco NX-OS
    "mgmt",
    "port-channel",
    "loopback",
    "nve",
    # Juniper JunOS
    "ge-",
    "xe-",
    "et-",
    "ae",
    "lo0",
    "irb",
    "em",
    "fxp",
    "me",
    "vme",
    "st0",
)

# Backward-compatible alias (EOS-only prefix list)
INTERFACE_PREFIXES = _EOS_INTERFACE_PREFIXES


def validate_multi_vendor_interface_name(interface: str) -> str | None:
    """Validate interface name across all supported vendors. Returns error message if invalid, None if OK.

    Accepts interface names from Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.
    """
    if not interface or not interface.strip():
        return "Interface name is required and cannot be empty"
    cli_err = validate_cli_param(interface, "interface")
    if cli_err:
        return cli_err
    if not any(interface.startswith(prefix) for prefix in MULTI_VENDOR_INTERFACE_PREFIXES):
        return (
            f"Invalid interface name '{interface}'. Must start with a recognized vendor prefix "
            f"(e.g., Ethernet1, GigabitEthernet0/0, ge-0/0/0, port-channel1)."
        )
    return None


# --- Cross-Vendor Normalization Helpers ---


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to colon-separated lowercase format (aa:bb:cc:dd:ee:ff).

    Handles all vendor formats:
    - EOS/JunOS colon notation: aa:bb:cc:dd:ee:ff
    - Cisco dot notation: aabb.ccdd.eeff
    - Hyphen notation: aa-bb-cc-dd-ee-ff
    - Uppercase variants of all above

    Returns the original string unchanged if it cannot be parsed as a valid 48-bit MAC.
    """
    if not mac:
        return ""
    clean = re.sub(r"[.:\-]", "", mac.lower())
    if len(clean) != 12:
        return mac  # return as-is if not a valid MAC
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2))


_SPEED_MAP: dict[str, str] = {
    "10": "10Mbps",
    "10mbps": "10Mbps",
    "100": "100Mbps",
    "100mbps": "100Mbps",
    "1000": "1Gbps",
    "1000mbps": "1Gbps",
    "1g": "1Gbps",
    "1gbps": "1Gbps",
    "2500": "2.5Gbps",
    "2500mbps": "2.5Gbps",
    "5000": "5Gbps",
    "5000mbps": "5Gbps",
    "10000": "10Gbps",
    "10000mbps": "10Gbps",
    "10g": "10Gbps",
    "10gbps": "10Gbps",
    "25000": "25Gbps",
    "25000mbps": "25Gbps",
    "25g": "25Gbps",
    "25gbps": "25Gbps",
    "40000": "40Gbps",
    "40000mbps": "40Gbps",
    "40g": "40Gbps",
    "40gbps": "40Gbps",
    "50000": "50Gbps",
    "50000mbps": "50Gbps",
    "50g": "50Gbps",
    "100000": "100Gbps",
    "100000mbps": "100Gbps",
    "100g": "100Gbps",
    "100gbps": "100Gbps",
    "400000": "400Gbps",
    "400g": "400Gbps",
    "auto": "auto",
    "": "unknown",
}


def normalize_speed(speed: str) -> str:
    """Normalize interface speed string to a consistent format (e.g., '1Gbps', '10Gbps').

    Handles vendor-specific representations:
    - Numeric Mbps: '1000', '10000'
    - With units: '1000Mbps', '10Gbps', '1G'
    - Special: 'auto', ''

    Returns the original string if no mapping is found.
    """
    return _SPEED_MAP.get(speed.lower().strip(), speed)


def validate_speed(speed: str) -> str | None:
    """Validate interface speed value. Returns error message if invalid, None if OK."""
    valid = {"auto", "10", "100", "1000", "2500", "5000", "10000", "25000", "40000", "50000", "100000"}
    if speed not in valid:
        return f"Invalid speed: {speed}. Valid: {sorted(valid)}"
    return None


def validate_duplex(duplex: str) -> str | None:
    """Validate interface duplex value. Returns error message if invalid, None if OK."""
    valid = {"auto", "full", "half"}
    if duplex not in valid:
        return f"Invalid duplex: {duplex}. Valid: {sorted(valid)}"
    return None


def validate_mtu(mtu: int) -> str | None:
    """Validate MTU value (68-9214). Returns error message if invalid, None if OK."""
    if not 68 <= mtu <= 9214:
        return f"MTU must be between 68 and 9214, got {mtu}"
    return None


def validate_interface_name(interface: str) -> str | None:
    """Validate interface name pattern. Returns error message if invalid, None if OK."""
    if not interface or not interface.strip():
        return "Interface name is required and cannot be empty"
    if not any(interface.startswith(prefix) for prefix in INTERFACE_PREFIXES):
        return f"Invalid interface name '{interface}'. Must start with one of: {', '.join(INTERFACE_PREFIXES)}"
    return None


def validate_port_channel_id(channel_id: int) -> str | None:
    """Validate port-channel ID range (1-2000). Returns error message if invalid, None if OK."""
    if not 1 <= channel_id <= 2000:
        return f"Port-channel ID must be 1-2000, got {channel_id}"
    return None


def validate_stp_priority(priority: int) -> str | None:
    """Validate STP bridge priority. Must be a multiple of 4096, range 0-61440. Returns error message if invalid."""
    if priority < 0 or priority > 61440 or priority % 4096 != 0:
        return f"STP priority must be a multiple of 4096 between 0-61440, got {priority}"
    return None


_RD_PATTERN = re.compile(r"^(\d+:\d+|\d+\.\d+\.\d+\.\d+:\d+)$")


def validate_route_distinguisher(rd: str) -> str | None:
    """Validate RD format: ASN:nn or IP:nn. Returns error message if invalid, None if OK."""
    if not rd or not rd.strip():
        return "Route distinguisher cannot be empty"
    if not _RD_PATTERN.match(rd.strip()):
        return (
            f"Invalid route distinguisher format: {rd}. Expected ASN:nn (e.g., 65001:100) or IP:nn (e.g., 10.0.0.1:100)"
        )
    return None


_IP_PREFIX_PATTERN = re.compile(r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$")


def validate_ip_prefix(prefix: str) -> str | None:
    """Validate IPv4 CIDR notation (e.g., '10.0.0.0/8'). Returns error message if invalid, None if OK."""
    cli_err = validate_cli_param(prefix, "prefix")
    if cli_err:
        return cli_err
    if not _IP_PREFIX_PATTERN.match(prefix.strip()):
        return f"Invalid IP prefix '{prefix}'. Must be in CIDR notation (e.g., '10.0.0.0/8')."
    parts = prefix.strip().split("/")
    octets = parts[0].split(".")
    for octet in octets:
        if int(octet) > 255:
            return f"Invalid IP prefix '{prefix}'. Octet value {octet} exceeds 255."
    mask = int(parts[1])
    if mask > 32:
        return f"Invalid IP prefix '{prefix}'. Mask length {mask} exceeds 32."
    return None


_IP_ADDRESS_PATTERN = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def validate_ip_address(ip: str) -> str | None:
    """Validate IPv4 address format (e.g., '10.0.0.1'). Returns error message if invalid, None if OK."""
    cli_err = validate_cli_param(ip, "ip_address")
    if cli_err:
        return cli_err
    if not _IP_ADDRESS_PATTERN.match(ip.strip()):
        return f"Invalid IP address '{ip}'. Must be a valid IPv4 address (e.g., '10.0.0.1')."
    for octet in ip.strip().split("."):
        if int(octet) > 255:
            return f"Invalid IP address '{ip}'. Octet value {octet} exceeds 255."
    return None


# --- Free-Form Parameter Validators ---

VLAN_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9 _-]+$")
VRF_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_:-]+$")
ACL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def validate_vlan_name(name: str) -> str | None:
    """Validate VLAN name: alphanumeric, spaces, hyphens, underscores only. Max 32 chars.

    Returns error message if invalid, None if OK.
    """
    if not name or not name.strip():
        return "VLAN name cannot be empty"
    if len(name) > 32:
        return f"VLAN name must be 32 characters or fewer, got {len(name)}"
    if not VLAN_NAME_PATTERN.match(name):
        return "VLAN name must contain only alphanumeric characters, spaces, hyphens, and underscores"
    return None


def validate_description(description: str) -> str | None:
    """Validate interface/VLAN description: max 240 chars, no control characters.

    Returns error message if invalid, None if OK.
    """
    if not description:
        return "Description cannot be empty"
    if len(description) > 240:
        return f"Description must be 240 characters or fewer, got {len(description)}"
    if CONTROL_CHAR_PATTERN.search(description):
        return "Description must not contain control characters"
    return None


def validate_vrf_name(name: str) -> str | None:
    """Validate VRF name: alphanumeric, hyphens, underscores, colons. Max 32 chars. No spaces.

    Returns error message if invalid, None if OK.
    """
    if not name or not name.strip():
        return "VRF name cannot be empty"
    if len(name) > 32:
        return f"VRF name must be 32 characters or fewer, got {len(name)}"
    if not VRF_NAME_PATTERN.match(name):
        return "VRF name must contain only alphanumeric characters, hyphens, underscores, and colons (no spaces)"
    return None


def validate_acl_name(name: str) -> str | None:
    """Validate ACL name: alphanumeric, hyphens, underscores. Max 100 chars. No spaces.

    Returns error message if invalid, None if OK.
    """
    if not name or not name.strip():
        return "ACL name cannot be empty"
    if len(name) > 100:
        return f"ACL name must be 100 characters or fewer, got {len(name)}"
    if not ACL_NAME_PATTERN.match(name):
        return "ACL name must contain only alphanumeric characters, hyphens, and underscores (no spaces)"
    return None


def validate_bgp_asn(asn: int) -> str | None:
    """Validate BGP AS number: 1-4294967295 (32-bit range).

    Returns error message if invalid, None if OK.
    """
    if not isinstance(asn, int) or isinstance(asn, bool):
        return "BGP ASN must be an integer"
    if asn < 1 or asn > 4294967295:
        return f"BGP ASN must be between 1 and 4294967295, got {asn}"
    return None


def validate_community_string(community: str) -> str | None:
    """Validate SNMP community string: printable ASCII, no spaces, 1-32 chars.

    Returns error message if invalid, None if OK.
    """
    if not community:
        return "Community string cannot be empty"
    if len(community) > 32:
        return f"Community string must be 32 characters or fewer, got {len(community)}"
    if " " in community:
        return "Community string must not contain spaces"
    if not all(32 <= ord(c) <= 126 for c in community):
        return "Community string must contain only printable ASCII characters"
    return None


# --- Retry Decorators ---

# Retry show commands: 3 attempts, 1s/2s/4s exponential backoff
# Only retry transport errors — NOT CommandError (device-side, retrying won't help)
RETRY_SHOW = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(
        (
            pyeapi.eapilib.ConnectionError,
            ConnectionRefusedError,
            TimeoutError,
        )
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

# Retry config commands: conditional to prevent double-apply risk.
# When enabled: 2 attempts, 2s/5s exponential backoff.
# When disabled (default): single attempt, no retry.
# IMPORTANT: If a config push succeeds but the HTTP response times out,
# retry will re-apply commands — risking double-creation of VLANs, BGP peers, etc.
RETRY_CONFIG_ENABLED = retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    retry=retry_if_exception_type(
        (
            pyeapi.eapilib.ConnectionError,
            ConnectionRefusedError,
            TimeoutError,
        )
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
RETRY_CONFIG_DISABLED = retry(stop=stop_after_attempt(1), reraise=True)  # no-op: single attempt


def _get_config_retry():
    """Return the appropriate config retry decorator based on settings."""
    s = _get_settings()
    return RETRY_CONFIG_ENABLED if s.config_retry_enabled else RETRY_CONFIG_DISABLED


# --- Lazy NetworkSettings Access ---

_settings = None


def _init_settings(settings_instance):
    """Initialize helpers with the server's settings instance to ensure consistency.

    Rebuilds module-level singletons (rate limiter, cache, circuit breaker) that
    may have been constructed with a default NetworkSettings before the server's
    canonical settings were available.
    """
    global _settings, device_rate_limiter, user_rate_limiter, tenant_rate_limiter
    global command_cache, circuit_breaker_registry
    _settings = settings_instance
    # Rebuild singletons now that we have the canonical settings
    device_rate_limiter = _create_rate_limiter()
    user_rate_limiter = _create_user_rate_limiter()
    tenant_rate_limiter = _create_tenant_rate_limiter()
    command_cache = _create_command_cache()
    circuit_breaker_registry = _create_circuit_breaker_registry()


def _get_settings():
    """Return the cached NetworkSettings instance.

    Used at both import time (for module-level singleton construction like
    rate limiter, cache, circuit breaker) and at runtime throughout the module.
    The instance is set by _init_settings() when server.py starts, which also
    rebuilds the singletons. Falls back to creating a fresh NetworkSettings
    if called before _init_settings() (e.g., during import-time initialization).
    """
    global _settings
    if _settings is None:
        from network_mcp.config import NetworkSettings

        _settings = NetworkSettings()
    return _settings


# --- Per-Device Rate Limiter ---


def _create_rate_limiter():
    """Create rate limiter using NetworkSettings values (local or Redis-backed)."""
    s = _get_settings()
    return create_rate_limiter(
        backend=s.net_distributed_backend,
        redis_url=s.net_redis_url,
        show_rate=s.rate_limit_show,
        config_rate=s.rate_limit_config,
    )


device_rate_limiter = _create_rate_limiter()

# --- Per-User Rate Limiter ---


def _create_user_rate_limiter():
    """Create per-user rate limiter using NetworkSettings values."""
    from network_mcp.rate_limiter import UserRateLimiter

    s = _get_settings()
    return UserRateLimiter(
        read_rate=s.rate_limit_user_read,
        write_rate=s.rate_limit_user_write,
        admin_rate=s.rate_limit_user_admin,
    )


user_rate_limiter = _create_user_rate_limiter()

# --- Per-Tenant Rate Limiter ---


def _create_tenant_rate_limiter():
    """Create per-tenant rate limiter using NetworkSettings values."""
    from network_mcp.rate_limiter import TenantRateLimiter

    s = _get_settings()
    return TenantRateLimiter(rate=s.rate_limit_tenant)


tenant_rate_limiter = _create_tenant_rate_limiter()


def _check_user_tenant_rate_limit(is_config: bool = False) -> dict | None:
    """Check per-user and per-tenant rate limits using middleware request context.

    Args:
        is_config: True for config commands (unused currently, reserved for future tier logic).

    Returns:
        Error dict with retry_after_seconds if rate limited, None if allowed.
    """
    from network_mcp.middleware import get_current_scopes, get_current_tenant_id, get_current_user

    user_id = get_current_user()
    tenant_id = get_current_tenant_id()
    scopes = get_current_scopes()

    # Per-user check (skip for anonymous/unauthenticated)
    if user_id and user_id != "anonymous":
        if not user_rate_limiter.check(user_id, scopes):
            wait = user_rate_limiter.wait_time(user_id, scopes)
            return {
                "status": "error",
                "error": "Rate limit exceeded for user",
                "retry_after_seconds": round(wait, 2),
            }

    # Per-tenant check
    if tenant_id:
        if not tenant_rate_limiter.check(tenant_id):
            wait = tenant_rate_limiter.wait_time(tenant_id)
            return {
                "status": "error",
                "error": "Rate limit exceeded for tenant",
                "retry_after_seconds": round(wait, 2),
            }

    return None


# --- Command Cache ---


def _create_command_cache():
    """Create command cache using NetworkSettings values (local or Redis-backed)."""
    s = _get_settings()
    return create_cache(
        backend=s.net_distributed_backend,
        redis_url=s.net_redis_url,
        max_entries=s.net_cache_max_entries,
        max_memory_mb=s.net_cache_max_mb,
        enabled=s.net_cache_enabled,
    )


command_cache = _create_command_cache()

# --- Circuit Breaker Registry ---


def _create_circuit_breaker_registry():
    """Create circuit breaker registry using NetworkSettings values (local or Redis-backed)."""
    s = _get_settings()
    return create_circuit_breaker(
        backend=s.net_distributed_backend,
        redis_url=s.net_redis_url,
        threshold=s.net_cb_threshold,
        cooldown=s.net_cb_cooldown,
        enabled=s.net_circuit_breaker,
    )


circuit_breaker_registry = _create_circuit_breaker_registry()

# --- Metrics Collector (lazy to avoid circular import at module load) ---

_metrics_collector = None


def _get_metrics_collector():
    """Lazily import and return the metrics_collector singleton."""
    global _metrics_collector
    if _metrics_collector is None:
        from network_mcp.observability import metrics_collector

        _metrics_collector = metrics_collector
    return _metrics_collector


def _get_sli_tracker():
    """Lazily import and return the SLI tracker singleton (or None if not initialized)."""
    from network_mcp.slo import get_sli_tracker

    return get_sli_tracker()


# --- Command Execution Helpers ---


def run_show_command(
    conn_mgr: "ConnectionManager",
    host: str,
    commands: list[str],
    encoding: str = "json",
    cache_ttl: int | None = None,
) -> dict:
    """Execute show commands with validation, caching, retry, rate limiting, bulkhead, and audit.

    Pipeline: host validation -> tenant access check -> cache lookup -> per-device rate limit
    -> per-user/tenant rate limit -> circuit breaker check -> bulkhead (connection acquire with
    timeout) -> command execution with retry (3x exponential backoff) -> cache populate ->
    stale-if-error fallback on failure -> SLI/OTel recording -> audit logging.

    Args:
        conn_mgr: Connection manager instance.
        host: Target device hostname, IP, or inventory name.
        commands: List of show commands to execute.
        encoding: Response encoding ('json' or 'text').
        cache_ttl: Cache TTL in seconds. None = use default TTL per command pattern.
                   0 = bypass cache (always query device). Negative = bypass cache.

    On success: {"status": "success", "device": host, "data": <result_list>}
    On failure: {"status": "error", "device": host, "error": "<message>", "error_code": "...", "is_retryable": bool}

    Raises ToolError for input validation failures (host format).
    Returns error dicts for runtime failures (connection, command errors).
    """
    # Reject new work during shutdown
    from network_mcp.server import increment_in_flight

    if not increment_in_flight():
        return _attach_request_id(
            make_error_response(
                ErrorCode.SERVER_SHUTTING_DOWN,
                host,
                "Server is shutting down — no new requests accepted",
            )
        )

    try:
        return _run_show_command_inner(conn_mgr, host, commands, encoding, cache_ttl)
    finally:
        from network_mcp.server import decrement_in_flight

        decrement_in_flight()


def _run_show_command_inner(
    conn_mgr: "ConnectionManager",
    host: str,
    commands: list[str],
    encoding: str = "json",
    cache_ttl: int | None = None,
) -> dict:
    """Inner implementation of run_show_command, called after in-flight tracking is set up."""
    host_err = validate_host(host)
    if host_err:
        raise ToolError(host_err)

    # Tenant device access check
    tenant_err = check_tenant_device_access(host)
    if tenant_err:
        return _attach_request_id(make_error_response(ErrorCode.ACCESS_DENIED, host, tenant_err))

    from network_mcp.middleware import get_correlation_id
    from network_mcp.telemetry import record_cache_hit as otel_record_cache

    tool_name = commands[0] if commands else "show_command"
    correlation_id = get_correlation_id()

    # Resolve effective TTL
    effective_ttl = cache_ttl if cache_ttl is not None else get_default_ttl(commands)
    stale_ttl = _get_settings().net_cache_stale_if_error_ttl

    # Check cache before hitting the device
    mc = _get_metrics_collector()
    if effective_ttl > 0:
        cached = command_cache.get(host, commands, encoding)
        if cached is not None:
            mc.record_cache_hit()
            otel_record_cache(hit=True)
            return cached  # type: ignore[return-value]
        mc.record_cache_miss()
        otel_record_cache(hit=False)

    # Per-user / per-tenant rate limit check (before device-level)
    user_tenant_err = _check_user_tenant_rate_limit(is_config=False)
    if user_tenant_err:
        user_tenant_err["device"] = host
        return _attach_request_id(user_tenant_err)

    # Circuit breaker check (fail fast if device is known-unreachable)
    if not circuit_breaker_registry.allow_request(host):
        # Try stale cache before returning circuit breaker error
        if effective_ttl > 0 and stale_ttl > 0:
            stale = command_cache.get(host, commands, encoding, stale_if_error=True, stale_ttl=stale_ttl)
            if stale is not None:
                logger.warning("Device %s circuit open, serving stale cache data", host)
                return _attach_request_id(stale) if isinstance(stale, dict) else stale
        breaker = circuit_breaker_registry.get(host)
        return _attach_request_id(
            make_error_response(
                ErrorCode.CIRCUIT_OPEN,
                host,
                (
                    f"Circuit breaker open for {host}. "
                    f"Device has had {breaker.consecutive_failures} consecutive failures. "
                    f"Will retry after cooldown."
                ),
            )
        )

    # Rate limit check (with exponential backoff)
    settings = _get_settings()
    max_wait = settings.net_rate_limit_max_wait
    if max_wait > 0:
        if not device_rate_limiter.check_show_with_backoff(host, max_wait=max_wait):
            mc.record_rate_limit_throttle()
            wait = device_rate_limiter.show_wait_time(host)
            return _attach_request_id(
                make_error_response(
                    ErrorCode.RATE_LIMITED,
                    host,
                    (
                        f"Rate limit exceeded for device {host}. "
                        f"Please wait approximately {wait:.1f} seconds before retrying. "
                        f"This protects the device's management plane."
                    ),
                    retry_after_seconds=round(wait, 2),
                )
            )
    elif not device_rate_limiter.check_show(host):
        mc.record_rate_limit_throttle()
        wait = device_rate_limiter.show_wait_time(host)
        return _attach_request_id(
            make_error_response(
                ErrorCode.RATE_LIMITED,
                host,
                (
                    f"Rate limit exceeded for device {host}. "
                    f"Please wait approximately {wait:.1f} seconds before retrying. "
                    f"This protects the device's management plane."
                ),
                retry_after_seconds=round(wait, 2),
            )
        )

    # Bulkhead isolation: acquire per-vendor slot
    from network_mcp.connection import bulkhead_slot

    vendor = conn_mgr.get_platform(host)
    try:
        with bulkhead_slot(vendor, timeout=settings.net_conn_acquire_timeout):
            return _run_show_with_bulkhead(
                conn_mgr, host, commands, encoding, effective_ttl, stale_ttl, tool_name, correlation_id, mc
            )
    except TimeoutError as e:
        return _attach_request_id(
            make_error_response(
                ErrorCode.BULKHEAD_TIMEOUT,
                host,
                str(e),
            )
        )


def _run_show_with_bulkhead(
    conn_mgr: "ConnectionManager",
    host: str,
    commands: list[str],
    encoding: str,
    effective_ttl: int,
    stale_ttl: int,
    tool_name: str,
    correlation_id: str | None,
    mc,
) -> dict:
    """Execute show commands inside a bulkhead slot."""
    from network_mcp.telemetry import record_tool_call as otel_record
    from network_mcp.telemetry import trace_tool_call

    start = time.monotonic()
    with trace_tool_call(
        tool_name, host=host, correlation_id=correlation_id, command_count=len(commands), command_encoding=encoding
    ) as span:
        try:
            cache_hit = False
            if span:
                span.set_attribute("cache.hit", cache_hit)

            with conn_mgr.acquire(host) as driver:

                @RETRY_SHOW
                def _execute():
                    return driver.run_show(commands, encoding=encoding)

                result_data = _execute()
                result = {"status": "success", "device": host, "data": result_data}
                result = enforce_dict_output_limit(result)
                if isinstance(result["data"], dict):
                    result["data"] = sanitize_dict_values(result["data"], context="device_output")
                elif isinstance(result["data"], list):
                    result["data"] = [
                        sanitize_dict_values(item, context="device_output") if isinstance(item, dict) else item
                        for item in result["data"]
                    ]
                if span:
                    span.set_attribute("result.status", "success")
                duration_ms = (time.monotonic() - start) * 1000
                log_tool_invocation(
                    "run_show_command", host, {"commands": commands, "encoding": encoding}, result, duration_ms
                )
                mc.record_tool_call("run_show_command", "success", duration_ms)
                otel_record("run_show_command", "success", duration_ms, host)
                _sli = _get_sli_tracker()
                if _sli:
                    _sli.record_latency("run_show_command", duration_ms / 1000.0)
                    _sli.record_outcome("run_show_command", True)

                # Populate cache on success
                if effective_ttl > 0:
                    command_cache.put(host, commands, encoding, result, effective_ttl)

                circuit_breaker_registry.record_success(host)
                return _attach_request_id(result)
        except pyeapi.eapilib.ConnectionError as e:
            circuit_breaker_registry.record_failure(host)
            # Try stale cache before returning error
            if effective_ttl > 0 and stale_ttl > 0:
                stale = command_cache.get(host, commands, encoding, stale_if_error=True, stale_ttl=stale_ttl)
                if stale is not None:
                    logger.warning("Device %s unreachable, serving stale cache data", host)
                    if span:
                        span.set_attribute("result.status", "stale_cache")
                    duration_ms = (time.monotonic() - start) * 1000
                    mc.record_tool_call("run_show_command", "stale_cache", duration_ms)
                    return _attach_request_id(stale) if isinstance(stale, dict) else stale
            result = _attach_request_id(
                make_error_response(
                    ErrorCode.CONNECTION_ERROR,
                    host,
                    (
                        f"Connection to {host} failed: {sanitize_error_message(str(e))}. "
                        "Verify: (1) device is reachable, (2) API access is enabled "
                        "(eAPI for EOS, RESTCONF for IOS-XE, NX-API for NX-OS, NETCONF for JunOS), "
                        "(3) credentials are correct."
                    ),
                )
            )
            if span:
                span.set_attribute("result.status", "error")
            duration_ms = (time.monotonic() - start) * 1000
            log_tool_invocation("run_show_command", host, {"commands": commands}, result, duration_ms)
            mc.record_tool_call("run_show_command", "error", duration_ms)
            otel_record("run_show_command", "error", duration_ms, host)
            _sli = _get_sli_tracker()
            if _sli:
                _sli.record_latency("run_show_command", duration_ms / 1000.0)
                _sli.record_outcome("run_show_command", False)
            return result
        except pyeapi.eapilib.CommandError as e:
            # CommandError means device is reachable — don't count as circuit breaker failure
            circuit_breaker_registry.record_success(host)
            result = _attach_request_id(
                make_error_response(
                    ErrorCode.COMMAND_ERROR,
                    host,
                    (
                        f"Command error on {host}: {sanitize_error_message(str(e))}. "
                        "Check that the command syntax is correct and supported on this platform version."
                    ),
                )
            )
            if span:
                span.set_attribute("result.status", "error")
            duration_ms = (time.monotonic() - start) * 1000
            log_tool_invocation("run_show_command", host, {"commands": commands}, result, duration_ms)
            mc.record_tool_call("run_show_command", "error", duration_ms)
            otel_record("run_show_command", "error", duration_ms, host)
            _sli = _get_sli_tracker()
            if _sli:
                _sli.record_latency("run_show_command", duration_ms / 1000.0)
                _sli.record_outcome("run_show_command", False)
            return result
        except Exception as e:
            circuit_breaker_registry.record_failure(host)
            # Try stale cache before returning error
            if effective_ttl > 0 and stale_ttl > 0:
                stale = command_cache.get(host, commands, encoding, stale_if_error=True, stale_ttl=stale_ttl)
                if stale is not None:
                    logger.warning("Device %s error, serving stale cache data", host)
                    if span:
                        span.set_attribute("result.status", "stale_cache")
                    duration_ms = (time.monotonic() - start) * 1000
                    mc.record_tool_call("run_show_command", "stale_cache", duration_ms)
                    return _attach_request_id(stale) if isinstance(stale, dict) else stale
            result = _attach_request_id(
                make_error_response(
                    ErrorCode.INTERNAL_ERROR,
                    host,
                    f"Unexpected error on {host}: {sanitize_error_message(str(e))}",
                )
            )
            if span:
                span.set_attribute("result.status", "error")
            duration_ms = (time.monotonic() - start) * 1000
            log_tool_invocation("run_show_command", host, {"commands": commands}, result, duration_ms)
            mc.record_tool_call("run_show_command", "error", duration_ms)
            otel_record("run_show_command", "error", duration_ms, host)
            _sli = _get_sli_tracker()
            if _sli:
                _sli.record_latency("run_show_command", duration_ms / 1000.0)
                _sli.record_outcome("run_show_command", False)
            return result


# --- Read-Only Mode Check ---


def check_read_only() -> str | None:
    """Check if server is in read-only mode. Returns error message if read-only, None if writes are allowed."""
    s = _get_settings()
    if s.net_read_only:
        return (
            "Write operations are disabled. Set NET_READ_ONLY=false in your "
            "environment or .env file to enable configuration changes."
        )
    return None


# --- Config Command Deny List ---

DENIED_COMMANDS = [
    "reload",
    "write erase",
    "zerotouch",
    "delete flash:",
    "delete startup",
    "delete bootflash:",
    "format",
    "boot system",
    "aaa root",
    "bash",
    "configure replace",
    "copy startup",
    "configure terminal",
    "erase",
    "copy erase",
    "clear logging",
    "clear counters",
    "no management api http-commands",
    "no management api gnmi",
]


def validate_config_commands(commands: list[str]) -> str | None:
    """Validate config commands against deny list. Returns error message if blocked, None if OK."""
    for cmd in commands:
        cmd_lower = cmd.strip().lower()
        for denied in DENIED_COMMANDS:
            if cmd_lower.startswith(denied):
                return (
                    f"Command '{cmd}' is blocked by the safety deny-list. "
                    "This command cannot be executed through MCP for safety reasons."
                )
    return None


# --- Change Management (lazy singleton) ---

_change_manager = None


def _get_change_manager():
    """Lazily create and cache the ChangeManager singleton."""
    global _change_manager
    if _change_manager is None:
        from network_mcp.change_management import ChangeManager

        _change_manager = ChangeManager(_get_settings())
    return _change_manager


def get_change_manager():
    """Public accessor for the ChangeManager singleton (used by MCP tools)."""
    return _get_change_manager()


# --- Config Command Execution Helper ---


def run_config_command(
    conn_mgr: "ConnectionManager",
    host: str,
    commands: list[str],
    action: str,
    idempotency_key: str | None = None,
) -> dict:
    """Execute config commands with read-only check, validation, retry, rate limiting, and audit.

    When change management is enabled (NET_CHANGE_MGMT_ENABLED=true), wraps execution
    with: change request creation -> maintenance window check -> pre-snapshot ->
    execution -> post-snapshot -> validation (with optional auto-rollback).

    Args:
        conn_mgr: Connection manager instance.
        host: Target device.
        commands: List of config commands (e.g., ["vlan 100", "name SERVERS"]).
        action: Description of the action (e.g., "create_vlan").
        idempotency_key: Optional key to prevent duplicate execution. When provided,
            the first execution result is cached and subsequent calls with the same
            key return the cached result without re-executing.

    Returns dict with:
        - status "success" + device/action/data on success
        - status "error" + device/error/error_code/is_retryable on failure
        - status "pending_approval" when change management requires approval
    Raises ToolError for input validation failures.
    """
    # Check idempotency key first (before any side effects)
    _idem_acquired = False
    if idempotency_key:
        from network_mcp.idempotency import get_idempotency_store

        store = get_idempotency_store()
        if store:
            acquire_result = store.acquire(idempotency_key)
            if acquire_result is True:
                # We acquired the key — proceed with execution
                _idem_acquired = True
            elif isinstance(acquire_result, dict):
                # Cached or waited result
                logger.info("Idempotency key hit: %s — returning cached result", idempotency_key)
                cached_result = dict(acquire_result)
                cached_result["_idempotency"] = {"key": idempotency_key, "cached": True}
                return cached_result
            else:
                # Timed out waiting — proceed without idempotency
                logger.warning("Idempotency wait timed out for key %s", idempotency_key)

    # Reject new work during shutdown
    from network_mcp.server import increment_in_flight

    if not increment_in_flight():
        if _idem_acquired and idempotency_key:
            store = get_idempotency_store()
            if store:
                store.cancel(idempotency_key)
        return _attach_request_id(
            make_error_response(
                ErrorCode.SERVER_SHUTTING_DOWN,
                host,
                "Server is shutting down — no new requests accepted",
            )
        )

    try:
        result = _run_config_command_inner(conn_mgr, host, commands, action)

        # Store result under idempotency key on success
        if idempotency_key and result.get("status") == "success":
            from network_mcp.idempotency import get_idempotency_store

            store = get_idempotency_store()
            if store:
                if _idem_acquired:
                    store.complete(idempotency_key, result)
                else:
                    store.set(idempotency_key, result)
        elif _idem_acquired and idempotency_key:
            # Execution failed — cancel in-flight
            from network_mcp.idempotency import get_idempotency_store

            store = get_idempotency_store()
            if store:
                store.cancel(idempotency_key)

        return result
    finally:
        from network_mcp.server import decrement_in_flight

        decrement_in_flight()


def _run_config_command_inner(conn_mgr: "ConnectionManager", host: str, commands: list[str], action: str) -> dict:
    """Inner implementation of run_config_command, called after in-flight tracking is set up."""
    from network_mcp.middleware import get_correlation_id

    ro_err = check_read_only()
    if ro_err:
        return _attach_request_id(make_error_response(ErrorCode.READ_ONLY_MODE, host, ro_err))

    host_err = validate_host(host)
    if host_err:
        raise ToolError(host_err)

    # Tenant device access check
    tenant_err = check_tenant_device_access(host)
    if tenant_err:
        return _attach_request_id(make_error_response(ErrorCode.ACCESS_DENIED, host, tenant_err))

    correlation_id = get_correlation_id()

    # Deny-list check (centrally enforced for all config commands)
    deny_err = validate_config_commands(commands)
    if deny_err:
        return _attach_request_id(make_error_response(ErrorCode.CONFIG_REJECTED, host, deny_err))

    # --- Change management gate ---
    cm = _get_change_manager()
    cr = None
    if cm.enabled:
        from network_mcp.middleware import get_current_user

        user = get_current_user() or ""
        cr = cm.create_change_request(action, host, commands, user=user)

        # Check maintenance window
        if not cm.check_maintenance_window():
            cm.mark_failed(cr, "Outside maintenance window")
            return _attach_request_id(
                {
                    "status": "error",
                    "device": host,
                    "error": "Change rejected: current time is outside configured maintenance windows.",
                    "change_id": cr.id,
                    "risk": cr.risk.value,
                }
            )

        # If approval is required and change is not yet approved, return pending
        if cr.status.value == "pending":
            return {
                "status": "pending_approval",
                "device": host,
                "change_id": cr.id,
                "risk": cr.risk.value,
                "action": action,
                "commands": commands,
                "message": (
                    f"Change request {cr.id} requires approval. "
                    f"Risk level: {cr.risk.value}. Use net_approve_change to approve."
                ),
            }

        # Capture pre-snapshot
        cm.capture_pre_snapshot(cr, conn_mgr)
        cm.mark_executing(cr)

    # Per-user / per-tenant rate limit check (before device-level)
    user_tenant_err = _check_user_tenant_rate_limit(is_config=True)
    if user_tenant_err:
        user_tenant_err["device"] = host
        if cr:
            cm.mark_failed(cr, user_tenant_err["error"])
        return _attach_request_id(user_tenant_err)

    # Circuit breaker check (fail fast if device is known-unreachable)
    if not circuit_breaker_registry.allow_request(host):
        breaker = circuit_breaker_registry.get(host)
        if cr:
            cm.mark_failed(cr, f"Circuit breaker open for {host}")
        return _attach_request_id(
            make_error_response(
                ErrorCode.CIRCUIT_OPEN,
                host,
                (
                    f"Circuit breaker open for {host}. "
                    f"Device has had {breaker.consecutive_failures} consecutive failures. "
                    f"Will retry after cooldown."
                ),
            )
        )

    # Rate limit check (with exponential backoff)
    mc = _get_metrics_collector()
    settings = _get_settings()
    cfg_max_wait = settings.net_rate_limit_max_wait
    if cfg_max_wait > 0:
        if not device_rate_limiter.check_config_with_backoff(host, max_wait=cfg_max_wait):
            mc.record_rate_limit_throttle()
            wait = device_rate_limiter.config_wait_time(host)
            if cr:
                cm.mark_failed(cr, f"Rate limit exceeded for {host}")
            return _attach_request_id(
                make_error_response(
                    ErrorCode.RATE_LIMITED,
                    host,
                    (
                        f"Config rate limit exceeded for device {host}. "
                        f"Please wait approximately {wait:.1f} seconds before retrying. "
                        f"This protects the device's management plane."
                    ),
                    retry_after_seconds=round(wait, 2),
                )
            )
    elif not device_rate_limiter.check_config(host):
        mc.record_rate_limit_throttle()
        wait = device_rate_limiter.config_wait_time(host)
        if cr:
            cm.mark_failed(cr, f"Rate limit exceeded for {host}")
        return _attach_request_id(
            make_error_response(
                ErrorCode.RATE_LIMITED,
                host,
                (
                    f"Config rate limit exceeded for device {host}. "
                    f"Please wait approximately {wait:.1f} seconds before retrying. "
                    f"This protects the device's management plane."
                ),
                retry_after_seconds=round(wait, 2),
            )
        )

    # Bulkhead isolation: acquire per-vendor slot
    from network_mcp.connection import bulkhead_slot

    vendor = conn_mgr.get_platform(host)
    try:
        with bulkhead_slot(vendor, timeout=settings.net_conn_acquire_timeout):
            return _run_config_with_bulkhead(conn_mgr, host, commands, action, correlation_id, cr, cm, mc)
    except TimeoutError as e:
        if cr:
            cm.mark_failed(cr, str(e))
        return _attach_request_id(
            make_error_response(
                ErrorCode.BULKHEAD_TIMEOUT,
                host,
                str(e),
            )
        )


def _run_config_with_bulkhead(
    conn_mgr: "ConnectionManager",
    host: str,
    commands: list[str],
    action: str,
    correlation_id: str | None,
    cr,
    cm,
    mc,
) -> dict:
    """Execute config commands inside a bulkhead slot."""
    from network_mcp.telemetry import record_tool_call as otel_record
    from network_mcp.telemetry import trace_tool_call

    start = time.monotonic()
    with trace_tool_call(action, host=host, correlation_id=correlation_id, command_count=len(commands)) as span:
        try:
            with conn_mgr.acquire(host) as driver:

                @_get_config_retry()
                def _execute():
                    return driver.run_config(commands)

                _execute()

                # Invalidate cache for this host after successful config change
                command_cache.invalidate_host(host)

                circuit_breaker_registry.record_success(host)
                result = {
                    "status": "success",
                    "device": host,
                    "action": action,
                    "data": {"commands": commands},
                    "message": f"Configuration applied successfully: {action}",
                }

                # --- Change management post-validation ---
                if cr and cm.enabled:
                    cm.capture_post_snapshot(cr, conn_mgr)
                    if cm.auto_rollback_timeout > 0 and not cm.validate_change(cr):
                        logger.warning("Change %s failed validation, auto-rolling back", cr.id)
                        rollback_result = cm.rollback(cr, conn_mgr)
                        result["change_management"] = {
                            "change_id": cr.id,
                            "validation": "failed",
                            "action": "rolled_back",
                            "rollback": rollback_result,
                        }
                        result["status"] = "error"
                        result["error"] = "Change applied but failed post-validation; rolled back automatically."
                    else:
                        cm.mark_completed(cr)
                        result["change_management"] = {
                            "change_id": cr.id,
                            "risk": cr.risk.value,
                            "validation": "passed" if cr.post_snapshot else "skipped",
                        }

                if span:
                    span.set_attribute("result.status", result.get("status", "unknown"))
                duration_ms = (time.monotonic() - start) * 1000
                log_tool_invocation(action, host, {"commands": commands}, result, duration_ms)
                mc.record_tool_call("run_config_command", "success", duration_ms)
                otel_record("run_config_command", "success", duration_ms, host)
                _sli = _get_sli_tracker()
                if _sli:
                    _sli.record_latency("run_config_command", duration_ms / 1000.0)
                    _sli.record_outcome("run_config_command", True)
                return _attach_request_id(result)
        except pyeapi.eapilib.ConnectionError as e:
            circuit_breaker_registry.record_failure(host)
            if cr:
                cm.mark_failed(cr, f"Connection failed: {sanitize_error_message(str(e))}")
            result = _attach_request_id(
                make_error_response(
                    ErrorCode.CONNECTION_ERROR,
                    host,
                    (
                        f"Connection to {host} failed: {sanitize_error_message(str(e))}. "
                        "Verify: (1) device is reachable, (2) API access is enabled "
                        "(eAPI for EOS, RESTCONF for IOS-XE, NX-API for NX-OS, NETCONF for JunOS), "
                        "(3) credentials are correct."
                    ),
                )
            )
            if span:
                span.set_attribute("result.status", "error")
            duration_ms = (time.monotonic() - start) * 1000
            log_tool_invocation(action, host, {"commands": commands}, result, duration_ms)
            mc.record_tool_call("run_config_command", "error", duration_ms)
            otel_record("run_config_command", "error", duration_ms, host)
            _sli = _get_sli_tracker()
            if _sli:
                _sli.record_latency("run_config_command", duration_ms / 1000.0)
                _sli.record_outcome("run_config_command", False)
            return result
        except pyeapi.eapilib.CommandError as e:
            # CommandError means device is reachable — don't count as circuit breaker failure
            circuit_breaker_registry.record_success(host)
            if cr:
                cm.mark_failed(cr, f"Command error: {sanitize_error_message(str(e))}")
            result = _attach_request_id(
                make_error_response(
                    ErrorCode.COMMAND_ERROR,
                    host,
                    (
                        f"Configuration command error on {host}: {sanitize_error_message(str(e))}. "
                        "Check that the command syntax is correct and supported on this platform version."
                    ),
                )
            )
            if span:
                span.set_attribute("result.status", "error")
            duration_ms = (time.monotonic() - start) * 1000
            log_tool_invocation(action, host, {"commands": commands}, result, duration_ms)
            mc.record_tool_call("run_config_command", "error", duration_ms)
            otel_record("run_config_command", "error", duration_ms, host)
            _sli = _get_sli_tracker()
            if _sli:
                _sli.record_latency("run_config_command", duration_ms / 1000.0)
                _sli.record_outcome("run_config_command", False)
            return result
        except Exception as e:
            circuit_breaker_registry.record_failure(host)
            if cr:
                cm.mark_failed(cr, f"Unexpected error: {sanitize_error_message(str(e))}")
            result = _attach_request_id(
                make_error_response(
                    ErrorCode.INTERNAL_ERROR,
                    host,
                    f"Unexpected error on {host}: {sanitize_error_message(str(e))}",
                )
            )
            if span:
                span.set_attribute("result.status", "error")
            duration_ms = (time.monotonic() - start) * 1000
            log_tool_invocation(action, host, {"commands": commands}, result, duration_ms)
            mc.record_tool_call("run_config_command", "error", duration_ms)
            otel_record("run_config_command", "error", duration_ms, host)
            _sli = _get_sli_tracker()
            if _sli:
                _sli.record_latency("run_config_command", duration_ms / 1000.0)
                _sli.record_outcome("run_config_command", False)
            return result


# --- Session/Checkpoint Command Execution Helper ---


def run_session_command(conn_mgr: "ConnectionManager", host: str, commands: list[str], action: str) -> dict:
    """Execute raw commands (e.g., configure session, checkpoint) with ref-count protection, rate limiting, and audit.

    Unlike run_config_command which uses driver.run_config() (auto-enters config mode),
    this uses driver.run_show() for commands that must be sent as top-level exec commands
    (e.g., 'configure session X', 'commit', 'configure checkpoint save X').

    Does NOT check read-only or deny-list — callers must handle those checks themselves
    since session/checkpoint commands don't match the standard deny-list patterns.

    Args:
        conn_mgr: Connection manager instance.
        host: Target device hostname, IP, or inventory name.
        commands: List of exec commands to run (e.g., ["configure session X", "commit"]).
        action: Description of the action (for audit logging).
    """
    # Tenant device access check (M5)
    tenant_err = check_tenant_device_access(host)
    if tenant_err:
        return _attach_request_id(make_error_response(ErrorCode.RBAC_DENIED, host, tenant_err))

    # Circuit breaker check
    if not circuit_breaker_registry.allow_request(host):
        breaker = circuit_breaker_registry.get(host)
        return _attach_request_id(
            make_error_response(
                ErrorCode.CIRCUIT_OPEN,
                host,
                (
                    f"Circuit breaker open for {host}. "
                    f"Device has had {breaker.consecutive_failures} consecutive failures. "
                    f"Will retry after cooldown."
                ),
            )
        )

    # Rate limit check (use config rate since these are write-like operations)
    mc = _get_metrics_collector()
    if not device_rate_limiter.check_config(host):
        mc.record_rate_limit_throttle()
        wait = device_rate_limiter.config_wait_time(host)
        return _attach_request_id(
            make_error_response(
                ErrorCode.RATE_LIMITED,
                host,
                (
                    f"Config rate limit exceeded for device {host}. "
                    f"Please wait approximately {wait:.1f} seconds before retrying. "
                    f"This protects the device's management plane."
                ),
            )
        )

    start = time.monotonic()
    try:
        with conn_mgr.acquire(host) as driver:
            driver.run_show(commands)

            # Invalidate cache after successful session/checkpoint operation
            command_cache.invalidate_host(host)

            circuit_breaker_registry.record_success(host)
            result = {"status": "success", "device": host}
            duration_ms = (time.monotonic() - start) * 1000
            log_tool_invocation(action, host, {"commands": commands}, result, duration_ms)
            mc.record_tool_call("run_session_command", "success", duration_ms)
            return result
    except pyeapi.eapilib.ConnectionError as e:
        circuit_breaker_registry.record_failure(host)
        result = _attach_request_id(
            make_error_response(
                ErrorCode.CONNECTION_ERROR,
                host,
                (f"Connection to {host} failed: {sanitize_error_message(str(e))}. {CONNECTION_ERROR_ADVICE}"),
            )
        )
        duration_ms = (time.monotonic() - start) * 1000
        log_tool_invocation(action, host, {"commands": commands}, result, duration_ms)
        mc.record_tool_call("run_session_command", "error", duration_ms)
        return result
    except pyeapi.eapilib.CommandError as e:
        circuit_breaker_registry.record_success(host)
        result = _attach_request_id(
            make_error_response(
                ErrorCode.COMMAND_ERROR,
                host,
                (
                    f"Configuration command error on {host}: {sanitize_error_message(str(e))}. "
                    "Check that the command syntax is correct and supported on this platform version."
                ),
            )
        )
        duration_ms = (time.monotonic() - start) * 1000
        log_tool_invocation(action, host, {"commands": commands}, result, duration_ms)
        mc.record_tool_call("run_session_command", "error", duration_ms)
        return result
    except Exception as e:
        circuit_breaker_registry.record_failure(host)
        result = _attach_request_id(
            make_error_response(
                ErrorCode.INTERNAL_ERROR,
                host,
                f"Unexpected error on {host}: {sanitize_error_message(str(e))}",
            )
        )
        duration_ms = (time.monotonic() - start) * 1000
        log_tool_invocation(action, host, {"commands": commands}, result, duration_ms)
        mc.record_tool_call("run_session_command", "error", duration_ms)
        return result


# ---------------------------------------------------------------------------
# Intent-based config command (template engine integration)
# ---------------------------------------------------------------------------


def run_intent_command(
    conn_mgr: "ConnectionManager",
    host: str,
    intent: str,
    params: dict,
    platform: str,
    idempotency_key: str | None = None,
) -> dict:
    """Render intent-based commands via the template engine, then execute via run_config_command.

    This is a convenience wrapper that translates a high-level intent (e.g.
    ``'create_vlan'``) into vendor-specific CLI commands using the template
    engine, then delegates to ``run_config_command`` for execution.

    Args:
        conn_mgr: Connection manager instance.
        host: Target device.
        intent: Template intent name (e.g. 'create_vlan', 'set_interface_description').
        params: Parameters for the intent template.
        platform: Target platform ('eos', 'iosxe', 'nxos', 'junos').
        idempotency_key: Optional idempotency key (forwarded to run_config_command).

    Returns:
        Dict with status, device, action, and data (includes rendered commands).
    """
    from network_mcp.templates import TemplateError, render_commands

    try:
        commands = render_commands(intent, params, platform)
    except TemplateError as exc:
        return _attach_request_id(make_error_response(ErrorCode.VALIDATION_ERROR, host, str(exc)))

    result = run_config_command(conn_mgr, host, commands, intent, idempotency_key)
    if result.get("status") == "success" and "data" in result:
        result["data"]["rendered_commands"] = commands
        result["data"]["intent"] = intent
        result["data"]["platform"] = platform
    return result
