"""MockNxosDriver — test driver returning realistic NX-API data.

Loads mock JSON responses from this directory and normalizes them through
the same code paths as the real NxosDriver. Implements the full
NetworkDriver protocol for use in cross-vendor testing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("network-mcp")

_MOCK_DATA_DIR = Path(__file__).parent


def _load_mock(filename: str) -> dict:
    """Load a mock JSON response file."""
    path = _MOCK_DATA_DIR / filename
    with open(path) as f:
        return json.load(f)


# Pre-load all mock data at import time
MOCK_SHOW_VERSION = _load_mock("show_version.json")
MOCK_SHOW_INTERFACE = _load_mock("show_interface.json")
MOCK_BGP_SUMMARY = _load_mock("show_ip_bgp_summary.json")
MOCK_ARP_TABLE = _load_mock("show_ip_arp.json")
MOCK_LLDP_NEIGHBORS = _load_mock("show_lldp_neighbors_detail.json")
MOCK_SHOW_VPC = _load_mock("show_vpc.json")
MOCK_SHOW_FEX = _load_mock("show_fex.json")
MOCK_SHOW_VPC_DETAIL = _load_mock("show_vpc_detail.json")
MOCK_SHOW_FEX_DETAIL = _load_mock("show_fex_detail.json")
MOCK_SHOW_FEATURE = _load_mock("show_feature.json")
MOCK_SHOW_SYSTEM_RESOURCES = _load_mock("show_system_resources.json")


def _extract_body(mock_data: dict) -> dict:
    """Extract the body from an NX-API mock response envelope."""
    return mock_data.get("ins_api", {}).get("outputs", {}).get("output", {}).get("body", {})


class MockNxosDriver:
    """Mock NX-OS driver for testing without real devices.

    Returns pre-loaded NX-API mock data and normalizes it using the
    same normalization logic as NxosDriver. Satisfies the NetworkDriver
    protocol.
    """

    platform: str = "nxos"
    vendor: str = "cisco"

    VENDOR_API_VERSION = "nxapi-1.0"

    def __init__(self) -> None:
        self._connected: bool = False
        self._host: str | None = None

    def connect(self, host: str = "n9k-spine-01", **kwargs: Any) -> None:
        """Simulate connection to an NX-OS device."""
        self._host = host
        self._connected = True
        logger.debug("MockNxosDriver connected to '%s'", host)

    def close(self) -> None:
        """Simulate closing the connection."""
        self._connected = False
        self._host = None

    def is_connected(self) -> bool:
        """Return True if mock driver is connected."""
        return self._connected

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Return mock NX-API responses for show commands."""
        if not self._connected:
            raise ConnectionError("MockNxosDriver is not connected.")

        results: list[dict] = []
        for cmd in commands:
            cmd_lower = cmd.strip().lower()
            if "version" in cmd_lower:
                results.append(_extract_body(MOCK_SHOW_VERSION))
            elif "interface" in cmd_lower and "lldp" not in cmd_lower:
                results.append(_extract_body(MOCK_SHOW_INTERFACE))
            elif "bgp" in cmd_lower:
                results.append(_extract_body(MOCK_BGP_SUMMARY))
            elif "arp" in cmd_lower:
                results.append(_extract_body(MOCK_ARP_TABLE))
            elif "lldp" in cmd_lower:
                results.append(_extract_body(MOCK_LLDP_NEIGHBORS))
            elif cmd_lower == "show vpc detail":
                results.append(_extract_body(MOCK_SHOW_VPC_DETAIL))
            elif cmd_lower == "show vpc":
                results.append(_extract_body(MOCK_SHOW_VPC))
            elif cmd_lower.startswith("show fex ") and "detail" in cmd_lower:
                results.append(_extract_body(MOCK_SHOW_FEX_DETAIL))
            elif cmd_lower == "show fex":
                results.append(_extract_body(MOCK_SHOW_FEX))
            elif cmd_lower == "show feature":
                results.append(_extract_body(MOCK_SHOW_FEATURE))
            elif cmd_lower == "show system resources":
                results.append(_extract_body(MOCK_SHOW_SYSTEM_RESOURCES))
            elif "running-config" in cmd_lower:
                results.append({"output": "! Mock NX-OS running-config\nhostname n9k-spine-01\n!\nend"})
            elif "startup-config" in cmd_lower:
                results.append({"output": "! Mock NX-OS startup-config\nhostname n9k-spine-01\n!\nend"})
            else:
                results.append({"error": f"Unmocked command: {cmd}"})
        return results

    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility."""
        return self.run_show(commands, encoding=encoding)

    def run_config(self, commands: list[str]) -> list[str]:
        """Config commands are no-ops in mock mode."""
        if not self._connected:
            raise ConnectionError("MockNxosDriver is not connected.")
        return [f"Mock config accepted: {cmd}" for cmd in commands]

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility."""
        return self.run_config(commands)

    # --- Normalized getters ---

    def get_facts(self) -> dict:
        """Return normalized device facts from mock data."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        body = _extract_body(MOCK_SHOW_VERSION)
        driver = NxosDriver()
        return driver._normalize_facts_nxapi(body)

    def get_interfaces(self) -> dict[str, dict]:
        """Return normalized interfaces from mock data."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        body = _extract_body(MOCK_SHOW_INTERFACE)
        return NxosDriver._normalize_interfaces_nxapi(body)

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Return normalized BGP summary from mock data."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        body = _extract_body(MOCK_BGP_SUMMARY)
        return NxosDriver._normalize_bgp_nxapi(body, vrf)

    def get_arp_table(self) -> list[dict]:
        """Return normalized ARP table from mock data."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        body = _extract_body(MOCK_ARP_TABLE)
        return NxosDriver._normalize_arp_nxapi(body)

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Return normalized LLDP neighbors from mock data."""
        from network_mcp.drivers.cisco_nxos import NxosDriver

        body = _extract_body(MOCK_LLDP_NEIGHBORS)
        return NxosDriver._normalize_lldp_nxapi(body)

    def get_config(self, sanitized: bool = True) -> dict:
        """Return mock configuration text."""
        running = "! Mock NX-OS running-config\nhostname n9k-spine-01\n!\nend"
        startup = "! Mock NX-OS startup-config\nhostname n9k-spine-01\n!\nend"
        if sanitized:
            from network_mcp.sanitizer import config_sanitizer

            running = config_sanitizer.sanitize(running)
            startup = config_sanitizer.sanitize(startup)
        return {"running": running, "startup": startup}
