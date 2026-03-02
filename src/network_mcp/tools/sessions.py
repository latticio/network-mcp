"""Configuration session tools for Arista EOS."""

import re
import time

import pyeapi.eapilib

from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    check_read_only,
    handle_tool_errors,
    run_session_command,
    run_show_command,
    validate_config_commands,
    validate_host,
)
from network_mcp.sanitizer import config_sanitizer
from network_mcp.server import conn_mgr, mcp

# --- Input Validation for Config Sessions ---

SESSION_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")
MAX_SESSION_NAME_LEN = 50


def validate_session_name(name: str) -> str | None:
    """Validate config session name. Returns error message if invalid, None if OK."""
    if not name or not name.strip():
        return "Session name is required and cannot be empty"
    if len(name) > MAX_SESSION_NAME_LEN:
        return f"Session name must be {MAX_SESSION_NAME_LEN} characters or fewer"
    if not SESSION_NAME_PATTERN.match(name):
        return f"Invalid session name '{name}'. Must contain only alphanumeric characters and hyphens"
    return None


TIMER_FORMAT_PATTERN = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def validate_timer_format(timer: str) -> str | None:
    """Validate commit timer format (hh:mm:ss). Returns error message if invalid, None if OK."""
    if not TIMER_FORMAT_PATTERN.match(timer):
        return f"Invalid timer format '{timer}'. Must be hh:mm:ss (e.g., '00:05:00')"
    parts = timer.split(":")
    hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
    if hours > 23 or minutes > 59 or seconds > 59:
        return f"Invalid timer values '{timer}'. Hours: 0-23, minutes: 0-59, seconds: 0-59"
    if hours == 0 and minutes == 0 and seconds == 0:
        return "Timer cannot be 00:00:00 (zero duration)"
    return None


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_create_config_session(host: str, session_name: str | None = None) -> dict:
    """[WRITE] Create a named configuration session on an Arista EOS device.

    When to use: Before making config changes that you want to stage, review, and commit atomically.
    Output: Returns session name. Use add_to_config_session to stage commands, then commit or abort.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        session_name: Optional session name (auto-generated as mcp-<timestamp> if not provided).
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if session_name is None:
        session_name = f"mcp-{int(time.time())}"
    name_err = validate_session_name(session_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    result = run_session_command(
        conn_mgr, host, [f"configure session {session_name}", "abort"], "create_config_session"
    )
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "create_config_session",
        "data": {
            "session_name": session_name,
        },
        "message": (
            f"Config session '{session_name}' created. "
            "Use add_to_config_session to stage commands, "
            "get_config_session_diff to review, then "
            "commit_config_session or abort_config_session."
        ),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_config_session_diff(host: str, session_name: str) -> dict:
    """Show the pending changes (diff) for a configuration session.

    When to use: After staging commands in a session, before committing, to review what will change.
    Output: Unified diff of pending changes; has_changes indicates if the session has modifications.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        session_name: Name of the config session to preview.
    """
    name_err = validate_session_name(session_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    result = run_show_command(conn_mgr, host, [f"show session-config named {session_name} diffs"], encoding="text")
    if result["status"] == "error":
        return result
    diff = result["data"][0].get("output", "")
    # Sanitize diff text to prevent credential leaks (diffs may show password changes)
    diff = config_sanitizer.sanitize(diff)
    return {
        "status": "success",
        "device": host,
        "data": {
            "session_name": session_name,
            "diff": diff,
            "has_changes": bool(diff.strip()),
        },
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_add_to_config_session(host: str, session_name: str, commands: list[str]) -> dict:
    """[WRITE] Stage configuration commands into an existing session.

    When to use: To add config commands to a session without committing them immediately.
    Output: Returns staged commands and updated diff. Review with get_config_session_diff.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        session_name: Name of the existing config session.
        commands: List of configuration commands to stage.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    name_err = validate_session_name(session_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    if not commands:
        return {"status": "error", "device": host, "error": "No commands provided"}
    cmd_err = validate_config_commands(commands)
    if cmd_err:
        return {"status": "error", "device": host, "error": cmd_err}

    try:
        node = conn_mgr.get_node(host)
        node.config(commands, session=session_name)  # type: ignore[attr-defined]

        # Get updated diff
        diff_result = node.run_commands([f"show session-config named {session_name} diffs"], encoding="text")  # type: ignore[attr-defined]
        diff = config_sanitizer.sanitize(diff_result[0].get("output", ""))

        return {
            "status": "staged",
            "device": host,
            "action": "add_to_config_session",
            "data": {
                "session_name": session_name,
                "commands_added": commands,
                "diff": diff,
            },
            "message": (
                f"Commands staged in session '{session_name}'. "
                "Review the diff, then commit_config_session or abort_config_session."
            ),
        }
    except pyeapi.eapilib.ConnectionError as e:
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Connection to {host} failed: {e}. "
                "Verify: (1) device is reachable, (2) eAPI is enabled "
                "('management api http-commands' in EOS config), (3) credentials are correct."
            ),
        }
    except pyeapi.eapilib.CommandError as e:
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Command error on {host}: {e}. "
                "Check that the command syntax is correct and supported on this EOS version."
            ),
        }
    except Exception as e:
        return {"status": "error", "device": host, "error": f"Unexpected error: {e}"}


@mcp.tool(annotations=DESTRUCTIVE)
@handle_tool_errors
def eos_commit_config_session(host: str, session_name: str) -> dict:
    """[WRITE] Commit a configuration session, applying all staged changes immediately.

    When to use: After staging and reviewing changes, to apply them atomically to running config.
    Output: Returns diff of committed changes. Changes take effect immediately.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        session_name: Name of the config session to commit.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    name_err = validate_session_name(session_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    # Get diff before committing (for audit/response), sanitize to prevent credential leaks
    diff_result = run_show_command(conn_mgr, host, [f"show session-config named {session_name} diffs"], encoding="text")
    diff = diff_result["data"][0].get("output", "") if diff_result["status"] == "success" else ""
    diff = config_sanitizer.sanitize(diff)

    result = run_session_command(
        conn_mgr, host, [f"configure session {session_name}", "commit"], "commit_config_session"
    )
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "commit_config_session",
        "data": {
            "session_name": session_name,
            "diff": diff,
        },
        "message": f"Session '{session_name}' committed successfully. Changes are now active.",
    }


@mcp.tool(annotations=DESTRUCTIVE)
@handle_tool_errors
def eos_commit_config_session_timer(host: str, session_name: str, timer: str = "00:05:00") -> dict:
    """[WRITE] Commit a configuration session with an auto-rollback timer.

    When to use: For changes that could affect management connectivity (IP changes, ACLs, routing).
    Output: Changes applied with auto-rollback timer. Must confirm before timer expires.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        session_name: Name of the config session to commit.
        timer: Auto-rollback timer in hh:mm:ss format (default: 00:05:00 = 5 minutes).
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    name_err = validate_session_name(session_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}
    timer_err = validate_timer_format(timer)
    if timer_err:
        return {"status": "error", "device": host, "error": timer_err}

    # Get diff before committing, sanitize to prevent credential leaks
    diff_result = run_show_command(conn_mgr, host, [f"show session-config named {session_name} diffs"], encoding="text")
    diff = diff_result["data"][0].get("output", "") if diff_result["status"] == "success" else ""
    diff = config_sanitizer.sanitize(diff)

    result = run_session_command(
        conn_mgr, host, [f"configure session {session_name}", f"commit timer {timer}"], "commit_config_session_timer"
    )
    if result["status"] == "error":
        return result
    return {
        "status": "committed_with_timer",
        "device": host,
        "action": "commit_config_session_timer",
        "data": {
            "session_name": session_name,
            "timer": timer,
            "diff": diff,
        },
        "message": (
            f"Session '{session_name}' committed with {timer} auto-rollback timer. "
            f"Changes are ACTIVE but will be REVERTED after {timer} unless confirmed. "
            "Verify the change works, then call confirm_commit_timer to make it permanent."
        ),
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_confirm_commit_timer(host: str, session_name: str) -> dict:
    """[WRITE] Confirm a timer-based commit, making changes permanent.

    When to use: After commit_config_session_timer, once you've verified the change works.
    Output: Confirmation that changes are now permanent and timer is cancelled.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        session_name: Name of the session with a pending commit timer.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    name_err = validate_session_name(session_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    result = run_session_command(
        conn_mgr, host, [f"configure session {session_name}", "commit"], "confirm_commit_timer"
    )
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "confirm_commit_timer",
        "data": {"session_name": session_name},
        "message": f"Commit confirmed for session '{session_name}'. Changes are now permanent.",
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_abort_config_session(host: str, session_name: str) -> dict:
    """[WRITE] Abort a configuration session, discarding all staged changes.

    When to use: To discard staged changes without applying them.
    Output: Confirmation that session was aborted with no changes applied.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        session_name: Name of the config session to abort.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    name_err = validate_session_name(session_name)
    if name_err:
        return {"status": "error", "device": host, "error": name_err}

    result = run_session_command(conn_mgr, host, [f"configure session {session_name}", "abort"], "abort_config_session")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "abort_config_session",
        "data": {"session_name": session_name},
        "message": f"Session '{session_name}' aborted. No changes were applied.",
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_list_config_sessions(host: str) -> dict:
    """List all configuration sessions on an Arista EOS device.

    When to use: To check existing sessions before creating new ones, or to find session names.
    Output: List of sessions with names, states, and users. Max 5 uncommitted sessions allowed.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show configuration sessions detail"])
    if result["status"] == "error":
        return result
    sessions_raw = result["data"][0].get("sessions", {})
    session_list = []
    for name, info in sessions_raw.items():
        session_list.append(
            {
                "name": name,
                "state": info.get("state", "unknown"),
                "user": info.get("commitUser", ""),
                "description": info.get("description", ""),
            }
        )
    return {
        "status": "success",
        "device": host,
        "data": {
            "sessions": session_list,
            "count": len(session_list),
            "max_sessions": result["data"][0].get("maxSavedSessions", 5),
        },
    }
