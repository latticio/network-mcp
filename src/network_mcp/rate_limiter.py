"""Per-device, per-user, and per-tenant rate limiting using a token bucket algorithm."""

import random
import threading
import time


class TokenBucket:
    """Thread-safe token bucket rate limiter."""

    def __init__(self, rate: float, capacity: float):
        """
        Args:
            rate: Tokens per second (e.g., 5.0 = 5 requests/sec).
            capacity: Maximum burst size.
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> bool:
        """Try to acquire tokens. Returns True if allowed, False if rate limited."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def wait_time(self) -> float:
        """Seconds until at least one token is available (with jitter to prevent thundering herd)."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens >= 1.0:
                return 0.0
            return (1.0 - self._tokens) / self._rate + random.uniform(0, 0.1)  # noqa: S311

    def acquire_with_backoff(self, max_wait: float = 5.0, tokens: float = 1.0) -> bool:
        """Try to acquire tokens, waiting with exponential backoff if rate limited.

        Args:
            max_wait: Maximum total time to wait in seconds.
            tokens: Number of tokens to acquire.

        Returns:
            True if tokens were acquired within max_wait, False otherwise.
        """
        if self.acquire(tokens):
            return True

        if max_wait <= 0:
            return False

        # Exponential backoff: 0.1s, 0.2s, 0.4s, 0.8s, ...
        total_waited = 0.0
        delay = 0.1
        while total_waited < max_wait:
            actual_delay = min(delay, max_wait - total_waited)
            time.sleep(actual_delay)
            total_waited += actual_delay
            if self.acquire(tokens):
                return True
            delay = min(delay * 2, 1.0)  # cap individual wait at 1s

        return False


class DeviceRateLimiter:
    """Per-device rate limiting for show and config commands.

    Thread-safe: bucket creation is protected by a lock.
    """

    def __init__(
        self,
        show_rate: float = 5.0,
        show_burst: float = 10.0,
        config_rate: float = 1.0,
        config_burst: float = 2.0,
    ):
        self._show_rate = show_rate
        self._show_burst = show_burst
        self._config_rate = config_rate
        self._config_burst = config_burst
        self._show_buckets: dict[str, TokenBucket] = {}
        self._config_buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _get_show_bucket(self, host: str) -> TokenBucket:
        """Get or create a show-rate bucket for a host. Thread-safe."""
        bucket = self._show_buckets.get(host)
        if bucket is not None:
            return bucket
        with self._lock:
            # Double-check under lock
            bucket = self._show_buckets.get(host)
            if bucket is None:
                bucket = TokenBucket(self._show_rate, self._show_burst)
                self._show_buckets[host] = bucket
            return bucket

    def _get_config_bucket(self, host: str) -> TokenBucket:
        """Get or create a config-rate bucket for a host. Thread-safe."""
        bucket = self._config_buckets.get(host)
        if bucket is not None:
            return bucket
        with self._lock:
            bucket = self._config_buckets.get(host)
            if bucket is None:
                bucket = TokenBucket(self._config_rate, self._config_burst)
                self._config_buckets[host] = bucket
            return bucket

    def check_show(self, host: str) -> bool:
        """Check if a show command is allowed for this device."""
        return self._get_show_bucket(host).acquire()

    def check_config(self, host: str) -> bool:
        """Check if a config command is allowed for this device."""
        return self._get_config_bucket(host).acquire()

    def check_show_with_backoff(self, host: str, max_wait: float = 5.0) -> bool:
        """Check show rate limit with backoff. Returns True if acquired within max_wait."""
        bucket = self._get_show_bucket(host)
        return bucket.acquire_with_backoff(max_wait)

    def check_config_with_backoff(self, host: str, max_wait: float = 5.0) -> bool:
        """Check config rate limit with backoff. Returns True if acquired within max_wait."""
        bucket = self._get_config_bucket(host)
        return bucket.acquire_with_backoff(max_wait)

    def show_wait_time(self, host: str) -> float:
        bucket = self._show_buckets.get(host)
        return bucket.wait_time() if bucket else 0.0

    def config_wait_time(self, host: str) -> float:
        bucket = self._config_buckets.get(host)
        return bucket.wait_time() if bucket else 0.0


# --- Scope tier constants ---

TIER_READ = "read"
TIER_WRITE = "write"
TIER_ADMIN = "admin"

_TIER_DEFAULTS: dict[str, float] = {
    TIER_READ: 10.0,
    TIER_WRITE: 2.0,
    TIER_ADMIN: 20.0,
}

_TIER_BURST_MULTIPLIER = 2.0


def _scope_to_tier(scopes: list[str]) -> str:
    """Map a list of OAuth scopes to the highest applicable rate-limit tier."""
    if "network:admin" in scopes:
        return TIER_ADMIN
    if "network:write" in scopes:
        return TIER_WRITE
    return TIER_READ


class UserRateLimiter:
    """Per-user rate limiting with scope-tier awareness.

    Each user gets a token bucket keyed by (user_id, scope_tier). Higher-privilege
    tiers (admin) can have higher limits than lower tiers (read).

    Thread-safe: bucket creation is protected by a lock.
    """

    def __init__(
        self,
        read_rate: float = 10.0,
        write_rate: float = 2.0,
        admin_rate: float = 20.0,
    ):
        self._tier_rates: dict[str, float] = {
            TIER_READ: read_rate,
            TIER_WRITE: write_rate,
            TIER_ADMIN: admin_rate,
        }
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _get_bucket(self, user_id: str, tier: str) -> TokenBucket:
        """Get or create a bucket for (user_id, tier). Thread-safe."""
        key = f"{user_id}:{tier}"
        bucket = self._buckets.get(key)
        if bucket is not None:
            return bucket
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                rate = self._tier_rates.get(tier, self._tier_rates[TIER_READ])
                bucket = TokenBucket(rate, rate * _TIER_BURST_MULTIPLIER)
                self._buckets[key] = bucket
            return bucket

    def check(self, user_id: str, scopes: list[str] | None = None) -> bool:
        """Check if a request is allowed for this user.

        Args:
            user_id: The authenticated user identifier.
            scopes: OAuth scopes from the token (used to determine tier).

        Returns:
            True if allowed, False if rate limited.
        """
        tier = _scope_to_tier(scopes or [])
        return self._get_bucket(user_id, tier).acquire()

    def wait_time(self, user_id: str, scopes: list[str] | None = None) -> float:
        """Return seconds until the user's bucket has capacity."""
        tier = _scope_to_tier(scopes or [])
        key = f"{user_id}:{tier}"
        bucket = self._buckets.get(key)
        return bucket.wait_time() if bucket else 0.0


class TenantRateLimiter:
    """Per-tenant rate limiting using a single token bucket per tenant.

    Enforces an aggregate rate limit across all users within a tenant.

    Thread-safe: bucket creation is protected by a lock.
    """

    def __init__(self, rate: float = 50.0, burst: float | None = None):
        self._rate = rate
        self._burst = burst if burst is not None else rate * _TIER_BURST_MULTIPLIER
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _get_bucket(self, tenant_id: str) -> TokenBucket:
        """Get or create a bucket for a tenant. Thread-safe."""
        bucket = self._buckets.get(tenant_id)
        if bucket is not None:
            return bucket
        with self._lock:
            bucket = self._buckets.get(tenant_id)
            if bucket is None:
                bucket = TokenBucket(self._rate, self._burst)
                self._buckets[tenant_id] = bucket
            return bucket

    def check(self, tenant_id: str) -> bool:
        """Check if a request is allowed for this tenant.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            True if allowed, False if rate limited.
        """
        return self._get_bucket(tenant_id).acquire()

    def wait_time(self, tenant_id: str) -> float:
        """Return seconds until the tenant's bucket has capacity."""
        bucket = self._buckets.get(tenant_id)
        return bucket.wait_time() if bucket else 0.0
