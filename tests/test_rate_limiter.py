"""Tests for per-device rate limiting."""

from network_mcp.rate_limiter import DeviceRateLimiter, TokenBucket


class TestTokenBucket:
    def test_acquire_within_capacity(self):
        bucket = TokenBucket(rate=5.0, capacity=10.0)
        # Should be able to acquire up to capacity
        for _ in range(10):
            assert bucket.acquire() is True

    def test_acquire_exceeds_capacity(self):
        bucket = TokenBucket(rate=5.0, capacity=3.0)
        # Drain all tokens
        for _ in range(3):
            assert bucket.acquire() is True
        # Next should fail
        assert bucket.acquire() is False

    def test_wait_time(self):
        bucket = TokenBucket(rate=5.0, capacity=1.0)
        bucket.acquire()  # Drain the one token
        wait = bucket.wait_time()
        assert wait > 0.0
        assert wait <= 0.3  # At 5/s, ~0.2s base + up to 0.1s jitter

    def test_wait_time_when_available(self):
        bucket = TokenBucket(rate=5.0, capacity=5.0)
        assert bucket.wait_time() == 0.0


class TestDeviceRateLimiter:
    def test_show_allowed(self):
        limiter = DeviceRateLimiter(show_rate=5.0, show_burst=10.0)
        assert limiter.check_show("device1") is True

    def test_config_allowed(self):
        limiter = DeviceRateLimiter(config_rate=1.0, config_burst=2.0)
        assert limiter.check_config("device1") is True

    def test_show_rate_limit_exhaustion(self):
        limiter = DeviceRateLimiter(show_rate=5.0, show_burst=3.0)
        # Drain the burst capacity
        for _ in range(3):
            limiter.check_show("device1")
        # Next should be rate limited
        assert limiter.check_show("device1") is False

    def test_config_rate_limit_exhaustion(self):
        limiter = DeviceRateLimiter(config_rate=1.0, config_burst=1.0)
        limiter.check_config("device1")
        assert limiter.check_config("device1") is False

    def test_separate_devices(self):
        """Each device has its own bucket."""
        limiter = DeviceRateLimiter(show_rate=5.0, show_burst=1.0)
        limiter.check_show("device1")
        # device2 should still have tokens
        assert limiter.check_show("device2") is True

    def test_show_wait_time_no_bucket(self):
        limiter = DeviceRateLimiter()
        assert limiter.show_wait_time("unknown") == 0.0

    def test_config_wait_time_no_bucket(self):
        limiter = DeviceRateLimiter()
        assert limiter.config_wait_time("unknown") == 0.0


class TestTokenBucketJitter:
    """Verify jitter is applied to wait times."""

    def test_wait_time_includes_jitter(self):
        """When tokens are exhausted, wait_time should include a random jitter component."""
        bucket = TokenBucket(rate=5.0, capacity=1.0)
        bucket.acquire()  # Drain the one token

        # Collect multiple wait times — jitter means they won't all be identical
        wait_times = [bucket.wait_time() for _ in range(20)]

        # All should be positive
        assert all(w > 0.0 for w in wait_times)

        # With jitter (0-100ms), wait times should not all be the same
        unique_times = set(wait_times)
        assert len(unique_times) > 1, "Expected jitter to produce varying wait times"

    def test_wait_time_jitter_bounded(self):
        """Jitter should add at most 0.1s to the base wait time."""
        bucket = TokenBucket(rate=10.0, capacity=1.0)
        bucket.acquire()

        # Base wait at 10/s rate with 0 tokens is 0.1s; with jitter max is 0.2s
        for _ in range(50):
            wait = bucket.wait_time()
            assert wait <= 0.3  # generous bound accounting for timing
