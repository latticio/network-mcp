"""Session management for authenticated MCP users.

Tracks active user sessions with idle timeout, per-user session limits,
and cleanup of expired sessions. In-memory by default; optional Redis
backing when NET_DISTRIBUTED_BACKEND=redis.
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("network-mcp.session")


@dataclass
class SessionInfo:
    """Metadata for an active user session."""

    session_id: str
    user_id: str
    tenant_id: str | None
    created_at: float
    last_active: float
    max_idle_seconds: int = 3600
    metadata: dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """Check if the session has exceeded its idle timeout."""
        return (time.monotonic() - self.last_active) > self.max_idle_seconds


class SessionManager:
    """In-memory session store with idle expiration and per-user limits.

    Thread-safe: all mutations are protected by a lock.
    """

    def __init__(self, max_idle_seconds: int = 3600, max_per_user: int = 10):
        self._max_idle = max_idle_seconds
        self._max_per_user = max_per_user
        self._sessions: dict[str, SessionInfo] = {}
        self._user_sessions: dict[str, list[str]] = {}  # user_id -> [session_ids]
        self._lock = threading.Lock()

    def create_session(
        self,
        user_id: str,
        tenant_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Create a new session and return its ID.

        If the user already has max_per_user sessions, the oldest session
        is expired automatically to make room.

        Args:
            user_id: The authenticated user identifier.
            tenant_id: Optional tenant identifier.
            metadata: Optional metadata dict to attach to the session.

        Returns:
            The newly created session ID (UUID4).
        """
        session_id = str(uuid.uuid4())
        now = time.monotonic()
        session = SessionInfo(
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=now,
            last_active=now,
            max_idle_seconds=self._max_idle,
            metadata=metadata or {},
        )

        with self._lock:
            # Enforce per-user limit by expiring oldest sessions
            user_sids = self._user_sessions.get(user_id, [])
            # Clean up any already-expired sessions for this user first
            active_sids = [sid for sid in user_sids if sid in self._sessions and not self._sessions[sid].is_expired]
            while len(active_sids) >= self._max_per_user:
                oldest_sid = active_sids.pop(0)
                self._sessions.pop(oldest_sid, None)
                logger.debug("Evicted oldest session %s for user %s (max %d)", oldest_sid, user_id, self._max_per_user)

            self._sessions[session_id] = session
            active_sids.append(session_id)
            self._user_sessions[user_id] = active_sids

        logger.debug("Created session %s for user=%s tenant=%s", session_id, user_id, tenant_id)
        return session_id

    def validate_session(self, session_id: str) -> SessionInfo | None:
        """Look up a session and return its info if valid and not expired.

        Does NOT touch the session (use touch_session() to update last_active).

        Returns:
            SessionInfo if the session exists and is not expired, None otherwise.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.is_expired:
                self._remove_session_locked(session_id)
                return None
            return session

    def touch_session(self, session_id: str) -> None:
        """Update the last_active timestamp for a session.

        No-op if the session does not exist or is expired.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if session.is_expired:
                self._remove_session_locked(session_id)
                return
            session.last_active = time.monotonic()

    def expire_session(self, session_id: str) -> None:
        """Explicitly expire and remove a session."""
        with self._lock:
            self._remove_session_locked(session_id)

    def get_active_sessions(self, user_id: str) -> list[SessionInfo]:
        """Return all active (non-expired) sessions for a user."""
        with self._lock:
            sids = self._user_sessions.get(user_id, [])
            active: list[SessionInfo] = []
            expired: list[str] = []
            for sid in sids:
                session = self._sessions.get(sid)
                if session is None:
                    expired.append(sid)
                elif session.is_expired:
                    expired.append(sid)
                    self._sessions.pop(sid, None)
                else:
                    active.append(session)
            # Clean up index
            if expired:
                self._user_sessions[user_id] = [s.session_id for s in active]
            return active

    def cleanup_expired(self) -> int:
        """Remove all expired sessions across all users.

        Returns:
            Number of sessions removed.
        """
        with self._lock:
            expired_ids = [sid for sid, s in self._sessions.items() if s.is_expired]
            for sid in expired_ids:
                self._remove_session_locked(sid)
            if expired_ids:
                logger.debug("Cleaned up %d expired sessions", len(expired_ids))
            return len(expired_ids)

    @property
    def total_sessions(self) -> int:
        """Return the total number of tracked sessions (including potentially expired)."""
        with self._lock:
            return len(self._sessions)

    def _remove_session_locked(self, session_id: str) -> None:
        """Remove a session from both the main dict and the user index. Must hold self._lock."""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            user_sids = self._user_sessions.get(session.user_id, [])
            try:
                user_sids.remove(session_id)
            except ValueError:
                pass
            if not user_sids:
                self._user_sessions.pop(session.user_id, None)
