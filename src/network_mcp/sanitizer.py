"""Config output sanitization, prompt injection defense, and output size limits."""

import json
import logging
import re
from typing import Any, NamedTuple

logger = logging.getLogger("network-mcp")

# ---------------------------------------------------------------------------
# 1. Config Output Sanitization (passwords, keys, community strings, etc.)
# ---------------------------------------------------------------------------


class SanitizationRule(NamedTuple):
    name: str
    pattern: re.Pattern
    replacement: str


CONFIG_SANITIZATION_RULES = [
    # EOS Type-7 encrypted passwords (format: password 7 <hex> or key 7 <hex>)
    SanitizationRule(
        "type7_password",
        re.compile(r"(\s+(?:password|secret|key)\s+)7\s+\S+", re.IGNORECASE),
        r"\1***ENCRYPTED***",
    ),
    # Bare key values without type prefix (e.g., 'key <value>')
    SanitizationRule(
        "generic_key",
        re.compile(r"(\s+key\s+)\S+", re.IGNORECASE),
        r"\1****",
    ),
    # SHA-512 hashed passwords (format: secret sha512 $6$...)
    SanitizationRule(
        "sha512_password",
        re.compile(r"(\s+secret\s+)sha512\s+\$6\$\S+", re.IGNORECASE),
        r"\1***HASHED***",
    ),
    # Cleartext passwords (format: password 0 <password> or secret 0 <password>)
    SanitizationRule(
        "cleartext_password",
        re.compile(r"(\s+(?:password|secret)\s+)0\s+\S+", re.IGNORECASE),
        r"\1***CLEARTEXT_REDACTED***",
    ),
    # SNMP community strings
    SanitizationRule(
        "snmp_community",
        re.compile(
            r"(snmp-server\s+community\s+)\S+(\s+.*)?$",
            re.IGNORECASE | re.MULTILINE,
        ),
        r"\1***REDACTED***\2",
    ),
    # TACACS/RADIUS server keys
    SanitizationRule(
        "tacacs_radius_key",
        re.compile(
            r"((?:tacacs|radius)-server\s+.*\s+key\s+)(?:7\s+)?\S+",
            re.IGNORECASE,
        ),
        r"\1***REDACTED***",
    ),
    # BGP neighbor passwords
    SanitizationRule(
        "bgp_password",
        re.compile(
            r"(neighbor\s+\S+\s+password\s+)(?:7\s+)?\S+",
            re.IGNORECASE,
        ),
        r"\1***REDACTED***",
    ),
    # OSPF authentication keys
    SanitizationRule(
        "ospf_auth_key",
        re.compile(
            r"(ip\s+ospf\s+(?:message-digest-key\s+\d+\s+(?:md5|sha\S*)\s+)(?:7\s+)?)\S+",
            re.IGNORECASE,
        ),
        r"\1***REDACTED***",
    ),
    # NTP authentication keys
    SanitizationRule(
        "ntp_key",
        re.compile(
            r"(ntp\s+authentication-key\s+\d+\s+(?:md5|sha\S*)\s+(?:7\s+)?)\S+",
            re.IGNORECASE,
        ),
        r"\1***REDACTED***",
    ),
    # Enable secret
    SanitizationRule(
        "enable_secret",
        re.compile(
            r"(enable\s+(?:secret|password)\s+)(?:(?:0|5|7|sha512)\s+)?\S+",
            re.IGNORECASE,
        ),
        r"\1***REDACTED***",
    ),
    # Certificate/key blocks (multiline)
    SanitizationRule(
        "certificate_block",
        re.compile(
            r"(-----BEGIN\s+(?:RSA\s+)?(?:PRIVATE\s+KEY|CERTIFICATE)-----)"
            r"[\s\S]*?"
            r"(-----END\s+(?:RSA\s+)?(?:PRIVATE\s+KEY|CERTIFICATE)-----)",
            re.IGNORECASE,
        ),
        r"\1\n***CERTIFICATE_MATERIAL_REDACTED***\n\2",
    ),
    # API secrets and tokens
    SanitizationRule(
        "api_secret",
        re.compile(
            r"((?:api|token|auth)\s*[-_]?\s*(?:key|secret|token)\s*[=:]\s*)\S+",
            re.IGNORECASE,
        ),
        r"\1***REDACTED***",
    ),
    # JunOS $9$ encrypted passwords (e.g., $9$abcDEFghiJKL...)
    SanitizationRule(
        "junos_encrypted_password",
        re.compile(r"\$9\$[A-Za-z0-9./]+"),
        "$9$<REDACTED>",
    ),
    # NX-OS type 5 (MD5) passwords (e.g., password 5 $1$abc$DEFghiJKL)
    SanitizationRule(
        "nxos_type5_password",
        re.compile(r"(\s+(?:password|secret)\s+)5\s+\$1\$\S+", re.IGNORECASE),
        r"\1***TYPE5_REDACTED***",
    ),
    # NX-OS type 7 passwords (e.g., password 7 08354F4B1A)
    SanitizationRule(
        "nxos_type7_password",
        re.compile(r"(\s+(?:password|secret)\s+)7\s+[0-9A-Fa-f]+", re.IGNORECASE),
        r"\1***TYPE7_REDACTED***",
    ),
    # IOS-XE secret 5 (MD5) passwords (e.g., secret 5 $1$abc$DEFghiJKL)
    SanitizationRule(
        "iosxe_secret5",
        re.compile(r"(\s+secret\s+)5\s+\$1\$\S+", re.IGNORECASE),
        r"\1***SECRET5_REDACTED***",
    ),
    # IOS-XE secret 9 (scrypt) passwords (e.g., secret 9 $9$abc...)
    SanitizationRule(
        "iosxe_secret9",
        re.compile(r"(\s+secret\s+)9\s+\$9\$\S+", re.IGNORECASE),
        r"\1***SECRET9_REDACTED***",
    ),
]


class ConfigSanitizer:
    """Sanitizes sensitive data from EOS configuration output."""

    def __init__(self, rules: list[SanitizationRule] | None = None):
        self._rules = rules or list(CONFIG_SANITIZATION_RULES)

    def sanitize(self, config_text: str) -> str:
        """Apply all sanitization rules to config text."""
        result = config_text
        for rule in self._rules:
            result = rule.pattern.sub(rule.replacement, result)
        return result


# Global instance
config_sanitizer = ConfigSanitizer()

# ---------------------------------------------------------------------------
# 1b. Error Message Sanitization
# ---------------------------------------------------------------------------

# URL with embedded credentials: https://user:password@host
_URL_CREDENTIAL_PATTERN = re.compile(r"(https?://)([^:@/\s]+):([^@/\s]+)@", re.IGNORECASE)

# Authorization/Bearer tokens in header-like text
_BEARER_TOKEN_PATTERN = re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE)

# Password-like fields in JSON-style text: "password": "secret" or 'password': 'secret'
_JSON_PASSWORD_PATTERN = re.compile(
    r"""(["'](?:password|secret|token|api_key|auth_key|private_key)["']\s*:\s*)["'][^"']*["']""",
    re.IGNORECASE,
)

# Connection string passwords (e.g., password=secret in DSN or query params)
_CONNSTR_PASSWORD_PATTERN = re.compile(
    r"((?:password|pwd|passwd|secret)=)[^\s&;,]+",
    re.IGNORECASE,
)


def sanitize_error_message(message: str) -> str:
    """Strip credentials from error messages, URLs, and exception text.

    Handles:
    - URLs with embedded credentials: https://user:password@host -> https://***:***@host
    - API tokens in headers: "Authorization: Bearer eyJ..." -> "Authorization: Bearer <REDACTED>"
    - Password fields in JSON-like text: "password": "secret" -> "password": "<REDACTED>"
    - Connection strings with passwords: password=secret -> password=<REDACTED>
    """
    if not message:
        return message

    result = _URL_CREDENTIAL_PATTERN.sub(r"\1***:***@", message)
    result = _BEARER_TOKEN_PATTERN.sub(r"\1<REDACTED>", result)
    result = _JSON_PASSWORD_PATTERN.sub(r'\1"<REDACTED>"', result)
    result = _CONNSTR_PASSWORD_PATTERN.sub(r"\1<REDACTED>", result)
    return result


# ---------------------------------------------------------------------------
# 2. Prompt Injection Defense
# ---------------------------------------------------------------------------

# Patterns commonly used in prompt injection attempts
INJECTION_PATTERNS = [
    re.compile(r"<\s*(IMPORTANT|SYSTEM|INSTRUCTION|IGNORE|OVERRIDE)[^>]*>", re.IGNORECASE),
    re.compile(
        r"(IGNORE|DISREGARD|FORGET)\s+(ALL\s+)?(PREVIOUS|PRIOR|ABOVE)\s+(INSTRUCTIONS?|CONTEXT)",
        re.IGNORECASE,
    ),
    re.compile(r"(YOU\s+ARE|ACT\s+AS|PRETEND\s+TO\s+BE|YOUR\s+NEW\s+ROLE)", re.IGNORECASE),
    re.compile(
        r"(EXECUTE|RUN|CALL)\s+(THE\s+)?(FOLLOWING|THIS)\s+(COMMAND|TOOL|FUNCTION)",
        re.IGNORECASE,
    ),
    re.compile(r"push_config_commands|run_config_command|delete_vlan|shutdown_interface", re.IGNORECASE),
]

TAG_PATTERN = re.compile(r"<[^>]+>")


def sanitize_device_output(output: str, context: str = "") -> str:
    """Sanitize device output before returning to LLM.

    Strips HTML tags and known prompt injection patterns from device-controlled
    text fields (interface descriptions, LLDP system descriptions, syslog messages).

    Args:
        output: Raw text from the device.
        context: Description of where this came from (for logging).

    Returns:
        Sanitized text safe for LLM consumption.
    """
    if not isinstance(output, str):
        return output

    sanitized = TAG_PATTERN.sub("", output)

    for pattern in INJECTION_PATTERNS:
        if pattern.search(sanitized):
            logger.warning(
                "Potential prompt injection detected in device output (context: %s): %.200s", context, sanitized
            )
            sanitized = pattern.sub("[FILTERED]", sanitized)

    return sanitized


def sanitize_dict_values(data: dict, context: str = "") -> dict:
    """Recursively sanitize all string values in a dict."""
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            sanitized[key] = sanitize_device_output(value, context=f"{context}.{key}")
        elif isinstance(value, dict):
            sanitized[key] = sanitize_dict_values(value, context=f"{context}.{key}")
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_dict_values(item, context=f"{context}.{key}[]")
                if isinstance(item, dict)
                else sanitize_device_output(item, context=f"{context}.{key}[]")
                if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


# ---------------------------------------------------------------------------
# 3. Output Size Limits
# ---------------------------------------------------------------------------

MAX_OUTPUT_SIZE = 100_000  # characters

TRUNCATION_MESSAGE = (
    "\n\n[OUTPUT TRUNCATED: Response exceeded maximum size. "
    "Use more specific queries (e.g., filter by interface, VLAN, or prefix) "
    "to get smaller, targeted results.]"
)


def enforce_output_limit(output: str, max_size: int = MAX_OUTPUT_SIZE) -> str:
    """Truncate output string if it exceeds the maximum size."""
    if len(output) <= max_size:
        return output
    return output[:max_size] + TRUNCATION_MESSAGE


def enforce_dict_output_limit(data: dict, max_size: int = MAX_OUTPUT_SIZE) -> dict:
    """Check serialized size of a dict and add truncation marker if too large.

    Preserves the 'device' key from the original data so callers retain context
    about which device produced the oversized output.
    """
    serialized = json.dumps(data, default=str)
    if len(serialized) <= max_size:
        return data
    result: dict = {
        "status": "error",
        "error": (
            f"Output too large ({len(serialized)} bytes, limit {max_size})."
            " Use more specific queries to reduce output size."
        ),
        "truncated": True,
        "original_size_bytes": len(serialized),
    }
    if "device" in data:
        result["device"] = data["device"]
    return result


# ---------------------------------------------------------------------------
# 4. Cross-Driver Input Validation (OWASP MCP05 — Command Injection)
# ---------------------------------------------------------------------------

# RESTCONF path traversal patterns
_RESTCONF_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./"),  # literal ../
    re.compile(r"\.\.$"),  # trailing ..
    re.compile(r"%2[eE]%2[eE]"),  # URL-encoded ..
    re.compile(r"%2[eE]\./"),  # mixed encoding
    re.compile(r"\.%2[fF]"),  # . + encoded /
    re.compile(r"%00"),  # null byte
    re.compile(r"\x00"),  # literal null byte
]

# NETCONF XML injection patterns
_NETCONF_XML_INJECTION_PATTERNS = [
    re.compile(r"]]>"),  # NETCONF framing delimiter
    re.compile(r"<!ENTITY", re.IGNORECASE),  # XML entity injection
    re.compile(r"<!DOCTYPE", re.IGNORECASE),  # DOCTYPE declaration (XXE)
    re.compile(r"<!ELEMENT", re.IGNORECASE),  # DTD element declaration
    re.compile(r"<!ATTLIST", re.IGNORECASE),  # DTD attribute list
    re.compile(r"<!\[CDATA\[.*?]]>"),  # CDATA sections with framing delimiter
    re.compile(r"xmlns:\w+="),  # namespace injection
    re.compile(r"&\w+;"),  # XML entity references (except common ones)
]

# Common safe XML entity references that should NOT be flagged
_SAFE_XML_ENTITIES = frozenset({"&amp;", "&lt;", "&gt;", "&quot;", "&apos;"})

# NX-API / JSON injection patterns (string field attacks)
_NXAPI_JSON_INJECTION_PATTERNS = [
    re.compile(r'["\x00-\x1f]'),  # control characters in JSON strings
    re.compile(r"\\u0000"),  # unicode null escape
]


def validate_restconf_path(path: str) -> str | None:
    """Validate a RESTCONF URL path for path traversal attacks.

    OWASP MCP05 — Command Injection (RESTCONF variant).

    Args:
        path: The RESTCONF URL path to validate.

    Returns:
        Error message if path traversal is detected, None if safe.
    """
    if not path:
        return "RESTCONF path cannot be empty"

    for pattern in _RESTCONF_TRAVERSAL_PATTERNS:
        if pattern.search(path):
            logger.warning("RESTCONF path traversal attempt detected: %.200s", path)
            return f"RESTCONF path contains forbidden pattern (path traversal): {path!r}"

    return None


def validate_netconf_payload(xml_payload: str) -> str | None:
    """Validate a NETCONF XML payload for XML injection attacks.

    OWASP MCP05 — Command Injection (NETCONF variant).
    Blocks XXE, entity injection, and NETCONF framing manipulation.

    Args:
        xml_payload: The NETCONF XML string to validate.

    Returns:
        Error message if injection is detected, None if safe.
    """
    if not xml_payload:
        return None

    for pattern in _NETCONF_XML_INJECTION_PATTERNS:
        match = pattern.search(xml_payload)
        if match:
            matched_text = match.group(0)
            # Allow common safe XML entity references
            if matched_text in _SAFE_XML_ENTITIES:
                continue
            logger.warning("NETCONF XML injection attempt detected: %.200s", xml_payload)
            return f"NETCONF payload contains forbidden XML pattern: {matched_text!r}"

    return None


def validate_nxapi_payload(payload: str) -> str | None:
    """Validate NX-API JSON payload fields for injection attacks.

    OWASP MCP05 — Command Injection (NX-API variant).
    Blocks control characters and null bytes in string fields.

    Args:
        payload: The NX-API string field value to validate.

    Returns:
        Error message if injection is detected, None if safe.
    """
    if not payload:
        return None

    # Check for null bytes (both literal and escaped)
    if "\x00" in payload or "\\u0000" in payload:
        logger.warning("NX-API null byte injection attempt detected: %.200s", payload)
        return "NX-API payload contains null byte"

    # Check for control characters (except common whitespace: \t, \n, \r)
    for char in payload:
        if ord(char) < 0x20 and char not in ("\t", "\n", "\r"):
            logger.warning("NX-API control character injection detected: %.200s", payload)
            return f"NX-API payload contains control character: U+{ord(char):04X}"

    return None
