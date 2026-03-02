"""OAuth 2.1 / JWT authentication for Streamable HTTP transport.

Activated when AUTH_ENABLED=true. Validates JWT tokens attached to
incoming HTTP requests. Supports both static secret keys (HS256) and
JWKS-based key discovery (RS256/ES256) with automatic key rotation.

Auth scopes:
  - network:read   — read-only tools (show commands)
  - network:write  — read + write tools (config changes)
  - network:admin  — all tools including config management
  - network:audit  — compliance and audit tools

FastMCP integration:
  TokenVerifier adapts JWTTokenVerifier to the MCP SDK's TokenVerifier
  protocol (async verify_token -> mcp AccessToken). Pass it as the
  ``token_verifier`` argument to FastMCP alongside an AuthSettings instance.

OWASP MCP Top 10 compliance:
  - MCP01 (Token Mismanagement): Tokens never appear in tool return values.
  - MCP07 (M2M OAuth): client_credentials grant type supported via verify_m2m_token().
"""

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass

from mcp.server.auth.provider import AccessToken as MCPAccessToken

logger = logging.getLogger("network-mcp.auth")

# --- JWT library availability check ---

JWT_AVAILABLE = False
try:
    import jwt as pyjwt

    JWT_AVAILABLE = True
except ImportError:
    pyjwt = None  # type: ignore[assignment]

JWT_NOT_INSTALLED_ERROR = "PyJWT is not installed. Install with: pip install network-mcp[auth]"

# --- X.509 / cryptography library availability check ---

CRYPTO_AVAILABLE = False
try:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import NameOID

    CRYPTO_AVAILABLE = True
except ImportError:
    x509 = None  # type: ignore[assignment]
    Encoding = None  # type: ignore[assignment,misc]
    NameOID = None  # type: ignore[assignment]

CRYPTO_NOT_INSTALLED_ERROR = "cryptography is not installed. Install with: pip install network-mcp[mtls]"

# Regex to detect JWT tokens in output (three base64url segments separated by dots)
JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")

# Allowed algorithms — restrict to prevent algorithm confusion attacks
ALLOWED_ALGORITHMS = frozenset({"HS256", "RS256", "ES256", "ES384", "RS384", "RS512", "ES512"})


def redact_tokens_from_output(data: dict) -> dict:
    """Scan tool output dicts and redact any JWT tokens found in string values.

    OWASP MCP01 — Token Mismanagement: ensures tokens never leak in tool responses.
    """
    return _redact_recursive(data)


def _redact_recursive(obj):
    """Recursively redact JWT patterns from nested structures."""
    if isinstance(obj, str):
        return JWT_PATTERN.sub("[JWT_REDACTED]", obj)
    if isinstance(obj, dict):
        return {k: _redact_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_recursive(item) for item in obj]
    return obj


@dataclass
class AccessToken:
    """Parsed and validated access token."""

    token: str
    client_id: str
    scopes: list[str]
    expires_at: int | None = None
    grant_type: str | None = None
    refresh_token: str | None = None

    @property
    def is_expired(self) -> bool:
        """Check if the token has expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def has_scope(self, scope: str) -> bool:
        """Check if the token has a specific scope."""
        return scope in self.scopes

    @property
    def is_m2m(self) -> bool:
        """Check if this is a machine-to-machine (client_credentials) token."""
        return self.grant_type == "client-credentials"


class TokenRevocationStore:
    """In-memory store for revoked JWT token IDs (JTIs).

    Thread-safe set of revoked JTIs with TTL-based cleanup. Each revoked JTI is
    stored alongside its original token expiration time so that entries can be
    garbage-collected once they would have expired anyway.
    """

    def __init__(self) -> None:
        self._revoked: dict[str, int] = {}  # jti -> exp timestamp
        self._lock = threading.Lock()

    def revoke(self, jti: str, exp: int) -> None:
        """Add a JTI to the revoked set.

        Args:
            jti: The JWT ID to revoke.
            exp: The original token expiration timestamp (used for TTL cleanup).
        """
        with self._lock:
            self._revoked[jti] = exp

    def is_revoked(self, jti: str) -> bool:
        """Check if a JTI has been revoked.

        Args:
            jti: The JWT ID to check.

        Returns:
            True if the JTI is in the revocation set.
        """
        with self._lock:
            return jti in self._revoked

    def cleanup(self) -> int:
        """Remove expired entries from the revocation store.

        Returns:
            Number of entries removed.
        """
        now = int(time.time())
        with self._lock:
            expired = [jti for jti, exp in self._revoked.items() if exp <= now]
            for jti in expired:
                del self._revoked[jti]
            return len(expired)

    @property
    def size(self) -> int:
        """Return the number of entries in the revocation store."""
        with self._lock:
            return len(self._revoked)


class JWKSKeyManager:
    """Manage JWKS (JSON Web Key Set) fetching and caching.

    Fetches public keys from an identity provider's JWKS endpoint,
    caches them with a configurable TTL, and supports key lookup by 'kid'.
    """

    def __init__(self, jwks_url: str, refresh_interval: int = 3600):
        self._jwks_url = jwks_url
        self._refresh_interval = refresh_interval
        self._keys: dict[str, dict] = {}
        self._last_refresh: float = 0.0
        self._lock = threading.Lock()

    @property
    def jwks_url(self) -> str:
        return self._jwks_url

    def _needs_refresh(self) -> bool:
        return time.time() - self._last_refresh > self._refresh_interval

    def fetch_jwks(self, http_client=None) -> dict[str, dict]:
        """Fetch JWKS from the identity provider endpoint.

        Args:
            http_client: An httpx-compatible client with a .get() method.
                         If None, creates a temporary httpx.Client.

        Returns:
            Dict mapping 'kid' to JWK dict.
        """
        with self._lock:
            if not self._needs_refresh() and self._keys:
                return self._keys

            try:
                if http_client is None:
                    try:
                        import httpx
                    except ImportError:
                        logger.error("httpx is required for JWKS support. Install with: pip install httpx")
                        return self._keys
                    http_client = httpx.Client(timeout=10.0)
                    should_close = True
                else:
                    should_close = False

                try:
                    response = http_client.get(self._jwks_url)
                    response.raise_for_status()
                    jwks_data = response.json()
                finally:
                    if should_close:
                        http_client.close()

                new_keys: dict[str, dict] = {}
                for key in jwks_data.get("keys", []):
                    kid = key.get("kid")
                    kty = key.get("kty", "")
                    if kid and kty in ("RSA", "EC"):
                        new_keys[kid] = key

                self._keys = new_keys
                self._last_refresh = time.time()
                logger.info("JWKS refreshed: %d keys loaded from %s", len(new_keys), self._jwks_url)
            except Exception:
                logger.exception("Failed to fetch JWKS from %s", self._jwks_url)

            return self._keys

    def get_signing_key(self, kid: str, http_client=None) -> dict | None:
        """Get a specific signing key by 'kid' header.

        If the key is not found in cache, triggers a refresh and retries.
        """
        keys = self.fetch_jwks(http_client)
        if kid in keys:
            return keys[kid]

        # Key not found — force refresh and retry (key rotation scenario)
        with self._lock:
            self._last_refresh = 0.0
        keys = self.fetch_jwks(http_client)
        return keys.get(kid)

    def clear_cache(self) -> None:
        """Clear the JWKS cache. Used in tests."""
        with self._lock:
            self._keys.clear()
            self._last_refresh = 0.0


class JWTTokenVerifier:
    """Verify JWT tokens issued by an authorization server.

    Supports both static secret key verification (HS256) and JWKS-based
    public key verification (RS256/ES256) with automatic key rotation.
    """

    def __init__(
        self,
        secret_key: str = "",
        issuer: str | None = None,
        audience: str = "network-mcp",
        algorithms: list[str] | None = None,
        jwks_url: str = "",
        jwks_refresh_interval: int = 3600,
        revocation_store: TokenRevocationStore | None = None,
    ):
        self.secret_key = secret_key
        self.issuer = issuer
        self.audience = audience
        self.algorithms = algorithms or ["HS256"]
        self._revocation_store = revocation_store

        # Validate algorithms against allowlist
        for alg in self.algorithms:
            if alg not in ALLOWED_ALGORITHMS:
                raise ValueError(f"Unsupported algorithm: {alg!r}. Allowed: {sorted(ALLOWED_ALGORITHMS)}")

        if "HS256" in self.algorithms and any(a.startswith(("RS", "ES")) for a in self.algorithms):
            logger.warning(
                "Both symmetric (HS*) and asymmetric (RS*/ES*) algorithms are enabled. "
                "This creates a risk of algorithm confusion attacks. "
                "Use a single algorithm family for production deployments."
            )

        # JWKS support
        self._jwks_manager: JWKSKeyManager | None = None
        if jwks_url:
            self._jwks_manager = JWKSKeyManager(jwks_url, jwks_refresh_interval)

    def _decode_with_jwks(self, token: str, header: dict) -> dict | None:
        """Attempt to decode token using JWKS public key.

        Returns decoded payload or None if JWKS lookup fails.
        """
        if not self._jwks_manager:
            return None

        kid = header.get("kid")
        if not kid:
            return None

        jwk_data = self._jwks_manager.get_signing_key(kid)
        if not jwk_data:
            logger.debug("No JWKS key found for kid=%s", kid)
            return None

        try:
            from jwt import PyJWK

            signing_key = PyJWK(jwk_data)
        except Exception:
            logger.exception("Failed to construct signing key from JWK kid=%s", kid)
            return None

        decode_kwargs: dict = {
            "algorithms": self.algorithms,
        }
        if self.issuer:
            decode_kwargs["issuer"] = self.issuer
        if self.audience:
            decode_kwargs["audience"] = self.audience

        return pyjwt.decode(token, signing_key.key, **decode_kwargs)

    def verify_token(self, token: str) -> AccessToken | None:
        """Verify a JWT token and return an AccessToken if valid.

        Tries JWKS-based verification first (if configured and token has 'kid'),
        then falls back to static secret key.

        Args:
            token: The raw JWT token string.

        Returns:
            AccessToken if the token is valid, None otherwise.
        """
        if not JWT_AVAILABLE:
            logger.error(JWT_NOT_INSTALLED_ERROR)
            return None

        try:
            header = pyjwt.get_unverified_header(token)

            # Enforce algorithm — reject tokens requesting an algorithm we don't allow
            token_alg = header.get("alg", "")
            if token_alg not in self.algorithms:
                logger.warning("Token requests algorithm %r not in allowed list %s", token_alg, self.algorithms)
                return None

            payload = None

            # Try JWKS first if configured and token has 'kid'
            if self._jwks_manager and header.get("kid"):
                payload = self._decode_with_jwks(token, header)

            # Fallback to static secret key
            if payload is None and self.secret_key:
                decode_kwargs: dict = {
                    "algorithms": self.algorithms,
                }
                if self.issuer:
                    decode_kwargs["issuer"] = self.issuer
                if self.audience:
                    decode_kwargs["audience"] = self.audience
                payload = pyjwt.decode(token, self.secret_key, **decode_kwargs)

            if payload is None:
                logger.debug("JWT verification failed: no valid key found")
                return None

            # Check JTI-based revocation
            jti = payload.get("jti")
            if jti and self._revocation_store and self._revocation_store.is_revoked(jti):
                logger.debug("JWT rejected: token JTI %s has been revoked", jti)
                return None

            return self._extract_access_token(token, payload)
        except (
            pyjwt.DecodeError,
            pyjwt.InvalidTokenError,
            pyjwt.ExpiredSignatureError,
            pyjwt.InvalidAudienceError,
            pyjwt.InvalidIssuerError,
            pyjwt.InvalidAlgorithmError,
        ) as e:
            logger.debug("JWT verification failed: %s", e)
            return None

    def verify_m2m_token(self, token: str) -> AccessToken | None:
        """Verify a machine-to-machine (client_credentials) JWT.

        OWASP MCP07 — M2M OAuth: supports client_credentials grant type.
        M2M tokens typically have 'gty': 'client-credentials' claim (Auth0 convention)
        or no 'sub' claim with 'client_id' present.

        Args:
            token: The raw JWT token string.

        Returns:
            AccessToken if valid M2M token, None otherwise.
        """
        result = self.verify_token(token)
        if result is None:
            return None

        # M2M tokens are identified by grant_type or absence of user-level 'sub'
        if result.grant_type != "client-credentials":
            logger.debug("Token is not a client_credentials grant (gty=%s)", result.grant_type)
            return None

        return result

    def _extract_access_token(self, token: str, payload: dict) -> AccessToken:
        """Extract an AccessToken from a decoded JWT payload."""
        # Extract scopes from either 'scopes' (list) or 'scope' (space-separated string)
        scopes = payload.get("scopes")
        if scopes is None:
            scope_str = payload.get("scope", "")
            scopes = scope_str.split() if scope_str else []

        # Extract grant type (Auth0 uses 'gty', others may use 'grant_type')
        grant_type = payload.get("gty", payload.get("grant_type"))

        return AccessToken(
            token=token,
            client_id=payload.get("client_id", payload.get("sub", "unknown")),
            scopes=scopes,
            expires_at=payload.get("exp"),
            grant_type=grant_type,
        )


class TokenRefreshManager:
    """Manage JWT refresh token issuance, validation, and revocation.

    Issues refresh tokens (JWT with typ=refresh) and exchanges them for
    new access tokens. Tracks active refresh token JTIs for revocation.
    """

    def __init__(
        self,
        secret_key: str,
        issuer: str | None = None,
        audience: str = "network-mcp",
        access_token_ttl: int = 900,
        refresh_token_ttl: int = 14400,
        revocation_store: TokenRevocationStore | None = None,
    ):
        if not JWT_AVAILABLE:
            raise RuntimeError(JWT_NOT_INSTALLED_ERROR)
        self._secret_key = secret_key
        self._issuer = issuer
        self._audience = audience
        self._access_token_ttl = access_token_ttl
        self._refresh_token_ttl = refresh_token_ttl
        self._revocation_store = revocation_store or TokenRevocationStore()
        self._active_refresh_jtis: dict[str, int] = {}  # jti -> exp
        self._lock = threading.Lock()

    @property
    def revocation_store(self) -> TokenRevocationStore:
        """Return the revocation store used by this manager."""
        return self._revocation_store

    def issue_refresh_token(self, access_token: AccessToken) -> str:
        """Generate a refresh token for the given access token.

        Args:
            access_token: The validated access token to issue a refresh token for.

        Returns:
            Signed JWT refresh token string.
        """
        now = int(time.time())
        jti = str(uuid.uuid4())
        exp = now + self._refresh_token_ttl

        payload: dict = {
            "typ": "refresh",
            "jti": jti,
            "sub": access_token.client_id,
            "scopes": access_token.scopes,
            "iat": now,
            "exp": exp,
        }
        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience

        token = pyjwt.encode(payload, self._secret_key, algorithm="HS256")

        with self._lock:
            self._active_refresh_jtis[jti] = exp

        logger.debug("Issued refresh token jti=%s for client=%s (exp=%d)", jti, access_token.client_id, exp)
        return token

    def refresh_access_token(self, refresh_token: str) -> AccessToken:
        """Validate a refresh token and issue a new access token.

        Args:
            refresh_token: The signed JWT refresh token string.

        Returns:
            New AccessToken with the same scopes as the original.

        Raises:
            ValueError: If the refresh token is invalid, expired, or revoked.
        """
        try:
            decode_kwargs: dict = {"algorithms": ["HS256"]}
            if self._issuer:
                decode_kwargs["issuer"] = self._issuer
            if self._audience:
                decode_kwargs["audience"] = self._audience

            payload = pyjwt.decode(refresh_token, self._secret_key, **decode_kwargs)
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError) as e:
            raise ValueError(f"Invalid refresh token: {e}") from e

        # Must be a refresh token
        if payload.get("typ") != "refresh":
            raise ValueError("Token is not a refresh token")

        jti = payload.get("jti")
        if not jti:
            raise ValueError("Refresh token missing JTI claim")

        # Check revocation
        if self._revocation_store.is_revoked(jti):
            raise ValueError("Refresh token has been revoked")

        # Check that this JTI is one we issued
        with self._lock:
            if jti not in self._active_refresh_jtis:
                raise ValueError("Refresh token JTI not recognized")

        # Extract scopes
        scopes = payload.get("scopes", [])
        client_id = payload.get("sub", "unknown")

        # Issue new access token
        now = int(time.time())
        access_jti = str(uuid.uuid4())
        access_exp = now + self._access_token_ttl

        access_payload: dict = {
            "sub": client_id,
            "jti": access_jti,
            "scopes": scopes,
            "iat": now,
            "exp": access_exp,
        }
        if self._issuer:
            access_payload["iss"] = self._issuer
        if self._audience:
            access_payload["aud"] = self._audience

        new_token = pyjwt.encode(access_payload, self._secret_key, algorithm="HS256")

        logger.debug("Refreshed access token for client=%s (new_jti=%s)", client_id, access_jti)
        return AccessToken(
            token=new_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=access_exp,
        )

    def revoke_refresh_token(self, jti: str) -> None:
        """Revoke a refresh token by its JTI.

        Args:
            jti: The JWT ID of the refresh token to revoke.
        """
        with self._lock:
            exp = self._active_refresh_jtis.pop(jti, None)

        if exp is not None:
            self._revocation_store.revoke(jti, exp)
            logger.debug("Revoked refresh token jti=%s", jti)
        else:
            logger.debug("Revoke requested for unknown refresh token jti=%s", jti)

    def cleanup(self) -> int:
        """Remove expired refresh token JTIs from the active set.

        Returns:
            Number of entries removed.
        """
        now = int(time.time())
        with self._lock:
            expired = [jti for jti, exp in self._active_refresh_jtis.items() if exp <= now]
            for jti in expired:
                del self._active_refresh_jtis[jti]
        revocation_cleaned = self._revocation_store.cleanup()
        total = len(expired) + revocation_cleaned
        if total > 0:
            logger.debug(
                "Token cleanup: removed %d expired active JTIs, %d expired revoked JTIs",
                len(expired),
                revocation_cleaned,
            )
        return total

    @property
    def active_count(self) -> int:
        """Return the number of active (non-expired) refresh token JTIs."""
        with self._lock:
            return len(self._active_refresh_jtis)


def create_token_verifier(
    secret_key: str = "",
    issuer_url: str | None = None,
    audience: str = "network-mcp",
    algorithms: list[str] | None = None,
    jwks_url: str = "",
    jwks_refresh_interval: int = 3600,
    revocation_store: TokenRevocationStore | None = None,
) -> JWTTokenVerifier:
    """Create a JWTTokenVerifier from configuration.

    Args:
        secret_key: Secret key or public key for JWT verification.
        issuer_url: Expected token issuer URL.
        audience: Expected token audience.
        algorithms: List of allowed algorithms.
        jwks_url: JWKS endpoint URL for public key discovery.
        jwks_refresh_interval: Seconds between JWKS cache refreshes.
        revocation_store: Optional TokenRevocationStore for JTI-based revocation.

    Returns:
        Configured JWTTokenVerifier instance.
    """
    return JWTTokenVerifier(
        secret_key=secret_key,
        issuer=issuer_url,
        audience=audience,
        algorithms=algorithms,
        jwks_url=jwks_url,
        jwks_refresh_interval=jwks_refresh_interval,
        revocation_store=revocation_store,
    )


class CertificateVerifier:
    """Verify mTLS client certificates and extract identity for RBAC.

    Parses X.509 client certificates presented during TLS handshake, validates
    them against a CA certificate, and maps certificate subject attributes to
    AccessToken instances with appropriate scopes.

    Requires the ``cryptography`` library (pip install network-mcp[mtls]).
    """

    def __init__(
        self,
        ca_cert_path: str | None = None,
        allowed_cns: list[str] | None = None,
        allowed_ous: list[str] | None = None,
        scope_mapping: dict[str, list[str]] | None = None,
    ) -> None:
        if not CRYPTO_AVAILABLE:
            raise RuntimeError(CRYPTO_NOT_INSTALLED_ERROR)

        self._ca_cert = None
        if ca_cert_path:
            import pathlib

            ca_pem = pathlib.Path(ca_cert_path).read_bytes()
            self._ca_cert = x509.load_pem_x509_certificate(ca_pem)

        self._allowed_cns = set(allowed_cns) if allowed_cns else None
        self._allowed_ous = set(allowed_ous) if allowed_ous else None
        self._scope_mapping = scope_mapping or {}

    def verify_certificate(self, cert_pem: str) -> dict:
        """Parse and validate a PEM-encoded client certificate.

        Args:
            cert_pem: PEM-encoded X.509 certificate string.

        Returns:
            Dict with keys: cn, ou, serial, not_valid_after, issuer_cn.

        Raises:
            ValueError: If the certificate is invalid, expired, or not from the trusted CA.
        """
        if not CRYPTO_AVAILABLE:
            raise RuntimeError(CRYPTO_NOT_INSTALLED_ERROR)

        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode() if isinstance(cert_pem, str) else cert_pem)
        except Exception as e:
            raise ValueError(f"Invalid certificate: {e}") from e

        # Check expiry
        import datetime

        now = datetime.datetime.now(datetime.UTC)
        if now > cert.not_valid_after_utc:
            raise ValueError(f"Certificate expired at {cert.not_valid_after_utc.isoformat()}")
        if now < cert.not_valid_before_utc:
            raise ValueError(f"Certificate not valid until {cert.not_valid_before_utc.isoformat()}")

        # Verify issuer matches CA certificate if provided
        if self._ca_cert is not None:
            if cert.issuer != self._ca_cert.subject:
                raise ValueError(
                    f"Certificate issuer does not match trusted CA. "
                    f"Expected: {self._ca_cert.subject.rfc4514_string()}, "
                    f"Got: {cert.issuer.rfc4514_string()}"
                )

        # Extract subject fields
        cn = self._get_name_attribute(cert.subject, NameOID.COMMON_NAME)
        ou = self._get_name_attribute(cert.subject, NameOID.ORGANIZATIONAL_UNIT_NAME)
        issuer_cn = self._get_name_attribute(cert.issuer, NameOID.COMMON_NAME)

        # Check CN allowlist
        if self._allowed_cns is not None and cn not in self._allowed_cns:
            raise ValueError(f"Certificate CN '{cn}' not in allowed list: {sorted(self._allowed_cns)}")

        # Check OU allowlist
        if self._allowed_ous is not None and ou and ou not in self._allowed_ous:
            raise ValueError(f"Certificate OU '{ou}' not in allowed list: {sorted(self._allowed_ous)}")

        return {
            "cn": cn,
            "ou": ou,
            "serial": str(cert.serial_number),
            "not_valid_after": cert.not_valid_after_utc.isoformat(),
            "issuer_cn": issuer_cn,
        }

    def extract_identity(self, cert_info: dict) -> AccessToken:
        """Map certificate subject attributes to an AccessToken with scopes.

        Scope resolution order:
        1. Explicit scope_mapping for the CN
        2. Default scope: ["network:read"]

        Args:
            cert_info: Dict returned by verify_certificate().

        Returns:
            AccessToken with client_id set to "mtls:<CN>" and mapped scopes.
        """
        cn = cert_info.get("cn", "unknown")
        scopes = self._scope_mapping.get(cn, ["network:read"])

        return AccessToken(
            token=f"mtls-cert:{cert_info.get('serial', 'unknown')}",
            client_id=f"mtls:{cn}",
            scopes=scopes,
        )

    @staticmethod
    def _get_name_attribute(name: "x509.Name", oid: "x509.ObjectIdentifier") -> str | None:
        """Extract a single attribute value from an X.509 Name."""
        try:
            attrs = name.get_attributes_for_oid(oid)
            return attrs[0].value if attrs else None
        except Exception:
            return None

    @classmethod
    def from_settings(
        cls,
        ca_cert_path: str | None = None,
        allowed_cns_str: str | None = None,
        allowed_ous_str: str | None = None,
        scope_mapping_json: str | None = None,
    ) -> "CertificateVerifier":
        """Create a CertificateVerifier from config string settings.

        Args:
            ca_cert_path: Path to CA certificate file.
            allowed_cns_str: Comma-separated allowed Common Names.
            allowed_ous_str: Comma-separated allowed Org Units.
            scope_mapping_json: JSON string mapping CN -> list of scopes.

        Returns:
            Configured CertificateVerifier instance.
        """
        allowed_cns = [cn.strip() for cn in allowed_cns_str.split(",") if cn.strip()] if allowed_cns_str else None
        allowed_ous = [ou.strip() for ou in allowed_ous_str.split(",") if ou.strip()] if allowed_ous_str else None
        scope_mapping = json.loads(scope_mapping_json) if scope_mapping_json else None

        return cls(
            ca_cert_path=ca_cert_path,
            allowed_cns=allowed_cns,
            allowed_ous=allowed_ous,
            scope_mapping=scope_mapping,
        )


class TokenVerifier:
    """Adapter that bridges JWTTokenVerifier to the MCP SDK's TokenVerifier protocol.

    The MCP SDK expects ``async def verify_token(token: str) -> mcp.AccessToken | None``.
    This class wraps our synchronous JWTTokenVerifier and converts the result to the
    MCP SDK's Pydantic-based AccessToken model.

    When an APIKeyManager is attached, tokens starting with the API key prefix
    are validated via API key lookup instead of JWT verification.

    When a CertificateVerifier is attached, client certificates extracted from
    the request can be validated and mapped to access tokens as an alternative
    to JWT bearer tokens.
    """

    def __init__(self, jwt_verifier: JWTTokenVerifier) -> None:
        self._jwt_verifier = jwt_verifier
        self._api_key_manager = None
        self._api_key_prefix: str = "nmcp_"
        self._cert_verifier: CertificateVerifier | None = None

    def set_api_key_manager(self, manager, prefix: str = "nmcp_") -> None:
        """Attach an APIKeyManager for API key authentication.

        Args:
            manager: An APIKeyManager instance.
            prefix: The API key prefix to detect (default: "nmcp_").
        """
        self._api_key_manager = manager
        self._api_key_prefix = prefix

    def set_cert_verifier(self, cert_verifier: CertificateVerifier) -> None:
        """Attach a CertificateVerifier for mTLS client certificate authentication.

        Args:
            cert_verifier: A CertificateVerifier instance.
        """
        self._cert_verifier = cert_verifier

    def verify_client_certificate(self, cert_pem: str) -> MCPAccessToken | None:
        """Verify a client certificate and return an MCP AccessToken if valid.

        Args:
            cert_pem: PEM-encoded client certificate string.

        Returns:
            MCPAccessToken if certificate is valid, None otherwise.
        """
        if self._cert_verifier is None:
            return None
        try:
            cert_info = self._cert_verifier.verify_certificate(cert_pem)
            local_token = self._cert_verifier.extract_identity(cert_info)
            return MCPAccessToken(
                token=local_token.token,
                client_id=local_token.client_id,
                scopes=local_token.scopes,
                expires_at=local_token.expires_at,
            )
        except ValueError as e:
            logger.debug("mTLS certificate verification failed: %s", e)
            return None

    async def verify_token(self, token: str) -> MCPAccessToken | None:
        """Verify a bearer token and return an MCP AccessToken if valid.

        If the token starts with the API key prefix and an APIKeyManager is
        attached, validates as an API key. Otherwise falls through to JWT.

        Conforms to ``mcp.server.auth.provider.TokenVerifier`` protocol.
        """
        # API key path
        if self._api_key_manager is not None and token.startswith(self._api_key_prefix):
            api_key = self._api_key_manager.validate_key(token)
            if api_key is None:
                return None
            # Convert APIKey expiration to epoch timestamp for MCPAccessToken
            expires_at = None
            if api_key.expires_at:
                try:
                    from datetime import datetime

                    exp_dt = datetime.fromisoformat(api_key.expires_at)
                    expires_at = int(exp_dt.timestamp())
                except (ValueError, TypeError):
                    pass
            return MCPAccessToken(
                token=token,
                client_id=f"apikey:{api_key.key_id}",
                scopes=api_key.scopes,
                expires_at=expires_at,
            )

        # JWT path
        local_token = self._jwt_verifier.verify_token(token)
        if local_token is None:
            return None
        if local_token.is_expired:
            return None
        return MCPAccessToken(
            token=token,
            client_id=local_token.client_id,
            scopes=local_token.scopes,
            expires_at=local_token.expires_at,
        )


# Backward compatibility alias
EosTokenVerifier = TokenVerifier
