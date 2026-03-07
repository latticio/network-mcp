"""Palo Alto PAN-OS driver — XML/REST API transport.

Implements the NetworkDriver protocol for Palo Alto PAN-OS firewalls using
the PAN-OS XML API over HTTPS (port 443). Supports API key authentication
and API key generation from username/password credentials.

Requires httpx (available via the 'cisco' or 'sonic' extras).
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import defusedxml.ElementTree as DET  # safe XML parsing (prevents XXE attacks)

from network_mcp.drivers.base import NotSupportedError

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from network_mcp.helpers import normalize_mac as _normalize_mac
except Exception:  # pragma: no cover

    def _normalize_mac(mac: str) -> str:  # type: ignore[misc]
        return mac.lower()


logger = logging.getLogger("network-mcp")

# PAN-OS XML API endpoint path
_PANOS_API_PATH = "/api/"

# CLI command → XML API command mapping for run_show()
_CLI_TO_XML: dict[str, str] = {
    "show system info": "<show><system><info/></system></show>",
    "show interface all": "<show><interface>all</interface></show>",
    "show arp all": "<show><arp><entry name='all'/></arp></show>",
    "show routing route": "<show><routing><route/></routing></show>",
    "show lldp neighbors all": "<show><lldp><neighbors>all</neighbors></lldp></show>",
}

# PAN-OS route flag characters → normalized protocol names
_FLAG_TO_PROTOCOL: dict[str, str] = {
    "C": "connected",
    "S": "static",
    "R": "rip",
    "O": "ospf",
    "B": "bgp",
    "H": "static",
    "A": "connected",
}


def _xml_text(parent: ET.Element, tag: str) -> str:
    """Extract text from a child XML element, returning empty string if missing."""
    el = parent.find(tag)
    return el.text.strip() if el is not None and el.text else ""


def _parse_panos_uptime(uptime_str: str) -> int:
    """Parse PAN-OS uptime string to seconds.

    Handles formats like:
    - "0 days, 5:30:00"
    - "2 days, 14:22:51"
    - "5:30:00" (no days component)
    """
    if not uptime_str:
        return 0
    days = 0
    # Try "N days, H:M:S" format
    match = re.match(r"(\d+)\s+days?,\s+(\d+):(\d+):(\d+)", uptime_str, re.IGNORECASE)
    if match:
        days = int(match.group(1))
        hours, minutes, seconds = int(match.group(2)), int(match.group(3)), int(match.group(4))
        return days * 86400 + hours * 3600 + minutes * 60 + seconds
    # Try "H:M:S" format
    match = re.match(r"(\d+):(\d+):(\d+)", uptime_str)
    if match:
        hours, minutes, seconds = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return hours * 3600 + minutes * 60 + seconds
    return 0


def _normalize_panos_route_flags(flags: str) -> str:
    """Convert PAN-OS route flags string to a normalized protocol name.

    PAN-OS flags are single-character codes (e.g., 'A S' = active static,
    'A B' = active BGP, 'C' = connected).
    """
    if not flags:
        return "unknown"
    for flag_char, proto in _FLAG_TO_PROTOCOL.items():
        if flag_char in flags:
            return proto
    return "unknown"


def _cli_to_xml(cmd: str) -> str | None:
    """Map a CLI-style show command to a PAN-OS XML API command string.

    Returns None if the command is not mapped.
    """
    return _CLI_TO_XML.get(cmd.strip().lower())


class PanosDriver:
    """Palo Alto PAN-OS driver using the XML API over HTTPS.

    Satisfies the NetworkDriver protocol defined in drivers/base.py.

    PAN-OS devices expose an XML API at https://{host}/api/ with API key
    authentication. This driver supports both direct API key injection and
    API key generation from username/password credentials.
    """

    platform: str = "panos"
    vendor: str = "palo_alto"

    def __init__(self) -> None:
        self._client: httpx.Client | None = None
        self._host: str | None = None
        self._api_key: str | None = None
        self._connected: bool = False
        self._last_command_time: float = 0.0
        self._timeout_connect: float = 10.0
        self._timeout_show: float = 30.0
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
        """Establish connection to a PAN-OS device via the XML API.

        Args:
            host: Device hostname or IP address.
            transport: Transport protocol (only 'https' supported).
            username: Device username (used to generate API key if api_key not given).
            password: Device password (used to generate API key if api_key not given).
            **kwargs: Additional options:
                - api_key (str): Pre-generated PAN-OS API key (preferred).
                - port (int): HTTPS port (default 443).
                - verify_ssl (bool): Verify TLS certificates (default True).
                - timeout_connect (float): Connection timeout in seconds.
                - timeout_show (float): Show command timeout in seconds.
                - keepalive_interval (int): Keepalive interval in seconds.
        """
        if not HAS_HTTPX:
            raise ImportError("httpx not installed. Install with: pip install latticio[cisco]")

        self._timeout_connect = kwargs.pop("timeout_connect", 10.0)
        self._timeout_show = kwargs.pop("timeout_show", 30.0)
        self._keepalive_interval = kwargs.pop("keepalive_interval", 60)
        api_key = kwargs.pop("api_key", None)
        self._connect_kwargs = kwargs.copy()

        port = kwargs.get("port", 443)
        verify_ssl = kwargs.get("verify_ssl", True)

        self._host = host
        self._client = httpx.Client(
            base_url=f"https://{host}:{port}",
            verify=verify_ssl,
            timeout=self._timeout_show,
        )

        # Acquire API key — prefer explicit key, fall back to credential-based generation
        if api_key:
            self._api_key = api_key
        elif username and password:
            self._api_key = self._generate_api_key(username, password)
        else:
            self._client.close()
            self._client = None
            raise ValueError("Either api_key or username+password must be provided to connect to PAN-OS")

        # Probe connectivity
        try:
            self._xml_op("<show><system><info/></system></show>")
            self._connected = True
            self._last_command_time = time.monotonic()
            logger.debug("PAN-OS XML API connected to %s:%s", host, port)
        except Exception as exc:
            if self._client:
                self._client.close()
                self._client = None
            raise ConnectionError(f"Failed to connect to PAN-OS device {host}: {exc}") from exc

    def _generate_api_key(self, username: str, password: str) -> str:
        """Generate a PAN-OS API key from username/password credentials.

        Args:
            username: PAN-OS admin username.
            password: PAN-OS admin password.

        Returns:
            API key string for subsequent authenticated requests.

        Raises:
            ConnectionError: If key generation fails.
        """
        if not self._client:
            raise ConnectionError("HTTP client not initialized")
        resp = self._client.get(
            _PANOS_API_PATH,
            params={"type": "keygen", "user": username, "password": password},
        )
        resp.raise_for_status()
        root = DET.fromstring(resp.text)
        if root.get("status") != "success":
            raise ConnectionError(f"PAN-OS API key generation failed: {resp.text[:200]}")
        key_el = root.find(".//key")
        if key_el is None or not key_el.text:
            raise ConnectionError("API key not found in PAN-OS keygen response")
        return key_el.text.strip()

    def close(self) -> None:
        """Close the HTTP connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                logger.debug("Failed to close PAN-OS HTTP client: %s", exc)
            finally:
                self._client = None
        self._connected = False

    def is_connected(self) -> bool:
        """Return True if the driver has an active connection."""
        return self._connected

    def reconnect(self) -> None:
        """Force reconnection to the device."""
        if self._host is None:
            raise ConnectionError("Cannot reconnect: no previous connection parameters stored.")
        saved_key = self._api_key
        self.close()

        port = self._connect_kwargs.get("port", 443)
        verify_ssl = self._connect_kwargs.get("verify_ssl", True)
        self._client = httpx.Client(
            base_url=f"https://{self._host}:{port}",
            verify=verify_ssl,
            timeout=self._timeout_show,
        )
        self._api_key = saved_key
        try:
            self._xml_op("<show><system><info/></system></show>")
            self._connected = True
            self._last_command_time = time.monotonic()
        except Exception as exc:
            if self._client:
                self._client.close()
                self._client = None
            raise ConnectionError(f"Reconnect failed for {self._host}: {exc}") from exc

    # --- Internal helpers ---

    def _xml_op(self, cmd: str) -> ET.Element:
        """Execute a PAN-OS operational command and return the parsed XML root.

        Args:
            cmd: XML command string (e.g., '<show><system><info/></system></show>').

        Returns:
            Root element of the parsed XML response.

        Raises:
            ConnectionError: If not connected or the HTTP request fails.
            ValueError: If PAN-OS returns a non-success status.
        """
        if not self._client or not self._api_key:
            raise ConnectionError("PanosDriver is not connected. Call connect() first.")
        self._last_command_time = time.monotonic()
        resp = self._client.get(
            _PANOS_API_PATH,
            params={"type": "op", "cmd": cmd, "key": self._api_key},
        )
        resp.raise_for_status()
        root = DET.fromstring(resp.text)
        if root.get("status") != "success":
            msg_el = root.find(".//msg")
            msg = msg_el.text if msg_el is not None and msg_el.text else resp.text[:200]
            raise ValueError(f"PAN-OS API error: {msg}")
        return root

    def _check_alive(self) -> None:
        """Send a lightweight probe if the session has been idle too long."""
        if not self._connected:
            return
        elapsed = time.monotonic() - self._last_command_time
        if elapsed < self._keepalive_interval:
            return
        try:
            self._xml_op("<show><system><info/></system></show>")
        except Exception:
            logger.info("PAN-OS keep-alive failed for %s, reconnecting", self._host)
            try:
                self.reconnect()
            except ConnectionError as re_exc:
                raise ConnectionError(
                    f"Keep-alive probe failed for {self._host} and reconnect also failed: {re_exc}"
                ) from re_exc

    # --- Raw command execution ---

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands via the PAN-OS XML API.

        Maps CLI-style commands to PAN-OS XML API operational commands.
        The ``encoding`` argument is ignored — PAN-OS always returns XML.

        Args:
            commands: List of show commands (e.g., ['show system info']).
            encoding: Ignored; PAN-OS responses are always XML.

        Returns:
            List of dicts with 'output' (raw XML text) or 'error' key per command.
        """
        if not self._connected or not self._client:
            raise ConnectionError("PanosDriver is not connected. Call connect() first.")
        self._check_alive()
        results: list[dict] = []
        for cmd in commands:
            xml_cmd = _cli_to_xml(cmd)
            if xml_cmd:
                try:
                    root = self._xml_op(xml_cmd)
                    results.append({"output": ET.tostring(root, encoding="unicode")})
                except Exception as exc:
                    logger.warning("PAN-OS XML op '%s' failed: %s", xml_cmd, exc)
                    results.append({"error": str(exc)})
            else:
                results.append({"error": f"No XML mapping for command: {cmd}"})
        return results

    def run_config(self, commands: list[str]) -> list[str]:
        """Configuration commands are not yet implemented for PAN-OS."""
        raise NotSupportedError("run_config is not yet implemented for the PAN-OS driver")

    # --- Normalized getters ---

    def get_facts(self) -> dict:
        """Get device facts from 'show system info'.

        Returns:
            dict with hostname, model, version, serial, uptime_seconds, vendor, platform.
        """
        root = self._xml_op("<show><system><info/></system></show>")
        sys_el = root.find(".//system")
        if sys_el is None:
            raise ValueError("Unexpected PAN-OS system info response: missing <system> element")
        return {
            "hostname": _xml_text(sys_el, "hostname"),
            "model": _xml_text(sys_el, "model"),
            "version": _xml_text(sys_el, "sw-version"),
            "serial": _xml_text(sys_el, "serial"),
            "uptime_seconds": _parse_panos_uptime(_xml_text(sys_el, "uptime")),
            "vendor": "palo_alto",
            "platform": "panos",
        }

    def get_interfaces(self) -> dict[str, dict]:
        """Get interface status from 'show interface all'.

        Parses both logical (<ifnet>) and hardware (<hw>) interface entries.

        Returns:
            dict mapping interface name to is_up, is_enabled, description,
            speed (Mbps), mtu, mac_address fields.
        """
        root = self._xml_op("<show><interface>all</interface></show>")
        result: dict[str, dict] = {}

        # Parse logical interfaces from <ifnet> section
        for entry in root.findall(".//ifnet/entry"):
            name = _xml_text(entry, "name")
            if not name:
                continue
            state = _xml_text(entry, "state").lower()
            speed_str = _xml_text(entry, "speed")
            mtu_str = _xml_text(entry, "mtu")
            mac = _xml_text(entry, "mac")
            try:
                speed = int(speed_str) if speed_str.isdigit() else 0
            except (ValueError, AttributeError):
                speed = 0
            try:
                mtu = int(mtu_str) if mtu_str.isdigit() else 1500
            except (ValueError, AttributeError):
                mtu = 1500
            result[name] = {
                "is_up": state == "up",
                "is_enabled": state not in ("down", "disabled"),
                "description": "",
                "speed": speed,
                "mtu": mtu,
                "mac_address": _normalize_mac(mac) if mac else "",
            }

        # Parse hardware interfaces from <hw> section (physical ports)
        for entry in root.findall(".//hw/entry"):
            name = _xml_text(entry, "name")
            if not name or name in result:
                continue
            state = _xml_text(entry, "state").lower()
            speed_str = _xml_text(entry, "speed")
            mac = _xml_text(entry, "mac")
            mtu_str = _xml_text(entry, "mtu")
            try:
                speed = int(speed_str) if speed_str and speed_str.isdigit() else 0
            except (ValueError, AttributeError):
                speed = 0
            try:
                mtu = int(mtu_str) if mtu_str and mtu_str.isdigit() else 1500
            except (ValueError, AttributeError):
                mtu = 1500
            result[name] = {
                "is_up": state == "up",
                "is_enabled": state not in ("down", "disabled"),
                "description": "",
                "speed": speed,
                "mtu": mtu,
                "mac_address": _normalize_mac(mac) if mac else "",
            }

        return result

    def get_arp_table(self) -> list[dict]:
        """Get ARP table from 'show arp all'.

        Returns:
            list of dicts with ip, mac, interface, age fields.
        """
        root = self._xml_op("<show><arp><entry name='all'/></arp></show>")
        entries: list[dict] = []
        for entry in root.findall(".//entries/entry"):
            ip = _xml_text(entry, "ip")
            if not ip:
                continue
            mac = _xml_text(entry, "hw")
            iface = _xml_text(entry, "interface")
            ttl_str = _xml_text(entry, "ttl")
            try:
                age = float(ttl_str) if ttl_str else -1.0
            except (ValueError, TypeError):
                age = -1.0
            entries.append({
                "ip": ip,
                "mac": _normalize_mac(mac) if mac else "",
                "interface": iface,
                "age": age,
            })
        return entries

    def get_route_table(self, protocol: str = "", vrf: str = "default", limit: int = 100) -> list[dict]:
        """Get routing table from 'show routing route'.

        Args:
            protocol: Filter by protocol ('bgp', 'ospf', 'connected', 'static'). Empty = all.
            vrf: Ignored — PAN-OS virtual routers are not filtered here.
            limit: Maximum number of routes to return.

        Returns:
            list of dicts with prefix, next_hop, protocol, metric, preference fields.
        """
        root = self._xml_op("<show><routing><route/></routing></show>")
        routes: list[dict] = []
        for entry in root.findall(".//entry"):
            dest = _xml_text(entry, "destination")
            if not dest:
                continue
            nexthop = _xml_text(entry, "nexthop")
            flags = _xml_text(entry, "flags")
            metric_str = _xml_text(entry, "metric")
            proto = _normalize_panos_route_flags(flags)
            if protocol and proto != protocol.lower():
                continue
            try:
                metric = int(metric_str) if metric_str else 0
            except (ValueError, TypeError):
                metric = 0
            routes.append({
                "prefix": dest,
                "next_hop": nexthop,
                "protocol": proto,
                "metric": metric,
                "preference": 0,
            })
            if len(routes) >= limit:
                break
        return routes

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Get LLDP neighbor information.

        PAN-OS LLDP support is limited and may not be available on all platforms.
        Returns an empty dict if LLDP is not configured or the API returns an error.

        Returns:
            dict mapping local interface name to list of neighbor dicts
            (hostname, port, system_description).
        """
        try:
            root = self._xml_op("<show><lldp><neighbors>all</neighbors></lldp></show>")
        except (ValueError, Exception) as exc:
            logger.debug("PAN-OS LLDP not available or not supported: %s", exc)
            return {}
        result: dict[str, list[dict]] = {}
        for entry in root.findall(".//entry"):
            local_if = _xml_text(entry, "local-interface")
            if not local_if:
                continue
            neighbor = {
                "hostname": _xml_text(entry, "system-name"),
                "port": _xml_text(entry, "port-id"),
                "system_description": _xml_text(entry, "system-description"),
            }
            result.setdefault(local_if, []).append(neighbor)
        return result

    # --- Unsupported getters ---

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        raise NotSupportedError("get_bgp_summary is not supported on PAN-OS")

    def get_bgp_neighbors(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_bgp_neighbors is not supported on PAN-OS")

    def get_config(self, sanitized: bool = True) -> dict:
        raise NotSupportedError("get_config is not supported on PAN-OS driver")

    def get_vlans(self) -> list[dict]:
        raise NotSupportedError("get_vlans is not supported on PAN-OS")

    def get_vlan_detail(self, vlan_id: int) -> dict:
        raise NotSupportedError("get_vlan_detail is not supported on PAN-OS")

    def get_interface_counters(self) -> list[dict]:
        raise NotSupportedError("get_interface_counters is not supported on PAN-OS")

    def get_interface_errors(self) -> list[dict]:
        raise NotSupportedError("get_interface_errors is not supported on PAN-OS")

    def get_ip_interfaces(self) -> list[dict]:
        raise NotSupportedError("get_ip_interfaces is not supported on PAN-OS")

    def get_transceiver_info(self) -> list[dict]:
        raise NotSupportedError("get_transceiver_info is not supported on PAN-OS")

    def get_ospf_neighbors(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_ospf_neighbors is not supported on PAN-OS")

    def get_ospf_interfaces(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_ospf_interfaces is not supported on PAN-OS")

    def get_route_summary(self) -> dict:
        raise NotSupportedError("get_route_summary is not supported on PAN-OS")

    def get_mac_table(self, vlan: int | None = None, limit: int = 500) -> list[dict]:
        raise NotSupportedError("get_mac_table is not supported on PAN-OS")

    def get_stp_status(self) -> dict:
        raise NotSupportedError("get_stp_status is not supported on PAN-OS")

    def get_port_channels(self) -> list[dict]:
        raise NotSupportedError("get_port_channels is not supported on PAN-OS")

    def get_environment(self) -> dict:
        raise NotSupportedError("get_environment is not supported on PAN-OS")

    def get_ntp_status(self) -> dict:
        raise NotSupportedError("get_ntp_status is not supported on PAN-OS")

    def get_cpu_memory(self) -> dict:
        raise NotSupportedError("get_cpu_memory is not supported on PAN-OS")

    def get_acls(self) -> list[dict]:
        raise NotSupportedError("get_acls is not supported on PAN-OS")

    def get_snmp_config(self) -> dict:
        raise NotSupportedError("get_snmp_config is not supported on PAN-OS")

    def get_running_config_section(self, section: str) -> str:
        raise NotSupportedError("get_running_config_section is not supported on PAN-OS")

    def get_startup_config(self) -> str:
        raise NotSupportedError("get_startup_config is not supported on PAN-OS")

    def get_vrfs(self) -> list[dict]:
        raise NotSupportedError("get_vrfs is not supported on PAN-OS")

    def get_vrf_detail(self, vrf: str) -> dict:
        raise NotSupportedError("get_vrf_detail is not supported on PAN-OS")

    def get_trunk_interfaces(self) -> list[dict]:
        raise NotSupportedError("get_trunk_interfaces is not supported on PAN-OS")

    def get_lldp_neighbor_detail(self, interface: str = "") -> dict[str, list[dict]]:
        raise NotSupportedError("get_lldp_neighbor_detail is not supported on PAN-OS")

    def ping(self, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
        raise NotSupportedError("ping is not supported on the PAN-OS driver")

    def traceroute(self, destination: str, source: str = "", vrf: str = "default") -> dict:
        raise NotSupportedError("traceroute is not supported on the PAN-OS driver")

    def get_interface_flaps(self, hours: int = 24) -> list[dict]:
        raise NotSupportedError("get_interface_flaps is not supported on PAN-OS")

    def get_aaa_status(self) -> dict:
        raise NotSupportedError("get_aaa_status is not supported on PAN-OS")

    def get_user_sessions(self) -> list[dict]:
        raise NotSupportedError("get_user_sessions is not supported on PAN-OS")

    def get_copp_policy(self) -> dict:
        raise NotSupportedError("get_copp_policy is not supported on PAN-OS")

    def capabilities(self) -> dict[str, bool]:
        """Return supported features and getter availability for PAN-OS."""
        return {
            "config_sessions": False,
            "gnmi": False,
            "restconf": False,
            "netconf": False,
            "xml_api": True,
            "ssh": False,
            "get_facts": True,
            "get_interfaces": True,
            "get_bgp_summary": False,
            "get_bgp_neighbors": False,
            "get_arp_table": True,
            "get_lldp_neighbors": True,
            "get_config": False,
            "get_vlans": False,
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
