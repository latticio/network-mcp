"""MockIosXeDriver — test driver returning realistic IOS-XE RESTCONF data.

Loads mock JSON responses from this directory and normalizes them through
the same code paths as the real IosXeDriver. Implements the full
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
MOCK_INTERFACES = _load_mock("interfaces.json")
MOCK_BGP_SUMMARY = _load_mock("bgp_summary.json")
MOCK_ARP_TABLE = _load_mock("arp_table.json")
MOCK_LLDP_NEIGHBORS = _load_mock("lldp_neighbors.json")
MOCK_CAPABILITIES = _load_mock("restconf_capabilities.json")
MOCK_PLATFORM_INFO = _load_mock("platform_info.json")
MOCK_SDWAN_STATUS = _load_mock("sdwan_status.json")


class MockIosXeDriver:
    """Mock IOS-XE driver for testing without real devices.

    Returns pre-loaded RESTCONF mock data and normalizes it using the
    same normalization logic as IosXeDriver. Satisfies the NetworkDriver
    protocol.
    """

    platform: str = "iosxe"
    vendor: str = "cisco"

    VENDOR_API_VERSION = "restconf-1.0"

    def __init__(self) -> None:
        self._connected: bool = False
        self._host: str | None = None

    def connect(self, host: str = "csr1000v-01", **kwargs: Any) -> None:
        """Simulate connection to an IOS-XE device."""
        self._host = host
        self._connected = True
        logger.debug("MockIosXeDriver connected to '%s'", host)

    def close(self) -> None:
        """Simulate closing the connection."""
        self._connected = False
        self._host = None

    def is_connected(self) -> bool:
        """Return True if mock driver is connected."""
        return self._connected

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Return mock RESTCONF responses for show commands."""
        if not self._connected:
            raise ConnectionError("MockIosXeDriver is not connected.")

        results: list[dict] = []
        for cmd in commands:
            cmd_lower = cmd.strip().lower()
            if "version" in cmd_lower or cmd_lower == "show version":
                results.append(MOCK_SHOW_VERSION)
            elif "interface" in cmd_lower:
                results.append(MOCK_INTERFACES)
            elif "bgp" in cmd_lower:
                results.append(MOCK_BGP_SUMMARY)
            elif "arp" in cmd_lower:
                results.append(MOCK_ARP_TABLE)
            elif "lldp" in cmd_lower:
                results.append(MOCK_LLDP_NEIGHBORS)
            elif "restconf capabilities" in cmd_lower:
                results.append(MOCK_CAPABILITIES)
            elif "platform info" in cmd_lower:
                results.append(MOCK_PLATFORM_INFO)
            elif "sdwan control connections" in cmd_lower:
                results.append(MOCK_SDWAN_STATUS)
            elif "running-config" in cmd_lower:
                results.append({"output": "! Mock IOS-XE running-config\nhostname csr1000v-01\n!\nend"})
            elif "startup-config" in cmd_lower:
                results.append({"output": "! Mock IOS-XE startup-config\nhostname csr1000v-01\n!\nend"})
            else:
                results.append({"error": f"Unmocked command: {cmd}"})
        return results

    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility."""
        return self.run_show(commands, encoding=encoding)

    def run_config(self, commands: list[str]) -> list[str]:
        """Config commands are no-ops in mock mode."""
        if not self._connected:
            raise ConnectionError("MockIosXeDriver is not connected.")
        return [f"Mock config accepted: {cmd}" for cmd in commands]

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility."""
        return self.run_config(commands)

    # --- Normalized getters ---

    def get_facts(self) -> dict:
        """Return normalized device facts from mock data."""
        native = MOCK_SHOW_VERSION.get("Cisco-IOS-XE-native:native", {})
        return {
            "hostname": native.get("hostname", ""),
            "model": native.get("license", {}).get("udi", {}).get("pid", ""),
            "version": native.get("version", ""),
            "serial": native.get("license", {}).get("udi", {}).get("sn", ""),
            "uptime_seconds": 0,
            "vendor": self.vendor,
            "platform": self.platform,
        }

    def get_interfaces(self) -> dict[str, dict]:
        """Return normalized interfaces from mock data."""
        raw = MOCK_INTERFACES.get("ietf-interfaces:interfaces", {}).get("interface", [])
        normalized: dict[str, dict] = {}
        for intf in raw:
            name = intf.get("name", "")
            speed_bps = intf.get("speed", 0)
            if isinstance(speed_bps, int) and speed_bps > 1_000_000:
                speed_mbps = speed_bps // 1_000_000
            else:
                speed_mbps = speed_bps if isinstance(speed_bps, int) else 0

            mtu = intf.get("mtu", 1500)
            if not isinstance(mtu, int):
                mtu = 1500

            normalized[name] = {
                "is_up": intf.get("oper-status") == "up",
                "is_enabled": bool(intf.get("enabled", True)),
                "description": intf.get("description", ""),
                "speed": speed_mbps,
                "mtu": mtu,
                "mac_address": intf.get("phys-address", ""),
            }
        return normalized

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Return normalized BGP summary from mock data."""
        bgp_data = MOCK_BGP_SUMMARY.get("Cisco-IOS-XE-bgp-oper:bgp-state-data", {})
        router_id = ""
        as_number = 0
        peers: dict[str, dict] = {}

        address_families = bgp_data.get("address-families", {}).get("address-family", [])
        for af in address_families:
            if af.get("vrf-name", "default") == vrf:
                router_id = af.get("router-id", "")
                as_number = int(af.get("local-as", 0))
                break

        neighbors = bgp_data.get("neighbors", {}).get("neighbor", [])
        for neighbor in neighbors:
            if neighbor.get("vrf-name", "default") != vrf:
                continue
            peer_ip = neighbor.get("neighbor-id", "")
            if peer_ip:
                connection = neighbor.get("connection", {})
                prefix_activity = neighbor.get("prefix-activity", {}).get("received", {})
                peers[peer_ip] = {
                    "state": connection.get("state", "Unknown"),
                    "prefixes_received": int(prefix_activity.get("total-prefixes", 0)),
                    "uptime": int(neighbor.get("up-time", 0)),
                }

        return {
            "router_id": router_id,
            "as_number": as_number,
            "peers": peers,
        }

    def get_arp_table(self) -> list[dict]:
        """Return normalized ARP table from mock data."""
        from network_mcp.drivers.cisco_iosxe import _normalize_mac

        arp_data = MOCK_ARP_TABLE.get("Cisco-IOS-XE-arp-oper:arp-data", {})
        entries: list[dict] = []
        for vrf in arp_data.get("arp-vrf", []):
            for entry in vrf.get("arp-entry", []):
                entries.append(
                    {
                        "ip": entry.get("address", ""),
                        "mac": _normalize_mac(entry.get("hardware", "")),
                        "interface": entry.get("interface", ""),
                        "age": float(entry.get("time", -1)),
                    }
                )
        return entries

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Return normalized LLDP neighbors from mock data."""
        lldp_data = MOCK_LLDP_NEIGHBORS.get("Cisco-IOS-XE-lldp-oper:lldp-entries", {})
        normalized: dict[str, list[dict]] = {}
        for entry in lldp_data.get("lldp-entry", []):
            local_intf = entry.get("local-interface", "")
            neighbor = {
                "hostname": entry.get("device-id", ""),
                "port": entry.get("connecting-interface", ""),
                "system_description": entry.get("system-desc", ""),
            }
            if local_intf:
                normalized.setdefault(local_intf, []).append(neighbor)
        return normalized

    def get_config(self, sanitized: bool = True) -> dict:
        """Return mock configuration text."""
        running = "! Mock IOS-XE running-config\nhostname csr1000v-01\n!\nend"
        startup = "! Mock IOS-XE startup-config\nhostname csr1000v-01\n!\nend"
        if sanitized:
            from network_mcp.sanitizer import config_sanitizer

            running = config_sanitizer.sanitize(running)
            startup = config_sanitizer.sanitize(startup)
        return {"running": running, "startup": startup}
