"""Cisco NX-OS driver — NX-API primary transport with SSH fallback.

Implements the NetworkDriver protocol for Cisco NX-OS devices using
NX-API (via httpx) as the primary transport. Falls back to SSH when
NX-API is unavailable (``feature nxapi`` not enabled or unreachable).

SSH fallback uses scrapli for persistent, secure sessions (replacing
the previous subprocess-based SSH that had security issues including
disabled host key verification and no session reuse).

Requires the ``cisco`` optional extra: ``pip install latticio[cisco]``
SSH fallback requires: ``pip install latticio[ssh]``
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
    from scrapli.driver.core import NXOSDriver as ScrapliNXOSDriver
    from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliConnectionError, ScrapliTimeout

    HAS_SCRAPLI = True
except ImportError:
    ScrapliNXOSDriver = None  # type: ignore[assignment, misc]
    ScrapliAuthenticationFailed = None  # type: ignore[assignment, misc]
    ScrapliConnectionError = None  # type: ignore[assignment, misc]
    ScrapliTimeout = None  # type: ignore[assignment, misc]
    HAS_SCRAPLI = False

logger = logging.getLogger("network-mcp")

# Default idle threshold (seconds) before a keep-alive check is triggered
_DEFAULT_KEEPALIVE_INTERVAL = 60


def _unwrap_table(data: dict, table_key: str, row_key: str) -> list[dict]:
    """Unwrap NX-API TABLE_*/ROW_* nesting pattern.

    NX-API wraps single-element results as a plain dict and multi-element
    results as a list. This helper normalizes both to a list.

    Args:
        data: Parent dict containing the TABLE_* key.
        table_key: The TABLE_* key name (e.g. ``TABLE_interface``).
        row_key: The ROW_* key name (e.g. ``ROW_interface``).

    Returns:
        List of row dicts. Empty list if the key is missing.
    """
    table = data.get(table_key, {})
    if not table:
        return []
    rows = table.get(row_key, [])
    if isinstance(rows, dict):
        return [rows]
    if isinstance(rows, list):
        return rows
    return []


class NxosScrapliTransport:
    """Persistent SSH session using scrapli for NX-OS devices.

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
            raise ImportError("scrapli not installed. Install with: pip install latticio[ssh]")
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
        self._driver = ScrapliNXOSDriver(**driver_kwargs)
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


class NxosDriver:
    """Cisco NX-OS driver using NX-API (primary) with SSH fallback.

    Satisfies the NetworkDriver protocol defined in drivers/base.py.

    NX-API requires ``feature nxapi`` to be configured on the NX-OS device.
    If NX-API is not available, the driver falls back to SSH for command
    execution via scrapli.
    """

    platform: str = "nxos"
    vendor: str = "cisco"

    VENDOR_API_VERSION = "nxapi-1.0"

    def __init__(self) -> None:
        self._nxapi: httpx.Client | None = None
        self._ssh_transport: NxosScrapliTransport | None = None
        self._ssh_host: str | None = None
        self._ssh_username: str | None = None
        self._ssh_password: SecretStr | None = None
        self._ssh_port: int = 22
        self._ssh_private_key: str = ""
        self._ssh_strict_key: bool = True
        self._ssh_known_hosts: str = "~/.ssh/known_hosts"
        self._transport: str = "nxapi"  # "nxapi" or "ssh"
        self._connected: bool = False
        self._last_command_time: float = 0.0
        self._timeout_connect: float = 10.0
        self._timeout_show: float = 30.0
        self._timeout_config: float = 60.0
        self._keepalive_interval: int = _DEFAULT_KEEPALIVE_INTERVAL
        self._connect_transport: str = "nxapi"
        self._connect_kwargs: dict[str, Any] = {}

    # --- Connection lifecycle ---

    def connect(  # noqa: PLR0913
        self,
        host: str,
        transport: str = "nxapi",
        username: str = "admin",
        password: str = "",
        **kwargs: Any,
    ) -> None:
        """Establish connection to an NX-OS device.

        Tries NX-API first (if transport='nxapi'). On failure,
        falls back to SSH automatically via scrapli.

        Args:
            host: Device hostname or IP address.
            transport: Transport method ('nxapi' or 'ssh').
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

        if not HAS_HTTPX and transport == "nxapi":
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
        if transport == "nxapi":
            self._init_nxapi(host, username, password, **kwargs)
        else:
            self._init_ssh_fallback(host, username, password, **kwargs)
        self._last_command_time = time.monotonic()

    def _init_nxapi(self, host: str, username: str, password: str, **kwargs: Any) -> None:
        """Initialize NX-API transport with availability probe."""
        port = kwargs.get("port", 443)
        verify_ssl = kwargs.get("verify_ssl", True)

        base_url = f"https://{host}:{port}"
        self._nxapi = httpx.Client(
            base_url=base_url,
            auth=(username, password),
            verify=verify_ssl,
            headers={"Content-Type": "application/json"},
            timeout=self._timeout_show,
        )

        # Probe NX-API availability with a lightweight show version
        try:
            payload = self._build_nxapi_payload("show version")
            resp = self._nxapi.post("/ins", json=payload)
            resp.raise_for_status()
            self._transport = "nxapi"
            self._connected = True
            logger.debug("NX-API connected to %s:%s", host, port)
        except Exception as exc:
            # Check for auth failures — do NOT fallback to SSH for 401/403
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (401, 403):
                logger.error("NX-API authentication failed on %s (HTTP %d)", host, status_code)
                self._nxapi.close()
                self._nxapi = None
                raise ConnectionError(
                    f"Authentication failed for {host} (HTTP {status_code}). "
                    "Check credentials (NET_USERNAME/NET_PASSWORD)."
                ) from exc
            logger.warning("NX-API unavailable on %s (%s), falling back to SSH", host, exc)
            self._nxapi.close()
            self._nxapi = None
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
                self._ssh_transport = NxosScrapliTransport(
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
                "scrapli not installed — SSH fallback unavailable for %s. Install with: pip install latticio[ssh]",
                host,
            )
            self._ssh_transport = None

        self._connected = True

    def close(self) -> None:
        """Close all transport connections."""
        if self._nxapi is not None:
            try:
                self._nxapi.close()
            except Exception as exc:
                logger.debug("Failed to close NX-API client: %s", exc)
            finally:
                self._nxapi = None
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
        """Query supported features and getter availability for NX-OS."""
        return {
            "config_sessions": False,
            "gnmi": False,
            "restconf": False,
            "nxapi": True,
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
            if self._nxapi:
                payload = self._build_nxapi_payload("show clock")
                resp = self._nxapi.post("/ins", json=payload)
                resp.raise_for_status()
            elif self._ssh_transport and self._ssh_transport.is_alive:
                self._ssh_transport.send_command("show clock")
            else:
                raise ConnectionError("No active transport")
        except Exception:
            logger.info("NX-OS keep-alive failed for %s, reconnecting", self._ssh_host)
            self.reconnect()

    # --- NX-API payload construction ---

    @staticmethod
    def _build_nxapi_payload(command: str, cmd_type: str = "cli_show") -> dict:
        """Build an NX-API JSON-RPC payload for a single command.

        Args:
            command: The CLI command string (or semicolon-separated commands).
            cmd_type: NX-API command type ('cli_show', 'cli_show_ascii', 'cli_conf').

        Returns:
            NX-API JSON payload dict.
        """
        return {
            "ins_api": {
                "version": "1",
                "type": cmd_type,
                "chunk": "0",
                "sid": "1",
                "input": command,
                "output_format": "json",
            }
        }

    def _parse_nxapi_response(self, response_json: dict, host: str | None = None) -> list[dict]:
        """Parse an NX-API response, handling single and multi-command responses.

        Validates the expected NX-API JSON structure before extracting data.

        NX-API returns different structures for single vs. multiple commands:
        - Single command: ``{"ins_api": {"outputs": {"output": {<result>}}}}``
        - Multiple commands: ``{"ins_api": {"outputs": {"output": [{<r1>}, {<r2>}]}}}``

        Each output dict has ``code``, ``msg``, and ``body`` keys.

        Args:
            response_json: The raw JSON response from NX-API.
            host: Device hostname for error messages. Defaults to ``self._ssh_host``.

        Returns:
            List of body dicts (one per command). Error commands return
            ``{"error": <msg>}`` instead of the body.

        Raises:
            ValueError: If the response structure does not match the expected
                NX-API JSON schema (missing ``ins_api``, ``outputs``, or
                ``output`` keys).
        """
        if host is None:
            host = self._ssh_host or "unknown"

        if not isinstance(response_json, dict):
            got = type(response_json).__name__
            raise ValueError(f"Unexpected NX-API response structure from {host}: expected dict, got {got}")

        if "ins_api" not in response_json:
            raise ValueError(f"Unexpected NX-API response structure from {host}: missing key 'ins_api'")

        ins_api = response_json["ins_api"]
        if not isinstance(ins_api, dict):
            raise ValueError(f"Unexpected NX-API response structure from {host}: 'ins_api' is not a dict")

        if "outputs" not in ins_api:
            raise ValueError(f"Unexpected NX-API response structure from {host}: missing key 'outputs'")

        outputs_container = ins_api["outputs"]
        if not isinstance(outputs_container, dict):
            raise ValueError(f"Unexpected NX-API response structure from {host}: 'outputs' is not a dict")

        if "output" not in outputs_container:
            raise ValueError(f"Unexpected NX-API response structure from {host}: missing key 'output'")

        outputs = outputs_container["output"]

        # Single command response: output is a dict, not a list
        if isinstance(outputs, dict):
            outputs = [outputs]
        elif not isinstance(outputs, list):
            raise ValueError(
                f"Unexpected NX-API response structure from {host}: "
                f"'output' is neither a dict nor a list, got {type(outputs).__name__}"
            )

        results: list[dict] = []
        for output in outputs:
            if not isinstance(output, dict):
                results.append({"error": f"NX-API output entry is not a dict: {type(output).__name__}"})
                continue

            code = output.get("code")
            if code is not None:
                code = str(code)
            else:
                code = ""

            msg = output.get("msg", "")
            if not isinstance(msg, str):
                msg = str(msg)

            body = output.get("body", {})

            if code == "200" and msg.lower() == "success":
                if isinstance(body, dict):
                    results.append(body)
                elif isinstance(body, str):
                    results.append({"output": body})
                else:
                    results.append({})
            else:
                results.append({"error": f"NX-API error (code={code}): {msg}"})

        return results

    # --- Raw command execution ---

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands on the NX-OS device.

        For NX-API transport, sends POST to ``/ins`` with ``cli_show`` type.
        For SSH transport, executes commands via SSH.

        Args:
            commands: List of show commands to execute.
            encoding: Output encoding ('json' or 'text').

        Returns:
            List of response dicts, one per command.
        """
        if not self._connected:
            raise ConnectionError("NxosDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        if self._nxapi and encoding == "json":
            return self._nxapi_show(commands)
        if self._nxapi and encoding == "text":
            return self._nxapi_show_text(commands)
        return self._ssh_show(commands)

    def run_config(self, commands: list[str]) -> list[str]:
        """Execute configuration commands on the NX-OS device.

        Uses NX-API with ``cli_conf`` type or SSH for CLI commands.

        Args:
            commands: List of configuration commands.

        Returns:
            List of result strings.
        """
        if not self._connected:
            raise ConnectionError("NxosDriver is not connected. Call connect() first.")
        self._check_alive()
        self._last_command_time = time.monotonic()

        if self._nxapi:
            return self._nxapi_config(commands)
        return self._ssh_config(commands)

    # Backward-compatibility aliases (match pyeapi.Node interface)
    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Alias for run_show() — backward compatibility."""
        return self.run_show(commands, encoding=encoding)

    def config(self, commands: list[str], **kwargs: Any) -> list[str]:
        """Alias for run_config() — backward compatibility."""
        return self.run_config(commands)

    # --- NX-API transport methods ---

    def _nxapi_show(self, commands: list[str]) -> list[dict]:
        """Execute show commands via NX-API with JSON output."""
        results: list[dict] = []
        for cmd in commands:
            try:
                payload = self._build_nxapi_payload(cmd, cmd_type="cli_show")
                resp = self._nxapi.post("/ins", json=payload)  # type: ignore[union-attr]
                resp.raise_for_status()
                parsed = self._parse_nxapi_response(resp.json())
                results.extend(parsed)
            except Exception as exc:
                logger.warning("NX-API show '%s' failed: %s", cmd, exc)
                results.append({"error": str(exc)})
        return results

    def _nxapi_show_text(self, commands: list[str]) -> list[dict]:
        """Execute show commands via NX-API with ASCII text output."""
        results: list[dict] = []
        for cmd in commands:
            try:
                payload = self._build_nxapi_payload(cmd, cmd_type="cli_show_ascii")
                resp = self._nxapi.post("/ins", json=payload)  # type: ignore[union-attr]
                resp.raise_for_status()
                parsed = self._parse_nxapi_response(resp.json())
                results.extend(parsed)
            except Exception as exc:
                logger.warning("NX-API show-text '%s' failed: %s", cmd, exc)
                results.append({"error": str(exc)})
        return results

    def _nxapi_config(self, commands: list[str]) -> list[str]:
        """Execute configuration commands via NX-API with cli_conf type.

        Note: NX-API cli_conf does not guarantee atomic application — partial
        config may be applied if a command in the batch fails.
        """
        results: list[str] = []
        cmd_str = " ; ".join(commands)
        try:
            payload = self._build_nxapi_payload(cmd_str, cmd_type="cli_conf")
            resp = self._nxapi.post("/ins", json=payload)  # type: ignore[union-attr]
            resp.raise_for_status()
            parsed = self._parse_nxapi_response(resp.json())
            for p in parsed:
                if "error" in p:
                    results.append(p["error"])
                else:
                    results.append("Success")
        except Exception as exc:
            results.append(f"Error: {exc}")
        return results

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
            raise ConnectionError("SSH transport not available. Install scrapli: pip install latticio[ssh]")
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
            raise ConnectionError("SSH transport not available. Install scrapli: pip install latticio[ssh]")

        try:
            return self._ssh_transport.send_command(command)
        except Exception as exc:
            if HAS_SCRAPLI and ScrapliTimeout is not None and isinstance(exc, ScrapliTimeout):
                raise TimeoutError(f"SSH command timed out: {command}") from exc
            raise RuntimeError(f"SSH command failed: {exc}") from exc

    # --- Normalized getters (NetworkDriver protocol) ---

    def get_facts(self) -> dict:
        """Get device facts in a vendor-neutral format.

        Uses NX-API ``show version`` to extract hostname, model, version,
        serial, and uptime.

        Returns:
            dict with keys: hostname, model, version, serial,
            uptime_seconds, vendor, platform
        """
        if self._nxapi:
            try:
                payload = self._build_nxapi_payload("show version")
                resp = self._nxapi.post("/ins", json=payload)
                resp.raise_for_status()
                parsed = self._parse_nxapi_response(resp.json())
                if parsed and "error" not in parsed[0]:
                    return self._normalize_facts_nxapi(parsed[0])
            except Exception as exc:
                logger.warning("NX-API get_facts failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show version"])
        return self._normalize_facts_ssh(result[0].get("output", ""))

    def _normalize_facts_nxapi(self, data: dict) -> dict:
        """Normalize NX-API show version data to facts schema."""
        # NX-OS show version JSON keys
        hostname = data.get("host_name", "")
        model = data.get("chassis_id", "")
        version = data.get("nxos_ver_str", data.get("sys_ver_str", ""))
        serial = data.get("proc_board_id", "")

        # Uptime: NX-API provides kern_uptm_days, kern_uptm_hrs, kern_uptm_mins, kern_uptm_secs
        uptime_days = int(data.get("kern_uptm_days", 0))
        uptime_hrs = int(data.get("kern_uptm_hrs", 0))
        uptime_mins = int(data.get("kern_uptm_mins", 0))
        uptime_secs = int(data.get("kern_uptm_secs", 0))
        uptime_seconds = (uptime_days * 86400) + (uptime_hrs * 3600) + (uptime_mins * 60) + uptime_secs

        return {
            "hostname": hostname,
            "model": model,
            "version": version,
            "serial": serial,
            "uptime_seconds": uptime_seconds,
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
            # "Cisco Nexus Operating System (NX-OS) Software" or "NXOS: version 10.3(2)"
            if "NXOS" in line_stripped or "system:" in line_stripped.lower():
                version_match = re.search(r"version\s+(\S+)", line_stripped, re.IGNORECASE)
                if version_match:
                    version = version_match.group(1)
            elif "Device name:" in line_stripped:
                hostname = line_stripped.split(":", 1)[1].strip()
            elif "cisco" in line_stripped.lower() and ("Nexus" in line_stripped or "nexus" in line_stripped):
                model_match = re.search(r"cisco\s+(Nexus\s*\S+)", line_stripped, re.IGNORECASE)
                if model_match:
                    model = model_match.group(1)
            elif "Processor Board ID" in line_stripped:
                serial_match = re.search(r"Processor Board ID\s+(\S+)", line_stripped)
                if serial_match:
                    serial = serial_match.group(1)
            elif "uptime is" in line_stripped:
                uptime_seconds = _parse_nxos_uptime(line_stripped)

        return {
            "hostname": hostname,
            "model": model,
            "version": version,
            "serial": serial,
            "uptime_seconds": uptime_seconds,
            "vendor": "cisco",
            "platform": "nxos",
        }

    def get_interfaces(self) -> dict[str, dict]:
        """Get interface status in a vendor-neutral format.

        Uses NX-API ``show interface`` and normalizes the
        ``TABLE_interface.ROW_interface`` response.

        Returns:
            dict mapping interface name to normalized interface dict.
        """
        if self._nxapi:
            try:
                payload = self._build_nxapi_payload("show interface")
                resp = self._nxapi.post("/ins", json=payload)
                resp.raise_for_status()
                parsed = self._parse_nxapi_response(resp.json())
                if parsed and "error" not in parsed[0]:
                    return self._normalize_interfaces_nxapi(parsed[0])
            except Exception as exc:
                logger.warning("NX-API get_interfaces failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show interface"])
        return self._normalize_interfaces_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_interfaces_nxapi(data: dict) -> dict[str, dict]:
        """Normalize NX-API show interface TABLE_interface data."""
        rows = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        normalized: dict[str, dict] = {}

        for intf in rows:
            name = intf.get("interface", "")
            if not name:
                continue

            state = intf.get("state", "down")
            admin_state = intf.get("admin_state", "down")

            # Speed: NX-API returns speed in Kbit/s (e.g. 1000000 for 1Gbps)
            speed_raw = intf.get("eth_speed", "0")
            speed_mbps = _parse_speed(speed_raw)

            mtu = intf.get("eth_mtu", 1500)
            if isinstance(mtu, str):
                try:
                    mtu = int(mtu)
                except ValueError:
                    mtu = 1500

            mac = _normalize_mac(intf.get("eth_hw_addr", intf.get("eth_bia_addr", "")))
            description = intf.get("desc", "")

            normalized[name] = {
                "is_up": state == "up",
                "is_enabled": admin_state == "up",
                "description": description,
                "speed": speed_mbps,
                "mtu": mtu,
                "mac_address": mac,
            }

        return normalized

    @staticmethod
    def _normalize_interfaces_ssh(output: str) -> dict[str, dict]:
        """Parse 'show interface' text output into common schema."""
        normalized: dict[str, dict] = {}
        current_intf: str | None = None
        current_data: dict = {}

        for line in output.splitlines():
            # Interface header: "Ethernet1/1 is up"
            intf_match = re.match(r"^(\S+) is (up|down)(,\s+line protocol is (up|down))?", line)
            if intf_match:
                if current_intf:
                    normalized[current_intf] = current_data
                current_intf = intf_match.group(1)
                link_status = intf_match.group(2)
                current_data = {
                    "is_up": link_status == "up",
                    "is_enabled": True,
                    "description": "",
                    "speed": 0,
                    "mtu": 1500,
                    "mac_address": "",
                }
            elif current_intf:
                if "admin state is down" in line.lower():
                    current_data["is_enabled"] = False
                desc_match = re.match(r"\s+Description:\s+(.+)", line)
                if desc_match:
                    current_data["description"] = desc_match.group(1).strip()
                mtu_match = re.search(r"MTU\s+(\d+)\s+bytes", line)
                if mtu_match:
                    current_data["mtu"] = int(mtu_match.group(1))
                speed_match = re.search(r"BW\s+(\d+)\s+Kbit", line)
                if speed_match:
                    current_data["speed"] = int(speed_match.group(1)) // 1000
                mac_match = re.search(r"address(?:\s+is)?\s+([0-9a-fA-F.:]+)", line)
                if mac_match:
                    current_data["mac_address"] = _normalize_mac(mac_match.group(1))

        if current_intf:
            normalized[current_intf] = current_data
        return normalized

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Get BGP summary in a vendor-neutral format.

        Uses NX-API ``show ip bgp summary`` and normalizes the deeply
        nested ``TABLE_vrf.ROW_vrf.TABLE_af.ROW_af`` structure.

        Args:
            vrf: VRF name to query.

        Returns:
            dict with keys: router_id, as_number, peers
        """
        if self._nxapi:
            try:
                cmd = f"show ip bgp summary vrf {vrf}" if vrf != "default" else "show ip bgp summary"
                payload = self._build_nxapi_payload(cmd)
                resp = self._nxapi.post("/ins", json=payload)
                resp.raise_for_status()
                parsed = self._parse_nxapi_response(resp.json())
                if parsed and "error" not in parsed[0]:
                    return self._normalize_bgp_nxapi(parsed[0], vrf)
            except Exception as exc:
                logger.warning("NX-API get_bgp_summary failed: %s, trying SSH", exc)

        # SSH fallback
        cmd = f"show ip bgp summary vrf {vrf}" if vrf != "default" else "show ip bgp summary"
        result = self._ssh_show([cmd])
        return self._normalize_bgp_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_bgp_nxapi(data: dict, vrf: str = "default") -> dict:
        """Normalize NX-API BGP summary with TABLE_vrf/ROW_vrf nesting."""
        router_id = ""
        as_number = 0
        peers: dict[str, dict] = {}

        vrf_rows = _unwrap_table(data, "TABLE_vrf", "ROW_vrf")
        for vrf_row in vrf_rows:
            vrf_name = vrf_row.get("vrf-name-out", vrf_row.get("vrf_name_out", "default"))
            if vrf_name != vrf:
                continue

            router_id = vrf_row.get("router-id", vrf_row.get("router_id", ""))
            as_number = int(vrf_row.get("local-as", vrf_row.get("local_as", 0)))

            # Navigate TABLE_af -> ROW_af -> TABLE_saf -> ROW_saf -> TABLE_neighbor -> ROW_neighbor
            af_rows = _unwrap_table(vrf_row, "TABLE_af", "ROW_af")
            for af_row in af_rows:
                saf_rows = _unwrap_table(af_row, "TABLE_saf", "ROW_saf")
                for saf_row in saf_rows:
                    neighbor_rows = _unwrap_table(saf_row, "TABLE_neighbor", "ROW_neighbor")
                    for neighbor in neighbor_rows:
                        peer_ip = neighbor.get("neighborid", "")
                        if not peer_ip:
                            continue
                        state = neighbor.get("state", "Unknown")
                        # NX-OS uses "Established" for active peers
                        prefixes = 0
                        if state.lower() == "established":
                            prefixes = int(neighbor.get("prefixreceived", 0))

                        # Uptime: NX-API provides resettime or established time
                        uptime_str = neighbor.get("resettime", "")
                        uptime = _parse_bgp_uptime(uptime_str) if uptime_str else 0

                        peers[peer_ip] = {
                            "state": state,
                            "prefixes_received": prefixes,
                            "uptime": uptime,
                        }
            break  # Found our VRF

        return {
            "router_id": router_id,
            "as_number": as_number,
            "peers": peers,
        }

    @staticmethod
    def _normalize_bgp_ssh(output: str) -> dict:
        """Parse 'show ip bgp summary' text output."""
        router_id = ""
        as_number = 0
        peers: dict[str, dict] = {}

        for line in output.splitlines():
            id_match = re.search(r"router identifier\s+(\S+),\s+local AS number\s+(\d+)", line)
            if id_match:
                router_id = id_match.group(1)
                as_number = int(id_match.group(2))
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

        Uses NX-API ``show ip arp`` and normalizes the
        ``TABLE_vrf.ROW_vrf.TABLE_adj.ROW_adj`` response.

        Returns:
            list of dicts with keys: ip, mac, interface, age
        """
        if self._nxapi:
            try:
                payload = self._build_nxapi_payload("show ip arp")
                resp = self._nxapi.post("/ins", json=payload)
                resp.raise_for_status()
                parsed = self._parse_nxapi_response(resp.json())
                if parsed and "error" not in parsed[0]:
                    return self._normalize_arp_nxapi(parsed[0])
            except Exception as exc:
                logger.warning("NX-API get_arp_table failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show ip arp"])
        return self._normalize_arp_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_arp_nxapi(data: dict) -> list[dict]:
        """Normalize NX-API ARP table with TABLE_vrf/ROW_vrf nesting."""
        entries: list[dict] = []
        vrf_rows = _unwrap_table(data, "TABLE_vrf", "ROW_vrf")
        for vrf_row in vrf_rows:
            adj_rows = _unwrap_table(vrf_row, "TABLE_adj", "ROW_adj")
            for adj in adj_rows:
                ip_addr = adj.get("ip-addr-out", "")
                mac = _normalize_mac(adj.get("mac", ""))
                interface = adj.get("intf-out", "")
                age_str = adj.get("time-stamp", "")
                age = _parse_arp_age(age_str) if age_str else -1.0

                entries.append(
                    {
                        "ip": ip_addr,
                        "mac": mac,
                        "interface": interface,
                        "age": age,
                    }
                )
        return entries

    @staticmethod
    def _normalize_arp_ssh(output: str) -> list[dict]:
        """Parse 'show ip arp' text output."""
        entries: list[dict] = []
        for line in output.splitlines():
            # "10.0.0.2  00:15:32  0050.56a0.0010  Ethernet1/1"
            arp_match = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\S+)\s+\S+\s+(\S+)", line)
            if arp_match:
                ip_addr = arp_match.group(1)
                age_str = arp_match.group(2)
                mac_raw = arp_match.group(3)
                interface = arp_match.group(4)
                age = _parse_arp_age(age_str)
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

        Uses NX-API ``show lldp neighbors detail`` and normalizes the
        ``TABLE_nbor_detail.ROW_nbor_detail`` response.

        Returns:
            dict mapping local interface name to list of neighbor dicts.
        """
        if self._nxapi:
            try:
                payload = self._build_nxapi_payload("show lldp neighbors detail")
                resp = self._nxapi.post("/ins", json=payload)
                resp.raise_for_status()
                parsed = self._parse_nxapi_response(resp.json())
                if parsed and "error" not in parsed[0]:
                    return self._normalize_lldp_nxapi(parsed[0])
            except Exception as exc:
                logger.warning("NX-API get_lldp_neighbors failed: %s, trying SSH", exc)

        # SSH fallback
        result = self._ssh_show(["show lldp neighbors detail"])
        return self._normalize_lldp_ssh(result[0].get("output", ""))

    @staticmethod
    def _normalize_lldp_nxapi(data: dict) -> dict[str, list[dict]]:
        """Normalize NX-API LLDP neighbors detail data."""
        rows = _unwrap_table(data, "TABLE_nbor_detail", "ROW_nbor_detail")
        normalized: dict[str, list[dict]] = {}

        for entry in rows:
            local_intf = entry.get("l_port_id", "")
            neighbor = {
                "hostname": entry.get("sys_name", ""),
                "port": entry.get("port_id", ""),
                "system_description": entry.get("sys_desc", ""),
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
            if line_stripped.startswith("Local Port id:"):
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

        Uses NX-API ``show lldp neighbors detail`` and extracts
        extended TLV fields (chassis ID, management address, capabilities).

        Args:
            interface: Filter by local interface name (empty = all).

        Returns:
            dict mapping local interface name to list of neighbor detail dicts.
        """
        if self._nxapi:
            try:
                payload = self._build_nxapi_payload("show lldp neighbors detail")
                resp = self._nxapi.post("/ins", json=payload)
                resp.raise_for_status()
                parsed = self._parse_nxapi_response(resp.json())
                if parsed and "error" not in parsed[0]:
                    return self._normalize_lldp_detail_nxapi(parsed[0], interface)
            except Exception as exc:
                logger.warning("NX-API get_lldp_neighbor_detail failed: %s, trying SSH", exc)

        result = self._ssh_show(["show lldp neighbors detail"])
        return self._normalize_lldp_detail_ssh(result[0].get("output", ""), interface)

    @staticmethod
    def _normalize_lldp_detail_nxapi(data: dict, intf_filter: str) -> dict[str, list[dict]]:
        """Normalize NX-API LLDP detail data with extended TLV fields."""
        rows = _unwrap_table(data, "TABLE_nbor_detail", "ROW_nbor_detail")
        normalized: dict[str, list[dict]] = {}
        for entry in rows:
            local_intf = entry.get("l_port_id", "")
            if intf_filter and local_intf != intf_filter:
                continue
            caps_raw = entry.get("system_capability", "")
            caps = [c.strip().lower() for c in caps_raw.split(",") if c.strip()] if caps_raw else []
            neighbor = {
                "hostname": entry.get("sys_name", ""),
                "port": entry.get("port_id", ""),
                "system_description": entry.get("sys_desc", ""),
                "chassis_id": entry.get("chassis_id", ""),
                "management_address": entry.get("mgmt_addr", ""),
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
            if line_stripped.startswith("Local Port id:"):
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
        """Get device configuration in a vendor-neutral format.

        Uses NX-API with ``cli_show_ascii`` for text output, or SSH
        for ``show running-config`` and ``show startup-config``.

        Args:
            sanitized: If True, redact passwords and secrets from output.

        Returns:
            dict with keys: running, startup
        """
        running = ""
        startup = ""

        if self._nxapi:
            try:
                for cmd, key in [("show running-config", "running"), ("show startup-config", "startup")]:
                    payload = self._build_nxapi_payload(cmd, cmd_type="cli_show_ascii")
                    resp = self._nxapi.post("/ins", json=payload)
                    resp.raise_for_status()
                    parsed = self._parse_nxapi_response(resp.json())
                    if parsed and "error" not in parsed[0]:
                        text = parsed[0].get("output", parsed[0].get("body", ""))
                        if isinstance(text, str):
                            if key == "running":
                                running = text
                            else:
                                startup = text
                if running or startup:
                    if sanitized:
                        from network_mcp.sanitizer import config_sanitizer

                        running = config_sanitizer.sanitize(running)
                        startup = config_sanitizer.sanitize(startup)
                    return {"running": running, "startup": startup}
            except Exception as exc:
                logger.warning("NX-API get_config failed: %s, trying SSH", exc)

        # SSH fallback
        results = self._ssh_show(["show running-config", "show startup-config"])
        running = results[0].get("output", "") if results else ""
        startup = results[1].get("output", "") if len(results) > 1 else ""

        if sanitized:
            from network_mcp.sanitizer import config_sanitizer

            running = config_sanitizer.sanitize(running)
            startup = config_sanitizer.sanitize(startup)

        return {"running": running, "startup": startup}

    # --- Expanded normalized getters (20 new) ---
    # Transport: NX-API JSON for all getters (NX-OS has excellent structured
    # JSON output). SSH fallback for text-only commands.

    def _nxapi_command(self, command: str, cmd_type: str = "cli_show") -> dict:
        """Execute a single NX-API command and return the parsed body dict."""
        if not self._nxapi:
            raise ConnectionError("NX-API transport not available")
        payload = self._build_nxapi_payload(command, cmd_type=cmd_type)
        resp = self._nxapi.post("/ins", json=payload)
        resp.raise_for_status()
        parsed = self._parse_nxapi_response(resp.json())
        if not parsed:
            return {}
        if "error" in parsed[0]:
            raise RuntimeError(parsed[0]["error"])
        return parsed[0]

    def _nxapi_command_text(self, command: str) -> str:
        """Execute a single NX-API command and return ASCII text output."""
        data = self._nxapi_command(command, cmd_type="cli_show_ascii")
        return data.get("output", data.get("body", ""))

    def get_vlans(self) -> list[dict]:
        """Get all VLANs. Transport: NX-API ``show vlan``."""
        try:
            data = self._nxapi_command("show vlan")
            return self._normalize_vlans_nxapi(data)
        except Exception as exc:
            logger.warning("get_vlans failed: %s", exc)
            return []

    @staticmethod
    def _normalize_vlans_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_vlanbrief", "ROW_vlanbrief")
        results: list[dict] = []
        for row in rows:
            vlan_id = int(row.get("vlanshowbr-vlanid", 0))
            name = row.get("vlanshowbr-vlanname", "")
            status = "active" if row.get("vlanshowbr-vlanstate", "").startswith("act") else "suspend"
            ports_str = row.get("vlanshowplist-ifidx", "")
            interfaces = [p.strip() for p in ports_str.split(",") if p.strip()] if ports_str else []
            results.append({"vlan_id": vlan_id, "name": name, "status": status, "interfaces": interfaces})
        return results

    def get_vlan_detail(self, vlan_id: int) -> dict:
        """Get detail for a single VLAN. Transport: NX-API ``show vlan id``."""
        try:
            data = self._nxapi_command(f"show vlan id {vlan_id}")
            rows = _unwrap_table(data, "TABLE_vlanbriefid", "ROW_vlanbriefid")
            if not rows:
                rows = _unwrap_table(data, "TABLE_vlanbrief", "ROW_vlanbrief")
            if not rows:
                return {"vlan_id": vlan_id, "name": "", "status": "", "interfaces": [], "type": ""}
            row = rows[0]
            name = row.get("vlanshowbr-vlanname", "")
            status = "active" if row.get("vlanshowbr-vlanstate", "").startswith("act") else "suspend"
            ports_str = row.get("vlanshowplist-ifidx", "")
            interfaces = [p.strip() for p in ports_str.split(",") if p.strip()] if ports_str else []
            vlan_type = row.get("vlanshowbr-vlantype", "static")
            if "dynamic" in str(vlan_type).lower():
                vlan_type = "dynamic"
            else:
                vlan_type = "static"
            return {"vlan_id": vlan_id, "name": name, "status": status, "interfaces": interfaces, "type": vlan_type}
        except Exception as exc:
            logger.warning("get_vlan_detail(%s) failed: %s", vlan_id, exc)
            return {"vlan_id": vlan_id, "name": "", "status": "", "interfaces": [], "type": ""}

    def get_interface_counters(self) -> list[dict]:
        """Get traffic counters. Transport: NX-API ``show interface``."""
        try:
            data = self._nxapi_command("show interface")
            return self._normalize_counters_nxapi(data)
        except Exception as exc:
            logger.warning("get_interface_counters failed: %s", exc)
            return []

    @staticmethod
    def _normalize_counters_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        results: list[dict] = []
        for intf in rows:
            name = intf.get("interface", "")
            if not name:
                continue
            results.append(
                {
                    "interface": name,
                    "in_octets": int(intf.get("eth_inbytes", 0)),
                    "out_octets": int(intf.get("eth_outbytes", 0)),
                    "in_packets": int(intf.get("eth_inucast", 0)),
                    "out_packets": int(intf.get("eth_outucast", 0)),
                    "in_broadcast": int(intf.get("eth_inbcast", 0)),
                    "out_broadcast": int(intf.get("eth_outbcast", 0)),
                    "in_multicast": int(intf.get("eth_inmcast", 0)),
                    "out_multicast": int(intf.get("eth_outmcast", 0)),
                }
            )
        return results

    def get_interface_errors(self) -> list[dict]:
        """Get error counters. Transport: NX-API ``show interface``."""
        try:
            data = self._nxapi_command("show interface")
            return self._normalize_errors_nxapi(data)
        except Exception as exc:
            logger.warning("get_interface_errors failed: %s", exc)
            return []

    @staticmethod
    def _normalize_errors_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        results: list[dict] = []
        for intf in rows:
            name = intf.get("interface", "")
            if not name:
                continue
            results.append(
                {
                    "interface": name,
                    "in_errors": int(intf.get("eth_inerr", 0)),
                    "out_errors": int(intf.get("eth_outerr", 0)),
                    "in_crc": int(intf.get("eth_crc", 0)),
                    "in_frame": int(intf.get("eth_frame", 0)),
                    "in_runts": int(intf.get("eth_runts", 0)),
                    "in_giants": int(intf.get("eth_giants", 0)),
                }
            )
        return results

    def get_ip_interfaces(self) -> list[dict]:
        """Get L3 interfaces. Transport: NX-API ``show ip interface brief``."""
        try:
            data = self._nxapi_command("show ip interface brief")
            return self._normalize_ip_intfs_nxapi(data)
        except Exception as exc:
            logger.warning("get_ip_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _normalize_ip_intfs_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_intf", "ROW_intf")
        results: list[dict] = []
        for row in rows:
            intf = row.get("intf-name", "")
            ip_addr = row.get("prefix", "")
            subnet = ""
            if "/" in ip_addr:
                parts = ip_addr.split("/")
                ip_addr = parts[0]
                subnet = parts[1]
            vrf = row.get("vrf-name-out", "")
            proto_status = row.get("proto-state", "down")
            results.append(
                {
                    "interface": intf,
                    "ip_address": ip_addr,
                    "subnet": subnet,
                    "vrf": vrf,
                    "status": "up" if proto_status.lower() == "up" else "down",
                }
            )
        return results

    def get_transceiver_info(self) -> list[dict]:
        """Get transceiver DOM data. Transport: NX-API ``show interface transceiver``."""
        try:
            data = self._nxapi_command("show interface transceiver")
            return self._normalize_transceiver_nxapi(data)
        except Exception as exc:
            logger.warning("get_transceiver_info failed: %s", exc)
            return []

    @staticmethod
    def _normalize_transceiver_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        results: list[dict] = []
        for row in rows:
            intf = row.get("interface", "")
            if not intf:
                continue
            results.append(
                {
                    "interface": intf,
                    "type": row.get("type", row.get("sfp", "")),
                    "tx_power": float(row.get("tx_pwr", 0.0)),
                    "rx_power": float(row.get("rx_pwr", 0.0)),
                    "temp": float(row.get("temperature", row.get("temp", 0.0))),
                }
            )
        return results

    def get_ospf_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get OSPF neighbors. Transport: NX-API ``show ip ospf neighbors``."""
        try:
            cmd = f"show ip ospf neighbors vrf {vrf}" if vrf != "default" else "show ip ospf neighbors"
            data = self._nxapi_command(cmd)
            return self._normalize_ospf_nxapi(data)
        except Exception as exc:
            logger.warning("get_ospf_neighbors failed: %s", exc)
            return []

    @staticmethod
    def _normalize_ospf_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_ctx", "ROW_ctx")
        results: list[dict] = []
        for ctx in rows:
            nbr_rows = _unwrap_table(ctx, "TABLE_nbr", "ROW_nbr")
            for nbr in nbr_rows:
                results.append(
                    {
                        "neighbor_id": nbr.get("rid", ""),
                        "state": nbr.get("state", "").split("/")[0].capitalize(),
                        "interface": nbr.get("intf", ""),
                        "area": nbr.get("area", ""),
                        "uptime": nbr.get("uptime", ""),
                    }
                )
        return results

    def get_ospf_interfaces(self, vrf: str = "default") -> list[dict]:
        """Get OSPF-enabled interfaces. Transport: NX-API ``show ip ospf interface brief``."""
        try:
            cmd = f"show ip ospf interface brief vrf {vrf}" if vrf != "default" else "show ip ospf interface brief"
            data = self._nxapi_command(cmd)
            return self._normalize_ospf_interfaces_nxapi(data)
        except Exception as exc:
            logger.warning("get_ospf_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _normalize_ospf_interfaces_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_ctx", "ROW_ctx")
        results: list[dict] = []
        for ctx in rows:
            intf_rows = _unwrap_table(ctx, "TABLE_intf", "ROW_intf")
            for intf in intf_rows:
                results.append(
                    {
                        "interface": intf.get("ifname", ""),
                        "area": str(intf.get("area", "")),
                        "network_type": intf.get("type", "").lower(),
                        "cost": int(intf.get("cost", 0)),
                        "neighbor_count": int(intf.get("nbr_adjs", intf.get("nbr_count", 0))),
                    }
                )
        return results

    def get_bgp_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get detailed BGP neighbors. Transport: NX-API ``show ip bgp neighbors``."""
        try:
            cmd = f"show ip bgp neighbors vrf {vrf}" if vrf != "default" else "show ip bgp neighbors"
            data = self._nxapi_command(cmd)
            return self._normalize_bgp_neighbors_nxapi(data)
        except Exception as exc:
            logger.warning("get_bgp_neighbors failed: %s", exc)
            return []

    @staticmethod
    def _normalize_bgp_neighbors_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_neighbor", "ROW_neighbor")
        results: list[dict] = []
        for nbr in rows:
            results.append(
                {
                    "neighbor": nbr.get("neighbor-id", ""),
                    "remote_as": int(nbr.get("remoteas", 0)),
                    "state": nbr.get("state", ""),
                    "prefixes_received": int(nbr.get("prefixesrcvd", 0)),
                    "prefixes_sent": int(nbr.get("prefixessent", 0)),
                    "uptime": str(nbr.get("uptimeepoch", nbr.get("up", ""))),
                    "description": nbr.get("description", ""),
                }
            )
        return results

    def get_route_table(self, protocol: str = "", vrf: str = "default", limit: int = 100) -> list[dict]:
        """Get routing table. Transport: NX-API ``show ip route``."""
        try:
            cmd = f"show ip route vrf {vrf}" if vrf != "default" else "show ip route"
            data = self._nxapi_command(cmd)
            return self._normalize_routes_nxapi(data, protocol, limit)
        except Exception as exc:
            logger.warning("get_route_table failed: %s", exc)
            return []

    @staticmethod
    def _normalize_routes_nxapi(data: dict, protocol_filter: str, limit: int) -> list[dict]:
        vrf_rows = _unwrap_table(data, "TABLE_vrf", "ROW_vrf")
        results: list[dict] = []
        for vrf_row in vrf_rows:
            addrf_rows = _unwrap_table(vrf_row, "TABLE_addrf", "ROW_addrf")
            for addrf in addrf_rows:
                prefix_rows = _unwrap_table(addrf, "TABLE_prefix", "ROW_prefix")
                for prefix_row in prefix_rows:
                    prefix = prefix_row.get("ipprefix", "")
                    path_rows = _unwrap_table(prefix_row, "TABLE_path", "ROW_path")
                    for path in path_rows:
                        proto = path.get("clientname", "").lower()
                        if protocol_filter and proto != protocol_filter:
                            continue
                        next_hop = path.get("ipnexthop", path.get("ifname", ""))
                        results.append(
                            {
                                "prefix": prefix,
                                "next_hop": next_hop,
                                "protocol": proto,
                                "metric": int(path.get("metric", 0)),
                                "preference": int(path.get("pref", 0)),
                            }
                        )
                        if len(results) >= limit:
                            return results
        return results

    def get_route_summary(self) -> dict:
        """Get route count summary. Transport: NX-API ``show ip route summary``."""
        try:
            data = self._nxapi_command("show ip route summary")
            return self._normalize_route_summary_nxapi(data)
        except Exception as exc:
            logger.warning("get_route_summary failed: %s", exc)
            return {"total": 0, "connected": 0, "static": 0, "bgp": 0, "ospf": 0, "isis": 0}

    @staticmethod
    def _normalize_route_summary_nxapi(data: dict) -> dict:
        result = {"total": 0, "connected": 0, "static": 0, "bgp": 0, "ospf": 0, "isis": 0}
        vrf_rows = _unwrap_table(data, "TABLE_vrf", "ROW_vrf")
        for vrf_row in vrf_rows:
            result["total"] = int(vrf_row.get("routes-total", 0))
            route_rows = _unwrap_table(vrf_row, "TABLE_route_count", "ROW_route_count")
            for row in route_rows:
                proto = row.get("clientnamecount", "").lower()
                count = int(row.get("count", 0))
                if proto in result:
                    result[proto] = count
            break  # first VRF only
        return result

    def get_mac_table(self, vlan: int | None = None, limit: int = 500) -> list[dict]:
        """Get MAC address table. Transport: NX-API ``show mac address-table``."""
        try:
            cmd = "show mac address-table"
            if vlan is not None:
                cmd += f" vlan {vlan}"
            data = self._nxapi_command(cmd)
            return self._normalize_mac_nxapi(data, limit)
        except Exception as exc:
            logger.warning("get_mac_table failed: %s", exc)
            return []

    @staticmethod
    def _normalize_mac_nxapi(data: dict, limit: int) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_mac_address", "ROW_mac_address")
        results: list[dict] = []
        for row in rows:
            results.append(
                {
                    "mac_address": _normalize_mac(row.get("disp_mac_addr", "")),
                    "vlan": int(row.get("disp_vlan", 0)),
                    "interface": row.get("disp_port", ""),
                    "type": row.get("disp_type", "dynamic").lower(),
                }
            )
            if len(results) >= limit:
                break
        return results

    def get_stp_status(self) -> dict:
        """Get spanning-tree status. Transport: NX-API ``show spanning-tree``."""
        try:
            data = self._nxapi_command("show spanning-tree")
            return self._normalize_stp_nxapi(data)
        except Exception as exc:
            logger.warning("get_stp_status failed: %s", exc)
            return {"mode": "", "root_bridge": "", "instances": []}

    @staticmethod
    def _normalize_stp_nxapi(data: dict) -> dict:
        mode = data.get("stp_mode", "")
        root_bridge = ""
        instances: list[dict] = []
        vlan_rows = _unwrap_table(data, "TABLE_vlan", "ROW_vlan")
        for vlan_row in vlan_rows:
            inst_id = str(vlan_row.get("vlan_id", vlan_row.get("mst_id", "")))
            inst_root = vlan_row.get("root_bridge_addr", "")
            if not root_bridge and inst_root:
                root_bridge = inst_root
            root_port = vlan_row.get("root_port", "")
            priority = int(vlan_row.get("bridge_priority", 0))
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
        """Get port-channel summary. Transport: NX-API ``show port-channel summary``."""
        try:
            data = self._nxapi_command("show port-channel summary")
            return self._normalize_pc_nxapi(data)
        except Exception as exc:
            logger.warning("get_port_channels failed: %s", exc)
            return []

    @staticmethod
    def _normalize_pc_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_channel", "ROW_channel")
        results: list[dict] = []
        for row in rows:
            name = row.get("port-channel", "")
            status = "up" if row.get("status", "").lower() == "up" else "down"
            protocol = row.get("prtcl", "none").lower()
            members: list[dict] = []
            mem_rows = _unwrap_table(row, "TABLE_member", "ROW_member")
            for mem in mem_rows:
                mem_intf = mem.get("port", "")
                mem_status_raw = mem.get("port-status", "down").lower()
                if "p" in mem_status_raw or "bundled" in mem_status_raw:
                    mem_status = "bundled"
                elif "h" in mem_status_raw or "hot" in mem_status_raw:
                    mem_status = "hot-standby"
                else:
                    mem_status = "down"
                members.append({"interface": mem_intf, "status": mem_status})
            results.append({"name": name, "status": status, "protocol": protocol, "members": members})
        return results

    def get_environment(self) -> dict:
        """Get environmental data. Transport: NX-API ``show environment``."""
        try:
            data = self._nxapi_command("show environment")
            return self._normalize_env_nxapi(data)
        except Exception as exc:
            logger.warning("get_environment failed: %s", exc)
            return {"temperature": [], "fans": [], "power": []}

    @staticmethod
    def _normalize_env_nxapi(data: dict) -> dict:
        temps: list[dict] = []
        fans: list[dict] = []
        power: list[dict] = []
        temp_rows = _unwrap_table(data, "TABLE_tempinfo", "ROW_tempinfo")
        for row in temp_rows:
            temps.append(
                {
                    "sensor": row.get("tempmod", ""),
                    "value": float(row.get("curtemp", 0)),
                    "status": row.get("alarmstatus", "ok").lower(),
                }
            )
        fan_rows = _unwrap_table(data, "TABLE_faninfo", "ROW_faninfo")
        for row in fan_rows:
            fans.append(
                {
                    "name": row.get("fanname", ""),
                    "status": "ok" if row.get("fanstatus", "").lower() == "ok" else "failed",
                }
            )
        psu_rows = _unwrap_table(data, "TABLE_psinfo", "ROW_psinfo")
        for row in psu_rows:
            watts = 0.0
            w_str = row.get("actual_out", row.get("watts", "0"))
            try:
                watts = float(str(w_str).replace("W", "").strip())
            except ValueError:
                pass
            power.append(
                {
                    "name": row.get("psmodel", row.get("psnum", "")),
                    "status": "ok" if row.get("ps_status", "").lower() == "ok" else "failed",
                    "output_watts": watts,
                }
            )
        return {"temperature": temps, "fans": fans, "power": power}

    def get_ntp_status(self) -> dict:
        """Get NTP status. Transport: NX-API ``show ntp peer-status``."""
        try:
            data = self._nxapi_command("show ntp peer-status")
            return self._normalize_ntp_nxapi(data)
        except Exception as exc:
            logger.warning("get_ntp_status failed: %s", exc)
            return {"synchronized": False, "reference": "", "peers": []}

    @staticmethod
    def _normalize_ntp_nxapi(data: dict) -> dict:
        synchronized = False
        reference = ""
        peers: list[dict] = []
        rows = _unwrap_table(data, "TABLE_peersstatus", "ROW_peersstatus")
        for row in rows:
            peer_ip = row.get("remote", "").lstrip("*=+#o-~")
            is_sync = row.get("syncmode", "").strip() == "*"
            if is_sync:
                synchronized = True
                reference = peer_ip
            stratum = int(row.get("st", 0))
            offset = 0.0
            jitter = 0.0
            try:
                offset = float(row.get("delay", 0))
                jitter = float(row.get("jitter", row.get("disp", 0)))
            except (ValueError, TypeError):
                pass
            if peer_ip:
                peers.append({"peer": peer_ip, "stratum": stratum, "offset": offset, "jitter": jitter})
        return {"synchronized": synchronized, "reference": reference, "peers": peers}

    def get_cpu_memory(self) -> dict:
        """Get CPU/memory usage. Transport: NX-API ``show system resources``."""
        try:
            data = self._nxapi_command("show system resources")
            return self._normalize_cpu_mem_nxapi(data)
        except Exception as exc:
            logger.warning("get_cpu_memory failed: %s", exc)
            return {"cpu_percent": 0.0, "memory_total": 0, "memory_used": 0, "memory_free": 0}

    @staticmethod
    def _normalize_cpu_mem_nxapi(data: dict) -> dict:
        cpu_idle = float(data.get("cpu_state_idle", 100.0))
        cpu_percent = round(100.0 - cpu_idle, 2)
        mem_total = int(data.get("memory_usage_total", 0))
        mem_used = int(data.get("memory_usage_used", 0))
        mem_free = int(data.get("memory_usage_free", 0))
        return {"cpu_percent": cpu_percent, "memory_total": mem_total, "memory_used": mem_used, "memory_free": mem_free}

    def get_acls(self) -> list[dict]:
        """Get IP access lists. Transport: NX-API ``show ip access-lists``."""
        try:
            data = self._nxapi_command("show ip access-lists")
            return self._normalize_acls_nxapi(data)
        except Exception as exc:
            logger.warning("get_acls failed: %s", exc)
            return []

    @staticmethod
    def _normalize_acls_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_ip_acl", "ROW_ip_acl")
        results: list[dict] = []
        for acl in rows:
            name = acl.get("acl_name", "")
            acl_type = "standard" if "standard" in str(acl.get("acl_type", "")).lower() else "extended"
            rules: list[dict] = []
            ace_rows = _unwrap_table(acl, "TABLE_seqno", "ROW_seqno")
            for ace in ace_rows:
                rules.append(
                    {
                        "sequence": int(ace.get("seqno", 0)),
                        "action": ace.get("permitdeny", ""),
                        "source": ace.get("src_any", ace.get("src_ip_prefix", ace.get("remark", ""))),
                    }
                )
            results.append({"name": name, "type": acl_type, "rules": rules})
        return results

    def get_snmp_config(self) -> dict:
        """Get SNMP configuration. Transport: NX-API ``show snmp``."""
        try:
            data = self._nxapi_command("show snmp")
            return self._normalize_snmp_nxapi(data)
        except Exception as exc:
            logger.warning("get_snmp_config failed: %s", exc)
            return {"communities": [], "users": [], "traps": []}

    @staticmethod
    def _normalize_snmp_nxapi(data: dict) -> dict:
        communities: list[dict] = []
        users: list[dict] = []
        traps: list[dict] = []
        comm_rows = _unwrap_table(data, "TABLE_snmp_community", "ROW_snmp_community")
        for row in comm_rows:
            communities.append(
                {
                    "name": row.get("community_name", ""),
                    "access": "ro" if "ro" in str(row.get("grouporaccess", "")).lower() else "rw",
                }
            )
        user_rows = _unwrap_table(data, "TABLE_snmp_users", "ROW_snmp_users")
        for row in user_rows:
            users.append({"name": row.get("user", "")})
        trap_rows = _unwrap_table(data, "TABLE_host", "ROW_host")
        for row in trap_rows:
            traps.append({"host": row.get("host", "")})
        return {"communities": communities, "users": users, "traps": traps}

    def get_running_config_section(self, section: str) -> str:
        """Get a running-config section. Transport: NX-API text ``show running-config section``."""
        try:
            return self._nxapi_command_text(f"show running-config | section {section}")
        except Exception as exc:
            logger.warning("get_running_config_section failed: %s", exc)
            return ""

    def get_startup_config(self) -> str:
        """Get startup configuration. Transport: NX-API text ``show startup-config``."""
        try:
            return self._nxapi_command_text("show startup-config")
        except Exception as exc:
            logger.warning("get_startup_config failed: %s", exc)
            return ""

    def get_vrfs(self) -> list[dict]:
        """Get all VRFs. Transport: NX-API ``show vrf``."""
        try:
            data = self._nxapi_command("show vrf")
            return self._normalize_vrfs_nxapi(data)
        except Exception as exc:
            logger.warning("get_vrfs failed: %s", exc)
            return []

    @staticmethod
    def _normalize_vrfs_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_vrf", "ROW_vrf")
        results: list[dict] = []
        for row in rows:
            name = row.get("vrf_name", "")
            rd = row.get("rd", "")
            if rd == "--":
                rd = ""
            intfs_str = row.get("vrf_intf", "")
            if isinstance(intfs_str, list):
                interfaces = intfs_str
            elif intfs_str:
                interfaces = [i.strip() for i in intfs_str.split(",") if i.strip()]
            else:
                interfaces = []
            results.append({"name": name, "rd": rd, "interfaces": interfaces, "route_count": 0})
        return results

    def get_trunk_interfaces(self) -> list[dict]:
        """Get trunk ports. Transport: NX-API ``show interface trunk``."""
        try:
            data = self._nxapi_command("show interface trunk")
            return self._normalize_trunks_nxapi(data)
        except Exception as exc:
            logger.warning("get_trunk_interfaces failed: %s", exc)
            return []

    @staticmethod
    def _normalize_trunks_nxapi(data: dict) -> list[dict]:
        rows = _unwrap_table(data, "TABLE_interface", "ROW_interface")
        results: list[dict] = []
        for row in rows:
            intf = row.get("interface", "")
            native = 1
            native_str = row.get("native", "1")
            try:
                native = int(native_str)
            except (ValueError, TypeError):
                pass
            results.append(
                {
                    "interface": intf,
                    "mode": "trunk",
                    "native_vlan": native,
                    "allowed_vlans": str(row.get("vlans_allowed", "")),
                    "active_vlans": str(row.get("vlans_active", "")),
                }
            )
        return results

    def get_vrf_detail(self, vrf: str) -> dict:
        """Get detailed VRF information. Transport: NX-API ``show vrf {vrf} detail``."""
        try:
            data = self._nxapi_command(f"show vrf {vrf} detail")
            return self._normalize_vrf_detail_nxapi(vrf, data)
        except Exception as exc:
            logger.warning("get_vrf_detail failed: %s", exc)
            return {}

    @staticmethod
    def _normalize_vrf_detail_nxapi(vrf: str, data: dict) -> dict:
        rows = _unwrap_table(data, "TABLE_vrf", "ROW_vrf")
        if not rows:
            return {}
        row = rows[0]
        rd = row.get("rd", "")
        if rd == "--":
            rd = ""
        intfs_str = row.get("vrf_intf", "")
        if isinstance(intfs_str, list):
            interfaces = intfs_str
        elif intfs_str:
            interfaces = [i.strip() for i in intfs_str.split(",") if i.strip()]
        else:
            interfaces = []
        return {
            "name": row.get("vrf_name", vrf),
            "rd": rd,
            "interfaces": interfaces,
            "route_count": 0,
            "import_targets": [],
            "export_targets": [],
        }

    def ping(self, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
        """Execute a ping. Transport: NX-API (text) ``ping``."""
        cmd = f"ping {destination} vrf {vrf}" if vrf != "default" else f"ping {destination}"
        if source:
            cmd += f" source {source}"
        cmd += f" count {count}"
        try:
            output = self._nxapi_command_text(cmd)
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
        rtt_m = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", output)
        if rtt_m:
            result["rtt_min"] = float(rtt_m.group(1))
            result["rtt_avg"] = float(rtt_m.group(2))
            result["rtt_max"] = float(rtt_m.group(3))
        return result

    def traceroute(self, destination: str, source: str = "", vrf: str = "default") -> dict:
        """Execute a traceroute. Transport: NX-API (text) ``traceroute``."""
        cmd = f"traceroute {destination} vrf {vrf}" if vrf != "default" else f"traceroute {destination}"
        if source:
            cmd += f" source {source}"
        try:
            output = self._nxapi_command_text(cmd)
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
        """Get interface flap events. Transport: NX-API ``show logging``."""
        try:
            output = self._nxapi_command_text("show logging last 500")
        except Exception:
            return []
        return self._parse_flap_events(output)

    @staticmethod
    def _parse_flap_events(output: str) -> list[dict]:
        events: list[dict] = []
        for line in output.splitlines():
            lower = line.lower()
            if "changed state to" not in lower and "link status changed" not in lower:
                continue
            intf_m = re.search(r"(Ethernet\S+|Loopback\S+|Vlan\S+|port-channel\S+|mgmt\S+)", line, re.IGNORECASE)
            interface = intf_m.group(1) if intf_m else "unknown"
            event = "link state change"
            if "changed state to up" in lower:
                event = "link up"
            elif "changed state to down" in lower:
                event = "link down"
            ts_m = re.match(r"^(\d{4}\s+\w+\s+\d+\s+[\d:]+|\S+\s+\d+\s+[\d:]+)", line)
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


def _parse_speed(speed_str: str | int) -> int:
    """Parse NX-OS speed string/int to Mbps.

    NX-OS may return speed as an integer in Kbit/s or as a string
    like '1000 Mb/s', '10 Gb/s', 'auto', etc.
    """
    if isinstance(speed_str, int):
        if speed_str >= 1000:
            return speed_str // 1000
        return speed_str
    if isinstance(speed_str, str):
        speed_str = speed_str.strip().lower()
        if speed_str in ("auto", "--", ""):
            return 0
        gb_match = re.match(r"(\d+)\s*gb", speed_str)
        if gb_match:
            return int(gb_match.group(1)) * 1000
        mb_match = re.match(r"(\d+)\s*mb", speed_str)
        if mb_match:
            return int(mb_match.group(1))
        # Try plain integer (Kbit/s)
        try:
            val = int(speed_str)
            if val >= 1000:
                return val // 1000
            return val
        except ValueError:
            return 0
    return 0


def _parse_nxos_uptime(line: str) -> int:
    """Parse NX-OS uptime string to seconds.

    Example: "Kernel uptime is 125 day(s), 3 hour(s), 45 minute(s), 12 second(s)"
    """
    total = 0
    patterns = [
        (r"(\d+)\s+year", 365 * 24 * 3600),
        (r"(\d+)\s+day", 24 * 3600),
        (r"(\d+)\s+hour", 3600),
        (r"(\d+)\s+minute", 60),
        (r"(\d+)\s+second", 1),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, line)
        if match:
            total += int(match.group(1)) * multiplier
    return total


def _parse_bgp_uptime(uptime_str: str) -> int:
    """Parse NX-OS BGP uptime/resettime to seconds.

    NX-API may return formats like 'P2DT3H45M' (ISO 8601 duration),
    'never', or time strings like '01:23:45'.
    """
    if not uptime_str or uptime_str.lower() == "never":
        return 0

    # ISO 8601 duration: P2DT3H45M12S
    iso_match = re.match(
        r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        uptime_str,
        re.IGNORECASE,
    )
    if iso_match and any(iso_match.groups()):
        days = int(iso_match.group(1) or 0)
        hours = int(iso_match.group(2) or 0)
        minutes = int(iso_match.group(3) or 0)
        seconds = int(iso_match.group(4) or 0)
        return (days * 86400) + (hours * 3600) + (minutes * 60) + seconds

    # HH:MM:SS format
    time_match = re.match(r"(\d+):(\d+):(\d+)", uptime_str)
    if time_match:
        return int(time_match.group(1)) * 3600 + int(time_match.group(2)) * 60 + int(time_match.group(3))

    return 0


def _parse_arp_age(age_str: str) -> float:
    """Parse NX-OS ARP age string to seconds.

    NX-API returns age as 'HH:MM:SS' or '-' for permanent entries.
    """
    if not age_str or age_str == "-":
        return -1.0

    time_match = re.match(r"(\d+):(\d+):(\d+)", age_str)
    if time_match:
        hours = int(time_match.group(1))
        minutes = int(time_match.group(2))
        seconds = int(time_match.group(3))
        return float((hours * 3600) + (minutes * 60) + seconds)

    try:
        return float(age_str)
    except ValueError:
        return -1.0
