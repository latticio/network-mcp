"""Tests for per-vendor bulkhead isolation."""

import threading
import time

import pytest

from network_mcp.connection import BulkheadManager, bulkhead_slot, get_bulkhead, init_bulkhead


class TestBulkheadManager:
    """Tests for BulkheadManager acquire/release and isolation."""

    def test_acquire_and_release(self):
        """Acquire a slot, release it, verify available count."""
        bh = BulkheadManager(per_vendor_limit=5)
        assert bh.get_available("eos") == 5

        assert bh.acquire("eos", timeout=1.0) is True
        assert bh.get_available("eos") == 4

        bh.release("eos")
        assert bh.get_available("eos") == 5

    def test_limit_enforcement(self):
        """Create manager with limit=2, acquire 2 slots, verify 3rd times out."""
        bh = BulkheadManager(per_vendor_limit=2)
        assert bh.acquire("eos", timeout=1.0) is True
        assert bh.acquire("eos", timeout=1.0) is True
        assert bh.get_available("eos") == 0

        # Third acquisition should time out
        start = time.monotonic()
        assert bh.acquire("eos", timeout=0.2) is False
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15  # Should have waited ~0.2s

    def test_per_vendor_isolation(self):
        """Exhaust slots for vendor 'eos', verify vendor 'iosxe' still has slots."""
        bh = BulkheadManager(per_vendor_limit=2)
        assert bh.acquire("eos", timeout=1.0) is True
        assert bh.acquire("eos", timeout=1.0) is True
        assert bh.get_available("eos") == 0

        # Different vendor should be unaffected
        assert bh.acquire("iosxe", timeout=1.0) is True
        assert bh.get_available("iosxe") == 1

    def test_reset(self):
        """Verify reset clears all semaphores."""
        bh = BulkheadManager(per_vendor_limit=3)
        bh.acquire("eos", timeout=1.0)
        bh.acquire("iosxe", timeout=1.0)
        assert bh.get_available("eos") == 2
        assert bh.get_available("iosxe") == 2

        bh.reset()
        # After reset, new semaphores are created fresh
        assert bh.get_available("eos") == 3
        assert bh.get_available("iosxe") == 3

    def test_thread_safety(self):
        """Verify concurrent acquire/release from multiple threads is safe."""
        bh = BulkheadManager(per_vendor_limit=10)
        errors = []

        def worker():
            try:
                for _ in range(50):
                    assert bh.acquire("eos", timeout=5.0) is True
                    bh.release("eos")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert bh.get_available("eos") == 10


class TestBulkheadSlotContextManager:
    """Tests for the bulkhead_slot() context manager."""

    def test_normal_usage(self):
        """Verify slot is acquired and released on normal exit."""
        init_bulkhead(5)
        bh = get_bulkhead()
        assert bh is not None
        assert bh.get_available("eos") == 5

        with bulkhead_slot("eos", timeout=1.0):
            assert bh.get_available("eos") == 4

        assert bh.get_available("eos") == 5

    def test_release_on_exception(self):
        """Verify slot is released even when body raises."""
        init_bulkhead(5)
        bh = get_bulkhead()
        assert bh is not None

        with pytest.raises(ValueError, match="test error"):
            with bulkhead_slot("eos", timeout=1.0):
                assert bh.get_available("eos") == 4
                raise ValueError("test error")

        assert bh.get_available("eos") == 5

    def test_disabled_bulkhead(self):
        """When _bulkhead is None, bulkhead_slot() yields without error."""
        # _bulkhead is reset to None by the autouse fixture
        assert get_bulkhead() is None

        # Should not raise
        with bulkhead_slot("eos", timeout=1.0):
            pass

    def test_timeout_error_message(self):
        """Verify timeout exception includes vendor name and slot info."""
        init_bulkhead(1)
        bh = get_bulkhead()
        assert bh is not None

        # Exhaust the single slot
        bh.acquire("eos", timeout=1.0)

        with pytest.raises(TimeoutError, match="vendor 'eos'"):
            with bulkhead_slot("eos", timeout=0.1):
                pass  # pragma: no cover


class TestBulkheadModuleAccessors:
    """Tests for init_bulkhead and get_bulkhead."""

    def test_init_and_get(self):
        """init_bulkhead creates a manager, get_bulkhead returns it."""
        assert get_bulkhead() is None
        bh = init_bulkhead(10)
        assert get_bulkhead() is bh
        assert bh._per_vendor_limit == 10

    def test_get_platform_default(self):
        """ConnectionManager.get_platform returns 'eos' for unknown hosts."""
        from network_mcp.server import conn_mgr

        assert conn_mgr.get_platform("unknown-host-xyz") == "eos"
