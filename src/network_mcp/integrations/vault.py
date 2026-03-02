"""HashiCorp Vault integration for device credential retrieval and rotation.

The ``hvac`` library is imported lazily (deferred) inside the methods that
require it so that this module can always be imported even when ``hvac`` is
not installed.  When ``hvac`` is missing, methods return a clear error dict
rather than raising ``ImportError``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("network-mcp")


def _import_hvac() -> Any:
    """Lazy import of the hvac library.

    Returns the hvac module if installed, or ``None`` with a warning if not.
    """
    try:
        import hvac  # noqa: PLC0415

        return hvac
    except ImportError:
        logger.warning("hvac library not installed. Vault integration unavailable.")
        return None


_HVAC_MISSING_ERROR: dict[str, str] = {
    "status": "error",
    "error": "hvac is required for Vault integration. Install with: pip install hvac",
}


class VaultCredentialProvider:
    """Retrieve device credentials from HashiCorp Vault KV v2.

    The ``hvac`` library is imported only when a ``VaultCredentialProvider``
    is instantiated or when methods are called.  If ``hvac`` is not installed,
    methods return an error dict instead of raising.
    """

    def __init__(self, addr: str, token: str, mount_point: str = "secret"):
        hvac = _import_hvac()
        if hvac is None:
            raise ImportError("hvac not installed. Install with: pip install latticio[vault]")
        self._client = hvac.Client(url=addr, token=token)
        self._mount = mount_point
        if not self._client.is_authenticated():
            raise RuntimeError("Vault authentication failed")

    def get_credentials(self, device_name: str) -> dict[str, str]:
        """Get username/password for a device from Vault.

        Looks up: {mount_point}/data/network/{device_name}
        Expected keys: username, password
        """
        try:
            secret = self._client.secrets.kv.v2.read_secret_version(
                path=f"network/{device_name}",
                mount_point=self._mount,
            )
            data = secret["data"]["data"]
            return {"username": data["username"], "password": data["password"]}
        except Exception as e:
            logger.error("Failed to retrieve credentials for %s from Vault: %s", device_name, e)
            raise

    def get_all_credentials(self) -> dict[str, dict[str, str]]:
        """List all device credentials under network/ path."""
        keys = self._client.secrets.kv.v2.list_secrets(path="network/", mount_point=self._mount)["data"]["keys"]
        return {key.rstrip("/"): self.get_credentials(key.rstrip("/")) for key in keys}


class CredentialRotationManager:
    """Manages credential lifecycle with TTL-based rotation.

    Re-fetches credentials from Vault when TTL expires.
    Falls back to static credentials if Vault is unavailable.

    Args:
        vault_client: An optional ``VaultCredentialProvider`` instance.
            If ``None``, only static fallback credentials are used.
        ttl_seconds: Time-to-live in seconds before cached credentials
            are considered stale and refreshed from Vault.  Defaults to
            3600 (1 hour).
        static_credentials: Optional dict mapping host to
            ``{"username": ..., "password": ...}`` used as fallback when
            Vault is unavailable.
    """

    def __init__(
        self,
        vault_client: VaultCredentialProvider | None = None,
        ttl_seconds: int = 3600,
        static_credentials: dict[str, dict[str, str]] | None = None,
    ):
        self._vault_client = vault_client
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[dict[str, str], float]] = {}  # host -> (creds, expiry_time)
        self._static_credentials = static_credentials or {}

    def get_credentials(self, host: str) -> dict[str, str]:
        """Get credentials for a host, refreshing from Vault if TTL expired.

        Resolution order:
        1. Return cached credentials if within TTL.
        2. Fetch from Vault if available.
        3. Fall back to static credentials.
        4. Return empty dict if nothing is available.
        """
        # Check cache
        if host in self._cache:
            creds, expiry = self._cache[host]
            if time.monotonic() < expiry:
                return creds

        # Try Vault
        if self._vault_client is not None:
            try:
                creds = self._vault_client.get_credentials(host)
                self._cache[host] = (creds, time.monotonic() + self._ttl_seconds)
                return creds
            except Exception as exc:
                logger.warning(
                    "Vault credential fetch failed for %s: %s. Falling back to static credentials.",
                    host,
                    exc,
                )

        # Fall back to static credentials
        if host in self._static_credentials:
            creds = self._static_credentials[host]
            self._cache[host] = (creds, time.monotonic() + self._ttl_seconds)
            return creds

        return {}

    def invalidate(self, host: str) -> None:
        """Force credential refresh on next access by removing cached entry."""
        self._cache.pop(host, None)

    def invalidate_all(self) -> None:
        """Force credential refresh for all hosts on next access."""
        self._cache.clear()
