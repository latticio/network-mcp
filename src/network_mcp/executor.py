"""Multi-device executor for fabric-wide operations."""

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from network_mcp.connection import ConnectionManager

logger = logging.getLogger("network-mcp")

DEFAULT_MAX_CONCURRENT_DEVICES = 20
DEFAULT_DEVICE_TIMEOUT = 30.0


class MultiDeviceResult:
    """Thread-safe container for multi-device operation results."""

    def __init__(self) -> None:
        self.successes: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def add_success(self, host: str, data: Any) -> None:
        """Record a successful result for a device."""
        with self._lock:
            self.successes[host] = data

    def add_error(self, host: str, error: str) -> None:
        """Record an error for a device."""
        with self._lock:
            self.errors[host] = error

    @property
    def total(self) -> int:
        with self._lock:
            return len(self.successes) + len(self.errors)

    @property
    def success_count(self) -> int:
        with self._lock:
            return len(self.successes)

    @property
    def error_count(self) -> int:
        with self._lock:
            return len(self.errors)

    def to_dict(self) -> dict:
        """Return a structured summary dict with partial status support.

        Status is "success" when all devices succeed, "error" when all fail,
        and "partial" when some succeed and some fail.

        .. deprecated:: Use :meth:`to_response` for standardized partial failure format.
        """
        return self.to_response()

    def to_response(self, action: str = "") -> dict:
        """Convert to a standardized response dict with partial failure support.

        Returns:
            {
                "status": "success" | "error" | "partial",
                "action": action,
                "summary": {
                    "total": int,
                    "succeeded": int,
                    "failed": int,
                },
                "succeeded_hosts": [...],
                "failed_hosts": [...],
                "results": {host: data, ...},
                "errors": {host: error_message, ...},
            }
        """
        with self._lock:
            total = len(self.successes) + len(self.errors)
            if not self.errors:
                status = "success"
            elif not self.successes:
                status = "error"
            else:
                status = "partial"

            return {
                "status": status,
                "action": action,
                "summary": {
                    "total": total,
                    "succeeded": len(self.successes),
                    "failed": len(self.errors),
                },
                "succeeded_hosts": sorted(self.successes.keys()),
                "failed_hosts": sorted(self.errors.keys()),
                "results": dict(self.successes),
                "errors": dict(self.errors),
            }


def resolve_hosts(conn_mgr: ConnectionManager, targets: str = "all") -> list[str]:
    """Resolve a target specifier into a list of device hostnames.

    Supports:
        - "all" — all inventory devices
        - "group:<name>" — devices in a specific group
        - "tag:<value>" — devices with a specific tag
        - "role:<value>" — devices with a specific role
        - Comma-separated list of hostnames — explicit device list
    """
    targets = targets.strip()

    if targets.lower() == "all":
        return conn_mgr.list_devices()

    if targets.startswith("group:"):
        group_name = targets[len("group:") :]
        return conn_mgr.get_devices_by_group(group_name)

    if targets.startswith("tag:"):
        tag_value = targets[len("tag:") :]
        return conn_mgr.get_devices_by_tag(tag_value)

    if targets.startswith("role:"):
        role_value = targets[len("role:") :]
        return conn_mgr.get_devices_by_role(role_value)

    # Comma-separated list of hostnames
    return [h.strip() for h in targets.split(",") if h.strip()]


async def execute_on_devices(
    conn_mgr: ConnectionManager,
    hosts: list[str],
    operation: Callable[..., dict],
    *args: Any,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_DEVICES,
    timeout: float = DEFAULT_DEVICE_TIMEOUT,
    ctx: Any = None,
    **kwargs: Any,
) -> MultiDeviceResult:
    """Execute an operation across multiple devices concurrently.

    Args:
        conn_mgr: Connection manager instance.
        hosts: List of device hostnames to target.
        operation: Callable with signature ``operation(conn_mgr, host, *args, **kwargs) -> dict``.
        max_concurrent: Maximum concurrent device operations (semaphore bound).
        timeout: Per-device timeout in seconds.
        ctx: Optional MCP Context for progress reporting.
    """
    result = MultiDeviceResult()

    if not hosts:
        return result

    semaphore = asyncio.Semaphore(max_concurrent)
    # Safe without locking: asyncio is single-threaded and completed += 1
    # executes between await points (no concurrent read/write possible).
    completed = 0
    total = len(hosts)

    async def _run_one(host: str) -> None:
        nonlocal completed
        async with semaphore:
            try:
                device_result = await asyncio.wait_for(
                    asyncio.to_thread(operation, conn_mgr, host, *args, **kwargs),
                    timeout=timeout,
                )
                if isinstance(device_result, dict) and device_result.get("status") == "error":
                    result.add_error(host, device_result.get("error", "Unknown error"))
                else:
                    result.add_success(host, device_result)
            except TimeoutError:
                result.add_error(host, f"Operation timed out after {timeout}s")
            except Exception as e:
                result.add_error(host, str(e))
            finally:
                completed += 1
                if ctx:
                    try:
                        await ctx.report_progress(progress=completed, total=total)
                    except Exception:  # noqa: S110
                        pass  # Progress reporting is best-effort

    await asyncio.gather(*[_run_one(host) for host in hosts])
    return result
