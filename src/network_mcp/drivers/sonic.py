"""SONiC NOS driver — REST API primary transport.

Implements the NetworkDriver protocol for SONiC Network Operating System
using the sonic-restapi (REST) as the primary transport. SONiC exposes
device state via REST endpoints under ``/restconf/data/``.

Requires the ``sonic`` optional extra: ``pip install latticio[sonic]``
"""

from __future__ import annotations

import logging
import time
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import normalize_mac as _normalize_mac

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

logger = logging.getLogger("network-mcp")

# SONiC REST API paths (OpenConfig / sonic-restapi)
_SONIC_REST_PATHS = {
    "system": "/openconfig-system:system",
    "interfaces": "/openconfig-interfaces:interfaces",
    "bgp": "/openconfig-network-instance:network-instances/network-instance={vrf}/protocols/protocol=BGP,bgp/bgp",
    "lldp": "/openconfig-lldp:lldp/interfaces",
    "vlans": "/sonic-vlan:sonic-vlan/VLAN",
    "vlan_members": "/sonic-vlan:sonic-vlan/VLAN_MEMBER",
    "routes": "/openconfig-network-instance:network-instances/network-instance={vrf}/afts",
}

_REST_HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}


class SonicDriver:
    """SONiC NOS driver using REST API.

    Satisfies the NetworkDriver protocol defined in drivers/base.py.

    SONiC devices expose a REST API (sonic-restapi) that serves
    OpenConfig and SONiC YANG models over HTTPS.
    """

    platform: str = "sonic"
    vendor: str = "sonic"

    def __init__(self) -> None:
        self._client: httpx.Client | None = None
        self._host: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._connected: bool = False
        self._last_command_time: float = 0.0
        self._timeout_connect: float = 10.0
        self._timeout_show: float = 30.0
        self._timeout_config: float = 60.0
        self._keepalive_interval: int = 60
        self._connect_kwargs: dict[str, Any] = {}

    # --- Connection lifecycle ---

    def connect(
        self,
        host: str,
        transport: str = "https",
        username: str = "admin",
        password: str = "",
        **kwargs: Any,
    ) -> None:
        """Establish connection to a SONiC device via REST API.

        Args:
            host: Device hostname or IP address.
            transport: Transport method (only 'https' supported).
            username: Device username.
            password: Device password.
            **kwargs: Additional options (port, verify_ssl, timeout_connect,
                      timeout_show, timeout_config, keepalive_interval).
        """
        if not HAS_HTTPX:
            raise ImportError("httpx not installed. Install with: pip install latticio[sonic]")

        self._timeout_connect = kwargs.pop("timeout_connect", 10.0)
        self._timeout_show = kwargs.pop("timeout_show", 30.0)
        self._timeout_config = kwargs.pop("timeout_config", 60.0)
        self._keepalive_interval = kwargs.pop("keepalive_interval", 60)
        self._connect_kwargs = kwargs.copy()

        self._host = host
        self._username = username
        self._password = password

        self._connect_with_retry(host, username, password, **kwargs)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def _connect_with_retry(self, host: str, username: str, password: str, **kwargs: Any) -> None:
        """Internal connect with tenacity retry."""
        port = kwargs.get("port", 443)
        verify_ssl = kwargs.get("verify_ssl", True)

        base_url = f"https://{host}:{port}/restconf/data"
        self._client = httpx.Client(
            base_url=base_url,
            auth=(username, password),
            verify=verify_ssl,
            headers=_REST_HEADERS,
            timeout=self._timeout_show,
        )

        # Probe connectivity
        try:
            resp = self._client.get(_SONIC_REST_PATHS["system"])
            resp.raise_for_status()
            self._connected = True
            logger.debug("SONiC REST API connected to %s:%s", host, port)
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (401, 403):
                logger.error("SONiC REST authentication failed on %s (HTTP %d)", host, status_code)
                self._client.close()
                self._client = None
                raise ConnectionError(
                    f"Authentication failed for {host} (HTTP {status_code}). "
                    "Check credentials (NET_USERNAME/NET_PASSWORD)."
                ) from exc
            self._client.close()
            self._client = None
            raise ConnectionError(f"Failed to connect to SONiC device {host}: {exc}") from exc

        self._last_command_time = time.monotonic()

    def close(self) -> None:
        """Close the REST API connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                logger.debug("Failed to close SONiC REST client: %s", exc)
            finally:
                self._client = None
        self._connected = False

    def is_connected(self) -> bool:
        """Return True if the driver has an active connection."""
        return self._connected

    def capabilities(self) -> dict[str, bool]:
        """Query supported features and getter availability for SONiC."""
        return {
            "config_sessions": False,
            "gnmi": True,
            "restconf": True,
            "netconf": False,
            "ssh": False,
            "get_facts": True,
            "get_interfaces": True,
            "get_bgp_summary": True,
            "get_arp_table": False,
            "get_lldp_neighbors": True,
            "get_config": False,
            "get_vlans": True,
            "get_vlan_detail": False,
            "get_interface_counters": False,
            "get_interface_errors": False,
            "get_ip_interfaces": False,
            "get_transceiver_info": False,
            "get_ospf_neighbors": False,
            "get_ospf_interfaces": False,
            "get_route_table": True,
            "get_route_summary": False,
            "get_mac_table": False,
            "get_stp_status": False,
            "get_port_channels": False,
            "get_environment": False,
            "get_ntp_status": False,
            "get_cpu_memory": False,
            "get_acls": False,
            "get_snmp_config": False,
            "get_running_config_section": False,
            "get_startup_config": False,
            "get_vrfs": False,
            "get_vrf_detail": False,
            "ping": False,
            "traceroute": False,
            "get_interface_flaps": False,
        }

    def reconnect(self) -> None:
        """Force reconnection to the device."""
        if self._host is None:
            raise ConnectionError("Cannot reconnect: no previous connection parameters.")
        self.close()
        self._connect_with_retry(
            self._host,
            self._username or "admin",
            self._password or "",
            **self._connect_kwargs,
        )

    def _check_alive(self) -> None:
        """Send a lightweight probe if the session has been idle too long."""
        if not self._connected:
            return
        elapsed = time.monotonic() - self._last_command_time
        if elapsed < self._keepalive_interval:
            return
        try:
            if self._client:
                resp = self._client.get(_SONIC_REST_PATHS["system"])
                resp.raise_for_status()
            else:
                raise ConnectionError("No active REST client")
        except Exception:
            logger.info("SONiC keep-alive failed for %s, reconnecting", self._host)
            try:
                self.reconnect()
            except ConnectionError as re_exc:
                raise ConnectionError(
                    f"Keep-alive probe failed for {self._host} and reconnect also failed: {re_exc}"
                ) from re_exc

    def _rest_get(self, path: str) -> dict:
        """Execute a REST GET request and return JSON response.

        Args:
            path: REST API path (relative to base URL).

        Returns:
            Parsed JSON response as a dict.

        Raises:
            ConnectionError: If not connected or request fails.
        """
        if not self._connected or not self._client:
            raise ConnectionError("SonicDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        resp = self._client.get(path)
        resp.raise_for_status()
        return resp.json()

    # --- Raw command execution ---

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands on the SONiC device via REST API.

        SONiC REST API doesn't support arbitrary CLI commands. This method
        maps common show commands to REST API paths where possible.

        Args:
            commands: List of show commands to execute.
            encoding: Output encoding ('json' only for REST).

        Returns:
            List of response dicts, one per command.
        """
        if not self._connected or not self._client:
            raise ConnectionError("SonicDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        results: list[dict] = []
        for cmd in commands:
            path = self._map_command_to_path(cmd)
            if path:
                try:
                    resp = self._client.get(path)
                    resp.raise_for_status()
                    results.append(resp.json())
                except Exception as exc:
                    logger.warning("SONiC REST GET %s failed: %s", path, exc)
                    results.append({"error": str(exc)})
            else:
                results.append({"error": f"No REST mapping for command: {cmd}"})
        return results

    def run_config(self, commands: list[str]) -> list[str]:
        """Execute configuration commands on the SONiC device.

        Uses REST API PATCH/POST for configuration changes.

        Args:
            commands: List of configuration commands.

        Returns:
            List of result strings.
        """
        if not self._connected or not self._client:
            raise ConnectionError("SonicDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        # SONiC config via REST requires YANG-modeled payloads.
        # Raw CLI commands are not directly supported via REST.
        results: list[str] = []
        for cmd in commands:
            results.append(f"Config via REST not implemented for: {cmd}")
        return results

    # Backward-compatibility aliases
    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility."""
        return self.run_show(commands, encoding=encoding)

    def config(self, commands: list[str], **_kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility."""
        return self.run_config(commands)

    @staticmethod
    def _map_command_to_path(cmd: str) -> str | None:
        """Map a CLI-style show command to a SONiC REST API path."""
        cmd_lower = cmd.strip().lower()
        if "show system" in cmd_lower or "show version" in cmd_lower:
            return _SONIC_REST_PATHS["system"]
        if "show interface" in cmd_lower:
            return _SONIC_REST_PATHS["interfaces"]
        if "show bgp" in cmd_lower:
            return _SONIC_REST_PATHS["bgp"].format(vrf="default")
        if "show lldp" in cmd_lower:
            return _SONIC_REST_PATHS["lldp"]
        if "show vlan" in cmd_lower:
            return _SONIC_REST_PATHS["vlans"]
        return None

    # --- Normalized getters ---

    def get_facts(self) -> dict:
        """Get device facts in a vendor-neutral format.

        Uses OpenConfig system model to extract hostname, version, and
        hardware info.
        """
        try:
            data = self._rest_get(_SONIC_REST_PATHS["system"])
        except Exception as exc:
            raise ConnectionError(f"Failed to get facts from SONiC device: {exc}") from exc

        system = data.get("openconfig-system:system", data)
        state = system.get("state", system.get("config", {}))

        return {
            "hostname": state.get("hostname", ""),
            "model": state.get("hardware", "SONiC"),
            "version": state.get("software-version", state.get("openconfig-system-ext:software-version", "")),
            "serial": state.get("serial-number", ""),
            "uptime_seconds": state.get("boot-time", 0),
            "vendor": self.vendor,
            "platform": self.platform,
        }

    def get_interfaces(self) -> dict[str, dict]:
        """Get interface status in a vendor-neutral format.

        Uses OpenConfig interfaces model.
        """
        try:
            data = self._rest_get(_SONIC_REST_PATHS["interfaces"])
        except Exception as exc:
            raise ConnectionError(f"Failed to get interfaces from SONiC device: {exc}") from exc

        raw_intfs = data.get("openconfig-interfaces:interfaces", data).get("interface", [])
        normalized: dict[str, dict] = {}

        for intf in raw_intfs:
            name = intf.get("name", "")
            state = intf.get("state", {})
            config = intf.get("config", {})

            oper_status = state.get("oper-status", "DOWN")
            admin_status = state.get("admin-status", config.get("enabled", True))

            # Speed: OpenConfig uses bits/sec
            speed_raw = state.get("openconfig-if-ethernet:ethernet", {}).get("state", {}).get("port-speed", "")
            speed_mbps = self._parse_speed(speed_raw)

            normalized[name] = {
                "is_up": str(oper_status).upper() == "UP",
                "is_enabled": admin_status is True or str(admin_status).upper() == "UP",
                "description": config.get("description", state.get("description", "")),
                "speed": speed_mbps,
                "mtu": state.get("mtu", config.get("mtu", 9100)),
                "mac_address": _normalize_mac(state.get("mac-address", "")),
            }

        return normalized

    @staticmethod
    def _parse_speed(speed_str: str) -> int:
        """Parse OpenConfig speed string (e.g., 'SPEED_100GB') to Mbps."""
        if not speed_str or not isinstance(speed_str, str):
            return 0
        speed_upper = speed_str.upper()
        speed_map = {
            "SPEED_1GB": 1000,
            "SPEED_10GB": 10000,
            "SPEED_25GB": 25000,
            "SPEED_40GB": 40000,
            "SPEED_50GB": 50000,
            "SPEED_100GB": 100000,
            "SPEED_200GB": 200000,
            "SPEED_400GB": 400000,
        }
        return speed_map.get(speed_upper, 0)

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Get BGP summary in a vendor-neutral format.

        Uses OpenConfig BGP model.
        """
        path = _SONIC_REST_PATHS["bgp"].format(vrf=vrf)
        try:
            data = self._rest_get(path)
        except Exception as exc:
            raise ConnectionError(f"Failed to get BGP summary from SONiC device: {exc}") from exc

        bgp = data.get("openconfig-network-instance:bgp", data)
        global_state = bgp.get("global", {}).get("state", bgp.get("global", {}).get("config", {}))
        neighbors_data = bgp.get("neighbors", {}).get("neighbor", [])

        peers: dict[str, dict] = {}
        for nbr in neighbors_data:
            peer_addr = nbr.get("neighbor-address", "")
            state = nbr.get("state", {})
            peers[peer_addr] = {
                "state": state.get("session-state", "Unknown"),
                "prefixes_received": state.get("prefixes", {}).get("received", 0),
                "uptime": state.get("established-transitions", 0),
            }

        return {
            "router_id": global_state.get("router-id", ""),
            "as_number": global_state.get("as", 0),
            "peers": peers,
        }

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Get LLDP neighbor information in a vendor-neutral format.

        Uses OpenConfig LLDP model.
        """
        try:
            data = self._rest_get(_SONIC_REST_PATHS["lldp"])
        except Exception as exc:
            raise ConnectionError(f"Failed to get LLDP neighbors from SONiC device: {exc}") from exc

        lldp_intfs = data.get("openconfig-lldp:interfaces", data).get("interface", [])
        result: dict[str, list[dict]] = {}

        for intf in lldp_intfs:
            local_name = intf.get("name", "")
            neighbors = intf.get("neighbors", {}).get("neighbor", [])
            nbr_list: list[dict] = []
            for nbr in neighbors:
                state = nbr.get("state", {})
                nbr_list.append({
                    "hostname": state.get("system-name", ""),
                    "port": state.get("port-id", state.get("port-description", "")),
                    "system_description": state.get("system-description", ""),
                })
            if nbr_list:
                result[local_name] = nbr_list

        return result

    def get_vlans(self) -> list[dict]:
        """Get all VLANs configured on the device.

        Uses SONiC VLAN YANG model.
        """
        try:
            data = self._rest_get(_SONIC_REST_PATHS["vlans"])
        except Exception as exc:
            raise ConnectionError(f"Failed to get VLANs from SONiC device: {exc}") from exc

        vlans_raw = data.get("sonic-vlan:VLAN", data).get("VLAN_LIST", [])

        # Try to get VLAN members
        members_map: dict[str, list[str]] = {}
        try:
            members_data = self._rest_get(_SONIC_REST_PATHS["vlan_members"])
            member_list = members_data.get("sonic-vlan:VLAN_MEMBER", {}).get("VLAN_MEMBER_LIST", [])
            for m in member_list:
                vlan_name = m.get("name", "")
                port = m.get("ifname", "")
                if vlan_name and port:
                    members_map.setdefault(vlan_name, []).append(port)
        except Exception:
            logger.debug("Could not fetch VLAN members")

        result: list[dict] = []
        for vlan in vlans_raw:
            vlan_name = vlan.get("name", "")  # e.g., "Vlan100"
            vlan_id = 0
            if vlan_name.startswith("Vlan"):
                try:
                    vlan_id = int(vlan_name[4:])
                except ValueError:
                    pass
            elif "vlanid" in vlan:
                vlan_id = vlan.get("vlanid", 0)

            result.append({
                "vlan_id": vlan_id,
                "name": vlan.get("alias", vlan_name),
                "status": "active",
                "interfaces": members_map.get(vlan_name, []),
            })

        return result

    def get_route_table(self, protocol: str = "", vrf: str = "default", limit: int = 100) -> list[dict]:
        """Get IP routing table entries.

        Uses OpenConfig AFT (Abstract Forwarding Table) model.
        """
        path = _SONIC_REST_PATHS["routes"].format(vrf=vrf)
        try:
            data = self._rest_get(path)
        except Exception as exc:
            raise ConnectionError(f"Failed to get route table from SONiC device: {exc}") from exc

        afts = data.get("openconfig-network-instance:afts", data)
        ipv4_entries = afts.get("ipv4-unicast", {}).get("ipv4-entry", [])

        routes: list[dict] = []
        for entry in ipv4_entries[:limit]:
            prefix = entry.get("prefix", "")
            state = entry.get("state", {})
            next_hops = entry.get("next-hops", {}).get("next-hop", [])

            next_hop_addr = ""
            if next_hops:
                nh_state = next_hops[0].get("state", {})
                next_hop_addr = nh_state.get("ip-address", nh_state.get("next-hop", ""))

            route_protocol = state.get("origin-protocol", "unknown")

            # Filter by protocol if specified
            if protocol and protocol.lower() not in route_protocol.lower():
                continue

            routes.append({
                "prefix": prefix,
                "next_hop": next_hop_addr,
                "protocol": route_protocol,
                "metric": state.get("metric", 0),
                "preference": state.get("preference", 0),
            })

        return routes[:limit]

    # --- Unsupported getters (raise NotSupportedError) ---

    def get_arp_table(self) -> list[dict]:
        raise NotSupportedError("get_arp_table is not yet implemented for SONiC")

    def get_config(self, sanitized: bool = True) -> dict:
        raise NotSupportedError("get_config is not yet implemented for SONiC")

    def get_vlan_detail(self, vlan_id: int) -> dict:
        raise NotSupportedError("get_vlan_detail is not yet implemented for SONiC")

    def get_interface_counters(self) -> list[dict]:
        raise NotSupportedError("get_interface_counters is not yet implemented for SONiC")

    def get_interface_errors(self) -> list[dict]:
        raise NotSupportedError("get_interface_errors is not yet implemented for SONiC")

    def get_ip_interfaces(self) -> list[dict]:
        raise NotSupportedError("get_ip_interfaces is not yet implemented for SONiC")

    def get_transceiver_info(self) -> list[dict]:
        raise NotSupportedError("get_transceiver_info is not yet implemented for SONiC")

    def get_ospf_neighbors(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_ospf_neighbors is not yet implemented for SONiC")

    def get_ospf_interfaces(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_ospf_interfaces is not yet implemented for SONiC")

    def get_bgp_neighbors(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_bgp_neighbors is not yet implemented for SONiC")

    def get_route_summary(self) -> dict:
        raise NotSupportedError("get_route_summary is not yet implemented for SONiC")

    def get_mac_table(self, vlan: int | None = None, limit: int = 500) -> list[dict]:
        raise NotSupportedError("get_mac_table is not yet implemented for SONiC")

    def get_stp_status(self) -> dict:
        raise NotSupportedError("get_stp_status is not yet implemented for SONiC")

    def get_port_channels(self) -> list[dict]:
        raise NotSupportedError("get_port_channels is not yet implemented for SONiC")

    def get_environment(self) -> dict:
        raise NotSupportedError("get_environment is not yet implemented for SONiC")

    def get_ntp_status(self) -> dict:
        raise NotSupportedError("get_ntp_status is not yet implemented for SONiC")

    def get_cpu_memory(self) -> dict:
        raise NotSupportedError("get_cpu_memory is not yet implemented for SONiC")

    def get_acls(self) -> list[dict]:
        raise NotSupportedError("get_acls is not yet implemented for SONiC")

    def get_snmp_config(self) -> dict:
        raise NotSupportedError("get_snmp_config is not yet implemented for SONiC")

    def get_running_config_section(self, section: str) -> str:
        raise NotSupportedError("get_running_config_section is not yet implemented for SONiC")

    def get_startup_config(self) -> str:
        raise NotSupportedError("get_startup_config is not yet implemented for SONiC")

    def get_vrfs(self) -> list[dict]:
        raise NotSupportedError("get_vrfs is not yet implemented for SONiC")

    def get_trunk_interfaces(self) -> list[dict]:
        raise NotSupportedError("get_trunk_interfaces is not yet implemented for SONiC")

    def get_lldp_neighbor_detail(self, interface: str = "") -> dict[str, list[dict]]:
        raise NotSupportedError("get_lldp_neighbor_detail is not yet implemented for SONiC")

    def get_vrf_detail(self, vrf: str) -> dict:
        raise NotSupportedError("get_vrf_detail is not yet implemented for SONiC")

    def ping(self, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
        raise NotSupportedError("ping is not yet implemented for SONiC")

    def traceroute(self, destination: str, source: str = "", vrf: str = "default") -> dict:
        raise NotSupportedError("traceroute is not yet implemented for SONiC")

    def get_interface_flaps(self, hours: int = 24) -> list[dict]:
        raise NotSupportedError("get_interface_flaps is not yet implemented for SONiC")

    def get_aaa_status(self) -> dict:
        raise NotSupportedError("get_aaa_status is not yet implemented for SONiC")

    def get_user_sessions(self) -> list[dict]:
        raise NotSupportedError("get_user_sessions is not yet implemented for SONiC")

    def get_copp_policy(self) -> dict:
        raise NotSupportedError("get_copp_policy is not yet implemented for SONiC")

    def get_firewall_policies(self) -> list[dict]:
        raise NotSupportedError("get_firewall_policies is not supported on SONiC")
