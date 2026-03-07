"""Legacy DeviceDriver protocol and PyeapiDriver (EOS) implementation.

The original driver interface before multi-vendor support. Superseded by
NetworkDriver in drivers/base.py for new vendor drivers. Kept for backward
compatibility with EOS-specific tools.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol, runtime_checkable

import pyeapi
import pyeapi.client
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from network_mcp.helpers import normalize_mac

logger = logging.getLogger("network-mcp")

# Default idle threshold (seconds) before a keep-alive check is triggered
_DEFAULT_KEEPALIVE_INTERVAL = 60


@runtime_checkable
class DeviceDriver(Protocol):
    """Abstract interface for device communication.

    Legacy interface used by EOS tools via PyeapiDriver. For multi-vendor
    tools, use NetworkDriver from drivers/base.py instead.
    """

    @property
    def platform(self) -> str: ...

    def connect(self, host: str, transport: str, username: str, password: str, **kwargs: Any) -> None: ...

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]: ...

    def run_config(self, commands: list[str]) -> list[str]: ...

    def close(self) -> None: ...

    def is_connected(self) -> bool: ...


class EosDriver(DeviceDriver):
    """pyeapi-based driver for Arista EOS devices.

    Implements both the legacy DeviceDriver protocol and the new
    NetworkDriver protocol with normalized getter methods.
    """

    platform: str = "eos"
    vendor: str = "arista"

    VENDOR_API_VERSION = "eapi-1.0"

    def __init__(self) -> None:
        self._node: pyeapi.client.Node | None = None
        self._host: str | None = None
        self._transport_name: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._connect_kwargs: dict[str, Any] = {}
        self._last_command_time: float = 0.0
        self._timeout_show: float = 30.0
        self._timeout_config: float = 60.0
        self._keepalive_interval: int = _DEFAULT_KEEPALIVE_INTERVAL

    def connect(self, host: str, transport: str, username: str, password: str, **kwargs: Any) -> None:
        """Establish a pyeapi connection to the device."""
        self._host = host
        self._transport_name = transport
        self._username = username
        self._password = password
        self._timeout_show = kwargs.pop("timeout_show", 30.0)
        self._timeout_config = kwargs.pop("timeout_config", 60.0)
        self._keepalive_interval = kwargs.pop("keepalive_interval", _DEFAULT_KEEPALIVE_INTERVAL)
        self._connect_kwargs = kwargs
        self._connect_with_retry(host, transport, username, password, **kwargs)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def _connect_with_retry(self, host: str, transport: str, username: str, password: str, **kwargs: Any) -> None:
        """Internal connect with tenacity retry."""
        self._node = pyeapi.connect(
            host=host,
            transport=transport,
            username=username,
            password=password,
            return_node=True,
            **kwargs,
        )
        self._last_command_time = time.monotonic()

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands via eAPI."""
        if self._node is None:
            raise ConnectionError("EosDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()
        result: list[dict] = self._node.run_commands(commands, encoding=encoding)
        return result

    def run_config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Execute configuration commands via eAPI.

        Supports kwargs (e.g., session='name') to pass through to pyeapi's config().
        """
        if self._node is None:
            raise ConnectionError("EosDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()
        result: list[str] = self._node.config(commands, **kwargs)
        return result

    # --- Backward-compatibility aliases ---
    # Tool modules that call conn_mgr.get_node() use .run_commands() and .config()
    # directly. These aliases let EosDriver be a drop-in replacement for pyeapi.Node
    # without modifying tool modules.

    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility with pyeapi.Node interface."""
        return self.run_show(commands, encoding=encoding)

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility with pyeapi.Node interface."""
        return self.run_config(commands, **kwargs)

    def close(self) -> None:
        """Close the underlying pyeapi transport connection."""
        if self._node is not None:
            try:
                transport = getattr(self._node, "_connection", None)
                if transport and hasattr(transport, "close"):
                    transport.close()
            except Exception as e:
                logger.debug("Failed to close EosDriver transport: %s", e)
            finally:
                self._node = None

    def is_connected(self) -> bool:
        """Return True if a pyeapi node is established."""
        return self._node is not None

    def capabilities(self) -> dict[str, bool]:
        """Query supported features and getter availability for Arista EOS."""
        return {
            "config_sessions": True,
            "gnmi": True,
            "restconf": False,
            "netconf": False,
            "get_facts": True,
            "get_interfaces": True,
            "get_bgp_summary": True,
            "get_arp_table": True,
            "get_lldp_neighbors": True,
            "get_config": True,
            "get_vlans": True,
            "get_vlan_detail": True,
            "get_interface_counters": True,
            "get_interface_errors": True,
            "get_ip_interfaces": True,
            "get_transceiver_info": True,
            "get_ospf_neighbors": True,
            "get_route_table": True,
            "get_route_summary": True,
            "get_mac_table": True,
            "get_stp_status": True,
            "get_port_channels": True,
            "get_environment": True,
            "get_ntp_status": True,
            "get_cpu_memory": True,
            "get_acls": True,
            "get_snmp_config": True,
            "get_running_config_section": True,
            "get_startup_config": True,
            "get_vrfs": True,
            "get_vrf_detail": True,
            "get_trunk_interfaces": True,
            "ping": True,
            "traceroute": True,
            "get_interface_flaps": True,
        }

    def reconnect(self) -> None:
        """Force reconnection to the device."""
        if self._host is None:
            raise ConnectionError("Cannot reconnect: no previous connection parameters.")
        self.close()
        self._connect_with_retry(
            self._host,
            self._transport_name or "https",
            self._username or "admin",
            self._password or "",
            **self._connect_kwargs,
        )

    def _check_alive(self) -> None:
        """Send a lightweight command if the session has been idle too long.

        If the check fails, triggers a reconnect automatically.
        """
        if self._node is None:
            return
        elapsed = time.monotonic() - self._last_command_time
        if elapsed < self._keepalive_interval:
            return
        try:
            self._node.run_commands(["show hostname"])
        except Exception:
            logger.info("EOS keep-alive failed for %s, reconnecting", self._host)
            self.reconnect()

    # --- Normalized getters (NetworkDriver protocol) ---

    def get_facts(self) -> dict:
        """Get device facts in a vendor-neutral format.

        Uses ``show version`` to extract hostname, model, version, serial,
        and uptime, then maps to the normalized schema.

        Returns:
            dict with keys: hostname, model, version, serial,
            uptime_seconds, vendor, platform
        """
        result = self.run_show(["show version"])
        data = result[0]
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
        """Get interface status in a vendor-neutral format.

        Uses ``show interfaces`` to get detailed interface data including
        link status, admin status, description, speed, MTU, and MAC.

        Returns:
            dict mapping interface name to normalized interface dict.
        """
        result = self.run_show(["show interfaces"])
        raw_interfaces = result[0].get("interfaces", {})
        normalized: dict[str, dict] = {}
        for name, info in raw_interfaces.items():
            line_status = info.get("lineProtocolStatus", "down")
            intf_status = info.get("interfaceStatus", "disabled")
            # Bandwidth comes in bps from EOS — convert to Mbps
            bandwidth_bps = info.get("bandwidth", 0)
            speed_mbps = bandwidth_bps // 1_000_000 if bandwidth_bps else 0
            # MAC address: EOS uses physicalAddress or burnedInAddress
            mac = normalize_mac(info.get("physicalAddress", info.get("burnedInAddress", "")))
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
        """Get BGP summary in a vendor-neutral format.

        Uses ``show ip bgp summary vrf <vrf>`` to extract router ID,
        AS number, and per-peer state.

        Args:
            vrf: VRF name to query.

        Returns:
            dict with keys: router_id, as_number, peers
        """
        cmd = f"show ip bgp summary vrf {vrf}" if vrf != "default" else "show ip bgp summary"
        result = self.run_show([cmd])
        vrf_data = result[0].get("vrfs", {}).get(vrf, {})
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
        """Get ARP table in a vendor-neutral format.

        Uses ``show ip arp`` to extract IP-to-MAC bindings.

        Returns:
            list of dicts with keys: ip, mac, interface, age
        """
        result = self.run_show(["show ip arp"])
        raw_entries = result[0].get("ipV4Neighbors", [])
        entries: list[dict] = []
        for entry in raw_entries:
            entries.append(
                {
                    "ip": entry.get("address", ""),
                    "mac": normalize_mac(entry.get("hwAddress", "")),
                    "interface": entry.get("interface", ""),
                    "age": float(entry.get("age", -1)),
                }
            )
        return entries

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Get LLDP neighbor information in a vendor-neutral format.

        Uses ``show lldp neighbors detail`` to extract per-interface
        neighbor data.

        Returns:
            dict mapping local interface name to list of neighbor dicts.
        """
        result = self.run_show(["show lldp neighbors detail"])
        raw_neighbors = result[0].get("lldpNeighbors", {})
        normalized: dict[str, list[dict]] = {}
        # EOS returns lldpNeighbors as a dict keyed by local interface
        if isinstance(raw_neighbors, dict):
            for intf, neighbors in raw_neighbors.items():
                intf_list: list[dict] = []
                neighbor_list = neighbors if isinstance(neighbors, list) else [neighbors]
                for n in neighbor_list:
                    neighbor_info = n.get("lldpNeighborInfo", [n])
                    if not isinstance(neighbor_info, list):
                        neighbor_info = [neighbor_info]
                    for info in neighbor_info:
                        intf_list.append(
                            {
                                "hostname": info.get("systemName", ""),
                                "port": info.get("neighborInterfaceInfo", {}).get(
                                    "interfaceId_v2", info.get("portId", "")
                                ),
                                "system_description": info.get("systemDescription", ""),
                                "management_ip": info.get("managementAddress", ""),
                            }
                        )
                if intf_list:
                    normalized[intf] = intf_list
        return normalized

    def get_lldp_neighbor_detail(self, interface: str = "") -> dict[str, list[dict]]:
        """Get detailed LLDP neighbor TLV data.

        Uses ``show lldp neighbors detail`` (JSON) and extracts extended
        TLV fields including chassis ID, management address, and
        system capabilities.

        Args:
            interface: Filter by local interface name (empty = all).

        Returns:
            dict mapping local interface name to list of neighbor detail dicts.
        """
        result = self.run_show(["show lldp neighbors detail"])
        raw_neighbors = result[0].get("lldpNeighbors", {})
        normalized: dict[str, list[dict]] = {}
        if isinstance(raw_neighbors, dict):
            for intf, neighbors in raw_neighbors.items():
                if interface and intf != interface:
                    continue
                intf_list: list[dict] = []
                neighbor_list = neighbors if isinstance(neighbors, list) else [neighbors]
                for n in neighbor_list:
                    neighbor_info = n.get("lldpNeighborInfo", [n])
                    if not isinstance(neighbor_info, list):
                        neighbor_info = [neighbor_info]
                    for info in neighbor_info:
                        caps_raw = info.get("systemCapabilities", {})
                        caps = [k for k, v in caps_raw.items() if v] if isinstance(caps_raw, dict) else []
                        intf_list.append(
                            {
                                "hostname": info.get("systemName", ""),
                                "port": info.get("neighborInterfaceInfo", {}).get(
                                    "interfaceId_v2", info.get("portId", "")
                                ),
                                "system_description": info.get("systemDescription", ""),
                                "chassis_id": info.get("chassisId", ""),
                                "management_address": info.get("managementAddress", ""),
                                "capabilities": caps,
                            }
                        )
                if intf_list:
                    normalized[intf] = intf_list
        return normalized

    def get_config(self, sanitized: bool = True) -> dict:
        """Get device configuration in a vendor-neutral format.

        Uses ``show running-config`` and ``show startup-config`` with
        text encoding (these commands do not support JSON on EOS).

        Args:
            sanitized: If True, redact passwords and secrets from output.

        Returns:
            dict with keys: running, startup
        """
        results = self.run_show(["show running-config", "show startup-config"], encoding="text")
        running = results[0].get("output", "") if isinstance(results[0], dict) else str(results[0])
        startup = results[1].get("output", "") if isinstance(results[1], dict) else str(results[1])
        if sanitized:
            from network_mcp.sanitizer import config_sanitizer

            running = config_sanitizer.sanitize(running)
            startup = config_sanitizer.sanitize(startup)
        return {
            "running": running,
            "startup": startup,
        }

    # --- Expanded normalized getters (20 new) ---

    def get_vlans(self) -> list[dict]:
        """Get all VLANs configured on the device.

        Uses ``show vlan`` (JSON, root key: ``vlans``).

        Returns:
            list of dicts with keys: vlan_id, name, status, interfaces
        """
        try:
            result = self.run_show(["show vlan"])
        except Exception:
            return []
        raw_vlans = result[0].get("vlans", {})
        vlans: list[dict] = []
        for vid_str, info in raw_vlans.items():
            vlans.append(
                {
                    "vlan_id": int(vid_str),
                    "name": info.get("name", ""),
                    "status": info.get("status", ""),
                    "interfaces": list(info.get("interfaces", {}).keys()),
                }
            )
        return vlans

    def get_vlan_detail(self, vlan_id: int) -> dict:
        """Get detailed information for a single VLAN.

        Uses ``show vlan {vlan_id}`` (JSON).

        Args:
            vlan_id: VLAN number (1-4094).

        Returns:
            dict with keys: vlan_id, name, status, interfaces, type
        """
        try:
            result = self.run_show([f"show vlan {vlan_id}"])
        except Exception:
            return {"vlan_id": vlan_id, "name": "", "status": "", "interfaces": [], "type": ""}
        raw_vlans = result[0].get("vlans", {})
        info = raw_vlans.get(str(vlan_id), {})
        return {
            "vlan_id": vlan_id,
            "name": info.get("name", ""),
            "status": info.get("status", ""),
            "interfaces": list(info.get("interfaces", {}).keys()),
            "type": info.get("dynamic", False) and "dynamic" or "static",
        }

    def get_interface_counters(self) -> list[dict]:
        """Get traffic counters for all interfaces.

        Uses ``show interfaces counters`` (JSON, root key: ``interfaces``).

        Returns:
            list of dicts with keys: interface, in_octets, out_octets,
            in_packets, out_packets, in_broadcast, out_broadcast,
            in_multicast, out_multicast
        """
        try:
            result = self.run_show(["show interfaces counters"])
        except Exception:
            return []
        raw = result[0].get("interfaces", {})
        counters: list[dict] = []
        for name, info in raw.items():
            counters.append(
                {
                    "interface": name,
                    "in_octets": int(info.get("inOctets", 0)),
                    "out_octets": int(info.get("outOctets", 0)),
                    "in_packets": int(info.get("inUcastPkts", 0)),
                    "out_packets": int(info.get("outUcastPkts", 0)),
                    "in_broadcast": int(info.get("inBroadcastPkts", 0)),
                    "out_broadcast": int(info.get("outBroadcastPkts", 0)),
                    "in_multicast": int(info.get("inMulticastPkts", 0)),
                    "out_multicast": int(info.get("outMulticastPkts", 0)),
                }
            )
        return counters

    def get_interface_errors(self) -> list[dict]:
        """Get error counters for all interfaces.

        Uses ``show interfaces counters errors`` (JSON, root key: ``interfaceErrorCounters``).

        Returns:
            list of dicts with keys: interface, in_errors, out_errors,
            in_crc, in_frame, in_runts, in_giants
        """
        try:
            result = self.run_show(["show interfaces counters errors"])
        except Exception:
            return []
        raw = result[0].get("interfaceErrorCounters", {})
        errors: list[dict] = []
        for name, info in raw.items():
            errors.append(
                {
                    "interface": name,
                    "in_errors": int(info.get("inputErrors", 0)),
                    "out_errors": int(info.get("outputErrors", 0)),
                    "in_crc": int(info.get("fcsErrors", 0)),
                    "in_frame": int(info.get("alignmentErrors", 0)),
                    "in_runts": int(info.get("runtFrames", 0)),
                    "in_giants": int(info.get("frameTooLongs", 0)),
                }
            )
        return errors

    def get_ip_interfaces(self) -> list[dict]:
        """Get Layer 3 interfaces with IP addressing.

        Uses ``show ip interface brief`` (JSON, root key: ``interfaces``).

        Returns:
            list of dicts with keys: interface, ip_address, subnet, vrf, status
        """
        try:
            result = self.run_show(["show ip interface brief"])
        except Exception:
            return []
        raw = result[0].get("interfaces", {})
        entries: list[dict] = []
        for name, info in raw.items():
            addr_info = info.get("interfaceAddress", {})
            ip_addr = ""
            subnet = ""
            if isinstance(addr_info, dict):
                primary = addr_info.get("ipAddr", {})
                if isinstance(primary, dict):
                    ip_addr = primary.get("address", "")
                    mask_len = primary.get("maskLen", 0)
                    subnet = str(mask_len)
            entries.append(
                {
                    "interface": name,
                    "ip_address": ip_addr,
                    "subnet": subnet,
                    "vrf": info.get("vrf", "default"),
                    "status": info.get("lineProtocolStatus", "down"),
                }
            )
        return entries

    def get_transceiver_info(self) -> list[dict]:
        """Get transceiver / optics DOM data for all interfaces.

        Uses ``show interfaces transceiver`` (JSON, root key: ``interfaces``).

        Returns:
            list of dicts with keys: interface, type, tx_power, rx_power, temp
        """
        try:
            result = self.run_show(["show interfaces transceiver"])
        except Exception:
            return []
        raw = result[0].get("interfaces", {})
        transceivers: list[dict] = []
        for name, info in raw.items():
            transceivers.append(
                {
                    "interface": name,
                    "type": info.get("mediaType", ""),
                    "tx_power": float(info.get("txPower", 0.0)),
                    "rx_power": float(info.get("rxPower", 0.0)),
                    "temp": float(info.get("temperature", 0.0)),
                }
            )
        return transceivers

    def get_ospf_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get OSPF neighbor adjacencies.

        Uses ``show ip ospf neighbor`` (JSON).
        EOS structure: ``vrfs[vrf].instList[instance].ospfNeighborEntries``.

        Args:
            vrf: VRF name to query.

        Returns:
            list of dicts with keys: neighbor_id, state, interface, area, uptime
        """
        try:
            result = self.run_show(["show ip ospf neighbor"])
        except Exception:
            return []
        neighbors: list[dict] = []
        vrf_data = result[0].get("vrfs", {}).get(vrf, {})
        for inst_data in vrf_data.get("instList", {}).values():
            for entry in inst_data.get("ospfNeighborEntries", []):
                neighbors.append(
                    {
                        "neighbor_id": entry.get("routerId", ""),
                        "state": entry.get("adjacencyState", ""),
                        "interface": entry.get("interfaceName", ""),
                        "area": entry.get("areaId", ""),
                        "uptime": entry.get("uptime", ""),
                    }
                )
        return neighbors

    def get_ospf_interfaces(self, vrf: str = "default") -> list[dict]:
        """Get OSPF-enabled interfaces.

        Uses ``show ip ospf interface brief`` (JSON).
        EOS structure: ``vrfs[vrf].instList[instance].interfaces``.

        Args:
            vrf: VRF name to query.

        Returns:
            list of dicts with keys: interface, area, network_type, cost, neighbor_count
        """
        try:
            result = self.run_show(["show ip ospf interface brief"])
        except Exception:
            return []
        interfaces: list[dict] = []
        vrf_data = result[0].get("vrfs", {}).get(vrf, {})
        for inst_data in vrf_data.get("instList", {}).values():
            for intf_name, intf_info in inst_data.get("interfaces", {}).items():
                interfaces.append(
                    {
                        "interface": intf_name,
                        "area": str(intf_info.get("area", "")),
                        "network_type": intf_info.get("networkType", ""),
                        "cost": int(intf_info.get("cost", 0)),
                        "neighbor_count": int(intf_info.get("numAdj", 0)),
                    }
                )
        return interfaces

    def get_bgp_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get detailed BGP neighbor information.

        Uses ``show ip bgp neighbors`` (JSON).
        EOS structure: ``vrfs[vrf].peerList``.

        Args:
            vrf: VRF name to query.

        Returns:
            list of dicts with keys: neighbor, remote_as, state, prefixes_received,
            prefixes_sent, uptime, description
        """
        try:
            cmd = "show ip bgp neighbors" if vrf == "default" else f"show ip bgp neighbors vrf {vrf}"
            result = self.run_show([cmd])
        except Exception:
            return []
        neighbors: list[dict] = []
        vrf_data = result[0].get("vrfs", {}).get(vrf, {})
        for peer in vrf_data.get("peerList", []):
            neighbors.append(
                {
                    "neighbor": peer.get("peerAddress", ""),
                    "remote_as": int(peer.get("asn", 0)),
                    "state": peer.get("state", ""),
                    "prefixes_received": int(peer.get("prefixesReceived", 0)),
                    "prefixes_sent": int(peer.get("prefixesSent", 0)),
                    "uptime": str(peer.get("upDownTime", "")),
                    "description": peer.get("description", ""),
                }
            )
        return neighbors

    def get_route_table(self, protocol: str = "", vrf: str = "default", limit: int = 100) -> list[dict]:
        """Get IP routing table entries.

        Uses ``show ip route [vrf <vrf>]`` (JSON).
        EOS structure: ``vrfs[vrf].routes[prefix]``.

        Args:
            protocol: Filter by protocol (e.g., 'bgp', 'connected'). Empty = all.
            vrf: VRF name. Defaults to 'default'.
            limit: Maximum number of routes to return.

        Returns:
            list of dicts with keys: prefix, next_hop, protocol, metric, preference
        """
        try:
            cmd = "show ip route" if vrf == "default" else f"show ip route vrf {vrf}"
            result = self.run_show([cmd])
        except Exception:
            return []
        raw_routes = result[0].get("vrfs", {}).get(vrf, {}).get("routes", {})
        routes: list[dict] = []
        for prefix, info in raw_routes.items():
            route_type = info.get("routeType", "")
            if protocol and route_type.lower() != protocol.lower():
                continue
            # Extract first via entry for next_hop
            vias = info.get("vias", [])
            next_hop = vias[0].get("nexthopAddr", vias[0].get("interface", "")) if vias else ""
            routes.append(
                {
                    "prefix": prefix,
                    "next_hop": next_hop,
                    "protocol": route_type,
                    "metric": int(info.get("metric", 0)),
                    "preference": int(info.get("preference", 0)),
                }
            )
            if len(routes) >= limit:
                break
        return routes

    def get_route_summary(self) -> dict:
        """Get route count summary grouped by protocol.

        Uses ``show ip route summary`` (JSON).
        EOS structure: ``vrfs.default.totalRoutes``, ``vrfs.default.routes``.

        Returns:
            dict with keys: total, connected, static, bgp, ospf, isis
        """
        try:
            result = self.run_show(["show ip route summary"])
        except Exception:
            return {"total": 0, "connected": 0, "static": 0, "bgp": 0, "ospf": 0, "isis": 0}
        vrf_data = result[0].get("vrfs", {}).get("default", {})
        route_counts = vrf_data.get("routes", {})
        return {
            "total": int(vrf_data.get("totalRoutes", 0)),
            "connected": int(route_counts.get("connected", 0)),
            "static": int(route_counts.get("static", 0)),
            "bgp": int(route_counts.get("bgp", 0)),
            "ospf": int(route_counts.get("ospf", 0)),
            "isis": int(route_counts.get("isis", 0)),
        }

    def get_mac_table(self, vlan: int | None = None, limit: int = 500) -> list[dict]:
        """Get MAC address table entries.

        Uses ``show mac address-table [vlan <vlan>]`` (JSON).
        EOS structure: ``unicastTable.tableEntries``.

        Args:
            vlan: Filter by VLAN ID (optional).
            limit: Maximum entries to return.

        Returns:
            list of dicts with keys: mac_address, vlan, interface, type
        """
        try:
            cmd = "show mac address-table"
            if vlan is not None:
                cmd = f"show mac address-table vlan {vlan}"
            result = self.run_show([cmd])
        except Exception:
            return []
        entries_raw = result[0].get("unicastTable", {}).get("tableEntries", [])
        entries: list[dict] = []
        for e in entries_raw:
            entries.append(
                {
                    "mac_address": normalize_mac(e.get("macAddress", "")),
                    "vlan": int(e.get("vlanId", 0)),
                    "interface": e.get("interface", ""),
                    "type": e.get("entryType", "dynamic").lower(),
                }
            )
            if len(entries) >= limit:
                break
        return entries

    def get_stp_status(self) -> dict:
        """Get spanning tree status.

        Uses ``show spanning-tree`` (JSON).
        EOS structure: ``spanningTreeInstances``.

        Returns:
            dict with keys: mode, root_bridge, instances
        """
        try:
            result = self.run_show(["show spanning-tree"])
        except Exception:
            return {"mode": "", "root_bridge": "", "instances": []}
        data = result[0]
        instances_raw = data.get("spanningTreeInstances", {})
        instances: list[dict] = []
        root_bridge = ""
        for inst_id, inst_data in instances_raw.items():
            rb = inst_data.get("rootBridge", {})
            rb_id = rb.get("bridgeId", "")
            if not root_bridge and rb_id:
                root_bridge = rb_id
            instances.append(
                {
                    "id": inst_id,
                    "root_bridge": rb_id,
                    "root_port": inst_data.get("rootPort", ""),
                    "priority": int(inst_data.get("bridge", {}).get("priority", 32768)),
                }
            )
        return {
            "mode": data.get("spanningTreeMode", ""),
            "root_bridge": root_bridge,
            "instances": instances,
        }

    def get_port_channels(self) -> list[dict]:
        """Get port-channel / LAG summary.

        Uses ``show port-channel summary`` (JSON).
        EOS structure: ``portChannels``.

        Returns:
            list of dicts with keys: name, status, protocol, members
        """
        try:
            result = self.run_show(["show port-channel summary"])
        except Exception:
            return []
        raw = result[0].get("portChannels", {})
        channels: list[dict] = []
        for name, info in raw.items():
            members: list[dict] = []
            for member_name, member_info in info.get("activePorts", {}).items():
                members.append(
                    {
                        "interface": member_name,
                        "status": "bundled" if member_info.get("bundled", False) else "down",
                    }
                )
            for member_name in info.get("inactivePorts", {}).keys():
                members.append(
                    {
                        "interface": member_name,
                        "status": "down",
                    }
                )
            channels.append(
                {
                    "name": name,
                    "status": "up" if info.get("activePorts") else "down",
                    "protocol": info.get("protocol", "lacp"),
                    "members": members,
                }
            )
        return channels

    def get_environment(self) -> dict:
        """Get environmental sensor readings.

        Uses ``show environment temperature``, ``show environment cooling``,
        and ``show environment power`` (JSON).

        Returns:
            dict with keys: temperature, fans, power
        """
        try:
            result = self.run_show(
                [
                    "show environment temperature",
                    "show environment cooling",
                    "show environment power",
                ]
            )
        except Exception:
            return {"temperature": [], "fans": [], "power": []}

        temp_data = result[0]
        temp_sensors: list[dict] = []
        for sensor in temp_data.get("tempSensors", temp_data.get("temperatureSensors", [])):
            if isinstance(sensor, dict):
                temp_sensors.append(
                    {
                        "sensor": sensor.get("name", sensor.get("description", "")),
                        "value": float(sensor.get("currentTemperature", sensor.get("hwTemperature", 0.0))),
                        "status": sensor.get("hwStatus", sensor.get("alertStatus", "ok")),
                    }
                )

        fan_data = result[1]
        fans: list[dict] = []
        for fan in fan_data.get("fanTraySlots", fan_data.get("fans", [])):
            if isinstance(fan, dict):
                fans.append(
                    {
                        "name": fan.get("label", fan.get("name", "")),
                        "status": fan.get("status", "ok"),
                    }
                )

        power_data = result[2]
        psus: list[dict] = []
        for psu_name, psu in power_data.get("powerSupplies", {}).items():
            if isinstance(psu, dict):
                psus.append(
                    {
                        "name": psu_name,
                        "status": psu.get("state", "ok"),
                        "output_watts": float(psu.get("outputPower", 0.0)),
                    }
                )

        return {"temperature": temp_sensors, "fans": fans, "power": psus}

    def get_ntp_status(self) -> dict:
        """Get NTP synchronization status and peer list.

        Uses ``show ntp status`` and ``show ntp associations`` (JSON).

        Returns:
            dict with keys: synchronized, reference, peers
        """
        try:
            result = self.run_show(["show ntp status", "show ntp associations"])
        except Exception:
            return {"synchronized": False, "reference": "", "peers": []}

        status_data = result[0]
        assoc_data = result[1]

        synchronized = status_data.get("status", "") == "synchronised"
        reference = status_data.get("peerAddress", status_data.get("refId", ""))

        peers: list[dict] = []
        for peer_addr, peer_info in assoc_data.get("peers", {}).items():
            if isinstance(peer_info, dict):
                peers.append(
                    {
                        "peer": peer_addr,
                        "stratum": int(peer_info.get("stratum", 0)),
                        "offset": float(peer_info.get("offset", 0.0)),
                        "jitter": float(peer_info.get("jitter", 0.0)),
                    }
                )

        return {"synchronized": synchronized, "reference": reference, "peers": peers}

    def get_cpu_memory(self) -> dict:
        """Get CPU utilization and memory usage.

        Uses ``show processes top once`` for CPU and ``show version`` for memory.

        Returns:
            dict with keys: cpu_percent, memory_total, memory_used, memory_free
        """
        try:
            result = self.run_show(["show processes top once", "show version"])
        except Exception:
            return {"cpu_percent": 0.0, "memory_total": 0, "memory_used": 0, "memory_free": 0}

        top_data = result[0]
        ver_data = result[1]

        # CPU: EOS returns cpuInfo with %idle
        cpu_info = top_data.get("cpuInfo", {})
        idle = float(cpu_info.get("%Cpu(s)", {}).get("idle", cpu_info.get("idle", 100.0)))
        cpu_percent = round(100.0 - idle, 1)

        mem_total = int(ver_data.get("memTotal", 0))
        mem_free = int(ver_data.get("memFree", 0))
        mem_used = mem_total - mem_free

        return {
            "cpu_percent": cpu_percent,
            "memory_total": mem_total,
            "memory_used": mem_used,
            "memory_free": mem_free,
        }

    def get_acls(self) -> list[dict]:
        """Get IP access control lists and their rules.

        Uses ``show ip access-lists`` (JSON, root key: ``aclList``).

        Returns:
            list of dicts with keys: name, type, rules
        """
        try:
            result = self.run_show(["show ip access-lists"])
        except Exception:
            return []
        acls: list[dict] = []
        for acl in result[0].get("aclList", []):
            rules: list[dict] = []
            for rule in acl.get("sequence", []):
                rules.append(
                    {
                        "sequence": int(rule.get("sequenceNumber", 0)),
                        "action": rule.get("action", ""),
                        "source": rule.get("source", rule.get("text", "")),
                    }
                )
            acls.append(
                {
                    "name": acl.get("name", ""),
                    "type": acl.get("type", "").lower(),
                    "rules": rules,
                }
            )
        return acls

    def get_snmp_config(self) -> dict:
        """Get SNMP configuration summary.

        Uses ``show snmp`` (JSON).

        Returns:
            dict with keys: communities, users, traps
        """
        try:
            result = self.run_show(["show snmp"])
        except Exception:
            return {"communities": [], "users": [], "traps": []}

        data = result[0]
        communities: list[dict] = []
        for name, info in data.get("communities", {}).items():
            access = "rw" if info.get("access", "ro") == "rw" else "ro"
            communities.append({"name": name, "access": access})

        users: list[dict] = []
        for user_name, user_info in data.get("users", {}).items():
            users.append(
                {
                    "name": user_name,
                    "group": user_info.get("group", ""),
                    "version": user_info.get("version", ""),
                }
            )

        traps: list[dict] = []
        for host, host_info in data.get("notifications", {}).get("hosts", {}).items():
            traps.append(
                {
                    "host": host,
                    "community": host_info.get("community", ""),
                    "version": host_info.get("version", ""),
                }
            )

        return {"communities": communities, "users": users, "traps": traps}

    def get_running_config_section(self, section: str) -> str:
        """Get a specific section of the running configuration.

        Uses ``show running-config section {section}`` (text encoding).

        Args:
            section: Configuration section keyword.

        Returns:
            str: Matching configuration section text.
        """
        try:
            result = self.run_show([f"show running-config section {section}"], encoding="text")
        except Exception:
            return ""
        data = result[0]
        if isinstance(data, dict):
            return data.get("output", "")
        return str(data)

    def get_startup_config(self) -> str:
        """Get the full startup configuration.

        Uses ``show startup-config`` (text encoding).

        Returns:
            str: Complete startup configuration text.
        """
        try:
            result = self.run_show(["show startup-config"], encoding="text")
        except Exception:
            return ""
        data = result[0]
        if isinstance(data, dict):
            return data.get("output", "")
        return str(data)

    def get_vrfs(self) -> list[dict]:
        """Get all VRF instances on the device.

        Uses ``show vrf`` (JSON, root key: ``vrfs``).

        Returns:
            list of dicts with keys: name, rd, interfaces, route_count
        """
        try:
            result = self.run_show(["show vrf"])
        except Exception:
            return []
        raw_vrfs = result[0].get("vrfs", {})
        vrfs: list[dict] = []
        for name, info in raw_vrfs.items():
            vrfs.append(
                {
                    "name": name,
                    "rd": info.get("routeDistinguisher", ""),
                    "interfaces": list(info.get("interfaces", [])),
                    "route_count": int(info.get("routeCount", 0)),
                }
            )
        return vrfs

    def get_trunk_interfaces(self) -> list[dict]:
        """Get trunk port configuration.

        Uses ``show interfaces trunk`` (JSON).

        Returns:
            list of dicts with keys: interface, mode, native_vlan, allowed_vlans, active_vlans
        """
        try:
            result = self.run_show(["show interfaces trunk"])
        except Exception:
            return []
        raw = result[0]
        trunks: list[dict] = []
        for name, info in raw.get("trunkGroups", {}).items():
            trunks.append(
                {
                    "interface": name,
                    "mode": "trunk",
                    "native_vlan": int(info.get("nativeVlan", 1)),
                    "allowed_vlans": info.get("allowedVlans", "ALL"),
                    "active_vlans": info.get("activeVlans", ""),
                }
            )
        return trunks

    def get_vrf_detail(self, vrf: str) -> dict:
        """Get detailed VRF information.

        Uses ``show vrf {name}`` (JSON).
        """
        try:
            result = self.run_show([f"show vrf {vrf}"])
        except Exception:
            return {}
        vrf_data = result[0].get("vrfs", {}).get(vrf, {})
        if not vrf_data:
            return {}
        return {
            "name": vrf,
            "rd": vrf_data.get("routeDistinguisher", ""),
            "interfaces": list(vrf_data.get("interfaces", [])),
            "route_count": int(vrf_data.get("routeCount", 0)),
            "import_targets": list(vrf_data.get("importRts", [])),
            "export_targets": list(vrf_data.get("exportRts", [])),
        }

    def ping(self, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
        """Execute a ping from the device.

        Uses ``ping {destination}`` (text encoding).
        """
        cmd = f"ping vrf {vrf} {destination}" if vrf != "default" else f"ping {destination}"
        if source:
            cmd += f" source {source}"
        cmd += f" repeat {count}"
        try:
            result = self.run_show([cmd], encoding="text")
        except Exception as exc:
            return {
                "success": False,
                "packets_sent": count,
                "packets_received": 0,
                "packet_loss_pct": 100.0,
                "rtt_min": 0,
                "rtt_avg": 0,
                "rtt_max": 0,
                "output": str(exc),
            }
        output = result[0].get("output", "") if isinstance(result[0], dict) else str(result[0])
        return self._parse_ping_output(output, count)

    @staticmethod
    def _parse_ping_output(output: str, count: int) -> dict:
        import re

        result: dict = {
            "success": False,
            "packets_sent": count,
            "packets_received": 0,
            "packet_loss_pct": 100.0,
            "rtt_min": 0.0,
            "rtt_avg": 0.0,
            "rtt_max": 0.0,
            "output": output,
        }
        # "5 packets transmitted, 5 received, 0% packet loss"
        m = re.search(r"(\d+)\s+packets?\s+transmitted,\s*(\d+)\s+received", output)
        if m:
            result["packets_sent"] = int(m.group(1))
            result["packets_received"] = int(m.group(2))
            sent = result["packets_sent"]
            result["packet_loss_pct"] = round((sent - result["packets_received"]) / sent * 100, 1) if sent else 100.0
            result["success"] = result["packets_received"] > 0
        # "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms"
        rtt_m = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))
        return result

    def traceroute(self, destination: str, source: str = "", vrf: str = "default") -> dict:
        """Execute a traceroute from the device.

        Uses ``traceroute {destination}`` (text encoding).
        """
        cmd = f"traceroute vrf {vrf} {destination}" if vrf != "default" else f"traceroute {destination}"
        if source:
            cmd += f" source {source}"
        try:
            result = self.run_show([cmd], encoding="text")
        except Exception as exc:
            return {"hops": [], "output": str(exc)}
        output = result[0].get("output", "") if isinstance(result[0], dict) else str(result[0])
        return self._parse_traceroute_output(output)

    @staticmethod
    def _parse_traceroute_output(output: str) -> dict:
        import re

        hops: list[dict] = []
        for line in output.splitlines():
            m = re.match(r"\s*(\d+)\s+(.+)", line)
            if not m:
                continue
            hop_num = int(m.group(1))
            rest = m.group(2).strip()
            # Extract first IP/hostname and RTT
            addr_m = re.search(r"([\d.]+|[\w.-]+)\s.*?([\d.]+)\s*ms", rest)
            if addr_m:
                hops.append({"hop": hop_num, "address": addr_m.group(1), "rtt_ms": float(addr_m.group(2))})
            elif "*" in rest:
                hops.append({"hop": hop_num, "address": "*", "rtt_ms": 0.0})
        return {"hops": hops, "output": output}

    def get_interface_flaps(self, hours: int = 24) -> list[dict]:
        """Get interface flap events from system logs.

        Uses ``show logging last {hours * 60}`` (text encoding) and filters for
        interface state change events.
        """
        log_lines = min(hours * 60, 10000)
        try:
            result = self.run_show([f"show logging last {log_lines}"], encoding="text")
        except Exception:
            return []
        output = result[0].get("output", "") if isinstance(result[0], dict) else str(result[0])
        return self._parse_flap_events(output)

    @staticmethod
    def _parse_flap_events(output: str) -> list[dict]:
        import re

        events: list[dict] = []
        for line in output.splitlines():
            lower = line.lower()
            if "changed state to" not in lower and "link status changed" not in lower:
                continue
            # Extract interface name
            intf_m = re.search(r"(Ethernet\S+|Loopback\S+|Vlan\S+|Port-Channel\S+|Management\S+)", line, re.IGNORECASE)
            interface = intf_m.group(1) if intf_m else "unknown"
            # Extract event
            event = "link state change"
            if "changed state to up" in lower:
                event = "link up"
            elif "changed state to down" in lower:
                event = "link down"
            # Extract timestamp (beginning of syslog line)
            ts_m = re.match(r"^(\S+\s+\d+\s+[\d:]+|\d{4}-\d{2}-\d{2}T[\d:.]+)", line)
            timestamp = ts_m.group(1) if ts_m else ""
            events.append({"interface": interface, "event": event, "timestamp": timestamp})
        return events

    def get_aaa_status(self) -> dict:
        """Get AAA configuration status."""
        from network_mcp.drivers.base import NotSupportedError

        raise NotSupportedError(f"get_aaa_status is not supported on {self.platform}")

    def get_user_sessions(self) -> list[dict]:
        """Get active user sessions on the device."""
        from network_mcp.drivers.base import NotSupportedError

        raise NotSupportedError(f"get_user_sessions is not supported on {self.platform}")

    def get_copp_policy(self) -> dict:
        """Get Control Plane Policing policy configuration and stats."""
        from network_mcp.drivers.base import NotSupportedError

        raise NotSupportedError(f"get_copp_policy is not supported on {self.platform}")

    def get_firewall_policies(self) -> list[dict]:
        """Get firewall policies — not applicable to Arista EOS routing/switching platforms."""
        from network_mcp.drivers.base import NotSupportedError

        raise NotSupportedError(f"get_firewall_policies is not supported on {self.platform}")
