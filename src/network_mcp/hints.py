"""Actionable remediation hints for error responses.

Maps error codes and message patterns to human-readable suggestions that help
operators quickly identify and resolve the root cause of a failure.
"""

from __future__ import annotations

# Default hints keyed by ErrorCode string value.
# These are shown when no more-specific pattern matches the error message.
_ERROR_CODE_HINTS: dict[str, str] = {
    "CONNECTION_ERROR": (
        "Check the device IP/hostname and verify the management API is enabled "
        "(eAPI for EOS, RESTCONF for IOS-XE, NX-API for NX-OS, NETCONF for JunOS)"
    ),
    "DEVICE_UNREACHABLE": (
        "Check the device IP/hostname and verify the management API is enabled "
        "(eAPI for EOS, RESTCONF for IOS-XE, NX-API for NX-OS, NETCONF for JunOS)"
    ),
    "AUTH_ERROR": "Verify NET_USERNAME and NET_PASSWORD environment variables match the device credentials",
    "TIMEOUT_ERROR": "Check network connectivity to the device or increase NET_TIMEOUT in your environment",
    "COMMAND_ERROR": (
        "This command may not be available on this platform or OS version; "
        "check vendor documentation or try 'show version' to confirm platform support"
    ),
    "RATE_LIMITED": (
        "Reduce the frequency of requests or increase RATE_LIMIT_SHOW / RATE_LIMIT_CONFIG "
        "in your environment"
    ),
    "CIRCUIT_OPEN": (
        "Wait for the circuit breaker cooldown period to pass, then verify device reachability"
    ),
    "READ_ONLY_MODE": (
        "Set NET_READ_ONLY=false in your environment or .env file to enable write operations"
    ),
    "CONFIG_REJECTED": (
        "Review the denied commands list; use safe configuration methods supported by MCP"
    ),
    "BULKHEAD_TIMEOUT": (
        "Reduce concurrent requests to this vendor platform or increase NET_CONN_ACQUIRE_TIMEOUT"
    ),
    "ACCESS_DENIED": "Ensure this device is included in the tenant's allowed device list",
    "RBAC_DENIED": (
        "Request the appropriate scope (network:read / network:write / network:admin) "
        "from your administrator"
    ),
    "OUTPUT_TOO_LARGE": (
        "Use filtering parameters (fields, limit) to reduce response size, "
        "or increase NET_MAX_OUTPUT_SIZE"
    ),
}

# Pattern-based hints — (keywords, hint) pairs checked against the lower-cased error message.
# More-specific patterns are listed first and take priority over the error-code default.
_PATTERN_HINTS: list[tuple[tuple[str, ...], str]] = [
    (
        ("connection refused", "refused"),
        (
            "Check that the device IP/port is correct and the management API service is running "
            "(eAPI on port 443/80, RESTCONF on 443, NX-API on 80/443, NETCONF on 830)"
        ),
    ),
    (
        ("ssl", "certificate", "tls handshake", "verify failed"),
        (
            "Check SSL certificate configuration; "
            "set NET_VERIFY_CERTS=false to accept self-signed certificates"
        ),
    ),
    (
        ("authentication failed", "auth failed", "unauthorized", "401", "invalid credentials", "login failed"),
        "Verify NET_USERNAME and NET_PASSWORD environment variables match the device credentials",
    ),
    (
        ("timed out", "timeout", "time out", "read timeout", "connect timeout"),
        "Check network connectivity to the device or increase NET_TIMEOUT in your environment",
    ),
    (
        (
            "not supported",
            "unsupported command",
            "invalid command",
            "unknown command",
            "incomplete command",
            "% invalid",
            "command rejected",
        ),
        (
            "This command may not be available on this platform or OS version; "
            "check vendor documentation or confirm with 'show version'"
        ),
    ),
]


def get_hint(error_code: str, error_message: str = "") -> str | None:
    """Return an actionable remediation hint for the given error code and message.

    Pattern-based matching is performed first (more specific); if no pattern matches,
    falls back to the error-code-level default hint.

    Args:
        error_code: The ErrorCode value string (e.g., ``"CONNECTION_ERROR"``).
        error_message: The full error message string used for pattern matching.

    Returns:
        A hint string, or ``None`` if no hint is registered for this error.
    """
    msg_lower = error_message.lower()
    for keywords, hint in _PATTERN_HINTS:
        if any(kw in msg_lower for kw in keywords):
            return hint
    return _ERROR_CODE_HINTS.get(error_code)
