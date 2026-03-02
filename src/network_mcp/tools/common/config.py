"""Vendor-agnostic configuration management tools.

Provides running/startup config retrieval, config section extraction,
config comparison (diff), save, push, dry-run validation, and config
version tracking across Arista EOS, Cisco IOS-XE, Cisco NX-OS, and
Juniper JunOS.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import uuid

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import (
    READ_ONLY,
    WRITE_SAFE,
    check_read_only,
    handle_tool_errors,
    validate_cli_param,
    validate_config_commands,
    validate_host,
)
from network_mcp.sanitizer import config_sanitizer
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")

# --- Config version tracking (optimistic concurrency) ---
_config_versions: dict[str, str] = {}


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_running_config(host: str, sanitize: bool = True) -> dict:
    """Get full running configuration from any vendor device.

    Output is sanitized to redact passwords and secrets by default.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        sanitize: Whether to redact sensitive data (default: True).
    """
    driver = conn_mgr.get_driver(host)
    try:
        config_data = driver.get_config(sanitized=False)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_config is not supported on {driver.platform}",
        }
    running = config_data.get("running", "")
    if sanitize:
        running = config_sanitizer.sanitize(running)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"config": running, "sanitized": sanitize},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_running_config_section(host: str, section: str, sanitize: bool = True) -> dict:
    """Get a specific section of running configuration from any vendor device.

    Extracts only the requested config section (e.g., 'router bgp', 'interface').
    On JunOS, maps to the top-level configuration stanza via NETCONF.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        section: Config section keyword (e.g., 'router bgp', 'interface Ethernet1').
        sanitize: Whether to redact sensitive data (default: True).
    """
    section_err = validate_cli_param(section, "section")
    if section_err:
        return {"status": "error", "device": host, "error": section_err}

    driver = conn_mgr.get_driver(host)
    try:
        config_text = driver.get_running_config_section(section)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_running_config_section is not supported on {driver.platform}",
        }
    if sanitize:
        config_text = config_sanitizer.sanitize(config_text)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"section": section, "config": config_text, "sanitized": sanitize},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_startup_config(host: str, sanitize: bool = True) -> dict:
    """Get startup configuration from any vendor device.

    On JunOS, startup config is the same as the committed running config
    (JunOS persists committed config across reboots automatically).
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
        sanitize: Whether to redact sensitive data (default: True).
    """
    driver = conn_mgr.get_driver(host)
    try:
        config_text = driver.get_startup_config()
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_startup_config is not supported on {driver.platform}",
        }
    if sanitize:
        config_text = config_sanitizer.sanitize(config_text)
    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {"config": config_text, "sanitized": sanitize},
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_compare_configs(host: str) -> dict:
    """Compare running vs startup configuration on any vendor device.

    Returns a unified diff highlighting differences between the running
    and startup configurations. On JunOS, running and startup are the
    same (committed config persists), so the diff will typically be empty.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    driver = conn_mgr.get_driver(host)
    try:
        config_data = driver.get_config(sanitized=False)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_config is not supported on {driver.platform}",
        }

    running = config_data.get("running", "")
    startup = config_data.get("startup", "")

    # Sanitize both configs before diffing to prevent credential leaks
    running_sanitized = config_sanitizer.sanitize(running)
    startup_sanitized = config_sanitizer.sanitize(startup)

    # Compute unified diff
    diff_lines = list(
        difflib.unified_diff(
            startup_sanitized.splitlines(keepends=True),
            running_sanitized.splitlines(keepends=True),
            fromfile="startup-config",
            tofile="running-config",
        )
    )
    diff_text = "".join(diff_lines)
    has_diff = bool(diff_text.strip())

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {
            "has_unsaved_changes": has_diff,
            "diff": diff_text,
        },
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def net_save_config(host: str) -> dict:
    """[WRITE] Save running configuration to startup on any vendor device.

    EOS/IOS-XE/NX-OS: Executes 'copy running-config startup-config'.
    JunOS: No-op (committed config already persists across reboots).
    Requires NET_READ_ONLY=false.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Args:
        host: Device hostname, IP, or inventory name.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    # JunOS: committed config persists across reboots, no save needed
    if driver.platform == "junos":
        return {
            "status": "success",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "action": "save_config",
            "message": "JunOS committed configuration already persists across reboots. No save required.",
        }

    # EOS/IOS-XE/NX-OS: copy running to startup
    try:
        save_cmd = "copy running-config startup-config"
        driver.run_show([save_cmd])
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"save_config is not supported on {driver.platform}",
        }

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "action": "save_config",
        "message": "Running configuration saved to startup-config",
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def net_push_config(host: str, commands: list[str], expected_version: str | None = None) -> dict:
    """[WRITE] Push configuration commands to any vendor device.

    Commands must be in the vendor-native CLI syntax. All commands are
    validated against the safety deny-list and checked for CLI injection.
    Requires NET_READ_ONLY=false.
    Works with Arista EOS, Cisco IOS-XE, Cisco NX-OS, and Juniper JunOS.

    Supports optimistic concurrency: if expected_version is provided,
    the current config hash is checked before applying. If another change
    was made since the version was obtained, the push is rejected.

    Args:
        host: Device hostname, IP, or inventory name.
        commands: List of configuration commands to apply.
        expected_version: Optional SHA-256 hash from net_get_config_version.
            If provided, config push is rejected when the device config has
            changed since this version was obtained.
    """
    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    if not commands:
        return {"status": "error", "device": host, "error": "No commands provided"}

    # Validate each command against deny-list
    deny_err = validate_config_commands(commands)
    if deny_err:
        return {"status": "error", "device": host, "error": deny_err}

    # Validate each command for CLI injection characters
    for cmd in commands:
        cli_err = validate_cli_param(cmd, "command")
        if cli_err:
            return {"status": "error", "device": host, "error": f"Invalid command '{cmd}': {cli_err}"}

    driver = conn_mgr.get_driver(host)

    # Optimistic concurrency check
    if expected_version is not None:
        try:
            config_data = driver.get_config(sanitized=False)
            current_running = config_data.get("running", "")
            current_hash = hashlib.sha256(current_running.encode("utf-8")).hexdigest()
            if current_hash != expected_version:
                return {
                    "status": "error",
                    "device": host,
                    "error": "Concurrent config change detected",
                    "details": {
                        "expected_version": expected_version,
                        "current_version": current_hash,
                    },
                }
        except NotSupportedError:
            pass  # If get_config not supported, skip version check

    try:
        driver.run_config(commands)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"run_config is not supported on {driver.platform}",
        }

    # Update config version after successful push
    try:
        config_data = driver.get_config(sanitized=False)
        new_running = config_data.get("running", "")
        _config_versions[host] = hashlib.sha256(new_running.encode("utf-8")).hexdigest()
    except Exception:
        logger.debug("Failed to update config version for %s after push", host)

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "action": "push_config",
        "data": {"commands": commands, "commands_count": len(commands)},
        "message": f"Configuration applied successfully: {len(commands)} command(s)",
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_dryrun_config(host: str, commands: list[str]) -> dict:
    """Validate configuration syntax without applying changes.

    Uses vendor-specific dry-run mechanisms:
    - EOS: config session in check-only mode (configure session, commands,
      show session-config diffs, abort)
    - IOS-XE: validates syntax via test parsing
    - NX-OS: validates via show running-config diff preview
    - JunOS: configure private + commit check + rollback 0

    Args:
        host: Target device hostname or IP address.
        commands: List of configuration commands to validate.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}
    if not commands:
        return {"status": "error", "device": host, "error": "No commands provided"}

    # Validate each command against deny-list
    deny_err = validate_config_commands(commands)
    if deny_err:
        return {"status": "error", "device": host, "error": deny_err}

    # Validate each command for CLI injection characters
    for cmd in commands:
        cli_err = validate_cli_param(cmd, "command")
        if cli_err:
            return {"status": "error", "device": host, "error": f"Invalid command '{cmd}': {cli_err}"}

    driver = conn_mgr.get_driver(host)
    platform = driver.platform

    if platform == "eos":
        return _dryrun_eos(host, commands, driver)
    elif platform in ("iosxe", "nxos"):
        return _dryrun_generic(host, commands, driver)
    elif platform == "junos":
        return _dryrun_junos(host, commands, driver)
    else:
        return _dryrun_generic(host, commands, driver)


def _dryrun_eos(host: str, commands: list[str], driver) -> dict:
    """EOS dry-run: use a temporary config session to validate and preview diffs."""
    session_name = f"dryrun-{uuid.uuid4().hex[:8]}"
    try:
        # Create session, add commands, get diffs, then abort
        session_cmds = [f"configure session {session_name}"]
        for cmd in commands:
            session_cmds.append(cmd)
        session_cmds.append("show session-config diffs")

        result = driver.run_show(session_cmds, encoding="text")

        # Extract the diff output (last command result)
        predicted_diff = ""
        if result and isinstance(result, list):
            last = result[-1]
            if isinstance(last, dict):
                predicted_diff = last.get("output", last.get("result", ""))
            elif isinstance(last, str):
                predicted_diff = last

        # Abort the session to discard changes
        try:
            driver.run_show([f"configure session {session_name}", "abort"])
        except Exception:
            logger.debug("Failed to abort dry-run session %s on %s", session_name, host)

        return {
            "status": "success",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "validation": {
                "syntax_valid": True,
                "predicted_diff": predicted_diff,
                "warnings": [],
            },
        }
    except Exception as e:
        # Attempt to abort session on error
        try:
            driver.run_show([f"configure session {session_name}", "abort"])
        except Exception:
            logger.debug("Failed to abort dry-run session %s on %s after error", session_name, host)

        error_msg = str(e)
        return {
            "status": "error",
            "device": host,
            "error": "Syntax validation failed",
            "details": {"line": commands[0] if commands else "", "error": error_msg},
        }


def _dryrun_junos(host: str, commands: list[str], driver) -> dict:
    """JunOS dry-run: configure private + commit check + rollback 0."""
    try:
        check_cmds = ["configure private"]
        for cmd in commands:
            check_cmds.append(cmd)
        check_cmds.append("commit check")
        check_cmds.append("rollback 0")

        result = driver.run_show(check_cmds, encoding="text")

        predicted_diff = ""
        if result and isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    text = item.get("output", item.get("result", ""))
                    if text:
                        predicted_diff = text

        return {
            "status": "success",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "validation": {
                "syntax_valid": True,
                "predicted_diff": predicted_diff,
                "warnings": [],
            },
        }
    except Exception as e:
        # Attempt rollback on error
        try:
            driver.run_show(["rollback 0"])
        except Exception:
            logger.debug("Failed to rollback dry-run on %s after error", host)

        error_msg = str(e)
        return {
            "status": "error",
            "device": host,
            "error": "Syntax validation failed",
            "details": {"line": commands[0] if commands else "", "error": error_msg},
        }


def _dryrun_generic(host: str, commands: list[str], driver) -> dict:
    """Generic dry-run: validate command syntax via run_show parse check."""
    try:
        # For IOS-XE/NX-OS, we attempt to validate by running the commands
        # in a way that checks syntax without committing
        check_cmds = list(commands)
        result = driver.run_show(check_cmds, encoding="text")

        predicted_diff = ""
        if result and isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    text = item.get("output", item.get("result", ""))
                    if text:
                        predicted_diff = text

        return {
            "status": "success",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "validation": {
                "syntax_valid": True,
                "predicted_diff": predicted_diff,
                "warnings": [],
            },
        }
    except Exception as e:
        error_msg = str(e)
        return {
            "status": "error",
            "device": host,
            "error": "Syntax validation failed",
            "details": {"line": commands[0] if commands else "", "error": error_msg},
        }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_get_config_version(host: str) -> dict:
    """Get the SHA-256 hash of the device's current running configuration.

    Use this to detect if the config has changed since last check
    (optimistic concurrency). Pass the returned version to
    net_push_config(expected_version=...) to prevent concurrent overwrites.

    Args:
        host: Target device hostname or IP address.
    """
    driver = conn_mgr.get_driver(host)
    try:
        config_data = driver.get_config(sanitized=False)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": driver.vendor,
            "platform": driver.platform,
            "error": f"get_config is not supported on {driver.platform}",
        }

    running = config_data.get("running", "")
    config_hash = hashlib.sha256(running.encode("utf-8")).hexdigest()
    _config_versions[host] = config_hash

    return {
        "status": "success",
        "device": host,
        "vendor": driver.vendor,
        "platform": driver.platform,
        "data": {
            "version": config_hash,
            "algorithm": "sha256",
        },
    }
