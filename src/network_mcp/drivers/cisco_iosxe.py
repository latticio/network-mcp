"""Cisco IOS-XE driver — RESTCONF primary transport with SSH fallback.

Implements the NetworkDriver protocol for Cisco IOS-XE devices using
RESTCONF (via httpx) as the primary transport. Falls back to SSH when
RESTCONF is unavailable (not configured or unreachable).

SSH fallback uses scrapli for persistent, secure sessions (replacing
the previous subprocess-based SSH that had security issues including
disabled host key verification and no session reuse).

Requires the ``cisco`` optional extra: ``pip install network-mcp[cisco]``
SSH fallback requires: ``pip install network-mcp[ssh]``
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from pydantic import SecretStr
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from network_mcp.drivers.base import NotSupportedError
from network_mcp.helpers import normalize_mac as _normalize_mac

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from scrapli.driver.core import IOSXEDriver as ScrapliIOSXEDriver
    from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliConnectionError, ScrapliTimeout

    HAS_SCRAPLI = True
except ImportError:
    ScrapliIOSXEDriver = None  # type: ignore[assignment, misc]
    ScrapliAuthenticationFailed = None  # type: ignore[assignment, misc]
    ScrapliConnectionError = None  # type: ignore[assignment, misc]
    ScrapliTimeout = None  # type: ignore[assignment, misc]
    HAS_SCRAPLI = False

logger = logging.getLogger("network-mcp")

# Default idle threshold (seconds) before a keep-alive check is triggered
_DEFAULT_KEEPALIVE_INTERVAL = 60

# RESTCONF media type for YANG-data JSON
_RESTCONF_HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}

# RESTCONF base paths for IOS-XE YANG models
_RESTCONF_PATHS = {
    "capabilities": "/ietf-restconf-monitoring:restconf-state/capabilities",
    "native": "/Cisco-IOS-XE-native:native",
    "interfaces": "/ietf-interfaces:interfaces",
    "bgp_oper": "/Cisco-IOS-XE-bgp-oper:bgp-state-data",
    "arp_oper": "/Cisco-IOS-XE-arp-oper:arp-data",
    "lldp_oper": "/Cisco-IOS-XE-lldp-oper:lldp-entries",
}


class IosXeScrapliTransport:
    """Persistent SSH session using scrapli for IOS-XE devices.

    Provides secure, reusable SSH connections with proper host key
    verification (replacing the insecure subprocess-based approach).
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
            raise ImportError("scrapli not installed. Install with: pip install network-mcp[ssh]")
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
        self._driver = ScrapliIOSXEDriver(**driver_kwargs)
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


class IosXeDriver:
    """Cisco IOS-XE driver using RESTCONF (primary) with SSH fallback.

    Satisfies the NetworkDriver protocol defined in drivers/base.py.

    RESTCONF requires ``ip http secure-server`` and ``restconf`` to be
    configured on the IOS-XE device. If RESTCONF is not available, the
    driver falls back to SSH for command execution.
    """

    platform: str = "iosxe"
    vendor: str = "cisco"

    VENDOR_API_VERSION = "restconf-1.0"

    def __init__(self) -> None:
        self._restconf: httpx.Client | None = None
        self._ssh_transport: IosXeScrapliTransport | None = None
        self._ssh_host: str | None = None
        self._ssh_username: str | None = None
        self._ssh_password: SecretStr | None = None
        self._ssh_port: int = 22
        self._ssh_private_key: str = ""
        self._ssh_strict_key: bool = True
        self._ssh_known_hosts: str = "~/.ssh/known_hosts"
        self._transport: str = "restconf"  # "restconf" or "ssh"
        self._connected: bool = False
        self._last_command_time: float = 0.0
        self._timeout_connect: float = 10.0
        self._timeout_show: float = 30.0
        self._timeout_config: float = 60.0
        self._keepalive_interval: int = _DEFAULT_KEEPALIVE_INTERVAL
        self._connect_transport: str = "restconf"
        self._connect_kwargs: dict[str, Any] = {}
        self._restconf_paths_unavailable: set[str] = set()

    # --- Connection lifecycle ---

    def connect(  # noqa: PLR0913
        self,
        host: str,
        transport: str = "restconf",
        username: str = "admin",
        password: str = "",
        **kwargs: Any,
    ) -> None:
        """Establish connection to an IOS-XE device.

        Tries RESTCONF first (if transport='restconf'). On failure,
        falls back to SSH automatically via scrapli.

        Args:
            host: Device hostname or IP address.
            transport: Transport method ('restconf' or 'ssh').
            username: Device username.
            password: Device password.
            **kwargs: Additional options (port, timeout, verify_ssl,
                      auth_private_key, ssh_strict_key, ssh_known_hosts_file,
                      timeout_connect, timeout_show, timeout_config).
        """
        self._timeout_connect = kwargs.pop("timeout_connect", 10.0)
        self._timeout_show = kwargs.pop("timeout_show", 30.0)
        self._timeout_config = kwargs.pop("timeout_config", 60.0)
        self._keepalive_interval = kwargs.pop("keepalive_interval", _DEFAULT_KEEPALIVE_INTERVAL)
        self._connect_transport = transport
        self._connect_kwargs = kwargs.copy()

        if not HAS_HTTPX and transport == "restconf":
            logger.warning("httpx not installed, falling back to SSH for %s", host)
            transport = "ssh"

        self._ssh_host = host
        self._ssh_username = username
        self._ssh_password = SecretStr(password) if password else None
        self._ssh_port = kwargs.get("port", 22) if transport == "ssh" else 22
        self._ssh_private_key = kwargs.get("auth_private_key", "")
        self._ssh_strict_key = kwargs.get("ssh_strict_key", True)
        self._ssh_known_hosts = kwargs.get("ssh_known_hosts_file", "~/.ssh/known_hosts")

        self._connect_with_retry(host, transport, username, password, **kwargs)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def _connect_with_retry(self, host: str, transport: str, username: str, password: str, **kwargs: Any) -> None:
        """Internal connect with tenacity retry."""
        if transport == "restconf":
            self._init_restconf(host, username, password, **kwargs)
        else:
            self._init_ssh_fallback(host, username, password, **kwargs)
        self._last_command_time = time.monotonic()

    def _init_restconf(self, host: str, username: str, password: str, **kwargs: Any) -> None:
        """Initialize RESTCONF transport with availability probe."""
        port = kwargs.get("port", 443)
        verify_ssl = kwargs.get("verify_ssl", True)

        base_url = f"https://{host}:{port}/restconf/data"
        self._restconf = httpx.Client(
            base_url=base_url,
            auth=(username, password),
            verify=verify_ssl,
            headers=_RESTCONF_HEADERS,
            timeout=self._timeout_show,
        )

        # Probe RESTCONF availability
        try:
            resp = self._restconf.get(_RESTCONF_PATHS["capabilities"])
            resp.raise_for_status()
            self._transport = "restconf"
            self._connected = True
            logger.debug("RESTCONF connected to %s:%s", host, port)
        except Exception as exc:
            # Check for auth failures — do NOT fallback to SSH for 401/403
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (401, 403):
                logger.error("RESTCONF authentication failed on %s (HTTP %d)", host, status_code)
                self._restconf.close()
                self._restconf = None
                raise ConnectionError(
                    f"Authentication failed for {host} (HTTP {status_code}). "
                    "Check credentials (NET_USERNAME/NET_PASSWORD)."
                ) from exc
            logger.warning("RESTCONF unavailable on %s (%s), falling back to SSH", host, exc)
            self._restconf.close()
            self._restconf = None
            self._init_ssh_fallback(host, username, password, **kwargs)

    def _init_ssh_fallback(self, host: str, username: str, password: str, **kwargs: Any) -> None:
        """Initialize SSH fallback transport using scrapli.

        Creates a persistent SSH session with proper host key verification.
        The session is reused across commands for efficiency.
        """
        self._transport = "ssh"
        self._ssh_host = host
        self._ssh_username = username
        self._ssh_password = SecretStr(password) if password else None
        self._ssh_port = kwargs.get("port", 22)

        if HAS_SCRAPLI:
            try:
                self._ssh_transport = IosXeScrapliTransport(
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
                "scrapli not installed — SSH fallback unavailable for %s. Install with: pip install network-mcp[ssh]",
                host,
            )
            self._ssh_transport = None

        self._connected = True

    def close(self) -> None:
        """Close all transport connections."""
        if self._restconf is not None:
            try:
                self._restconf.close()
            except Exception as exc:
                logger.debug("Failed to close RESTCONF client: %s", exc)
            finally:
                self._restconf = None
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
        """Query supported features and getter availability for IOS-XE."""
        return {
            "config_sessions": False,
            "gnmi": False,
            "restconf": True,
            "netconf": False,
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
        if self._ssh_host is None:
            raise ConnectionError("Cannot reconnect: no previous connection parameters.")
        self.close()
        self._connect_with_retry(
            self._ssh_host,
            self._connect_transport,
            self._ssh_username or "admin",
            self._ssh_password.get_secret_value()
            if isinstance(self._ssh_password, SecretStr)
            else (self._ssh_password or ""),
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
            if self._restconf:
                resp = self._restconf.get(_RESTCONF_PATHS["capabilities"])
                resp.raise_for_status()
            elif self._ssh_transport and self._ssh_transport.is_alive:
                self._ssh_transport.send_command("show clock")
            else:
                raise ConnectionError("No active transport")
        except Exception:
            logger.info("IOS-XE keep-alive failed for %s, reconnecting", self._ssh_host)
            try:
                self.reconnect()
            except ConnectionError as re_exc:
                raise ConnectionError(
                    f"Keep-alive probe failed for {self._ssh_host} and reconnect also failed: {re_exc}"
                ) from re_exc

    # --- Raw command execution ---

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands on the IOS-XE device.

        For RESTCONF transport, maps common show commands to RESTCONF GET
        requests. For SSH transport, executes commands via SSH.

        Args:
            commands: List of show commands to execute.
            encoding: Output encoding ('json' or 'text').

        Returns:
            List of response dicts, one per command.
        """
        if not self._connected:
            raise ConnectionError("IosXeDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        if self._restconf and encoding == "json":
            return self._restconf_show(commands)
        return self._ssh_show(commands)

    def run_config(self, commands: list[str]) -> list[str]:
        """Execute configuration commands on the IOS-XE device.

        Uses RESTCONF PATCH for structured config or SSH for CLI commands.

        Args:
            commands: List of configuration commands.

        Returns:
            List of result strings.
        """
        if not self._connected:
            raise ConnectionError("IosXeDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        if self._restconf:
            return self._restconf_config(commands)
        return self._ssh_config(commands)

    # Backward-compatibility aliases (match pyeapi.Node interface)
    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility."""
        return self.run_show(commands, encoding=encoding)

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility."""
        return self.run_config(commands)

    # --- RESTCONF transport methods ---

    def _restconf_show(self, commands: list[str]) -> list[dict]:
        """Execute show commands via RESTCONF GET requests.

        Falls back to SSH for individual commands when:
        - No RESTCONF path mapping exists for the command.
        - The RESTCONF path previously returned HTTP 404 (cached).
        - The current request returns HTTP 404 (path not supported on device).
        """
        results: list[dict] = []
        for cmd in commands:
            path = self._map_command_to_restconf_path(cmd)
            if path and self._restconf:
                # Skip RESTCONF for paths that previously returned 404
                if path in self._restconf_paths_unavailable:
                    logger.debug(
                        "RESTCONF path %s cached as unavailable on %s, using SSH",
                        path,
                        self._ssh_host,
                    )
                    ssh_result = self._ssh_show([cmd])
                    results.extend(ssh_result)
                    continue
                try:
                    resp = self._restconf.get(path)
                    resp.raise_for_status()
                    results.append(resp.json())
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    exc_name = type(exc).__name__.lower()
                    is_connect_error = "connect" in exc_name or "timeout" in exc_name
                    if status == 404:
                        logger.warning(
                            "RESTCONF path not found (404) for %s on %s, falling back to SSH",
                            path,
                            self._ssh_host,
                        )
                        self._restconf_paths_unavailable.add(path)
                        ssh_result = self._ssh_show([cmd])
                        results.extend(ssh_result)
                    elif status is not None and status >= 500:
                        logger.warning(
                            "RESTCONF server error (%d) for %s on %s, falling back to SSH",
                            status,
                            path,
                            self._ssh_host,
                        )
                        ssh_result = self._ssh_show([cmd])
                        results.extend(ssh_result)
                    elif is_connect_error:
                        logger.warning(
                            "RESTCONF connection error for %s on %s, falling back to SSH: %s",
                            path,
                            self._ssh_host,
                            exc,
                        )
                        ssh_result = self._ssh_show([cmd])
                        results.extend(ssh_result)
                    elif status is not None:
                        logger.warning("RESTCONF GET %s failed (HTTP %d): %s", path, status, exc)
                        results.append({"error": str(exc)})
                    else:
                        logger.warning("RESTCONF GET %s failed: %s", path, exc)
                        results.append({"error": str(exc)})
            else:
                # No RESTCONF mapping — fall back to SSH for this command
                ssh_result = self._ssh_show([cmd])
                results.extend(ssh_result)
        return results

    def _restconf_config(self, commands: list[str]) -> list[str]:
        """Execute config commands via RESTCONF PATCH."""
        # For CLI-style config commands, SSH is more reliable
        return self._ssh_config(commands)

    @staticmethod
    def _map_command_to_restconf_path(command: str) -> str | None:
        """Map a CLI show command to a RESTCONF path.

        Returns None if no mapping exists (falls back to SSH).
        """
        cmd = command.strip().lower()
        mappings: dict[str, str] = {
            "show version": _RESTCONF_PATHS["native"],
            "show interfaces": _RESTCONF_PATHS["interfaces"],
            "show ip bgp summary": _RESTCONF_PATHS["bgp_oper"],
            "show ip arp": _RESTCONF_PATHS["arp_oper"],
            "show lldp neighbors": _RESTCONF_PATHS["lldp_oper"],
            "show lldp neighbors detail": _RESTCONF_PATHS["lldp_oper"],
            "show running-config": _RESTCONF_PATHS["native"],
        }
        return mappings.get(cmd)

    # --- SSH transport methods ---

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

    def _ssh_config(self, commands: list[str]) -> list[str]:
        """Execute config commands via SSH (scrapli)."""
        if self._ssh_transport is None:
            raise ConnectionError("SSH transport not available. Install scrapli: pip install network-mcp[ssh]")
        try:
            output = self._ssh_transport.send_config(commands)
            return [output]
        except Exception as exc:
            return [f"Error: {exc}"]

    def _ssh_exec(self, command: str) -> str:
        """Execute a single show command via scrapli SSH.

        Uses a persistent SSH session for efficiency and security.
        The scrapli transport handles host key verification and
        session management automatically.
        """
        if not self._ssh_host or not self._ssh_username:
            raise ConnectionError("SSH not configured. Call connect() first.")
        if self._ssh_transport is None:
            raise ConnectionError("SSH transport not available. Install scrapli: pip install network-mcp[ssh]")

        try:
            return self._ssh_transport.send_command(command)
        except Exception as exc:
            if HAS_SCRAPLI and ScrapliTimeout is not None and isinstance(exc, ScrapliTimeout):
                raise TimeoutError(f"SSH command timed out: {command}") from exc
            raise RuntimeError(f"SSH command failed: {exc}") from exc

    # --- Normalized getters (NetworkDriver protocol) ---

    def get_facts(self) -> dict:
        """Get device facts in a vendor-neutral format.

        Uses RESTCONF ``/Cisco-IOS-XE-native:native`` to extract
        hostname, version, and system info.

        Returns:
            dict with keys: hostname, model, version, serial,
            uptime_seconds, vendor, platform
        """
        if self._restconf:
            try:
                resp = self._restconf.get(_RESTCONF_PATHS["native"])
                resp.raise_for_status()
                data = resp.json()
                native = data.get("Cisco-IOS-XE-native:native", data)
                return self._normalize_facts_restconf(native)
            except Exception as exc:
                logger.warning("RESTCONF get_facts failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show version"])
        return self._normalize_facts_ssh(result[0].get("output", ""))

    def _normalize_facts_restconf(self, native: dict) -> dict:
        """Normalize RESTCONF native data to facts schema."""
        return {
            "hostname": native.get("hostname", ""),
            "model": native.get("license", {}).get("udi", {}).get("pid", ""),
            "version": native.get("version", ""),
            "serial": native.get("license", {}).get("udi", {}).get("sn", ""),
            "uptime_seconds": 0,  # Not directly available in native config
            "vendor": self.vendor,
            "platform": self.platform,
        }

    @staticmethod
    def _normalize_facts_ssh(output: str) -> dict:
        """Parse 'show version' text output into facts schema."""
        hostname = ""
        model = ""
        version = ""
        serial = ""
        uptime_seconds = 0

        for line in output.splitlines():
            line_stripped = line.strip()
            if "Cisco IOS XE Software" in line_stripped or "Cisco IOS Software" in line_stripped:
                version_match = re.search(r"Version\s+([\d.]+[A-Za-z0-9]*)", line_stripped)
                if version_match:
                    version = version_match.group(1)
            elif line_stripped.startswith("cisco ") or line_stripped.startswith("Cisco "):
                model_match = re.search(r"cisco\s+(\S+)", line_stripped, re.IGNORECASE)
                if model_match:
                    model = model_match.group(1)
            elif "uptime is" in line_stripped:
                hostname_match = re.match(r"(\S+)\s+uptime is", line_stripped)
                if hostname_match:
                    hostname = hostname_match.group(1)
                uptime_seconds = _parse_ios_uptime(line_stripped)
            elif "Processor board ID" in line_stripped:
                serial_match = re.search(r"Processor board ID\s+(\S+)", line_stripped)
                if serial_match:
                    serial = serial_match.group(1)

        return {
            "hostname": hostname,
            "model": model,
            "version": version,
            "serial": serial,
            "uptime_seconds": uptime_seconds,
            "vendor": "cisco",
            "platform": "iosxe",
        }

    def get_interfaces(self) -> dict[str, dict]:
        """Get interface status in a vendor-neutral format.

        Uses RESTCONF ``/ietf-interfaces:interfaces`` to get interface
        data including link status, description, speed, MTU, and MAC.

        Returns:
            dict mapping interface name to normalized interface dict.
        """
        if self._restconf:
            try:
                resp = self._restconf.get(_RESTCONF_PATHS["interfaces"])
                resp.raise_for_status()
                data = resp.json()
                interfaces = data.get("ietf-interfaces:interfaces", {}).get("interface", [])
                return self._normalize_interfaces(interfaces)
            except Exception as exc:
                logger.warning("RESTCONF get_interfaces failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show interfaces"])
        return self._normalize_interfaces_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_interfaces(raw: list[dict]) -> dict[str, dict]:
        """Normalize IETF interfaces YANG data to common schema."""
        normalized: dict[str, dict] = {}
        for intf in raw:
            name = intf.get("name", "")
            oper_status = intf.get("oper-status", "down")
            admin_enabled = intf.get("enabled", True)
            description = intf.get("description", "")

            # Speed: IETF uses speed in bits/sec, convert to Mbps
            speed_bps = intf.get("speed", 0)
            if isinstance(speed_bps, int) and speed_bps > 1_000_000:
                speed_mbps = speed_bps // 1_000_000
            else:
                speed_mbps = speed_bps if isinstance(speed_bps, int) else 0

            mtu = intf.get("mtu", 1500)
            if not isinstance(mtu, int):
                mtu = 1500

            # MAC address from ietf-interfaces phys-address
            mac = _normalize_mac(intf.get("phys-address", ""))

            normalized[name] = {
                "is_up": oper_status == "up",
                "is_enabled": bool(admin_enabled),
                "description": description,
                "speed": speed_mbps,
                "mtu": mtu,
                "mac_address": mac,
            }
        return normalized

    @staticmethod
    def _normalize_interfaces_ssh(output: str) -> dict[str, dict]:
        """Parse 'show interfaces' text output into common schema."""
        normalized: dict[str, dict] = {}
        current_intf: str | None = None
        current_data: dict = {}

        for line in output.splitlines():
            # Interface header line: "GigabitEthernet1 is up, line protocol is up"
            intf_match = re.match(r"^(\S+) is (up|down|administratively down), line protocol is (up|down)", line)
            if intf_match:
                if current_intf:
                    normalized[current_intf] = current_data
                current_intf = intf_match.group(1)
                admin_status = intf_match.group(2)
                line_status = intf_match.group(3)
                current_data = {
                    "is_up": line_status == "up",
                    "is_enabled": admin_status != "administratively down",
                    "description": "",
                    "speed": 0,
                    "mtu": 1500,
                    "mac_address": "",
                }
            elif current_intf:
                desc_match = re.match(r"\s+Description:\s+(.+)", line)
                if desc_match:
                    current_data["description"] = desc_match.group(1).strip()
                mtu_match = re.search(r"MTU\s+(\d+)\s+bytes", line)
                if mtu_match:
                    current_data["mtu"] = int(mtu_match.group(1))
                speed_match = re.search(r"BW\s+(\d+)\s+Kbit", line)
                if speed_match:
                    current_data["speed"] = int(speed_match.group(1)) // 1000
                mac_match = re.search(r"address is ([0-9a-fA-F.:]+)", line)
                if mac_match:
                    current_data["mac_address"] = _normalize_mac(mac_match.group(1))

        if current_intf:
            normalized[current_intf] = current_data
        return normalized

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Get BGP summary in a vendor-neutral format.

        Uses RESTCONF ``/Cisco-IOS-XE-bgp-oper:bgp-state-data`` for
        BGP operational state.

        Args:
            vrf: VRF name to query.

        Returns:
            dict with keys: router_id, as_number, peers
        """
        if self._restconf:
            try:
                resp = self._restconf.get(_RESTCONF_PATHS["bgp_oper"])
                resp.raise_for_status()
                data = resp.json()
                return self._normalize_bgp_restconf(data, vrf)
            except Exception as exc:
                logger.warning("RESTCONF get_bgp_summary failed: %s, trying SSH", exc)

        # SSH fallback
        cmd = f"show ip bgp vpnv4 vrf {vrf} summary" if vrf != "default" else "show ip bgp summary"
        result = self._ssh_show([cmd])
        return self._normalize_bgp_ssh(result[0].get("output", ""), vrf)

    @staticmethod
    def _normalize_bgp_restconf(data: dict, vrf: str = "default") -> dict:
        """Normalize IOS-XE BGP RESTCONF operational data."""
        bgp_data = data.get("Cisco-IOS-XE-bgp-oper:bgp-state-data", data)
        router_id = ""
        as_number = 0
        peers: dict[str, dict] = {}

        # Navigate address-families to find matching VRF
        address_families = bgp_data.get("address-families", {}).get("address-family", [])
        for af in address_families:
            af_vrf = af.get("vrf-name", "default")
            if af_vrf != vrf:
                continue

            router_id = af.get("router-id", router_id)
            as_number = int(af.get("local-as", as_number))

            # Extract BGP neighbors
            neighbors = bgp_data.get("neighbors", {}).get("neighbor", [])
            for neighbor in neighbors:
                neighbor_vrf = neighbor.get("vrf-name", "default")
                if neighbor_vrf != vrf:
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
            break  # Found our VRF

        return {
            "router_id": router_id,
            "as_number": as_number,
            "peers": peers,
        }

    @staticmethod
    def _normalize_bgp_ssh(output: str, vrf: str = "default") -> dict:
        """Parse 'show ip bgp summary' text output."""
        router_id = ""
        as_number = 0
        peers: dict[str, dict] = {}

        lines = output.splitlines()
        for line in lines:
            # Router ID and AS: "BGP router identifier 10.0.0.1, local AS number 65000"
            id_match = re.search(r"router identifier\s+(\S+),\s+local AS number\s+(\d+)", line)
            if id_match:
                router_id = id_match.group(1)
                as_number = int(id_match.group(2))
            # Peer lines start with an IP address after the header
            peer_match = re.match(r"^(\d+\.\d+\.\d+\.\d+)\s+", line)
            if peer_match:
                parts = line.split()
                if len(parts) >= 9:
                    peer_ip = parts[0]
                    state_pfx = parts[-1]
                    try:
                        prefixes = int(state_pfx)
                        state = "Established"
                    except ValueError:
                        prefixes = 0
                        state = state_pfx
                    peers[peer_ip] = {
                        "state": state,
                        "prefixes_received": prefixes,
                        "uptime": 0,
                    }

        return {
            "router_id": router_id,
            "as_number": as_number,
            "peers": peers,
        }

    def get_arp_table(self) -> list[dict]:
        """Get ARP table in a vendor-neutral format.

        Uses RESTCONF ``/Cisco-IOS-XE-arp-oper:arp-data`` for ARP
        operational data.

        Returns:
            list of dicts with keys: ip, mac, interface, age
        """
        if self._restconf:
            try:
                resp = self._restconf.get(_RESTCONF_PATHS["arp_oper"])
                resp.raise_for_status()
                data = resp.json()
                return self._normalize_arp_restconf(data)
            except Exception as exc:
                logger.warning("RESTCONF get_arp_table failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show ip arp"])
        return self._normalize_arp_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_arp_restconf(data: dict) -> list[dict]:
        """Normalize IOS-XE ARP RESTCONF operational data."""
        arp_data = data.get("Cisco-IOS-XE-arp-oper:arp-data", data)
        arp_vrf = arp_data.get("arp-vrf", [])
        entries: list[dict] = []

        for vrf in arp_vrf:
            arp_entries = vrf.get("arp-entry", [])
            for entry in arp_entries:
                entries.append(
                    {
                        "ip": entry.get("address", ""),
                        "mac": _normalize_mac(entry.get("hardware", "")),
                        "interface": entry.get("interface", ""),
                        "age": float(entry.get("time", -1)),
                    }
                )

        return entries

    @staticmethod
    def _normalize_arp_ssh(output: str) -> list[dict]:
        """Parse 'show ip arp' text output."""
        entries: list[dict] = []
        for line in output.splitlines():
            # "Internet  10.0.0.1   0   0050.56a0.0001  ARPA   GigabitEthernet1"
            arp_match = re.match(r"Internet\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\S+)\s+ARPA\s+(\S+)", line)
            if arp_match:
                ip_addr = arp_match.group(1)
                age_str = arp_match.group(2)
                mac_raw = arp_match.group(3)
                interface = arp_match.group(4)
                try:
                    age = float(age_str) * 60  # IOS shows age in minutes
                except ValueError:
                    age = -1.0
                entries.append(
                    {
                        "ip": ip_addr,
                        "mac": _normalize_mac(mac_raw),
                        "interface": interface,
                        "age": age,
                    }
                )
        return entries

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Get LLDP neighbor information in a vendor-neutral format.

        Uses RESTCONF ``/Cisco-IOS-XE-lldp-oper:lldp-entries`` for
        LLDP operational data.

        Returns:
            dict mapping local interface name to list of neighbor dicts.
        """
        if self._restconf:
            try:
                resp = self._restconf.get(_RESTCONF_PATHS["lldp_oper"])
                resp.raise_for_status()
                data = resp.json()
                return self._normalize_lldp_restconf(data)
            except Exception as exc:
                logger.warning("RESTCONF get_lldp_neighbors failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show lldp neighbors detail"])
        return self._normalize_lldp_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_lldp_restconf(data: dict) -> dict[str, list[dict]]:
        """Normalize IOS-XE LLDP RESTCONF operational data."""
        lldp_data = data.get("Cisco-IOS-XE-lldp-oper:lldp-entries", data)
        lldp_entries = lldp_data.get("lldp-entry", [])
        normalized: dict[str, list[dict]] = {}

        for entry in lldp_entries:
            local_intf = entry.get("local-interface", "")
            neighbor = {
                "hostname": entry.get("device-id", ""),
                "port": entry.get("connecting-interface", ""),
                "system_description": entry.get("system-desc", ""),
            }
            if local_intf:
                normalized.setdefault(local_intf, []).append(neighbor)

        return normalized

    @staticmethod
    def _normalize_lldp_ssh(output: str) -> dict[str, list[dict]]:
        """Parse 'show lldp neighbors detail' text output."""
        normalized: dict[str, list[dict]] = {}
        local_intf = ""
        hostname = ""
        port = ""
        sys_desc = ""

        for line in output.splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("Local Intf:"):
                # Save previous entry
                if local_intf and hostname:
                    normalized.setdefault(local_intf, []).append(
                        {
                            "hostname": hostname,
                            "port": port,
                            "system_description": sys_desc,
                        }
                    )
                local_intf = line_stripped.split(":", 1)[1].strip()
                hostname = ""
                port = ""
                sys_desc = ""
            elif line_stripped.startswith("System Name:"):
                hostname = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("Port id:"):
                port = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("System Description:"):
                sys_desc = line_stripped.split(":", 1)[1].strip()

        # Save last entry
        if local_intf and hostname:
            normalized.setdefault(local_intf, []).append(
                {
                    "hostname": hostname,
                    "port": port,
                    "system_description": sys_desc,
                }
            )

        return normalized

    def get_lldp_neighbor_detail(self, interface: str = "") -> dict[str, list[dict]]:
        """Get detailed LLDP neighbor TLV data.

        Uses RESTCONF or SSH ``show lldp neighbors detail`` to extract
        extended TLV fields (chassis ID, management address, capabilities).

        Args:
            interface: Filter by local interface name (empty = all).

        Returns:
            dict mapping local interface name to list of neighbor detail dicts.
        """
        if self._restconf:
            try:
                resp = self._restconf.get(_RESTCONF_PATHS["lldp_oper"])
                resp.raise_for_status()
                data = resp.json()
                return self._normalize_lldp_detail_restconf(data, interface)
            except Exception as exc:
                logger.warning("RESTCONF get_lldp_neighbor_detail failed: %s, trying SSH", exc)

        result = self._ssh_show(["show lldp neighbors detail"])
        return self._normalize_lldp_detail_ssh(result[0].get("output", ""), interface)

    @staticmethod
    def _normalize_lldp_detail_restconf(data: dict, intf_filter: str) -> dict[str, list[dict]]:
        """Normalize IOS-XE LLDP RESTCONF data with extended TLV fields."""
        lldp_data = data.get("Cisco-IOS-XE-lldp-oper:lldp-entries", data)
        lldp_entries = lldp_data.get("lldp-entry", [])
        normalized: dict[str, list[dict]] = {}
        for entry in lldp_entries:
            local_intf = entry.get("local-interface", "")
            if intf_filter and local_intf != intf_filter:
                continue
            caps_raw = entry.get("capability", [])
            caps = [c.get("name", "").lower() for c in caps_raw] if isinstance(caps_raw, list) else []
            neighbor = {
                "hostname": entry.get("device-id", ""),
                "port": entry.get("connecting-interface", ""),
                "system_description": entry.get("system-desc", ""),
                "chassis_id": entry.get("chassis-id", ""),
                "management_address": entry.get("mgmt-addr", ""),
                "capabilities": caps,
            }
            if local_intf:
                normalized.setdefault(local_intf, []).append(neighbor)
        return normalized

    @staticmethod
    def _normalize_lldp_detail_ssh(output: str, intf_filter: str) -> dict[str, list[dict]]:
        """Parse 'show lldp neighbors detail' text output with extended TLV fields."""
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
            if line_stripped.startswith("Local Intf:"):
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
            elif line_stripped.startswith("Port id:"):
                port = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("System Description:"):
                sys_desc = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("Chassis id:"):
                chassis_id = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("Management Addresses:"):
                pass  # next line has the address
            elif line_stripped.startswith("IP:"):
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
        """Get device configuration in a vendor-neutral format.

        Uses SSH ``show running-config`` and ``show startup-config``
        since RESTCONF native model returns structured (not text) config.

        Args:
            sanitized: If True, redact passwords and secrets from output.

        Returns:
            dict with keys: running, startup
        """
        running = ""
        startup = ""

        # SSH is used for text config output (RESTCONF returns structured JSON, not CLI text)
        results = self._ssh_show(["show running-config", "show startup-config"])
        running = results[0].get("output", "") if results else ""
        startup = results[1].get("output", "") if len(results) > 1 else ""

        if sanitized:
            from network_mcp.sanitizer import config_sanitizer

            running = config_sanitizer.sanitize(running)
            startup = config_sanitizer.sanitize(startup)

        return {
            "running": running,
            "startup": startup,
        }

    # --- Expanded normalized getters (20 new) ---
    # Transport: SSH for all getters (IOS-XE text parsing is more reliable
    # than the fragmented RESTCONF YANG model coverage across versions).

    def get_vlans(self) -> list[dict]:
        """Get all VLANs. Transport: SSH ``show vlan brief``."""
        try:
            output = self._ssh_exec("show vlan brief")
            return self._parse_vlans_ssh(output)
        except Exception as exc:
            logger.warning("get_vlans failed: %s", exc)
            return []

    @staticmethod
    def _parse_vlans_ssh(output: str) -> list[dict]:
        vlans: list[dict] = []
        for line in output.splitlines():
            m = re.match(r"^(\d+)\s+(\S+)\s+(active|suspend|act/unsup)\s*(.*)", line)
            if m:
                vlan_id = int(m.group(1))
                name = m.group(2)
                status = "active" if m.group(3).startswith("act") else "suspend"
                ports_str = m.group(4).strip()
                interfaces = [p.strip() for p in ports_str.split(",") if p.strip()] if ports_str else []
                vlans.append({"vlan_id": vlan_id, "name": name, "status": status, "interfaces": interfaces})
        return vlans

    def get_vlan_detail(self, vlan_id: int) -> dict:
        """Get detail for a single VLAN. Transport: SSH ``show vlan id``."""
        try:
            output = self._ssh_exec(f"show vlan id {vlan_id}")
            return self._parse_vlan_detail_ssh(output, vlan_id)
        except Exception as exc:
            logger.warning("get_vlan_detail(%s) failed: %s", vlan_id, exc)
            return {"vlan_id": vlan_id, "name": "", "status": "", "interfaces": [], "type": ""}

    @staticmethod
    def _parse_vlan_detail_ssh(output: str, vlan_id: int) -> dict:
        name = ""
        status = ""
        interfaces: list[str] = []
        vlan_type = "static"
        for line in output.splitlines():
            m = re.match(r"^(\d+)\s+(\S+)\s+(active|suspend|act/unsup)\s*(.*)", line)
            if m and int(m.group(1)) == vlan_id:
                name = m.group(2)
                status = "active" if m.group(3).startswith("act") else "suspend"
                ports_str = m.group(4).strip()
                interfaces = [p.strip() for p in ports_str.split(",") if p.strip()] if ports_str else []
            if "Type" in line and "DYNAMIC" in line.upper():
                vlan_type = "dynamic"
        return {"vlan_id": vlan_id, "name": name, "status": status, "interfaces": interfaces, "type": vlan_type}

    def get_interface_counters(self) -> list[dict]:
        """Get traffic counters. Transport: SSH ``show interfaces``."""
        try:
            output = self._ssh_exec("show interfaces")
            return self._parse_interface_counters_ssh(output)
        except Exception as exc:
            logger.warning("get_interface_counters failed: %s", exc)
            return []

    @staticmethod
    def _parse_interface_counters_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        current: str | None = None
        data: dict = {}
        for line in output.splitlines():
            intf_m = re.match(r"^(\S+) is ", line)
            if intf_m:
                if current:
                    results.append({"interface": current, **data})
                current = intf_m.group(1)
                data = {
                    "in_octets": 0,
                    "out_octets": 0,
                    "in_packets": 0,
                    "out_packets": 0,
                    "in_broadcast": 0,
                    "out_broadcast": 0,
                    "in_multicast": 0,
                    "out_multicast": 0,
                }
            elif current:
                for pattern, key in [
                    (r"(\d+) packets input,\s*(\d+) bytes", ("in_packets", "in_octets")),
                    (r"(\d+) packets output,\s*(\d+) bytes", ("out_packets", "out_octets")),
                ]:
                    m = re.search(pattern, line)
                    if m:
                        data[key[0]] = int(m.group(1))
                        data[key[1]] = int(m.group(2))
                bcast_m = re.search(r"(\d+) broadcasts", line)
                if bcast_m and "input" in line.lower():
                    data["in_broadcast"] = int(bcast_m.group(1))
                elif bcast_m:
                    data["out_broadcast"] = int(bcast_m.group(1))
                mcast_m = re.search(r"(\d+) multicasts", line)
                if mcast_m and "input" in line.lower():
                    data["in_multicast"] = int(mcast_m.group(1))
                elif mcast_m:
                    data["out_multicast"] = int(mcast_m.group(1))
        if current:
            results.append({"interface": current, **data})
        return results

    def get_interface_errors(self) -> list[dict]:
        """Get error counters. Transport: SSH ``show interfaces``."""
        try:
            output = self._ssh_exec("show interfaces")
            return self._parse_interface_errors_ssh(output)
        except Exception as exc:
            logger.warning("get_interface_errors failed: %s", exc)
            return []

    @staticmethod
    def _parse_interface_errors_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        current: str | None = None
        data: dict = {}
        for line in output.splitlines():
            intf_m = re.match(r"^(\S+) is ", line)
            if intf_m:
                if current:
                    results.append({"interface": current, **data})
                current = intf_m.group(1)
                data = {"in_errors": 0, "out_errors": 0, "in_crc": 0, "in_frame": 0, "in_runts": 0, "in_giants": 0}
            elif current:
                in_err = re.search(r"(\d+) input errors", line)
                if in_err:
                    data["in_errors"] = int(in_err.group(1))
                out_err = re.search(r"(\d+) output errors", line)
                if out_err:
                    data["out_errors"] = int(out_err.group(1))
                crc_m = re.search(r"(\d+) CRC", line)
                if crc_m:
                    data["in_crc"] = int(crc_m.group(1))
                frame_m = re.search(r"(\d+) frame", line)
                if frame_m:
                    data["in_frame"] = int(frame_m.group(1))
                runt_m = re.search(r"(\d+) runts", line)
                if runt_m:
                    data["in_runts"] = int(runt_m.group(1))
                giant_m = re.search(r"(\d+) giants", line)
                if giant_m:
                    data["in_giants"] = int(giant_m.group(1))
        if current:
            results.append({"interface": current, **data})
        return results

    def get_ip_interfaces(self) -> list[dict]:
        """Get L3 interfaces. Transport: SSH ``show ip interface brief``."""
        try:
            output = self._ssh_exec("show ip interface brief")
            return self._parse_ip_interfaces_ssh(output)
        except Exception as exc:
            logger.warning("get_ip_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _parse_ip_interfaces_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        for line in output.splitlines():
            # "GigabitEthernet1   10.0.0.1   YES manual up   up"
            m = re.match(r"^(\S+)\s+(\d+\.\d+\.\d+\.\d+|unassigned)\s+\S+\s+\S+\s+(\S+)\s+(\S+)", line)
            if m:
                intf = m.group(1)
                ip_addr = m.group(2) if m.group(2) != "unassigned" else ""
                proto_status = m.group(4)
                results.append(
                    {
                        "interface": intf,
                        "ip_address": ip_addr,
                        "subnet": "",
                        "vrf": "",
                        "status": "up" if proto_status == "up" else "down",
                    }
                )
        return results

    def get_transceiver_info(self) -> list[dict]:
        """Get transceiver DOM data. Transport: SSH ``show interfaces transceiver``."""
        try:
            output = self._ssh_exec("show interfaces transceiver")
            return self._parse_transceiver_ssh(output)
        except Exception as exc:
            logger.warning("get_transceiver_info failed: %s", exc)
            return []

    @staticmethod
    def _parse_transceiver_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        # IOS-XE format varies; typical table: "Gi1/0/1  SFP-10G-SR  -2.1  -5.3  35.0"
        for line in output.splitlines():
            m = re.match(r"^(\S+)\s+(\S+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)", line)
            if m:
                try:
                    results.append(
                        {
                            "interface": m.group(1),
                            "type": m.group(2),
                            "tx_power": float(m.group(3)),
                            "rx_power": float(m.group(4)),
                            "temp": float(m.group(5)),
                        }
                    )
                except ValueError:
                    pass
        return results

    def get_ospf_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get OSPF neighbors. Transport: SSH ``show ip ospf neighbor``."""
        try:
            cmd = "show ip ospf neighbor"
            if vrf != "default":
                cmd = f"show ip ospf neighbor vrf {vrf}"
            output = self._ssh_exec(cmd)
            return self._parse_ospf_ssh(output)
        except Exception as exc:
            logger.warning("get_ospf_neighbors failed: %s", exc)
            return []

    @staticmethod
    def _parse_ospf_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        for line in output.splitlines():
            # "10.0.0.2  1  FULL/DR  00:15:30  10.0.0.2  Gi0/0"
            m = re.match(
                r"^(\d+\.\d+\.\d+\.\d+)\s+\d+\s+(\S+)\s+(\S+)\s+\S+\s+(\S+)",
                line.strip(),
            )
            if m:
                state_raw = m.group(2).split("/")[0]
                results.append(
                    {
                        "neighbor_id": m.group(1),
                        "state": state_raw.capitalize(),
                        "interface": m.group(4),
                        "area": "",
                        "uptime": m.group(3),
                    }
                )
        return results

    def get_ospf_interfaces(self, vrf: str = "default") -> list[dict]:
        """Get OSPF-enabled interfaces. Transport: SSH ``show ip ospf interface brief``."""
        try:
            cmd = "show ip ospf interface brief"
            if vrf != "default":
                cmd = f"show ip ospf interface brief vrf {vrf}"
            output = self._ssh_exec(cmd)
            return self._parse_ospf_interfaces_ssh(output)
        except Exception as exc:
            logger.warning("get_ospf_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _parse_ospf_interfaces_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        for line in output.splitlines():
            # "Gi0/0  1  10.0.0.0/24  1  P2P  1"
            m = re.match(
                r"^(\S+)\s+(\d+)\s+\S+\s+(\d+)\s+(\S+)\s+(\d+)",
                line.strip(),
            )
            if m:
                results.append(
                    {
                        "interface": m.group(1),
                        "area": m.group(2),
                        "network_type": m.group(4).lower(),
                        "cost": int(m.group(3)),
                        "neighbor_count": int(m.group(5)),
                    }
                )
        return results

    def get_bgp_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get detailed BGP neighbors. Transport: SSH ``show ip bgp neighbors``."""
        try:
            cmd = "show ip bgp neighbors"
            if vrf != "default":
                cmd = f"show ip bgp neighbors vrf {vrf}"
            output = self._ssh_exec(cmd)
            return self._parse_bgp_neighbors_ssh(output)
        except Exception as exc:
            logger.warning("get_bgp_neighbors failed: %s", exc)
            return []

    @staticmethod
    def _parse_bgp_neighbors_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        current: dict | None = None
        for line in output.splitlines():
            m = re.match(r"^BGP neighbor is (\S+),\s+remote AS (\d+)", line.strip())
            if m:
                if current:
                    results.append(current)
                current = {
                    "neighbor": m.group(1).rstrip(","),
                    "remote_as": int(m.group(2)),
                    "state": "",
                    "prefixes_received": 0,
                    "prefixes_sent": 0,
                    "uptime": "",
                    "description": "",
                }
                continue
            if current is None:
                continue
            stripped = line.strip()
            if stripped.startswith("BGP state ="):
                state_m = re.match(r"BGP state = (\w+)", stripped)
                if state_m:
                    current["state"] = state_m.group(1)
                up_m = re.search(r"up for (\S+)", stripped)
                if up_m:
                    current["uptime"] = up_m.group(1)
            elif "Prefixes Current:" in stripped:
                pf_m = re.search(r"(\d+)\s+(\d+)", stripped)
                if pf_m:
                    current["prefixes_received"] = int(pf_m.group(1))
                    current["prefixes_sent"] = int(pf_m.group(2))
            elif stripped.startswith("Description:"):
                current["description"] = stripped.replace("Description:", "").strip()
        if current:
            results.append(current)
        return results

    def get_route_table(self, protocol: str = "", vrf: str = "default", limit: int = 100) -> list[dict]:
        """Get routing table. Transport: SSH ``show ip route``."""
        try:
            cmd = "show ip route"
            if vrf != "default":
                cmd = f"show ip route vrf {vrf}"
            if protocol:
                cmd += f" {protocol}"
            output = self._ssh_exec(cmd)
            return self._parse_routes_ssh(output, protocol, limit)
        except Exception as exc:
            logger.warning("get_route_table failed: %s", exc)
            return []

    @staticmethod
    def _parse_routes_ssh(output: str, protocol_filter: str, limit: int) -> list[dict]:
        results: list[dict] = []
        proto_map = {"C": "connected", "S": "static", "B": "bgp", "O": "ospf", "i": "isis", "D": "eigrp"}
        for line in output.splitlines():
            m = re.match(r"^([CSBOD*>i\s]+)\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
            if not m:
                m = re.match(r"^([CSBOD*>i]+)\s+(\d+\.\d+\.\d+\.\d+)\s", line)
            if m:
                flags = m.group(1).strip()
                prefix = m.group(2)
                proto_code = flags[0] if flags else ""
                route_proto = proto_map.get(proto_code, proto_code)
                if protocol_filter and route_proto != protocol_filter:
                    continue
                nh_m = re.search(r"via\s+(\S+)", line)
                next_hop = nh_m.group(1).rstrip(",") if nh_m else ""
                if not next_hop:
                    direct_m = re.search(r"directly connected,\s+(\S+)", line, re.IGNORECASE)
                    if direct_m:
                        next_hop = direct_m.group(1)
                metric_m = re.search(r"\[(\d+)/(\d+)\]", line)
                preference = int(metric_m.group(1)) if metric_m else 0
                metric = int(metric_m.group(2)) if metric_m else 0
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
                    break
        return results

    def get_route_summary(self) -> dict:
        """Get route count summary. Transport: SSH ``show ip route summary``."""
        try:
            output = self._ssh_exec("show ip route summary")
            return self._parse_route_summary_ssh(output)
        except Exception as exc:
            logger.warning("get_route_summary failed: %s", exc)
            return {"total": 0, "connected": 0, "static": 0, "bgp": 0, "ospf": 0, "isis": 0}

    @staticmethod
    def _parse_route_summary_ssh(output: str) -> dict:
        result = {"total": 0, "connected": 0, "static": 0, "bgp": 0, "ospf": 0, "isis": 0}
        for line in output.splitlines():
            low = line.strip().lower()
            # "Total of 250 routes"
            total_m = re.search(r"total.*?(\d+)\s+route", low)
            if total_m:
                result["total"] = int(total_m.group(1))
            for proto in ("connected", "static", "bgp", "ospf", "isis"):
                if proto in low:
                    count_m = re.search(r"(\d+)", line.strip())
                    if count_m:
                        result[proto] = int(count_m.group(1))
        return result

    def get_mac_table(self, vlan: int | None = None, limit: int = 500) -> list[dict]:
        """Get MAC address table. Transport: SSH ``show mac address-table``."""
        try:
            cmd = "show mac address-table"
            if vlan is not None:
                cmd += f" vlan {vlan}"
            output = self._ssh_exec(cmd)
            return self._parse_mac_table_ssh(output, limit)
        except Exception as exc:
            logger.warning("get_mac_table failed: %s", exc)
            return []

    @staticmethod
    def _parse_mac_table_ssh(output: str, limit: int) -> list[dict]:
        results: list[dict] = []
        for line in output.splitlines():
            # " 100  001c.7300.0001  DYNAMIC  Gi1/0/1"
            m = re.match(r"\s*(\d+)\s+([0-9a-fA-F.]+)\s+(\S+)\s+(\S+)", line)
            if m:
                results.append(
                    {
                        "mac_address": _normalize_mac(m.group(2)),
                        "vlan": int(m.group(1)),
                        "interface": m.group(4),
                        "type": m.group(3).lower(),
                    }
                )
                if len(results) >= limit:
                    break
        return results

    def get_stp_status(self) -> dict:
        """Get spanning-tree status. Transport: SSH ``show spanning-tree``."""
        try:
            output = self._ssh_exec("show spanning-tree")
            return self._parse_stp_ssh(output)
        except Exception as exc:
            logger.warning("get_stp_status failed: %s", exc)
            return {"mode": "", "root_bridge": "", "instances": []}

    @staticmethod
    def _parse_stp_ssh(output: str) -> dict:
        mode = ""
        root_bridge = ""
        instances: list[dict] = []
        current_inst: str | None = None
        current_root = ""
        current_root_port = ""
        current_priority = 0
        in_root_id = False

        for line in output.splitlines():
            mode_m = re.search(r"Spanning tree enabled protocol\s+(\S+)", line, re.IGNORECASE)
            if mode_m:
                mode = mode_m.group(1).lower()
            inst_m = re.match(r"^(VLAN\d+|MST\d+|RSTP)", line.strip())
            if inst_m:
                if current_inst:
                    instances.append(
                        {
                            "id": current_inst,
                            "root_bridge": current_root,
                            "root_port": current_root_port,
                            "priority": current_priority,
                        }
                    )
                current_inst = inst_m.group(1)
                current_root = ""
                current_root_port = ""
                current_priority = 0
                in_root_id = False
            if "Root ID" in line:
                in_root_id = True
            if "Bridge ID" in line:
                in_root_id = False
            # Address may be on same line as Root ID or on the next line
            addr_m = re.search(r"Address\s+(\S+)", line)
            if addr_m and in_root_id:
                current_root = addr_m.group(1)
                if not root_bridge:
                    root_bridge = current_root
            root_port_m = re.search(r"Root port\s+(\S+)", line, re.IGNORECASE)
            if root_port_m:
                current_root_port = root_port_m.group(1)
            prio_m = re.search(r"Priority\s+(\d+)", line)
            if prio_m and current_inst:
                current_priority = int(prio_m.group(1))

        if current_inst:
            instances.append(
                {
                    "id": current_inst,
                    "root_bridge": current_root,
                    "root_port": current_root_port,
                    "priority": current_priority,
                }
            )
        return {"mode": mode, "root_bridge": root_bridge, "instances": instances}

    def get_port_channels(self) -> list[dict]:
        """Get port-channel summary. Transport: SSH ``show etherchannel summary``."""
        try:
            output = self._ssh_exec("show etherchannel summary")
            return self._parse_port_channels_ssh(output)
        except Exception as exc:
            logger.warning("get_port_channels failed: %s", exc)
            return []

    @staticmethod
    def _parse_port_channels_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        for line in output.splitlines():
            # "1  Po1(SU)  LACP  Gi1/0/1(P)  Gi1/0/2(P)"
            m = re.match(r"^\d+\s+(Po\S+)\((\S+)\)\s+(\S+)\s+(.*)", line)
            if m:
                name = m.group(1)
                flag = m.group(2)
                protocol = m.group(3).lower()
                status = "up" if "U" in flag else "down"
                members_str = m.group(4).strip()
                members: list[dict] = []
                for mem_m in re.finditer(r"(\S+)\((\S)\)", members_str):
                    mem_flag = mem_m.group(2)
                    mem_status = "bundled" if mem_flag == "P" else ("hot-standby" if mem_flag == "H" else "down")
                    members.append({"interface": mem_m.group(1), "status": mem_status})
                results.append({"name": name, "status": status, "protocol": protocol, "members": members})
        return results

    def get_environment(self) -> dict:
        """Get environmental data. Transport: SSH ``show environment all``."""
        try:
            output = self._ssh_exec("show environment all")
            return self._parse_environment_ssh(output)
        except Exception as exc:
            logger.warning("get_environment failed: %s", exc)
            return {"temperature": [], "fans": [], "power": []}

    @staticmethod
    def _parse_environment_ssh(output: str) -> dict:
        temps: list[dict] = []
        fans: list[dict] = []
        power: list[dict] = []
        for line in output.splitlines():
            # Temperature: "CPU  45C  OK"
            temp_m = re.match(r"^\s*(\S+.*?)\s+(\d+)\s*C\s+(\S+)", line)
            if temp_m:
                temps.append(
                    {
                        "sensor": temp_m.group(1).strip(),
                        "value": float(temp_m.group(2)),
                        "status": temp_m.group(3).lower(),
                    }
                )
            # Fan: "FAN 1  OK"
            fan_m = re.match(r"^\s*(FAN\s*\S+|Fan\s*\S+)\s+(OK|FAILED|ok|failed)", line, re.IGNORECASE)
            if fan_m:
                fans.append({"name": fan_m.group(1).strip(), "status": fan_m.group(2).lower()})
            # Power: "PSU 1  OK  150W"
            psu_pat = r"^\s*(PS\S*\s*\d+|PSU\s*\d+|Power Supply\s*\d+)\s+(OK|FAILED|ok|failed)"
            psu_m = re.match(psu_pat, line, re.IGNORECASE)
            if psu_m:
                watts = 0.0
                w_m = re.search(r"(\d+)\s*W", line)
                if w_m:
                    watts = float(w_m.group(1))
                power.append({"name": psu_m.group(1).strip(), "status": psu_m.group(2).lower(), "output_watts": watts})
        return {"temperature": temps, "fans": fans, "power": power}

    def get_ntp_status(self) -> dict:
        """Get NTP status. Transport: SSH ``show ntp status`` + ``show ntp associations``."""
        try:
            status_output = self._ssh_exec("show ntp status")
            assoc_output = self._ssh_exec("show ntp associations")
            return self._parse_ntp_ssh(status_output, assoc_output)
        except Exception as exc:
            logger.warning("get_ntp_status failed: %s", exc)
            return {"synchronized": False, "reference": "", "peers": []}

    @staticmethod
    def _parse_ntp_ssh(status_output: str, assoc_output: str) -> dict:
        synchronized = "synchronized" in status_output.lower() and "unsynchronized" not in status_output.lower()
        reference = ""
        ref_m = re.search(r"reference is\s+(\S+)", status_output)
        if ref_m:
            reference = ref_m.group(1)

        peers: list[dict] = []
        for line in assoc_output.splitlines():
            # "*~10.0.0.100  2  64  377  0.500  1.200"
            m = re.match(r"^[*#\-+~x.o ]*(\d+\.\d+\.\d+\.\d+)\s+.*?(\d+)\s+\d+\s+\S+\s+([\d.\-]+)\s+([\d.\-]+)", line)
            if m:
                try:
                    peers.append(
                        {
                            "peer": m.group(1),
                            "stratum": int(m.group(2)),
                            "offset": float(m.group(3)),
                            "jitter": float(m.group(4)),
                        }
                    )
                except ValueError:
                    pass
        return {"synchronized": synchronized, "reference": reference, "peers": peers}

    def get_cpu_memory(self) -> dict:
        """Get CPU/memory utilization. Transport: SSH ``show processes cpu sorted``."""
        try:
            output = self._ssh_exec("show processes cpu sorted")
            return self._parse_cpu_memory_ssh(output)
        except Exception as exc:
            logger.warning("get_cpu_memory failed: %s", exc)
            return {"cpu_percent": 0.0, "memory_total": 0, "memory_used": 0, "memory_free": 0}

    @staticmethod
    def _parse_cpu_memory_ssh(output: str) -> dict:
        cpu_percent = 0.0
        mem_total = 0
        mem_used = 0
        mem_free = 0
        for line in output.splitlines():
            # "CPU utilization for five seconds: 15%/3%"
            cpu_m = re.search(r"five seconds:\s+(\d+)%", line)
            if cpu_m:
                cpu_percent = float(cpu_m.group(1))
            # "Processor Pool Total: 2048000 Used: 1024000 Free: 1024000"
            mem_m = re.search(r"Total:\s+(\d+)\s+Used:\s+(\d+)\s+Free:\s+(\d+)", line)
            if mem_m:
                mem_total = int(mem_m.group(1))
                mem_used = int(mem_m.group(2))
                mem_free = int(mem_m.group(3))
        return {"cpu_percent": cpu_percent, "memory_total": mem_total, "memory_used": mem_used, "memory_free": mem_free}

    def get_acls(self) -> list[dict]:
        """Get IP access lists. Transport: SSH ``show ip access-lists``."""
        try:
            output = self._ssh_exec("show ip access-lists")
            return self._parse_acls_ssh(output)
        except Exception as exc:
            logger.warning("get_acls failed: %s", exc)
            return []

    @staticmethod
    def _parse_acls_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        current_acl: dict | None = None
        for line in output.splitlines():
            # "Standard IP access list MGMT-ACCESS" or "Extended IP access list WEB-ACL"
            hdr_m = re.match(r"^(Standard|Extended)\s+IP\s+access\s+list\s+(\S+)", line, re.IGNORECASE)
            if hdr_m:
                if current_acl:
                    results.append(current_acl)
                current_acl = {"name": hdr_m.group(2), "type": hdr_m.group(1).lower(), "rules": []}
            elif current_acl:
                # "  10 permit 10.0.0.0/8"
                rule_m = re.match(r"\s+(\d+)\s+(permit|deny)\s+(.*)", line)
                if rule_m:
                    current_acl["rules"].append(
                        {
                            "sequence": int(rule_m.group(1)),
                            "action": rule_m.group(2),
                            "source": rule_m.group(3).strip(),
                        }
                    )
        if current_acl:
            results.append(current_acl)
        return results

    def get_snmp_config(self) -> dict:
        """Get SNMP configuration. Transport: SSH ``show snmp``."""
        try:
            output = self._ssh_exec("show snmp")
            return self._parse_snmp_ssh(output)
        except Exception as exc:
            logger.warning("get_snmp_config failed: %s", exc)
            return {"communities": [], "users": [], "traps": []}

    @staticmethod
    def _parse_snmp_ssh(output: str) -> dict:
        communities: list[dict] = []
        users: list[dict] = []
        traps: list[dict] = []
        for line in output.splitlines():
            # "Community name: public  ... access: read-only"
            comm_m = re.search(r"Community\s+name:\s+(\S+).*?(read-only|read-write)", line, re.IGNORECASE)
            if comm_m:
                access = "ro" if "only" in comm_m.group(2).lower() else "rw"
                communities.append({"name": comm_m.group(1), "access": access})
            # Trap target lines
            trap_m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+.*trap", line, re.IGNORECASE)
            if trap_m:
                traps.append({"host": trap_m.group(1)})
        return {"communities": communities, "users": users, "traps": traps}

    def get_running_config_section(self, section: str) -> str:
        """Get a running-config section. Transport: SSH ``show running-config | section``."""
        try:
            output = self._ssh_exec(f"show running-config | section {section}")
            return output
        except Exception as exc:
            logger.warning("get_running_config_section failed: %s", exc)
            return ""

    def get_startup_config(self) -> str:
        """Get startup configuration. Transport: SSH ``show startup-config``."""
        try:
            output = self._ssh_exec("show startup-config")
            return output
        except Exception as exc:
            logger.warning("get_startup_config failed: %s", exc)
            return ""

    def get_vrfs(self) -> list[dict]:
        """Get all VRFs. Transport: SSH ``show vrf``."""
        try:
            output = self._ssh_exec("show vrf")
            return self._parse_vrfs_ssh(output)
        except Exception as exc:
            logger.warning("get_vrfs failed: %s", exc)
            return []

    @staticmethod
    def _parse_vrfs_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        for line in output.splitlines():
            # "MGMT  65000:1  ipv4,ipv6  Gi0/0"
            m = re.match(r"^\s*(\S+)\s+(\S+)\s+\S+\s*(.*)", line)
            if m and m.group(1).lower() != "name":
                name = m.group(1)
                rd = m.group(2) if m.group(2) != "<not" else ""
                intfs_str = m.group(3).strip()
                interfaces = [i.strip() for i in intfs_str.split(",") if i.strip()] if intfs_str else []
                results.append({"name": name, "rd": rd, "interfaces": interfaces, "route_count": 0})
        return results

    def get_trunk_interfaces(self) -> list[dict]:
        """Get trunk ports. Transport: SSH ``show interfaces trunk``."""
        try:
            output = self._ssh_exec("show interfaces trunk")
            return self._parse_trunks_ssh(output)
        except Exception as exc:
            logger.warning("get_trunk_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _parse_trunks_ssh(output: str) -> list[dict]:
        results: list[dict] = []
        section = ""
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("-"):
                continue
            lower = stripped.lower()
            if lower.startswith("port") and "mode" in lower:
                section = "mode"
                continue
            if lower.startswith("port") and "vlans allowed" in lower:
                section = "allowed"
                continue
            if lower.startswith("port") and "vlans in spanning" in lower:
                section = "active"
                continue
            if lower.startswith("port") and "vlans" in lower:
                section = "other"
                continue
            parts = stripped.split()
            if not parts:
                continue
            intf = parts[0]
            if section == "mode":
                # "Gi0/1  on  802.1q  1  1-4094"
                native = 1
                if len(parts) >= 4:
                    try:
                        native = int(parts[3])
                    except ValueError:
                        pass
                results.append(
                    {
                        "interface": intf,
                        "mode": "trunk",
                        "native_vlan": native,
                        "allowed_vlans": "",
                        "active_vlans": "",
                    }
                )
            elif section == "allowed":
                allowed = parts[1] if len(parts) > 1 else ""
                for entry in results:
                    if entry["interface"] == intf:
                        entry["allowed_vlans"] = allowed
                        break
            elif section == "active":
                active = parts[1] if len(parts) > 1 else ""
                for entry in results:
                    if entry["interface"] == intf:
                        entry["active_vlans"] = active
                        break
        return results

    def get_vrf_detail(self, vrf: str) -> dict:
        """Get detailed VRF information. Transport: SSH ``show vrf detail {vrf}``."""
        try:
            output = self._ssh_exec(f"show vrf detail {vrf}")
            return self._parse_vrf_detail_ssh(vrf, output)
        except Exception as exc:
            logger.warning("get_vrf_detail failed: %s", exc)
            return {}

    @staticmethod
    def _parse_vrf_detail_ssh(vrf: str, output: str) -> dict:
        result: dict = {
            "name": vrf,
            "rd": "",
            "interfaces": [],
            "route_count": 0,
            "import_targets": [],
            "export_targets": [],
        }
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("RD "):
                result["rd"] = stripped.split()[-1] if len(stripped.split()) > 1 else ""
            elif "Import RT:" in stripped or "import" in stripped.lower() and "rt" in stripped.lower():
                parts = stripped.split(":")
                if len(parts) >= 3:
                    result["import_targets"].append(":".join(parts[-2:]).strip())
            elif "Export RT:" in stripped or "export" in stripped.lower() and "rt" in stripped.lower():
                parts = stripped.split(":")
                if len(parts) >= 3:
                    result["export_targets"].append(":".join(parts[-2:]).strip())
            elif stripped.startswith(("Gi", "Te", "Fa", "Lo", "Vl", "Po", "Tu")):
                intf = stripped.split()[0]
                if intf not in result["interfaces"]:
                    result["interfaces"].append(intf)
        return result

    def ping(self, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
        """Execute a ping. Transport: SSH ``ping``."""
        cmd = f"ping vrf {vrf} {destination}" if vrf != "default" else f"ping {destination}"
        if source:
            cmd += f" source {source}"
        cmd += f" repeat {count}"
        try:
            output = self._ssh_exec(cmd)
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
        m = re.search(r"(\d+)\s+packets?\s+transmitted,\s*(\d+)\s+received", output)
        if m:
            result["packets_sent"] = int(m.group(1))
            result["packets_received"] = int(m.group(2))
            sent = result["packets_sent"]
            result["packet_loss_pct"] = round((sent - result["packets_received"]) / sent * 100, 1) if sent else 100.0
            result["success"] = result["packets_received"] > 0
        # IOS-XE: "round-trip min/avg/max = 1/2/3 ms"
        rtt_m = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))
        return result

    def traceroute(self, destination: str, source: str = "", vrf: str = "default") -> dict:
        """Execute a traceroute. Transport: SSH ``traceroute``."""
        cmd = f"traceroute vrf {vrf} {destination}" if vrf != "default" else f"traceroute {destination}"
        if source:
            cmd += f" source {source}"
        try:
            output = self._ssh_exec(cmd)
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
        """Get interface flap events. Transport: SSH ``show logging``."""
        try:
            output = self._ssh_exec("show logging | include changed state|line protocol")
        except Exception:
            return []
        return self._parse_flap_events(output)

    @staticmethod
    def _parse_flap_events(output: str) -> list[dict]:
        events: list[dict] = []
        for line in output.splitlines():
            lower = line.lower()
            if "changed state to" not in lower and "line protocol" not in lower:
                continue
            intf_m = re.search(
                r"(GigabitEthernet\S+|TenGigabitEthernet\S+|FastEthernet\S+|Loopback\S+|Vlan\S+|Port-channel\S+)",
                line,
                re.IGNORECASE,
            )
            interface = intf_m.group(1) if intf_m else "unknown"
            event = "link state change"
            if "changed state to up" in lower or "protocol on.*up" in lower:
                event = "link up"
            elif "changed state to down" in lower or "protocol on.*down" in lower:
                event = "link down"
            ts_m = re.match(r"^(\S+\s+\d+\s+[\d:]+|\d{4}-\d{2}-\d{2}T[\d:.]+|\*?\w+\s+\d+\s+[\d:.]+)", line)
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


# --- Helper functions ---


def _parse_ios_uptime(line: str) -> int:
    """Parse IOS uptime string to seconds.

    Example: "router1 uptime is 2 years, 3 weeks, 4 days, 5 hours, 6 minutes"
    """
    total = 0
    patterns = [
        (r"(\d+)\s+year", 365 * 24 * 3600),
        (r"(\d+)\s+week", 7 * 24 * 3600),
        (r"(\d+)\s+day", 24 * 3600),
        (r"(\d+)\s+hour", 3600),
        (r"(\d+)\s+minute", 60),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, line)
        if match:
            total += int(match.group(1)) * multiplier
    return total
