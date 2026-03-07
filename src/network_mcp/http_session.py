"""HTTP session state persistence for MCP transport session resumption.

Stores tool call history per session so clients can reconnect after
disconnection and retrieve context from their previous session.
In-memory only (no Redis) — session state is lost on server restart.
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("network-mcp.http-session")


@dataclass
class ToolCallRecord:
    """Record of a single tool call within a session."""

    tool_name: str
    arguments: dict
    result_summary: str
    timestamp: float
    duration_ms: float = 0.0
    status: str = "success"

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result_summary": self.result_summary,
            "timestamp": self.timestamp,
            "duration_ms": round(self.duration_ms, 1),
            "status": self.status,
        }


@dataclass
class HTTPSessionState:
    """Persistent state for an HTTP transport session."""

    session_id: str
    created_at: float
    last_active: float
    ttl_seconds: int = 3600
    tool_history: list[ToolCallRecord] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """Check if the session has exceeded its TTL."""
        return (time.monotonic() - self.last_active) > self.ttl_seconds

    def to_dict(self) -> dict:
        """Serialize session state for API responses."""
        return {
            "session_id": self.session_id,
            "tool_call_count": len(self.tool_history),
            "is_expired": self.is_expired,
            "metadata": self.metadata,
        }


class HTTPSessionStore:
    """In-memory store for HTTP session state with TTL expiration.

    Thread-safe: all mutations are protected by a lock.
    """

    def __init__(
        self,
        ttl_seconds: int = 3600,
        max_sessions: int = 1000,
        max_history_per_session: int = 100,
    ):
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._max_history = max_history_per_session
        self._sessions: dict[str, HTTPSessionState] = {}
        self._lock = threading.Lock()

    def create_session(self, session_id: str | None = None, metadata: dict | None = None) -> str:
        """Create a new session and return its ID.

        Args:
            session_id: Optional explicit session ID. If None, a UUID4 is generated.
            metadata: Optional metadata to attach to the session.

        Returns:
            The session ID.
        """
        if session_id is None:
            session_id = str(uuid.uuid4())
        now = time.monotonic()
        state = HTTPSessionState(
            session_id=session_id,
            created_at=now,
            last_active=now,
            ttl_seconds=self._ttl,
            metadata=metadata or {},
        )
        with self._lock:
            if len(self._sessions) >= self._max_sessions:
                self._evict_oldest_locked()
            self._sessions[session_id] = state
        logger.debug("Created HTTP session %s", session_id)
        return session_id

    def get_session(self, session_id: str) -> HTTPSessionState | None:
        """Get a session by ID, touching its last_active timestamp.

        Returns None if the session doesn't exist or is expired.
        """
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return None
            if state.is_expired:
                del self._sessions[session_id]
                logger.debug("Session %s expired on access", session_id)
                return None
            state.last_active = time.monotonic()
            return state

    def record_tool_call(self, session_id: str, record: ToolCallRecord) -> bool:
        """Append a tool call record to a session's history.

        Returns True if the record was added, False if session not found/expired.
        """
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return False
            if state.is_expired:
                del self._sessions[session_id]
                return False
            state.last_active = time.monotonic()
            state.tool_history.append(record)
            if len(state.tool_history) > self._max_history:
                state.tool_history = state.tool_history[-self._max_history:]
            return True

    def get_history(self, session_id: str) -> list[ToolCallRecord]:
        """Get the tool call history for a session."""
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return []
            if state.is_expired:
                del self._sessions[session_id]
                return []
            state.last_active = time.monotonic()
            return list(state.tool_history)

    def record_to_most_recent(self, record: ToolCallRecord) -> bool:
        """Record a tool call into the most recently active non-expired session.

        Thread-safe: acquires the internal lock before iterating sessions.
        Returns True if a session was found and the record was added.
        """
        with self._lock:
            best: HTTPSessionState | None = None
            for state in self._sessions.values():
                if not state.is_expired:
                    if best is None or state.last_active > best.last_active:
                        best = state
            if best is None:
                return False
            best.last_active = time.monotonic()
            best.tool_history.append(record)
            if len(best.tool_history) > self._max_history:
                best.tool_history = best.tool_history[-self._max_history:]
            return True

    def remove_session(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[dict]:
        """List all active (non-expired) sessions."""
        with self._lock:
            self._cleanup_expired_locked()
            return [s.to_dict() for s in self._sessions.values()]

    def cleanup_expired(self) -> int:
        """Remove all expired sessions. Returns count of removed sessions."""
        with self._lock:
            return self._cleanup_expired_locked()

    @property
    def session_count(self) -> int:
        """Return the number of tracked sessions (may include expired)."""
        with self._lock:
            return len(self._sessions)

    def _cleanup_expired_locked(self) -> int:
        """Remove expired sessions while holding the lock."""
        expired = [sid for sid, s in self._sessions.items() if s.is_expired]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.debug("Cleaned up %d expired HTTP sessions", len(expired))
        return len(expired)

    def _evict_oldest_locked(self) -> None:
        """Evict the oldest session to make room for a new one."""
        if not self._sessions:
            return
        oldest_id = min(self._sessions, key=lambda sid: self._sessions[sid].last_active)
        del self._sessions[oldest_id]
        logger.debug("Evicted oldest HTTP session %s (max %d)", oldest_id, self._max_sessions)
