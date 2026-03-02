"""Deep health checks, liveness, and readiness endpoints.

Provides three levels of health checking for Kubernetes probes:
- Liveness: lightweight, never blocks, confirms process is alive
- Readiness: checks modules loaded and connection pool initialized
- Deep health: validates device reachability, Redis, and auth provider
"""

from __future__ import annotations

import logging
import os
import random
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from network_mcp.config import NetworkSettings
    from network_mcp.connection import ConnectionManager

logger = logging.getLogger("network-mcp")

# Module-level server start time — set once during import or via init_health()
_server_start_time: float = time.monotonic()

# Module-level flag: set to True after all tool modules have been loaded
_modules_loaded: bool = False

# Module-level flag: set to True after at least one device has been verified
_device_verified: bool = False


def init_health(start_time: float | None = None) -> None:
    """Initialize health module state.

    Args:
        start_time: Optional override for server start time (monotonic).
                    Defaults to current time.
    """
    global _server_start_time
    _server_start_time = start_time if start_time is not None else time.monotonic()


def mark_modules_loaded() -> None:
    """Mark that core modules have finished loading."""
    global _modules_loaded
    _modules_loaded = True


def mark_device_verified() -> None:
    """Mark that at least one device has been successfully contacted."""
    global _device_verified
    _device_verified = True


def get_liveness() -> dict:
    """Lightweight liveness check.

    Returns immediately with process info. No external calls, no locks,
    no database queries. This endpoint must never block or fail unless
    the process is truly unresponsive.

    Returns:
        Dict with status, pid, and uptime_seconds.
    """
    uptime = round(time.monotonic() - _server_start_time, 1)
    return {
        "status": "alive",
        "pid": os.getpid(),
        "uptime_seconds": uptime,
    }


def get_readiness(conn_mgr: ConnectionManager, settings: NetworkSettings) -> tuple[dict, int]:
    """Check if the server is ready to accept requests.

    Verifies:
    - Core tool modules have been loaded
    - Connection pool is initialized
    - Optionally, at least one device has been verified (if NET_VERIFY_CONNECTIONS=true)

    Args:
        conn_mgr: ConnectionManager instance.
        settings: NetworkSettings instance.

    Returns:
        Tuple of (response dict, HTTP status code).
    """
    pool_initialized = conn_mgr is not None
    modules_ok = _modules_loaded

    # If verify_connections is enabled, also check that at least one device has been verified
    verify_check_ok = True
    if settings.net_verify_connections:
        verify_check_ok = _device_verified

    is_ready = modules_ok and pool_initialized and verify_check_ok
    status_code = 200 if is_ready else 503

    return {
        "status": "ready" if is_ready else "not_ready",
        "modules_loaded": modules_ok,
        "pool_initialized": pool_initialized,
        "device_verified": _device_verified if settings.net_verify_connections else None,
    }, status_code


def _check_device(conn_mgr: ConnectionManager) -> dict:
    """Check device reachability by running 'show hostname' on a random device.

    Args:
        conn_mgr: ConnectionManager instance.

    Returns:
        Check result dict with status and details.
    """
    devices = conn_mgr.list_devices()
    if not devices:
        return {
            "status": "skip",
            "message": "No devices in inventory",
        }

    target = random.choice(devices)  # noqa: S311
    try:
        driver = conn_mgr.get_node(target)
        driver.run_show(["show hostname"])
        return {
            "status": "pass",
            "device": target,
        }
    except Exception as e:
        return {
            "status": "fail",
            "device": target,
            "error": str(e),
        }


def _check_redis(settings: NetworkSettings) -> dict:
    """Check Redis connectivity via PING.

    Args:
        settings: NetworkSettings instance.

    Returns:
        Check result dict with status and details.
    """
    if settings.net_distributed_backend != "redis":
        return {"status": "skip", "message": "Redis not enabled"}

    try:
        import redis

        client = redis.from_url(settings.net_redis_url, decode_responses=True, socket_timeout=5)
        pong = client.ping()
        client.close()
        if pong:
            return {"status": "pass"}
        return {"status": "fail", "error": "PING returned False"}  # pragma: no cover
    except ImportError:
        return {"status": "fail", "error": "redis package not installed"}
    except Exception as e:
        return {"status": "fail", "error": str(e)}


def _check_auth(settings: NetworkSettings) -> dict:
    """Check auth provider reachability via OIDC discovery endpoint.

    Args:
        settings: NetworkSettings instance.

    Returns:
        Check result dict with status and details.
    """
    if not settings.auth_enabled:
        return {"status": "skip", "message": "Auth not enabled"}

    if not settings.auth_issuer_url:
        return {"status": "fail", "error": "AUTH_ISSUER_URL not configured"}

    try:
        import httpx

        url = f"{settings.auth_issuer_url.rstrip('/')}/.well-known/openid-configuration"
        resp = httpx.head(url, timeout=3.0)
        if resp.status_code < 500:
            return {"status": "pass", "issuer_url": settings.auth_issuer_url}
        return {"status": "fail", "error": f"HTTP {resp.status_code}"}
    except ImportError:
        # Fallback to urllib if httpx not available
        import urllib.error
        import urllib.request

        url = f"{settings.auth_issuer_url.rstrip('/')}/.well-known/openid-configuration"
        try:
            req = urllib.request.Request(url, method="HEAD")  # noqa: S310
            urllib.request.urlopen(req, timeout=3)  # noqa: S310
            return {"status": "pass", "issuer_url": settings.auth_issuer_url}
        except Exception as e:
            return {"status": "fail", "error": str(e)}
    except Exception as e:
        return {"status": "fail", "error": str(e)}


def get_deep_health(conn_mgr: ConnectionManager, settings: NetworkSettings) -> tuple[dict, int]:
    """Run deep health checks against external dependencies.

    Validates:
    - Device reachability (random device from inventory)
    - Redis connectivity (if NET_DISTRIBUTED_BACKEND=redis)
    - Auth provider reachability (if AUTH_ENABLED=true)

    Args:
        conn_mgr: ConnectionManager instance.
        settings: NetworkSettings instance.

    Returns:
        Tuple of (response dict, HTTP status code).
    """
    checks = {
        "device": _check_device(conn_mgr),
        "redis": _check_redis(settings),
        "auth": _check_auth(settings),
    }

    # Determine overall status based on active checks (not skipped)
    active_checks = {k: v for k, v in checks.items() if v["status"] != "skip"}
    if not active_checks:
        # All checks skipped — report healthy (nothing to validate)
        overall = "healthy"
    else:
        passed = sum(1 for v in active_checks.values() if v["status"] == "pass")
        total = len(active_checks)
        if passed == total:
            overall = "healthy"
        elif passed == 0:
            overall = "unhealthy"
        else:
            overall = "degraded"

    status_code = 503 if overall == "unhealthy" else 200

    return {
        "status": overall,
        "checks": checks,
        "timestamp": datetime.now(UTC).isoformat(),
    }, status_code
