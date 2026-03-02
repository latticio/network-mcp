"""Tests for per-user and per-tenant rate limiting."""

import threading
import time
from unittest.mock import patch

from network_mcp.rate_limiter import (
    TIER_ADMIN,
    TIER_READ,
    TIER_WRITE,
    TenantRateLimiter,
    UserRateLimiter,
    _scope_to_tier,
)

# --- _scope_to_tier tests ---


class TestScopeToTier:
    def test_admin_scope(self):
        assert _scope_to_tier(["network:admin"]) == TIER_ADMIN

    def test_write_scope(self):
        assert _scope_to_tier(["network:write"]) == TIER_WRITE

    def test_read_scope(self):
        assert _scope_to_tier(["network:read"]) == TIER_READ

    def test_empty_scopes_defaults_to_read(self):
        assert _scope_to_tier([]) == TIER_READ

    def test_admin_takes_precedence_over_write(self):
        assert _scope_to_tier(["network:write", "network:admin"]) == TIER_ADMIN

    def test_write_takes_precedence_over_read(self):
        assert _scope_to_tier(["network:read", "network:write"]) == TIER_WRITE


# --- UserRateLimiter tests ---


class TestUserRateLimiter:
    def test_allows_requests_within_limit(self):
        limiter = UserRateLimiter(read_rate=5.0, write_rate=2.0, admin_rate=10.0)
        # Burst capacity = rate * 2.0
        for _ in range(10):
            assert limiter.check("user-1", ["network:read"])

    def test_blocks_requests_exceeding_limit(self):
        limiter = UserRateLimiter(read_rate=1.0, write_rate=1.0, admin_rate=1.0)
        # Burst capacity = 1.0 * 2.0 = 2 tokens
        assert limiter.check("user-1", ["network:read"])
        assert limiter.check("user-1", ["network:read"])
        # Third should be blocked
        assert not limiter.check("user-1", ["network:read"])

    def test_different_users_are_independent(self):
        limiter = UserRateLimiter(read_rate=1.0, write_rate=1.0, admin_rate=1.0)
        # Exhaust user-1
        assert limiter.check("user-1", ["network:read"])
        assert limiter.check("user-1", ["network:read"])
        assert not limiter.check("user-1", ["network:read"])
        # user-2 should still be allowed
        assert limiter.check("user-2", ["network:read"])

    def test_different_tiers_have_different_limits(self):
        limiter = UserRateLimiter(read_rate=1.0, write_rate=1.0, admin_rate=5.0)
        # Read user: burst = 2
        assert limiter.check("user-read", ["network:read"])
        assert limiter.check("user-read", ["network:read"])
        assert not limiter.check("user-read", ["network:read"])
        # Admin user: burst = 10
        for _ in range(10):
            assert limiter.check("user-admin", ["network:admin"])

    def test_wait_time_returns_positive_when_limited(self):
        limiter = UserRateLimiter(read_rate=1.0)
        assert limiter.check("user-1", ["network:read"])
        assert limiter.check("user-1", ["network:read"])
        assert not limiter.check("user-1", ["network:read"])
        wait = limiter.wait_time("user-1", ["network:read"])
        assert wait > 0

    def test_wait_time_returns_zero_when_not_limited(self):
        limiter = UserRateLimiter(read_rate=10.0)
        wait = limiter.wait_time("user-1", ["network:read"])
        assert wait == 0.0

    def test_tokens_refill_over_time(self):
        limiter = UserRateLimiter(read_rate=100.0)
        # Exhaust burst (200 tokens)
        for _ in range(200):
            limiter.check("user-1", ["network:read"])
        assert not limiter.check("user-1", ["network:read"])
        # Wait for refill
        time.sleep(0.05)  # ~5 tokens at 100/s
        assert limiter.check("user-1", ["network:read"])

    def test_none_scopes_defaults_to_read(self):
        limiter = UserRateLimiter(read_rate=1.0, admin_rate=100.0)
        # With None scopes, should use read tier (burst=2)
        assert limiter.check("user-1", None)
        assert limiter.check("user-1", None)
        assert not limiter.check("user-1", None)

    def test_thread_safety(self):
        limiter = UserRateLimiter(read_rate=100.0)
        results = []

        def worker():
            for _ in range(50):
                results.append(limiter.check("user-1", ["network:read"]))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # All calls should have completed without error
        assert len(results) == 200


# --- TenantRateLimiter tests ---


class TestTenantRateLimiter:
    def test_allows_requests_within_limit(self):
        limiter = TenantRateLimiter(rate=10.0)
        # Burst = 10 * 2 = 20
        for _ in range(20):
            assert limiter.check("tenant-1")

    def test_blocks_requests_exceeding_limit(self):
        limiter = TenantRateLimiter(rate=1.0)
        # Burst = 2
        assert limiter.check("tenant-1")
        assert limiter.check("tenant-1")
        assert not limiter.check("tenant-1")

    def test_different_tenants_are_independent(self):
        limiter = TenantRateLimiter(rate=1.0)
        assert limiter.check("tenant-1")
        assert limiter.check("tenant-1")
        assert not limiter.check("tenant-1")
        # tenant-2 is separate
        assert limiter.check("tenant-2")

    def test_wait_time_returns_positive_when_limited(self):
        limiter = TenantRateLimiter(rate=1.0)
        limiter.check("tenant-1")
        limiter.check("tenant-1")
        limiter.check("tenant-1")
        wait = limiter.wait_time("tenant-1")
        assert wait > 0

    def test_wait_time_returns_zero_when_no_bucket(self):
        limiter = TenantRateLimiter(rate=10.0)
        assert limiter.wait_time("nonexistent") == 0.0

    def test_custom_burst(self):
        limiter = TenantRateLimiter(rate=1.0, burst=5.0)
        for _ in range(5):
            assert limiter.check("tenant-1")
        assert not limiter.check("tenant-1")

    def test_thread_safety(self):
        limiter = TenantRateLimiter(rate=100.0)
        results = []

        def worker():
            for _ in range(50):
                results.append(limiter.check("tenant-1"))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 200


# --- Integration: _check_user_tenant_rate_limit ---


class TestCheckUserTenantRateLimit:
    def test_returns_none_when_anonymous(self):
        from network_mcp.helpers import _check_user_tenant_rate_limit, user_rate_limiter

        user_rate_limiter._buckets.clear()
        with (
            patch("network_mcp.middleware.get_current_user", return_value="anonymous"),
            patch("network_mcp.middleware.get_current_tenant_id", return_value=None),
            patch("network_mcp.middleware.get_current_scopes", return_value=[]),
        ):
            assert _check_user_tenant_rate_limit() is None

    def test_returns_error_when_user_limited(self):
        from network_mcp.helpers import _check_user_tenant_rate_limit

        with (
            patch("network_mcp.middleware.get_current_user", return_value="test-user"),
            patch("network_mcp.middleware.get_current_tenant_id", return_value=None),
            patch("network_mcp.middleware.get_current_scopes", return_value=["network:read"]),
            patch("network_mcp.helpers.user_rate_limiter") as mock_ulr,
        ):
            mock_ulr.check.return_value = False
            mock_ulr.wait_time.return_value = 1.5
            result = _check_user_tenant_rate_limit()
            assert result is not None
            assert result["status"] == "error"
            assert result["retry_after_seconds"] == 1.5
            assert "user" in result["error"]

    def test_returns_error_when_tenant_limited(self):
        from network_mcp.helpers import _check_user_tenant_rate_limit

        with (
            patch("network_mcp.middleware.get_current_user", return_value="anonymous"),
            patch("network_mcp.middleware.get_current_tenant_id", return_value="acme-corp"),
            patch("network_mcp.middleware.get_current_scopes", return_value=[]),
            patch("network_mcp.helpers.tenant_rate_limiter") as mock_tlr,
        ):
            mock_tlr.check.return_value = False
            mock_tlr.wait_time.return_value = 2.0
            result = _check_user_tenant_rate_limit()
            assert result is not None
            assert result["status"] == "error"
            assert result["retry_after_seconds"] == 2.0
            assert "tenant" in result["error"]

    def test_user_checked_before_tenant(self):
        from network_mcp.helpers import _check_user_tenant_rate_limit

        with (
            patch("network_mcp.middleware.get_current_user", return_value="test-user"),
            patch("network_mcp.middleware.get_current_tenant_id", return_value="acme-corp"),
            patch("network_mcp.middleware.get_current_scopes", return_value=["network:read"]),
            patch("network_mcp.helpers.user_rate_limiter") as mock_ulr,
            patch("network_mcp.helpers.tenant_rate_limiter") as mock_tlr,
        ):
            mock_ulr.check.return_value = False
            mock_ulr.wait_time.return_value = 0.5
            result = _check_user_tenant_rate_limit()
            assert result is not None
            assert "user" in result["error"]
            # Tenant limiter should not have been called
            mock_tlr.check.assert_not_called()
