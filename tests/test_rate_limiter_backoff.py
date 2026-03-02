"""Tests for rate limiter exponential backoff."""

import time
from unittest.mock import patch

from network_mcp.rate_limiter import DeviceRateLimiter, TokenBucket


class TestTokenBucketBackoff:
    """Tests for TokenBucket.acquire_with_backoff()."""

    def test_succeeds_immediately_when_tokens_available(self):
        """Verify returns True without sleeping when tokens are available."""
        bucket = TokenBucket(rate=10.0, capacity=10.0)
        start = time.monotonic()
        assert bucket.acquire_with_backoff(max_wait=5.0) is True
        elapsed = time.monotonic() - start
        assert elapsed < 0.05  # Should be near-instant

    def test_waits_and_succeeds(self):
        """Drain bucket, verify backoff eventually acquires token."""
        # Rate of 10 tokens/sec, capacity of 1 token — drains fast, refills fast
        bucket = TokenBucket(rate=10.0, capacity=1.0)
        # Drain the bucket
        bucket.acquire(1.0)

        start = time.monotonic()
        assert bucket.acquire_with_backoff(max_wait=2.0) is True
        elapsed = time.monotonic() - start
        # Should have waited some time for refill but succeeded within 2s
        assert 0.05 < elapsed < 2.0

    def test_fails_after_max_wait(self):
        """Set very low rate and short max_wait, verify returns False."""
        # Very slow refill: 0.1 tokens/sec, need 1 token = 10 seconds refill
        bucket = TokenBucket(rate=0.1, capacity=1.0)
        bucket.acquire(1.0)  # drain

        start = time.monotonic()
        assert bucket.acquire_with_backoff(max_wait=0.3) is False
        elapsed = time.monotonic() - start
        assert 0.25 < elapsed < 0.6  # Waited approximately max_wait

    def test_max_wait_zero_disables_backoff(self):
        """Verify immediate failure when max_wait=0."""
        bucket = TokenBucket(rate=1.0, capacity=1.0)
        bucket.acquire(1.0)  # drain

        start = time.monotonic()
        assert bucket.acquire_with_backoff(max_wait=0.0) is False
        elapsed = time.monotonic() - start
        assert elapsed < 0.05  # Should be instant

    def test_backoff_timing(self):
        """Verify total wait time is approximately as expected."""
        # Rate of 2 tokens/sec, drain it — should refill 1 token in 0.5s
        bucket = TokenBucket(rate=2.0, capacity=1.0)
        bucket.acquire(1.0)

        start = time.monotonic()
        result = bucket.acquire_with_backoff(max_wait=3.0)
        elapsed = time.monotonic() - start

        assert result is True
        # First backoff is 0.1s, token refills at 2/sec = 0.5s for 1 token
        # Should succeed after a few backoff iterations but well under 3s
        assert elapsed < 1.5


class TestDeviceRateLimiterBackoff:
    """Tests for DeviceRateLimiter backoff methods."""

    def test_check_show_with_backoff(self):
        """Integration test through DeviceRateLimiter."""
        limiter = DeviceRateLimiter(show_rate=10.0, show_burst=1.0)
        # First call drains the bucket
        assert limiter.check_show("spine-01") is True

        # Backoff should allow the next one to succeed
        assert limiter.check_show_with_backoff("spine-01", max_wait=2.0) is True

    def test_check_config_with_backoff(self):
        """Integration test through DeviceRateLimiter."""
        limiter = DeviceRateLimiter(config_rate=10.0, config_burst=1.0)
        # First call drains the bucket
        assert limiter.check_config("spine-01") is True

        # Backoff should allow the next one to succeed
        assert limiter.check_config_with_backoff("spine-01", max_wait=2.0) is True

    def test_check_show_with_backoff_timeout(self):
        """Verify backoff fails when rate is too slow."""
        limiter = DeviceRateLimiter(show_rate=0.1, show_burst=1.0)
        limiter.check_show("spine-01")  # drain

        assert limiter.check_show_with_backoff("spine-01", max_wait=0.3) is False

    def test_check_config_with_backoff_timeout(self):
        """Verify backoff fails when rate is too slow."""
        limiter = DeviceRateLimiter(config_rate=0.1, config_burst=1.0)
        limiter.check_config("spine-01")  # drain

        assert limiter.check_config_with_backoff("spine-01", max_wait=0.3) is False


class TestBackoffInRunShowCommand:
    """Tests for backoff integration in run_show_command."""

    def test_backoff_in_run_show_command(self, mock_conn_mgr, mock_node):
        """Verify run_show_command uses backoff and succeeds after waiting."""
        from network_mcp.helpers import run_show_command
        from network_mcp.server import conn_mgr

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]

        # Use backoff with settings
        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.net_rate_limit_max_wait = 2.0
            settings.net_conn_acquire_timeout = 30.0
            settings.net_cache_stale_if_error_ttl = 0

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] == "success"

    def test_max_wait_zero_disables_backoff(self, mock_conn_mgr, mock_node):
        """Verify immediate failure when NET_RATE_LIMIT_MAX_WAIT=0."""
        from network_mcp.helpers import device_rate_limiter, run_show_command
        from network_mcp.server import conn_mgr

        mock_node.run_commands.return_value = [{"version": "4.32.1F"}]

        # Drain the rate limiter for this host
        device_rate_limiter._show_buckets.clear()

        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.net_rate_limit_max_wait = 0.0
            settings.net_conn_acquire_timeout = 30.0
            settings.net_cache_stale_if_error_ttl = 0
            settings.net_cache_enabled = True

            # Create a very restrictive rate limiter
            bucket = TokenBucket(rate=0.01, capacity=1.0)
            bucket.acquire(1.0)  # drain
            device_rate_limiter._show_buckets["spine-01"] = bucket

            result = run_show_command(conn_mgr, "spine-01", ["show version"])
            assert result["status"] == "error"
            assert "RATE_LIMITED" in result.get("error_code", "")

    def test_backoff_in_run_config_command(self, mock_conn_mgr, mock_node):
        """Verify run_config_command uses backoff."""
        from network_mcp.helpers import run_config_command
        from network_mcp.server import conn_mgr

        mock_node.config.return_value = [{}]

        with patch("network_mcp.helpers._get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.net_read_only = False
            settings.net_rate_limit_max_wait = 2.0
            settings.net_conn_acquire_timeout = 30.0
            settings.config_retry_enabled = False
            settings.net_change_mgmt_enabled = False

            result = run_config_command(conn_mgr, "spine-01", ["vlan 100"], "test_action")
            assert result["status"] == "success"
