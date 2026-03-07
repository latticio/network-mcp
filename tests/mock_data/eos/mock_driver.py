"""MockEosDriver — test driver returning realistic Arista EOS data.

Implements the full NetworkDriver protocol for use in cross-vendor testing.
Uses hardcoded mock data that mirrors actual eAPI responses and normalizes
them through EosDriver's normalization logic.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("network-mcp")


# --- Mock EOS eAPI responses ---

MOCK_SHOW_VERSION = {
    "modelName": "DCS-7050TX3-48C8",
    "version": "4.32.1F",
    "serialNumber": "SSJ17856312",
    "hostname": "spine-01",
    "uptime": 864000.5,
    "memTotal": 8048832,
    "memFree": 4024416,
    "systemMacAddress": "00:1c:73:aa:bb:cc",
    "hardwareRevision": "12.05",
}

MOCK_SHOW_INTERFACES = {
    "interfaces": {
        "Ethernet1": {
            "name": "Ethernet1",
            "lineProtocolStatus": "up",
            "interfaceStatus": "connected",
            "hardware": "Ethernet",
            "mtu": 9214,
            "bandwidth": 100000000000,
            "description": "uplink to spine-02",
            "physicalAddress": "00:1c:73:aa:bb:01",
            "burnedInAddress": "00:1c:73:aa:bb:01",
        },
        "Ethernet2": {
            "name": "Ethernet2",
            "lineProtocolStatus": "down",
            "interfaceStatus": "disabled",
            "hardware": "Ethernet",
            "mtu": 1500,
            "bandwidth": 1000000000,
            "description": "",
            "physicalAddress": "00:1c:73:aa:bb:02",
            "burnedInAddress": "00:1c:73:aa:bb:02",
        },
    }
}

MOCK_BGP_SUMMARY = {
    "vrfs": {
        "default": {
            "routerId": "10.0.0.1",
            "asn": "65000",
            "peers": {
                "10.0.0.2": {
                    "peerState": "Established",
                    "prefixReceived": 150,
                    "upDownTime": 1234567,
                    "asn": "65001",
                },
                "10.0.0.3": {
                    "peerState": "Active",
                    "prefixReceived": 0,
                    "upDownTime": 100,
                    "asn": "65002",
                },
            },
        }
    }
}

MOCK_ARP_TABLE = {
    "ipV4Neighbors": [
        {
            "address": "10.0.0.2",
            "hwAddress": "00:1c:73:cc:dd:01",
            "interface": "Ethernet1",
            "age": 120.5,
        },
        {
            "address": "10.0.0.3",
            "hwAddress": "00:1c:73:cc:dd:02",
            "interface": "Ethernet2",
            "age": 0,
        },
    ]
}

MOCK_LLDP_NEIGHBORS_DETAIL = {
    "lldpNeighbors": {
        "Ethernet1": {
            "lldpNeighborInfo": [
                {
                    "systemName": "spine-02.lab",
                    "neighborInterfaceInfo": {
                        "interfaceId_v2": "Ethernet49/1",
                        "interfaceId": "Ethernet49/1",
                    },
                    "systemDescription": "Arista Networks EOS version 4.32.1F",
                    "portId": "Ethernet49/1",
                }
            ]
        },
    }
}


class MockEosDriver:
    """Mock EOS driver for testing without real devices.

    Returns pre-loaded eAPI mock data and normalizes it using the
    same normalization logic as EosDriver. Satisfies the NetworkDriver
    protocol.
    """

    platform: str = "eos"
    vendor: str = "arista"

    VENDOR_API_VERSION = "eapi-1.0"

    def __init__(self) -> None:
        self._connected: bool = False
        self._host: str | None = None

    def connect(self, host: str = "spine-01", **kwargs: Any) -> None:
        """Simulate connection to an EOS device."""
        self._host = host
        self._connected = True
        logger.debug("MockEosDriver connected to '%s'", host)

    def close(self) -> None:
        """Simulate closing the connection."""
        self._connected = False
        self._host = None

    def is_connected(self) -> bool:
        """Return True if mock driver is connected."""
        return self._connected

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Return mock eAPI responses for show commands."""
        if not self._connected:
            raise ConnectionError("MockEosDriver is not connected.")

        results: list[dict] = []
        for cmd in commands:
            cmd_lower = cmd.strip().lower()
            if "version" in cmd_lower:
                results.append(MOCK_SHOW_VERSION)
            elif "interface" in cmd_lower and "lldp" not in cmd_lower:
                results.append(MOCK_SHOW_INTERFACES)
            elif "bgp" in cmd_lower:
                results.append(MOCK_BGP_SUMMARY)
            elif "arp" in cmd_lower:
                results.append(MOCK_ARP_TABLE)
            elif "lldp" in cmd_lower:
                results.append(MOCK_LLDP_NEIGHBORS_DETAIL)
            elif "running-config" in cmd_lower:
                results.append({"output": "! Mock EOS running-config\nhostname spine-01\n!\nend"})
            elif "startup-config" in cmd_lower:
                results.append({"output": "! Mock EOS startup-config\nhostname spine-01\n!\nend"})
            else:
                results.append({"error": f"Unmocked command: {cmd}"})
        return results

    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility."""
        return self.run_show(commands, encoding=encoding)

    def run_config(self, commands: list[str]) -> list[str]:
        """Config commands are no-ops in mock mode."""
        if not self._connected:
            raise ConnectionError("MockEosDriver is not connected.")
        return [f"Mock config accepted: {cmd}" for cmd in commands]

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility."""
        return self.run_config(commands)

    # --- Normalized getters ---

    def get_facts(self) -> dict:
        """Return normalized device facts from mock data."""
        data = MOCK_SHOW_VERSION
        return {
            "hostname": data.get("hostname", ""),
            "model": data.get("modelName", ""),
            "version": data.get("version", ""),
            "serial": data.get("serialNumber", ""),
            "uptime_seconds": int(data.get("uptime", 0)),
            "vendor": self.vendor,
            "platform": self.platform,
        }

    def get_interfaces(self) -> dict[str, dict]:
        """Return normalized interfaces from mock data."""
        raw_interfaces = MOCK_SHOW_INTERFACES.get("interfaces", {})
        normalized: dict[str, dict] = {}
        for name, info in raw_interfaces.items():
            line_status = info.get("lineProtocolStatus", "down")
            intf_status = info.get("interfaceStatus", "disabled")
            bandwidth_bps = info.get("bandwidth", 0)
            speed_mbps = bandwidth_bps // 1_000_000 if bandwidth_bps else 0
            mac = info.get("physicalAddress", info.get("burnedInAddress", ""))
            normalized[name] = {
                "is_up": line_status == "up",
                "is_enabled": intf_status not in ("disabled", "errdisabled"),
                "description": info.get("description", ""),
                "speed": speed_mbps,
                "mtu": info.get("mtu", 0),
                "mac_address": mac,
            }
        return normalized

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Return normalized BGP summary from mock data."""
        vrf_data = MOCK_BGP_SUMMARY.get("vrfs", {}).get(vrf, {})
        raw_peers = vrf_data.get("peers", {})
        peers: dict[str, dict] = {}
        for ip, peer_info in raw_peers.items():
            peers[ip] = {
                "state": peer_info.get("peerState", "Unknown"),
                "prefixes_received": int(peer_info.get("prefixReceived", 0)),
                "uptime": int(peer_info.get("upDownTime", 0)),
            }
        as_number_raw = vrf_data.get("asn", 0)
        try:
            as_number = int(as_number_raw)
        except (ValueError, TypeError):
            as_number = 0
        return {
            "router_id": vrf_data.get("routerId", ""),
            "as_number": as_number,
            "peers": peers,
        }

    def get_arp_table(self) -> list[dict]:
        """Return normalized ARP table from mock data."""
        raw_entries = MOCK_ARP_TABLE.get("ipV4Neighbors", [])
        entries: list[dict] = []
        for entry in raw_entries:
            entries.append(
                {
                    "ip": entry.get("address", ""),
                    "mac": entry.get("hwAddress", ""),
                    "interface": entry.get("interface", ""),
                    "age": float(entry.get("age", -1)),
                }
            )
        return entries

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Return normalized LLDP neighbors from mock data."""
        raw_neighbors = MOCK_LLDP_NEIGHBORS_DETAIL.get("lldpNeighbors", {})
        normalized: dict[str, list[dict]] = {}
        for intf, neighbors in raw_neighbors.items():
            intf_list: list[dict] = []
            neighbor_info = neighbors.get("lldpNeighborInfo", [])
            if isinstance(neighbor_info, list):
                for info in neighbor_info:
                    intf_list.append(
                        {
                            "hostname": info.get("systemName", ""),
                            "port": info.get("neighborInterfaceInfo", {}).get("interfaceId_v2", info.get("portId", "")),
                            "system_description": info.get("systemDescription", ""),
                            "management_ip": info.get("managementAddress", ""),
                        }
                    )
            if intf_list:
                normalized[intf] = intf_list
        return normalized

    def get_config(self, sanitized: bool = True) -> dict:
        """Return mock configuration text."""
        running = "! Mock EOS running-config\nhostname spine-01\n!\nend"
        startup = "! Mock EOS startup-config\nhostname spine-01\n!\nend"
        if sanitized:
            from network_mcp.sanitizer import config_sanitizer

            running = config_sanitizer.sanitize(running)
            startup = config_sanitizer.sanitize(startup)
        return {"running": running, "startup": startup}
