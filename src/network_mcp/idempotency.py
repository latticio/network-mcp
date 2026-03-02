"""Idempotency key support for preventing duplicate write operations.

When a client provides an idempotency key, the first execution result is cached.
Subsequent requests with the same key return the cached result without re-executing.
"""

import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("network-mcp")


@dataclass
class IdempotencyEntry:
    """Stored result for an idempotency key."""

    key: str
    result: dict
    created_at: float
    ttl: int


class IdempotencyStore:
    """Thread-safe store for idempotency keys and their results.

    Keys expire after NET_IDEMPOTENCY_KEY_TTL seconds (default 3600 = 1 hour).
    """

    def __init__(self, ttl: int = 3600, max_entries: int = 10000):
        self._ttl = ttl
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._store: dict[str, IdempotencyEntry] = {}
        self._in_flight: dict[str, threading.Event] = {}

    def get(self, key: str) -> dict | None:
        """Look up a cached result by idempotency key.

        Returns the cached result dict if found and not expired, None otherwise.
        Expired entries are cleaned up on access.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if (time.monotonic() - entry.created_at) > entry.ttl:
                del self._store[key]
                return None
            return entry.result

    def acquire(self, key: str, timeout: float = 30.0) -> dict | None | bool:
        """Acquire the right to execute for an idempotency key.

        Returns:
            - dict: cached result (caller should NOT execute)
            - True: caller acquired the key and should execute
            - None: timed out waiting for another thread's result
        """
        with self._lock:
            # Check cache first
            entry = self._store.get(key)
            if entry is not None:
                if (time.monotonic() - entry.created_at) <= entry.ttl:
                    return entry.result
                del self._store[key]

            # Check if another thread is already executing
            event = self._in_flight.get(key)
            if event is None:
                # We're first — mark in-flight
                self._in_flight[key] = threading.Event()
                return True

        # Another thread is executing — wait for it
        if event.wait(timeout=timeout):
            return self.get(key)
        return None

    def complete(self, key: str, result: dict) -> None:
        """Mark an in-flight key as complete and store its result."""
        self.set(key, result)
        with self._lock:
            event = self._in_flight.pop(key, None)
        if event:
            event.set()

    def cancel(self, key: str) -> None:
        """Cancel an in-flight key (execution failed, no result to cache)."""
        with self._lock:
            event = self._in_flight.pop(key, None)
        if event:
            event.set()

    def set(self, key: str, result: dict) -> None:
        """Store a result for an idempotency key."""
        with self._lock:
            # Evict oldest entries if at capacity
            if len(self._store) >= self._max_entries:
                self._cleanup_expired()
            if len(self._store) >= self._max_entries:
                # Still full after cleanup — evict oldest
                oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
                del self._store[oldest_key]

            self._store[key] = IdempotencyEntry(
                key=key,
                result=result,
                created_at=time.monotonic(),
                ttl=self._ttl,
            )

    def _cleanup_expired(self) -> int:
        """Remove expired entries. Must be called under lock. Returns count removed."""
        now = time.monotonic()
        expired = [k for k, v in self._store.items() if (now - v.created_at) > v.ttl]
        for k in expired:
            del self._store[k]
        return len(expired)

    def reset(self) -> None:
        """Clear all entries and in-flight state. For testing only."""
        with self._lock:
            self._store.clear()
            # Wake any waiting threads before clearing
            for event in self._in_flight.values():
                event.set()
            self._in_flight.clear()


# Module-level singleton
_idempotency_store: IdempotencyStore | None = None


def get_idempotency_store() -> IdempotencyStore | None:
    """Return the module-level idempotency store singleton, or None if not initialized."""
    return _idempotency_store


def init_idempotency_store(ttl: int = 3600, max_entries: int = 10000) -> IdempotencyStore:
    """Initialize the module-level idempotency store singleton."""
    global _idempotency_store
    _idempotency_store = IdempotencyStore(ttl=ttl, max_entries=max_entries)
    return _idempotency_store
