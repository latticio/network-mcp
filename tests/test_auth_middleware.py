"""Tests for auth scope utilities, request context, TokenVerifier, and server auth wiring.

Tests cover:
- Scope checking: read token can't access write tools
- Annotation → scope mapping
- Thread-local request context
- Audit log includes sub claim when auth enabled
- TokenVerifier adapter (JWTTokenVerifier → MCP AccessToken)
- FastMCP created with auth=None when AUTH_ENABLED=false
"""

import logging
import time

import pytest

from network_mcp.auth import JWT_AVAILABLE, AccessToken, JWTTokenVerifier, TokenVerifier
from network_mcp.middleware import (
    SCOPE_READ,
    SCOPE_WRITE,
    check_scope,
    clear_request_context,
    get_current_scopes,
    get_current_user,
    required_scope_for_annotations,
    set_request_context,
)

# Skip all tests if PyJWT is not installed
pytestmark = pytest.mark.skipif(not JWT_AVAILABLE, reason="PyJWT not installed")

if JWT_AVAILABLE:
    import jwt as pyjwt

# Test constants
TEST_SECRET = "test-secret-key-for-unit-tests-only"
TEST_ISSUER = "https://test-auth.example.com"
TEST_AUDIENCE = "network-mcp"


# --- Helpers ---


def make_token(
    sub: str = "test-user",
    scopes: str = "network:read",
    exp: int | None = None,
    issuer: str = TEST_ISSUER,
    audience: str = TEST_AUDIENCE,
    secret: str = TEST_SECRET,
) -> str:
    """Create a signed JWT token for testing."""
    payload = {
        "sub": sub,
        "scope": scopes,
        "iss": issuer,
        "aud": audience,
        "iat": int(time.time()),
    }
    if exp is not None:
        payload["exp"] = exp
    else:
        payload["exp"] = int(time.time()) + 3600  # 1 hour from now

    return pyjwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def _clean_context():
    """Reset request context before and after each test."""
    clear_request_context()
    yield
    clear_request_context()


# ─── Scope checking ─────────────────────────────────────────────────────


class TestScopeChecking:
    """Scope-based tool access control."""

    def test_read_scope_allows_read_tools(self):
        token = AccessToken(token="t", client_id="user", scopes=["network:read"])
        result = check_scope(token, SCOPE_READ)
        assert result is None  # No error

    def test_read_scope_blocks_write_tools(self):
        token = AccessToken(token="t", client_id="user", scopes=["network:read"])
        result = check_scope(token, SCOPE_WRITE)
        assert result is not None
        assert "Insufficient scope" in result
        assert "network:write" in result
        assert "network:read" in result

    def test_write_scope_allows_read_tools(self):
        token = AccessToken(token="t", client_id="user", scopes=["network:write"])
        result = check_scope(token, SCOPE_READ)
        assert result is None

    def test_write_scope_allows_write_tools(self):
        token = AccessToken(token="t", client_id="user", scopes=["network:write"])
        result = check_scope(token, SCOPE_WRITE)
        assert result is None

    def test_admin_scope_allows_everything(self):
        token = AccessToken(token="t", client_id="admin", scopes=["network:admin"])
        assert check_scope(token, SCOPE_READ) is None
        assert check_scope(token, SCOPE_WRITE) is None

    def test_no_scopes_blocks_everything(self):
        token = AccessToken(token="t", client_id="user", scopes=[])
        assert check_scope(token, SCOPE_READ) is not None
        assert check_scope(token, SCOPE_WRITE) is not None

    def test_scope_error_message_format(self):
        token = AccessToken(token="t", client_id="user", scopes=["network:read"])
        result = check_scope(token, SCOPE_WRITE)
        assert "Insufficient scope. This tool requires 'network:write'." in result
        assert "Your token has: 'network:read'." in result


# ─── Annotation → scope mapping ──────────────────────────────────────────


class TestAnnotationMapping:
    """Map tool annotations to required scopes."""

    def test_read_only_annotation(self):
        from network_mcp.helpers import READ_ONLY

        assert required_scope_for_annotations(READ_ONLY) == SCOPE_READ

    def test_write_safe_annotation(self):
        from network_mcp.helpers import WRITE_SAFE

        assert required_scope_for_annotations(WRITE_SAFE) == SCOPE_WRITE

    def test_destructive_annotation(self):
        from network_mcp.helpers import DESTRUCTIVE

        assert required_scope_for_annotations(DESTRUCTIVE) == SCOPE_WRITE

    def test_none_annotation_defaults_to_read(self):
        assert required_scope_for_annotations(None) == SCOPE_READ


# ─── Request context ─────────────────────────────────────────────────────


class TestRequestContext:
    """Thread-local request context management."""

    def test_default_user_is_anonymous(self):
        assert get_current_user() == "anonymous"

    def test_set_and_get_user(self):
        set_request_context("bob", ["network:read"])
        assert get_current_user() == "bob"
        assert get_current_scopes() == ["network:read"]

    def test_clear_resets_to_anonymous(self):
        set_request_context("alice", ["network:write"])
        clear_request_context()
        assert get_current_user() == "anonymous"
        assert get_current_scopes() == []


# ─── Audit log includes user ────────────────────────────────────────────


class TestAuditUserAttribution:
    """Verify audit logs include the authenticated user.

    AuditLogger sets propagate=False with its own handler, so we capture
    records by temporarily adding a handler that stores them.
    """

    def test_audit_log_includes_user_field(self):
        from network_mcp.audit import AuditLogger

        audit = AuditLogger(logger_name="test-audit-user")
        captured_records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: captured_records.append(record)
        audit._logger.addHandler(handler)

        try:
            set_request_context("jane.doe@corp.com", ["network:read"])
            audit.log_tool_call(
                tool_name="get_device_info",
                device="spine-01",
                parameters={"host": "spine-01"},
                result_status="success",
                duration_ms=42.5,
            )

            audit_records = [r for r in captured_records if hasattr(r, "audit_data")]
            assert len(audit_records) == 1
            assert audit_records[0].audit_data["user"] == "jane.doe@corp.com"
        finally:
            audit._logger.removeHandler(handler)

    def test_audit_log_anonymous_when_no_auth(self):
        from network_mcp.audit import AuditLogger

        audit = AuditLogger(logger_name="test-audit-anon")
        captured_records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: captured_records.append(record)
        audit._logger.addHandler(handler)

        try:
            clear_request_context()
            audit.log_tool_call(
                tool_name="get_bgp_summary",
                device="leaf-01",
                parameters={"host": "leaf-01"},
                result_status="success",
                duration_ms=15.0,
            )

            audit_records = [r for r in captured_records if hasattr(r, "audit_data")]
            assert len(audit_records) == 1
            assert audit_records[0].audit_data["user"] == "anonymous"
        finally:
            audit._logger.removeHandler(handler)

    def test_audit_log_explicit_user_override(self):
        from network_mcp.audit import AuditLogger

        audit = AuditLogger(logger_name="test-audit-override")
        captured_records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: captured_records.append(record)
        audit._logger.addHandler(handler)

        try:
            set_request_context("should-be-ignored", ["network:read"])
            audit.log_tool_call(
                tool_name="get_vlans",
                device="spine-02",
                parameters={},
                result_status="success",
                duration_ms=10.0,
                user="explicit-user",
            )

            audit_records = [r for r in captured_records if hasattr(r, "audit_data")]
            assert len(audit_records) == 1
            assert audit_records[0].audit_data["user"] == "explicit-user"
        finally:
            audit._logger.removeHandler(handler)


# ─── JWTTokenVerifier direct tests ──────────────────────────────────────


class TestJWTTokenVerifier:
    """Direct tests for the JWTTokenVerifier class."""

    def test_verify_valid_token(self):
        verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        token_str = make_token(sub="admin-user", scopes="network:admin")
        result = verifier.verify_token(token_str)
        assert result is not None
        assert result.client_id == "admin-user"
        assert "network:admin" in result.scopes

    def test_verify_returns_none_for_bad_token(self):
        verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        result = verifier.verify_token("completely-invalid")
        assert result is None

    def test_verify_expired_token_returns_none(self):
        verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        token_str = make_token(exp=int(time.time()) - 100)
        result = verifier.verify_token(token_str)
        assert result is None

    def test_scopes_from_list_claim(self):
        """Token with 'scopes' as a list (not space-separated string)."""
        payload = {
            "sub": "list-user",
            "scopes": ["network:read", "network:write"],
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "exp": int(time.time()) + 3600,
        }
        token_str = pyjwt.encode(payload, TEST_SECRET, algorithm="HS256")
        verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        result = verifier.verify_token(token_str)
        assert result is not None
        assert "network:read" in result.scopes
        assert "network:write" in result.scopes

    def test_no_scope_claim_defaults_to_empty(self):
        """Token without any scope claim gets empty scopes."""
        payload = {
            "sub": "no-scope-user",
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "exp": int(time.time()) + 3600,
        }
        token_str = pyjwt.encode(payload, TEST_SECRET, algorithm="HS256")
        verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        result = verifier.verify_token(token_str)
        assert result is not None
        assert result.scopes == []


# ─── TokenVerifier (adapter) ─────────────────────────────────────────


class TestTokenVerifier:
    """Tests for the TokenVerifier adapter that bridges JWTTokenVerifier to MCP SDK."""

    @pytest.fixture
    def verifier(self):
        jwt_verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        return TokenVerifier(jwt_verifier)

    @pytest.mark.asyncio
    async def test_valid_token_returns_mcp_access_token(self, verifier):
        token_str = make_token(sub="admin-user", scopes="network:read network:write")
        result = await verifier.verify_token(token_str)
        assert result is not None
        assert result.client_id == "admin-user"
        assert "network:read" in result.scopes
        assert "network:write" in result.scopes
        # Verify it's the MCP SDK AccessToken (Pydantic model), not our dataclass
        from mcp.server.auth.provider import AccessToken as MCPAccessToken

        assert isinstance(result, MCPAccessToken)

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self, verifier):
        result = await verifier.verify_token("not-a-valid-jwt")
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self, verifier):
        token_str = make_token(exp=int(time.time()) - 3600)
        result = await verifier.verify_token(token_str)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_secret_returns_none(self, verifier):
        token_str = make_token(secret="wrong-key")
        result = await verifier.verify_token(token_str)
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_issuer_returns_none(self, verifier):
        token_str = make_token(issuer="https://evil.example.com")
        result = await verifier.verify_token(token_str)
        assert result is None


# ─── Server auth wiring ─────────────────────────────────────────────────


class TestServerAuthWiring:
    """Test that FastMCP is created with correct auth configuration."""

    def test_default_auth_disabled(self):
        """When AUTH_ENABLED=false (default), mcp has no auth settings."""
        from network_mcp.server import mcp

        assert mcp.settings.auth is None

    def test_default_no_token_verifier(self):
        """When AUTH_ENABLED=false, no token verifier is set."""
        from network_mcp.server import _token_verifier

        assert _token_verifier is None


# ─── Integration: full auth flow ─────────────────────────────────────────


class TestIntegrationFlow:
    """End-to-end: TokenVerifier → scope check."""

    @pytest.mark.asyncio
    async def test_full_read_only_flow(self):
        jwt_verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        eos_verifier = TokenVerifier(jwt_verifier)
        token_str = make_token(sub="read-user", scopes="network:read")

        # Verify token through adapter
        mcp_token = await eos_verifier.verify_token(token_str)
        assert mcp_token is not None

        # Check scope for read-only tool (using our local AccessToken for scope check)
        local_token = AccessToken(token=token_str, client_id=mcp_token.client_id, scopes=mcp_token.scopes)
        scope_err = check_scope(local_token, SCOPE_READ)
        assert scope_err is None

    @pytest.mark.asyncio
    async def test_full_write_blocked_flow(self):
        jwt_verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        eos_verifier = TokenVerifier(jwt_verifier)
        token_str = make_token(sub="read-user", scopes="network:read")

        mcp_token = await eos_verifier.verify_token(token_str)
        assert mcp_token is not None

        local_token = AccessToken(token=token_str, client_id=mcp_token.client_id, scopes=mcp_token.scopes)
        scope_err = check_scope(local_token, SCOPE_WRITE)
        assert scope_err is not None
        assert "Insufficient scope" in scope_err

    @pytest.mark.asyncio
    async def test_full_admin_flow(self):
        jwt_verifier = JWTTokenVerifier(secret_key=TEST_SECRET, issuer=TEST_ISSUER)
        eos_verifier = TokenVerifier(jwt_verifier)
        token_str = make_token(sub="admin", scopes="network:admin")

        mcp_token = await eos_verifier.verify_token(token_str)
        assert mcp_token is not None

        local_token = AccessToken(token=token_str, client_id=mcp_token.client_id, scopes=mcp_token.scopes)
        assert check_scope(local_token, SCOPE_READ) is None
        assert check_scope(local_token, SCOPE_WRITE) is None
