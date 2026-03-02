"""Tests for OAuth 2.1 well-known metadata endpoint generation.

Covers:
- OAuthMetadata (protected resource document)
- OAuthServerMetadata (authorization server document)
- All required fields present
- PKCE S256 method listed
- Scope listing
- Optional fields
"""

from network_mcp.oauth import SUPPORTED_SCOPES, OAuthMetadata, OAuthServerMetadata

# =========================================================================
# OAuthMetadata (Protected Resource) Tests
# =========================================================================


class TestOAuthMetadata:
    """Tests for the .well-known/oauth-protected-resource document."""

    def test_required_fields_present(self):
        """Document should contain all required fields per spec."""
        meta = OAuthMetadata(
            resource_url="https://network-mcp.example.com",
            authorization_servers=["https://auth.example.com"],
        )
        doc = meta.to_dict()

        assert "resource" in doc
        assert "authorization_servers" in doc
        assert "scopes_supported" in doc
        assert "bearer_methods_supported" in doc

    def test_resource_url(self):
        """Resource field should match the configured URL."""
        meta = OAuthMetadata(resource_url="https://mcp.corp.com")
        doc = meta.to_dict()

        assert doc["resource"] == "https://mcp.corp.com"

    def test_authorization_servers(self):
        """Authorization servers should be listed correctly."""
        meta = OAuthMetadata(
            resource_url="https://mcp.corp.com",
            authorization_servers=["https://auth1.com", "https://auth2.com"],
        )
        doc = meta.to_dict()

        assert doc["authorization_servers"] == ["https://auth1.com", "https://auth2.com"]

    def test_empty_authorization_servers(self):
        """No auth servers should result in empty list."""
        meta = OAuthMetadata(resource_url="https://mcp.corp.com")
        doc = meta.to_dict()

        assert doc["authorization_servers"] == []

    def test_scopes_supported(self):
        """All four network scopes should be listed."""
        meta = OAuthMetadata(resource_url="https://mcp.corp.com")
        doc = meta.to_dict()

        assert "network:read" in doc["scopes_supported"]
        assert "network:write" in doc["scopes_supported"]
        assert "network:admin" in doc["scopes_supported"]
        assert "network:audit" in doc["scopes_supported"]

    def test_bearer_methods(self):
        """Only 'header' bearer method should be supported."""
        meta = OAuthMetadata(resource_url="https://mcp.corp.com")
        doc = meta.to_dict()

        assert doc["bearer_methods_supported"] == ["header"]

    def test_resource_documentation_included(self):
        """Documentation URL should be included when provided."""
        meta = OAuthMetadata(
            resource_url="https://mcp.corp.com",
            resource_documentation="https://docs.mcp.corp.com",
        )
        doc = meta.to_dict()

        assert doc["resource_documentation"] == "https://docs.mcp.corp.com"

    def test_resource_documentation_absent(self):
        """Documentation URL should be absent when not provided."""
        meta = OAuthMetadata(resource_url="https://mcp.corp.com")
        doc = meta.to_dict()

        assert "resource_documentation" not in doc


# =========================================================================
# OAuthServerMetadata (Authorization Server) Tests
# =========================================================================


class TestOAuthServerMetadata:
    """Tests for the .well-known/oauth-authorization-server document."""

    def test_required_fields_present(self):
        """Document should contain all required fields per RFC 8414."""
        meta = OAuthServerMetadata(issuer="https://auth.example.com")
        doc = meta.to_dict()

        assert "issuer" in doc
        assert "authorization_endpoint" in doc
        assert "token_endpoint" in doc
        assert "scopes_supported" in doc
        assert "response_types_supported" in doc
        assert "code_challenge_methods_supported" in doc

    def test_issuer(self):
        """Issuer should match the configured URL."""
        meta = OAuthServerMetadata(issuer="https://auth.corp.com")
        doc = meta.to_dict()

        assert doc["issuer"] == "https://auth.corp.com"

    def test_default_endpoints(self):
        """Endpoints should default to issuer-based paths."""
        meta = OAuthServerMetadata(issuer="https://auth.corp.com")
        doc = meta.to_dict()

        assert doc["authorization_endpoint"] == "https://auth.corp.com/authorize"
        assert doc["token_endpoint"] == "https://auth.corp.com/token"

    def test_custom_endpoints(self):
        """Custom endpoints should override defaults."""
        meta = OAuthServerMetadata(
            issuer="https://auth.corp.com",
            authorization_endpoint="https://auth.corp.com/oauth/authorize",
            token_endpoint="https://auth.corp.com/oauth/token",
        )
        doc = meta.to_dict()

        assert doc["authorization_endpoint"] == "https://auth.corp.com/oauth/authorize"
        assert doc["token_endpoint"] == "https://auth.corp.com/oauth/token"

    def test_pkce_s256_only(self):
        """Only S256 code challenge method should be supported (no plain)."""
        meta = OAuthServerMetadata(issuer="https://auth.corp.com")
        doc = meta.to_dict()

        assert doc["code_challenge_methods_supported"] == ["S256"]
        assert "plain" not in doc["code_challenge_methods_supported"]

    def test_response_types(self):
        """Only 'code' response type (authorization code flow) should be listed."""
        meta = OAuthServerMetadata(issuer="https://auth.corp.com")
        doc = meta.to_dict()

        assert doc["response_types_supported"] == ["code"]

    def test_grant_types_supported(self):
        """Authorization code, client credentials, and refresh token grants should be listed."""
        meta = OAuthServerMetadata(issuer="https://auth.corp.com")
        doc = meta.to_dict()

        assert "authorization_code" in doc["grant_types_supported"]
        assert "client_credentials" in doc["grant_types_supported"]
        assert "refresh_token" in doc["grant_types_supported"]

    def test_token_endpoint_auth_methods(self):
        """Token endpoint auth methods should include standard methods."""
        meta = OAuthServerMetadata(issuer="https://auth.corp.com")
        doc = meta.to_dict()

        auth_methods = doc["token_endpoint_auth_methods_supported"]
        assert "client_secret_basic" in auth_methods
        assert "client_secret_post" in auth_methods
        assert "private_key_jwt" in auth_methods

    def test_scopes_match_resource_scopes(self):
        """Auth server scopes should match the protected resource scopes."""
        meta = OAuthServerMetadata(issuer="https://auth.corp.com")
        doc = meta.to_dict()

        assert doc["scopes_supported"] == SUPPORTED_SCOPES


# =========================================================================
# SUPPORTED_SCOPES Constant Tests
# =========================================================================


class TestSupportedScopes:
    """Tests for the SUPPORTED_SCOPES constant."""

    def test_all_four_scopes(self):
        """All four network scopes should be defined."""
        assert len(SUPPORTED_SCOPES) == 4
        assert "network:read" in SUPPORTED_SCOPES
        assert "network:write" in SUPPORTED_SCOPES
        assert "network:admin" in SUPPORTED_SCOPES
        assert "network:audit" in SUPPORTED_SCOPES

    def test_scopes_are_strings(self):
        """All scopes should be strings."""
        for scope in SUPPORTED_SCOPES:
            assert isinstance(scope, str)
