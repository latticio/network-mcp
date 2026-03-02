"""API key management tools for network-mcp administrators.

All tools require ``network:admin`` scope and ``NET_API_KEY_ENABLED=true``.
"""

from dataclasses import asdict

from network_mcp.helpers import WRITE_SAFE
from network_mcp.server import _api_key_manager, mcp


@mcp.tool(annotations=WRITE_SAFE)
def net_create_api_key(
    name: str,
    scopes: list[str],
    ttl_days: int | None = None,
    tenant_id: str | None = None,
) -> dict:
    """[ADMIN] Create a new API key for service account or CI/CD authentication.

    Returns the raw key exactly once — it cannot be retrieved later.
    Requires NET_API_KEY_ENABLED=true and network:admin scope.

    Args:
        name: Human-readable name for the key (e.g., "ci-pipeline-prod").
        scopes: List of authorized scopes (e.g., ["network:read", "network:write"]).
        ttl_days: Optional expiration in days from now. None means no expiration.
        tenant_id: Optional tenant binding for multi-tenant deployments.
    """
    if _api_key_manager is None:
        return {
            "status": "error",
            "error": "API key management is not enabled. Set NET_API_KEY_ENABLED=true.",
        }
    raw_key, api_key = _api_key_manager.generate_key(
        name=name,
        scopes=scopes,
        tenant_id=tenant_id,
        ttl_days=ttl_days,
    )
    key_data = asdict(api_key)
    key_data["key_hash"] = "[REDACTED]"
    return {
        "status": "success",
        "raw_key": raw_key,
        "warning": "Store this key securely — it cannot be retrieved again.",
        "key": key_data,
    }


@mcp.tool(annotations=WRITE_SAFE)
def net_list_api_keys() -> dict:
    """[ADMIN] List all API keys with metadata (no raw keys or hashes exposed).

    Requires NET_API_KEY_ENABLED=true and network:admin scope.
    """
    if _api_key_manager is None:
        return {
            "status": "error",
            "error": "API key management is not enabled. Set NET_API_KEY_ENABLED=true.",
        }
    keys = _api_key_manager.list_keys()
    return {
        "status": "success",
        "count": len(keys),
        "keys": [asdict(k) for k in keys],
    }


@mcp.tool(annotations=WRITE_SAFE)
def net_revoke_api_key(key_id: str) -> dict:
    """[ADMIN] Revoke an API key, immediately invalidating it.

    Requires NET_API_KEY_ENABLED=true and network:admin scope.

    Args:
        key_id: The key ID to revoke (e.g., "nmcp_a1b2c3d4").
    """
    if _api_key_manager is None:
        return {
            "status": "error",
            "error": "API key management is not enabled. Set NET_API_KEY_ENABLED=true.",
        }
    success = _api_key_manager.revoke_key(key_id)
    if not success:
        return {"status": "error", "error": f"API key not found: {key_id}"}
    return {"status": "success", "key_id": key_id, "message": "Key revoked successfully."}


@mcp.tool(annotations=WRITE_SAFE)
def net_rotate_api_key(key_id: str) -> dict:
    """[ADMIN] Rotate an API key: revoke the old key and issue a new one with the same scopes.

    Returns the new raw key exactly once. Requires NET_API_KEY_ENABLED=true and
    network:admin scope.

    Args:
        key_id: The key ID to rotate (e.g., "nmcp_a1b2c3d4").
    """
    if _api_key_manager is None:
        return {
            "status": "error",
            "error": "API key management is not enabled. Set NET_API_KEY_ENABLED=true.",
        }
    result = _api_key_manager.rotate_key(key_id)
    if result is None:
        return {"status": "error", "error": f"API key not found: {key_id}"}
    raw_key, api_key = result
    key_data = asdict(api_key)
    key_data["key_hash"] = "[REDACTED]"
    return {
        "status": "success",
        "raw_key": raw_key,
        "warning": "Store this key securely — it cannot be retrieved again.",
        "old_key_id": key_id,
        "new_key": key_data,
    }
