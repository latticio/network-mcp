"""MockJunosDriver — test driver returning realistic JunOS NETCONF data.

Loads mock XML responses from this directory and normalizes them through
the same code paths as the real JunosDriver. Implements the full
NetworkDriver protocol for use in cross-vendor testing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from network_mcp.drivers.juniper_junos import JunosDriver

logger = logging.getLogger("network-mcp")

_MOCK_DATA_DIR = Path(__file__).parent


def _load_mock_xml(filename: str) -> str:
    """Load a mock XML response file."""
    path = _MOCK_DATA_DIR / filename
    return path.read_text()


# Pre-load all mock XML data at import time
MOCK_SYSTEM_INFORMATION = _load_mock_xml("system_information.xml")
MOCK_INTERFACE_INFORMATION = _load_mock_xml("interface_information.xml")
MOCK_BGP_SUMMARY = _load_mock_xml("bgp_summary.xml")
MOCK_ARP_TABLE = _load_mock_xml("arp_table.xml")
MOCK_LLDP_NEIGHBORS = _load_mock_xml("lldp_neighbors.xml")
MOCK_ROUTING_INSTANCES = _load_mock_xml("routing_instances.xml")
MOCK_FIREWALL_FILTERS = _load_mock_xml("firewall_filters.xml")
MOCK_SYSTEM_ALARMS = _load_mock_xml("system_alarms.xml")
MOCK_COMMIT_HISTORY = _load_mock_xml("commit_history.xml")
MOCK_ROLLBACK_COMPARE = (_MOCK_DATA_DIR / "rollback_compare.txt").read_text()


class MockJunosDriver:
    """Mock JunOS driver for testing without real devices.

    Returns pre-loaded NETCONF XML mock data and normalizes it using the
    same normalization logic as JunosDriver. Satisfies the NetworkDriver
    protocol.
    """

    platform: str = "junos"
    vendor: str = "juniper"

    VENDOR_API_VERSION = "netconf-1.0"

    def __init__(self) -> None:
        self._connected: bool = False
        self._host: str | None = None

    def connect(self, host: str = "junos-spine-01", **kwargs: Any) -> None:
        """Simulate connection to a JunOS device."""
        self._host = host
        self._connected = True
        logger.debug("MockJunosDriver connected to '%s'", host)

    def close(self) -> None:
        """Simulate closing the connection."""
        self._connected = False
        self._host = None

    def is_connected(self) -> bool:
        """Return True if mock driver is connected."""
        return self._connected

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Return mock NETCONF responses for show commands."""
        if not self._connected:
            raise ConnectionError("MockJunosDriver is not connected.")

        results: list[dict] = []
        for cmd in commands:
            cmd_lower = cmd.strip().lower()
            if "route instance" in cmd_lower:
                results.append({"output": MOCK_ROUTING_INSTANCES})
            elif "firewall" in cmd_lower:
                results.append({"output": MOCK_FIREWALL_FILTERS})
            elif "system alarms" in cmd_lower or "system alarm" in cmd_lower:
                results.append({"output": MOCK_SYSTEM_ALARMS})
            elif "system commit" in cmd_lower or "commit" in cmd_lower:
                results.append({"output": MOCK_COMMIT_HISTORY})
            elif "rollback compare" in cmd_lower:
                results.append({"output": MOCK_ROLLBACK_COMPARE})
            elif "version" in cmd_lower or "system" in cmd_lower:
                results.append({"output": MOCK_SYSTEM_INFORMATION})
            elif "interface" in cmd_lower and "lldp" not in cmd_lower:
                results.append({"output": MOCK_INTERFACE_INFORMATION})
            elif "bgp" in cmd_lower:
                results.append({"output": MOCK_BGP_SUMMARY})
            elif "arp" in cmd_lower:
                results.append({"output": MOCK_ARP_TABLE})
            elif "lldp" in cmd_lower:
                results.append({"output": MOCK_LLDP_NEIGHBORS})
            elif "running-config" in cmd_lower or "configuration" in cmd_lower:
                results.append({"output": "## Mock JunOS config\nsystem {\n    host-name junos-spine-01;\n}\n"})
            elif "startup-config" in cmd_lower:
                results.append({"output": "## Mock JunOS config\nsystem {\n    host-name junos-spine-01;\n}\n"})
            else:
                results.append({"error": f"Unmocked command: {cmd}"})
        return results

    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility."""
        return self.run_show(commands, encoding=encoding)

    def run_config(self, commands: list[str]) -> list[str]:
        """Config commands are no-ops in mock mode."""
        if not self._connected:
            raise ConnectionError("MockJunosDriver is not connected.")
        return [f"Mock config accepted: {cmd}" for cmd in commands]

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility."""
        return self.run_config(commands)

    # --- Normalized getters ---

    def get_facts(self) -> dict:
        """Return normalized device facts from mock NETCONF XML data."""
        return JunosDriver._normalize_facts(MOCK_SYSTEM_INFORMATION)

    def get_interfaces(self) -> dict[str, dict]:
        """Return normalized interfaces from mock NETCONF XML data."""
        return JunosDriver._normalize_interfaces(MOCK_INTERFACE_INFORMATION)

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Return normalized BGP summary from mock NETCONF XML data."""
        return JunosDriver._normalize_bgp_summary(MOCK_BGP_SUMMARY)

    def get_arp_table(self) -> list[dict]:
        """Return normalized ARP table from mock NETCONF XML data."""
        return JunosDriver._normalize_arp_table(MOCK_ARP_TABLE)

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Return normalized LLDP neighbors from mock NETCONF XML data."""
        return JunosDriver._normalize_lldp_neighbors(MOCK_LLDP_NEIGHBORS)

    def get_config(self, sanitized: bool = True) -> dict:
        """Return mock configuration text."""
        running = "## Mock JunOS config\nsystem {\n    host-name junos-spine-01;\n}\n"
        startup = running  # JunOS committed config = startup
        if sanitized:
            from network_mcp.sanitizer import config_sanitizer

            running = config_sanitizer.sanitize(running)
            startup = config_sanitizer.sanitize(startup)
        return {"running": running, "startup": startup}
