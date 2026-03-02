"""Juniper JunOS vendor-specific tools using NETCONF.

These tools expose JunOS-specific features that don't have cross-vendor
equivalents: routing instances, firewall filters, system alarms, commit
history, and commit-confirmed workflow.

Only loaded when the ``juniper`` extra is installed (scrapli-netconf available).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET  # noqa: N817, S405

from network_mcp.helpers import READ_ONLY, WRITE_SAFE, check_read_only, handle_tool_errors, validate_host
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")


# --- XML helpers (namespace-agnostic) ---


def _xml_find_text(element: ET.Element, tag: str, default: str = "") -> str:
    """Find text content of a child element, stripping namespaces."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    for elem in element:
        local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if local_name == tag and elem.text:
            return elem.text.strip()
    return default


def _xml_findall(element: ET.Element, tag: str) -> list[ET.Element]:
    """Find all child elements matching tag, ignoring namespaces."""
    results: list[ET.Element] = []
    for elem in element:
        local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if local_name == tag:
            results.append(elem)
    return results


def _xml_find(element: ET.Element, tag: str) -> ET.Element | None:
    """Find first child element matching tag, ignoring namespaces."""
    for elem in element:
        local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if local_name == tag:
            return elem
    return None


def _deep_find(element: ET.Element, tag: str) -> ET.Element | None:
    """Recursively find the first element matching tag, ignoring namespaces."""
    local_name = element.tag.split("}")[-1] if "}" in element.tag else element.tag
    if local_name == tag:
        return element
    for child in element:
        result = _deep_find(child, tag)
        if result is not None:
            return result
    return None


def _deep_findall(element: ET.Element, tag: str) -> list[ET.Element]:
    """Recursively find all elements matching tag, ignoring namespaces."""
    results: list[ET.Element] = []
    local_name = element.tag.split("}")[-1] if "}" in element.tag else element.tag
    if local_name == tag:
        results.append(element)
    for child in element:
        results.extend(_deep_findall(child, tag))
    return results


def _int_or_default(value: str, default: int = 0) -> int:
    """Safely convert a value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# --- Read-Only Tools ---


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def junos_get_routing_instances(host: str) -> dict:
    """Get routing instances from a Juniper JunOS device.

    JunOS uses routing instances instead of VRFs. Returns instance name,
    type (virtual-router, vrf, forwarding), interfaces, and route counts.

    Args:
        host: Hostname, IP address, or inventory name of the device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "junos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Routing instances are only available on JunOS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show route instance"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get routing instances: {error_msg}"}

    data = result[0].get("output", "")
    return {
        "status": "success",
        "device": host,
        "data": _normalize_routing_instances(data),
    }


def _normalize_routing_instances(xml_str: str) -> dict:
    """Normalize ``<get-instance-information/>`` NETCONF XML response.

    Returns a dict with ``instances`` list and ``instance_count``.
    """
    instances: list[dict] = []

    if not xml_str or not xml_str.strip():
        return {"instances": [], "instance_count": 0}

    try:
        root = ET.fromstring(xml_str)  # noqa: S314
    except ET.ParseError:
        return {"instances": [], "instance_count": 0}

    # Find all instance-core elements (each represents a routing instance)
    for inst_elem in _deep_findall(root, "instance-core"):
        name = _xml_find_text(inst_elem, "instance-name")
        inst_type = _xml_find_text(inst_elem, "instance-type")
        state = _xml_find_text(inst_elem, "instance-state")

        # Gather interfaces
        interfaces: list[str] = []
        for intf_elem in _deep_findall(inst_elem, "instance-interface"):
            intf_name = _xml_find_text(intf_elem, "interface-name")
            if intf_name:
                interfaces.append(intf_name)

        # Route count from instance-rib
        route_count = 0
        for rib_elem in _deep_findall(inst_elem, "instance-rib"):
            count_str = _xml_find_text(rib_elem, "active-route-count")
            route_count += _int_or_default(count_str)

        if name:
            instances.append(
                {
                    "name": name,
                    "type": inst_type,
                    "state": state,
                    "interfaces": interfaces,
                    "route_count": route_count,
                }
            )

    return {
        "instances": instances,
        "instance_count": len(instances),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def junos_get_firewall_filters(host: str) -> dict:
    """Get firewall filter details from a Juniper JunOS device.

    JunOS uses firewall filters instead of ACLs. Returns filter names,
    terms (rules), match conditions, and actions.

    Args:
        host: Hostname, IP address, or inventory name of the device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "junos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Firewall filters are only available on JunOS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show firewall"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get firewall filters: {error_msg}"}

    data = result[0].get("output", "")
    return {
        "status": "success",
        "device": host,
        "data": _normalize_firewall_filters(data),
    }


def _normalize_firewall_filters(xml_str: str) -> dict:
    """Normalize ``<get-firewall-information/>`` NETCONF XML response.

    Returns a dict with ``filters`` list and ``filter_count``.
    """
    filters: list[dict] = []

    if not xml_str or not xml_str.strip():
        return {"filters": [], "filter_count": 0}

    try:
        root = ET.fromstring(xml_str)  # noqa: S314
    except ET.ParseError:
        return {"filters": [], "filter_count": 0}

    for filter_elem in _deep_findall(root, "filter-information"):
        filter_name = _xml_find_text(filter_elem, "filter-name")

        terms: list[dict] = []
        for counter_elem in _deep_findall(filter_elem, "counter"):
            term_name = _xml_find_text(counter_elem, "counter-name")
            packets = _int_or_default(_xml_find_text(counter_elem, "packet-count"))
            byte_count = _int_or_default(_xml_find_text(counter_elem, "byte-count"))

            terms.append(
                {
                    "name": term_name,
                    "packet_count": packets,
                    "byte_count": byte_count,
                }
            )

        if filter_name:
            filters.append(
                {
                    "name": filter_name,
                    "terms": terms,
                    "term_count": len(terms),
                }
            )

    return {
        "filters": filters,
        "filter_count": len(filters),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def junos_get_system_alarms(host: str) -> dict:
    """Get active system alarms from a Juniper JunOS device.

    Returns alarm class (major/minor), description, and timestamp.

    Args:
        host: Hostname, IP address, or inventory name of the device.
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "junos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"System alarms are only available on JunOS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show(["show system alarms"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get system alarms: {error_msg}"}

    data = result[0].get("output", "")
    return {
        "status": "success",
        "device": host,
        "data": _normalize_system_alarms(data),
    }


def _normalize_system_alarms(xml_str: str) -> dict:
    """Normalize ``<get-system-alarm-information/>`` NETCONF XML response.

    Returns a dict with ``alarms`` list and ``alarm_count``.
    """
    alarms: list[dict] = []

    if not xml_str or not xml_str.strip():
        return {"alarms": [], "alarm_count": 0}

    try:
        root = ET.fromstring(xml_str)  # noqa: S314
    except ET.ParseError:
        return {"alarms": [], "alarm_count": 0}

    for alarm_elem in _deep_findall(root, "alarm-detail"):
        alarm_class = _xml_find_text(alarm_elem, "alarm-class")
        alarm_time = _xml_find_text(alarm_elem, "alarm-time")
        alarm_description = _xml_find_text(alarm_elem, "alarm-description")
        alarm_type = _xml_find_text(alarm_elem, "alarm-short-description")

        alarms.append(
            {
                "class": alarm_class,
                "time": alarm_time,
                "description": alarm_description,
                "type": alarm_type,
            }
        )

    return {
        "alarms": alarms,
        "alarm_count": len(alarms),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def junos_get_commit_history(host: str, count: int = 10) -> dict:
    """Get commit history from a Juniper JunOS device.

    JunOS tracks every configuration commit with user, timestamp, and comment.

    Args:
        host: Hostname, IP address, or inventory name of the device.
        count: Number of recent commits to return (default 10, max 50).
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if count < 1 or count > 50:
        return {"status": "error", "device": host, "error": "count must be between 1 and 50"}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "junos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Commit history is only available on JunOS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show([f"show system commit count {count}"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to get commit history: {error_msg}"}

    data = result[0].get("output", "")
    return {
        "status": "success",
        "device": host,
        "data": _normalize_commit_history(data),
    }


def _normalize_commit_history(xml_str: str) -> dict:
    """Normalize ``<get-commit-information/>`` NETCONF XML response.

    Returns a dict with ``commits`` list and ``commit_count``.
    """
    commits: list[dict] = []

    if not xml_str or not xml_str.strip():
        return {"commits": [], "commit_count": 0}

    try:
        root = ET.fromstring(xml_str)  # noqa: S314
    except ET.ParseError:
        return {"commits": [], "commit_count": 0}

    for commit_elem in _deep_findall(root, "commit-history"):
        sequence = _int_or_default(_xml_find_text(commit_elem, "sequence-number"))
        user = _xml_find_text(commit_elem, "user")
        client = _xml_find_text(commit_elem, "client")
        date_time = _xml_find_text(commit_elem, "date-time")
        comment = _xml_find_text(commit_elem, "log")

        commits.append(
            {
                "sequence": sequence,
                "user": user,
                "client": client,
                "date_time": date_time,
                "comment": comment,
            }
        )

    return {
        "commits": commits,
        "commit_count": len(commits),
    }


@mcp.tool(annotations=READ_ONLY)
@handle_tool_errors
def junos_compare_rollback(host: str, rollback_id: int = 0) -> dict:
    """Compare current config against a rollback point on a Juniper JunOS device.

    Args:
        host: Hostname, IP address, or inventory name of the device.
        rollback_id: Rollback point to compare against (0 = last committed, 1-49).
    """
    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if rollback_id < 0 or rollback_id > 49:
        return {"status": "error", "device": host, "error": "rollback_id must be between 0 and 49"}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "junos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Rollback comparison is only available on JunOS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_show([f"show configuration rollback compare {rollback_id}"])
    if not result or "error" in result[0]:
        error_msg = result[0].get("error", "Unknown error") if result else "No response"
        return {"status": "error", "device": host, "error": f"Failed to compare rollback: {error_msg}"}

    output = result[0].get("output", "")
    return {
        "status": "success",
        "device": host,
        "data": _normalize_rollback_compare(output),
    }


def _normalize_rollback_compare(output: str) -> dict:
    """Normalize rollback comparison output.

    Returns a dict with ``diff`` text and summary of additions/deletions.
    """
    if not output or not output.strip():
        return {"diff": "", "additions": 0, "deletions": 0, "has_changes": False}

    additions = 0
    deletions = 0
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("+") and not stripped.startswith("+++"):
            additions += 1
        elif stripped.startswith("-") and not stripped.startswith("---"):
            deletions += 1

    return {
        "diff": output.strip(),
        "additions": additions,
        "deletions": deletions,
        "has_changes": additions > 0 or deletions > 0,
    }


# --- Write Tools ---


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def junos_commit_confirmed(host: str, minutes: int = 10, comment: str = "") -> dict:
    """[WRITE] Commit configuration with automatic rollback on a Juniper JunOS device.

    If not confirmed within the specified time, JunOS automatically rolls back.
    This is a safety feature for remote changes.

    Args:
        host: Hostname, IP address, or inventory name of the device.
        minutes: Rollback timeout in minutes (default 10, max 60).
        comment: Commit comment for audit trail.
    """
    read_only_err = check_read_only()
    if read_only_err:
        return {"status": "error", "device": host, "error": read_only_err}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if minutes < 1 or minutes > 60:
        return {"status": "error", "device": host, "error": "minutes must be between 1 and 60"}

    if comment:
        # Validate comment for injection characters
        if re.search(r"[;|`\n\r\x00]|\$\(|\$\{", comment):
            return {"status": "error", "device": host, "error": "comment contains invalid characters"}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "junos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Commit-confirmed is only available on JunOS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    # Build commit-confirmed command
    cmd = f"commit confirmed {minutes}"
    if comment:
        cmd += f' comment "{comment}"'

    result = driver.run_config([cmd])
    if not result:
        return {"status": "error", "device": host, "error": "No response from commit-confirmed"}

    # Check for errors in result
    for r in result:
        if isinstance(r, str) and "error" in r.lower():
            return {"status": "error", "device": host, "error": r}

    return {
        "status": "success",
        "device": host,
        "data": {
            "message": f"Commit-confirmed applied with {minutes}-minute rollback timer",
            "rollback_minutes": minutes,
            "comment": comment,
            "confirmed": False,
        },
    }


@mcp.tool(annotations=WRITE_SAFE)
@handle_tool_errors
def junos_confirm_commit(host: str) -> dict:
    """[WRITE] Confirm a pending commit-confirmed on a Juniper JunOS device.

    Must be called before the timeout expires to prevent automatic rollback.

    Args:
        host: Hostname, IP address, or inventory name of the device.
    """
    read_only_err = check_read_only()
    if read_only_err:
        return {"status": "error", "device": host, "error": read_only_err}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    driver = conn_mgr.get_driver(host)

    if getattr(driver, "platform", None) != "junos":
        return {
            "status": "error",
            "device": host,
            "error": (
                f"Confirm commit is only available on JunOS devices. "
                f"Device platform: {getattr(driver, 'platform', 'unknown')}"
            ),
        }

    result = driver.run_config(["commit"])
    if not result:
        return {"status": "error", "device": host, "error": "No response from confirm commit"}

    # Check for errors in result
    for r in result:
        if isinstance(r, str) and "error" in r.lower():
            return {"status": "error", "device": host, "error": r}

    return {
        "status": "success",
        "device": host,
        "data": {
            "message": "Commit confirmed successfully — rollback timer cancelled",
            "confirmed": True,
        },
    }
