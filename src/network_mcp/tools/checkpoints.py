"""Configuration checkpoint tools for Arista EOS."""

import re

from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    check_read_only,
    handle_tool_errors,
    run_session_command,
    run_show_command,
    validate_host,
)
from network_mcp.server import conn_mgr, mcp

# --- Input Validation for Checkpoints ---

CHECKPOINT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")
MAX_CHECKPOINT_NAME_LEN = 50


def validate_checkpoint_name(name: str) -> str | None:
    """Validate checkpoint name. Returns error message if invalid, None if OK."""
    if not name or not name.strip():
        return "Checkpoint name is required and cannot be empty"
    if len(name) > MAX_CHECKPOINT_NAME_LEN:
        return f"Checkpoint name must be {MAX_CHECKPOINT_NAME_LEN} characters or fewer"
    if not CHECKPOINT_NAME_PATTERN.match(name):
        return f"Invalid checkpoint name '{name}'. Must contain only alphanumeric characters and hyphens"
    return None


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_create_checkpoint(host: str, checkpoint_name: str) -> dict:
    """[WRITE] Save the current running configuration as a named checkpoint.

    When to use: Before making significant changes, to create a restore point.
    Output: Confirmation with checkpoint name. Use rollback_to_checkpoint to restore.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        checkpoint_name: Name for the checkpoint (alphanumeric and hyphens only).
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    name_err = validate_checkpoint_name(checkpoint_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    result = run_session_command(conn_mgr, host, [f"configure checkpoint save {checkpoint_name}"], "create_checkpoint")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "create_checkpoint",
        "data": {"checkpoint_name": checkpoint_name},
        "message": f"Checkpoint '{checkpoint_name}' saved. Use rollback_to_checkpoint to restore if needed.",
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_list_checkpoints(host: str) -> dict:
    """List all configuration checkpoints on an Arista EOS device.

    When to use: To see available checkpoints before performing a rollback.
    Output: List of checkpoint names stored on flash (survives reboots).

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show configuration checkpoints"], encoding="text")
    if result["status"] == "error":
        return result
    output = result["data"][0].get("output", "")
    return {
        "status": "success",
        "device": host,
        "data": {"checkpoints": output},
    }


@mcp.tool(annotations=DESTRUCTIVE)
@handle_tool_errors
def eos_rollback_to_checkpoint(host: str, checkpoint_name: str) -> dict:
    """[DESTRUCTIVE] Restore running configuration from a checkpoint.

    When to use: To revert configuration to a known-good state after a failed change.
    Output: Confirmation of rollback. WARNING: replaces ENTIRE running config.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        checkpoint_name: Name of the checkpoint to restore.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    name_err = validate_checkpoint_name(checkpoint_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    cmd = [f"configure checkpoint restore {checkpoint_name}"]
    result = run_session_command(conn_mgr, host, cmd, "rollback_to_checkpoint")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "rollback_to_checkpoint",
        "data": {"checkpoint_name": checkpoint_name},
        "message": f"Running config restored from checkpoint '{checkpoint_name}'.",
    }


@mcp.tool(annotations=DESTRUCTIVE)
@handle_tool_errors
def eos_delete_config_checkpoint(host: str, checkpoint_name: str) -> dict:
    """[WRITE] Delete a configuration checkpoint from an Arista EOS device.

    When to use: To clean up old checkpoints that are no longer needed.
    Output: Confirmation of deletion. Cannot be undone.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        checkpoint_name: Name of the checkpoint to delete.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    name_err = validate_checkpoint_name(checkpoint_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    result = run_session_command(conn_mgr, host, [f"delete checkpoint:{checkpoint_name}"], "delete_config_checkpoint")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "delete_config_checkpoint",
        "data": {"checkpoint_name": checkpoint_name},
        "message": f"Checkpoint '{checkpoint_name}' deleted.",
    }
