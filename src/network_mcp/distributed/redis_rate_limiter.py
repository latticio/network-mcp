"""Redis-backed distributed token bucket rate limiter.

Uses fixed-window counters via Redis INCR with TTL for atomic, distributed
rate limiting. Each server instance shares the same rate limit counters so
that the aggregate request rate across all instances stays within bounds.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("network-mcp")


class RedisRateLimiter:
    """Distributed per-device rate limiter backed by Redis.

    Implements the same public interface as DeviceRateLimiter so it can be
    used as a drop-in replacement via the factory.

    Uses a fixed-window counter per host per command type. The window is 1 second,
    matching the token bucket's refill rate in the local implementation.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        show_rate: float = 5.0,
        show_burst: float = 10.0,
        config_rate: float = 1.0,
        config_burst: float = 2.0,
        key_prefix: str = "netmcp:ratelimit",
    ):
        self._redis_url = redis_url
        self._show_rate = show_rate
        self._show_burst = show_burst
        self._config_rate = config_rate
        self._config_burst = config_burst
        self._key_prefix = key_prefix
        self._redis = None

    def _get_redis(self):
        """Lazy Redis connection — avoids import-time connection."""
        if self._redis is None:
            import redis

            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _acquire(self, host: str, command_type: str) -> bool:
        """Attempt to acquire a rate limit token via Redis fixed-window counter."""
        r = self._get_redis()
        key = f"{self._key_prefix}:{host}:{command_type}"
        rate = self._show_rate if command_type == "show" else self._config_rate

        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 1)
        results = pipe.execute()
        count = results[0]

        return count <= rate

    def check_show(self, host: str) -> bool:
        """Check if a show command is allowed for this device."""
        try:
            return self._acquire(host, "show")
        except Exception:
            logger.warning("Redis rate limiter unavailable, allowing request (fail-open)", exc_info=True)
            return True

    def check_config(self, host: str) -> bool:
        """Check if a config command is allowed for this device."""
        try:
            return self._acquire(host, "config")
        except Exception:
            logger.warning("Redis rate limiter unavailable, allowing request (fail-open)", exc_info=True)
            return True

    def show_wait_time(self, host: str) -> float:
        """Return seconds to wait before next show token is available."""
        try:
            r = self._get_redis()
            key = f"{self._key_prefix}:{host}:show"
            ttl = r.ttl(key)
            if ttl > 0:
                return float(ttl)
        except Exception:
            logger.debug("Redis rate limiter show_wait_time failed for %s", host, exc_info=True)
        return 1.0

    def config_wait_time(self, host: str) -> float:
        """Return seconds to wait before next config token is available."""
        try:
            r = self._get_redis()
            key = f"{self._key_prefix}:{host}:config"
            ttl = r.ttl(key)
            if ttl > 0:
                return float(ttl)
        except Exception:
            logger.debug("Redis rate limiter config_wait_time failed for %s", host, exc_info=True)
        return 1.0

    def reset(self, host: str = "") -> None:
        """Reset rate limiter state in Redis."""
        try:
            r = self._get_redis()
            if host:
                for cmd_type in ("show", "config"):
                    r.delete(f"{self._key_prefix}:{host}:{cmd_type}")
            else:
                # SCAN-based deletion to avoid blocking with KEYS
                cursor = 0
                while True:
                    cursor, keys = r.scan(cursor, match=f"{self._key_prefix}:*", count=100)
                    if keys:
                        r.delete(*keys)
                    if cursor == 0:
                        break
        except Exception:
            logger.warning("Redis rate limiter reset failed", exc_info=True)
