"""API versioning and deprecation policy.

Deprecation Policy:
- Deprecated tools remain functional for 2 major versions (12 months minimum)
- Deprecated tools emit a warning in response: {"_deprecated": true, "_sunset": "2027-03-01"}
- New tools are marked as "beta" for 1 minor version before becoming stable

Usage:
    from network_mcp.versioning import deprecated, beta

    @mcp.tool(annotations=READ_ONLY)
    @deprecated(sunset_date="2027-03-01", replacement="net_get_device_info")
    def eos_get_device_info(host: str) -> dict:
        ...

    @mcp.tool(annotations=READ_ONLY)
    @beta(since="6.0.0")
    def net_get_device_topology(host: str) -> dict:
        ...
"""

import functools
import logging
from datetime import date, datetime

logger = logging.getLogger("network-mcp")


def deprecated(sunset_date: str, replacement: str = ""):
    """Mark a tool function as deprecated.

    Adds ``_deprecated``, ``_sunset``, and optionally ``_replacement`` keys
    to the tool's return dict.  Logs a warning on each invocation.

    Args:
        sunset_date: ISO-format date (YYYY-MM-DD) when the tool will be removed.
        replacement: Name of the replacement tool, if any.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if isinstance(result, dict):
                result["_deprecated"] = True
                result["_sunset"] = sunset_date
                if replacement:
                    result["_replacement"] = replacement
                logger.warning("Deprecated tool %s called. Sunset: %s", func.__name__, sunset_date)
            return result

        # Store metadata for introspection
        wrapper._deprecated = True  # type: ignore[attr-defined]
        wrapper._sunset_date = sunset_date  # type: ignore[attr-defined]
        wrapper._replacement = replacement  # type: ignore[attr-defined]
        return wrapper

    return decorator


def beta(since: str = ""):
    """Mark a tool function as beta / experimental.

    Adds ``_beta: true`` and ``_since`` to the tool's return dict.

    Args:
        since: Version string when the tool was introduced (e.g., "6.0.0").
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if isinstance(result, dict):
                result["_beta"] = True
                if since:
                    result["_since"] = since
            return result

        wrapper._beta = True  # type: ignore[attr-defined]
        wrapper._since = since  # type: ignore[attr-defined]
        return wrapper

    return decorator


def is_deprecated(func) -> bool:
    """Check if a function has been marked as deprecated."""
    return getattr(func, "_deprecated", False)


def is_beta(func) -> bool:
    """Check if a function has been marked as beta."""
    return getattr(func, "_beta", False)


def get_sunset_date(func) -> str:
    """Get the sunset date for a deprecated function. Returns empty string if not deprecated."""
    return getattr(func, "_sunset_date", "")


def get_replacement(func) -> str:
    """Get the replacement tool name for a deprecated function. Returns empty string if none."""
    return getattr(func, "_replacement", "")


def is_past_sunset(func) -> bool:
    """Check if a deprecated function is past its sunset date."""
    sunset = get_sunset_date(func)
    if not sunset:
        return False
    try:
        sunset_dt = datetime.strptime(sunset, "%Y-%m-%d").date()
        return date.today() > sunset_dt
    except ValueError:
        return False
