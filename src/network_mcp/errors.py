"""Structured error codes for the Network MCP platform.

Every tool error response includes an error_code field from this taxonomy.
Callers can use is_retryable and category to decide how to handle failures.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    """High-level error classification."""

    TRANSIENT = "transient"  # Temporary, may resolve on retry
    PERMANENT = "permanent"  # Will not resolve without intervention
    AUTH = "auth"  # Authentication/authorization failure
    VALIDATION = "validation"  # Input validation failure


class ErrorCode(StrEnum):
    """Standardized error codes returned in tool error responses."""

    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    DEVICE_UNREACHABLE = "DEVICE_UNREACHABLE"
    COMMAND_ERROR = "COMMAND_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RBAC_DENIED = "RBAC_DENIED"
    CONFIG_REJECTED = "CONFIG_REJECTED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    READ_ONLY_MODE = "READ_ONLY_MODE"
    OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
    SERVER_SHUTTING_DOWN = "SERVER_SHUTTING_DOWN"
    BULKHEAD_TIMEOUT = "BULKHEAD_TIMEOUT"
    ACCESS_DENIED = "ACCESS_DENIED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# --- Driver-specific error codes ---

CONNECTION_TIMEOUT = "DRIVER_CONNECTION_TIMEOUT"
AUTH_FAILED = "DRIVER_AUTH_FAILED"
COMMAND_FAILED = "DRIVER_COMMAND_FAILED"
PARSE_ERROR = "DRIVER_PARSE_ERROR"
RESTCONF_FALLBACK = "DRIVER_RESTCONF_FALLBACK"
NXAPI_INVALID_RESPONSE = "DRIVER_NXAPI_INVALID_RESPONSE"
NETCONF_XML_ERROR = "DRIVER_NETCONF_XML_ERROR"


class DriverError(Exception):
    """Base driver error with structured error code.

    Carries a machine-readable code, the target host, and optional
    details dict so that callers can programmatically handle failures.
    """

    def __init__(self, message: str, code: str, host: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.host = host
        self.details = details or {}


ERROR_METADATA: dict[ErrorCode, dict] = {
    ErrorCode.CONNECTION_ERROR: {
        "category": ErrorCategory.TRANSIENT,
        "is_retryable": True,
        "http_status": 502,
        "retry_after_seconds": 5,
        "description": "Failed to connect to network device API",
    },
    ErrorCode.TIMEOUT_ERROR: {
        "category": ErrorCategory.TRANSIENT,
        "is_retryable": True,
        "http_status": 504,
        "retry_after_seconds": 10,
        "description": "Device did not respond within timeout period",
    },
    ErrorCode.AUTH_ERROR: {
        "category": ErrorCategory.AUTH,
        "is_retryable": False,
        "http_status": 401,
        "retry_after_seconds": None,
        "description": "Authentication failed — invalid or expired credentials",
    },
    ErrorCode.RATE_LIMITED: {
        "category": ErrorCategory.TRANSIENT,
        "is_retryable": True,
        "http_status": 429,
        "retry_after_seconds": 2,
        "description": "Rate limit exceeded for device",
    },
    ErrorCode.DEVICE_UNREACHABLE: {
        "category": ErrorCategory.TRANSIENT,
        "is_retryable": True,
        "http_status": 503,
        "retry_after_seconds": 30,
        "description": "Device is unreachable — network or management plane issue",
    },
    ErrorCode.COMMAND_ERROR: {
        "category": ErrorCategory.PERMANENT,
        "is_retryable": False,
        "http_status": 400,
        "retry_after_seconds": None,
        "description": "Command rejected by device (syntax error or unsupported)",
    },
    ErrorCode.VALIDATION_ERROR: {
        "category": ErrorCategory.VALIDATION,
        "is_retryable": False,
        "http_status": 422,
        "retry_after_seconds": None,
        "description": "Input parameter validation failed",
    },
    ErrorCode.RBAC_DENIED: {
        "category": ErrorCategory.AUTH,
        "is_retryable": False,
        "http_status": 403,
        "retry_after_seconds": None,
        "description": "Insufficient scope/permissions for this operation",
    },
    ErrorCode.CONFIG_REJECTED: {
        "category": ErrorCategory.PERMANENT,
        "is_retryable": False,
        "http_status": 400,
        "retry_after_seconds": None,
        "description": "Configuration command blocked by deny-list or safety policy",
    },
    ErrorCode.CIRCUIT_OPEN: {
        "category": ErrorCategory.TRANSIENT,
        "is_retryable": True,
        "http_status": 503,
        "retry_after_seconds": 60,
        "description": "Circuit breaker open — device has had consecutive failures",
    },
    ErrorCode.READ_ONLY_MODE: {
        "category": ErrorCategory.PERMANENT,
        "is_retryable": False,
        "http_status": 403,
        "retry_after_seconds": None,
        "description": "Write operation blocked — server is in read-only mode",
    },
    ErrorCode.OUTPUT_TOO_LARGE: {
        "category": ErrorCategory.PERMANENT,
        "is_retryable": False,
        "http_status": 413,
        "retry_after_seconds": None,
        "description": "Command output exceeds size limit",
    },
    ErrorCode.SERVER_SHUTTING_DOWN: {
        "category": ErrorCategory.TRANSIENT,
        "is_retryable": False,
        "http_status": 503,
        "retry_after_seconds": None,
        "description": "Server is shutting down — no new requests accepted",
    },
    ErrorCode.BULKHEAD_TIMEOUT: {
        "category": ErrorCategory.TRANSIENT,
        "is_retryable": True,
        "http_status": 503,
        "retry_after_seconds": 5,
        "description": "Bulkhead concurrency limit reached for vendor — too many concurrent operations",
    },
    ErrorCode.ACCESS_DENIED: {
        "category": ErrorCategory.AUTH,
        "is_retryable": False,
        "http_status": 403,
        "retry_after_seconds": None,
        "description": "Access denied — device not in tenant scope",
    },
    ErrorCode.INTERNAL_ERROR: {
        "category": ErrorCategory.TRANSIENT,
        "is_retryable": True,
        "http_status": 500,
        "retry_after_seconds": 5,
        "description": "Unexpected internal server error",
    },
}


def make_error_response(
    error_code: ErrorCode,
    host: str,
    error: str,
    **extra,
) -> dict:
    """Build a standardized error response dict with error code metadata."""
    meta = ERROR_METADATA[error_code]
    result = {
        "status": "error",
        "device": host,
        "error": error,
        "error_code": error_code.value,
        "error_category": meta["category"].value,
        "is_retryable": meta["is_retryable"],
    }
    if meta["retry_after_seconds"] is not None:
        result["retry_after_seconds"] = meta["retry_after_seconds"]
    result.update(extra)
    return result


def classify_exception(exc: Exception) -> ErrorCode:
    """Map a Python exception to the appropriate ErrorCode."""
    try:
        import pyeapi.eapilib

        if isinstance(exc, pyeapi.eapilib.ConnectionError):
            return ErrorCode.CONNECTION_ERROR
        if isinstance(exc, pyeapi.eapilib.CommandError):
            return ErrorCode.COMMAND_ERROR
    except ImportError:
        pass

    if isinstance(exc, TimeoutError):
        return ErrorCode.TIMEOUT_ERROR
    if isinstance(exc, ConnectionRefusedError):
        return ErrorCode.DEVICE_UNREACHABLE
    if isinstance(exc, PermissionError):
        return ErrorCode.AUTH_ERROR

    return ErrorCode.INTERNAL_ERROR
