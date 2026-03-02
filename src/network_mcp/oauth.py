"""OAuth 2.1 well-known metadata endpoints for MCP resource server discovery.

Generates the ``.well-known/oauth-protected-resource`` and
``.well-known/oauth-authorization-server`` metadata documents per
RFC 8414 (Authorization Server Metadata) and the OAuth 2.1 Protected
Resource Metadata draft.

PKCE support:
  Only ``S256`` is listed as a supported code challenge method.  Plain
  challenges are intentionally excluded per OAuth 2.1 best practice.

These endpoints are registered as custom HTTP routes in server.py when
the HTTP transport is active.
"""

import logging

logger = logging.getLogger("network-mcp.oauth")

# All scopes supported by this resource server
SUPPORTED_SCOPES = [
    "network:read",
    "network:write",
    "network:admin",
    "network:audit",
]


class OAuthMetadata:
    """Generates the ``.well-known/oauth-protected-resource`` document.

    Describes this MCP server as an OAuth 2.1 protected resource,
    listing its authorization servers, supported scopes, and bearer
    token methods.
    """

    def __init__(
        self,
        resource_url: str,
        authorization_servers: list[str] | None = None,
        resource_documentation: str | None = None,
    ) -> None:
        self._resource_url = resource_url
        self._authorization_servers = authorization_servers or []
        self._resource_documentation = resource_documentation

    def to_dict(self) -> dict:
        """Return the protected resource metadata document as a dict."""
        doc: dict = {
            "resource": self._resource_url,
            "authorization_servers": self._authorization_servers,
            "scopes_supported": SUPPORTED_SCOPES,
            "bearer_methods_supported": ["header"],
        }
        if self._resource_documentation:
            doc["resource_documentation"] = self._resource_documentation
        return doc


class OAuthServerMetadata:
    """Generates the ``.well-known/oauth-authorization-server`` document.

    Describes the authorization server metadata for OAuth 2.1 clients,
    including PKCE support with S256 code challenge method.
    """

    def __init__(
        self,
        issuer: str,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
    ) -> None:
        self._issuer = issuer
        self._authorization_endpoint = authorization_endpoint or f"{issuer}/authorize"
        self._token_endpoint = token_endpoint or f"{issuer}/token"

    def to_dict(self) -> dict:
        """Return the authorization server metadata document as a dict."""
        return {
            "issuer": self._issuer,
            "authorization_endpoint": self._authorization_endpoint,
            "token_endpoint": self._token_endpoint,
            "scopes_supported": SUPPORTED_SCOPES,
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "grant_types_supported": [
                "authorization_code",
                "client_credentials",
                "refresh_token",
            ],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
                "private_key_jwt",
            ],
        }
