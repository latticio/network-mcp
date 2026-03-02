"""Running/startup configuration and config diff tools for Arista EOS.

Config session tools are in sessions.py. Checkpoint tools are in checkpoints.py.
"""

import re
import time

from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    check_read_only,
    handle_tool_errors,
    run_config_command,
    run_session_command,
    run_show_command,
    validate_cli_param,
    validate_config_commands,
    validate_host,
)
from network_mcp.sanitizer import config_sanitizer
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_running_config(host: str, sanitize: bool = True) -> dict:
    """Get the full running configuration from an Arista EOS device.

    When to use: To view the complete device configuration, audit settings, or compare configs.
    Output: Full running-config text. Sensitive data redacted by default (passwords, keys, communities).

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        sanitize: Whether to redact sensitive data (default: True).
    """
    result = run_show_command(conn_mgr, host, ["show running-config"], encoding="text")
    if result["status"] == "error":
        return result
    config_text = result["data"][0].get("output", "")
    if sanitize:
        config_text = config_sanitizer.sanitize(config_text)
    return {
        "status": "success",
        "device": host,
        "data": {"config": config_text, "sanitized": sanitize},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_running_config_section(host: str, section: str, sanitize: bool = True) -> dict:
    """Get a specific section of the running configuration from an Arista EOS device.

    When to use: To view only a relevant config section (e.g., 'router bgp', 'interface Ethernet1').
    Output: Config text for the requested section. Sensitive data redacted by default.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        section: Configuration section to retrieve (e.g., 'router bgp', 'interface Ethernet1', 'ip access-list').
        sanitize: Whether to redact sensitive data (default: True).
    """
    section_err = validate_cli_param(section, "section")
    if section_err:
        return {"status": "error", "device": host, "error": section_err}
    result = run_show_command(conn_mgr, host, [f"show running-config section {section}"], encoding="text")
    if result["status"] == "error":
        return result
    config_text = result["data"][0].get("output", "")
    if sanitize:
        config_text = config_sanitizer.sanitize(config_text)
    return {
        "status": "success",
        "device": host,
        "data": {"section": section, "config": config_text, "sanitized": sanitize},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_get_startup_config(host: str, sanitize: bool = True) -> dict:
    """Get the startup configuration from an Arista EOS device.

    When to use: To check the config that will be loaded on next reboot, or compare with running.
    Output: Full startup-config text. Sensitive data redacted by default.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        sanitize: Whether to redact sensitive data (default: True).
    """
    result = run_show_command(conn_mgr, host, ["show startup-config"], encoding="text")
    if result["status"] == "error":
        return result
    config_text = result["data"][0].get("output", "")
    if sanitize:
        config_text = config_sanitizer.sanitize(config_text)
    return {
        "status": "success",
        "device": host,
        "data": {"config": config_text, "sanitized": sanitize},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_diff_running_startup(host: str) -> dict:
    """Get the diff between running and startup configuration on an Arista EOS device.

    When to use: To check if there are unsaved config changes that would be lost on reboot.
    Output: Unified diff text. has_unsaved_changes is True if running differs from startup.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show running-config diffs"], encoding="text")
    if result["status"] == "error":
        return result
    output = result["data"][0].get("output", "")
    # Sanitize diff text to prevent credential leaks (diffs may show password changes)
    output = config_sanitizer.sanitize(output)
    has_diff = bool(output.strip())
    return {
        "status": "success",
        "device": host,
        "data": {
            "has_unsaved_changes": has_diff,
            "diff": output,
        },
    }


# --- Drift severity classification ---

_CRITICAL_PREFIXES = ("router bgp", "router ospf", "mlag", "management api", "management ssh", "aaa")
_WARNING_PREFIXES = (
    "interface ",
    "vlan ",
    "ip access-list",
    "ntp",
    "ip route",
    "prefix-list",
    "route-map",
    "spanning-tree",
)


def _classify_drift_severity(line: str) -> str:
    """Classify a diff line's severity based on the config section it belongs to."""
    stripped = line.lstrip("+-! ").lower()
    for prefix in _CRITICAL_PREFIXES:
        if stripped.startswith(prefix):
            return "critical"
    for prefix in _WARNING_PREFIXES:
        if stripped.startswith(prefix):
            return "warning"
    return "info"


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def eos_detect_config_drift(host: str) -> dict:
    """Detect configuration drift between running and startup configs on an Arista EOS device.

    When to use: To check if the active configuration has drifted from the saved startup
    config, with severity classification for each changed section.
    Output: Structured drift report with sections, severity levels, and summary counts.

    Severity levels:
      - critical: Routing protocols (BGP, OSPF), MLAG, management access, AAA
      - warning: Interfaces, VLANs, ACLs, NTP, static routes, prefix-lists, route-maps
      - info: Descriptions, logging, banners, aliases, and other non-critical settings

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    result = run_show_command(conn_mgr, host, ["show running-config diffs"], encoding="text")
    if result["status"] == "error":
        return result

    diff_text = result["data"][0].get("output", "").strip()
    # Sanitize diff text to prevent credential leaks (diffs may show password changes)
    diff_text = config_sanitizer.sanitize(diff_text)
    if not diff_text:
        return {
            "status": "success",
            "device": host,
            "data": {
                "has_drift": False,
                "sections": [],
                "summary": {"total": 0, "critical": 0, "warning": 0, "info": 0},
            },
        }

    # Parse diff into sections and classify severity
    sections = []
    current_section = None
    current_lines: list[str] = []
    current_severity = "info"

    for line in diff_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Detect section boundaries: lines that aren't indented and don't start with +/-/!
        is_section_header = not line.startswith((" ", "\t")) and not line.startswith(("+", "-", "!"))
        # Also treat top-level +/- lines (config stanzas being added/removed) as section starts
        is_stanza_change = line.startswith(("+", "-")) and not line.startswith(("+++", "---"))

        if is_section_header or (is_stanza_change and not line[1:].startswith((" ", "\t"))):
            # Save previous section
            if current_section and current_lines:
                sections.append(
                    {
                        "section": current_section,
                        "severity": current_severity,
                        "diff_lines": current_lines,
                    }
                )
            current_section = stripped.lstrip("+-! ")
            current_severity = _classify_drift_severity(line)
            current_lines = [line]
        else:
            current_lines.append(line)
            # Promote severity if a sub-line has higher severity
            line_sev = _classify_drift_severity(line)
            if line_sev == "critical":
                current_severity = "critical"
            elif line_sev == "warning" and current_severity == "info":
                current_severity = "warning"

    # Save last section
    if current_section and current_lines:
        sections.append(
            {
                "section": current_section,
                "severity": current_severity,
                "diff_lines": current_lines,
            }
        )

    summary = {
        "total": len(sections),
        "critical": sum(1 for s in sections if s["severity"] == "critical"),
        "warning": sum(1 for s in sections if s["severity"] == "warning"),
        "info": sum(1 for s in sections if s["severity"] == "info"),
    }

    return {
        "status": "success",
        "device": host,
        "data": {
            "has_drift": True,
            "sections": sections,
            "summary": summary,
        },
    }


# --- Write Tools ---


@mcp.tool(annotations=DESTRUCTIVE)
@handle_tool_errors
def eos_push_config_commands(host: str, commands: list[str]) -> dict:
    """[WRITE] Push a list of configuration commands to an Arista EOS device.

    When to use: For custom config changes not covered by other tools (e.g., ACLs, route-maps).
    Output: Confirmation with applied commands. Dangerous commands are blocked by deny-list.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        commands: List of config mode commands to execute in order.
    """
    if not commands:
        return {"status": "error", "device": host, "error": "No commands provided"}
    return run_config_command(conn_mgr, host, commands, "push_config_commands")


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_save_running_config(host: str) -> dict:
    """[WRITE] Save the running configuration to startup on an Arista EOS device.

    When to use: After making config changes to ensure they persist across reboots.
    Output: Confirmation that running-config was saved to startup-config.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    result = run_session_command(conn_mgr, host, ["copy running-config startup-config"], "save_running_config")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "save_running_config",
        "message": "Running configuration saved to startup-config",
    }


# --- Day-2 Operations: Backup, Maintenance Window, Auto-Rollback ---

_VALID_BACKUP_PREFIXES = ("scp://", "sftp://", "ftp://", "flash:")


def validate_backup_destination(dest: str) -> str | None:
    """Validate backup destination format. Returns error message if invalid, None if OK."""
    if not dest or not dest.strip():
        return "Destination is required and cannot be empty"
    if not dest.startswith(_VALID_BACKUP_PREFIXES):
        return f"Invalid destination. Must start with one of: {_VALID_BACKUP_PREFIXES}"
    err = validate_cli_param(dest, "destination")
    if err:
        return err
    return None


def validate_regex_pattern(pattern: str) -> str | None:
    """Validate a regex pattern. Returns error message if invalid, None if OK."""
    if not pattern or not pattern.strip():
        return "Pattern is required and cannot be empty"
    try:
        re.compile(pattern)
        return None
    except re.error as e:
        return f"Invalid regex pattern: {e}"


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_backup_config(host: str, destination: str) -> dict:
    """[WRITE] Copy running configuration to an external destination or flash.

    When to use: To create off-device backups before maintenance or as part of a regular backup policy.
    Output: Confirmation that copy command was issued. Supports SCP, SFTP, FTP, and flash: destinations.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        destination: Backup destination URL (scp://user@server/path, sftp://..., ftp://...) or flash: path.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    dest_err = validate_backup_destination(destination)
    if dest_err:
        return {"status": "error", "device": host, "error": dest_err}

    result = run_session_command(conn_mgr, host, [f"copy running-config {destination}"], "backup_config")
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "action": "backup_config",
        "data": {"destination": destination},
        "message": f"Running configuration copied to {destination}",
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_schedule_maintenance(host: str, commands: list[str], rollback_minutes: int = 5) -> dict:
    """[WRITE] Apply configuration with an automatic rollback timer via a config session.

    When to use: For risky changes (IP, ACL, routing) where automatic rollback is desired if something
    goes wrong. If the timer expires without confirmation, EOS reverts to the pre-change config.
    Output: Session name for use with confirm_commit_timer or abort_config_session.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        commands: List of configuration commands to apply.
        rollback_minutes: Minutes before automatic rollback (1-120, default 5).
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    if not commands:
        return {"status": "error", "device": host, "error": "No commands provided"}
    if not 1 <= rollback_minutes <= 120:
        return {
            "status": "error",
            "device": host,
            "error": f"rollback_minutes must be between 1 and 120, got {rollback_minutes}",
        }
    cmd_err = validate_config_commands(commands)
    if cmd_err:
        return {"status": "error", "device": host, "error": cmd_err}

    session_name = f"maint-{int(time.time())}"
    session_cmds = [f"configure session {session_name}"]
    session_cmds.extend(commands)
    session_cmds.append(f"commit timer {rollback_minutes}:00")

    result = run_session_command(conn_mgr, host, session_cmds, "schedule_maintenance")
    if result["status"] == "error":
        return result
    return {
        "status": "committed_with_timer",
        "device": host,
        "action": "schedule_maintenance",
        "data": {
            "session_name": session_name,
            "commands": commands,
            "rollback_minutes": rollback_minutes,
        },
        "message": (
            f"Configuration applied via session '{session_name}' with {rollback_minutes}-minute rollback timer. "
            f"Changes are ACTIVE but will REVERT after {rollback_minutes} minutes unless confirmed. "
            "Use confirm_commit_timer or abort_config_session to manage."
        ),
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def eos_auto_rollback_on_failure(
    host: str,
    commands: list[str],
    health_check_command: str,
    expected_pattern: str,
) -> dict:
    """[WRITE] Apply configuration, run a health check, and auto-rollback if health degrades.

    When to use: For changes where you want automated verification. Applies config, runs a show command,
    checks output against a regex pattern, and rolls back to a checkpoint if the pattern is not found.
    Output: Success if health check passes; failure with rollback confirmation if it fails.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        commands: List of configuration commands to apply.
        health_check_command: A show command to verify health after applying config (must start with 'show ').
        expected_pattern: Regex pattern that the health check output must match for success.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    if not commands:
        return {"status": "error", "device": host, "error": "No commands provided"}
    cmd_err = validate_config_commands(commands)
    if cmd_err:
        return {"status": "error", "device": host, "error": cmd_err}

    # Validate health check command is a show command
    if not health_check_command or not health_check_command.strip().lower().startswith("show "):
        return {
            "status": "error",
            "device": host,
            "error": "health_check_command must be a show command (must start with 'show ')",
        }
    hc_err = validate_cli_param(health_check_command, "health_check_command")
    if hc_err:
        return {"status": "error", "device": host, "error": hc_err}

    # Validate regex pattern
    pat_err = validate_regex_pattern(expected_pattern)
    if pat_err:
        return {"status": "error", "device": host, "error": pat_err}

    # Step 1: Create a checkpoint
    checkpoint_name = f"auto-rb-{int(time.time())}"
    cp_result = run_session_command(
        conn_mgr, host, [f"configure checkpoint save {checkpoint_name}"], "auto_rollback_checkpoint"
    )
    if cp_result["status"] == "error":
        return cp_result

    # Step 2: Apply configuration commands
    config_result = run_config_command(conn_mgr, host, commands, "auto_rollback_apply")
    if config_result["status"] == "error":
        # Rollback on config failure
        run_session_command(
            conn_mgr, host, [f"configure checkpoint restore {checkpoint_name}"], "auto_rollback_restore"
        )
        return {
            "status": "error",
            "device": host,
            "error": f"Config apply failed, rolled back to checkpoint '{checkpoint_name}': {config_result['error']}",
        }

    # Step 3: Run health check (show command, text encoding for regex matching)
    hc_result = run_show_command(conn_mgr, host, [health_check_command], encoding="text")
    if hc_result["status"] == "error":
        # Health check command failed — rollback
        run_session_command(
            conn_mgr, host, [f"configure checkpoint restore {checkpoint_name}"], "auto_rollback_restore"
        )
        return {
            "status": "error",
            "device": host,
            "action": "auto_rollback",
            "error": (
                f"Health check command failed, rolled back to checkpoint '{checkpoint_name}': {hc_result['error']}"
            ),
        }

    # Step 4: Check output against expected pattern
    hc_output = hc_result["data"][0].get("output", "")
    if not re.search(expected_pattern, hc_output):
        # Pattern not found — rollback
        run_session_command(
            conn_mgr, host, [f"configure checkpoint restore {checkpoint_name}"], "auto_rollback_restore"
        )
        return {
            "status": "error",
            "device": host,
            "action": "auto_rollback",
            "data": {
                "checkpoint_name": checkpoint_name,
                "health_check_output": hc_output,
                "expected_pattern": expected_pattern,
            },
            "error": (
                f"Health check failed: pattern '{expected_pattern}' not found in output. "
                f"Configuration rolled back to checkpoint '{checkpoint_name}'."
            ),
        }

    # Step 5: Health check passed — success
    return {
        "status": "success",
        "device": host,
        "action": "auto_rollback_on_failure",
        "data": {
            "checkpoint_name": checkpoint_name,
            "commands": commands,
            "health_check_command": health_check_command,
            "health_check_passed": True,
        },
        "message": (
            f"Configuration applied and health check passed. "
            f"Checkpoint '{checkpoint_name}' retained for manual rollback if needed."
        ),
    }
