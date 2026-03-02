"""Tests for session management."""

import threading
import time

from network_mcp.session_mgr import SessionInfo, SessionManager


class TestSessionInfo:
    def test_not_expired_when_recent(self):
        info = SessionInfo(
            session_id="s1",
            user_id="u1",
            tenant_id=None,
            created_at=time.monotonic(),
            last_active=time.monotonic(),
            max_idle_seconds=3600,
        )
        assert not info.is_expired

    def test_expired_when_idle_exceeded(self):
        old = time.monotonic() - 7200
        info = SessionInfo(
            session_id="s1",
            user_id="u1",
            tenant_id=None,
            created_at=old,
            last_active=old,
            max_idle_seconds=3600,
        )
        assert info.is_expired


class TestSessionManager:
    def test_create_session_returns_uuid(self):
        mgr = SessionManager(max_idle_seconds=3600, max_per_user=10)
        sid = mgr.create_session("user-1")
        assert isinstance(sid, str)
        assert len(sid) == 36  # UUID4 format

    def test_create_session_with_metadata(self):
        mgr = SessionManager()
        sid = mgr.create_session("user-1", tenant_id="acme", metadata={"source": "api"})
        session = mgr.validate_session(sid)
        assert session is not None
        assert session.user_id == "user-1"
        assert session.tenant_id == "acme"
        assert session.metadata == {"source": "api"}

    def test_validate_existing_session(self):
        mgr = SessionManager()
        sid = mgr.create_session("user-1")
        session = mgr.validate_session(sid)
        assert session is not None
        assert session.session_id == sid
        assert session.user_id == "user-1"

    def test_validate_nonexistent_session(self):
        mgr = SessionManager()
        assert mgr.validate_session("nonexistent-id") is None

    def test_validate_expired_session_returns_none(self):
        mgr = SessionManager(max_idle_seconds=1)
        sid = mgr.create_session("user-1")
        # Manually backdate last_active
        with mgr._lock:
            mgr._sessions[sid].last_active = time.monotonic() - 10
        assert mgr.validate_session(sid) is None
        # Session should be cleaned up
        assert mgr.total_sessions == 0

    def test_touch_updates_last_active(self):
        mgr = SessionManager()
        sid = mgr.create_session("user-1")
        session_before = mgr.validate_session(sid)
        assert session_before is not None
        original_active = session_before.last_active

        time.sleep(0.01)
        mgr.touch_session(sid)

        session_after = mgr.validate_session(sid)
        assert session_after is not None
        assert session_after.last_active > original_active

    def test_touch_nonexistent_session_is_noop(self):
        mgr = SessionManager()
        mgr.touch_session("nonexistent")  # should not raise
        assert mgr.total_sessions == 0

    def test_touch_expired_session_removes_it(self):
        mgr = SessionManager(max_idle_seconds=1)
        sid = mgr.create_session("user-1")
        with mgr._lock:
            mgr._sessions[sid].last_active = time.monotonic() - 10
        mgr.touch_session(sid)
        assert mgr.total_sessions == 0

    def test_expire_session(self):
        mgr = SessionManager()
        sid = mgr.create_session("user-1")
        assert mgr.validate_session(sid) is not None
        mgr.expire_session(sid)
        assert mgr.validate_session(sid) is None

    def test_expire_nonexistent_session_is_noop(self):
        mgr = SessionManager()
        mgr.expire_session("nonexistent")  # should not raise
        assert mgr.total_sessions == 0

    def test_get_active_sessions(self):
        mgr = SessionManager()
        sid1 = mgr.create_session("user-1")
        sid2 = mgr.create_session("user-1")
        mgr.create_session("user-2")  # different user

        active = mgr.get_active_sessions("user-1")
        assert len(active) == 2
        assert {s.session_id for s in active} == {sid1, sid2}

    def test_get_active_sessions_filters_expired(self):
        mgr = SessionManager(max_idle_seconds=1)
        sid1 = mgr.create_session("user-1")
        sid2 = mgr.create_session("user-1")
        # Expire sid1
        with mgr._lock:
            mgr._sessions[sid1].last_active = time.monotonic() - 10
        active = mgr.get_active_sessions("user-1")
        assert len(active) == 1
        assert active[0].session_id == sid2

    def test_get_active_sessions_empty_for_unknown_user(self):
        mgr = SessionManager()
        assert mgr.get_active_sessions("unknown") == []

    def test_max_per_user_evicts_oldest(self):
        mgr = SessionManager(max_per_user=2)
        sid1 = mgr.create_session("user-1")
        sid2 = mgr.create_session("user-1")
        sid3 = mgr.create_session("user-1")
        # sid1 should have been evicted
        assert mgr.validate_session(sid1) is None
        assert mgr.validate_session(sid2) is not None
        assert mgr.validate_session(sid3) is not None
        active = mgr.get_active_sessions("user-1")
        assert len(active) == 2

    def test_cleanup_expired(self):
        mgr = SessionManager(max_idle_seconds=1)
        mgr.create_session("user-1")
        mgr.create_session("user-2")
        # Backdate all sessions
        with mgr._lock:
            for s in mgr._sessions.values():
                s.last_active = time.monotonic() - 10
        removed = mgr.cleanup_expired()
        assert removed == 2
        assert mgr.total_sessions == 0

    def test_cleanup_expired_keeps_active(self):
        mgr = SessionManager(max_idle_seconds=3600)
        sid1 = mgr.create_session("user-1")
        removed = mgr.cleanup_expired()
        assert removed == 0
        assert mgr.validate_session(sid1) is not None

    def test_total_sessions(self):
        mgr = SessionManager()
        assert mgr.total_sessions == 0
        mgr.create_session("user-1")
        assert mgr.total_sessions == 1
        mgr.create_session("user-2")
        assert mgr.total_sessions == 2

    def test_concurrent_session_creation(self):
        mgr = SessionManager(max_per_user=100)
        sids = []
        lock = threading.Lock()

        def worker():
            sid = mgr.create_session("user-1")
            with lock:
                sids.append(sid)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(sids) == 20
        # All should be unique
        assert len(set(sids)) == 20
        # All should be valid
        active = mgr.get_active_sessions("user-1")
        assert len(active) == 20

    def test_expire_removes_from_user_index(self):
        mgr = SessionManager()
        sid = mgr.create_session("user-1")
        mgr.expire_session(sid)
        assert mgr.get_active_sessions("user-1") == []
        assert "user-1" not in mgr._user_sessions
