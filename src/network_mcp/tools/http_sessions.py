"""MCP tools for HTTP session management and resumption.

Provides tools for clients to create tracked sessions, resume after
disconnection, and query tool call history for context restoration.
"""

import logging

from network_mcp.helpers import READ_ONLY
from network_mcp.server import http_session_store, mcp

logger = logging.getLogger("network-mcp")


@mcp.tool(annotations=READ_ONLY)
def net_session_create(metadata: str = "") -> dict:
    """Create a tracked HTTP session for tool call history persistence.

    Returns a session_id that can be used with net_session_resume to restore
    context after disconnection. Tool calls are automatically recorded into
    the most recently created active session.

    Args:
        metadata: Optional JSON string of metadata to attach to the session.
    """
    if http_session_store is None:
        return {
            "status": "error",
            "error": "HTTP session resumption is not enabled (set NET_HTTP_SESSION_RESUMPTION=true)",
        }

    import json

    meta = {}
    if metadata:
        try:
            meta = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            meta = {"raw": metadata}

    session_id = http_session_store.create_session(metadata=meta)
    return {
        "status": "success",
        "session_id": session_id,
        "message": "Session created. Tool calls will be recorded. Use net_session_resume to restore context.",
    }


@mcp.tool(annotations=READ_ONLY)
def net_session_resume(session_id: str) -> dict:
    """Resume a previous HTTP session and retrieve tool call history.

    Use this after reconnecting to restore context from a previous session.
    Returns the session's tool call history so the AI can understand what
    was done before the disconnection.

    Args:
        session_id: The session ID returned by net_session_create.
    """
    if http_session_store is None:
        return {
            "status": "error",
            "error": "HTTP session resumption is not enabled (set NET_HTTP_SESSION_RESUMPTION=true)",
        }

    state = http_session_store.get_session(session_id)
    if state is None:
        return {
            "status": "error",
            "error": f"Session '{session_id}' not found or expired",
        }

    history = http_session_store.get_history(session_id)
    return {
        "status": "success",
        "session_id": session_id,
        "tool_call_count": len(history),
        "metadata": state.metadata,
        "history": [record.to_dict() for record in history],
    }


@mcp.tool(annotations=READ_ONLY)
def net_session_status(session_id: str = "") -> dict:
    """Get status of an HTTP session or list all active sessions.

    Args:
        session_id: Optional session ID to query. If empty, lists all active sessions.
    """
    if http_session_store is None:
        return {
            "status": "error",
            "error": "HTTP session resumption is not enabled (set NET_HTTP_SESSION_RESUMPTION=true)",
        }

    if session_id:
        state = http_session_store.get_session(session_id)
        if state is None:
            return {
                "status": "error",
                "error": f"Session '{session_id}' not found or expired",
            }
        return {
            "status": "success",
            **state.to_dict(),
        }

    sessions = http_session_store.list_sessions()
    return {
        "status": "success",
        "total_sessions": len(sessions),
        "sessions": sessions,
    }


@mcp.tool(annotations=READ_ONLY)
def net_session_end(session_id: str) -> dict:
    """End and remove a tracked HTTP session.

    Args:
        session_id: The session ID to end.
    """
    if http_session_store is None:
        return {
            "status": "error",
            "error": "HTTP session resumption is not enabled (set NET_HTTP_SESSION_RESUMPTION=true)",
        }

    removed = http_session_store.remove_session(session_id)
    if not removed:
        return {
            "status": "error",
            "error": f"Session '{session_id}' not found",
        }
    return {
        "status": "success",
        "session_id": session_id,
        "message": "Session ended and removed",
    }
