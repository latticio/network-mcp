"""Command result cache with TTL-based expiry, LRU eviction, and memory limits."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger("network-mcp")

# Default TTLs by command pattern (seconds). Order matters — first match wins.
_DEFAULT_TTLS: list[tuple[str, int]] = [
    ("show version", 300),
    ("show inventory", 300),
    ("show processes top", 0),
    ("show interfaces status", 30),
    ("show ip bgp summary", 15),
    ("show running-config", 60),
]
_FALLBACK_TTL = 30


def get_default_ttl(commands: list[str]) -> int:
    """Return default cache TTL for a command list based on pattern matching.

    Matches against the first command in the list. Returns 0 (no cache) for
    commands like 'show processes top'. Falls back to 30s for unmatched commands.
    """
    if not commands:
        return 0
    first_cmd = commands[0].lower().strip()
    for pattern, ttl in _DEFAULT_TTLS:
        if first_cmd.startswith(pattern):
            return ttl
    return _FALLBACK_TTL


@dataclass
class CacheEntry:
    """A single cached command result."""

    data: object
    timestamp: float
    ttl: int
    size_bytes: int
    # LRU tracking: updated on every access
    last_accessed: float = field(default_factory=time.monotonic)

    @property
    def is_expired(self) -> bool:
        """Return True if this entry has exceeded its TTL."""
        return (time.monotonic() - self.timestamp) > self.ttl

    @property
    def age_seconds(self) -> float:
        """Return the age of this entry in seconds."""
        return time.monotonic() - self.timestamp


def _estimate_size(data: object) -> int:
    """Estimate memory size of cached data in bytes.

    Uses JSON serialization length as a size proxy. More memory-efficient
    than repr() for large objects since json.dumps streams internally.
    Exact accounting is not needed — this is a guard against unbounded memory growth.
    """
    try:
        import json

        return len(json.dumps(data, default=str))
    except Exception:
        return sys.getsizeof(data)


_CACHE_KDF_SALT = b"network-mcp-cache-encryption-v1"


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a Fernet-compatible key from an arbitrary secret string.

    Uses HKDF (if cryptography is installed) or PBKDF2-HMAC-SHA256 (stdlib)
    to produce 32 bytes, then base64url-encodes for Fernet.
    """
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=_CACHE_KDF_SALT, info=b"fernet-key")
        digest = hkdf.derive(secret.encode())
    except ImportError:
        # Fallback to stdlib PBKDF2 when cryptography is not installed
        digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), _CACHE_KDF_SALT, iterations=100_000)
    return base64.urlsafe_b64encode(digest)


class _CacheEncryptor:
    """Handles Fernet encryption/decryption for cached values."""

    def __init__(self, encryption_key: str | None = None, auth_secret_key: str | None = None) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise ImportError(
                "cryptography is required for cache encryption. Install with: pip install cryptography"
            ) from None

        if encryption_key:
            key = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        elif auth_secret_key:
            key = _derive_fernet_key(auth_secret_key)
        else:
            raise ValueError("NET_CACHE_ENCRYPTION_KEY or AUTH_SECRET_KEY required when NET_CACHE_ENCRYPT=true")

        self._fernet = Fernet(key)

    def encrypt(self, data: object) -> bytes:
        """Serialize data to JSON and encrypt with Fernet."""
        plaintext = json.dumps(data, default=str).encode()
        return self._fernet.encrypt(plaintext)

    def decrypt(self, token: bytes) -> object:
        """Decrypt Fernet token and deserialize from JSON."""
        plaintext = self._fernet.decrypt(token)
        return json.loads(plaintext)


class CommandCache:
    """Thread-safe command result cache with TTL expiry, LRU eviction, and memory limits.

    Key: (host, tuple(commands), encoding) — immutable tuple for hashability.
    Value: CacheEntry with data, timestamp, TTL, and size estimate.

    Thread safety: Uses threading.Lock with small critical sections.
    The lock is never held during device I/O.
    """

    def __init__(
        self,
        max_entries: int = 500,
        max_memory_mb: int = 50,
        enabled: bool = True,
        encrypt: bool = False,
        encryption_key: str | None = None,
        auth_secret_key: str | None = None,
    ) -> None:
        self._cache: dict[tuple[str, tuple[str, ...], str], CacheEntry] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._max_memory_bytes = max_memory_mb * 1024 * 1024
        self._current_memory_bytes = 0
        self._enabled = enabled
        # Stats
        self._hits = 0
        self._misses = 0
        # Encryption
        self._encryptor: _CacheEncryptor | None = None
        if encrypt:
            self._encryptor = _CacheEncryptor(
                encryption_key=encryption_key,
                auth_secret_key=auth_secret_key,
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def _make_key(host: str, commands: list[str], encoding: str) -> tuple[str, tuple[str, ...], str]:
        return (host, tuple(commands), encoding)

    def get(
        self,
        host: str,
        commands: list[str],
        encoding: str = "json",
        stale_if_error: bool = False,
        stale_ttl: int = 300,
    ) -> object | None:
        """Return cached data if present and not expired, else None.

        When stale_if_error=True and the entry is expired but within stale_ttl,
        returns the stale entry wrapped with cache metadata. This allows serving
        expired data when the device is unreachable.

        Args:
            host: Device hostname.
            commands: Command list.
            encoding: Response encoding.
            stale_if_error: If True, return expired entries within stale_ttl window.
            stale_ttl: Maximum seconds past expiry to still serve stale data.
        """
        if not self._enabled:
            return None

        key = self._make_key(host, commands, encoding)
        now = time.monotonic()

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            # Check TTL expiry
            if now - entry.timestamp > entry.ttl:
                if stale_if_error and stale_ttl > 0:
                    stale_age = entry.age_seconds - entry.ttl
                    if stale_age <= stale_ttl:
                        entry.last_accessed = now
                        self._hits += 1
                        return self._wrap_stale_response(entry)
                # Normal behavior: expired = cache miss (don't delete — may be needed for stale-if-error)
                self._misses += 1
                return None

            # Cache hit — update LRU timestamp
            entry.last_accessed = now
            self._hits += 1

            if self._encryptor is not None and isinstance(entry.data, bytes):
                try:
                    return self._encryptor.decrypt(entry.data)
                except Exception:
                    logger.warning("Cache decryption failed for %s; treating as miss", key[0])
                    self._hits -= 1
                    self._misses += 1
                    return None

            return entry.data

    def _wrap_stale_response(self, entry: CacheEntry) -> dict:
        """Wrap a stale cache entry with metadata indicating it's stale."""
        data = entry.data
        if self._encryptor is not None and isinstance(data, bytes):
            try:
                data = self._encryptor.decrypt(data)
            except Exception:
                logger.warning("Cache decryption failed for stale entry; treating as miss")
                return {}
        if isinstance(data, dict):
            result = dict(data)
            result["_cache"] = {
                "stale": True,
                "age_seconds": round(entry.age_seconds, 1),
                "original_ttl": entry.ttl,
                "expired_seconds_ago": round(entry.age_seconds - entry.ttl, 1),
            }
            return result
        return data  # type: ignore[return-value]

    def put(self, host: str, commands: list[str], encoding: str, data: object, ttl: int) -> None:
        """Store a result in the cache with the given TTL (seconds).

        If ttl <= 0, the result is not cached.
        """
        if not self._enabled or ttl <= 0:
            return

        key = self._make_key(host, commands, encoding)
        size = _estimate_size(data)
        store_data: object = data
        if self._encryptor is not None:
            store_data = self._encryptor.encrypt(data)
            size = len(store_data)  # type: ignore[arg-type]
        now = time.monotonic()
        new_entry = CacheEntry(data=store_data, timestamp=now, ttl=ttl, size_bytes=size, last_accessed=now)

        with self._lock:
            # If key already exists, remove old entry's memory accounting
            old = self._cache.get(key)
            if old is not None:
                self._current_memory_bytes -= old.size_bytes

            # Evict entries if needed to stay within limits
            self._evict_if_needed(size)

            self._cache[key] = new_entry
            self._current_memory_bytes += size

    def _evict_if_needed(self, incoming_size: int) -> None:
        """Evict LRU entries until space is available. Called under lock."""
        # Evict for entry count limit
        while len(self._cache) >= self._max_entries:
            self._evict_lru()

        # Evict for memory limit
        while self._current_memory_bytes + incoming_size > self._max_memory_bytes and self._cache:
            self._evict_lru()

    def _evict_lru(self) -> None:
        """Remove the least-recently-accessed entry. Called under lock."""
        if not self._cache:
            return
        lru_key = min(self._cache, key=lambda k: self._cache[k].last_accessed)
        entry = self._cache.pop(lru_key)
        self._current_memory_bytes -= entry.size_bytes
        logger.debug("Cache evicted entry for %s (LRU)", lru_key[0])

    def invalidate_host(self, host: str) -> int:
        """Remove all cached entries for a specific host. Returns count of removed entries."""
        with self._lock:
            keys_to_remove = [k for k in self._cache if k[0] == host]
            for key in keys_to_remove:
                entry = self._cache.pop(key)
                self._current_memory_bytes -= entry.size_bytes
            count = len(keys_to_remove)

        if count:
            logger.debug("Cache invalidated %d entries for host '%s'", count, host)
        return count

    def clear(self) -> int:
        """Remove all cached entries. Returns count of removed entries."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._current_memory_bytes = 0
            self._hits = 0
            self._misses = 0
        if count:
            logger.debug("Cache cleared: %d entries removed", count)
        return count

    def stats(self) -> dict:
        """Return cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "enabled": self._enabled,
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "memory_mb": round(self._current_memory_bytes / (1024 * 1024), 2),
                "max_memory_mb": self._max_memory_bytes / (1024 * 1024),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
            }
