"""Fortinet FortiOS driver — REST API v2 transport.

Implements the NetworkDriver protocol for Fortinet FortiGate firewalls using
the FortiOS REST API v2 over HTTPS (port 443). Supports API key authentication
via Bearer token header.

Requires httpx (available via the 'cisco' or 'sonic' extras).
"""

from __future__ import annotations

import logging
import time
from typing import Any

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


def _safe_int(value: Any, default: int = 0) -> int:
    """Convert a value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float, returning default on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


class FortiosDriver:
    """Fortinet FortiOS driver using the REST API v2 over HTTPS.

    Satisfies the NetworkDriver protocol defined in drivers/base.py.

    FortiOS devices expose a REST API at https://{host}/api/v2/ with API key
    authentication via Bearer token. This driver supports pre-generated API keys
    set via the NET_FORTIOS_API_KEY environment variable or passed directly.
    """

    platform: str = "fortios"
    vendor: str = "fortinet"

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
        """Establish connection to a FortiOS device via the REST API v2.

        Args:
            host: Device hostname or IP address.
            transport: Transport protocol (only 'https' supported).
            username: Unused — FortiOS REST API uses API keys only.
            password: Unused — FortiOS REST API uses API keys only.
            **kwargs: Additional options:
                - api_key (str): FortiOS REST API key (required).
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

        if not api_key:
            raise ValueError(
                "api_key must be provided to connect to FortiOS (set NET_FORTIOS_API_KEY or pass api_key kwarg)"
            )

        self._host = host
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=f"https://{host}:{port}",
            verify=verify_ssl,
            timeout=self._timeout_show,
            headers={"Authorization": f"Bearer {api_key}"},
        )

        # Probe connectivity
        try:
            self._get("/api/v2/monitor/system/status")
            self._connected = True
            self._last_command_time = time.monotonic()
            logger.debug("FortiOS REST API connected to %s:%s", host, port)
        except Exception as exc:
            if self._client:
                self._client.close()
                self._client = None
            raise ConnectionError(f"Failed to connect to FortiOS device {host}: {exc}") from exc

    def close(self) -> None:
        """Close the HTTP connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                logger.debug("Failed to close FortiOS HTTP client: %s", exc)
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
            headers={"Authorization": f"Bearer {saved_key}"},
        )
        self._api_key = saved_key
        try:
            self._get("/api/v2/monitor/system/status")
            self._connected = True
            self._last_command_time = time.monotonic()
        except Exception as exc:
            if self._client:
                self._client.close()
                self._client = None
            raise ConnectionError(f"Reconnect failed for {self._host}: {exc}") from exc

    # --- Internal helpers ---

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """Execute a GET request to the FortiOS REST API.

        Args:
            path: API path (e.g., '/api/v2/monitor/system/status').
            params: Optional query parameters.

        Returns:
            Parsed JSON response dict.

        Raises:
            ConnectionError: If not connected or the HTTP request fails.
            ValueError: If FortiOS returns an error status in the response body.
        """
        if not self._client:
            raise ConnectionError("FortiosDriver is not connected. Call connect() first.")
        self._last_command_time = time.monotonic()
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        # Monitor endpoints: {"status": "success", "results": {...}}
        # CMDB endpoints: {"status": 200, "results": [...]}
        # Error: {"status": "error", "message": "..."}
        status = data.get("status")
        if status not in ("success", 200, None):
            msg = data.get("message", str(status))
            raise ValueError(f"FortiOS API error at {path}: {msg}")
        return data

    def _check_alive(self) -> None:
        """Send a lightweight probe if the session has been idle too long."""
        if not self._connected:
            return
        elapsed = time.monotonic() - self._last_command_time
        if elapsed < self._keepalive_interval:
            return
        try:
            self._get("/api/v2/monitor/system/status")
        except Exception:
            logger.info("FortiOS keep-alive failed for %s, reconnecting", self._host)
            try:
                self.reconnect()
            except ConnectionError as re_exc:
                raise ConnectionError(
                    f"Keep-alive probe failed for {self._host} and reconnect also failed: {re_exc}"
                ) from re_exc

    # --- Raw command execution (not supported via REST API) ---

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """FortiOS CLI commands are not supported via the REST API driver."""
        raise NotSupportedError("run_show is not supported on the FortiOS REST API driver")

    def run_config(self, commands: list[str]) -> list[str]:
        """Configuration commands are not yet implemented for FortiOS."""
        raise NotSupportedError("run_config is not yet implemented for the FortiOS driver")

    # --- Normalized getters ---

    def get_facts(self) -> dict:
        """Get device facts from GET /api/v2/monitor/system/status.

        Returns:
            dict with hostname, model, version, serial, uptime_seconds, vendor, platform.
        """
        data = self._get("/api/v2/monitor/system/status")
        results = data.get("results", {})
        return {
            "hostname": results.get("hostname", ""),
            "model": results.get("model", ""),
            "version": results.get("version", ""),
            "serial": results.get("serial_number", ""),
            "uptime_seconds": _safe_int(results.get("uptime", 0)),
            "vendor": "fortinet",
            "platform": "fortios",
        }

    def get_interfaces(self) -> dict[str, dict]:
        """Get interface status from GET /api/v2/monitor/system/interface.

        Returns:
            dict mapping interface name to is_up, is_enabled, description,
            speed (Mbps), mtu, mac_address fields.
        """
        data = self._get("/api/v2/monitor/system/interface")
        results = data.get("results", {})
        # Results can be a dict (keyed by name) or a list
        if isinstance(results, list):
            entries = results
        else:
            entries = list(results.values())

        ifaces: dict[str, dict] = {}
        for entry in entries:
            name = entry.get("name", "")
            if not name:
                continue
            link = entry.get("link", False)
            # Speed may be reported in bps (e.g. 1000000000) or Mbps (e.g. 1000)
            speed_raw = _safe_int(entry.get("speed", 0))
            speed_mbps = speed_raw // 1_000_000 if speed_raw > 1_000_000 else speed_raw
            mac = entry.get("mac_address", entry.get("mac", ""))
            ifaces[name] = {
                "is_up": bool(link),
                "is_enabled": entry.get("status", "").lower() != "down",
                "description": entry.get("description", ""),
                "speed": speed_mbps,
                "mtu": _safe_int(entry.get("mtu", 1500)),
                "mac_address": _normalize_mac(mac) if mac else "",
            }
        return ifaces

    def get_arp_table(self) -> list[dict]:
        """Get ARP table from GET /api/v2/monitor/network/arp.

        Returns:
            list of dicts with ip, mac, interface, age fields.
        """
        data = self._get("/api/v2/monitor/network/arp")
        results = data.get("results", [])
        entries: list[dict] = []
        for entry in results:
            ip = entry.get("ip", "")
            if not ip:
                continue
            mac = entry.get("mac", "")
            entries.append({
                "ip": ip,
                "mac": _normalize_mac(mac) if mac else "",
                "interface": entry.get("interface", ""),
                "age": _safe_float(entry.get("age", -1.0)),
            })
        return entries

    def get_route_table(self, protocol: str = "", vrf: str = "default", limit: int = 100) -> list[dict]:
        """Get routing table from GET /api/v2/monitor/router/ipv4.

        Args:
            protocol: Filter by protocol (e.g., 'bgp', 'ospf', 'connected', 'static'). Empty = all.
            vrf: Ignored — FortiOS virtual domains are not filtered here.
            limit: Maximum number of routes to return.

        Returns:
            list of dicts with prefix, next_hop, protocol, metric, preference fields.
        """
        data = self._get("/api/v2/monitor/router/ipv4")
        results = data.get("results", [])
        routes: list[dict] = []
        for entry in results:
            prefix = entry.get("ip_mask", entry.get("prefix", ""))
            if not prefix:
                continue
            proto = entry.get("type", "unknown").lower()
            if protocol and proto != protocol.lower():
                continue
            # Gateway can be a list of dicts or a plain string
            gw_list = entry.get("gateway", [])
            if isinstance(gw_list, list) and gw_list:
                next_hop = gw_list[0].get("ip", "") if isinstance(gw_list[0], dict) else str(gw_list[0])
            elif isinstance(gw_list, str):
                next_hop = gw_list
            else:
                next_hop = ""
            routes.append({
                "prefix": prefix,
                "next_hop": next_hop,
                "protocol": proto,
                "metric": _safe_int(entry.get("metric", 0)),
                "preference": _safe_int(entry.get("distance", 0)),
            })
            if len(routes) >= limit:
                break
        return routes

    def get_firewall_policies(self) -> list[dict]:
        """Get firewall policies from GET /api/v2/cmdb/firewall/policy.

        Returns:
            list of dicts, each with keys:
                - policy_id (int): Policy sequence number
                - name (str): Policy name
                - action (str): 'accept', 'deny', or 'ipsec'
                - status (str): 'enable' or 'disable'
                - srcintf (list[str]): Source interface names
                - dstintf (list[str]): Destination interface names
                - srcaddr (list[str]): Source address object names
                - dstaddr (list[str]): Destination address object names
                - service (list[str]): Service object names
                - nat (bool): Whether NAT is enabled
                - comments (str): Policy comments
        """
        data = self._get("/api/v2/cmdb/firewall/policy")
        results = data.get("results", [])
        policies: list[dict] = []
        for entry in results:
            srcintf = [i.get("name", "") for i in entry.get("srcintf", []) if isinstance(i, dict)]
            dstintf = [i.get("name", "") for i in entry.get("dstintf", []) if isinstance(i, dict)]
            srcaddr = [a.get("name", "") for a in entry.get("srcaddr", []) if isinstance(a, dict)]
            dstaddr = [a.get("name", "") for a in entry.get("dstaddr", []) if isinstance(a, dict)]
            service = [s.get("name", "") for s in entry.get("service", []) if isinstance(s, dict)]
            policies.append({
                "policy_id": _safe_int(entry.get("policyid", 0)),
                "name": entry.get("name", ""),
                "action": entry.get("action", ""),
                "status": entry.get("status", ""),
                "srcintf": srcintf,
                "dstintf": dstintf,
                "srcaddr": srcaddr,
                "dstaddr": dstaddr,
                "service": service,
                "nat": entry.get("nat", "disable") == "enable",
                "comments": entry.get("comments", ""),
            })
        return policies

    # --- Unsupported getters ---

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        raise NotSupportedError("get_bgp_summary is not supported on FortiOS driver")

    def get_bgp_neighbors(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_bgp_neighbors is not supported on FortiOS driver")

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        raise NotSupportedError("get_lldp_neighbors is not supported on FortiOS driver")

    def get_config(self, sanitized: bool = True) -> dict:
        raise NotSupportedError("get_config is not supported on FortiOS driver")

    def get_vlans(self) -> list[dict]:
        raise NotSupportedError("get_vlans is not supported on FortiOS driver")

    def get_vlan_detail(self, vlan_id: int) -> dict:
        raise NotSupportedError("get_vlan_detail is not supported on FortiOS driver")

    def get_interface_counters(self) -> list[dict]:
        raise NotSupportedError("get_interface_counters is not supported on FortiOS driver")

    def get_interface_errors(self) -> list[dict]:
        raise NotSupportedError("get_interface_errors is not supported on FortiOS driver")

    def get_ip_interfaces(self) -> list[dict]:
        raise NotSupportedError("get_ip_interfaces is not supported on FortiOS driver")

    def get_transceiver_info(self) -> list[dict]:
        raise NotSupportedError("get_transceiver_info is not supported on FortiOS driver")

    def get_ospf_neighbors(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_ospf_neighbors is not supported on FortiOS driver")

    def get_ospf_interfaces(self, vrf: str = "default") -> list[dict]:
        raise NotSupportedError("get_ospf_interfaces is not supported on FortiOS driver")

    def get_route_summary(self) -> dict:
        raise NotSupportedError("get_route_summary is not supported on FortiOS driver")

    def get_mac_table(self, vlan: int | None = None, limit: int = 500) -> list[dict]:
        raise NotSupportedError("get_mac_table is not supported on FortiOS driver")

    def get_stp_status(self) -> dict:
        raise NotSupportedError("get_stp_status is not supported on FortiOS driver")

    def get_port_channels(self) -> list[dict]:
        raise NotSupportedError("get_port_channels is not supported on FortiOS driver")

    def get_environment(self) -> dict:
        raise NotSupportedError("get_environment is not supported on FortiOS driver")

    def get_ntp_status(self) -> dict:
        raise NotSupportedError("get_ntp_status is not supported on FortiOS driver")

    def get_cpu_memory(self) -> dict:
        raise NotSupportedError("get_cpu_memory is not supported on FortiOS driver")

    def get_acls(self) -> list[dict]:
        raise NotSupportedError("get_acls is not supported on FortiOS driver")

    def get_snmp_config(self) -> dict:
        raise NotSupportedError("get_snmp_config is not supported on FortiOS driver")

    def get_running_config_section(self, section: str) -> str:
        raise NotSupportedError("get_running_config_section is not supported on FortiOS driver")

    def get_startup_config(self) -> str:
        raise NotSupportedError("get_startup_config is not supported on FortiOS driver")

    def get_vrfs(self) -> list[dict]:
        raise NotSupportedError("get_vrfs is not supported on FortiOS driver")

    def get_vrf_detail(self, vrf: str) -> dict:
        raise NotSupportedError("get_vrf_detail is not supported on FortiOS driver")

    def get_trunk_interfaces(self) -> list[dict]:
        raise NotSupportedError("get_trunk_interfaces is not supported on FortiOS driver")

    def get_lldp_neighbor_detail(self, interface: str = "") -> dict[str, list[dict]]:
        raise NotSupportedError("get_lldp_neighbor_detail is not supported on FortiOS driver")

    def ping(self, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
        raise NotSupportedError("ping is not supported on the FortiOS driver")

    def traceroute(self, destination: str, source: str = "", vrf: str = "default") -> dict:
        raise NotSupportedError("traceroute is not supported on the FortiOS driver")

    def get_interface_flaps(self, hours: int = 24) -> list[dict]:
        raise NotSupportedError("get_interface_flaps is not supported on FortiOS driver")

    def get_aaa_status(self) -> dict:
        raise NotSupportedError("get_aaa_status is not supported on FortiOS driver")

    def get_user_sessions(self) -> list[dict]:
        raise NotSupportedError("get_user_sessions is not supported on FortiOS driver")

    def get_copp_policy(self) -> dict:
        raise NotSupportedError("get_copp_policy is not supported on FortiOS driver")

    def capabilities(self) -> dict[str, bool]:
        """Return supported features and getter availability for FortiOS."""
        return {
            "config_sessions": False,
            "gnmi": False,
            "restconf": False,
            "netconf": False,
            "rest_api": True,
            "ssh": False,
            "get_facts": True,
            "get_interfaces": True,
            "get_bgp_summary": False,
            "get_bgp_neighbors": False,
            "get_arp_table": True,
            "get_lldp_neighbors": False,
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
            "get_firewall_policies": True,
        }
