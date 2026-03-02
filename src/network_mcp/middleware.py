"""Auth scope utilities and request context for tool-level access control.

Scope checking functions map tool annotations (READ_ONLY, WRITE_SAFE, DESTRUCTIVE)
to required OAuth scopes and validate tokens against them.

Thread-local request context tracks the authenticated user and request ID for
audit logging. The actual token validation is handled by FastMCP's built-in
BearerAuthBackend via TokenVerifier (see auth.py). These utilities are for
scope enforcement within tool code (Phase 6, Session 32) and for audit attribution.
"""

import logging
import threading
import time
import uuid

from .auth import AccessToken

# --- Thread-local storage for authenticated user context ---

_request_context = threading.local()


def get_current_user() -> str:
    """Return the authenticated user (JWT sub claim) for the current request.

    Returns 'anonymous' when auth is disabled or no token is present.
    """
    return getattr(_request_context, "user", "anonymous")


def get_current_scopes() -> list[str]:
    """Return the scopes from the current authenticated request."""
    return getattr(_request_context, "scopes", [])


def get_current_tenant_id() -> str | None:
    """Return the tenant ID from the current authenticated request.

    Returns None when auth is disabled or no tenant information is in the JWT.
    """
    return getattr(_request_context, "tenant_id", None)


def get_request_id() -> str | None:
    """Return the request ID for the current thread, or None if not set."""
    return getattr(_request_context, "request_id", None)


def get_correlation_id() -> str:
    """Get or create a correlation ID for the current request.

    Returns the existing request ID if set, otherwise generates a new UUID v4.
    The correlation ID is stored in the request context for propagation through
    audit logging, helpers, and telemetry.
    """
    existing = getattr(_request_context, "request_id", None)
    if existing:
        return existing
    # Generate and store a new correlation ID
    correlation_id = str(uuid.uuid4())
    _request_context.request_id = correlation_id
    return correlation_id


def set_request_context(
    user: str | None = None,
    scopes: list[str] | None = None,
    tenant_id: str | None = None,
    tenant_devices: list[str] | None = None,
) -> str:
    """Set the authenticated user context for the current thread and return generated request ID.

    Args:
        user: Authenticated username (JWT sub claim).
        scopes: OAuth scopes from the token.
        tenant_id: Tenant identifier from the token.
        tenant_devices: Optional list of allowed device hostname patterns for this tenant.
            Supports fnmatch-style wildcards (e.g., ``["spine-*", "leaf-01"]``).
    """
    request_id = str(uuid.uuid4())
    _request_context.request_id = request_id
    _request_context.user = user or "anonymous"
    _request_context.scopes = scopes or []
    _request_context.tenant_id = tenant_id
    _request_context.tenant_devices = tenant_devices
    _request_context.start_time = time.monotonic()
    return request_id


def get_request_duration() -> float | None:
    """Return elapsed time since request start in seconds, or None if no context is set."""
    start = getattr(_request_context, "start_time", None)
    if start is None:
        return None
    return time.monotonic() - start


def clear_request_context() -> None:
    """Clear the authenticated user context for the current thread."""
    _request_context.request_id = None
    _request_context.user = "anonymous"
    _request_context.scopes = []
    _request_context.tenant_id = None
    _request_context.tenant_devices = None
    _request_context.start_time = None


def get_trace_context() -> dict:
    """Return a dict with correlation ID and OTel trace/span IDs (if available).

    Returns:
        dict with keys:
            - ``correlation_id`` (str): Request correlation ID (always present).
            - ``trace_id`` (str | None): OTel trace ID hex string, or None.
            - ``span_id`` (str | None): OTel span ID hex string, or None.
    """
    ctx: dict = {"correlation_id": get_correlation_id(), "trace_id": None, "span_id": None}
    try:
        from opentelemetry import trace as otel_trace

        current_span = otel_trace.get_current_span()
        span_ctx = current_span.get_span_context()
        if span_ctx and span_ctx.trace_id:
            ctx["trace_id"] = format(span_ctx.trace_id, "032x")
            ctx["span_id"] = format(span_ctx.span_id, "016x")
    except ImportError:
        pass
    return ctx


# --- Logging filter for request ID injection ---


class RequestIdFilter(logging.Filter):
    """Logging filter that injects the current request ID into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"  # type: ignore[attr-defined]
        return True


# --- Scope mapping ---

# Maps tool annotation dicts to required scopes
SCOPE_READ = "network:read"
SCOPE_WRITE = "network:write"
SCOPE_ADMIN = "network:admin"


def required_scope_for_annotations(annotations: dict | None) -> str:
    """Determine the required scope based on tool annotations.

    Args:
        annotations: Tool annotation dict (e.g., READ_ONLY, WRITE_SAFE, DESTRUCTIVE).

    Returns:
        Required scope string.
    """
    if annotations is None:
        return SCOPE_READ

    is_read_only = getattr(annotations, "readOnlyHint", False)
    if is_read_only:
        return SCOPE_READ
    return SCOPE_WRITE


def check_scope(token: AccessToken, required: str) -> str | None:
    """Check if a token has the required scope.

    Scope hierarchy: network:admin > network:write > network:read
    - network:admin grants access to everything
    - network:write grants access to read + write tools
    - network:read grants access to read-only tools

    Args:
        token: The validated access token.
        required: The required scope string.

    Returns:
        None if scope is sufficient, error message string if insufficient.
    """
    token_scopes = token.scopes

    # Admin scope grants everything
    if SCOPE_ADMIN in token_scopes:
        return None

    # Write scope also grants read access
    if required == SCOPE_READ and (SCOPE_READ in token_scopes or SCOPE_WRITE in token_scopes):
        return None

    if required == SCOPE_WRITE and SCOPE_WRITE in token_scopes:
        return None

    scope_str = " ".join(token_scopes) if token_scopes else "(none)"
    return f"Insufficient scope. This tool requires '{required}'. Your token has: '{scope_str}'."
