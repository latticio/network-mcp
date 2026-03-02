"""Tests for request ID propagation and logging filter."""

import logging
import threading
import uuid

from network_mcp.middleware import (
    RequestIdFilter,
    clear_request_context,
    get_request_id,
    set_request_context,
)


class TestRequestIdPropagation:
    def test_set_request_context_returns_valid_uuid(self):
        try:
            request_id = set_request_context(user="admin", scopes=["network:read"])
            # Should be a valid UUID4 string
            parsed = uuid.UUID(request_id, version=4)
            assert str(parsed) == request_id
        finally:
            clear_request_context()

    def test_get_request_id_returns_set_value(self):
        try:
            request_id = set_request_context(user="admin", scopes=[])
            assert get_request_id() == request_id
        finally:
            clear_request_context()

    def test_get_request_id_returns_none_when_no_context(self):
        clear_request_context()
        assert get_request_id() is None

    def test_clear_request_context_clears_request_id(self):
        set_request_context(user="admin", scopes=[])
        assert get_request_id() is not None
        clear_request_context()
        assert get_request_id() is None

    def test_set_request_context_generates_unique_ids(self):
        try:
            id1 = set_request_context(user="admin", scopes=[])
            id2 = set_request_context(user="admin", scopes=[])
            assert id1 != id2
        finally:
            clear_request_context()

    def test_set_request_context_with_no_args(self):
        """set_request_context() with no args should still generate a request ID."""
        try:
            request_id = set_request_context()
            assert request_id is not None
            assert get_request_id() == request_id
        finally:
            clear_request_context()

    def test_thread_isolation(self):
        """Different threads should get different request IDs."""
        results = {}
        barrier = threading.Barrier(2)

        def worker(thread_name):
            try:
                barrier.wait(timeout=5)
                request_id = set_request_context(user=thread_name, scopes=[])
                results[thread_name] = request_id
            finally:
                clear_request_context()

        t1 = threading.Thread(target=worker, args=("thread-1",))
        t2 = threading.Thread(target=worker, args=("thread-2",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert "thread-1" in results
        assert "thread-2" in results
        assert results["thread-1"] != results["thread-2"]


class TestRequestIdFilter:
    def test_filter_adds_request_id(self):
        try:
            request_id = set_request_context(user="admin", scopes=[])
            filt = RequestIdFilter()
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0, msg="test", args=(), exc_info=None
            )
            result = filt.filter(record)
            assert result is True
            assert record.request_id == request_id  # type: ignore[attr-defined]
        finally:
            clear_request_context()

    def test_filter_defaults_to_dash_when_no_context(self):
        clear_request_context()
        filt = RequestIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0, msg="test", args=(), exc_info=None
        )
        result = filt.filter(record)
        assert result is True
        assert record.request_id == "-"  # type: ignore[attr-defined]

    def test_filter_always_returns_true(self):
        """Filter should never suppress log records, only enrich them."""
        filt = RequestIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0, msg="test", args=(), exc_info=None
        )
        assert filt.filter(record) is True
