"""NetBox DCIM integration — bidirectional inventory sync, drift detection, cable audit."""

from __future__ import annotations

import copy
import logging
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from network_mcp.helpers import READ_ONLY, WRITE_SAFE
from network_mcp.server import mcp, settings

logger = logging.getLogger("network-mcp.netbox")

try:
    import pynetbox
except ImportError:
    pynetbox = None  # type: ignore[assignment]


def _is_transient_error(exc: BaseException) -> bool:
    """Determine if an exception is transient and worth retrying.

    Retries on connection errors, timeouts, and HTTP 5xx server errors.
    Does not retry on 4xx client errors (bad request, auth, not found, etc.).
    """
    try:
        import requests

        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code >= 500
    except ImportError:
        pass

    if pynetbox is not None:
        try:
            if isinstance(exc, pynetbox.RequestError):
                status = getattr(exc, "status_code", None) or getattr(exc, "req", None)
                if isinstance(status, int) and status >= 500:
                    return True
                if status is None:
                    return True
        except Exception:
            logger.debug("Error checking pynetbox exception type")

    error_msg = str(exc).lower()
    transient_indicators = ["connection", "timeout", "timed out", "temporarily unavailable", "502", "503", "504"]
    return any(indicator in error_msg for indicator in transient_indicators)


def _netbox_retry_decorator():
    """Build a tenacity retry decorator using current settings."""
    return retry(
        stop=stop_after_attempt(settings.net_netbox_retries),
        wait=wait_exponential(multiplier=settings.net_netbox_retry_delay, min=1, max=30),
        retry=retry_if_exception(_is_transient_error),
        before_sleep=lambda rs: logger.warning("NetBox API retry %d for %s", rs.attempt_number, rs.fn.__name__),
    )


def _with_netbox_retry(func, *args, **kwargs):
    """Execute a function with NetBox retry logic."""
    decorated = _netbox_retry_decorator()(func)
    return decorated(*args, **kwargs)


_PYNETBOX_MISSING = {
    "status": "error",
    "error": "pynetbox not installed. Install with: pip install latticio[netbox]",
}

_NETBOX_NOT_CONFIGURED = {
    "status": "error",
    "error": (
        "NetBox is not configured. Set NETBOX_URL and NETBOX_TOKEN environment variables to enable NetBox integration."
    ),
}

# Map NetBox platform slugs to network-mcp platform identifiers
_PLATFORM_MAP: dict[str, str] = {
    "arista-eos": "eos",
    "arista_eos": "eos",
    "cisco-ios-xe": "iosxe",
    "cisco_ios_xe": "iosxe",
    "cisco-nxos": "nxos",
    "cisco_nxos": "nxos",
    "juniper-junos": "junos",
    "juniper_junos": "junos",
}


def _get_netbox_client():  # type: ignore[return]
    """Get an authenticated pynetbox API client.

    Returns:
        pynetbox.api instance configured with URL and token from settings.

    Raises:
        RuntimeError: If NETBOX_URL or NETBOX_TOKEN is not configured.
    """
    if not settings.netbox_url or not settings.netbox_token:
        raise RuntimeError("NETBOX_URL and NETBOX_TOKEN must be set")
    return pynetbox.api(settings.netbox_url, token=settings.netbox_token.get_secret_value())


@mcp.tool(annotations=READ_ONLY)
def net_sync_from_netbox(
    site: str | None = None,
    role: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Pull devices from NetBox DCIM and sync to local inventory.

    Queries NetBox for active devices with optional filters, maps NetBox platform
    slugs to network-mcp platforms, and returns the synced device list.

    Args:
        site: Optional NetBox site slug to filter devices (e.g., 'dc1').
        role: Optional device role slug to filter (e.g., 'spine', 'leaf').
        tag: Optional tag to filter devices (e.g., 'production').
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import NETBOX_SYNC_RESPONSE

        return copy.deepcopy(NETBOX_SYNC_RESPONSE)

    if pynetbox is None:
        return _PYNETBOX_MISSING

    if not settings.netbox_url or not settings.netbox_token:
        return _NETBOX_NOT_CONFIGURED

    try:
        nb = _get_netbox_client()
        filters: dict[str, Any] = {"status": "active"}
        if site:
            filters["site"] = site
        if role:
            filters["role"] = role
        if tag:
            filters["tag"] = tag

        devices = list(nb.dcim.devices.filter(**filters))
        synced = []
        for device in devices:
            primary_ip = str(device.primary_ip).split("/")[0] if device.primary_ip else None
            platform = _PLATFORM_MAP.get(str(device.platform).lower(), "eos") if device.platform else "eos"

            synced.append(
                {
                    "name": device.name,
                    "host": primary_ip or device.name,
                    "platform": platform,
                    "role": str(device.role) if device.role else None,
                    "site": str(device.site) if device.site else None,
                    "serial": device.serial,
                    "tags": [str(t) for t in device.tags],
                }
            )

        return {"status": "success", "synced_count": len(synced), "devices": synced}
    except Exception as e:
        return {"status": "error", "error": f"NetBox sync failed: {e}"}


@mcp.tool(annotations=WRITE_SAFE)
def net_push_to_netbox(host: str) -> dict[str, Any]:
    """[WRITE] Push discovered device facts from a live device to NetBox DCIM.

    Queries the live device for facts (serial number, OS version), finds the
    matching device in NetBox by hostname, and updates its serial number.

    Args:
        host: Hostname, IP address, or inventory name of the device to push facts from.
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import NETBOX_PUSH_RESPONSE

        return {**copy.deepcopy(NETBOX_PUSH_RESPONSE), "device": host}

    if pynetbox is None:
        return {**_PYNETBOX_MISSING, "device": host}

    if not settings.netbox_url or not settings.netbox_token:
        return {**_NETBOX_NOT_CONFIGURED, "device": host}

    try:
        nb = _get_netbox_client()

        # Get live device facts via the vendor-agnostic tool
        from network_mcp.tools.common.device import net_get_device_info

        facts = net_get_device_info(host)
        if facts["status"] != "success":
            return facts

        data = facts["data"]
        hostname = data.get("hostname", host)

        # Find device in NetBox by hostname
        nb_device = nb.dcim.devices.get(name=hostname)
        if nb_device:
            nb_device.serial = data.get("serial", "")
            nb_device.save()
            return {
                "status": "success",
                "device": host,
                "action": "updated",
                "netbox_id": nb_device.id,
                "updated_fields": {"serial": data.get("serial", "")},
            }
        return {
            "status": "error",
            "device": host,
            "error": f"Device '{hostname}' not found in NetBox. Create the device in NetBox first.",
        }
    except Exception as e:
        return {"status": "error", "device": host, "error": f"NetBox push failed: {e}"}


@mcp.tool(annotations=READ_ONLY)
def net_netbox_drift_check(site: str | None = None) -> dict[str, Any]:
    """Compare NetBox DCIM state vs live device state and report mismatches.

    For each active device in NetBox (optionally filtered by site), queries the
    live device and compares serial numbers and reachability. Reports drifts with
    severity levels: 'critical' for unreachable devices, 'warning' for field mismatches.

    Args:
        site: Optional NetBox site slug to limit the check scope.
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import NETBOX_DRIFT_RESPONSE

        return copy.deepcopy(NETBOX_DRIFT_RESPONSE)

    if pynetbox is None:
        return _PYNETBOX_MISSING

    if not settings.netbox_url or not settings.netbox_token:
        return _NETBOX_NOT_CONFIGURED

    try:
        nb = _get_netbox_client()
        filters: dict[str, Any] = {"status": "active"}
        if site:
            filters["site"] = site

        devices = list(nb.dcim.devices.filter(**filters))
        drifts: list[dict[str, Any]] = []
        checked = 0

        for nb_device in devices:
            primary_ip = str(nb_device.primary_ip).split("/")[0] if nb_device.primary_ip else None
            if not primary_ip:
                drifts.append(
                    {
                        "device": nb_device.name,
                        "severity": "warning",
                        "field": "primary_ip",
                        "drift": "No primary IP assigned in NetBox",
                    }
                )
                continue

            checked += 1
            try:
                from network_mcp.tools.common.device import net_get_device_info

                live = net_get_device_info(primary_ip)
                if live["status"] != "success":
                    drifts.append(
                        {
                            "device": nb_device.name,
                            "severity": "critical",
                            "drift": "unreachable",
                            "detail": live.get("error", "Device query failed"),
                        }
                    )
                    continue

                live_data = live["data"]

                # Compare serial number
                if nb_device.serial and live_data.get("serial") and nb_device.serial != live_data["serial"]:
                    drifts.append(
                        {
                            "device": nb_device.name,
                            "severity": "warning",
                            "field": "serial",
                            "netbox": nb_device.serial,
                            "live": live_data["serial"],
                        }
                    )

                # Compare model if available
                nb_model = str(nb_device.device_type) if nb_device.device_type else None
                live_model = live_data.get("model")
                if nb_model and live_model and nb_model.lower() != live_model.lower():
                    drifts.append(
                        {
                            "device": nb_device.name,
                            "severity": "warning",
                            "field": "model",
                            "netbox": nb_model,
                            "live": live_model,
                        }
                    )

            except Exception as e:
                drifts.append(
                    {
                        "device": nb_device.name,
                        "severity": "critical",
                        "drift": f"Error checking device: {e}",
                    }
                )

        return {
            "status": "success",
            "total_devices": len(devices),
            "devices_checked": checked,
            "drift_count": len(drifts),
            "drifts": drifts,
        }
    except Exception as e:
        return {"status": "error", "error": f"NetBox drift check failed: {e}"}


@mcp.tool(annotations=READ_ONLY)
def net_netbox_cable_audit(site: str | None = None) -> dict[str, Any]:
    """Validate LLDP neighbors match NetBox cable records.

    Fetches cables from NetBox, retrieves LLDP neighbor data from live devices,
    and compares the two. Reports mismatches where NetBox cable endpoints don't
    match actual LLDP neighbor discovery.

    Args:
        site: Optional NetBox site slug to limit the audit scope.
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import NETBOX_CABLE_AUDIT_RESPONSE

        return copy.deepcopy(NETBOX_CABLE_AUDIT_RESPONSE)

    if pynetbox is None:
        return _PYNETBOX_MISSING

    if not settings.netbox_url or not settings.netbox_token:
        return _NETBOX_NOT_CONFIGURED

    try:
        nb = _get_netbox_client()

        # Get active devices with their primary IPs for host lookup
        device_filters: dict[str, Any] = {"status": "active"}
        if site:
            device_filters["site"] = site
        nb_devices = list(nb.dcim.devices.filter(**device_filters))

        # Build a map of device name -> primary_ip for querying LLDP
        device_ip_map: dict[str, str] = {}
        for dev in nb_devices:
            if dev.primary_ip:
                device_ip_map[dev.name] = str(dev.primary_ip).split("/")[0]

        # Fetch cables (filter by site if provided)
        cable_filters: dict[str, Any] = {}
        if site:
            cable_filters["site"] = site
        cables = list(nb.dcim.cables.filter(**cable_filters))

        mismatches: list[dict[str, Any]] = []
        cables_checked = 0

        # Cache LLDP results per device to avoid duplicate queries
        lldp_cache: dict[str, dict] = {}

        for cable in cables:
            # Extract cable endpoint info
            a_term = cable.a_terminations[0] if cable.a_terminations else None
            b_term = cable.b_terminations[0] if cable.b_terminations else None
            if not a_term or not b_term:
                continue

            # Get device names and interface names from terminations
            a_device_name = str(a_term.object.device) if hasattr(a_term.object, "device") else None
            a_interface = str(a_term.object) if a_term.object else None
            b_device_name = str(b_term.object.device) if hasattr(b_term.object, "device") else None
            b_interface = str(b_term.object) if b_term.object else None

            if not a_device_name or not b_device_name:
                continue

            # Try to verify from side A's perspective
            a_ip = device_ip_map.get(a_device_name)
            if not a_ip:
                continue

            cables_checked += 1

            # Get LLDP neighbors for device A (with caching)
            if a_device_name not in lldp_cache:
                try:
                    from network_mcp.tools.common.switching import net_get_lldp_neighbors

                    lldp_result = net_get_lldp_neighbors(a_ip)
                    lldp_cache[a_device_name] = lldp_result if lldp_result["status"] == "success" else {}
                except Exception:
                    lldp_cache[a_device_name] = {}

            lldp_data = lldp_cache.get(a_device_name, {}).get("data", {})

            # Check if the cable's A-side interface has an LLDP neighbor matching B-side
            if a_interface and a_interface in lldp_data:
                neighbors = lldp_data[a_interface]
                found_match = any(b_device_name.lower() in n.get("hostname", "").lower() for n in neighbors)
                if not found_match:
                    actual_neighbors = [n.get("hostname", "unknown") for n in neighbors]
                    mismatches.append(
                        {
                            "cable_id": cable.id,
                            "side_a": {"device": a_device_name, "interface": a_interface},
                            "side_b_expected": {"device": b_device_name, "interface": b_interface},
                            "side_b_actual": actual_neighbors,
                            "severity": "warning",
                            "detail": f"LLDP neighbor on {a_device_name}:{a_interface} does not match NetBox cable",
                        }
                    )
            elif a_interface:
                # Interface exists in NetBox cable but no LLDP neighbor found
                mismatches.append(
                    {
                        "cable_id": cable.id,
                        "side_a": {"device": a_device_name, "interface": a_interface},
                        "side_b_expected": {"device": b_device_name, "interface": b_interface},
                        "severity": "info",
                        "detail": f"No LLDP neighbor found on {a_device_name}:{a_interface}",
                    }
                )

        return {
            "status": "success",
            "cables_checked": cables_checked,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }
    except Exception as e:
        return {"status": "error", "error": f"NetBox cable audit failed: {e}"}
