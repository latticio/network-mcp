"""gNMI telemetry tools for Arista EOS.

These tools use pygnmi to interact with EOS devices via gNMI (gRPC Network
Management Interface). gNMI is an optional dependency — tools gracefully
degrade if pygnmi is not installed.

Requires gNMI to be enabled on the target device::

    management api gnmi
      transport grpc default
      no shutdown
"""

import logging

from network_mcp.gnmi_connection import (
    GNMI_AVAILABLE,
    GNMI_NOT_INSTALLED_ERROR,
    GnmiManager,
)
from network_mcp.helpers import READ_ONLY, WRITE_SAFE, check_read_only, validate_host
from network_mcp.server import mcp, settings

logger = logging.getLogger("network-mcp.gnmi")

# Module-level gNMI manager — uses NetworkSettings instance from server
gnmi_mgr = GnmiManager(settings=settings)


@mcp.tool(annotations=READ_ONLY)
def eos_gnmi_get(host: str, paths: list[str]) -> dict:
    """Retrieve operational/config data from an Arista EOS device via gNMI Get.

    Uses OpenConfig YANG paths to query structured data. Each path must start
    with ``/``. Multiple paths can be queried in a single request.

    Common OpenConfig paths for Arista EOS:
      - /interfaces/interface/state — interface operational state
      - /interfaces/interface[name=Ethernet1]/state/counters — interface counters
      - /network-instances/network-instance[name=default]/.../bgp/neighbors — BGP neighbors
      - /system/state — system hostname, uptime
      - /components/component/state/temperature/instant — temperature sensors
      - /lldp/interfaces/interface/neighbors — LLDP neighbors

    Requires gNMI enabled on device:
      management api gnmi
        transport grpc default
        no shutdown

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        paths: List of OpenConfig/YANG paths to query (each must start with /).
    """
    if not GNMI_AVAILABLE:
        return {**GNMI_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if not paths:
        return {"status": "error", "device": host, "error": "At least one gNMI path is required."}

    for p in paths:
        if not p.startswith("/"):
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid gNMI path: '{p}'. Paths must start with /",
            }

    return gnmi_mgr.execute_get(host, paths, port=settings.gnmi_port)


@mcp.tool(annotations=READ_ONLY)
def eos_gnmi_subscribe_once(host: str, paths: list[str]) -> dict:
    """One-shot gNMI subscription to retrieve a snapshot of telemetry data.

    Uses gNMI Subscribe RPC in ONCE mode — collects all updates until the
    device sends a sync_response, then returns the collected data. This is
    similar to gnmi_get but uses the Subscribe RPC which may return data
    in a different structure on some devices.

    Useful for paths that work better with Subscribe than Get, or for
    getting a consistent point-in-time snapshot of multiple counters.

    Requires gNMI enabled on device:
      management api gnmi
        transport grpc default
        no shutdown

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        paths: List of OpenConfig/YANG paths to subscribe to (each must start with /).
    """
    if not GNMI_AVAILABLE:
        return {**GNMI_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if not paths:
        return {"status": "error", "device": host, "error": "At least one gNMI path is required."}

    for p in paths:
        if not p.startswith("/"):
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid gNMI path: '{p}'. Paths must start with /",
            }

    return gnmi_mgr.execute_subscribe_once(host, paths, port=settings.gnmi_port)


@mcp.tool(annotations=READ_ONLY)
def eos_gnmi_capabilities(host: str) -> dict:
    """Get gNMI capabilities from an Arista EOS device.

    Returns the supported YANG models, encodings, and gNMI protocol version.
    Use this to discover what OpenConfig models and paths are available on
    a device before querying with gnmi_get or gnmi_subscribe_once.

    Requires gNMI enabled on device:
      management api gnmi
        transport grpc default
        no shutdown

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    if not GNMI_AVAILABLE:
        return {**GNMI_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    return gnmi_mgr.execute_capabilities(host, port=settings.gnmi_port)


@mcp.tool(annotations=READ_ONLY)
def eos_gnmi_subscribe_stream(host: str, paths: list[str], duration: int = 10) -> dict:
    """Stream gNMI telemetry data from an Arista EOS device for a specified duration.

    Uses gNMI Subscribe RPC in STREAM mode — collects updates for ``duration``
    seconds and returns all received data. Useful for monitoring counters,
    interface state changes, or any telemetry that changes over time.

    The duration is capped at the configured gNMI timeout (GNMI_TIMEOUT, default 10s).

    Requires gNMI enabled on device:
      management api gnmi
        transport grpc default
        no shutdown

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        paths: List of OpenConfig/YANG paths to subscribe to (each must start with /).
        duration: Seconds to collect stream updates (default: 10, max: GNMI_TIMEOUT).
    """
    if not GNMI_AVAILABLE:
        return {**GNMI_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if not paths:
        return {"status": "error", "device": host, "error": "At least one gNMI path is required."}

    for p in paths:
        if not p.startswith("/"):
            return {
                "status": "error",
                "device": host,
                "error": f"Invalid gNMI path: '{p}'. Paths must start with /",
            }

    if duration <= 0:
        return {"status": "error", "device": host, "error": "Duration must be a positive integer."}

    return gnmi_mgr.execute_subscribe_stream(host, paths, duration=duration, port=settings.gnmi_port)


@mcp.tool(annotations=WRITE_SAFE)
def eos_gnmi_set(host: str, updates: list[dict] | None = None, deletes: list[str] | None = None) -> dict:
    """[WRITE] Modify device configuration via gNMI Set on an Arista EOS device.

    Applies configuration changes using the gNMI Set RPC. Supports both update
    (set value at path) and delete (remove config at path) operations.
    Requires NET_READ_ONLY=false.

    Requires gNMI enabled on device:
      management api gnmi
        transport grpc default
        no shutdown

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        updates: List of update dicts, each with 'path' (str starting with /) and 'val' (dict or str).
        deletes: List of OpenConfig/YANG paths to delete (each must start with /).
    """
    if not GNMI_AVAILABLE:
        return {**GNMI_NOT_INSTALLED_ERROR, "device": host}

    ro_err = check_read_only()
    if ro_err:
        return {"status": "error", "device": host, "error": ro_err}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    if not updates and not deletes:
        return {"status": "error", "device": host, "error": "At least one update or delete is required."}

    # Validate and convert updates to tuples
    update_tuples = None
    if updates:
        update_tuples = []
        for u in updates:
            path = u.get("path", "")
            if not path.startswith("/"):
                return {
                    "status": "error",
                    "device": host,
                    "error": f"Invalid gNMI path: '{path}'. Paths must start with /",
                }
            update_tuples.append((path, u.get("val", {})))

    # Validate delete paths
    if deletes:
        for d in deletes:
            if not d.startswith("/"):
                return {
                    "status": "error",
                    "device": host,
                    "error": f"Invalid gNMI path: '{d}'. Paths must start with /",
                }

    return gnmi_mgr.execute_set(host, updates=update_tuples, deletes=deletes, port=settings.gnmi_port)
