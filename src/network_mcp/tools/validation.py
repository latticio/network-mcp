"""ANTA (Arista Network Test Automation) validation tools for Arista EOS.

These tools wrap ANTA's test framework to provide structured network validation.
ANTA is an optional dependency — tools gracefully degrade if it is not installed.
"""

import logging
from typing import Any

import yaml  # type: ignore[import-untyped]

from network_mcp.helpers import READ_ONLY, validate_host
from network_mcp.server import conn_mgr, mcp

logger = logging.getLogger("network-mcp")

# --- ANTA availability check ---

ANTA_AVAILABLE = False
try:
    from anta.catalog import AntaCatalog
    from anta.device import AsyncEOSDevice
    from anta.inventory import AntaInventory
    from anta.result_manager import ResultManager
    from anta.runner import main as anta_runner

    ANTA_AVAILABLE = True
except ImportError:
    pass

ANTA_NOT_INSTALLED_ERROR = {
    "status": "error",
    "error": "ANTA is not installed. Install with: pip install network-mcp[anta]",
}


def _get_device_credentials(host: str) -> dict[str, Any]:
    """Resolve device credentials from the connection manager.

    Returns a dict with host, username, password, port, and proto suitable
    for constructing an AsyncEOSDevice.
    """
    if host in conn_mgr._inventory:
        creds = conn_mgr._inventory[host]
        return {
            "host": creds.host,
            "username": creds.username,
            "password": creds.password.get_secret_value(),
            "port": creds.port,
            "proto": "https" if creds.transport == "https" else "http",
        }
    # Fall back to default credentials
    return {
        "host": host,
        "username": conn_mgr._default_username,
        "password": conn_mgr._default_password,
        "port": 443,
        "proto": "https" if conn_mgr._default_transport == "https" else "http",
    }


async def _run_anta_catalog(host: str, catalog: "AntaCatalog") -> dict:
    """Run an ANTA test catalog against a single device and return structured results.

    This is the shared implementation used by all validation tools.
    """
    creds = _get_device_credentials(host)

    device = AsyncEOSDevice(
        host=creds["host"],
        username=creds["username"],
        password=creds["password"],
        port=creds["port"],
        proto=creds["proto"],
        name=host,
        insecure=True,
    )

    inventory = AntaInventory()
    inventory.add_device(device)

    await inventory.connect_inventory()

    if not device.established:
        return {
            "status": "error",
            "device": host,
            "error": f"Could not connect to device {host} via ANTA (asynceapi)",
        }

    manager = ResultManager()
    await anta_runner(manager, inventory, catalog)

    # Parse results into structured output
    test_results = []
    for result in manager.results:
        test_results.append(
            {
                "test": result.test,
                "result": result.result,
                "messages": result.messages if hasattr(result, "messages") else [],
                "description": result.description if hasattr(result, "description") else "",
            }
        )

    passed = sum(1 for r in test_results if r["result"] == "success")
    failed = sum(1 for r in test_results if r["result"] == "failure")
    skipped = sum(1 for r in test_results if r["result"] == "skipped")
    errored = sum(1 for r in test_results if r["result"] == "error")

    return {
        "status": "success",
        "device": host,
        "data": {
            "summary": {
                "total": len(test_results),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "errored": errored,
            },
            "results": test_results,
        },
    }


# --- BGP Validation ---

BGP_CATALOG_DATA: dict[str, Any] = {
    "anta.tests.routing.bgp": [
        {"VerifyBGPPeersHealth": {"address_families": [{"afi": "ipv4", "safi": "unicast"}]}},
    ],
}


@mcp.tool(annotations=READ_ONLY)
async def eos_validate_bgp(host: str) -> dict:
    """Run ANTA BGP validation tests against an Arista EOS device.

    Runs BGP health checks including peer state validation for IPv4 unicast.
    Requires ANTA optional dependency: pip install network-mcp[anta]

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    if not ANTA_AVAILABLE:
        return {**ANTA_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    try:
        catalog = AntaCatalog.from_dict(BGP_CATALOG_DATA)
        return await _run_anta_catalog(host, catalog)
    except Exception as e:
        return {"status": "error", "device": host, "error": f"ANTA validation failed: {e}"}


# --- MLAG Validation ---

MLAG_CATALOG_DATA: dict[str, Any] = {
    "anta.tests.mlag": [
        {"VerifyMlagStatus": None},
        {"VerifyMlagInterfaces": None},
        {"VerifyMlagConfigSanity": None},
    ],
}


@mcp.tool(annotations=READ_ONLY)
async def eos_validate_mlag(host: str) -> dict:
    """Run ANTA MLAG validation tests against an Arista EOS device.

    Checks MLAG status, interface consistency, and configuration sanity.
    Requires ANTA optional dependency: pip install network-mcp[anta]

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    if not ANTA_AVAILABLE:
        return {**ANTA_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    try:
        catalog = AntaCatalog.from_dict(MLAG_CATALOG_DATA)
        return await _run_anta_catalog(host, catalog)
    except Exception as e:
        return {"status": "error", "device": host, "error": f"ANTA validation failed: {e}"}


# --- Interface Validation ---

INTERFACES_CATALOG_DATA: dict[str, Any] = {
    "anta.tests.interfaces": [
        {"VerifyInterfaceUtilization": None},
        {"VerifyInterfaceErrors": None},
        {"VerifyInterfaceDiscards": None},
        {"VerifyInterfaceErrDisabled": None},
    ],
}


@mcp.tool(annotations=READ_ONLY)
async def eos_validate_interfaces(host: str) -> dict:
    """Run ANTA interface validation tests against an Arista EOS device.

    Checks interface utilization, error counters, discards, and err-disabled status.
    Requires ANTA optional dependency: pip install network-mcp[anta]

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    if not ANTA_AVAILABLE:
        return {**ANTA_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    try:
        catalog = AntaCatalog.from_dict(INTERFACES_CATALOG_DATA)
        return await _run_anta_catalog(host, catalog)
    except Exception as e:
        return {"status": "error", "device": host, "error": f"ANTA validation failed: {e}"}


# --- System Validation ---

SYSTEM_CATALOG_DATA: dict[str, Any] = {
    "anta.tests.system": [
        {"VerifyNTP": None},
        {"VerifyCPUUtilization": None},
        {"VerifyMemoryUtilization": None},
        {"VerifyFileSystemUtilization": None},
        {"VerifyCoredump": None},
        {"VerifyAgentLogs": None},
        {"VerifyReloadCause": None},
    ],
}


@mcp.tool(annotations=READ_ONLY)
async def eos_validate_system(host: str) -> dict:
    """Run ANTA system/hardware validation tests against an Arista EOS device.

    Checks NTP sync, CPU/memory/filesystem utilization, coredumps, agent logs,
    and reload cause.
    Requires ANTA optional dependency: pip install network-mcp[anta]

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
    """
    if not ANTA_AVAILABLE:
        return {**ANTA_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    try:
        catalog = AntaCatalog.from_dict(SYSTEM_CATALOG_DATA)
        return await _run_anta_catalog(host, catalog)
    except Exception as e:
        return {"status": "error", "device": host, "error": f"ANTA validation failed: {e}"}


# --- Custom ANTA Test Catalog ---


@mcp.tool(annotations=READ_ONLY)
async def eos_run_anta_tests(host: str, catalog_yaml: str) -> dict:
    """Run an arbitrary ANTA test catalog against an Arista EOS device.

    Accepts a YAML-formatted ANTA test catalog string and runs it against
    the specified device. This allows maximum flexibility for custom validation.
    Requires ANTA optional dependency: pip install network-mcp[anta]

    Example catalog_yaml:
        anta.tests.system:
          - VerifyUptime:
              minimum: 86400
          - VerifyNTP:

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        catalog_yaml: YAML-formatted ANTA test catalog string.
    """
    if not ANTA_AVAILABLE:
        return {**ANTA_NOT_INSTALLED_ERROR, "device": host}

    host_err = validate_host(host)
    if host_err:
        return {"status": "error", "device": host, "error": host_err}

    try:
        catalog_data = yaml.safe_load(catalog_yaml)
    except yaml.YAMLError as e:
        return {"status": "error", "device": host, "error": f"Invalid YAML catalog: {e}"}

    if not isinstance(catalog_data, dict):
        return {
            "status": "error",
            "device": host,
            "error": "Catalog YAML must be a mapping of test modules to test lists",
        }

    try:
        catalog = AntaCatalog.from_dict(catalog_data)
        return await _run_anta_catalog(host, catalog)
    except Exception as e:
        return {"status": "error", "device": host, "error": f"ANTA validation failed: {e}"}
