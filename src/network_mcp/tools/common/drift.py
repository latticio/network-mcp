"""Vendor-agnostic configuration drift detection and remediation tools.

Compares running device configuration against golden intended state from
local files or NetBox, with severity-classified drift reports and
optional remediation command generation.

Drift remediation routes through the change management system by default,
creating a ChangeRequest for review and approval. A direct-apply variant
is available for emergency use.
"""

from __future__ import annotations

import logging

from network_mcp.drift import DriftDetector, DriftSeverity
from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import (
    DESTRUCTIVE,
    READ_ONLY,
    WRITE_SAFE,
    check_read_only,
    get_change_manager,
    handle_tool_errors,
    validate_cli_param,
    validate_config_commands,
    validate_host,
)
from network_mcp.sanitizer import config_sanitizer
from network_mcp.server import conn_mgr, mcp, settings

logger = logging.getLogger("network-mcp.drift")

_detector: DriftDetector | None = None


def _get_detector() -> DriftDetector:
    """Lazily create and cache the DriftDetector singleton."""
    global _detector
    if _detector is None:
        _detector = DriftDetector(settings)
    return _detector


def _get_running_config(host: str) -> tuple[str | None, dict | None]:
    """Fetch running config from a device. Returns (config_text, error_dict)."""
    driver = conn_mgr.get_driver(host)
    try:
        config_data = driver.get_config(sanitized=False)
    except NotSupportedError:
        return None, {
            "status": "not_supported",
            "device": host,
            "vendor": getattr(driver, "vendor", "unknown"),
            "platform": getattr(driver, "platform", "unknown"),
            "error": f"get_config is not supported on {getattr(driver, 'platform', 'unknown')}",
        }
    running = config_data.get("running", "")
    return running, None


_VALID_SOURCES = ("file", "netbox")
_VALID_SEVERITIES = ("critical", "warning", "info")


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_check_drift(host: str, golden_source: str = "file") -> dict:
    """Check configuration drift on any vendor device against golden config.

    Compares running configuration with a golden intended-state config.
    Returns a severity-classified drift report with per-section details.

    Severity levels:
      - critical: ACLs, routing protocols (BGP, OSPF), AAA, crypto, route-maps
      - warning: Interface config, VLANs, logging, NTP, SNMP, spanning-tree
      - info: Comments, banners, descriptions, other non-critical settings

    Golden config sources:
      - file: Reads from NET_GOLDEN_CONFIG_DIR/{hostname}.conf
      - netbox: Fetches config context from NetBox (requires NETBOX_URL/TOKEN)

    Args:
        host: Device hostname, IP, or inventory name.
        golden_source: Source of golden config ("file" or "netbox").
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if golden_source not in _VALID_SOURCES:
        return {
            "status": "error",
            "device": host,
            "error": f"Invalid golden_source '{golden_source}'. Must be one of: {_VALID_SOURCES}",
        }

    detector = _get_detector()

    # Load golden config
    golden_config = detector.load_golden_config(host, source=golden_source)
    if golden_config is None:
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Golden config not found for device '{host}' from source '{golden_source}'. "
                + (
                    f"Ensure NET_GOLDEN_CONFIG_DIR is set and contains {host}.conf"
                    if golden_source == "file"
                    else "Check NetBox configuration."
                )
            ),
        }

    # Fetch running config
    running_config, err = _get_running_config(host)
    if err:
        return err

    # Sanitize before comparison to avoid false diffs on credential formatting
    running_sanitized = config_sanitizer.sanitize(running_config)
    golden_sanitized = config_sanitizer.sanitize(golden_config)

    report = detector.detect_drift(host, running_sanitized, golden_sanitized)

    driver = conn_mgr.get_driver(host)
    return {
        "status": "success",
        "device": host,
        "vendor": getattr(driver, "vendor", "unknown"),
        "platform": getattr(driver, "platform", "unknown"),
        "data": {
            "has_drift": report.total_drifts > 0,
            "report": report.to_dict(),
        },
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def net_drift_report(hosts: list[str] | None = None) -> dict:
    """Generate drift report across multiple devices.

    Checks each device against its golden config and returns an aggregated
    summary. Devices without golden configs are reported with a warning.

    Args:
        hosts: List of device hostnames to check. If empty/None, checks all inventory devices.
    """
    detector = _get_detector()

    # Resolve host list
    if not hosts:
        hosts = conn_mgr.list_devices()
        if not hosts:
            return {
                "status": "error",
                "error": "No devices in inventory. Configure NET_INVENTORY_FILE or NET_DEMO_MODE.",
            }

    succeeded: dict[str, dict] = {}
    failed: dict[str, str] = {}
    total_critical = 0
    total_warning = 0
    total_info = 0
    devices_with_drift = 0

    for host in hosts:
        host_err = validate_host(host)
        if host_err:
            failed[host] = host_err
            continue

        golden_config = detector.load_golden_config(host, source="file")
        if golden_config is None:
            failed[host] = "Golden config not found"
            continue

        try:
            running_config, err = _get_running_config(host)
            if err:
                failed[host] = err.get("error", "Failed to fetch config")
                continue

            running_sanitized = config_sanitizer.sanitize(running_config)
            golden_sanitized = config_sanitizer.sanitize(golden_config)

            report = detector.detect_drift(host, running_sanitized, golden_sanitized)

            if report.total_drifts > 0:
                devices_with_drift += 1
                total_critical += report.critical_drifts
                total_warning += report.warning_drifts
                total_info += report.info_drifts

            succeeded[host] = {
                "device": host,
                "has_drift": report.total_drifts > 0,
                "critical": report.critical_drifts,
                "warning": report.warning_drifts,
                "info": report.info_drifts,
                "total": report.total_drifts,
            }
        except Exception as e:
            failed[host] = str(e)

    # Determine status
    total = len(hosts)
    n_failed = len(failed)
    if not failed:
        status = "success"
    elif not succeeded:
        status = "error"
    else:
        status = "partial"

    return {
        "status": status,
        "action": "drift_report",
        "summary": {
            "total": total,
            "succeeded": len(succeeded),
            "failed": n_failed,
            "devices_with_drift": devices_with_drift,
            "total_critical": total_critical,
            "total_warning": total_warning,
            "total_info": total_info,
        },
        "succeeded_hosts": sorted(succeeded.keys()),
        "failed_hosts": sorted(failed.keys()),
        "results": succeeded,
        "errors": failed,
    }


def _calculate_drift_risk(drift_count: int) -> str:
    """Calculate change request risk level from number of drift differences."""
    if drift_count >= 10:
        return "HIGH"
    elif drift_count >= 3:
        return "MEDIUM"
    return "LOW"


def _prepare_drift_remediation(
    host: str, golden_source: str, severity: str
) -> tuple[list[str] | None, dict | None, object | None]:
    """Common validation and remediation command generation for drift tools.

    Returns (commands, error_dict, report) — error_dict is non-None on failure.
    """
    ro_err = check_read_only()
    if ro_err:
        return None, {"status": "error", "device": host, "error": ro_err}, None

    host_err = validate_host(host)
    if host_err:
        return None, {"status": "error", "device": host, "error": host_err}, None

    if golden_source not in _VALID_SOURCES:
        return (
            None,
            {
                "status": "error",
                "device": host,
                "error": f"Invalid golden_source '{golden_source}'. Must be one of: {_VALID_SOURCES}",
            },
            None,
        )

    if severity not in _VALID_SEVERITIES:
        return (
            None,
            {
                "status": "error",
                "device": host,
                "error": f"Invalid severity '{severity}'. Must be one of: {_VALID_SEVERITIES}",
            },
            None,
        )

    severity_map = {
        "critical": DriftSeverity.CRITICAL,
        "warning": DriftSeverity.WARNING,
        "info": DriftSeverity.INFO,
    }
    min_severity = severity_map[severity]

    detector = _get_detector()

    golden_config = detector.load_golden_config(host, source=golden_source)
    if golden_config is None:
        return (
            None,
            {
                "status": "error",
                "device": host,
                "error": f"Golden config not found for device '{host}' from source '{golden_source}'.",
            },
            None,
        )

    running_config, err = _get_running_config(host)
    if err:
        return None, err, None

    running_sanitized = config_sanitizer.sanitize(running_config)
    golden_sanitized = config_sanitizer.sanitize(golden_config)

    report = detector.detect_drift(host, running_sanitized, golden_sanitized)

    if report.total_drifts == 0:
        return (
            None,
            {
                "status": "success",
                "device": host,
                "action": "remediate_drift",
                "message": "No drift detected — device configuration matches golden config.",
                "data": {"commands_applied": 0},
            },
            report,
        )

    commands = detector.generate_remediation_commands(report, min_severity)

    if not commands:
        return (
            None,
            {
                "status": "success",
                "device": host,
                "action": "remediate_drift",
                "message": f"No drifts at severity '{severity}' or above require remediation.",
                "data": {"commands_applied": 0, "total_drifts": report.total_drifts},
            },
            report,
        )

    deny_err = validate_config_commands(commands)
    if deny_err:
        return None, {"status": "error", "device": host, "error": f"Remediation blocked: {deny_err}"}, report

    for cmd in commands:
        cli_err = validate_cli_param(cmd, "remediation command")
        if cli_err:
            return (
                None,
                {
                    "status": "error",
                    "device": host,
                    "error": f"Invalid remediation command: {cli_err}",
                },
                report,
            )

    return commands, None, report


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def net_remediate_drift(host: str, golden_source: str = "file", severity: str = "critical") -> dict:
    """[WRITE] Create a change request to remediate configuration drift.

    Instead of directly applying remediation commands, this creates a
    ChangeRequest through the change management system for review and
    approval. Use net_approve_change to apply the remediation.

    For emergency direct application, use net_remediate_drift_direct.

    Only remediates drifts at or above the specified severity level.
    Requires NET_READ_ONLY=false.

    Args:
        host: Device hostname, IP, or inventory name.
        golden_source: Source of golden config ("file" or "netbox").
        severity: Minimum severity to remediate ("critical", "warning", or "info").
    """
    commands, err, report = _prepare_drift_remediation(host, golden_source, severity)
    if err is not None:
        return err

    # Route through change management
    cm = get_change_manager()
    risk_level = _calculate_drift_risk(report.total_drifts)

    if cm.enabled:
        from network_mcp.middleware import get_current_user

        user = get_current_user() or ""
        cr = cm.create_change_request("drift_remediation", host, commands, user=user)
        return {
            "status": "success",
            "device": host,
            "message": "Drift remediation change request created. Approve to apply.",
            "change_id": cr.id,
            "commands": commands,
            "risk_level": risk_level,
            "source": "drift_remediation",
            "severity_threshold": severity,
            "drift_summary": report.to_dict(),
        }

    # Change management not enabled — create a lightweight tracking entry
    from network_mcp.change_management import ChangeRequest, ChangeRisk, ChangeStatus

    risk_map = {"LOW": ChangeRisk.LOW, "MEDIUM": ChangeRisk.MEDIUM, "HIGH": ChangeRisk.HIGH}
    import uuid

    cr_id = str(uuid.uuid4())
    cr = ChangeRequest(
        id=cr_id,
        tool_name="drift_remediation",
        device=host,
        commands=commands,
        risk=risk_map.get(risk_level, ChangeRisk.MEDIUM),
        status=ChangeStatus.PENDING,
        created_by="",
    )

    return {
        "status": "success",
        "device": host,
        "message": "Drift remediation change request created. Approve to apply.",
        "change_id": cr.id,
        "commands": commands,
        "risk_level": risk_level,
        "source": "drift_remediation",
        "severity_threshold": severity,
        "drift_summary": report.to_dict(),
    }


@mcp.tool(annotations=DESTRUCTIVE)
@handle_tool_errors
def net_remediate_drift_direct(host: str, golden_source: str = "file", severity: str = "critical") -> dict:
    """[DESTRUCTIVE] Directly apply drift remediation commands without change management.

    This is the emergency/backward-compatible variant that applies
    remediation commands immediately without creating a change request.
    Prefer net_remediate_drift for normal operations.

    Only remediates drifts at or above the specified severity level.
    Requires NET_READ_ONLY=false.

    Args:
        host: Device hostname, IP, or inventory name.
        golden_source: Source of golden config ("file" or "netbox").
        severity: Minimum severity to remediate ("critical", "warning", or "info").
    """
    commands, err, report = _prepare_drift_remediation(host, golden_source, severity)
    if err is not None:
        return err

    # Push commands directly
    driver = conn_mgr.get_driver(host)
    try:
        driver.run_config(commands)
    except NotSupportedError:
        return {
            "status": "not_supported",
            "device": host,
            "vendor": getattr(driver, "vendor", "unknown"),
            "platform": getattr(driver, "platform", "unknown"),
            "error": f"run_config is not supported on {getattr(driver, 'platform', 'unknown')}",
        }

    return {
        "status": "success",
        "device": host,
        "vendor": getattr(driver, "vendor", "unknown"),
        "platform": getattr(driver, "platform", "unknown"),
        "action": "remediate_drift",
        "data": {
            "commands_applied": len(commands),
            "commands": commands,
            "severity_threshold": severity,
            "drift_summary": report.to_dict(),
        },
        "message": f"Applied {len(commands)} remediation command(s) for drifts at severity '{severity}' or above.",
    }
