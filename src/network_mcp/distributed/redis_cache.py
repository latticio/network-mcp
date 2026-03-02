"""Redis-backed distributed command cache.

Shares cached command results across all server instances.
Uses Redis strings with TTL for automatic expiry. JSON serialization
for stored values enables cross-process sharing.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("network-mcp")


class RedisCommandCache:
    """Distributed command cache backed by Redis.

    Implements the same public interface as CommandCache so it can be
    used as a drop-in replacement via the factory.

    Redis handles TTL expiry natively — no LRU eviction needed.
    Memory limits are enforced via Redis maxmemory configuration.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        max_entries: int = 500,
        max_memory_mb: int = 50,
        enabled: bool = True,
        key_prefix: str = "netmcp:cache",
    ):
        self._redis_url = redis_url
        self._max_entries = max_entries
        self._max_memory_mb = max_memory_mb
        self._enabled = enabled
        self._key_prefix = key_prefix
        self._redis = None
        # Stats tracked locally per instance (not shared across instances)
        self._hits = 0
        self._misses = 0

    def _get_redis(self):
        """Lazy Redis connection."""
        if self._redis is None:
            import redis

            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _make_key(self, host: str, commands: list[str], encoding: str = "json") -> str:
        """Build a Redis key from host + commands + encoding."""
        cmd_part = "|".join(commands)
        return f"{self._key_prefix}:{host}:{cmd_part}:{encoding}"

    def get(self, host: str, commands: list[str], encoding: str = "json") -> object | None:
        """Return cached data if present, else None. Redis handles TTL expiry."""
        if not self._enabled:
            return None

        try:
            r = self._get_redis()
            key = self._make_key(host, commands, encoding)
            data = r.get(key)
            if data is None:
                self._misses += 1
                return None
            self._hits += 1
            return json.loads(data)
        except Exception:
            logger.warning("Redis cache get failed", exc_info=True)
            self._misses += 1
            return None

    def put(self, host: str, commands: list[str], encoding: str, data: object, ttl: int) -> None:
        """Cache command result in Redis with TTL."""
        if not self._enabled or ttl <= 0:
            return

        try:
            r = self._get_redis()
            key = self._make_key(host, commands, encoding)
            r.setex(key, ttl, json.dumps(data, default=str))
        except Exception:
            logger.warning("Redis cache put failed", exc_info=True)

    def invalidate_host(self, host: str) -> int:
        """Clear all cache entries for a device using SCAN."""
        try:
            r = self._get_redis()
            count = 0
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{self._key_prefix}:{host}:*", count=100)
                if keys:
                    count += r.delete(*keys)
                if cursor == 0:
                    break
            return count
        except Exception:
            logger.warning("Redis cache invalidate_host failed for %s", host, exc_info=True)
            return 0

    def clear(self) -> int:
        """Remove all cached entries."""
        try:
            r = self._get_redis()
            count = 0
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{self._key_prefix}:*", count=100)
                if keys:
                    count += r.delete(*keys)
                if cursor == 0:
                    break
            self._hits = 0
            self._misses = 0
            return count
        except Exception:
            logger.warning("Redis cache clear failed", exc_info=True)
            return 0

    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        entry_count = 0
        try:
            r = self._get_redis()
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{self._key_prefix}:*", count=100)
                entry_count += len(keys)
                if cursor == 0:
                    break
        except Exception:
            logger.debug("Redis cache stats scan failed", exc_info=True)
        return {
            "enabled": self._enabled,
            "backend": "redis",
            "entries": entry_count,
            "max_entries": self._max_entries,
            "memory_mb": 0.0,  # Redis manages memory externally
            "max_memory_mb": self._max_memory_mb,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
        }
