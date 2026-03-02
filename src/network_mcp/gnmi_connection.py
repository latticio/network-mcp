"""gNMI device connection management via pygnmi.

Provides both per-request connections (GnmiManager) and a thread-safe
connection pool (GnmiConnectionPool) with TTL-based expiry and FIFO eviction.
Maps gRPC errors to user-friendly messages.
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from network_mcp.config import NetworkSettings

logger = logging.getLogger("network-mcp.gnmi")

# --- pygnmi availability check ---

GNMI_AVAILABLE = False
try:
    import grpc
    from pygnmi.client import gNMIclient

    GNMI_AVAILABLE = True
except ImportError:
    grpc = None  # type: ignore[assignment]
    gNMIclient = None  # type: ignore[assignment, misc]

GNMI_NOT_INSTALLED_ERROR = {
    "status": "error",
    "error": "pygnmi is not installed. Install with: pip install latticio[gnmi]",
}

GNMI_NOT_ENABLED_ERROR = {
    "status": "error",
    "error": (
        "gNMI is not reachable on this device. Ensure gNMI is enabled:\n"
        "  management api gnmi\n"
        "    transport grpc default\n"
        "    no shutdown"
    ),
}

# --- gRPC error mapping ---

_GRPC_ERROR_MAP = {
    "UNAVAILABLE": "Device unreachable or gNMI not enabled. Check connectivity and gNMI config.",
    "UNAUTHENTICATED": "Authentication failed. Check gNMI credentials (NET_USERNAME/NET_PASSWORD).",
    "NOT_FOUND": "gNMI path not found on device. Verify the OpenConfig path is correct.",
    "DEADLINE_EXCEEDED": "Request timed out. Device may be overloaded or path returns too much data.",
    "PERMISSION_DENIED": "Insufficient privileges for this gNMI operation.",
    "INVALID_ARGUMENT": "Invalid gNMI request. Check path syntax.",
    "INTERNAL": "Internal error on the gNMI server (device-side issue).",
}


def _map_grpc_error(error: Exception) -> str:
    """Map a gRPC error to a user-friendly message."""
    if not GNMI_AVAILABLE:
        return str(error)

    if isinstance(error, grpc.RpcError):
        code = error.code()
        code_name = code.name if hasattr(code, "name") else str(code)
        details = error.details() if hasattr(error, "details") else ""
        friendly = _GRPC_ERROR_MAP.get(code_name, f"gRPC error ({code_name})")
        if details:
            return f"{friendly} Details: {details}"
        return friendly

    if "FutureTimeoutError" in type(error).__name__:
        return "gNMI connection timeout. Is gNMI enabled on the device?"

    return f"Unexpected gNMI error: {error}"


class GnmiManager:
    """Manages pygnmi gNMI connections to network devices.

    Creates a fresh gNMIclient per request — gRPC channels are not
    thread-safe, so connections are NOT pooled.
    """

    def __init__(self, settings: "NetworkSettings") -> None:
        self._default_username = settings.net_username
        self._default_password = settings.net_password.get_secret_value()
        self._default_port = settings.gnmi_port
        self._insecure = settings.gnmi_insecure
        self._timeout = settings.gnmi_timeout
        self._tls_cert = settings.gnmi_tls_cert
        self._tls_key = settings.gnmi_tls_key
        self._tls_ca = settings.gnmi_tls_ca

    def create_client(self, host: str, port: int | None = None) -> "gNMIclient":
        """Create a new gNMIclient for a device.

        The caller MUST use this as a context manager::

            with gnmi_mgr.create_client(host) as gc:
                result = gc.get(path=[...])

        Args:
            host: Hostname or IP address of the device.
            port: gNMI port override (default from GNMI_PORT env or 6030).

        Returns:
            A gNMIclient context-manager instance.
        """
        if not GNMI_AVAILABLE:
            raise RuntimeError("pygnmi is not installed")

        actual_port = port or self._default_port

        kwargs: dict[str, Any] = {
            "target": (host, str(actual_port)),
            "username": self._default_username,
            "password": self._default_password,
            "insecure": self._insecure,
            "gnmi_timeout": self._timeout,
        }

        # TLS configuration
        if self._tls_cert and self._tls_key:
            kwargs["insecure"] = False
            kwargs["override"] = host  # Override SSL target name
            kwargs["path_cert"] = self._tls_cert
            kwargs["path_key"] = self._tls_key
            if self._tls_ca:
                kwargs["path_root"] = self._tls_ca

        return gNMIclient(**kwargs)

    def execute_get(self, host: str, paths: list[str], port: int | None = None) -> dict:
        """Execute a gNMI Get and return a normalised result dict.

        Args:
            host: Device hostname/IP.
            paths: List of OpenConfig/YANG paths.
            port: Optional port override.

        Returns:
            ``{"status": "success", "device": host, "data": ...}`` or error dict.
        """
        try:
            with self.create_client(host, port) as gc:
                result = gc.get(path=paths)
            return {
                "status": "success",
                "device": host,
                "data": _flatten_notifications(result),
            }
        except Exception as e:
            return {"status": "error", "device": host, "error": _map_grpc_error(e)}

    def execute_subscribe_once(self, host: str, paths: list[str], port: int | None = None) -> dict:
        """Execute a gNMI Subscribe ONCE and collect until sync_response.

        Args:
            host: Device hostname/IP.
            paths: List of OpenConfig/YANG paths.
            port: Optional port override.

        Returns:
            Normalised result dict with collected updates.
        """
        subscribe_request = {
            "subscription": [{"path": p, "mode": "target_defined"} for p in paths],
            "mode": "once",
            "encoding": "json",
        }

        collected: list[dict] = []
        try:
            with self.create_client(host, port) as gc:
                for response in gc.subscribe2(subscribe=subscribe_request):
                    if response.get("sync_response"):
                        break
                    if "update" in response:
                        collected.append(response["update"])
            return {
                "status": "success",
                "device": host,
                "data": collected,
            }
        except Exception as e:
            return {"status": "error", "device": host, "error": _map_grpc_error(e)}

    def execute_subscribe_stream(
        self, host: str, paths: list[str], duration: int = 10, port: int | None = None
    ) -> dict:
        """Execute a gNMI Subscribe in STREAM mode, collecting updates for a fixed duration.

        Since MCP is request-response, we collect updates for ``duration`` seconds
        and return all received data.

        Args:
            host: Device hostname/IP.
            paths: List of OpenConfig/YANG paths.
            duration: Seconds to collect updates (capped at gnmi_timeout).
            port: Optional port override.

        Returns:
            Normalised result dict with collected stream updates.
        """
        actual_duration = min(duration, self._timeout)
        subscribe_request = {
            "subscription": [{"path": p, "mode": "sample", "sample_interval": 1000000000} for p in paths],
            "mode": "stream",
            "encoding": "json",
        }

        collected: list[dict] = []
        try:
            with self.create_client(host, port) as gc:
                start = time.monotonic()
                for response in gc.subscribe2(subscribe=subscribe_request):
                    if time.monotonic() - start >= actual_duration:
                        break
                    if response.get("sync_response"):
                        continue
                    if "update" in response:
                        collected.append(response["update"])
            return {
                "status": "success",
                "device": host,
                "data": collected,
                "duration_seconds": round(time.monotonic() - start, 1),
            }
        except Exception as e:
            return {"status": "error", "device": host, "error": _map_grpc_error(e)}

    def execute_set(
        self,
        host: str,
        updates: list[tuple[str, dict]] | None = None,
        deletes: list[str] | None = None,
        port: int | None = None,
    ) -> dict:
        """Execute a gNMI Set operation to modify device configuration.

        Args:
            host: Device hostname/IP.
            updates: List of (path, value) tuples for update operations.
            deletes: List of paths for delete operations.
            port: Optional port override.

        Returns:
            Normalised result dict with set response.
        """
        if not updates and not deletes:
            return {"status": "error", "device": host, "error": "No updates or deletes provided"}

        try:
            with self.create_client(host, port) as gc:
                kwargs: dict[str, Any] = {}
                if updates:
                    kwargs["update"] = updates
                if deletes:
                    kwargs["delete"] = deletes
                result = gc.set(**kwargs)
            return {"status": "success", "device": host, "data": result}
        except Exception as e:
            return {"status": "error", "device": host, "error": _map_grpc_error(e)}

    def execute_capabilities(self, host: str, port: int | None = None) -> dict:
        """Get gNMI capabilities from a device.

        Returns supported models, encodings, and gNMI version.
        """
        try:
            with self.create_client(host, port) as gc:
                caps = gc.capabilities()
            return {
                "status": "success",
                "device": host,
                "data": {
                    "gnmi_version": caps.get("gnmi_version", "unknown"),
                    "supported_encodings": caps.get("supported_encodings", []),
                    "supported_models": [
                        {
                            "name": m.get("name", ""),
                            "organization": m.get("organization", ""),
                            "version": m.get("version", ""),
                        }
                        for m in caps.get("supported_models", [])
                    ],
                },
            }
        except Exception as e:
            return {"status": "error", "device": host, "error": _map_grpc_error(e)}


class _PoolEntry:
    """Internal wrapper storing a pooled gNMI client and its creation timestamp."""

    __slots__ = ("client", "created_at", "refcount")

    def __init__(self, client: Any, created_at: float) -> None:
        self.client = client
        self.created_at = created_at
        self.refcount = 0


class GnmiConnectionPool:
    """Thread-safe gNMI connection pool with TTL-based expiry and FIFO eviction.

    Pools gNMIclient instances keyed by ``(host, port)`` tuples. Connections are
    lazily created on first request and automatically evicted when the pool
    exceeds ``max_size`` or when their TTL expires.

    Unlike GnmiManager (which creates a fresh connection per request), the pool
    reuses existing connections for better performance when making many gNMI
    calls to the same device.

    Note: gRPC channels have internal thread-safety for unary RPCs, but callers
    should still ensure a single pooled connection is not used concurrently for
    streaming RPCs. The pool is safe for concurrent *get_connection* calls.
    """

    def __init__(self, settings: "NetworkSettings") -> None:
        self._default_username = settings.net_username
        self._default_password = settings.net_password.get_secret_value()
        self._default_port = settings.gnmi_port
        self._insecure = settings.gnmi_insecure
        self._timeout = settings.gnmi_timeout
        self._tls_cert = settings.gnmi_tls_cert
        self._tls_key = settings.gnmi_tls_key
        self._tls_ca = settings.gnmi_tls_ca
        self._max_size = settings.net_gnmi_pool_max
        self._ttl = settings.net_gnmi_pool_ttl

        self._pool: OrderedDict[tuple[str, int], _PoolEntry] = OrderedDict()
        self._lock = threading.Lock()

    def _build_client_kwargs(self, host: str, port: int) -> dict[str, Any]:
        """Build kwargs dict for gNMIclient construction."""
        kwargs: dict[str, Any] = {
            "target": (host, str(port)),
            "username": self._default_username,
            "password": self._default_password,
            "insecure": self._insecure,
            "gnmi_timeout": self._timeout,
        }
        if self._tls_cert and self._tls_key:
            kwargs["insecure"] = False
            kwargs["override"] = host
            kwargs["path_cert"] = self._tls_cert
            kwargs["path_key"] = self._tls_key
            if self._tls_ca:
                kwargs["path_root"] = self._tls_ca
        return kwargs

    def _evict_expired(self) -> int:
        """Remove all entries whose TTL has elapsed. Must be called with _lock held.

        Entries with refcount > 0 are skipped (still in use by callers).

        Returns:
            Number of entries evicted.
        """
        now = time.monotonic()
        expired_keys = [
            k for k, entry in self._pool.items() if (now - entry.created_at) >= self._ttl and entry.refcount <= 0
        ]
        for key in expired_keys:
            entry = self._pool.pop(key)
            self._close_client(entry.client)
            logger.debug("gNMI pool: evicted expired connection %s:%d", key[0], key[1])
        return len(expired_keys)

    def _evict_oldest(self) -> None:
        """Remove the oldest (first-inserted) entry with refcount 0. Must be called with _lock held."""
        for key in list(self._pool):
            entry = self._pool[key]
            if entry.refcount <= 0:
                self._pool.pop(key)
                self._close_client(entry.client)
                logger.debug("gNMI pool: evicted oldest connection %s:%d (pool full)", key[0], key[1])
                return
        logger.warning("gNMI pool: all connections in use, cannot evict (pool size: %d)", len(self._pool))

    @staticmethod
    def _close_client(client: Any) -> None:
        """Attempt to close a gNMI client connection gracefully."""
        try:
            if hasattr(client, "close"):
                client.close()
        except Exception:
            logger.warning("Failed to close gNMI client: best-effort cleanup", exc_info=True)

    def get_connection(self, host: str, port: int | None = None) -> Any:
        """Get or create a pooled gNMI client for the given host and port.

        If a cached connection exists and has not expired, it is returned.
        Otherwise a new connection is created, added to the pool, and returned.
        When the pool is full, expired entries are evicted first, then the
        oldest entry is evicted via FIFO.

        Args:
            host: Device hostname or IP address.
            port: gNMI port override (defaults to GNMI_PORT setting).

        Returns:
            A connected gNMIclient instance.

        Raises:
            RuntimeError: If pygnmi is not installed.
        """
        if not GNMI_AVAILABLE:
            raise RuntimeError("pygnmi is not installed. Install with: pip install latticio[gnmi]")

        actual_port = port or self._default_port
        key = (host, actual_port)

        with self._lock:
            # Check for existing valid connection
            if key in self._pool:
                entry = self._pool[key]
                if (time.monotonic() - entry.created_at) < self._ttl:
                    # Move to end to maintain LRU-like ordering
                    self._pool.move_to_end(key)
                    entry.refcount += 1
                    logger.debug("gNMI pool: cache hit for %s:%d (refcount=%d)", host, actual_port, entry.refcount)
                    return entry.client
                # Expired and not in use — remove it
                if entry.refcount <= 0:
                    self._pool.pop(key)
                    self._close_client(entry.client)
                    logger.debug("gNMI pool: expired connection %s:%d", host, actual_port)
                else:
                    # Expired but still in use — reuse it
                    entry.refcount += 1
                    logger.debug("gNMI pool: reusing expired but in-use connection %s:%d", host, actual_port)
                    return entry.client

            # Need a new connection — make room if necessary
            self._evict_expired()
            while len(self._pool) >= self._max_size:
                self._evict_oldest()

            # Create new client
            kwargs = self._build_client_kwargs(host, actual_port)
            client = gNMIclient(**kwargs)
            # Connect the client (gNMIclient context manager calls connect)
            try:
                client.__enter__()
            except Exception:
                self._close_client(client)
                raise

            entry = _PoolEntry(client=client, created_at=time.monotonic())
            entry.refcount = 1
            self._pool[key] = entry
            logger.debug("gNMI pool: created new connection %s:%d (pool size: %d)", host, actual_port, len(self._pool))
            return client

    def release_connection(self, host: str, port: int | None = None) -> None:
        """Release a connection back to the pool, decrementing its reference count.

        Must be called after each get_connection() when the caller is done
        using the connection. Connections with refcount > 0 are protected
        from eviction.

        Args:
            host: Device hostname or IP address.
            port: gNMI port override.
        """
        actual_port = port or self._default_port
        key = (host, actual_port)
        with self._lock:
            entry = self._pool.get(key)
            if entry and entry.refcount > 0:
                entry.refcount -= 1
                logger.debug("gNMI pool: released connection %s:%d (refcount=%d)", host, actual_port, entry.refcount)

    def close_connection(self, host: str, port: int | None = None) -> None:
        """Explicitly close and remove a specific connection from the pool.

        Args:
            host: Device hostname or IP address.
            port: gNMI port override.
        """
        actual_port = port or self._default_port
        key = (host, actual_port)
        with self._lock:
            entry = self._pool.pop(key, None)
            if entry:
                self._close_client(entry.client)
                logger.debug("gNMI pool: closed connection %s:%d", host, actual_port)

    def close_all(self) -> None:
        """Close all pooled connections and clear the pool."""
        with self._lock:
            for key, entry in self._pool.items():
                self._close_client(entry.client)
                logger.debug("gNMI pool: closed connection %s:%d", key[0], key[1])
            self._pool.clear()

    @property
    def pool_size(self) -> int:
        """Return the current number of connections in the pool."""
        with self._lock:
            return len(self._pool)


def _flatten_notifications(result: dict) -> list[dict]:
    """Flatten pygnmi notification structure into a simpler list.

    pygnmi returns ``{"notification": [{"timestamp": ..., "update": [...]}]}``.
    We flatten this to a list of ``{"path": ..., "val": ...}`` dicts.
    """
    flat: list[dict] = []
    for notification in result.get("notification", []):
        timestamp = notification.get("timestamp", 0)
        for update in notification.get("update", []):
            flat.append(
                {
                    "path": update.get("path", ""),
                    "val": update.get("val"),
                    "timestamp": timestamp,
                }
            )
    return flat
