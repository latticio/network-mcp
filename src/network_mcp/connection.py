"""Multi-vendor device connection management via NetworkDriver abstraction."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from pydantic import BaseModel, SecretStr

from network_mcp.drivers import DRIVER_REGISTRY
from network_mcp.drivers.base import NetworkDriver

if TYPE_CHECKING:
    from network_mcp.config import NetworkSettings
    from network_mcp.integrations.vault import VaultCredentialProvider
    from network_mcp.inventory import InventoryBackend

logger = logging.getLogger("network-mcp")

DEFAULT_MAX_CONNECTIONS = 100


# --- Bulkhead Isolation ---


class BulkheadManager:
    """Per-vendor concurrency limits to isolate failure domains.

    Prevents one vendor's slow/failing devices from exhausting the
    connection pool and blocking operations to other vendors.
    """

    def __init__(self, per_vendor_limit: int = 20):
        self._per_vendor_limit = per_vendor_limit
        self._lock = threading.Lock()
        self._semaphores: dict[str, threading.Semaphore] = {}

    def _get_semaphore(self, vendor: str) -> threading.Semaphore:
        """Get or create a semaphore for a vendor."""
        with self._lock:
            if vendor not in self._semaphores:
                self._semaphores[vendor] = threading.Semaphore(self._per_vendor_limit)
            return self._semaphores[vendor]

    def acquire(self, vendor: str, timeout: float = 30.0) -> bool:
        """Acquire a slot for the given vendor. Returns False if timeout expires."""
        sem = self._get_semaphore(vendor)
        return sem.acquire(timeout=timeout)

    def release(self, vendor: str) -> None:
        """Release a slot for the given vendor."""
        sem = self._get_semaphore(vendor)
        sem.release()

    def get_available(self, vendor: str) -> int:
        """Return approximate available slots for a vendor."""
        sem = self._get_semaphore(vendor)
        # Semaphore._value is CPython implementation detail, use cautiously
        return getattr(sem, "_value", self._per_vendor_limit)

    def reset(self) -> None:
        """Reset all semaphores. For testing only."""
        with self._lock:
            self._semaphores.clear()


_bulkhead: BulkheadManager | None = None


def get_bulkhead() -> BulkheadManager | None:
    """Return the global BulkheadManager instance, or None if disabled."""
    return _bulkhead


def init_bulkhead(per_vendor_limit: int) -> BulkheadManager:
    """Initialize the global BulkheadManager with the given per-vendor limit."""
    global _bulkhead
    _bulkhead = BulkheadManager(per_vendor_limit)
    return _bulkhead


@contextmanager
def bulkhead_slot(vendor: str, timeout: float = 30.0) -> Generator[None, None, None]:
    """Context manager to acquire and release a bulkhead slot."""
    bh = get_bulkhead()
    if bh is None:
        yield
        return
    if not bh.acquire(vendor, timeout):
        raise TimeoutError(
            f"Bulkhead timeout: no available slots for vendor '{vendor}' "
            f"after {timeout}s. {bh.get_available(vendor)} of {bh._per_vendor_limit} slots in use."
        )
    try:
        yield
    finally:
        bh.release(vendor)


def _calculate_max_connections(inventory_size: int) -> int:
    """Calculate optimal pool size based on inventory.

    Formula: min(inventory_size * 2, 500) — allows up to 2 concurrent connections
    per device while capping at 500 to avoid file descriptor exhaustion.
    Falls back to DEFAULT_MAX_CONNECTIONS if inventory is empty or very small.
    """
    if inventory_size <= 0:
        return DEFAULT_MAX_CONNECTIONS
    return min(inventory_size * 2, 500)


class DeviceCredentials(BaseModel):
    """Credentials for an EOS device."""

    host: str
    username: str = "admin"
    password: SecretStr = SecretStr("")
    transport: str = "https"
    port: int = 443
    platform: str = "eos"

    # Multi-device metadata
    role: str | None = None
    groups: list[str] = []
    tags: list[str] = []
    mlag_peer: str | None = None
    tenants: list[str] = []


class ConnectionManager:
    """Manages NetworkDriver connections to network devices.

    Stores NetworkDriver instances instead of raw pyeapi nodes.
    Supports pluggable InventoryBackend for device lookup.
    """

    def __init__(
        self,
        settings: NetworkSettings,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        inventory_backend: InventoryBackend | None = None,
        vault_provider: VaultCredentialProvider | None = None,
    ) -> None:
        self._drivers: dict[str, NetworkDriver] = {}
        self._refcounts: dict[str, int] = {}
        self._verified: set[str] = set()
        self._nodes_lock = threading.Lock()
        # Per-host locks ensure only one thread uses a driver at a time,
        # preventing pyeapi's non-thread-safe HTTP transport from concurrent access.
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_locks_guard = threading.Lock()
        self._inventory: dict[str, DeviceCredentials] = {}
        self._default_username = settings.net_username
        self._default_password = settings.net_password.get_secret_value()
        self._default_transport = settings.net_transport
        self._timeout = settings.timeout_connect
        self._keepalive_interval = settings.keepalive_interval
        self._auto_verify = settings.net_verify_connections
        self._verify_ssl = settings.net_verify_ssl
        self._demo_mode = settings.net_demo_mode
        self._vault_provider = vault_provider

        # Pool statistics tracking
        self._total_created: int = 0
        self._total_checkouts: int = 0
        self._total_checkout_time: float = 0.0
        self._stats_lock = threading.Lock()

        # Load inventory from backend if provided
        if inventory_backend is not None:
            self._inventory = inventory_backend.get_devices()

        # Initialize bulkhead isolation if configured
        if settings.net_bulkhead_per_vendor > 0:
            init_bulkhead(settings.net_bulkhead_per_vendor)
            logger.info("Bulkhead isolation enabled: %d slots per vendor", settings.net_bulkhead_per_vendor)

        # Auto-tune max_connections: explicit setting > auto-calculation > default
        explicit_max = getattr(settings, "max_connections", None)
        if explicit_max is not None:
            self._max_connections = explicit_max
        elif max_connections != DEFAULT_MAX_CONNECTIONS:
            # Caller passed an explicit value to __init__
            self._max_connections = max_connections
        elif self._inventory:
            self._max_connections = _calculate_max_connections(len(self._inventory))
            logger.info(
                "Connection pool auto-tuned: max_connections=%d (inventory_size=%d)",
                self._max_connections,
                len(self._inventory),
            )
        else:
            self._max_connections = DEFAULT_MAX_CONNECTIONS

    def _resolve_credentials(self, device_name: str) -> tuple[str, str]:
        """Resolve credentials for a device, checking Vault first if enabled.

        Returns:
            Tuple of (username, password).
        """
        if self._vault_provider is not None:
            try:
                creds = self._vault_provider.get_credentials(device_name)
                logger.debug("Using Vault credentials for device '%s'", device_name)
                return creds["username"], creds["password"]
            except Exception as e:
                logger.warning("Vault lookup failed for %s, falling back to env vars: %s", device_name, e)
        return self._default_username, self._default_password

    def load_inventory_from_backend(self, backend: InventoryBackend) -> None:
        """Load device inventory from an InventoryBackend."""
        self._inventory = backend.get_devices()

    def load_inventory(self, path: str) -> None:
        """Load device inventory from YAML file (legacy method).

        Prefer using an InventoryBackend passed to __init__ or load_inventory_from_backend().
        """
        from network_mcp.inventory import YamlInventoryBackend

        backend = YamlInventoryBackend(
            path=path,
            default_username=self._default_username,
            default_password=self._default_password,
            default_transport=self._default_transport,
        )
        self._inventory = backend.get_devices()

    def _get_host_lock(self, host: str) -> threading.Lock:
        """Get or create a per-host lock for thread-safe driver access."""
        with self._host_locks_guard:
            if host not in self._host_locks:
                self._host_locks[host] = threading.Lock()
            return self._host_locks[host]

    @staticmethod
    def _invalidate_cache_for_host(host: str) -> None:
        """Invalidate command cache entries for a host when its connection is evicted."""
        try:
            from network_mcp.helpers import command_cache

            command_cache.invalidate_host(host)
        except ImportError:
            pass  # Cache module not yet loaded during init

    def _close_driver(self, driver: NetworkDriver) -> None:
        """Attempt to close a driver's connection."""
        try:
            driver.close()
        except Exception as e:
            logger.debug("Failed to close driver: %s", e)

    def _create_driver(self, creds: DeviceCredentials, device_name: str | None = None) -> NetworkDriver:
        """Create and connect a NetworkDriver for the given credentials.

        Uses DRIVER_REGISTRY to select the correct driver class based on
        the device's platform field. In demo mode, creates a MockDriver.

        Raises:
            ValueError: If the device's platform is not in DRIVER_REGISTRY.
        """
        if self._demo_mode:
            from network_mcp.mock_driver import MockDriver

            driver = MockDriver()
            # MockDriver uses device_name (e.g., "spine-01") for response lookup,
            # not the IP address stored in creds.host.
            driver.connect(host=device_name or creds.host)
            return driver  # type: ignore[return-value]

        platform = creds.platform
        driver_cls = DRIVER_REGISTRY.get(platform)
        if driver_cls is None:
            raise ValueError(
                f"Unsupported platform '{platform}' for device '{creds.host}'. "
                f"Supported platforms: {', '.join(sorted(DRIVER_REGISTRY))}. "
                "Check the 'platform' field in your inventory."
            )

        driver = driver_cls()  # type: ignore[assignment]
        driver.connect(
            host=creds.host,
            transport=creds.transport,
            username=creds.username,
            password=creds.password.get_secret_value(),
            port=creds.port,
            timeout=self._timeout,
            verify_ssl=self._verify_ssl,
            keepalive_interval=self._keepalive_interval,
        )
        return driver  # type: ignore[return-value]

    def _verify_driver(self, host: str, driver: NetworkDriver) -> None:
        """Verify a driver by running 'show version'. Called WITHOUT _nodes_lock held.

        On failure, the caller is responsible for cleaning up _drivers and _refcounts
        under the lock. This method only performs the network I/O.
        """
        if host in self._verified:
            return
        try:
            driver.run_show(["show version"])
            self._verified.add(host)
            # Signal health module that at least one device is verified
            from network_mcp.health import mark_device_verified

            mark_device_verified()
            logger.debug("Connection verified for '%s'", host)
        except Exception as e:
            raise ConnectionError(
                f"Connection verification failed for '{host}': {e}. "
                "Verify: (1) device is reachable, (2) eAPI is enabled "
                "('management api http-commands' in EOS config), "
                "(3) credentials are correct."
            ) from e

    def _evict_one(self) -> bool:
        """Evict oldest connection with refcount == 0. Returns False if all in use.

        Called under _nodes_lock.
        """
        for host in list(self._drivers):  # insertion order = FIFO
            if self._refcounts.get(host, 0) == 0:
                old_driver = self._drivers.pop(host)
                self._refcounts.pop(host, None)
                self._verified.discard(host)
                self._close_driver(old_driver)
                self._invalidate_cache_for_host(host)
                logger.debug("Evicted cached connection for '%s' (pool full)", host)
                return True
        return False  # all connections in active use

    def _cleanup_failed_verify(self, host: str, driver: NetworkDriver) -> None:
        """Remove a driver that failed verification, cleaning up all tracking state."""
        with self._nodes_lock:
            self._drivers.pop(host, None)
            self._refcounts.pop(host, None)
            self._verified.discard(host)
        self._close_driver(driver)

    @contextmanager
    def acquire(self, host: str, verify: bool | None = None) -> Generator[NetworkDriver, None, None]:
        """Get a connection with ref-count and per-host lock protection.

        Thread-safety: A per-host lock ensures only one thread uses a driver at
        a time, preventing pyeapi's non-thread-safe HTTP transport from concurrent
        access. Different hosts are fully parallel. The pool-level lock (_nodes_lock)
        is only held briefly for bookkeeping, never during I/O.

        Args:
            host: Device hostname, IP, or inventory name.
            verify: If True, validate connection on first use.
        """
        # Acquire the per-host lock FIRST to serialize access to this device
        host_lock = self._get_host_lock(host)
        host_lock.acquire()
        try:
            checkout_start = time.monotonic()
            need_verify = False
            with self._nodes_lock:
                should_verify = verify if verify is not None else self._auto_verify

                if host in self._drivers:
                    driver = self._drivers[host]
                    need_verify = should_verify and host not in self._verified
                else:
                    # Need to create a new connection
                    if len(self._drivers) >= self._max_connections:
                        if not self._evict_one():
                            raise ConnectionError(
                                f"Connection pool exhausted ({self._max_connections}/{self._max_connections} in use). "
                                "Retry later."
                            )

                    if host in self._inventory:
                        creds = self._inventory[host]
                    else:
                        username, password = self._resolve_credentials(host)
                        creds = DeviceCredentials(
                            host=host,
                            username=username,
                            password=SecretStr(password),
                            transport=self._default_transport,
                        )

                    driver = self._create_driver(creds, device_name=host)
                    self._drivers[host] = driver
                    need_verify = should_verify
                    with self._stats_lock:
                        self._total_created += 1

                # Atomic refcount increment
                self._refcounts[host] = self._refcounts.get(host, 0) + 1

            # Verify OUTSIDE the pool lock to avoid blocking other hosts during I/O
            if need_verify:
                try:
                    self._verify_driver(host, driver)
                except Exception:
                    self._cleanup_failed_verify(host, driver)
                    raise

            checkout_elapsed = time.monotonic() - checkout_start
            with self._stats_lock:
                self._total_checkouts += 1
                self._total_checkout_time += checkout_elapsed

            try:
                yield driver
            finally:
                with self._nodes_lock:
                    if host in self._refcounts:
                        self._refcounts[host] -= 1
                        if self._refcounts[host] <= 0:
                            self._refcounts.pop(host, None)
        finally:
            host_lock.release()

    def get_driver(self, host: str, verify: bool | None = None) -> NetworkDriver:
        """Get a DeviceDriver for a device.

        Note: Prefer acquire() for ref-count protection in tool code.
        get_driver() is retained for backward compatibility.

        Args:
            host: Either a hostname/IP or a device name from inventory.
                  Inventory names are checked first.
            verify: If True, run 'show version' to validate the connection on
                    first use. If None, uses the NET_VERIFY_CONNECTIONS setting.
        """
        need_verify = False
        with self._nodes_lock:
            should_verify = verify if verify is not None else self._auto_verify

            if host in self._drivers:
                driver = self._drivers[host]
                need_verify = should_verify and host not in self._verified
            else:
                # Evict oldest entry with refcount==0 if at capacity
                if len(self._drivers) >= self._max_connections:
                    if not self._evict_one():
                        raise ConnectionError(
                            f"Connection pool exhausted ({self._max_connections}/{self._max_connections} in use). "
                            "Retry later."
                        )

                # Check inventory
                if host in self._inventory:
                    creds = self._inventory[host]
                else:
                    username, password = self._resolve_credentials(host)
                    creds = DeviceCredentials(
                        host=host,
                        username=username,
                        password=SecretStr(password),
                        transport=self._default_transport,
                    )

                driver = self._create_driver(creds, device_name=host)
                self._drivers[host] = driver
                need_verify = should_verify
                with self._stats_lock:
                    self._total_created += 1

        # Verify outside the lock to avoid blocking all threads during network I/O
        if need_verify:
            try:
                self._verify_driver(host, driver)
            except Exception:
                self._cleanup_failed_verify(host, driver)
                raise

        return driver

    def get_node(self, host: str, verify: bool | None = None) -> NetworkDriver:
        """Alias for get_driver() — backward compatibility.

        EosDriver exposes run_commands() and config() methods that match
        the pyeapi.Node interface, so existing tool code continues to work.
        """
        return self.get_driver(host, verify=verify)

    def remove_node(self, host: str) -> None:
        """Remove and close a single cached connection."""
        with self._nodes_lock:
            driver = self._drivers.pop(host, None)
            self._refcounts.pop(host, None)
            self._verified.discard(host)
            if driver:
                self._close_driver(driver)
                self._invalidate_cache_for_host(host)
        with self._host_locks_guard:
            self._host_locks.pop(host, None)

    def list_devices(self) -> list[str]:
        """Return all device names from the inventory."""
        with self._nodes_lock:
            return list(self._inventory.keys())

    def get_inventory(self) -> dict[str, DeviceCredentials]:
        """Return a snapshot of the full inventory. Public API for read access."""
        with self._nodes_lock:
            return dict(self._inventory)

    def get_platform(self, host: str) -> str:
        """Return the platform string for a host from inventory, defaulting to 'eos'."""
        with self._nodes_lock:
            creds = self._inventory.get(host)
            if creds is not None:
                return creds.platform
        return "eos"

    def get_devices_by_group(self, group: str) -> list[str]:
        """Return device names that belong to the given group."""
        with self._nodes_lock:
            return [name for name, creds in self._inventory.items() if group in creds.groups]

    def get_devices_by_tag(self, tag: str) -> list[str]:
        """Return device names that have the given tag."""
        with self._nodes_lock:
            return [name for name, creds in self._inventory.items() if tag in creds.tags]

    def get_devices_by_role(self, role: str) -> list[str]:
        """Return device names that have the given role."""
        with self._nodes_lock:
            return [name for name, creds in self._inventory.items() if creds.role == role]

    def pool_stats(self) -> dict:
        """Return connection pool statistics for observability."""
        with self._nodes_lock:
            active = sum(1 for rc in self._refcounts.values() if rc > 0)
            idle = len(self._drivers) - active
            per_device = {host: {"refcount": self._refcounts.get(host, 0)} for host in self._drivers}

        with self._stats_lock:
            total_created = self._total_created
            total_checkouts = self._total_checkouts
            avg_checkout_ms = (
                (self._total_checkout_time / self._total_checkouts * 1000) if self._total_checkouts > 0 else 0.0
            )

        return {
            "active": active,
            "idle": idle,
            "pooled": active + idle,
            "max": self._max_connections,
            "total_created": total_created,
            "total_checkouts": total_checkouts,
            "avg_checkout_ms": round(avg_checkout_ms, 3),
            "per_device": per_device,
        }

    def close_all(self) -> None:
        """Close all cached connections and clear the cache."""
        with self._nodes_lock:
            for driver in self._drivers.values():
                self._close_driver(driver)
            self._drivers.clear()
            self._refcounts.clear()
            self._verified.clear()
        with self._host_locks_guard:
            self._host_locks.clear()
