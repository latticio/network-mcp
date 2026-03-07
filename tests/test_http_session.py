"""Tests for HTTP session state persistence and session management tools."""

import threading
import time

from network_mcp.http_session import HTTPSessionState, HTTPSessionStore, ToolCallRecord


class TestToolCallRecord:
    def test_creation(self):
        record = ToolCallRecord(
            tool_name="get_vlans",
            arguments={"host": "spine-01"},
            result_summary='{"status": "success"}',
            timestamp=time.time(),
            duration_ms=42.5,
            status="success",
        )
        assert record.tool_name == "get_vlans"
        assert record.status == "success"
        assert record.duration_ms == 42.5

    def test_to_dict(self):
        ts = time.time()
        record = ToolCallRecord(
            tool_name="get_vlans",
            arguments={"host": "spine-01"},
            result_summary="ok",
            timestamp=ts,
            duration_ms=42.567,
            status="success",
        )
        d = record.to_dict()
        assert d["tool_name"] == "get_vlans"
        assert d["arguments"] == {"host": "spine-01"}
        assert d["result_summary"] == "ok"
        assert d["timestamp"] == ts
        assert d["duration_ms"] == 42.6  # rounded to 1 decimal
        assert d["status"] == "success"

    def test_default_status(self):
        record = ToolCallRecord(
            tool_name="test",
            arguments={},
            result_summary="",
            timestamp=0.0,
        )
        assert record.status == "success"
        assert record.duration_ms == 0.0


class TestHTTPSessionState:
    def test_not_expired_when_recent(self):
        state = HTTPSessionState(
            session_id="s1",
            created_at=time.monotonic(),
            last_active=time.monotonic(),
            ttl_seconds=3600,
        )
        assert not state.is_expired

    def test_expired_when_ttl_exceeded(self):
        old = time.monotonic() - 7200
        state = HTTPSessionState(
            session_id="s1",
            created_at=old,
            last_active=old,
            ttl_seconds=3600,
        )
        assert state.is_expired

    def test_to_dict(self):
        state = HTTPSessionState(
            session_id="test-123",
            created_at=time.monotonic(),
            last_active=time.monotonic(),
            ttl_seconds=3600,
            metadata={"source": "test"},
        )
        d = state.to_dict()
        assert d["session_id"] == "test-123"
        assert d["tool_call_count"] == 0
        assert d["is_expired"] is False
        assert d["metadata"] == {"source": "test"}

    def test_to_dict_with_history(self):
        state = HTTPSessionState(
            session_id="s1",
            created_at=time.monotonic(),
            last_active=time.monotonic(),
        )
        state.tool_history.append(
            ToolCallRecord(tool_name="test", arguments={}, result_summary="", timestamp=0.0)
        )
        assert state.to_dict()["tool_call_count"] == 1


class TestHTTPSessionStore:
    def test_create_session_returns_uuid(self):
        store = HTTPSessionStore()
        sid = store.create_session()
        assert isinstance(sid, str)
        assert len(sid) == 36  # UUID4 format

    def test_create_session_with_explicit_id(self):
        store = HTTPSessionStore()
        sid = store.create_session(session_id="my-custom-id")
        assert sid == "my-custom-id"
        state = store.get_session("my-custom-id")
        assert state is not None
        assert state.session_id == "my-custom-id"

    def test_create_session_with_metadata(self):
        store = HTTPSessionStore()
        sid = store.create_session(metadata={"client": "cursor"})
        state = store.get_session(sid)
        assert state is not None
        assert state.metadata == {"client": "cursor"}

    def test_get_session_returns_none_for_missing(self):
        store = HTTPSessionStore()
        assert store.get_session("nonexistent") is None

    def test_get_session_touches_last_active(self):
        store = HTTPSessionStore()
        sid = store.create_session()
        state1 = store.get_session(sid)
        assert state1 is not None
        active1 = state1.last_active

        time.sleep(0.01)
        state2 = store.get_session(sid)
        assert state2 is not None
        assert state2.last_active > active1

    def test_get_session_returns_none_for_expired(self):
        store = HTTPSessionStore(ttl_seconds=1)
        sid = store.create_session()
        # Backdate last_active
        with store._lock:
            store._sessions[sid].last_active = time.monotonic() - 10
        assert store.get_session(sid) is None
        # Session should be cleaned up
        assert store.session_count == 0

    def test_record_tool_call(self):
        store = HTTPSessionStore()
        sid = store.create_session()
        record = ToolCallRecord(
            tool_name="get_vlans",
            arguments={"host": "spine-01"},
            result_summary="ok",
            timestamp=time.time(),
            duration_ms=10.0,
        )
        assert store.record_tool_call(sid, record) is True
        history = store.get_history(sid)
        assert len(history) == 1
        assert history[0].tool_name == "get_vlans"

    def test_record_tool_call_nonexistent_session(self):
        store = HTTPSessionStore()
        record = ToolCallRecord(
            tool_name="test",
            arguments={},
            result_summary="",
            timestamp=0.0,
        )
        assert store.record_tool_call("nonexistent", record) is False

    def test_record_tool_call_expired_session(self):
        store = HTTPSessionStore(ttl_seconds=1)
        sid = store.create_session()
        with store._lock:
            store._sessions[sid].last_active = time.monotonic() - 10
        record = ToolCallRecord(
            tool_name="test",
            arguments={},
            result_summary="",
            timestamp=0.0,
        )
        assert store.record_tool_call(sid, record) is False
        assert store.session_count == 0

    def test_record_trims_history(self):
        store = HTTPSessionStore(max_history_per_session=3)
        sid = store.create_session()
        for i in range(5):
            record = ToolCallRecord(
                tool_name=f"tool_{i}",
                arguments={},
                result_summary="",
                timestamp=float(i),
            )
            store.record_tool_call(sid, record)
        history = store.get_history(sid)
        assert len(history) == 3
        # Should keep the most recent 3
        assert history[0].tool_name == "tool_2"
        assert history[2].tool_name == "tool_4"

    def test_get_history_empty_for_missing(self):
        store = HTTPSessionStore()
        assert store.get_history("nonexistent") == []

    def test_get_history_empty_for_expired(self):
        store = HTTPSessionStore(ttl_seconds=1)
        sid = store.create_session()
        record = ToolCallRecord(
            tool_name="test",
            arguments={},
            result_summary="",
            timestamp=0.0,
        )
        store.record_tool_call(sid, record)
        with store._lock:
            store._sessions[sid].last_active = time.monotonic() - 10
        assert store.get_history(sid) == []

    def test_remove_session(self):
        store = HTTPSessionStore()
        sid = store.create_session()
        assert store.remove_session(sid) is True
        assert store.get_session(sid) is None
        assert store.session_count == 0

    def test_remove_nonexistent_session(self):
        store = HTTPSessionStore()
        assert store.remove_session("nonexistent") is False

    def test_list_sessions(self):
        store = HTTPSessionStore()
        sid1 = store.create_session(metadata={"id": 1})
        sid2 = store.create_session(metadata={"id": 2})
        sessions = store.list_sessions()
        assert len(sessions) == 2
        session_ids = {s["session_id"] for s in sessions}
        assert sid1 in session_ids
        assert sid2 in session_ids

    def test_list_sessions_filters_expired(self):
        store = HTTPSessionStore(ttl_seconds=1)
        store.create_session()
        sid2 = store.create_session()
        # Expire the first session
        with store._lock:
            first_sid = next(iter(store._sessions))
            store._sessions[first_sid].last_active = time.monotonic() - 10
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == sid2

    def test_cleanup_expired(self):
        store = HTTPSessionStore(ttl_seconds=1)
        store.create_session()
        store.create_session()
        with store._lock:
            for s in store._sessions.values():
                s.last_active = time.monotonic() - 10
        removed = store.cleanup_expired()
        assert removed == 2
        assert store.session_count == 0

    def test_cleanup_keeps_active(self):
        store = HTTPSessionStore(ttl_seconds=3600)
        sid = store.create_session()
        removed = store.cleanup_expired()
        assert removed == 0
        assert store.get_session(sid) is not None

    def test_session_count(self):
        store = HTTPSessionStore()
        assert store.session_count == 0
        store.create_session()
        assert store.session_count == 1
        store.create_session()
        assert store.session_count == 2

    def test_max_sessions_eviction(self):
        store = HTTPSessionStore(max_sessions=2)
        sid1 = store.create_session()
        sid2 = store.create_session()
        sid3 = store.create_session()
        # sid1 should have been evicted (oldest)
        assert store.get_session(sid1) is None
        assert store.get_session(sid2) is not None
        assert store.get_session(sid3) is not None
        assert store.session_count == 2

    def test_concurrent_session_creation(self):
        store = HTTPSessionStore(max_sessions=1000)
        sids = []
        lock = threading.Lock()

        def worker():
            sid = store.create_session()
            with lock:
                sids.append(sid)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(sids) == 20
        assert len(set(sids)) == 20  # all unique
        assert store.session_count == 20

    def test_concurrent_record_tool_calls(self):
        store = HTTPSessionStore()
        sid = store.create_session()

        def worker(i):
            record = ToolCallRecord(
                tool_name=f"tool_{i}",
                arguments={"i": i},
                result_summary="ok",
                timestamp=time.time(),
            )
            store.record_tool_call(sid, record)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        history = store.get_history(sid)
        assert len(history) == 10


class TestHTTPSessionTools:
    """Test the MCP tools for session management."""

    def test_net_session_create(self):
        from network_mcp.tools.http_sessions import net_session_create

        result = net_session_create()
        assert result["status"] == "success"
        assert "session_id" in result
        assert len(result["session_id"]) == 36  # UUID4

    def test_net_session_create_with_metadata(self):
        from network_mcp.tools.http_sessions import net_session_create

        result = net_session_create(metadata='{"client": "test"}')
        assert result["status"] == "success"
        assert "session_id" in result

    def test_net_session_create_with_invalid_json_metadata(self):
        from network_mcp.tools.http_sessions import net_session_create

        result = net_session_create(metadata="not-json")
        assert result["status"] == "success"
        # Should store as raw metadata

    def test_net_session_resume_valid(self):
        from network_mcp.tools.http_sessions import net_session_create, net_session_resume

        create_result = net_session_create()
        sid = create_result["session_id"]
        resume_result = net_session_resume(sid)
        assert resume_result["status"] == "success"
        assert resume_result["session_id"] == sid
        assert resume_result["tool_call_count"] == 0
        assert resume_result["history"] == []

    def test_net_session_resume_invalid(self):
        from network_mcp.tools.http_sessions import net_session_resume

        result = net_session_resume("nonexistent-id")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_net_session_status_specific(self):
        from network_mcp.tools.http_sessions import net_session_create, net_session_status

        create_result = net_session_create()
        sid = create_result["session_id"]
        status_result = net_session_status(sid)
        assert status_result["status"] == "success"
        assert status_result["session_id"] == sid

    def test_net_session_status_list_all(self):
        from network_mcp.tools.http_sessions import net_session_status

        result = net_session_status()
        assert result["status"] == "success"
        assert "total_sessions" in result
        assert "sessions" in result

    def test_net_session_end(self):
        from network_mcp.tools.http_sessions import net_session_create, net_session_end, net_session_resume

        create_result = net_session_create()
        sid = create_result["session_id"]
        end_result = net_session_end(sid)
        assert end_result["status"] == "success"
        # Should not be resumable anymore
        resume_result = net_session_resume(sid)
        assert resume_result["status"] == "error"

    def test_net_session_end_nonexistent(self):
        from network_mcp.tools.http_sessions import net_session_end

        result = net_session_end("nonexistent")
        assert result["status"] == "error"

    def test_session_resume_with_recorded_history(self):
        """Test that tool call history is properly returned on resume."""
        from network_mcp.server import http_session_store
        from network_mcp.tools.http_sessions import net_session_create, net_session_resume

        create_result = net_session_create()
        sid = create_result["session_id"]

        # Manually record a tool call (simulating what the call_tool wrapper does)
        record = ToolCallRecord(
            tool_name="get_vlans",
            arguments={"host": "spine-01"},
            result_summary='{"status": "success", "device": "spine-01"}',
            timestamp=time.time(),
            duration_ms=15.3,
            status="success",
        )
        http_session_store.record_tool_call(sid, record)

        resume_result = net_session_resume(sid)
        assert resume_result["status"] == "success"
        assert resume_result["tool_call_count"] == 1
        assert len(resume_result["history"]) == 1
        assert resume_result["history"][0]["tool_name"] == "get_vlans"
        assert resume_result["history"][0]["status"] == "success"
