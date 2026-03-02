"""Tests for Redis-based leader election."""

import time
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.leader import _LEADER_KEY, LeaderElector, _generate_instance_id

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_redis():
    """Create a mock Redis client for leader election tests."""
    client = MagicMock()
    client.set.return_value = True
    client.get.return_value = None
    client.delete.return_value = 1
    client.expire.return_value = True
    client.ping.return_value = True
    return client


@pytest.fixture()
def elector(mock_redis):
    """Create a LeaderElector with a mocked Redis client."""
    with patch.dict("sys.modules", {"redis": MagicMock()}):
        le = LeaderElector(
            redis_url="redis://localhost:6379/0",
            ttl=30,
            instance_id="test-host:1234",
        )
        le._redis = mock_redis
        return le


# ---------------------------------------------------------------------------
# Instance ID Generation
# ---------------------------------------------------------------------------


class TestInstanceId:
    """Tests for instance ID generation."""

    def test_generate_instance_id_format(self):
        """Instance ID should be hostname:pid format."""
        instance_id = _generate_instance_id()
        assert ":" in instance_id
        parts = instance_id.split(":")
        assert len(parts) == 2
        assert parts[1].isdigit()

    def test_custom_instance_id(self, mock_redis):
        """LeaderElector should accept a custom instance ID."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            le = LeaderElector(instance_id="my-custom-id")
            assert le.instance_id == "my-custom-id"


# ---------------------------------------------------------------------------
# Lease Acquisition
# ---------------------------------------------------------------------------


class TestTryAcquire:
    """Tests for try_acquire()."""

    def test_acquire_succeeds_no_existing_leader(self, elector, mock_redis):
        """try_acquire() succeeds when no existing leader -> is_leader() returns True."""
        mock_redis.set.return_value = True  # SET NX succeeds

        assert elector.try_acquire() is True
        assert elector.is_leader() is True

        mock_redis.set.assert_called_once_with(
            _LEADER_KEY,
            "test-host:1234",
            nx=True,
            ex=30,
        )

    def test_acquire_fails_another_instance_holds_lease(self, elector, mock_redis):
        """try_acquire() fails when another instance holds lease -> is_leader() returns False."""
        mock_redis.set.return_value = None  # SET NX fails (key exists)
        mock_redis.get.return_value = "other-host:5678"  # Another instance holds it

        assert elector.try_acquire() is False
        assert elector.is_leader() is False

    def test_acquire_reentrant_if_already_leader(self, elector, mock_redis):
        """try_acquire() recognizes if we already hold the lease."""
        mock_redis.set.return_value = None  # SET NX fails
        mock_redis.get.return_value = "test-host:1234"  # But we hold it

        assert elector.try_acquire() is True
        assert elector.is_leader() is True

    def test_acquire_fails_on_redis_error(self, elector, mock_redis):
        """try_acquire() returns False on Redis connection error."""
        mock_redis.set.side_effect = Exception("Connection refused")

        assert elector.try_acquire() is False
        assert elector.is_leader() is False


# ---------------------------------------------------------------------------
# Lease Renewal
# ---------------------------------------------------------------------------


class TestRenew:
    """Tests for renew()."""

    def test_renew_extends_lease_ttl(self, elector, mock_redis):
        """renew() extends lease TTL when we hold the lease."""
        # First acquire
        mock_redis.set.return_value = True
        elector.try_acquire()
        assert elector.is_leader() is True

        # Then renew
        mock_redis.get.return_value = "test-host:1234"
        assert elector.renew() is True
        mock_redis.expire.assert_called_with(_LEADER_KEY, 30)

    def test_renew_fails_when_not_leader(self, elector):
        """renew() returns False when not the leader."""
        assert elector.renew() is False

    def test_renew_detects_lease_stolen(self, elector, mock_redis):
        """renew() detects when another instance stole the lease."""
        # Acquire first
        mock_redis.set.return_value = True
        elector.try_acquire()

        # Lease was stolen
        mock_redis.get.return_value = "other-host:5678"
        assert elector.renew() is False
        assert elector.is_leader() is False

    def test_renew_redis_failure_with_ttl_not_expired(self, elector, mock_redis):
        """Redis failure during renew doesn't immediately demote if TTL not expired."""
        mock_redis.set.return_value = True
        elector.try_acquire()

        mock_redis.get.side_effect = Exception("Redis down")
        # TTL not expired yet (just acquired)
        assert elector.renew() is False
        # Still leader because TTL hasn't expired
        assert elector.is_leader() is True


# ---------------------------------------------------------------------------
# Lease Expiry
# ---------------------------------------------------------------------------


class TestLeaseExpiry:
    """Tests for lease expiration behavior."""

    def test_lease_expires_another_instance_can_acquire(self, mock_redis):
        """Lease expires -> another instance can acquire."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            # First instance acquires
            le1 = LeaderElector(ttl=1, instance_id="host1:1")
            le1._redis = mock_redis
            mock_redis.set.return_value = True
            le1.try_acquire()
            assert le1.is_leader() is True

            # Simulate TTL expiry by setting acquired_at far in the past
            le1._lease_acquired_at = time.monotonic() - 100

            # First instance should now detect expiry
            assert le1.is_leader() is False

    def test_redis_unavailable_demotes_after_ttl(self, elector, mock_redis):
        """Redis unavailable -> leader demotes after TTL."""
        mock_redis.set.return_value = True
        elector.try_acquire()
        assert elector.is_leader() is True

        # Simulate time passing beyond TTL
        elector._lease_acquired_at = time.monotonic() - 60  # Well past 30s TTL

        # Redis fails on renew
        mock_redis.get.side_effect = Exception("Redis down")
        elector.renew()

        # Should be demoted
        assert elector.is_leader() is False


# ---------------------------------------------------------------------------
# Lease Release
# ---------------------------------------------------------------------------


class TestRelease:
    """Tests for release()."""

    def test_release_clears_lease(self, elector, mock_redis):
        """release() clears the lease."""
        mock_redis.set.return_value = True
        elector.try_acquire()
        assert elector.is_leader() is True

        mock_redis.get.return_value = "test-host:1234"
        assert elector.release() is True
        assert elector.is_leader() is False
        mock_redis.delete.assert_called_with(_LEADER_KEY)

    def test_release_fails_when_not_holding_lease(self, elector, mock_redis):
        """release() returns False when another instance holds the lease."""
        mock_redis.set.return_value = True
        elector.try_acquire()

        mock_redis.get.return_value = "other-host:5678"
        assert elector.release() is False
        assert elector.is_leader() is False

    def test_release_handles_redis_error(self, elector, mock_redis):
        """release() handles Redis errors gracefully."""
        mock_redis.set.return_value = True
        elector.try_acquire()

        mock_redis.get.side_effect = Exception("Redis down")
        assert elector.release() is False
        assert elector.is_leader() is False


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    """Tests for status()."""

    def test_status_not_leader(self, elector):
        """Status shows not_leader when lease not held."""
        status = elector.status()
        assert status["enabled"] is True
        assert status["instance_id"] == "test-host:1234"
        assert status["is_leader"] is False
        assert status["lease_ttl"] == 30

    def test_status_is_leader(self, elector, mock_redis):
        """Status shows is_leader when lease held."""
        mock_redis.set.return_value = True
        elector.try_acquire()

        status = elector.status()
        assert status["is_leader"] is True
        assert status["lease_acquired_at"] is not None


# ---------------------------------------------------------------------------
# Background Renewal Thread
# ---------------------------------------------------------------------------


class TestRenewalThread:
    """Tests for start_renewal() and stop_renewal()."""

    def test_start_stop_renewal(self, elector, mock_redis):
        """start_renewal() and stop_renewal() manage the background thread."""
        mock_redis.set.return_value = True
        mock_redis.get.return_value = "test-host:1234"

        elector.start_renewal()
        assert elector._renewal_thread is not None
        assert elector._renewal_thread.is_alive()

        elector.stop_renewal()
        assert elector.is_leader() is False

    def test_start_renewal_acquires_if_not_leader(self, elector, mock_redis):
        """start_renewal() tries to acquire if not already leader."""
        mock_redis.set.return_value = True
        mock_redis.get.return_value = "test-host:1234"  # renew() verifies ownership
        assert elector.is_leader() is False

        elector.start_renewal()
        assert elector.is_leader() is True

        elector.stop_renewal()
