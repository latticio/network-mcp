"""Distributed state backends for horizontal scaling.

When NET_DISTRIBUTED_BACKEND=redis, uses Redis for shared state across
multiple server instances. Falls back to local (in-memory) otherwise.
"""

from network_mcp.distributed.factory import create_cache, create_circuit_breaker, create_rate_limiter

__all__ = ["create_cache", "create_circuit_breaker", "create_rate_limiter"]
