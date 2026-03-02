"""Redis-backed distributed circuit breaker.

Shares circuit breaker state across all server instances using Redis hashes.
Each device gets a hash with fields: state, failures, last_failure, cooldown, threshold.

State machine matches the local implementation:
    HEALTHY → CIRCUIT_OPEN after N consecutive failures
    CIRCUIT_OPEN → HALF_OPEN after cooldown period
    HALF_OPEN → HEALTHY on success
    HALF_OPEN → CIRCUIT_OPEN on failure (doubles cooldown)
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger("network-mcp")

_MAX_COOLDOWN = 300  # Cap exponential backoff at 5 minutes


class RedisCircuitBreakerRegistry:
    """Distributed circuit breaker registry backed by Redis.

    Implements the same public interface as CircuitBreakerRegistry so it can be
    used as a drop-in replacement via the factory.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        threshold: int = 3,
        cooldown: int = 60,
        enabled: bool = False,
        key_prefix: str = "netmcp:circuit",
    ):
        self._redis_url = redis_url
        self._threshold = threshold
        self._base_cooldown = cooldown
        self._enabled = enabled
        self._key_prefix = key_prefix
        self._redis = None

    def _get_redis(self):
        """Lazy Redis connection."""
        if self._redis is None:
            import redis

            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _key(self, host: str) -> str:
        return f"{self._key_prefix}:{host}"

    def _get_state(self, host: str) -> dict:
        """Get circuit breaker state from Redis, initializing if missing."""
        try:
            r = self._get_redis()
            data = r.get(self._key(host))
            if data:
                return json.loads(data)
        except Exception:
            logger.warning("Redis circuit breaker read failed for %s", host, exc_info=True)
        return {
            "state": "healthy",
            "failures": 0,
            "last_failure": 0.0,
            "cooldown": self._base_cooldown,
            "threshold": self._threshold,
        }

    def _set_state(self, host: str, state: dict) -> None:
        """Write circuit breaker state to Redis."""
        try:
            r = self._get_redis()
            r.set(self._key(host), json.dumps(state))
        except Exception:
            logger.warning("Redis circuit breaker write failed for %s", host, exc_info=True)

    def _check_transition(self, state: dict) -> dict:
        """Check if OPEN → HALF_OPEN transition should happen based on cooldown."""
        if state["state"] == "open":
            elapsed = time.time() - state["last_failure"]
            if elapsed >= state["cooldown"]:
                state["state"] = "half_open"
        return state

    def allow_request(self, host: str) -> bool:
        """Check if a request to the given host is allowed.

        When the registry is disabled, always returns True.
        Fails open if Redis is unavailable.
        """
        if not self._enabled:
            return True
        try:
            state = self._get_state(host)
            state = self._check_transition(state)
            self._set_state(host, state)
            return state["state"] != "open"
        except Exception:
            logger.warning("Redis circuit breaker check failed, allowing request (fail-open)", exc_info=True)
            return True

    def record_success(self, host: str) -> None:
        """Record a success — reset to HEALTHY."""
        if not self._enabled:
            return
        try:
            state = self._get_state(host)
            state["state"] = "healthy"
            state["failures"] = 0
            state["cooldown"] = self._base_cooldown
            self._set_state(host, state)
        except Exception:
            logger.warning("Redis circuit breaker record_success failed for %s", host, exc_info=True)

    def record_failure(self, host: str) -> None:
        """Record a failure — increment failure count and potentially open the circuit."""
        if not self._enabled:
            return
        try:
            state = self._get_state(host)
            state = self._check_transition(state)
            state["failures"] += 1
            state["last_failure"] = time.time()

            if state["state"] == "half_open":
                state["cooldown"] = min(state["cooldown"] * 2, _MAX_COOLDOWN)
                state["state"] = "open"
            elif state["failures"] >= state["threshold"]:
                state["state"] = "open"

            self._set_state(host, state)
        except Exception:
            logger.warning("Redis circuit breaker record_failure failed for %s", host, exc_info=True)

    def get(self, host: str):
        """Get a breaker-like object for a host (for compatibility with local API)."""
        return _RedisCircuitBreakerView(self, host)

    def get_open_circuits(self) -> list[str]:
        """Return list of hosts with open circuits."""
        try:
            r = self._get_redis()
            result = []
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{self._key_prefix}:*", count=100)
                for key in keys:
                    data = r.get(key)
                    if data:
                        state = json.loads(data)
                        state = self._check_transition(state)
                        if state["state"] == "open":
                            host = key.removeprefix(f"{self._key_prefix}:")
                            result.append(host)
                if cursor == 0:
                    break
            return result
        except Exception:
            logger.warning("Redis circuit breaker get_open_circuits failed", exc_info=True)
            return []

    def stats(self) -> dict:
        """Return registry stats for observability."""
        breakers = {}
        try:
            r = self._get_redis()
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{self._key_prefix}:*", count=100)
                for key in keys:
                    data = r.get(key)
                    if data:
                        state = json.loads(data)
                        state = self._check_transition(state)
                        host = key.removeprefix(f"{self._key_prefix}:")
                        breakers[host] = {
                            "host": host,
                            "state": state["state"],
                            "consecutive_failures": state["failures"],
                            "threshold": state["threshold"],
                            "cooldown_seconds": state["cooldown"],
                        }
                if cursor == 0:
                    break
        except Exception:
            logger.warning("Redis circuit breaker stats failed", exc_info=True)

        open_circuits = [h for h, b in breakers.items() if b["state"] == "open"]
        return {
            "enabled": self._enabled,
            "total_breakers": len(breakers),
            "open_circuits": open_circuits,
            "breakers": breakers,
        }

    def reset(self, host: str | None = None) -> None:
        """Reset breaker(s). If host is None, reset all."""
        try:
            r = self._get_redis()
            if host is not None:
                r.delete(self._key(host))
            else:
                cursor = 0
                while True:
                    cursor, keys = r.scan(cursor, match=f"{self._key_prefix}:*", count=100)
                    if keys:
                        r.delete(*keys)
                    if cursor == 0:
                        break
        except Exception:
            logger.warning("Redis circuit breaker reset failed", exc_info=True)


class _RedisCircuitBreakerView:
    """Lightweight view object to provide .consecutive_failures for compatibility."""

    def __init__(self, registry: RedisCircuitBreakerRegistry, host: str):
        self._registry = registry
        self._host = host

    @property
    def consecutive_failures(self) -> int:
        state = self._registry._get_state(self._host)
        return state["failures"]

    @property
    def host(self) -> str:
        return self._host

    def allow_request(self) -> bool:
        return self._registry.allow_request(self._host)

    def record_success(self) -> None:
        self._registry.record_success(self._host)

    def record_failure(self) -> None:
        self._registry.record_failure(self._host)

    def to_dict(self) -> dict:
        state = self._registry._get_state(self._host)
        state = self._registry._check_transition(state)
        return {
            "host": self._host,
            "state": state["state"],
            "consecutive_failures": state["failures"],
            "threshold": state["threshold"],
            "cooldown_seconds": state["cooldown"],
        }
