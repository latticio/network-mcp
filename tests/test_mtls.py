"""Tests for mTLS client certificate validation.

Covers:
- Certificate parsing (valid cert, expired cert, wrong CA)
- CN/OU allowlisting
- Scope mapping from certificate
- Integration with RBAC
- Fallback to JWT when no cert
- CertificateVerifier.from_settings() factory
- TokenVerifier.verify_client_certificate() integration
"""

import datetime
import json
from unittest.mock import MagicMock

import pytest

# Skip all tests if cryptography is not installed
cryptography = pytest.importorskip("cryptography", reason="cryptography not installed")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from network_mcp.auth import AccessToken, CertificateVerifier  # noqa: E402

# =========================================================================
# Test certificate generation helpers
# =========================================================================


def _generate_key():
    """Generate an RSA private key for testing."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_cert(
    cn: str = "test-client",
    ou: str | None = "engineering",
    issuer_cn: str = "Test CA",
    issuer_key=None,
    subject_key=None,
    not_valid_before: datetime.datetime | None = None,
    not_valid_after: datetime.datetime | None = None,
    serial_number: int | None = None,
):
    """Build a self-signed or CA-signed X.509 certificate for testing."""
    if subject_key is None:
        subject_key = _generate_key()
    if issuer_key is None:
        issuer_key = subject_key

    now = datetime.datetime.now(datetime.UTC)
    if not_valid_before is None:
        not_valid_before = now - datetime.timedelta(days=1)
    if not_valid_after is None:
        not_valid_after = now + datetime.timedelta(days=365)

    subject_attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if ou:
        subject_attrs.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou))

    issuer_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)])
    subject_name = x509.Name(subject_attrs)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject_name)
        .issuer_name(issuer_name)
        .public_key(subject_key.public_key())
        .serial_number(serial_number or x509.random_serial_number())
        .not_valid_before(not_valid_before)
        .not_valid_after(not_valid_after)
    )

    cert = builder.sign(issuer_key, hashes.SHA256())
    return cert, subject_key


def _cert_to_pem(cert) -> str:
    """Convert an X.509 certificate to PEM string."""
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _generate_ca():
    """Generate a CA certificate and key pair."""
    ca_key = _generate_key()
    ca_cert, _ = _build_cert(
        cn="Test CA",
        ou=None,
        issuer_cn="Test CA",
        issuer_key=ca_key,
        subject_key=ca_key,
    )
    return ca_cert, ca_key


@pytest.fixture
def ca_pair():
    """Fixture that returns (ca_cert, ca_key) for a test CA."""
    return _generate_ca()


@pytest.fixture
def ca_cert_path(ca_pair, tmp_path):
    """Write the CA cert to a temp file and return its path."""
    ca_cert, _ = ca_pair
    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    return str(ca_path)


@pytest.fixture
def client_cert_pem(ca_pair):
    """Generate a valid client cert signed by the test CA."""
    ca_cert, ca_key = ca_pair
    cert, _ = _build_cert(
        cn="test-client",
        ou="engineering",
        issuer_cn="Test CA",
        issuer_key=ca_key,
    )
    return _cert_to_pem(cert)


@pytest.fixture
def expired_cert_pem(ca_pair):
    """Generate an expired client cert signed by the test CA."""
    ca_cert, ca_key = ca_pair
    now = datetime.datetime.now(datetime.UTC)
    cert, _ = _build_cert(
        cn="expired-client",
        ou="engineering",
        issuer_cn="Test CA",
        issuer_key=ca_key,
        not_valid_before=now - datetime.timedelta(days=365),
        not_valid_after=now - datetime.timedelta(days=1),
    )
    return _cert_to_pem(cert)


@pytest.fixture
def wrong_ca_cert_pem():
    """Generate a client cert signed by a different CA."""
    other_ca_key = _generate_key()
    cert, _ = _build_cert(
        cn="wrong-ca-client",
        ou="engineering",
        issuer_cn="Other CA",
        issuer_key=other_ca_key,
    )
    return _cert_to_pem(cert)


# =========================================================================
# Certificate Parsing Tests
# =========================================================================


class TestCertificateVerifierParsing:
    """Tests for certificate parsing and validation."""

    def test_valid_certificate(self, ca_cert_path, client_cert_pem):
        """Valid cert from trusted CA should parse successfully."""
        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        result = verifier.verify_certificate(client_cert_pem)

        assert result["cn"] == "test-client"
        assert result["ou"] == "engineering"
        assert result["issuer_cn"] == "Test CA"
        assert result["serial"] is not None
        assert result["not_valid_after"] is not None

    def test_expired_certificate(self, ca_cert_path, expired_cert_pem):
        """Expired cert should raise ValueError."""
        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)

        with pytest.raises(ValueError, match="Certificate expired"):
            verifier.verify_certificate(expired_cert_pem)

    def test_not_yet_valid_certificate(self, ca_pair, ca_cert_path):
        """Certificate not yet valid should raise ValueError."""
        _, ca_key = ca_pair
        now = datetime.datetime.now(datetime.UTC)
        cert, _ = _build_cert(
            cn="future-client",
            issuer_cn="Test CA",
            issuer_key=ca_key,
            not_valid_before=now + datetime.timedelta(days=1),
            not_valid_after=now + datetime.timedelta(days=365),
        )
        pem = _cert_to_pem(cert)
        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)

        with pytest.raises(ValueError, match="not valid until"):
            verifier.verify_certificate(pem)

    def test_wrong_ca_certificate(self, ca_cert_path, wrong_ca_cert_pem):
        """Cert signed by wrong CA should raise ValueError."""
        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)

        with pytest.raises(ValueError, match="issuer does not match"):
            verifier.verify_certificate(wrong_ca_cert_pem)

    def test_invalid_pem_data(self, ca_cert_path):
        """Garbage PEM data should raise ValueError."""
        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)

        with pytest.raises(ValueError, match="Invalid certificate"):
            verifier.verify_certificate("not-a-certificate")

    def test_no_ca_cert_accepts_any_issuer(self, client_cert_pem):
        """Without CA cert, any valid cert should be accepted."""
        verifier = CertificateVerifier(ca_cert_path=None)
        result = verifier.verify_certificate(client_cert_pem)

        assert result["cn"] == "test-client"

    def test_cert_without_ou(self, ca_pair, ca_cert_path):
        """Certificate without OU should parse successfully."""
        _, ca_key = ca_pair
        cert, _ = _build_cert(
            cn="no-ou-client",
            ou=None,
            issuer_cn="Test CA",
            issuer_key=ca_key,
        )
        pem = _cert_to_pem(cert)
        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        result = verifier.verify_certificate(pem)

        assert result["cn"] == "no-ou-client"
        assert result["ou"] is None


# =========================================================================
# CN/OU Allowlist Tests
# =========================================================================


class TestCNOUAllowlisting:
    """Tests for CN and OU allowlist enforcement."""

    def test_allowed_cn(self, ca_cert_path, client_cert_pem):
        """Cert with CN in allowlist should be accepted."""
        verifier = CertificateVerifier(
            ca_cert_path=ca_cert_path,
            allowed_cns=["test-client", "other-client"],
        )
        result = verifier.verify_certificate(client_cert_pem)
        assert result["cn"] == "test-client"

    def test_denied_cn(self, ca_cert_path, client_cert_pem):
        """Cert with CN not in allowlist should be rejected."""
        verifier = CertificateVerifier(
            ca_cert_path=ca_cert_path,
            allowed_cns=["admin-client"],
        )
        with pytest.raises(ValueError, match="CN.*not in allowed"):
            verifier.verify_certificate(client_cert_pem)

    def test_allowed_ou(self, ca_cert_path, client_cert_pem):
        """Cert with OU in allowlist should be accepted."""
        verifier = CertificateVerifier(
            ca_cert_path=ca_cert_path,
            allowed_ous=["engineering", "operations"],
        )
        result = verifier.verify_certificate(client_cert_pem)
        assert result["ou"] == "engineering"

    def test_denied_ou(self, ca_cert_path, client_cert_pem):
        """Cert with OU not in allowlist should be rejected."""
        verifier = CertificateVerifier(
            ca_cert_path=ca_cert_path,
            allowed_ous=["management"],
        )
        with pytest.raises(ValueError, match="OU.*not in allowed"):
            verifier.verify_certificate(client_cert_pem)

    def test_no_ou_with_ou_allowlist(self, ca_pair, ca_cert_path):
        """Cert without OU should be accepted even with OU allowlist (OU is optional)."""
        _, ca_key = ca_pair
        cert, _ = _build_cert(
            cn="no-ou-client",
            ou=None,
            issuer_cn="Test CA",
            issuer_key=ca_key,
        )
        pem = _cert_to_pem(cert)
        verifier = CertificateVerifier(
            ca_cert_path=ca_cert_path,
            allowed_ous=["engineering"],
        )
        # No OU on cert means the OU check is skipped
        result = verifier.verify_certificate(pem)
        assert result["cn"] == "no-ou-client"


# =========================================================================
# Scope Mapping Tests
# =========================================================================


class TestScopeMapping:
    """Tests for mapping certificate CN to access scopes."""

    def test_default_scope(self, ca_cert_path, client_cert_pem):
        """Cert with no scope mapping should get default network:read."""
        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        cert_info = verifier.verify_certificate(client_cert_pem)
        token = verifier.extract_identity(cert_info)

        assert token.scopes == ["network:read"]
        assert token.client_id == "mtls:test-client"

    def test_mapped_admin_scope(self, ca_cert_path, client_cert_pem):
        """Cert with CN in scope mapping should get mapped scopes."""
        verifier = CertificateVerifier(
            ca_cert_path=ca_cert_path,
            scope_mapping={
                "test-client": ["network:admin"],
                "other-client": ["network:read"],
            },
        )
        cert_info = verifier.verify_certificate(client_cert_pem)
        token = verifier.extract_identity(cert_info)

        assert token.scopes == ["network:admin"]
        assert token.client_id == "mtls:test-client"

    def test_mapped_multiple_scopes(self, ca_cert_path, client_cert_pem):
        """Cert CN can map to multiple scopes."""
        verifier = CertificateVerifier(
            ca_cert_path=ca_cert_path,
            scope_mapping={
                "test-client": ["network:read", "network:write", "network:audit"],
            },
        )
        cert_info = verifier.verify_certificate(client_cert_pem)
        token = verifier.extract_identity(cert_info)

        assert set(token.scopes) == {"network:read", "network:write", "network:audit"}

    def test_token_format(self, ca_cert_path, client_cert_pem):
        """Token should have mtls-cert prefix with serial number."""
        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        cert_info = verifier.verify_certificate(client_cert_pem)
        token = verifier.extract_identity(cert_info)

        assert token.token.startswith("mtls-cert:")
        assert token.client_id.startswith("mtls:")


# =========================================================================
# RBAC Integration Tests
# =========================================================================


class TestRBACIntegration:
    """Tests for mTLS token integration with RBAC scope checking."""

    def test_mtls_token_passes_rbac_read_check(self, ca_cert_path, client_cert_pem):
        """mTLS token with network:read should pass RBAC check for read tools."""
        from network_mcp.rbac import check_authorization

        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        cert_info = verifier.verify_certificate(client_cert_pem)
        token = verifier.extract_identity(cert_info)

        # Build claims dict matching how server.py does it
        token_claims = {
            "scope": " ".join(token.scopes),
            "client_id": token.client_id,
        }
        authorized, msg = check_authorization("eos_get_vlans", token_claims)
        assert authorized

    def test_mtls_token_denied_write_without_scope(self, ca_cert_path, client_cert_pem):
        """mTLS token with only network:read should be denied for write tools."""
        from network_mcp.rbac import check_authorization

        verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        cert_info = verifier.verify_certificate(client_cert_pem)
        token = verifier.extract_identity(cert_info)

        token_claims = {
            "scope": " ".join(token.scopes),
            "client_id": token.client_id,
        }
        authorized, msg = check_authorization("eos_create_vlan", token_claims)
        assert not authorized

    def test_mtls_admin_token_passes_all(self, ca_cert_path, client_cert_pem):
        """mTLS token with network:admin should pass all RBAC checks."""
        from network_mcp.rbac import check_authorization

        verifier = CertificateVerifier(
            ca_cert_path=ca_cert_path,
            scope_mapping={"test-client": ["network:admin"]},
        )
        cert_info = verifier.verify_certificate(client_cert_pem)
        token = verifier.extract_identity(cert_info)

        token_claims = {
            "scope": " ".join(token.scopes),
            "client_id": token.client_id,
        }

        for tool_name in ["eos_get_vlans", "eos_create_vlan", "eos_delete_checkpoint"]:
            authorized, msg = check_authorization(tool_name, token_claims)
            assert authorized, f"Admin token should be authorized for {tool_name}: {msg}"


# =========================================================================
# from_settings Factory Tests
# =========================================================================


class TestFromSettings:
    """Tests for the CertificateVerifier.from_settings() factory method."""

    def test_from_settings_minimal(self):
        """from_settings with no arguments should create a permissive verifier."""
        verifier = CertificateVerifier.from_settings()
        assert verifier._allowed_cns is None
        assert verifier._allowed_ous is None
        assert verifier._scope_mapping == {}

    def test_from_settings_with_cns(self):
        """from_settings should parse comma-separated CN list."""
        verifier = CertificateVerifier.from_settings(allowed_cns_str="admin-client, operator-client, viewer-client")
        assert verifier._allowed_cns == {"admin-client", "operator-client", "viewer-client"}

    def test_from_settings_with_ous(self):
        """from_settings should parse comma-separated OU list."""
        verifier = CertificateVerifier.from_settings(allowed_ous_str="engineering, operations")
        assert verifier._allowed_ous == {"engineering", "operations"}

    def test_from_settings_with_scope_mapping_json(self):
        """from_settings should parse JSON scope mapping."""
        mapping = {
            "admin-client": ["network:admin"],
            "viewer": ["network:read"],
        }
        verifier = CertificateVerifier.from_settings(scope_mapping_json=json.dumps(mapping))
        assert verifier._scope_mapping == mapping

    def test_from_settings_with_ca_cert(self, ca_cert_path):
        """from_settings should load CA certificate from path."""
        verifier = CertificateVerifier.from_settings(ca_cert_path=ca_cert_path)
        assert verifier._ca_cert is not None


# =========================================================================
# TokenVerifier Integration Tests
# =========================================================================


class TestTokenVerifierCertIntegration:
    """Tests for TokenVerifier.verify_client_certificate() integration."""

    def test_verify_client_certificate_valid(self, ca_cert_path, client_cert_pem):
        """TokenVerifier should verify valid client certificates."""
        from network_mcp.auth import JWTTokenVerifier, TokenVerifier

        jwt_verifier = MagicMock(spec=JWTTokenVerifier)
        tv = TokenVerifier(jwt_verifier)

        cert_verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        tv.set_cert_verifier(cert_verifier)

        result = tv.verify_client_certificate(client_cert_pem)
        assert result is not None
        assert result.client_id == "mtls:test-client"
        assert "network:read" in result.scopes

    def test_verify_client_certificate_invalid(self, ca_cert_path):
        """TokenVerifier should return None for invalid certificates."""
        from network_mcp.auth import JWTTokenVerifier, TokenVerifier

        jwt_verifier = MagicMock(spec=JWTTokenVerifier)
        tv = TokenVerifier(jwt_verifier)

        cert_verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        tv.set_cert_verifier(cert_verifier)

        result = tv.verify_client_certificate("not-a-certificate")
        assert result is None

    def test_verify_client_certificate_no_verifier(self):
        """TokenVerifier without cert verifier should return None."""
        from network_mcp.auth import JWTTokenVerifier, TokenVerifier

        jwt_verifier = MagicMock(spec=JWTTokenVerifier)
        tv = TokenVerifier(jwt_verifier)

        result = tv.verify_client_certificate("some-cert-pem")
        assert result is None

    def test_verify_client_certificate_expired(self, ca_cert_path, expired_cert_pem):
        """TokenVerifier should return None for expired certificates."""
        from network_mcp.auth import JWTTokenVerifier, TokenVerifier

        jwt_verifier = MagicMock(spec=JWTTokenVerifier)
        tv = TokenVerifier(jwt_verifier)

        cert_verifier = CertificateVerifier(ca_cert_path=ca_cert_path)
        tv.set_cert_verifier(cert_verifier)

        result = tv.verify_client_certificate(expired_cert_pem)
        assert result is None

    @pytest.mark.asyncio
    async def test_jwt_fallback_when_no_cert(self):
        """Token verify_token should still work via JWT when no cert is provided."""
        from network_mcp.auth import JWTTokenVerifier, TokenVerifier

        jwt_verifier = MagicMock(spec=JWTTokenVerifier)
        jwt_verifier.verify_token.return_value = AccessToken(
            token="jwt-token",
            client_id="jwt-user",
            scopes=["network:read"],
            expires_at=int(datetime.datetime.now(datetime.UTC).timestamp()) + 3600,
        )

        tv = TokenVerifier(jwt_verifier)
        # No cert verifier attached — JWT path should work
        result = await tv.verify_token("jwt-token")

        assert result is not None
        assert result.client_id == "jwt-user"
        jwt_verifier.verify_token.assert_called_once_with("jwt-token")
