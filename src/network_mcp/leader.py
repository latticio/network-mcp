"""Redis-based leader election for multi-replica deployments.

Uses Redis SETNX (SET ... NX EX) to acquire a lease. Only the leader
runs expensive periodic operations (health probes, startup verification).
Non-leaders still respond to all health endpoints normally.

Requires NET_LEADER_ELECTION=true and NET_DISTRIBUTED_BACKEND=redis.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time

logger = logging.getLogger("network-mcp")

# Redis key prefix for leader election
_LEADER_KEY = "network-mcp:leader"


def _generate_instance_id() -> str:
    """Generate a unique instance ID from hostname + PID."""
    hostname = platform.node() or "unknown"
    pid = os.getpid()
    return f"{hostname}:{pid}"


class LeaderElector:
    """Redis-based leader election using SETNX lease.

    Acquires leadership by setting a Redis key with NX (only if not exists)
    and EX (expiry in seconds). The leader must renew the lease periodically
    (at TTL/3 intervals) to maintain leadership.

    If the lease expires (e.g., Redis is down), the instance automatically
    demotes itself.

    Args:
        redis_url: Redis connection URL.
        ttl: Lease TTL in seconds (default 30).
        instance_id: Unique identifier for this instance. Auto-generated
                     from hostname + PID if not provided.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        ttl: int = 30,
        instance_id: str | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._ttl = ttl
        self._instance_id = instance_id or _generate_instance_id()
        self._is_leader = False
        self._lease_acquired_at: float | None = None
        self._lock = threading.Lock()
        self._redis = None
        self._renewal_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def instance_id(self) -> str:
        """Return this instance's unique identifier."""
        return self._instance_id

    def _get_redis(self):
        """Lazy Redis connection."""
        if self._redis is None:
            import redis

            self._redis = redis.from_url(self._redis_url, decode_responses=True, socket_timeout=5)
        return self._redis

    def try_acquire(self) -> bool:
        """Attempt to acquire the leader lease.

        Uses Redis SET with NX (only if not exists) and EX (expiry).

        Returns:
            True if this instance is now the leader, False otherwise.
        """
        try:
            client = self._get_redis()
            result = client.set(_LEADER_KEY, self._instance_id, nx=True, ex=self._ttl)
            with self._lock:
                if result:
                    self._is_leader = True
                    self._lease_acquired_at = time.monotonic()
                    logger.info("Leader election: acquired lease (instance=%s, ttl=%ds)", self._instance_id, self._ttl)
                else:
                    # Check if we already hold the lease (re-acquire after restart)
                    current = client.get(_LEADER_KEY)
                    if current == self._instance_id:
                        self._is_leader = True
                        self._lease_acquired_at = time.monotonic()
                    else:
                        self._is_leader = False
                return self._is_leader
        except Exception as e:
            logger.warning("Leader election: failed to acquire lease: %s", e)
            with self._lock:
                self._is_leader = False
            return False

    def renew(self) -> bool:
        """Renew the leader lease by resetting the TTL.

        Only succeeds if this instance currently holds the lease.

        Returns:
            True if renewal succeeded, False if lease lost.
        """
        if not self._is_leader:
            return False

        try:
            client = self._get_redis()
            # Verify we still own the lease before renewing
            current = client.get(_LEADER_KEY)
            if current != self._instance_id:
                with self._lock:
                    self._is_leader = False
                    self._lease_acquired_at = None
                logger.warning("Leader election: lease lost to another instance (current=%s)", current)
                return False

            # Refresh the TTL
            client.expire(_LEADER_KEY, self._ttl)
            with self._lock:
                self._lease_acquired_at = time.monotonic()
            return True
        except Exception as e:
            logger.warning("Leader election: failed to renew lease: %s", e)
            # Don't immediately demote — wait until TTL expires
            with self._lock:
                if self._lease_acquired_at is not None:
                    elapsed = time.monotonic() - self._lease_acquired_at
                    if elapsed > self._ttl:
                        self._is_leader = False
                        self._lease_acquired_at = None
                        logger.warning("Leader election: lease expired after renewal failure, demoting")
            return False

    def release(self) -> bool:
        """Release the leader lease.

        Only deletes the key if this instance holds it (to prevent
        releasing another instance's lease).

        Returns:
            True if the lease was successfully released, False otherwise.
        """
        try:
            client = self._get_redis()
            current = client.get(_LEADER_KEY)
            if current == self._instance_id:
                client.delete(_LEADER_KEY)
                with self._lock:
                    self._is_leader = False
                    self._lease_acquired_at = None
                logger.info("Leader election: released lease (instance=%s)", self._instance_id)
                return True
            with self._lock:
                self._is_leader = False
                self._lease_acquired_at = None
            return False
        except Exception as e:
            logger.warning("Leader election: failed to release lease: %s", e)
            with self._lock:
                self._is_leader = False
                self._lease_acquired_at = None
            return False

    def is_leader(self) -> bool:
        """Check if this instance is currently the leader.

        Returns:
            True if this instance holds the leader lease.
        """
        with self._lock:
            # Check if lease has expired locally
            if self._is_leader and self._lease_acquired_at is not None:
                elapsed = time.monotonic() - self._lease_acquired_at
                if elapsed > self._ttl:
                    self._is_leader = False
                    self._lease_acquired_at = None
                    logger.warning("Leader election: lease expired locally, demoting")
            return self._is_leader

    def _renewal_loop(self) -> None:
        """Background thread that renews the lease at TTL/3 intervals."""
        interval = max(self._ttl // 3, 1)
        while not self._stop_event.is_set():
            if self._is_leader:
                self.renew()
            self._stop_event.wait(interval)

    def start_renewal(self) -> None:
        """Start the background lease renewal thread.

        Acquires the lease if not already held, then starts a daemon thread
        that renews it at TTL/3 intervals.
        """
        if not self._is_leader:
            self.try_acquire()

        self._stop_event.clear()
        self._renewal_thread = threading.Thread(
            target=self._renewal_loop,
            name="leader-renewal",
            daemon=True,
        )
        self._renewal_thread.start()
        logger.info("Leader election: renewal thread started (interval=%ds)", max(self._ttl // 3, 1))

    def stop_renewal(self) -> None:
        """Stop the background lease renewal thread and release the lease."""
        self._stop_event.set()
        if self._renewal_thread is not None:
            self._renewal_thread.join(timeout=5)
            self._renewal_thread = None
        self.release()
        logger.info("Leader election: renewal thread stopped")

    def status(self) -> dict:
        """Return leader election status for health endpoint consumption.

        Returns:
            Dict with leader election state information.
        """
        with self._lock:
            return {
                "enabled": True,
                "instance_id": self._instance_id,
                "is_leader": self._is_leader,
                "lease_ttl": self._ttl,
                "lease_acquired_at": self._lease_acquired_at,
            }
