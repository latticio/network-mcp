"""Juniper JunOS driver — NETCONF primary transport with SSH fallback.

Implements the NetworkDriver protocol for Juniper JunOS devices using
NETCONF as the primary transport. Falls back to SSH when NETCONF port
830 is blocked or unavailable.

SSH fallback uses scrapli for persistent, secure sessions with proper
host key verification.

Requires the ``juniper`` optional extra: ``pip install network-mcp[juniper]``
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET  # noqa: N817, S405
from typing import Any

from pydantic import SecretStr
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import normalize_mac as _normalize_mac

try:
    from scrapli_netconf.driver import NetconfDriver

    HAS_SCRAPLI_NETCONF = True
except ImportError:
    NetconfDriver = None  # type: ignore[assignment, misc]
    HAS_SCRAPLI_NETCONF = False

try:
    from scrapli.driver.core import JunosDriver as ScrapliJunosDriver
    from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliTimeout

    HAS_SCRAPLI = True
except ImportError:
    ScrapliJunosDriver = None  # type: ignore[assignment, misc]
    ScrapliAuthenticationFailed = None  # type: ignore[assignment, misc]
    ScrapliTimeout = None  # type: ignore[assignment, misc]
    HAS_SCRAPLI = False

logger = logging.getLogger("network-mcp")

# Default idle threshold (seconds) before a keep-alive check is triggered
_DEFAULT_KEEPALIVE_INTERVAL = 60


def _xml_find_text(element: ET.Element, tag: str, default: str = "") -> str:
    """Find text content of a child element, stripping namespaces.

    JunOS NETCONF responses use Juniper namespaces which complicate
    direct XPath queries. This helper strips namespaces for simpler lookups.
    """
    # Try direct match first
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()

    # Try namespace-agnostic search
    for elem in element:
        local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if local_name == tag and elem.text:
            return elem.text.strip()

    return default


def _xml_findall(element: ET.Element, tag: str) -> list[ET.Element]:
    """Find all child elements matching tag, ignoring namespaces."""
    results: list[ET.Element] = []
    for elem in element:
        local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if local_name == tag:
            results.append(elem)
    return results


def _xml_find(element: ET.Element, tag: str) -> ET.Element | None:
    """Find first child element matching tag, ignoring namespaces."""
    for elem in element:
        local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if local_name == tag:
            return elem
    return None


def _safe_parse_xml(xml_str: str, command: str, host: str = "unknown") -> ET.Element | None:
    """Parse XML string with debug logging on failure.

    Returns the root Element on success, or ``None`` on parse error.
    Callers should return a sensible default (empty dict/list) when
    ``None`` is returned.
    """
    try:
        return ET.fromstring(xml_str)  # noqa: S314
    except ET.ParseError as exc:
        logger.debug("XML parse error in %s response from %s: %s", command, host, exc)
        return None


def _parse_junos_uptime(uptime_str: str) -> int:
    """Parse JunOS uptime string to seconds.

    JunOS formats:
    - "{ junos:seconds 864000 }" (XML attribute style)
    - "864000" (plain seconds)
    - "10 days, 0:00:00" (human-readable from CLI)
    """
    if not uptime_str:
        return 0

    # Plain seconds
    try:
        return int(uptime_str)
    except ValueError:
        pass

    # Try extracting seconds from XML-style
    sec_match = re.search(r"seconds\s+(\d+)", uptime_str)
    if sec_match:
        return int(sec_match.group(1))

    # Human-readable: "10 days, 3:45:12"
    total = 0
    day_match = re.search(r"(\d+)\s+day", uptime_str)
    if day_match:
        total += int(day_match.group(1)) * 86400
    time_match = re.search(r"(\d+):(\d+):(\d+)", uptime_str)
    if time_match:
        total += int(time_match.group(1)) * 3600 + int(time_match.group(2)) * 60 + int(time_match.group(3))
    return total


def _parse_junos_speed(speed_str: str) -> int:
    """Parse JunOS interface speed string to Mbps.

    JunOS formats: "1000mbps", "10Gbps", "100000", "Auto"
    """
    if not speed_str:
        return 0
    speed_str = speed_str.strip().lower()

    if speed_str in ("auto", "unspecified", ""):
        return 0

    gbps_match = re.match(r"(\d+)\s*gbps?", speed_str)
    if gbps_match:
        return int(gbps_match.group(1)) * 1000

    mbps_match = re.match(r"(\d+)\s*mbps?", speed_str)
    if mbps_match:
        return int(mbps_match.group(1))

    try:
        val = int(speed_str)
        # If value is very large, it's likely in bps
        if val >= 1_000_000_000:
            return val // 1_000_000
        if val >= 1_000_000:
            return val // 1_000
        return val
    except ValueError:
        return 0


# --- SSH CLI text parsers ---


def _parse_facts_ssh(output: str) -> dict:
    """Parse 'show version' and 'show chassis hardware' text output into facts schema.

    JunOS 'show version' example:
        Hostname: junos-spine-01
        Model: qfx5120-48t
        Junos: 23.4R1.10
        ...
    """
    hostname = ""
    model = ""
    version = ""
    serial = ""
    uptime_seconds = 0

    for line in output.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("Hostname:"):
            hostname = line_stripped.split(":", 1)[1].strip()
        elif line_stripped.startswith("Model:"):
            model = line_stripped.split(":", 1)[1].strip()
        elif line_stripped.startswith("Junos:"):
            version = line_stripped.split(":", 1)[1].strip()
        elif re.match(r"^JUNOS\s+", line_stripped) and not version:
            ver_match = re.search(r"\[(.+?)\]", line_stripped)
            if ver_match:
                version = ver_match.group(1)
        elif "Serial Number" in line_stripped or "serial-number" in line_stripped.lower():
            serial_match = re.search(r"(?:Serial Number|serial-number)[:\s]+(\S+)", line_stripped, re.IGNORECASE)
            if serial_match:
                serial = serial_match.group(1)
        elif "System booted" in line_stripped or "uptime" in line_stripped.lower():
            # Extract uptime from parenthesized "(10 days, 3:45:12 ago)" if present,
            # otherwise use the whole line
            paren_match = re.search(r"\((.+?)\)", line_stripped)
            uptime_text = paren_match.group(1) if paren_match else line_stripped
            uptime_match = re.search(r"(\d+)\s+day", uptime_text)
            if uptime_match:
                uptime_seconds += int(uptime_match.group(1)) * 86400
            time_match = re.search(r"(\d+):(\d+):(\d+)", uptime_text)
            if time_match:
                uptime_seconds += (
                    int(time_match.group(1)) * 3600 + int(time_match.group(2)) * 60 + int(time_match.group(3))
                )

    return {
        "hostname": hostname,
        "model": model,
        "version": version,
        "serial": serial,
        "uptime_seconds": uptime_seconds,
        "vendor": "juniper",
        "platform": "junos",
    }


def _parse_interfaces_ssh(output: str) -> dict[str, dict]:
    """Parse 'show interfaces terse' text output into interfaces schema.

    JunOS 'show interfaces terse' example:
        Interface               Admin Link Proto    Local                 Remote
        ge-0/0/0                up    up
        ge-0/0/0.0              up    up   inet     10.0.0.1/30
        ge-0/0/1                up    down
    """
    normalized: dict[str, dict] = {}
    for line in output.splitlines():
        # Skip header and empty lines
        if not line.strip() or line.strip().startswith("Interface"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        name = parts[0]
        # Skip logical interfaces (those with .N suffix)
        if "." in name:
            continue
        admin_status = parts[1].lower()
        link_status = parts[2].lower()

        normalized[name] = {
            "is_up": link_status == "up",
            "is_enabled": admin_status == "up",
            "description": "",
            "speed": 0,
            "mtu": 1500,
            "mac_address": "",
        }

    return normalized


def _parse_bgp_summary_ssh(output: str) -> dict:
    """Parse 'show bgp summary' text output into BGP summary schema.

    JunOS 'show bgp summary' example:
        Threading mode: BGP I/O
        ...
        Groups: 2 Peers: 2 Down peers: 0
        ...
        Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/...
        10.0.0.2              65001       1234       1235       0       0     1d 2:03:04 Establ
          inet.0: 150/200/150/0
        10.0.0.3              65002          0          0       0       0        3:45:12 Active
    """
    router_id = ""
    as_number = 0
    peers: dict[str, dict] = {}

    in_peer_section = False
    current_peer = ""

    for line in output.splitlines():
        # Router ID: "Router ID: 10.0.0.1"
        id_match = re.search(r"Router ID:\s+(\S+)", line)
        if id_match:
            router_id = id_match.group(1)

        # Local AS: may appear as "Local AS: 65000" or in the header
        as_match = re.search(r"Local AS:\s+(\d+)", line)
        if as_match:
            as_number = int(as_match.group(1))

        # Peer lines: start with IP address followed by AS, then stats columns.
        # The state/prefix field is always the LAST token on the line.
        # "Last Up/Dwn" field can contain spaces (e.g., "1d 2:03:04").
        peer_match = re.match(r"^(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+", line)
        if peer_match:
            in_peer_section = True
            current_peer = peer_match.group(1)
            # State is the last whitespace-separated token on the line
            parts = line.split()
            state_field = parts[-1] if parts else "Unknown"

            # State: "Establ" means Established; anything else is the state
            if state_field.startswith("Establ"):
                state = "Established"
            else:
                state = state_field

            peers[current_peer] = {
                "state": state,
                "prefixes_received": 0,
                "uptime": 0,
            }
        elif in_peer_section and current_peer and re.match(r"^\s+inet\.0:", line):
            # Prefix line: "  inet.0: 150/200/150/0"
            pfx_match = re.search(r"inet\.0:\s+(\d+)/(\d+)/(\d+)", line)
            if pfx_match:
                peers[current_peer]["prefixes_received"] = int(pfx_match.group(2))

    return {
        "router_id": router_id,
        "as_number": as_number,
        "peers": peers,
    }


def _parse_arp_ssh(output: str) -> list[dict]:
    """Parse 'show arp no-resolve' text output into ARP table schema.

    JunOS 'show arp no-resolve' example:
        MAC Address       Address         Name                      Interface               Flags
        00:05:86:cc:dd:01 10.0.0.2        10.0.0.2                  ge-0/0/0                none
        00:05:86:cc:dd:02 10.0.0.3        10.0.0.3                  ge-0/0/1                none
    """
    entries: list[dict] = []
    for line in output.splitlines():
        # Skip header and empty lines
        if not line.strip() or line.strip().startswith("MAC Address") or line.strip().startswith("Total"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        mac_raw = parts[0]
        ip_addr = parts[1]
        # Validate IP format
        if not re.match(r"\d+\.\d+\.\d+\.\d+", ip_addr):
            continue
        interface = parts[3]

        entries.append(
            {
                "ip": ip_addr,
                "mac": _normalize_mac(mac_raw),
                "interface": interface,
                "age": -1.0,
            }
        )

    return entries


def _parse_lldp_ssh(output: str) -> dict[str, list[dict]]:
    """Parse 'show lldp neighbors' text output into LLDP schema.

    JunOS 'show lldp neighbors' example:
        Local Interface    Parent Interface    Chassis Id          Port info          System Name
        ge-0/0/0           -                   00:05:86:71:62:02   ge-0/0/1           spine-02.lab
        xe-0/0/0           -                   00:05:86:71:62:03   xe-0/0/1           leaf-01.lab
    """
    normalized: dict[str, list[dict]] = {}
    for line in output.splitlines():
        # Skip header and empty lines
        if not line.strip() or line.strip().startswith("Local Interface"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_intf = parts[0]
        # Skip if first field doesn't look like an interface name
        if not re.match(r"[a-z]", local_intf):
            continue
        # parent_interface = parts[1] (not used)
        # chassis_id = parts[2] (not used)
        port = parts[3]
        hostname = parts[4] if len(parts) >= 5 else ""

        normalized.setdefault(local_intf, []).append(
            {
                "hostname": hostname,
                "port": port,
                "system_description": "",
            }
        )

    return normalized


# --- SSH transport class ---


class JunosScrapliTransport:
    """Persistent SSH session using scrapli for JunOS devices.

    Provides secure, reusable SSH connections with proper host key
    verification.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 22,
        auth_private_key: str = "",
        auth_strict_key: bool = True,
        ssh_known_hosts_file: str = "~/.ssh/known_hosts",
        timeout_connect: float = 10.0,
        timeout_ops: float = 30.0,
    ) -> None:
        if not HAS_SCRAPLI:
            raise ImportError("scrapli not installed. Install with: pip install network-mcp[juniper]")
        driver_kwargs: dict[str, Any] = {
            "host": host,
            "auth_username": username,
            "auth_password": password,
            "port": port,
            "auth_strict_key": auth_strict_key,
            "ssh_known_hosts_file": ssh_known_hosts_file,
            "transport": "system",
            "timeout_socket": timeout_connect,
            "timeout_transport": timeout_connect,
            "timeout_ops": timeout_ops,
        }
        if auth_private_key:
            driver_kwargs["auth_private_key"] = auth_private_key
        self._driver = ScrapliJunosDriver(**driver_kwargs)
        self._connected = False

    def open(self) -> None:
        """Open the SSH connection."""
        self._driver.open()
        self._connected = True

    def send_command(self, command: str) -> str:
        """Send a single show command and return output."""
        if not self._connected:
            self.open()
        response = self._driver.send_command(command)
        if response.failed:
            raise RuntimeError(f"Command failed: {response.channel_input}")
        return str(response.result)

    def send_config(self, configs: list[str]) -> str:
        """Send configuration commands and return output."""
        if not self._connected:
            self.open()
        response = self._driver.send_configs(configs)
        if response.failed:
            raise RuntimeError(f"Config commands failed: {response.result}")
        return str(response.result)

    def close(self) -> None:
        """Close the SSH connection."""
        if self._connected:
            self._driver.close()
            self._connected = False

    @property
    def is_alive(self) -> bool:
        """Return True if the SSH session is connected."""
        return self._connected


class JunosDriver:
    """Juniper JunOS driver using NETCONF (primary) with SSH fallback.

    Satisfies the NetworkDriver protocol defined in drivers/base.py.

    Uses NETCONF RPCs for structured data retrieval and configuration
    management. When NETCONF is unavailable (port 830 blocked), falls
    back to SSH CLI via scrapli with text-based output parsing.
    """

    platform: str = "junos"
    vendor: str = "juniper"

    VENDOR_API_VERSION = "netconf-1.0"

    def __init__(self) -> None:
        self._conn: Any = None  # NetconfDriver when scrapli-netconf installed
        self._ssh_transport: JunosScrapliTransport | None = None
        self._host: str | None = None
        self._username: str | None = None
        self._password: SecretStr | None = None
        self._port: int = 830
        self._ssh_port: int = 22
        self._ssh_private_key: str = ""
        self._ssh_strict_key: bool = True
        self._ssh_known_hosts: str = "~/.ssh/known_hosts"
        self._transport: str = "netconf"  # "netconf" or "ssh"
        self._connected: bool = False
        self._last_command_time: float = 0.0
        self._timeout_connect: float = 10.0
        self._timeout_show: float = 30.0
        self._timeout_config: float = 60.0
        self._keepalive_interval: int = _DEFAULT_KEEPALIVE_INTERVAL
        self._connect_transport: str = "netconf"
        self._connect_kwargs: dict[str, Any] = {}

    # --- Connection lifecycle ---

    def connect(  # noqa: PLR0913
        self,
        host: str,
        transport: str = "netconf",
        username: str = "admin",
        password: str = "",
        **kwargs: Any,
    ) -> None:
        """Establish connection to a JunOS device.

        Tries NETCONF first (if transport='netconf'). On failure,
        falls back to SSH automatically via scrapli.

        Args:
            host: Device hostname or IP address.
            transport: Transport method ('netconf' or 'ssh').
            username: Device username.
            password: Device password.
            **kwargs: Additional options (port, timeout, strict_key,
                      ssh_port, auth_private_key, ssh_strict_key,
                      ssh_known_hosts_file, timeout_connect, timeout_show,
                      timeout_config).
        """
        self._timeout_connect = kwargs.pop("timeout_connect", 10.0)
        self._timeout_show = kwargs.pop("timeout_show", 30.0)
        self._timeout_config = kwargs.pop("timeout_config", 60.0)
        self._keepalive_interval = kwargs.pop("keepalive_interval", _DEFAULT_KEEPALIVE_INTERVAL)
        self._connect_transport = transport
        self._connect_kwargs = kwargs.copy()

        self._host = host
        self._username = username
        self._password = SecretStr(password) if password else None
        self._ssh_port = kwargs.get("ssh_port", 22)
        self._ssh_private_key = kwargs.get("auth_private_key", "")
        self._ssh_strict_key = kwargs.get("ssh_strict_key", True)
        self._ssh_known_hosts = kwargs.get("ssh_known_hosts_file", "~/.ssh/known_hosts")

        if not HAS_SCRAPLI_NETCONF and transport == "netconf":
            logger.warning("scrapli-netconf not installed, falling back to SSH for %s", host)
            transport = "ssh"

        self._connect_with_retry(host, transport, username, password, **kwargs)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def _connect_with_retry(self, host: str, transport: str, username: str, password: str, **kwargs: Any) -> None:
        """Internal connect with tenacity retry."""
        if transport == "netconf":
            self._init_netconf(host, username, password, **kwargs)
        else:
            self._init_ssh_fallback(host, username, password, **kwargs)
        self._last_command_time = time.monotonic()

    def _init_netconf(self, host: str, username: str, password: str, **kwargs: Any) -> None:
        """Initialize NETCONF transport with availability probe."""
        self._port = kwargs.get("port", 830)
        strict_key = kwargs.get("strict_key", self._ssh_strict_key)

        self._conn = NetconfDriver(
            host=host,
            auth_username=username,
            auth_password=password,
            port=self._port,
            auth_strict_key=strict_key,
            transport="system",
            timeout_socket=self._timeout_connect,
            timeout_transport=self._timeout_connect,
            timeout_ops=self._timeout_show,
        )

        try:
            self._conn.open()
            self._transport = "netconf"
            self._connected = True
            logger.debug("NETCONF connected to %s:%s", host, self._port)
        except Exception as exc:
            # Do not fallback to SSH for authentication failures
            if HAS_SCRAPLI and ScrapliAuthenticationFailed and isinstance(exc, ScrapliAuthenticationFailed):
                logger.error("NETCONF authentication failed on %s: %s", host, exc)
                self._conn = None
                raise ConnectionError(
                    f"Authentication failed for {host}. Check credentials (NET_USERNAME/NET_PASSWORD)."
                ) from exc
            logger.warning("NETCONF connection to %s failed: %s, falling back to SSH", host, exc)
            self._conn = None
            self._init_ssh_fallback(host, username, password, **kwargs)

    def _init_ssh_fallback(self, host: str, username: str, password: str, **kwargs: Any) -> None:
        """Initialize SSH fallback transport using scrapli.

        Creates a persistent SSH session with proper host key verification.
        The session is reused across commands for efficiency.
        """
        self._transport = "ssh"
        self._ssh_port = kwargs.get("ssh_port", kwargs.get("port", 22))

        if HAS_SCRAPLI:
            try:
                self._ssh_transport = JunosScrapliTransport(
                    host=host,
                    username=username,
                    password=password,
                    port=self._ssh_port,
                    auth_private_key=self._ssh_private_key,
                    auth_strict_key=self._ssh_strict_key,
                    ssh_known_hosts_file=self._ssh_known_hosts,
                    timeout_connect=self._timeout_connect,
                    timeout_ops=self._timeout_show,
                )
                logger.debug("Scrapli SSH transport initialized for %s:%s", host, self._ssh_port)
            except Exception as exc:
                logger.warning("Failed to initialize scrapli SSH for %s: %s", host, exc)
                self._ssh_transport = None
        else:
            logger.warning(
                "scrapli not installed — SSH fallback unavailable for %s. "
                "Install with: pip install network-mcp[juniper]",
                host,
            )
            self._ssh_transport = None

        self._connected = True

    def close(self) -> None:
        """Close all transport connections."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:
                logger.debug("Failed to close NETCONF session: %s", exc)
            finally:
                self._conn = None
        if self._ssh_transport is not None:
            try:
                self._ssh_transport.close()
            except Exception as exc:
                logger.debug("Failed to close SSH transport: %s", exc)
            finally:
                self._ssh_transport = None
        self._connected = False

    def is_connected(self) -> bool:
        """Return True if the driver has an active connection."""
        return self._connected

    def capabilities(self) -> dict[str, bool]:
        """Query supported features and getter availability for JunOS."""
        return {
            "config_sessions": True,
            "gnmi": False,
            "restconf": False,
            "netconf": True,
            "ssh": True,
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
            "ping": True,
            "traceroute": True,
            "get_interface_flaps": True,
        }

    def reconnect(self) -> None:
        """Force reconnection to the device."""
        if self._host is None:
            raise ConnectionError("Cannot reconnect: no previous connection parameters.")
        self.close()
        transport = self._connect_transport
        if not HAS_SCRAPLI_NETCONF and transport == "netconf":
            transport = "ssh"
        self._connect_with_retry(
            self._host,
            transport,
            self._username or "admin",
            self._password.get_secret_value() if isinstance(self._password, SecretStr) else (self._password or ""),
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
            if self._conn:
                self._conn.rpc(filter_="<get-system-uptime-information/>")
            elif self._ssh_transport and self._ssh_transport.is_alive:
                self._ssh_transport.send_command("show system uptime")
            else:
                raise ConnectionError("No active transport")
        except Exception:
            logger.info("JunOS keep-alive failed for %s, reconnecting", self._host)
            self.reconnect()

    # --- Raw command execution ---

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands via NETCONF or SSH.

        For NETCONF transport, wraps each command in ``<command>`` RPC.
        For SSH transport, executes commands via SSH CLI.

        Args:
            commands: List of show commands to execute.
            encoding: Output encoding ('json' or 'text').

        Returns:
            List of response dicts, one per command.
        """
        if not self._connected:
            raise ConnectionError("JunosDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        if self._conn:
            return self._netconf_show(commands, encoding)
        return self._ssh_show(commands)

    def _netconf_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands via NETCONF CLI-over-NETCONF."""
        results: list[dict] = []
        for cmd in commands:
            try:
                if encoding == "json":
                    rpc = f'<command format="json">{cmd}</command>'
                else:
                    rpc = f"<command>{cmd}</command>"
                response = self._conn.rpc(filter_=rpc)
                if hasattr(response, "result"):
                    results.append({"output": response.result})
                else:
                    results.append({"output": str(response)})
            except Exception as exc:
                logger.warning("NETCONF command '%s' failed: %s", cmd, exc)
                results.append({"error": str(exc)})
        return results

    def _ssh_show(self, commands: list[str]) -> list[dict]:
        """Execute show commands via SSH."""
        results: list[dict] = []
        for cmd in commands:
            try:
                output = self._ssh_exec(cmd)
                results.append({"output": output})
            except Exception as exc:
                results.append({"error": str(exc)})
        return results

    def _ssh_exec(self, command: str) -> str:
        """Execute a single show command via scrapli SSH."""
        if not self._host or not self._username:
            raise ConnectionError("SSH not configured. Call connect() first.")
        if self._ssh_transport is None:
            raise ConnectionError("SSH transport not available. Install scrapli: pip install network-mcp[juniper]")

        try:
            return self._ssh_transport.send_command(command)
        except Exception as exc:
            if HAS_SCRAPLI and ScrapliTimeout is not None and isinstance(exc, ScrapliTimeout):
                raise TimeoutError(f"SSH command timed out: {command}") from exc
            raise RuntimeError(f"SSH command failed: {exc}") from exc

    def run_config(self, commands: list[str]) -> list[str]:
        """Apply configuration via NETCONF or SSH.

        NETCONF uses edit-config with candidate/commit model.
        SSH uses scrapli send_configs.

        Args:
            commands: List of configuration commands (set-style or XML).

        Returns:
            List of result strings.
        """
        if not self._connected:
            raise ConnectionError("JunosDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        if self._conn:
            return self._netconf_config(commands)
        return self._ssh_config(commands)

    def _netconf_config(self, commands: list[str]) -> list[str]:
        """Apply configuration via NETCONF edit-config with candidate/commit."""
        results: list[str] = []
        locked = False
        committed = False
        edit_failed = False
        try:
            self._conn.lock(target="candidate")
            locked = True

            for cmd in commands:
                try:
                    config_xml = f"<config><configuration>{cmd}</configuration></config>"
                    self._conn.edit_config(config=config_xml, target="candidate")
                    results.append(f"Config applied: {cmd}")
                except Exception as exc:
                    results.append(f"Error applying '{cmd}': {exc}")
                    edit_failed = True
                    break

            if edit_failed:
                results.append("Config transaction aborted due to edit failure")
            else:
                self._conn.commit()
                committed = True
        except Exception as exc:
            results.append(f"Config transaction error: {exc}")
        finally:
            if locked:
                try:
                    if not committed:
                        self._conn.discard()
                except Exception:
                    logger.debug("Failed to discard candidate config", exc_info=True)
                try:
                    self._conn.unlock(target="candidate")
                except Exception:
                    logger.debug("Failed to unlock candidate config", exc_info=True)

        return results

    def _ssh_config(self, commands: list[str]) -> list[str]:
        """Execute config commands via SSH (scrapli)."""
        if self._ssh_transport is None:
            raise ConnectionError("SSH transport not available. Install scrapli: pip install network-mcp[juniper]")
        try:
            output = self._ssh_transport.send_config(commands)
            return [output]
        except Exception as exc:
            return [f"Error: {exc}"]

    # Backward-compatibility aliases
    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility."""
        return self.run_show(commands, encoding=encoding)

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility."""
        return self.run_config(commands)

    # --- Normalized getters (NetworkDriver protocol) ---

    def get_facts(self) -> dict:
        """Get device facts via NETCONF RPC or SSH CLI.

        Returns:
            dict with keys: hostname, model, version, serial,
            uptime_seconds, vendor, platform
        """
        if self._conn:
            try:
                response = self._conn.rpc(filter_="<get-system-information/>")
                return self._normalize_facts(response.result, host=self._host or "unknown")
            except Exception as exc:
                logger.warning("get_facts NETCONF RPC failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show version"])
        return _parse_facts_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_facts(xml_str: str, host: str = "unknown") -> dict:
        """Parse ``<get-system-information/>`` XML response to facts schema."""
        try:
            root = ET.fromstring(xml_str)  # noqa: S314
        except ET.ParseError as exc:
            logger.debug("XML parse error in get_facts response from %s: %s", host, exc)
            return {
                "hostname": "",
                "model": "",
                "version": "",
                "serial": "",
                "uptime_seconds": 0,
                "vendor": "juniper",
                "platform": "junos",
            }

        # Navigate to system-information element
        sys_info = _xml_find(root, "system-information")
        if sys_info is None:
            logger.debug("XML element 'system-information' not found in get_facts response from %s", host)
            sys_info = root

        hostname = _xml_find_text(sys_info, "host-name")
        if not hostname:
            logger.debug("XML element 'host-name' not found in get_facts response from %s", host)
        model = _xml_find_text(sys_info, "hardware-model")
        if not model:
            logger.debug("XML element 'hardware-model' not found in get_facts response from %s", host)
        version = _xml_find_text(sys_info, "os-version")
        if not version:
            version = _xml_find_text(sys_info, "junos-version")
        if not version:
            logger.debug("XML element 'os-version'/'junos-version' not found in get_facts response from %s", host)
        serial = _xml_find_text(sys_info, "serial-number")

        # Uptime: JunOS provides seconds since boot in current-time or
        # via separate system-uptime-information RPC
        uptime_str = _xml_find_text(sys_info, "up-time")
        uptime_seconds = _parse_junos_uptime(uptime_str)

        return {
            "hostname": hostname,
            "model": model,
            "version": version,
            "serial": serial,
            "uptime_seconds": uptime_seconds,
            "vendor": "juniper",
            "platform": "junos",
        }

    def get_interfaces(self) -> dict[str, dict]:
        """Get interfaces via NETCONF RPC or SSH CLI.

        Returns:
            dict mapping interface name to normalized interface dict.
        """
        if self._conn:
            try:
                response = self._conn.rpc(filter_="<get-interface-information/>")
                return self._normalize_interfaces(response.result, host=self._host or "unknown")
            except Exception as exc:
                logger.warning("get_interfaces NETCONF RPC failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show interfaces terse"])
        return _parse_interfaces_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_interfaces(xml_str: str, host: str = "unknown") -> dict[str, dict]:
        """Parse ``<get-interface-information/>`` XML to interfaces schema."""
        try:
            root = ET.fromstring(xml_str)  # noqa: S314
        except ET.ParseError as exc:
            logger.debug("XML parse error in get_interfaces response from %s: %s", host, exc)
            return {}

        normalized: dict[str, dict] = {}

        # Find interface-information container
        intf_info = _xml_find(root, "interface-information")
        if intf_info is None:
            logger.debug("XML element 'interface-information' not found in get_interfaces response from %s", host)
            intf_info = root

        for phy_intf in _xml_findall(intf_info, "physical-interface"):
            name = _xml_find_text(phy_intf, "name")
            if not name:
                continue

            oper_status = _xml_find_text(phy_intf, "oper-status", "down")
            admin_status = _xml_find_text(phy_intf, "admin-status", "down")
            description = _xml_find_text(phy_intf, "description")
            speed_str = _xml_find_text(phy_intf, "speed")
            speed = _parse_junos_speed(speed_str)
            mtu_str = _xml_find_text(phy_intf, "mtu")
            try:
                mtu = int(mtu_str) if mtu_str else 1500
            except ValueError:
                # JunOS may return "Unlimited" for some interfaces
                mtu = 1500

            mac = _normalize_mac(_xml_find_text(phy_intf, "current-physical-address"))
            if not mac:
                mac = _normalize_mac(_xml_find_text(phy_intf, "hardware-physical-address"))

            normalized[name] = {
                "is_up": oper_status.lower() == "up",
                "is_enabled": admin_status.lower() == "up",
                "description": description,
                "speed": speed,
                "mtu": mtu,
                "mac_address": mac,
            }

        return normalized

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Get BGP summary via NETCONF RPC or SSH CLI.

        Args:
            vrf: VRF name (JunOS routing-instance). 'default' = global table.

        Returns:
            dict with keys: router_id, as_number, peers
        """
        if self._conn:
            try:
                if vrf != "default":
                    rpc = f"<get-bgp-summary-information><instance>{vrf}</instance></get-bgp-summary-information>"
                else:
                    rpc = "<get-bgp-summary-information/>"
                response = self._conn.rpc(filter_=rpc)
                return self._normalize_bgp_summary(response.result, host=self._host or "unknown")
            except Exception as exc:
                logger.warning("get_bgp_summary NETCONF RPC failed: %s, trying SSH", exc)

        # SSH fallback
        cmd = f"show bgp summary instance {vrf}" if vrf != "default" else "show bgp summary"
        result = self._ssh_show([cmd])
        return _parse_bgp_summary_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_bgp_summary(xml_str: str, host: str = "unknown") -> dict:
        """Parse ``<get-bgp-summary-information/>`` XML to BGP summary schema."""
        try:
            root = ET.fromstring(xml_str)  # noqa: S314
        except ET.ParseError as exc:
            logger.debug("XML parse error in get_bgp_summary response from %s: %s", host, exc)
            return {"router_id": "", "as_number": 0, "peers": {}}

        bgp_info = _xml_find(root, "bgp-information")
        if bgp_info is None:
            logger.debug("XML element 'bgp-information' not found in get_bgp_summary response from %s", host)
            bgp_info = root

        router_id = _xml_find_text(bgp_info, "local-id")
        if not router_id:
            router_id = _xml_find_text(bgp_info, "bgp-rib-state")

        as_str = _xml_find_text(bgp_info, "local-as")
        try:
            as_number = int(as_str) if as_str else 0
        except ValueError:
            as_number = 0

        peers: dict[str, dict] = {}
        for peer_elem in _xml_findall(bgp_info, "bgp-peer"):
            peer_ip = _xml_find_text(peer_elem, "peer-address")
            if not peer_ip:
                continue
            # Strip port suffix if present (e.g., "10.0.0.2+179")
            peer_ip = peer_ip.split("+")[0]

            state = _xml_find_text(peer_elem, "peer-state", "Unknown")
            pfx_str = _xml_find_text(peer_elem, "peer-restart-nlri-configured")
            if not pfx_str:
                # Try received prefix count from rib-table
                rib = _xml_find(peer_elem, "bgp-rib")
                if rib is not None:
                    pfx_str = _xml_find_text(rib, "received-prefix-count")
                    if not pfx_str:
                        pfx_str = _xml_find_text(rib, "active-prefix-count")
            try:
                prefixes_received = int(pfx_str) if pfx_str else 0
            except ValueError:
                prefixes_received = 0

            uptime_str = _xml_find_text(peer_elem, "elapsed-time")
            uptime = _parse_junos_uptime(uptime_str)

            peers[peer_ip] = {
                "state": state,
                "prefixes_received": prefixes_received,
                "uptime": uptime,
            }

        return {
            "router_id": router_id,
            "as_number": as_number,
            "peers": peers,
        }

    def get_arp_table(self) -> list[dict]:
        """Get ARP table via NETCONF RPC or SSH CLI.

        Returns:
            list of dicts with keys: ip, mac, interface, age
        """
        if self._conn:
            try:
                response = self._conn.rpc(filter_="<get-arp-table-information/>")
                return self._normalize_arp_table(response.result, host=self._host or "unknown")
            except Exception as exc:
                logger.warning("get_arp_table NETCONF RPC failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show arp no-resolve"])
        return _parse_arp_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_arp_table(xml_str: str, host: str = "unknown") -> list[dict]:
        """Parse ``<get-arp-table-information/>`` XML to ARP table schema."""
        try:
            root = ET.fromstring(xml_str)  # noqa: S314
        except ET.ParseError as exc:
            logger.debug("XML parse error in get_arp_table response from %s: %s", host, exc)
            return []

        entries: list[dict] = []

        arp_info = _xml_find(root, "arp-table-information")
        if arp_info is None:
            logger.debug("XML element 'arp-table-information' not found in get_arp_table response from %s", host)
            arp_info = root

        for arp_entry in _xml_findall(arp_info, "arp-table-entry"):
            ip_addr = _xml_find_text(arp_entry, "ip-address")
            if not ip_addr:
                continue

            mac = _normalize_mac(_xml_find_text(arp_entry, "mac-address"))
            interface = _xml_find_text(arp_entry, "interface-name")

            age_str = _xml_find_text(arp_entry, "timer")
            try:
                age = float(age_str) if age_str else -1.0
            except ValueError:
                age = -1.0

            entries.append(
                {
                    "ip": ip_addr,
                    "mac": mac,
                    "interface": interface,
                    "age": age,
                }
            )

        return entries

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Get LLDP neighbors via NETCONF RPC or SSH CLI.

        Returns:
            dict mapping local interface name to list of neighbor dicts.
        """
        if self._conn:
            try:
                response = self._conn.rpc(filter_="<get-lldp-neighbors-information/>")
                return self._normalize_lldp_neighbors(response.result, host=self._host or "unknown")
            except Exception as exc:
                logger.warning("get_lldp_neighbors NETCONF RPC failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show lldp neighbors"])
        return _parse_lldp_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_lldp_neighbors(xml_str: str, host: str = "unknown") -> dict[str, list[dict]]:
        """Parse ``<get-lldp-neighbors-information/>`` XML to LLDP schema."""
        try:
            root = ET.fromstring(xml_str)  # noqa: S314
        except ET.ParseError as exc:
            logger.debug("XML parse error in get_lldp_neighbors response from %s: %s", host, exc)
            return {}

        normalized: dict[str, list[dict]] = {}

        lldp_info = _xml_find(root, "lldp-neighbors-information")
        if lldp_info is None:
            logger.debug(
                "XML element 'lldp-neighbors-information' not found in get_lldp_neighbors response from %s",
                host,
            )
            lldp_info = root

        for neighbor in _xml_findall(lldp_info, "lldp-neighbor-information"):
            local_intf = _xml_find_text(neighbor, "lldp-local-port-id")
            if not local_intf:
                local_intf = _xml_find_text(neighbor, "lldp-local-interface")
            if not local_intf:
                continue

            hostname = _xml_find_text(neighbor, "lldp-remote-system-name")
            port = _xml_find_text(neighbor, "lldp-remote-port-id")
            if not port:
                port = _xml_find_text(neighbor, "lldp-remote-port-description")
            sys_desc = _xml_find_text(neighbor, "lldp-remote-system-description")

            normalized.setdefault(local_intf, []).append(
                {
                    "hostname": hostname,
                    "port": port,
                    "system_description": sys_desc,
                }
            )

        return normalized

    def get_lldp_neighbor_detail(self, interface: str = "") -> dict[str, list[dict]]:
        """Get detailed LLDP neighbor TLV data via NETCONF or SSH.

        Uses ``<get-lldp-neighbors-information/>`` and extracts extended
        TLV fields (chassis ID, management address, capabilities).

        Args:
            interface: Filter by local interface name (empty = all).

        Returns:
            dict mapping local interface name to list of neighbor detail dicts.
        """
        if self._conn:
            try:
                response = self._conn.rpc(filter_="<get-lldp-neighbors-information/>")
                return self._normalize_lldp_detail(response.result, interface, host=self._host or "unknown")
            except Exception as exc:
                logger.warning("get_lldp_neighbor_detail NETCONF RPC failed: %s, trying SSH", exc)

        result = self._ssh_show(["show lldp neighbors"])
        return self._normalize_lldp_detail_ssh(result[0].get("output", ""), interface)

    @staticmethod
    def _normalize_lldp_detail(xml_str: str, intf_filter: str, host: str = "unknown") -> dict[str, list[dict]]:
        """Parse ``<get-lldp-neighbors-information/>`` XML with extended TLV fields."""
        try:
            root = ET.fromstring(xml_str)  # noqa: S314
        except ET.ParseError as exc:
            logger.debug("XML parse error in get_lldp_neighbor_detail response from %s: %s", host, exc)
            return {}

        normalized: dict[str, list[dict]] = {}

        lldp_info = _xml_find(root, "lldp-neighbors-information")
        if lldp_info is None:
            logger.debug(
                "XML element 'lldp-neighbors-information' not found in get_lldp_neighbor_detail response from %s",
                host,
            )
            lldp_info = root

        for neighbor in _xml_findall(lldp_info, "lldp-neighbor-information"):
            local_intf = _xml_find_text(neighbor, "lldp-local-port-id")
            if not local_intf:
                local_intf = _xml_find_text(neighbor, "lldp-local-interface")
            if not local_intf:
                continue
            if intf_filter and local_intf != intf_filter:
                continue

            hostname = _xml_find_text(neighbor, "lldp-remote-system-name")
            port = _xml_find_text(neighbor, "lldp-remote-port-id")
            if not port:
                port = _xml_find_text(neighbor, "lldp-remote-port-description")
            sys_desc = _xml_find_text(neighbor, "lldp-remote-system-description")
            chassis_id = _xml_find_text(neighbor, "lldp-remote-chassis-id")
            mgmt_addr = _xml_find_text(neighbor, "lldp-remote-management-address")

            # Parse capabilities
            caps: list[str] = []
            caps_elem = _xml_find(neighbor, "lldp-remote-system-capabilities-supported")
            if caps_elem is not None and caps_elem.text:
                caps = [c.strip().lower() for c in caps_elem.text.split(",") if c.strip()]

            normalized.setdefault(local_intf, []).append(
                {
                    "hostname": hostname,
                    "port": port,
                    "system_description": sys_desc,
                    "chassis_id": chassis_id,
                    "management_address": mgmt_addr,
                    "capabilities": caps,
                }
            )

        return normalized

    @staticmethod
    def _normalize_lldp_detail_ssh(output: str, intf_filter: str) -> dict[str, list[dict]]:
        """Parse 'show lldp neighbors' text output with extended TLV fields."""
        normalized: dict[str, list[dict]] = {}
        local_intf = ""
        hostname = ""
        port = ""
        sys_desc = ""
        chassis_id = ""
        mgmt_addr = ""
        capabilities: list[str] = []

        for line in output.splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("Local Interface:") or line_stripped.startswith("Local Port ID:"):
                if local_intf and hostname:
                    if not intf_filter or local_intf == intf_filter:
                        normalized.setdefault(local_intf, []).append(
                            {
                                "hostname": hostname,
                                "port": port,
                                "system_description": sys_desc,
                                "chassis_id": chassis_id,
                                "management_address": mgmt_addr,
                                "capabilities": capabilities,
                            }
                        )
                local_intf = line_stripped.split(":", 1)[1].strip()
                hostname = ""
                port = ""
                sys_desc = ""
                chassis_id = ""
                mgmt_addr = ""
                capabilities = []
            elif line_stripped.startswith("System Name:"):
                hostname = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("Port ID:"):
                port = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("System Description:"):
                sys_desc = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("Chassis ID:"):
                chassis_id = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("Management Address:"):
                mgmt_addr = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("System Capabilities:"):
                raw_caps = line_stripped.split(":", 1)[1].strip()
                capabilities = [c.strip().lower() for c in raw_caps.split(",") if c.strip()]

        if local_intf and hostname:
            if not intf_filter or local_intf == intf_filter:
                normalized.setdefault(local_intf, []).append(
                    {
                        "hostname": hostname,
                        "port": port,
                        "system_description": sys_desc,
                        "chassis_id": chassis_id,
                        "management_address": mgmt_addr,
                        "capabilities": capabilities,
                    }
                )

        return normalized

    def get_config(self, sanitized: bool = True) -> dict:
        """Get device configuration via NETCONF or SSH.

        JunOS uses candidate/running config model. Startup config is the
        same as running on JunOS (committed config persists across reboots).

        Args:
            sanitized: If True, redact passwords and secrets from output.

        Returns:
            dict with keys: running, startup
        """
        running = ""

        if self._conn:
            try:
                response = self._conn.get_config(source="running")
                running = response.result if hasattr(response, "result") else str(response)
            except Exception as exc:
                logger.warning("get_config NETCONF RPC failed: %s, trying SSH", exc)

        if not running:
            # SSH fallback
            result = self._ssh_show(["show configuration"])
            running = result[0].get("output", "") if result else ""

        # JunOS committed config = startup config
        startup = running

        if sanitized and running:
            from network_mcp.sanitizer import config_sanitizer

            running = config_sanitizer.sanitize(running)
            startup = config_sanitizer.sanitize(startup)

        return {"running": running, "startup": startup}

    # --- Expanded normalized getters (20 new) ---

    def _netconf_rpc(self, rpc_filter: str) -> str:
        """Execute a NETCONF RPC and return the XML result string."""
        if not self._conn:
            raise ConnectionError("NETCONF transport not available")
        response = self._conn.rpc(filter_=rpc_filter)
        return response.result if hasattr(response, "result") else str(response)

    def get_vlans(self) -> list[dict]:
        """Get all VLANs. Transport: NETCONF ``<get-vlan-information/>``."""
        try:
            xml_str = self._netconf_rpc("<get-vlan-information/>")
            return self._normalize_vlans(xml_str)
        except Exception as exc:
            logger.warning("get_vlans failed: %s", exc)
            return []

    @staticmethod
    def _normalize_vlans(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_vlans")
        if root is None:
            return []
        vlan_info = _xml_find(root, "vlan-information")
        if vlan_info is None:
            vlan_info = root
        results: list[dict] = []
        for vlan_elem in _xml_findall(vlan_info, "vlan"):
            tag = _xml_find_text(vlan_elem, "vlan-tag")
            if not tag:
                tag = _xml_find_text(vlan_elem, "vlan-id")
            try:
                vlan_id = int(tag)
            except (ValueError, TypeError):
                continue
            name = _xml_find_text(vlan_elem, "vlan-name")
            status_raw = _xml_find_text(vlan_elem, "vlan-status", "active")
            status = "active" if "active" in status_raw.lower() else "suspend"
            interfaces: list[str] = []
            members = _xml_find(vlan_elem, "vlan-detail")
            if members is not None:
                for member in _xml_findall(members, "vlan-member-interface"):
                    intf_name = _xml_find_text(member, "vlan-member-interface-name")
                    if intf_name:
                        interfaces.append(intf_name)
            results.append({"vlan_id": vlan_id, "name": name, "status": status, "interfaces": interfaces})
        return results

    def get_vlan_detail(self, vlan_id: int) -> dict:
        """Get detail for a single VLAN. Transport: NETCONF ``<get-vlan-information>``."""
        try:
            rpc = f"<get-vlan-information><vlan-name>{vlan_id}</vlan-name></get-vlan-information>"
            xml_str = self._netconf_rpc(rpc)
            vlans = self._normalize_vlans(xml_str)
            if vlans:
                vlan = vlans[0]
                vlan["type"] = "static"
                return vlan
            return {"vlan_id": vlan_id, "name": "", "status": "", "interfaces": [], "type": ""}
        except Exception as exc:
            logger.warning("get_vlan_detail(%s) failed: %s", vlan_id, exc)
            return {"vlan_id": vlan_id, "name": "", "status": "", "interfaces": [], "type": ""}

    def get_interface_counters(self) -> list[dict]:
        """Get traffic counters. Transport: NETCONF ``<get-interface-information>``."""
        try:
            xml_str = self._netconf_rpc("<get-interface-information><statistics/></get-interface-information>")
            return self._normalize_interface_counters(xml_str)
        except Exception as exc:
            logger.warning("get_interface_counters failed: %s", exc)
            return []

    @staticmethod
    def _normalize_interface_counters(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_interface_counters")
        if root is None:
            return []
        intf_info = _xml_find(root, "interface-information")
        if intf_info is None:
            intf_info = root
        results: list[dict] = []
        for phy in _xml_findall(intf_info, "physical-interface"):
            name = _xml_find_text(phy, "name")
            if not name:
                continue
            traffic = _xml_find(phy, "traffic-statistics")
            if traffic is None:
                traffic = phy
            results.append(
                {
                    "interface": name,
                    "in_octets": int(_xml_find_text(traffic, "input-bytes", "0")),
                    "out_octets": int(_xml_find_text(traffic, "output-bytes", "0")),
                    "in_packets": int(_xml_find_text(traffic, "input-packets", "0")),
                    "out_packets": int(_xml_find_text(traffic, "output-packets", "0")),
                    "in_broadcast": int(_xml_find_text(traffic, "input-broadcast-packets", "0")),
                    "out_broadcast": int(_xml_find_text(traffic, "output-broadcast-packets", "0")),
                    "in_multicast": int(_xml_find_text(traffic, "input-multicast-packets", "0")),
                    "out_multicast": int(_xml_find_text(traffic, "output-multicast-packets", "0")),
                }
            )
        return results

    def get_interface_errors(self) -> list[dict]:
        """Get error counters. Transport: NETCONF ``<get-interface-information>``."""
        try:
            xml_str = self._netconf_rpc("<get-interface-information><statistics/></get-interface-information>")
            return self._normalize_interface_errors(xml_str)
        except Exception as exc:
            logger.warning("get_interface_errors failed: %s", exc)
            return []

    @staticmethod
    def _normalize_interface_errors(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_interface_errors")
        if root is None:
            return []
        intf_info = _xml_find(root, "interface-information")
        if intf_info is None:
            intf_info = root
        results: list[dict] = []
        for phy in _xml_findall(intf_info, "physical-interface"):
            name = _xml_find_text(phy, "name")
            if not name:
                continue
            in_err_list = _xml_find(phy, "input-error-list")
            out_err_list = _xml_find(phy, "output-error-list")
            in_errors = 0
            in_crc = 0
            in_frame = 0
            in_runts = 0
            in_giants = 0
            if in_err_list is not None:
                in_errors = int(_xml_find_text(in_err_list, "input-errors", "0"))
                in_crc = int(_xml_find_text(in_err_list, "input-crc-errors", "0"))
                if not in_crc:
                    in_crc = int(_xml_find_text(in_err_list, "input-fcs-errors", "0"))
                in_frame = int(_xml_find_text(in_err_list, "framing-errors", "0"))
                in_runts = int(_xml_find_text(in_err_list, "input-runts", "0"))
                in_giants = int(_xml_find_text(in_err_list, "input-giants", "0"))
            out_errors = 0
            if out_err_list is not None:
                out_errors = int(_xml_find_text(out_err_list, "output-errors", "0"))
            results.append(
                {
                    "interface": name,
                    "in_errors": in_errors,
                    "out_errors": out_errors,
                    "in_crc": in_crc,
                    "in_frame": in_frame,
                    "in_runts": in_runts,
                    "in_giants": in_giants,
                }
            )
        return results

    def get_ip_interfaces(self) -> list[dict]:
        """Get L3 interfaces. Transport: NETCONF ``<get-interface-information><terse/>``.

        Filters for interfaces with inet (IPv4) addresses.
        """
        try:
            xml_str = self._netconf_rpc("<get-interface-information><terse/></get-interface-information>")
            return self._normalize_ip_interfaces(xml_str)
        except Exception as exc:
            logger.warning("get_ip_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _normalize_ip_interfaces(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_ip_interfaces")
        if root is None:
            return []
        intf_info = _xml_find(root, "interface-information")
        if intf_info is None:
            intf_info = root
        results: list[dict] = []
        for logical in _xml_findall(intf_info, "logical-interface"):
            name = _xml_find_text(logical, "name")
            if not name:
                continue
            oper_status = _xml_find_text(logical, "oper-status", "down")
            # Look for address-family inet
            for af in _xml_findall(logical, "address-family"):
                af_name = _xml_find_text(af, "address-family-name")
                if af_name != "inet":
                    continue
                addr_elem = _xml_find(af, "interface-address")
                ip_address = ""
                subnet = ""
                if addr_elem is not None:
                    addr_text = _xml_find_text(addr_elem, "ifa-local")
                    if "/" in addr_text:
                        parts = addr_text.split("/")
                        ip_address = parts[0]
                        subnet = parts[1]
                    else:
                        ip_address = addr_text
                results.append(
                    {
                        "interface": name,
                        "ip_address": ip_address,
                        "subnet": subnet,
                        "vrf": "",
                        "status": "up" if oper_status.lower() == "up" else "down",
                    }
                )
                break  # One entry per logical interface
        return results

    def get_transceiver_info(self) -> list[dict]:
        """Get transceiver DOM data. Transport: NETCONF ``<get-interface-optics-diagnostics-information/>``."""
        try:
            xml_str = self._netconf_rpc("<get-interface-optics-diagnostics-information/>")
            return self._normalize_transceiver(xml_str)
        except Exception as exc:
            logger.warning("get_transceiver_info failed: %s", exc)
            return []

    @staticmethod
    def _normalize_transceiver(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_transceiver_info")
        if root is None:
            return []
        optics_info = _xml_find(root, "interface-optics-diagnostics-information")
        if optics_info is None:
            optics_info = root
        results: list[dict] = []
        for phy in _xml_findall(optics_info, "physical-interface"):
            name = _xml_find_text(phy, "name")
            if not name:
                continue
            diag = _xml_find(phy, "optics-diagnostics")
            if diag is None:
                diag = phy
            optic_type = _xml_find_text(diag, "optic-type", _xml_find_text(phy, "optic-type"))
            tx_str = _xml_find_text(diag, "laser-output-power-dbm", "0.0")
            rx_str = _xml_find_text(diag, "rx-signal-avg-optical-power-dbm", "0.0")
            if not rx_str or rx_str == "0.0":
                rx_str = _xml_find_text(diag, "laser-rx-optical-power-dbm", "0.0")
            temp_str = _xml_find_text(diag, "module-temperature-celsius", "0.0")
            if not temp_str or temp_str == "0.0":
                temp_str = _xml_find_text(diag, "module-temperature", "0.0")
            try:
                tx_power = float(tx_str)
            except ValueError:
                tx_power = 0.0
            try:
                rx_power = float(rx_str)
            except ValueError:
                rx_power = 0.0
            try:
                temp = float(temp_str)
            except ValueError:
                temp = 0.0
            results.append(
                {
                    "interface": name,
                    "type": optic_type,
                    "tx_power": tx_power,
                    "rx_power": rx_power,
                    "temp": temp,
                }
            )
        return results

    def get_ospf_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get OSPF neighbors. Transport: NETCONF ``<get-ospf-neighbor-information/>``."""
        try:
            if vrf != "default":
                rpc = f"<get-ospf-neighbor-information><instance>{vrf}</instance></get-ospf-neighbor-information>"
            else:
                rpc = "<get-ospf-neighbor-information/>"
            xml_str = self._netconf_rpc(rpc)
            return self._normalize_ospf_neighbors(xml_str)
        except Exception as exc:
            logger.warning("get_ospf_neighbors failed: %s", exc)
            return []

    @staticmethod
    def _normalize_ospf_neighbors(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_ospf_neighbors")
        if root is None:
            return []
        ospf_info = _xml_find(root, "ospf-neighbor-information")
        if ospf_info is None:
            ospf_info = root
        results: list[dict] = []
        for nbr in _xml_findall(ospf_info, "ospf-neighbor"):
            neighbor_id = _xml_find_text(nbr, "neighbor-id")
            if not neighbor_id:
                continue
            state_raw = _xml_find_text(nbr, "ospf-neighbor-state", "Unknown")
            state = state_raw.capitalize()
            interface = _xml_find_text(nbr, "interface-name")
            area = _xml_find_text(nbr, "ospf-area")
            uptime = _xml_find_text(nbr, "neighbor-adjacency-time")
            results.append(
                {
                    "neighbor_id": neighbor_id,
                    "state": state,
                    "interface": interface,
                    "area": area,
                    "uptime": uptime,
                }
            )
        return results

    def get_ospf_interfaces(self, vrf: str = "default") -> list[dict]:
        """Get OSPF-enabled interfaces. Transport: NETCONF ``<get-ospf-interface-information/>``."""
        try:
            if vrf != "default":
                rpc = f"<get-ospf-interface-information><instance>{vrf}</instance></get-ospf-interface-information>"
            else:
                rpc = "<get-ospf-interface-information/>"
            xml_str = self._netconf_rpc(rpc)
            return self._normalize_ospf_interfaces(xml_str)
        except Exception as exc:
            logger.warning("get_ospf_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _normalize_ospf_interfaces(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_ospf_interfaces")
        if root is None:
            return []
        ospf_info = _xml_find(root, "ospf-interface-information")
        if ospf_info is None:
            ospf_info = root
        results: list[dict] = []
        for intf in _xml_findall(ospf_info, "ospf-interface"):
            name = _xml_find_text(intf, "interface-name")
            if not name:
                continue
            area = _xml_find_text(intf, "ospf-area", "")
            ntype = _xml_find_text(intf, "interface-type", "").lower()
            cost_str = _xml_find_text(intf, "interface-cost", "0")
            neighbor_str = _xml_find_text(intf, "neighbor-count", "0")
            results.append(
                {
                    "interface": name,
                    "area": area,
                    "network_type": ntype,
                    "cost": int(cost_str),
                    "neighbor_count": int(neighbor_str),
                }
            )
        return results

    def get_bgp_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get detailed BGP neighbors. Transport: NETCONF ``<get-bgp-neighbor-information/>``."""
        try:
            if vrf != "default":
                rpc = f"<get-bgp-neighbor-information><instance>{vrf}</instance></get-bgp-neighbor-information>"
            else:
                rpc = "<get-bgp-neighbor-information/>"
            xml_str = self._netconf_rpc(rpc)
            return self._normalize_bgp_neighbors(xml_str)
        except Exception as exc:
            logger.warning("get_bgp_neighbors failed: %s", exc)
            return []

    @staticmethod
    def _normalize_bgp_neighbors(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_bgp_neighbors")
        if root is None:
            return []
        bgp_info = _xml_find(root, "bgp-information")
        if bgp_info is None:
            bgp_info = root
        results: list[dict] = []
        for peer in _xml_findall(bgp_info, "bgp-peer"):
            address = _xml_find_text(peer, "peer-address", "")
            # JunOS may include port in peer-address, strip it
            if "+" in address:
                address = address.split("+")[0]
            remote_as_str = _xml_find_text(peer, "peer-as", "0")
            state = _xml_find_text(peer, "peer-state", "")
            pfx_recv = _xml_find_text(peer, "bgp-rib/received-prefix-count", "0")
            pfx_sent = _xml_find_text(peer, "bgp-rib/advertised-prefix-count", "0")
            uptime = _xml_find_text(peer, "elapsed-time", "")
            description = _xml_find_text(peer, "description", "")
            results.append(
                {
                    "neighbor": address,
                    "remote_as": int(remote_as_str),
                    "state": state.capitalize() if state else "",
                    "prefixes_received": int(pfx_recv),
                    "prefixes_sent": int(pfx_sent),
                    "uptime": uptime,
                    "description": description,
                }
            )
        return results

    def get_route_table(self, protocol: str = "", vrf: str = "default", limit: int = 100) -> list[dict]:
        """Get routing table. Transport: NETCONF ``<get-route-information/>``."""
        try:
            if vrf != "default":
                rpc = f"<get-route-information><table>{vrf}.inet.0</table></get-route-information>"
            else:
                rpc = "<get-route-information/>"
            xml_str = self._netconf_rpc(rpc)
            return self._normalize_route_table(xml_str, protocol, limit)
        except Exception as exc:
            logger.warning("get_route_table failed: %s", exc)
            return []

    @staticmethod
    def _normalize_route_table(xml_str: str, protocol_filter: str, limit: int) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_route_table")
        if root is None:
            return []
        route_info = _xml_find(root, "route-information")
        if route_info is None:
            route_info = root
        results: list[dict] = []
        for route_table in _xml_findall(route_info, "route-table"):
            for rt in _xml_findall(route_table, "rt"):
                prefix = _xml_find_text(rt, "rt-destination")
                rt_entry = _xml_find(rt, "rt-entry")
                if rt_entry is None:
                    continue
                proto_raw = _xml_find_text(rt_entry, "protocol-name", "").lower()
                # Normalize JunOS protocol names
                proto_map = {"direct": "connected", "local": "connected", "access-internal": "connected"}
                route_proto = proto_map.get(proto_raw, proto_raw)
                if protocol_filter and route_proto != protocol_filter:
                    continue
                nh_elem = _xml_find(rt_entry, "nh")
                next_hop = ""
                if nh_elem is not None:
                    next_hop = _xml_find_text(nh_elem, "to")
                    if not next_hop:
                        next_hop = _xml_find_text(nh_elem, "via")
                metric_str = _xml_find_text(rt_entry, "metric", "0")
                pref_str = _xml_find_text(rt_entry, "preference", "0")
                try:
                    metric = int(metric_str)
                except ValueError:
                    metric = 0
                try:
                    preference = int(pref_str)
                except ValueError:
                    preference = 0
                results.append(
                    {
                        "prefix": prefix,
                        "next_hop": next_hop,
                        "protocol": route_proto,
                        "metric": metric,
                        "preference": preference,
                    }
                )
                if len(results) >= limit:
                    return results
        return results

    def get_route_summary(self) -> dict:
        """Get route count summary. Transport: NETCONF ``<get-route-summary-information/>``."""
        try:
            xml_str = self._netconf_rpc("<get-route-summary-information/>")
            return self._normalize_route_summary(xml_str)
        except Exception as exc:
            logger.warning("get_route_summary failed: %s", exc)
            return {"total": 0, "connected": 0, "static": 0, "bgp": 0, "ospf": 0, "isis": 0}

    @staticmethod
    def _normalize_route_summary(xml_str: str) -> dict:
        root = _safe_parse_xml(xml_str, "get_route_summary")
        if root is None:
            return {"total_routes": 0, "active_routes": 0, "protocols": {}}
        summary_info = _xml_find(root, "route-summary-information")
        if summary_info is None:
            summary_info = root
        result = {"total": 0, "connected": 0, "static": 0, "bgp": 0, "ospf": 0, "isis": 0}
        # Total routes
        for table in _xml_findall(summary_info, "route-table"):
            total_str = _xml_find_text(table, "total-route-count", "0")
            try:
                result["total"] += int(total_str)
            except ValueError:
                pass
            # Per-protocol counts
            for proto_elem in _xml_findall(table, "protocols"):
                proto_name = _xml_find_text(proto_elem, "protocol-name", "").lower()
                count_str = _xml_find_text(proto_elem, "protocol-route-count", "0")
                try:
                    count = int(count_str)
                except ValueError:
                    count = 0
                # Map JunOS protocol names
                proto_map = {"direct": "connected", "local": "connected", "access-internal": "connected"}
                normalized_name = proto_map.get(proto_name, proto_name)
                if normalized_name in result:
                    result[normalized_name] += count
            break  # First table (inet.0)
        return result

    def get_mac_table(self, vlan: int | None = None, limit: int = 500) -> list[dict]:
        """Get MAC address table. Transport: NETCONF ``<get-ethernet-switching-table-information/>``."""
        try:
            xml_str = self._netconf_rpc("<get-ethernet-switching-table-information/>")
            return self._normalize_mac_table(xml_str, vlan, limit)
        except Exception as exc:
            logger.warning("get_mac_table failed: %s", exc)
            return []

    @staticmethod
    def _normalize_mac_table(xml_str: str, vlan_filter: int | None, limit: int) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_mac_table")
        if root is None:
            return []
        sw_info = _xml_find(root, "ethernet-switching-table-information")
        if sw_info is None:
            sw_info = root
        results: list[dict] = []
        for table in _xml_findall(sw_info, "ethernet-switching-table"):
            for entry in _xml_findall(table, "mac-table-entry"):
                mac_raw = _xml_find_text(entry, "mac-address")
                vlan_str = _xml_find_text(entry, "mac-vlan")
                if not vlan_str:
                    vlan_str = _xml_find_text(entry, "vlan-name")
                # Extract VLAN ID from name like "VLAN100" or plain "100"
                vlan_id = 0
                vlan_match = re.search(r"(\d+)", vlan_str)
                if vlan_match:
                    vlan_id = int(vlan_match.group(1))
                if vlan_filter is not None and vlan_id != vlan_filter:
                    continue
                interface = _xml_find_text(entry, "mac-interfaces")
                if not interface:
                    interface = _xml_find_text(entry, "mac-interface")
                type_raw = _xml_find_text(entry, "mac-type", "dynamic").lower()
                mac_type = "static" if "static" in type_raw else "dynamic"
                results.append(
                    {
                        "mac_address": _normalize_mac(mac_raw),
                        "vlan": vlan_id,
                        "interface": interface,
                        "type": mac_type,
                    }
                )
                if len(results) >= limit:
                    return results
        return results

    def get_stp_status(self) -> dict:
        """Get spanning-tree status. Transport: NETCONF ``<get-stp-bridge-information/>``."""
        try:
            xml_str = self._netconf_rpc("<get-stp-bridge-information/>")
            return self._normalize_stp_status(xml_str)
        except Exception as exc:
            logger.warning("get_stp_status failed: %s", exc)
            return {"mode": "", "root_bridge": "", "instances": []}

    @staticmethod
    def _normalize_stp_status(xml_str: str) -> dict:
        root = _safe_parse_xml(xml_str, "get_stp_status")
        if root is None:
            return {"enabled": False, "mode": "", "instances": []}
        stp_info = _xml_find(root, "stp-bridge-information")
        if stp_info is None:
            stp_info = root
        mode = _xml_find_text(stp_info, "stp-protocol", "rstp").lower()
        root_bridge = ""
        instances: list[dict] = []
        for inst in _xml_findall(stp_info, "stp-instance"):
            inst_id = _xml_find_text(inst, "stp-instance-id", _xml_find_text(inst, "vlan-id", ""))
            inst_root = _xml_find_text(inst, "stp-root-bridge-address")
            if not inst_root:
                inst_root = _xml_find_text(inst, "stp-designated-bridge-address")
            if not root_bridge and inst_root:
                root_bridge = inst_root
            root_port = _xml_find_text(inst, "stp-root-port")
            prio_str = _xml_find_text(inst, "stp-bridge-priority", "0")
            try:
                priority = int(prio_str)
            except ValueError:
                priority = 0
            instances.append(
                {
                    "id": inst_id,
                    "root_bridge": inst_root,
                    "root_port": root_port,
                    "priority": priority,
                }
            )
        return {"mode": mode, "root_bridge": root_bridge, "instances": instances}

    def get_port_channels(self) -> list[dict]:
        """Get aggregated Ethernet (port-channel) summary.

        JunOS calls these "ae" (aggregated Ethernet) interfaces.
        Transport: NETCONF ``<get-lacp-interface-information/>``.
        """
        try:
            xml_str = self._netconf_rpc("<get-lacp-interface-information/>")
            return self._normalize_port_channels(xml_str)
        except Exception as exc:
            logger.warning("get_port_channels failed: %s", exc)
            return []

    @staticmethod
    def _normalize_port_channels(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_port_channels")
        if root is None:
            return []
        lacp_info = _xml_find(root, "lacp-interface-information")
        if lacp_info is None:
            lacp_info = root
        results: list[dict] = []
        for lag in _xml_findall(lacp_info, "lag-lacp-header"):
            name = _xml_find_text(lag, "aggregate-name")
            if not name:
                continue
            status_raw = _xml_find_text(lag, "aggregate-oper-state", "down").lower()
            status = "up" if status_raw == "up" else "down"
            protocol_raw = _xml_find_text(lag, "aggregate-lacp-mode", "lacp").lower()
            protocol = "lacp" if "lacp" in protocol_raw else "static"
            members: list[dict] = []
            for member in _xml_findall(lag, "lag-lacp-protocol"):
                mem_name = _xml_find_text(member, "name")
                if not mem_name:
                    continue
                mem_state = _xml_find_text(member, "lacp-mux-state", "down").lower()
                if "collecting" in mem_state or "distributing" in mem_state:
                    mem_status = "bundled"
                elif "standby" in mem_state or "hot" in mem_state:
                    mem_status = "hot-standby"
                else:
                    mem_status = "down"
                members.append({"interface": mem_name, "status": mem_status})
            results.append({"name": name, "status": status, "protocol": protocol, "members": members})
        return results

    def get_environment(self) -> dict:
        """Get environmental sensor data. Transport: NETCONF ``<get-environment-information/>``."""
        try:
            xml_str = self._netconf_rpc("<get-environment-information/>")
            return self._normalize_environment(xml_str)
        except Exception as exc:
            logger.warning("get_environment failed: %s", exc)
            return {"temperature": [], "fans": [], "power": []}

    @staticmethod
    def _normalize_environment(xml_str: str) -> dict:
        root = _safe_parse_xml(xml_str, "get_environment")
        if root is None:
            return {"fans": [], "power": [], "temperature": []}
        env_info = _xml_find(root, "environment-information")
        if env_info is None:
            env_info = root
        temps: list[dict] = []
        fans: list[dict] = []
        power: list[dict] = []
        for item in _xml_findall(env_info, "environment-item"):
            name = _xml_find_text(item, "name")
            status_raw = _xml_find_text(item, "status", "").lower()
            temp_str = _xml_find_text(item, "temperature")
            # Classify by name or available data
            name_lower = name.lower()
            if "fan" in name_lower:
                fan_status = "ok" if "ok" in status_raw else "failed"
                fans.append({"name": name, "status": fan_status})
            elif "power" in name_lower or "psu" in name_lower or "pem" in name_lower:
                watts = 0.0
                watts_str = _xml_find_text(item, "power-output")
                if watts_str:
                    w_match = re.search(r"([\d.]+)", watts_str)
                    if w_match:
                        watts = float(w_match.group(1))
                psu_status = "ok" if "ok" in status_raw else "failed"
                power.append({"name": name, "status": psu_status, "output_watts": watts})
            elif temp_str or "temp" in name_lower or "cpu" in name_lower or "inlet" in name_lower:
                temp_val = 0.0
                if temp_str:
                    temp_match = re.search(r"([\d.]+)", temp_str)
                    if temp_match:
                        temp_val = float(temp_match.group(1))
                temp_status = "ok" if "ok" in status_raw else ("critical" if "critical" in status_raw else "warning")
                temps.append({"sensor": name, "value": temp_val, "status": temp_status})
        return {"temperature": temps, "fans": fans, "power": power}

    def get_ntp_status(self) -> dict:
        """Get NTP status. Transport: NETCONF ``<get-ntp-status/>`` + ``<get-ntp-associations-information/>``."""
        try:
            xml_str = self._netconf_rpc("<get-ntp-associations-information/>")
            return self._normalize_ntp_status(xml_str)
        except Exception as exc:
            logger.warning("get_ntp_status failed: %s", exc)
            return {"synchronized": False, "reference": "", "peers": []}

    @staticmethod
    def _normalize_ntp_status(xml_str: str) -> dict:
        root = _safe_parse_xml(xml_str, "get_ntp_status")
        if root is None:
            return {"synchronized": False, "stratum": 0, "peers": []}
        ntp_info = _xml_find(root, "ntp-associations-information")
        if ntp_info is None:
            ntp_info = root
        synchronized = False
        reference = ""
        peers: list[dict] = []
        for peer_elem in _xml_findall(ntp_info, "ntp-peer"):
            peer_addr = _xml_find_text(peer_elem, "peer-address")
            if not peer_addr:
                continue
            # JunOS marks the active peer with tally-code '*'
            tally = _xml_find_text(peer_elem, "tally-code")
            if tally == "*":
                synchronized = True
                reference = peer_addr
            stratum_str = _xml_find_text(peer_elem, "peer-stratum", "0")
            offset_str = _xml_find_text(peer_elem, "peer-offset", "0.0")
            jitter_str = _xml_find_text(peer_elem, "peer-jitter", "0.0")
            try:
                stratum = int(stratum_str)
            except ValueError:
                stratum = 0
            try:
                offset = float(offset_str)
            except ValueError:
                offset = 0.0
            try:
                jitter = float(jitter_str)
            except ValueError:
                jitter = 0.0
            peers.append({"peer": peer_addr, "stratum": stratum, "offset": offset, "jitter": jitter})
        return {"synchronized": synchronized, "reference": reference, "peers": peers}

    def get_cpu_memory(self) -> dict:
        """Get CPU/memory utilization.

        Transport: NETCONF ``<get-route-engine-information/>``.
        JunOS reports CPU/memory via Routing Engine information.
        """
        try:
            xml_str = self._netconf_rpc("<get-route-engine-information/>")
            return self._normalize_cpu_memory(xml_str)
        except Exception as exc:
            logger.warning("get_cpu_memory failed: %s", exc)
            return {"cpu_percent": 0.0, "memory_total": 0, "memory_used": 0, "memory_free": 0}

    @staticmethod
    def _normalize_cpu_memory(xml_str: str) -> dict:
        root = _safe_parse_xml(xml_str, "get_cpu_memory")
        if root is None:
            return {"cpu_percent": 0.0, "memory_total": 0, "memory_used": 0, "memory_free": 0}
        re_info = _xml_find(root, "route-engine-information")
        if re_info is None:
            re_info = root
        re_elem = _xml_find(re_info, "route-engine")
        if re_elem is None:
            re_elem = re_info
        cpu_idle_str = _xml_find_text(re_elem, "cpu-idle", "")
        if not cpu_idle_str:
            cpu_user = float(_xml_find_text(re_elem, "cpu-user", "0"))
            cpu_system = float(_xml_find_text(re_elem, "cpu-system", "0"))
            cpu_percent = round(cpu_user + cpu_system, 2)
        else:
            try:
                cpu_percent = round(100.0 - float(cpu_idle_str), 2)
            except ValueError:
                cpu_percent = 0.0
        mem_total_str = _xml_find_text(re_elem, "memory-dram-size", "0")
        mem_used_str = _xml_find_text(re_elem, "memory-buffer-utilization", "0")
        try:
            mem_total = int(mem_total_str)
        except ValueError:
            mem_total = 0
        try:
            mem_used_pct = int(mem_used_str)
        except ValueError:
            mem_used_pct = 0
        # JunOS reports memory total in MB and utilization as percentage
        mem_total_kb = mem_total * 1024
        mem_used_kb = (mem_total_kb * mem_used_pct) // 100
        mem_free_kb = mem_total_kb - mem_used_kb
        return {
            "cpu_percent": cpu_percent,
            "memory_total": mem_total_kb,
            "memory_used": mem_used_kb,
            "memory_free": mem_free_kb,
        }

    def get_acls(self) -> list[dict]:
        """Get firewall filters (JunOS equivalent of ACLs).

        Transport: NETCONF ``<get-firewall-information/>``.
        """
        try:
            xml_str = self._netconf_rpc("<get-firewall-information/>")
            return self._normalize_acls(xml_str)
        except Exception as exc:
            logger.warning("get_acls failed: %s", exc)
            return []

    @staticmethod
    def _normalize_acls(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_acls")
        if root is None:
            return []
        fw_info = _xml_find(root, "firewall-information")
        if fw_info is None:
            fw_info = root
        results: list[dict] = []
        for fw_filter in _xml_findall(fw_info, "filter-information"):
            name = _xml_find_text(fw_filter, "filter-name")
            if not name:
                continue
            # JunOS doesn't distinguish standard/extended like Cisco; use "extended"
            acl_type = "extended"
            rules: list[dict] = []
            for counter in _xml_findall(fw_filter, "counter"):
                counter_name = _xml_find_text(counter, "counter-name")
                # JunOS firewall filter counters encode action in the counter name
                action = "permit"
                if "discard" in counter_name.lower() or "deny" in counter_name.lower():
                    action = "deny"
                rules.append(
                    {
                        "sequence": len(rules) + 1,
                        "action": action,
                        "source": counter_name,
                    }
                )
            results.append({"name": name, "type": acl_type, "rules": rules})
        return results

    def get_snmp_config(self) -> dict:
        """Get SNMP configuration. Transport: NETCONF ``<get-configuration>``."""
        try:
            xml_str = self._netconf_rpc("<get-configuration><snmp/></get-configuration>")
            return self._normalize_snmp_config(xml_str)
        except Exception as exc:
            logger.warning("get_snmp_config failed: %s", exc)
            return {"communities": [], "users": [], "traps": []}

    @staticmethod
    def _normalize_snmp_config(xml_str: str) -> dict:
        root = _safe_parse_xml(xml_str, "get_snmp_config")
        if root is None:
            return {"communities": [], "location": "", "contact": "", "traps": []}
        config = _xml_find(root, "configuration")
        if config is None:
            config = root
        snmp = _xml_find(config, "snmp")
        if snmp is None:
            snmp = config
        communities: list[dict] = []
        users: list[dict] = []
        traps: list[dict] = []
        for comm in _xml_findall(snmp, "community"):
            comm_name = _xml_find_text(comm, "name")
            auth = _xml_find_text(comm, "authorization", "read-only")
            access = "ro" if "read-only" in auth.lower() else "rw"
            if comm_name:
                communities.append({"name": comm_name, "access": access})
        for v3_user in _xml_findall(snmp, "v3"):
            for usm in _xml_findall(v3_user, "usm"):
                for local_engine in _xml_findall(usm, "local-engine"):
                    for user in _xml_findall(local_engine, "user"):
                        user_name = _xml_find_text(user, "name")
                        if user_name:
                            users.append({"name": user_name})
        for trap_group in _xml_findall(snmp, "trap-group"):
            for target in _xml_findall(trap_group, "targets"):
                host = _xml_find_text(target, "name")
                if host:
                    traps.append({"host": host})
        return {"communities": communities, "users": users, "traps": traps}

    def get_running_config_section(self, section: str) -> str:
        """Get a specific section of the running configuration.

        JunOS uses hierarchical config; ``section`` maps to a top-level stanza.
        Transport: NETCONF ``<get-configuration>``.
        """
        try:
            xml_str = self._netconf_rpc(f"<get-configuration><{section}/></get-configuration>")
            # Return the raw XML section as text — JunOS config is hierarchical XML
            return xml_str
        except Exception as exc:
            logger.warning("get_running_config_section failed: %s", exc)
            return ""

    def get_startup_config(self) -> str:
        """Get the full startup configuration.

        JunOS does not separate startup from running; committed config
        persists across reboots. Returns the running config text.
        Transport: NETCONF ``<get-configuration format="text"/>``.
        """
        try:
            xml_str = self._netconf_rpc('<get-configuration format="text"/>')
            # Extract text from <configuration-text> or return raw
            root = _safe_parse_xml(xml_str, "get_startup_config", host=self._host or "unknown")
            if root is None:
                return xml_str
            text_elem = _xml_find(root, "configuration-text")
            if text_elem is not None and text_elem.text:
                return text_elem.text
            config_elem = _xml_find(root, "configuration")
            if config_elem is not None and config_elem.text:
                return config_elem.text
            return xml_str
        except Exception as exc:
            logger.warning("get_startup_config failed: %s", exc)
            return ""

    def get_vrfs(self) -> list[dict]:
        """Get all routing instances (JunOS equivalent of VRFs).

        Transport: NETCONF ``<get-instance-information/>``.
        """
        try:
            xml_str = self._netconf_rpc("<get-instance-information/>")
            return self._normalize_vrfs(xml_str)
        except Exception as exc:
            logger.warning("get_vrfs failed: %s", exc)
            return []

    @staticmethod
    def _normalize_vrfs(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_vrfs")
        if root is None:
            return []
        inst_info = _xml_find(root, "instance-information")
        if inst_info is None:
            inst_info = root
        results: list[dict] = []
        for inst in _xml_findall(inst_info, "instance-core"):
            name = _xml_find_text(inst, "instance-name")
            if not name:
                continue
            rd = _xml_find_text(inst, "instance-rd")
            if rd == "---" or rd == "--":
                rd = ""
            interfaces: list[str] = []
            for intf in _xml_findall(inst, "instance-interface"):
                intf_name = _xml_find_text(intf, "interface-name")
                if intf_name:
                    interfaces.append(intf_name)
            route_count = 0
            rc_str = _xml_find_text(inst, "instance-rib-route-count")
            if rc_str:
                try:
                    route_count = int(rc_str)
                except ValueError:
                    pass
            results.append({"name": name, "rd": rd, "interfaces": interfaces, "route_count": route_count})
        return results

    def get_trunk_interfaces(self) -> list[dict]:
        """Get trunk (tagged) interfaces.

        Transport: NETCONF ``<get-ethernet-switching-interface-information/>``.
        JunOS uses 'trunk' mode for tagged ports.
        """
        try:
            xml_str = self._netconf_rpc("<get-ethernet-switching-interface-information/>")
            return self._normalize_trunks(xml_str)
        except Exception as exc:
            logger.warning("get_trunk_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _normalize_trunks(xml_str: str) -> list[dict]:
        root = _safe_parse_xml(xml_str, "get_trunks")
        if root is None:
            return []
        sw_info = _xml_find(root, "switching-interface-information")
        if sw_info is None:
            sw_info = root
        results: list[dict] = []
        for intf in _xml_findall(sw_info, "interface"):
            name = _xml_find_text(intf, "interface-name")
            mode = _xml_find_text(intf, "interface-port-mode") or ""
            if mode.lower() != "trunk":
                continue
            native_str = _xml_find_text(intf, "interface-native-vlan") or "0"
            try:
                native = int(native_str)
            except ValueError:
                native = 0
            vlan_members: list[str] = []
            for member in _xml_findall(intf, "interface-vlan-member"):
                vlan_name = _xml_find_text(member, "interface-vlan-member-tagid")
                if vlan_name:
                    vlan_members.append(vlan_name)
            results.append(
                {
                    "interface": name or "",
                    "mode": "trunk",
                    "native_vlan": native,
                    "allowed_vlans": ",".join(vlan_members) if vlan_members else "all",
                    "active_vlans": ",".join(vlan_members) if vlan_members else "",
                }
            )
        return results

    def get_vrf_detail(self, vrf: str) -> dict:
        """Get detailed routing instance information.

        Transport: NETCONF ``<get-instance-information>`` with instance name.
        JunOS calls VRFs "routing instances".
        """
        try:
            rpc = f"<get-instance-information><instance-name>{vrf}</instance-name></get-instance-information>"
            xml_str = self._netconf_rpc(rpc)
            vrfs = self._normalize_vrfs(xml_str)
            if vrfs:
                detail = vrfs[0]
                detail.setdefault("import_targets", [])
                detail.setdefault("export_targets", [])
                return detail
            return {}
        except Exception as exc:
            logger.warning("get_vrf_detail failed: %s", exc)
            return {}

    def ping(self, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
        """Execute a ping. Transport: NETCONF ``<ping>`` RPC or SSH."""
        cmd = f"ping {destination} count {count} rapid"
        if source:
            cmd += f" source {source}"
        if vrf != "default":
            cmd += f" routing-instance {vrf}"
        try:
            results = self.run_show([cmd], encoding="text")
            output = results[0].get("output", "") if results else ""
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
        return self._parse_ping_output(output, count)

    @staticmethod
    def _parse_ping_output(output: str, count: int) -> dict:
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
        # JunOS: "5 packets transmitted, 5 packets received, 0% packet loss"
        m = re.search(r"(\d+)\s+packets?\s+transmitted,\s*(\d+)\s+packets?\s+received", output)
        if m:
            result["packets_sent"] = int(m.group(1))
            result["packets_received"] = int(m.group(2))
            sent = result["packets_sent"]
            result["packet_loss_pct"] = round((sent - result["packets_received"]) / sent * 100, 1) if sent else 100.0
            result["success"] = result["packets_received"] > 0
        # JunOS: "round-trip min/avg/max/stddev = 0.123/0.456/0.789/0.012 ms"
        rtt_m = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))
        return result

    def traceroute(self, destination: str, source: str = "", vrf: str = "default") -> dict:
        """Execute a traceroute. Transport: SSH/NETCONF ``traceroute``."""
        cmd = f"traceroute {destination}"
        if source:
            cmd += f" source {source}"
        if vrf != "default":
            cmd += f" routing-instance {vrf}"
        try:
            results = self.run_show([cmd], encoding="text")
            output = results[0].get("output", "") if results else ""
        except Exception as exc:
            return {"hops": [], "output": str(exc)}
        return self._parse_traceroute_output(output)

    @staticmethod
    def _parse_traceroute_output(output: str) -> dict:
        hops: list[dict] = []
        for line in output.splitlines():
            m = re.match(r"\s*(\d+)\s+(.+)", line)
            if not m:
                continue
            hop_num = int(m.group(1))
            rest = m.group(2).strip()
            addr_m = re.search(r"([\d.]+|[\w.-]+)\s.*?([\d.]+)\s*ms", rest)
            if addr_m:
                hops.append({"hop": hop_num, "address": addr_m.group(1), "rtt_ms": float(addr_m.group(2))})
            elif "*" in rest:
                hops.append({"hop": hop_num, "address": "*", "rtt_ms": 0.0})
        return {"hops": hops, "output": output}

    def get_interface_flaps(self, hours: int = 24) -> list[dict]:
        """Get interface flap events. Transport: SSH/NETCONF ``show log messages``."""
        try:
            results = self.run_show(['show log messages | match "LINK_UP|LINK_DOWN|changed state"'], encoding="text")
            output = results[0].get("output", "") if results else ""
        except Exception:
            return []
        return self._parse_flap_events(output)

    @staticmethod
    def _parse_flap_events(output: str) -> list[dict]:
        events: list[dict] = []
        for line in output.splitlines():
            lower = line.lower()
            if "link_up" not in lower and "link_down" not in lower and "changed state" not in lower:
                continue
            intf_m = re.search(r"(ge-\S+|xe-\S+|et-\S+|ae\d+|lo\d+|irb\.\d+)", line, re.IGNORECASE)
            interface = intf_m.group(1) if intf_m else "unknown"
            event = "link state change"
            if "link_up" in lower or "changed state to up" in lower:
                event = "link up"
            elif "link_down" in lower or "changed state to down" in lower:
                event = "link down"
            ts_m = re.match(r"^(\w+\s+\d+\s+[\d:]+|\d{4}-\d{2}-\d{2}\s+[\d:]+)", line)
            timestamp = ts_m.group(1) if ts_m else ""
            events.append({"interface": interface, "event": event, "timestamp": timestamp})
        return events

    def get_aaa_status(self) -> dict:
        """Get AAA configuration status."""
        raise NotSupportedError(f"get_aaa_status is not supported on {self.platform}")

    def get_user_sessions(self) -> list[dict]:
        """Get active user sessions on the device."""
        raise NotSupportedError(f"get_user_sessions is not supported on {self.platform}")

    def get_copp_policy(self) -> dict:
        """Get Control Plane Policing policy configuration and stats."""
        raise NotSupportedError(f"get_copp_policy is not supported on {self.platform}")
