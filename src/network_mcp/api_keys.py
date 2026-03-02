"""API key authentication for service accounts and CI/CD pipelines.

Provides an alternative to JWT bearer tokens for machine-to-machine auth.
API keys use the format ``nmcp_<32 hex chars>`` and are stored as SHA-256
hashes in a JSON file. Keys support scope binding, tenant isolation,
optional expiration, and rotation.

Activated when ``NET_API_KEY_ENABLED=true``. API keys are detected in the
Authorization header by their prefix (default ``nmcp_``).
"""

import hashlib
import json
import logging
import os
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("network-mcp.api-keys")


@dataclass
class APIKey:
    """Metadata for a registered API key.

    The raw key is never stored — only its SHA-256 hash.
    """

    key_id: str
    key_hash: str
    name: str
    scopes: list[str]
    tenant_id: str | None = None
    created_at: str = ""  # ISO-8601 string for JSON serialization
    expires_at: str | None = None  # ISO-8601 or None
    last_used: str | None = None
    is_active: bool = True

    @property
    def is_expired(self) -> bool:
        """Return True if the key has an expiration and it has passed."""
        if self.expires_at is None:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return datetime.now(UTC) > exp
        except (ValueError, TypeError):
            return False


def _hash_key(raw_key: str) -> str:
    """Compute SHA-256 hex digest of a raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class APIKeyManager:
    """Manage API key lifecycle: generation, validation, revocation, and rotation.

    Keys are persisted to a JSON file with file-level locking for thread safety.
    """

    def __init__(self, key_file: str = ".api-keys.json", prefix: str = "nmcp_") -> None:
        self._key_file = Path(key_file)
        self._prefix = prefix
        self._lock = threading.Lock()
        self._keys: dict[str, APIKey] = {}
        self._hash_index: dict[str, str] = {}  # key_hash -> key_id (for fast lookup)
        self._load()

    # --- Public API ---

    def generate_key(
        self,
        name: str,
        scopes: list[str],
        tenant_id: str | None = None,
        ttl_days: int | None = None,
    ) -> tuple[str, APIKey]:
        """Generate a new API key.

        Args:
            name: Human-readable name for the key.
            scopes: Authorized scopes (e.g., ["network:read"]).
            tenant_id: Optional tenant binding.
            ttl_days: Optional expiration in days from now.

        Returns:
            Tuple of (raw_key, APIKey metadata). The raw key is only returned
            once and must be stored securely by the caller.
        """
        raw_key = self._prefix + secrets.token_hex(32)
        key_hash = _hash_key(raw_key)
        key_id = raw_key[: len(self._prefix) + 8]  # prefix + first 8 hex chars

        now = datetime.now(UTC)
        expires_at = None
        if ttl_days is not None and ttl_days > 0:
            from datetime import timedelta

            expires_at = (now + timedelta(days=ttl_days)).isoformat()

        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            scopes=list(scopes),
            tenant_id=tenant_id,
            created_at=now.isoformat(),
            expires_at=expires_at,
        )

        with self._lock:
            self._keys[key_id] = api_key
            self._hash_index[key_hash] = key_id
            self._save()

        logger.info("API key created: id=%s name=%s scopes=%s", key_id, name, scopes)
        return raw_key, api_key

    def validate_key(self, raw_key: str) -> APIKey | None:
        """Validate a raw API key and return its metadata if valid.

        Returns None if the key is unknown, inactive, or expired.
        """
        if not raw_key.startswith(self._prefix):
            return None

        key_hash = _hash_key(raw_key)

        with self._lock:
            key_id = self._hash_index.get(key_hash)
            if key_id is None:
                return None

            api_key = self._keys.get(key_id)
            if api_key is None:
                return None

            if not api_key.is_active:
                logger.debug("API key %s is inactive", key_id)
                return None

            if api_key.is_expired:
                logger.debug("API key %s has expired", key_id)
                return None

            # Update last_used timestamp
            api_key.last_used = datetime.now(UTC).isoformat()
            self._save()

        return api_key

    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key by its ID.

        Returns True if the key was found and deactivated.
        """
        with self._lock:
            api_key = self._keys.get(key_id)
            if api_key is None:
                return False
            api_key.is_active = False
            self._save()

        logger.info("API key revoked: id=%s name=%s", key_id, api_key.name)
        return True

    def list_keys(self) -> list[APIKey]:
        """List all API keys (both active and inactive).

        Never exposes raw keys or hashes in the returned list.
        """
        with self._lock:
            result = []
            for api_key in self._keys.values():
                # Return a copy with hash redacted
                safe_copy = APIKey(
                    key_id=api_key.key_id,
                    key_hash="[REDACTED]",
                    name=api_key.name,
                    scopes=list(api_key.scopes),
                    tenant_id=api_key.tenant_id,
                    created_at=api_key.created_at,
                    expires_at=api_key.expires_at,
                    last_used=api_key.last_used,
                    is_active=api_key.is_active,
                )
                result.append(safe_copy)
            return result

    def rotate_key(self, key_id: str) -> tuple[str, APIKey] | None:
        """Rotate an API key: revoke the old one and issue a new one with the same scopes.

        Returns the new (raw_key, APIKey) tuple, or None if the key_id was not found.
        """
        with self._lock:
            old_key = self._keys.get(key_id)
            if old_key is None:
                return None
            # Capture attributes before revoking
            name = old_key.name
            scopes = list(old_key.scopes)
            tenant_id = old_key.tenant_id
            # Deactivate old key
            old_key.is_active = False
            self._save()

        logger.info("Rotating API key: old_id=%s", key_id)
        return self.generate_key(name=name, scopes=scopes, tenant_id=tenant_id)

    # --- Persistence ---

    def _load(self) -> None:
        """Load keys from JSON file."""
        if not self._key_file.exists():
            return
        try:
            data = json.loads(self._key_file.read_text(encoding="utf-8"))
            for entry in data.get("keys", []):
                api_key = APIKey(**entry)
                self._keys[api_key.key_id] = api_key
                self._hash_index[api_key.key_hash] = api_key.key_id
            logger.info("Loaded %d API keys from %s", len(self._keys), self._key_file)
        except Exception:
            logger.exception("Failed to load API keys from %s", self._key_file)

    def _save(self) -> None:
        """Save keys to JSON file.

        Caller must hold self._lock. Uses atomic write (write to temp + rename)
        to prevent corruption.
        """
        data = {"keys": [asdict(k) for k in self._keys.values()]}
        tmp_path = self._key_file.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(str(tmp_path), str(self._key_file))
            os.chmod(str(self._key_file), 0o600)
        except Exception:
            logger.exception("Failed to save API keys to %s", self._key_file)
            # Clean up temp file on failure
            if tmp_path.exists():
                tmp_path.unlink()
