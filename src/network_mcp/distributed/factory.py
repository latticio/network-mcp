"""Factory functions for creating local or Redis-backed state components."""

from __future__ import annotations

import logging

logger = logging.getLogger("network-mcp")


def create_rate_limiter(backend: str = "local", **kwargs):
    """Factory for rate limiter (local or Redis-backed).

    Args:
        backend: "local" or "redis".
        **kwargs: Forwarded to the chosen implementation.
            For redis: redis_url, show_rate, config_rate, show_burst, config_burst.
            For local: show_rate, show_burst, config_rate, config_burst.
    """
    if backend == "redis":
        from network_mcp.distributed.redis_rate_limiter import RedisRateLimiter

        logger.info("Using Redis-backed distributed rate limiter")
        return RedisRateLimiter(**kwargs)
    from network_mcp.rate_limiter import DeviceRateLimiter

    # Filter out redis_url if present (local doesn't need it)
    kwargs.pop("redis_url", None)
    return DeviceRateLimiter(**kwargs)


def create_circuit_breaker(backend: str = "local", **kwargs):
    """Factory for circuit breaker registry (local or Redis-backed).

    Args:
        backend: "local" or "redis".
        **kwargs: Forwarded to the chosen implementation.
            For redis: redis_url, threshold, cooldown, enabled.
            For local: threshold, cooldown, enabled.
    """
    if backend == "redis":
        from network_mcp.distributed.redis_circuit_breaker import RedisCircuitBreakerRegistry

        logger.info("Using Redis-backed distributed circuit breaker")
        return RedisCircuitBreakerRegistry(**kwargs)
    from network_mcp.circuit_breaker import CircuitBreakerRegistry

    kwargs.pop("redis_url", None)
    return CircuitBreakerRegistry(**kwargs)


def create_cache(backend: str = "local", **kwargs):
    """Factory for command cache (local or Redis-backed).

    Args:
        backend: "local" or "redis".
        **kwargs: Forwarded to the chosen implementation.
            For redis: redis_url, max_memory_mb, enabled.
            For local: max_entries, max_memory_mb, enabled.
    """
    if backend == "redis":
        from network_mcp.distributed.redis_cache import RedisCommandCache

        logger.info("Using Redis-backed distributed command cache")
        return RedisCommandCache(**kwargs)
    from network_mcp.cache import CommandCache

    kwargs.pop("redis_url", None)
    return CommandCache(**kwargs)
