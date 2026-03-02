"""Tests for GnmiConnectionPool — thread-safe gNMI connection pooling."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.gnmi_connection import GNMI_AVAILABLE, GnmiConnectionPool, _PoolEntry
from tests.conftest import make_test_settings


def _make_pool(**overrides):
    """Create a GnmiConnectionPool with test defaults."""
    settings = make_test_settings(
        net_gnmi_pool_max=overrides.pop("net_gnmi_pool_max", 5),
        net_gnmi_pool_ttl=overrides.pop("net_gnmi_pool_ttl", 300),
        **overrides,
    )
    return GnmiConnectionPool(settings)


# ---- Basic pool tests ----


class TestGnmiConnectionPoolCreate:
    """Test pool creation and basic properties."""

    def test_pool_starts_empty(self):
        pool = _make_pool()
        assert pool.pool_size == 0

    def test_pool_respects_max_size_setting(self):
        pool = _make_pool(net_gnmi_pool_max=10)
        assert pool._max_size == 10

    def test_pool_respects_ttl_setting(self):
        pool = _make_pool(net_gnmi_pool_ttl=600)
        assert pool._ttl == 600


class TestGnmiConnectionPoolGetConnection:
    """Test get_connection creates and caches connections."""

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_get_connection_creates_new(self):
        pool = _make_pool()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("network_mcp.gnmi_connection.gNMIclient", return_value=mock_client):
            client = pool.get_connection("switch1", 6030)

        assert client is mock_client
        assert pool.pool_size == 1

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_get_connection_returns_cached(self):
        pool = _make_pool()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("network_mcp.gnmi_connection.gNMIclient", return_value=mock_client) as mock_cls:
            client1 = pool.get_connection("switch1", 6030)
            client2 = pool.get_connection("switch1", 6030)

        assert client1 is client2
        # gNMIclient constructor called only once (connection was cached)
        assert mock_cls.call_count == 1
        assert pool.pool_size == 1

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_get_connection_uses_default_port(self):
        pool = _make_pool()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("network_mcp.gnmi_connection.gNMIclient", return_value=mock_client):
            pool.get_connection("switch1")

        assert ("switch1", 6030) in pool._pool


class TestGnmiConnectionPoolTTLExpiry:
    """Test TTL-based connection expiry."""

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_expired_connection_is_replaced(self):
        pool = _make_pool(net_gnmi_pool_ttl=10)

        mock_client1 = MagicMock()
        mock_client1.__enter__ = MagicMock(return_value=mock_client1)
        mock_client1.__exit__ = MagicMock(return_value=False)

        mock_client2 = MagicMock()
        mock_client2.__enter__ = MagicMock(return_value=mock_client2)
        mock_client2.__exit__ = MagicMock(return_value=False)

        with patch("network_mcp.gnmi_connection.gNMIclient", side_effect=[mock_client1, mock_client2]):
            client1 = pool.get_connection("switch1", 6030)
            assert client1 is mock_client1

            # Simulate TTL expiry by backdating created_at beyond TTL
            pool._pool[("switch1", 6030)].created_at = time.monotonic() - 20

            client2 = pool.get_connection("switch1", 6030)
            assert client2 is mock_client2
            assert pool.pool_size == 1

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_evict_expired_removes_stale_entries(self):
        pool = _make_pool(net_gnmi_pool_ttl=10)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("network_mcp.gnmi_connection.gNMIclient", return_value=mock_client):
            pool.get_connection("switch1", 6030)
            # Backdate beyond TTL to simulate expiry
            pool._pool[("switch1", 6030)].created_at = time.monotonic() - 20

            evicted = pool._evict_expired()

        assert evicted == 1
        assert pool.pool_size == 0


class TestGnmiConnectionPoolMaxEviction:
    """Test FIFO eviction when pool is full."""

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_evicts_oldest_when_full(self):
        pool = _make_pool(net_gnmi_pool_max=2)
        clients = []
        for _i in range(3):
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            clients.append(mock_client)

        with patch("network_mcp.gnmi_connection.gNMIclient", side_effect=clients):
            pool.get_connection("switch1", 6030)
            pool.get_connection("switch2", 6030)
            assert pool.pool_size == 2

            # Adding a third should evict the first
            pool.get_connection("switch3", 6030)
            assert pool.pool_size == 2
            assert ("switch1", 6030) not in pool._pool
            assert ("switch2", 6030) in pool._pool
            assert ("switch3", 6030) in pool._pool


class TestGnmiConnectionPoolCloseOperations:
    """Test close_connection and close_all."""

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_close_connection_removes_entry(self):
        pool = _make_pool()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("network_mcp.gnmi_connection.gNMIclient", return_value=mock_client):
            pool.get_connection("switch1", 6030)
            assert pool.pool_size == 1

        pool.close_connection("switch1", 6030)
        assert pool.pool_size == 0

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_close_connection_nonexistent_is_noop(self):
        pool = _make_pool()
        pool.close_connection("nonexistent", 6030)  # should not raise
        assert pool.pool_size == 0

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_close_all_clears_pool(self):
        pool = _make_pool()
        clients = []
        for _i in range(3):
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            clients.append(mock_client)

        with patch("network_mcp.gnmi_connection.gNMIclient", side_effect=clients):
            pool.get_connection("switch1", 6030)
            pool.get_connection("switch2", 6030)
            pool.get_connection("switch3", 6030)

        pool.close_all()
        assert pool.pool_size == 0


class TestGnmiConnectionPoolThreadSafety:
    """Test concurrent access to the pool."""

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_concurrent_get_connection(self):
        pool = _make_pool(net_gnmi_pool_max=20)
        errors: list[str] = []

        def _worker(host_id):
            try:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                with patch("network_mcp.gnmi_connection.gNMIclient", return_value=mock_client):
                    pool.get_connection(f"switch{host_id}", 6030)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Thread errors: {errors}"
        assert pool.pool_size == 10


class TestGnmiConnectionPoolPoolSize:
    """Test pool_size property."""

    def test_pool_size_starts_at_zero(self):
        pool = _make_pool()
        assert pool.pool_size == 0

    @pytest.mark.skipif(not GNMI_AVAILABLE, reason="pygnmi not installed")
    def test_pool_size_tracks_connections(self):
        pool = _make_pool()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("network_mcp.gnmi_connection.gNMIclient", return_value=mock_client):
            pool.get_connection("switch1", 6030)
            assert pool.pool_size == 1

        pool.close_connection("switch1", 6030)
        assert pool.pool_size == 0


class TestGnmiConnectionPoolPygnmiMissing:
    """Test behavior when pygnmi is not installed."""

    def test_get_connection_raises_when_pygnmi_missing(self):
        pool = _make_pool()
        with patch("network_mcp.gnmi_connection.GNMI_AVAILABLE", False):
            with pytest.raises(RuntimeError, match="pygnmi is not installed"):
                pool.get_connection("switch1", 6030)


class TestGnmiConnectionPoolReleaseConnection:
    """Test release_connection (currently a no-op)."""

    def test_release_connection_is_noop(self):
        pool = _make_pool()
        # Should not raise even with nothing in pool
        pool.release_connection("switch1", 6030)
        assert pool.pool_size == 0


class TestPoolEntry:
    """Test the _PoolEntry internal class."""

    def test_pool_entry_stores_client_and_timestamp(self):
        mock_client = MagicMock()
        now = time.monotonic()
        entry = _PoolEntry(client=mock_client, created_at=now)
        assert entry.client is mock_client
        assert entry.created_at == now
