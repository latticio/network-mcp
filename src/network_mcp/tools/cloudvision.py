"""CloudVision Portal (CVP) integration tools for Arista EOS.

These tools query CloudVision Portal's REST API to retrieve inventory
and event data. Requires EOS_CVP_URL and EOS_CVP_TOKEN to be configured.

Uses urllib.request from stdlib — no additional dependencies required.
"""

import json
import logging
import ssl
import urllib.error
import urllib.request

from network_mcp.helpers import READ_ONLY, validate_host
from network_mcp.server import mcp, settings

logger = logging.getLogger("network-mcp.cloudvision")

_CVP_NOT_CONFIGURED = {
    "status": "error",
    "error": (
        "CloudVision is not configured. Set EOS_CVP_URL and EOS_CVP_TOKEN "
        "environment variables to enable CloudVision integration."
    ),
}


def _cvp_request(path: str, method: str = "GET") -> dict:
    """Make an authenticated request to the CloudVision Portal REST API.

    Args:
        path: API path (e.g., "/api/resources/inventory/v1/Device/all").
        method: HTTP method.

    Returns:
        Parsed JSON response dict.

    Raises:
        RuntimeError: If CVP is not configured.
        urllib.error.URLError: On network/HTTP errors.
    """
    if not settings.eos_cvp_url or not settings.eos_cvp_token:
        raise RuntimeError("CloudVision not configured")

    url = settings.eos_cvp_url.rstrip("/") + path
    token = settings.eos_cvp_token.get_secret_value()

    req = urllib.request.Request(url, method=method)  # noqa: S310
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")

    # SSL verification is configurable via EOS_CVP_VERIFY_SSL (default: False for self-signed certs)
    ctx = ssl.create_default_context()
    if not settings.eos_cvp_verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, timeout=settings.timeout_connect, context=ctx) as resp:  # noqa: S310
        result: dict = json.loads(resp.read().decode("utf-8"))
        return result


@mcp.tool(annotations=READ_ONLY)
def eos_get_cvp_inventory(host: str | None = None) -> dict:
    """Get device inventory from CloudVision Portal.

    When to use: To list all devices managed by CloudVision, or get details for a specific device.
    Output: Device list with hostname, IP, model, EOS version, streaming status.

    Requires EOS_CVP_URL and EOS_CVP_TOKEN to be configured.

    Args:
        host: Optional hostname or IP to filter for a specific device. Returns all if not set.
    """
    if not settings.eos_cvp_url or not settings.eos_cvp_token:
        return {**_CVP_NOT_CONFIGURED, "device": host} if host else _CVP_NOT_CONFIGURED

    if host:
        host_err = validate_host(host)
        if host_err:
            return {"status": "error", "device": host, "error": host_err}

    try:
        data = _cvp_request("/api/resources/inventory/v1/Device/all")
    except RuntimeError:
        return {**_CVP_NOT_CONFIGURED, "device": host} if host else _CVP_NOT_CONFIGURED
    except urllib.error.URLError as e:
        result = {"status": "error", "error": f"Failed to connect to CloudVision: {e}"}
        if host:
            result["device"] = host
        return result

    # Parse response — CVP Resource API returns {"result": {"value": {...}}} entries
    raw_devices = data.get("result", data) if isinstance(data, dict) else data
    devices = []

    # Handle both list and dict-of-results formats
    items = raw_devices if isinstance(raw_devices, list) else [raw_devices]
    for item in items:
        value = item.get("value", item) if isinstance(item, dict) else item
        if not isinstance(value, dict):
            continue
        device = {
            "hostname": value.get("hostname", value.get("fqdn", "")),
            "ip_address": value.get("ipAddress", ""),
            "model": value.get("modelName", ""),
            "eos_version": value.get("softwareVersion", value.get("eosVersion", "")),
            "serial_number": value.get("serialNumber", ""),
            "streaming_status": value.get("streamingStatus", ""),
        }
        devices.append(device)

    # Filter by host if specified
    if host:
        devices = [d for d in devices if host.lower() in (d["hostname"].lower(), d["ip_address"].lower())]

    return {
        "status": "success",
        "data": {"total": len(devices), "devices": devices},
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_cvp_events(host: str | None = None, severity: str | None = None) -> dict:
    """Get recent events from CloudVision Portal.

    When to use: To check for active alerts, warnings, or informational events across the network.
    Output: List of events with title, severity, device, timestamp.

    Requires EOS_CVP_URL and EOS_CVP_TOKEN to be configured.

    Args:
        host: Optional hostname or IP to filter events for a specific device.
        severity: Optional severity filter: 'CRITICAL', 'WARNING', 'INFO', 'ERROR'.
    """
    if not settings.eos_cvp_url or not settings.eos_cvp_token:
        return {**_CVP_NOT_CONFIGURED, "device": host} if host else _CVP_NOT_CONFIGURED

    if host:
        host_err = validate_host(host)
        if host_err:
            return {"status": "error", "device": host, "error": host_err}

    if severity and severity.upper() not in ("CRITICAL", "WARNING", "INFO", "ERROR"):
        result = {"status": "error", "error": f"Invalid severity: '{severity}'. Use CRITICAL, WARNING, INFO, or ERROR."}
        if host:
            result["device"] = host
        return result

    try:
        data = _cvp_request("/api/resources/event/v1/Event/all")
    except RuntimeError:
        return {**_CVP_NOT_CONFIGURED, "device": host} if host else _CVP_NOT_CONFIGURED
    except urllib.error.URLError as e:
        result = {"status": "error", "error": f"Failed to connect to CloudVision: {e}"}
        if host:
            result["device"] = host
        return result

    # Parse response
    raw_events = data.get("result", data) if isinstance(data, dict) else data
    events = []

    items = raw_events if isinstance(raw_events, list) else [raw_events]
    for item in items:
        value = item.get("value", item) if isinstance(item, dict) else item
        if not isinstance(value, dict):
            continue
        event = {
            "title": value.get("title", value.get("description", "")),
            "severity": value.get("severity", ""),
            "device": value.get("deviceId", value.get("hostname", "")),
            "timestamp": value.get("timestamp", value.get("lastUpdatedTime", "")),
        }
        events.append(event)

    # Apply filters
    if host:
        events = [e for e in events if host.lower() in e["device"].lower()]
    if severity:
        events = [e for e in events if e["severity"].upper() == severity.upper()]

    return {
        "status": "success",
        "data": {"total": len(events), "events": events},
    }
