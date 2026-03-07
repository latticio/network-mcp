"""NetworkDriver protocol — vendor-neutral device driver interface.

Extends the original DeviceDriver with normalized getter methods that return
a common data structure regardless of the underlying vendor platform. Each
vendor driver (EOS, IOS-XE, NX-OS, JunOS) implements the translation from
platform-specific output to the common format.

Existing run_show()/run_config() methods remain unchanged for backward
compatibility with all current EOS tools.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class NotSupportedError(Exception):
    """Raised when a getter is not supported by a vendor's platform."""


@runtime_checkable
class NetworkDriver(Protocol):
    """Multi-vendor device driver protocol.

    Extends DeviceDriver with normalized getter methods that return
    vendor-neutral data structures. Each vendor driver implements
    the translation from platform-specific output to the common format.

    Existing run_show()/run_config() methods remain unchanged for
    backward compatibility with all current EOS tools.
    """

    @property
    def platform(self) -> str:
        """Platform identifier: 'eos', 'iosxe', 'nxos', 'junos'."""
        ...

    @property
    def vendor(self) -> str:
        """Vendor name: 'arista', 'cisco', 'juniper'."""
        ...

    # --- Connection lifecycle (unchanged from DeviceDriver) ---

    def connect(self, **kwargs: Any) -> None:
        """Establish a connection to the device."""
        ...

    def close(self) -> None:
        """Close the connection to the device."""
        ...

    def is_connected(self) -> bool:
        """Return True if the driver has an active connection."""
        ...

    # --- Raw command execution (unchanged) ---

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]:
        """Execute show commands and return vendor-specific output."""
        ...

    def run_config(self, commands: list[str]) -> list[str]:
        """Execute configuration commands."""
        ...

    # --- Normalized getters (original 6) ---

    def get_facts(self) -> dict:
        """Get device facts in a vendor-neutral format.

        Returns:
            dict with keys:
                - hostname (str): Device hostname
                - model (str): Hardware model name
                - version (str): Software version string
                - serial (str): Serial number
                - uptime_seconds (int): Uptime in seconds
                - vendor (str): Vendor name (e.g., 'arista')
                - platform (str): Platform identifier (e.g., 'eos')
        """
        ...

    def get_interfaces(self) -> dict[str, dict]:
        """Get interface status in a vendor-neutral format.

        Returns:
            dict mapping interface name to:
                - is_up (bool): True if the interface link is up
                - is_enabled (bool): True if the interface is admin enabled
                - description (str): Interface description
                - speed (int): Interface speed in Mbps
                - mtu (int): Maximum transmission unit
                - mac_address (str): MAC address (format: 'xx:xx:xx:xx:xx:xx')
        """
        ...

    def get_bgp_summary(self, vrf: str = "default") -> dict:
        """Get BGP summary in a vendor-neutral format.

        Args:
            vrf: VRF name to query. Defaults to 'default'.

        Returns:
            dict with keys:
                - router_id (str): BGP router ID
                - as_number (int): Local AS number
                - peers (dict): Mapping of peer IP to:
                    - state (str): Peer state (e.g., 'Established', 'Active')
                    - prefixes_received (int): Number of prefixes received
                    - uptime (int): Peer uptime in seconds
        """
        ...

    def get_arp_table(self) -> list[dict]:
        """Get ARP table in a vendor-neutral format.

        Returns:
            list of dicts, each with keys:
                - ip (str): IP address
                - mac (str): MAC address (format: 'xx:xx:xx:xx:xx:xx')
                - interface (str): Interface name
                - age (float): Entry age in seconds (-1 if not available)
        """
        ...

    def get_lldp_neighbors(self) -> dict[str, list[dict]]:
        """Get LLDP neighbor information in a vendor-neutral format.

        Returns:
            dict mapping local interface name to list of neighbors, each with:
                - hostname (str): Remote system name
                - port (str): Remote port identifier
                - system_description (str): Remote system description
                - management_ip (str): Remote management IP address
                  (empty string if not available from the vendor API)
        """
        ...

    def get_config(self, sanitized: bool = True) -> dict:
        """Get device configuration in a vendor-neutral format.

        Args:
            sanitized: If True, redact passwords and secrets from output.

        Returns:
            dict with keys:
                - running (str): Running configuration text
                - startup (str): Startup configuration text
        """
        ...

    # --- Expanded normalized getters (20 new) ---

    def get_vlans(self) -> list[dict]:
        """Get all VLANs configured on the device.

        Returns:
            list of dicts, each with keys:
                - vlan_id (int): VLAN number
                - name (str): VLAN name
                - status (str): 'active' or 'suspend'
                - interfaces (list[str]): Member interface names
        """
        ...

    def get_vlan_detail(self, vlan_id: int) -> dict:
        """Get detailed information for a single VLAN.

        Args:
            vlan_id: VLAN number (1-4094).

        Returns:
            dict with keys:
                - vlan_id (int): VLAN number
                - name (str): VLAN name
                - status (str): 'active' or 'suspend'
                - interfaces (list[str]): Member interface names
                - type (str): VLAN type (e.g., 'static', 'dynamic')
        """
        ...

    def get_interface_counters(self) -> list[dict]:
        """Get traffic counters for all interfaces.

        Returns:
            list of dicts, each with keys:
                - interface (str): Interface name
                - in_octets (int): Input bytes
                - out_octets (int): Output bytes
                - in_packets (int): Input unicast packets
                - out_packets (int): Output unicast packets
                - in_broadcast (int): Input broadcast packets
                - out_broadcast (int): Output broadcast packets
                - in_multicast (int): Input multicast packets
                - out_multicast (int): Output multicast packets
        """
        ...

    def get_interface_errors(self) -> list[dict]:
        """Get error counters for all interfaces.

        Returns:
            list of dicts, each with keys:
                - interface (str): Interface name
                - in_errors (int): Total input errors
                - out_errors (int): Total output errors
                - in_crc (int): CRC / FCS errors
                - in_frame (int): Frame alignment errors
                - in_runts (int): Runt frame count
                - in_giants (int): Oversized frame count
        """
        ...

    def get_ip_interfaces(self) -> list[dict]:
        """Get Layer 3 interfaces with IP addressing.

        Returns:
            list of dicts, each with keys:
                - interface (str): Interface name
                - ip_address (str): Primary IP address (CIDR notation or empty)
                - subnet (str): Subnet mask or prefix length
                - vrf (str): VRF name
                - status (str): Protocol status ('up' or 'down')
        """
        ...

    def get_transceiver_info(self) -> list[dict]:
        """Get transceiver / optics DOM data for all interfaces.

        Returns:
            list of dicts, each with keys:
                - interface (str): Interface name
                - type (str): Media / transceiver type (e.g., '10GBASE-SR')
                - tx_power (float): Transmit power in dBm
                - rx_power (float): Receive power in dBm
                - temp (float): Module temperature in Celsius
        """
        ...

    def get_ospf_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get OSPF neighbor adjacencies.

        Args:
            vrf: VRF name to query. Defaults to 'default'.

        Returns:
            list of dicts, each with keys:
                - neighbor_id (str): OSPF router ID of the neighbor
                - state (str): Adjacency state (e.g., 'Full', 'Init', '2-Way')
                - interface (str): Local interface name
                - area (str): OSPF area ID
                - uptime (str): Adjacency uptime string
        """
        ...

    def get_ospf_interfaces(self, vrf: str = "default") -> list[dict]:
        """Get OSPF-enabled interfaces.

        Args:
            vrf: VRF name to query. Defaults to 'default'.

        Returns:
            list of dicts, each with keys:
                - interface (str): Interface name
                - area (str): OSPF area ID
                - network_type (str): Network type (e.g., 'broadcast', 'point-to-point')
                - cost (int): OSPF cost
                - neighbor_count (int): Number of OSPF neighbors on this interface
        """
        ...

    def get_bgp_neighbors(self, vrf: str = "default") -> list[dict]:
        """Get detailed BGP neighbor information.

        Args:
            vrf: VRF name to query. Defaults to 'default'.

        Returns:
            list of dicts, each with keys:
                - neighbor (str): Neighbor IP address
                - remote_as (int): Remote AS number
                - state (str): BGP session state (e.g., 'Established', 'Active')
                - prefixes_received (int): Number of prefixes received
                - prefixes_sent (int): Number of prefixes sent
                - uptime (str): Session uptime string
                - description (str): Neighbor description (empty if not set)
        """
        ...

    def get_route_table(self, protocol: str = "", vrf: str = "default", limit: int = 100) -> list[dict]:
        """Get IP routing table entries.

        Args:
            protocol: Filter by protocol (e.g., 'bgp', 'ospf', 'connected'). Empty = all.
            vrf: VRF name. Defaults to 'default'.
            limit: Maximum number of routes to return. Defaults to 100.

        Returns:
            list of dicts, each with keys:
                - prefix (str): Network prefix in CIDR notation
                - next_hop (str): Next-hop IP address or interface
                - protocol (str): Route protocol source (e.g., 'bgp', 'connected')
                - metric (int): Route metric
                - preference (int): Administrative distance
        """
        ...

    def get_route_summary(self) -> dict:
        """Get route count summary grouped by protocol.

        Returns:
            dict with keys:
                - total (int): Total number of routes
                - connected (int): Connected route count
                - static (int): Static route count
                - bgp (int): BGP route count
                - ospf (int): OSPF route count
                - isis (int): IS-IS route count (0 if not applicable)
        """
        ...

    def get_mac_table(self, vlan: int | None = None, limit: int = 500) -> list[dict]:
        """Get MAC address table entries.

        Args:
            vlan: Filter by VLAN ID (optional).
            limit: Maximum number of entries to return. Defaults to 500.

        Returns:
            list of dicts, each with keys:
                - mac_address (str): MAC address (format: 'xx:xx:xx:xx:xx:xx')
                - vlan (int): VLAN number
                - interface (str): Learned interface name
                - type (str): Entry type ('dynamic' or 'static')
        """
        ...

    def get_stp_status(self) -> dict:
        """Get spanning tree status.

        Returns:
            dict with keys:
                - mode (str): STP mode (e.g., 'mstp', 'rstp', 'rapid-pvst')
                - root_bridge (str): Root bridge ID
                - instances (list[dict]): STP instances, each with:
                    - id (str): Instance ID
                    - root_bridge (str): Instance root bridge ID
                    - root_port (str): Root port for this instance
                    - priority (int): Local bridge priority
        """
        ...

    def get_port_channels(self) -> list[dict]:
        """Get port-channel / LAG summary.

        Returns:
            list of dicts, each with keys:
                - name (str): Port-channel name (e.g., 'Port-Channel1')
                - status (str): 'up' or 'down'
                - protocol (str): Aggregation protocol ('lacp' or 'static')
                - members (list[dict]): Member interfaces, each with:
                    - interface (str): Member interface name
                    - status (str): Member status ('bundled', 'hot-standby', 'down')
        """
        ...

    def get_environment(self) -> dict:
        """Get environmental sensor readings (temperature, fans, power).

        Returns:
            dict with keys:
                - temperature (list[dict]): Sensor readings, each with:
                    - sensor (str): Sensor name
                    - value (float): Temperature in Celsius
                    - status (str): 'ok', 'warning', or 'critical'
                - fans (list[dict]): Fan status, each with:
                    - name (str): Fan tray name
                    - status (str): 'ok' or 'failed'
                - power (list[dict]): PSU status, each with:
                    - name (str): PSU name
                    - status (str): 'ok' or 'failed'
                    - output_watts (float): Output power in watts
        """
        ...

    def get_ntp_status(self) -> dict:
        """Get NTP synchronization status and peer list.

        Returns:
            dict with keys:
                - synchronized (bool): True if clock is NTP-synchronized
                - reference (str): Current reference peer address
                - peers (list[dict]): NTP peers, each with:
                    - peer (str): Peer IP address or hostname
                    - stratum (int): Peer stratum level
                    - offset (float): Clock offset in milliseconds
                    - jitter (float): Jitter in milliseconds
        """
        ...

    def get_cpu_memory(self) -> dict:
        """Get CPU utilization and memory usage.

        Returns:
            dict with keys:
                - cpu_percent (float): CPU utilization percentage
                - memory_total (int): Total memory in KB
                - memory_used (int): Used memory in KB
                - memory_free (int): Free memory in KB
        """
        ...

    def get_acls(self) -> list[dict]:
        """Get IP access control lists and their rules.

        Returns:
            list of dicts, each with keys:
                - name (str): ACL name
                - type (str): ACL type ('standard' or 'extended')
                - rules (list[dict]): ACL entries, each with:
                    - sequence (int): Sequence number
                    - action (str): 'permit' or 'deny'
                    - source (str): Source match criteria
        """
        ...

    def get_snmp_config(self) -> dict:
        """Get SNMP configuration summary.

        Returns:
            dict with keys:
                - communities (list[dict]): SNMP communities, each with:
                    - name (str): Community string
                    - access (str): 'ro' or 'rw'
                - users (list[dict]): SNMPv3 users
                - traps (list[dict]): Trap targets
        """
        ...

    def get_running_config_section(self, section: str) -> str:
        """Get a specific section of the running configuration.

        Args:
            section: Configuration section keyword (e.g., 'router bgp', 'interface').

        Returns:
            str: Text of the matching configuration section.
        """
        ...

    def get_startup_config(self) -> str:
        """Get the full startup configuration.

        Returns:
            str: Complete startup configuration text.
        """
        ...

    def get_vrfs(self) -> list[dict]:
        """Get all VRF instances on the device.

        Returns:
            list of dicts, each with keys:
                - name (str): VRF name
                - rd (str): Route distinguisher (empty if not set)
                - interfaces (list[str]): Interfaces assigned to the VRF
                - route_count (int): Number of routes in the VRF (0 if unknown)
        """
        ...

    def get_trunk_interfaces(self) -> list[dict]:
        """Get trunk port configuration from the device.

        Returns:
            list of dicts, each with keys:
                - interface (str): Interface name
                - mode (str): 'trunk'
                - native_vlan (int): Native/untagged VLAN ID
                - allowed_vlans (str): Allowed VLAN list (e.g., '1-100,200')
                - active_vlans (str): Currently active VLANs on this trunk
        """
        ...

    def get_lldp_neighbor_detail(self, interface: str = "") -> dict[str, list[dict]]:
        """Get detailed LLDP neighbor TLV data.

        Returns extended LLDP information including chassis ID, management
        address, and system capabilities — beyond the basic hostname/port
        returned by ``get_lldp_neighbors()``.

        Args:
            interface: Filter by local interface name (empty = all).

        Returns:
            dict mapping local interface name to list of neighbor dicts,
            each with keys:
                - hostname (str): Remote system name
                - port (str): Remote port identifier
                - system_description (str): Remote system description
                - chassis_id (str): Chassis ID (typically a MAC address)
                - management_address (str): Management IP address
                - capabilities (list[str]): System capabilities
                  (e.g., ['bridge', 'router'])
        """
        ...

    def get_vrf_detail(self, vrf: str) -> dict:
        """Get detailed information for a specific VRF.

        Args:
            vrf: VRF name to query.

        Returns:
            dict with keys:
                - name (str): VRF name
                - rd (str): Route distinguisher (empty if not set)
                - interfaces (list[str]): Interfaces assigned to the VRF
                - route_count (int): Number of routes in the VRF (0 if unknown)
                - import_targets (list[str]): Import route targets
                - export_targets (list[str]): Export route targets
        """
        ...

    def ping(self, destination: str, count: int = 5, source: str = "", vrf: str = "default") -> dict:
        """Execute a ping from the device.

        Args:
            destination: IP address or hostname to ping.
            count: Number of ping packets (1-20).
            source: Source interface or IP (optional).
            vrf: VRF context (default: "default").

        Returns:
            dict with keys:
                - success (bool): True if any replies received
                - packets_sent (int): Number of packets sent
                - packets_received (int): Number of replies received
                - packet_loss_pct (float): Packet loss percentage
                - rtt_min (float): Minimum RTT in ms (0 if no replies)
                - rtt_avg (float): Average RTT in ms (0 if no replies)
                - rtt_max (float): Maximum RTT in ms (0 if no replies)
                - output (str): Raw ping output text
        """
        ...

    def traceroute(self, destination: str, source: str = "", vrf: str = "default") -> dict:
        """Execute a traceroute from the device.

        Args:
            destination: IP address or hostname to trace.
            source: Source interface or IP (optional).
            vrf: VRF context (default: "default").

        Returns:
            dict with keys:
                - hops (list[dict]): Traceroute hops, each with:
                    - hop (int): Hop number
                    - address (str): IP address of the hop ('*' if no reply)
                    - rtt_ms (float): Round-trip time in ms (0 if timeout)
                - output (str): Raw traceroute output text
        """
        ...

    def get_interface_flaps(self, hours: int = 24) -> list[dict]:
        """Get interface flap events from system logs.

        Args:
            hours: Look-back period in hours (default 24).

        Returns:
            list of dicts, each with keys:
                - interface (str): Interface name
                - event (str): Event description (e.g., 'link down', 'link up')
                - timestamp (str): Event timestamp string
        """
        ...

    # --- Security getters ---

    def get_aaa_status(self) -> dict:
        """Get AAA configuration status.

        Returns:
            dict with keys:
                - methods (list[dict]): Authentication methods, each with:
                    - type (str): Method type ('local', 'radius', 'tacacs+')
                    - servers (list[str]): Server addresses (if applicable)
                    - status (str): 'active' or 'inactive'
                - authorization (dict): Authorization settings
                - accounting (dict): Accounting settings
        """
        ...

    def get_user_sessions(self) -> list[dict]:
        """Get active user sessions on the device.

        Returns:
            list of dicts, each with keys:
                - username (str): Username
                - source_ip (str): Source IP address (empty if local console)
                - session_type (str): Session type ('ssh', 'console', 'api', 'telnet')
                - idle_time (str): Idle time string
        """
        ...

    def get_copp_policy(self) -> dict:
        """Get Control Plane Policing policy configuration and stats.

        Returns:
            dict with keys:
                - classes (list[dict]): CoPP classes, each with:
                    - name (str): Class name
                    - rate_limit (str): Rate limit value
                    - matched_packets (int): Matched packet count
                    - dropped_packets (int): Dropped packet count
        """
        ...

    def get_firewall_policies(self) -> list[dict]:
        """Get firewall policies from the device.

        Returns firewall/security policy rules. Primarily implemented by
        firewall platforms (FortiOS, PAN-OS). Most switch/router drivers
        will raise NotSupportedError.

        Returns:
            list of dicts, each with keys:
                - policy_id (int): Policy sequence number
                - name (str): Policy name
                - action (str): 'accept', 'deny', or vendor-specific action
                - status (str): Policy status ('enable' or 'disable')
                - srcintf (list[str]): Source interface names
                - dstintf (list[str]): Destination interface names
                - srcaddr (list[str]): Source address object names
                - dstaddr (list[str]): Destination address object names
                - service (list[str]): Service object names
                - nat (bool): Whether NAT is enabled
                - comments (str): Policy comments
        """
        raise NotSupportedError("get_firewall_policies is not supported on this platform")

    # --- Capability & connection health ---

    def capabilities(self) -> dict[str, bool]:
        """Query supported features and getters for this driver/platform.

        Returns a dict mapping feature/getter names to availability. Includes
        both platform features (``config_sessions``, ``gnmi``, ``restconf``,
        ``netconf``) and getter-level flags (``get_vlans``, ``get_ospf_neighbors``,
        etc.) so that common tools and discovery layers can introspect support
        without calling getters and catching ``NotSupportedError``.

        Returns:
            dict mapping feature name to availability, e.g.
            ``{'config_sessions': True, 'get_vlans': True, 'get_transceiver_info': False, ...}``
        """
        ...

    def reconnect(self) -> None:
        """Force reconnection to the device.

        Closes the current transport session and re-opens it.
        Raises ConnectionError if reconnection fails.
        """
        ...


# --- Platform support map ---

# Maps getter method names to the list of platforms that implement them
# (i.e., don't raise NotSupportedError). Updated as drivers are expanded.
GETTER_PLATFORM_SUPPORT: dict[str, list[str]] = {
    "get_facts": ["eos", "iosxe", "nxos", "junos", "sonic"],
    "get_interfaces": ["eos", "iosxe", "nxos", "junos", "sonic"],
    "get_bgp_summary": ["eos", "iosxe", "nxos", "junos", "sonic"],
    "get_arp_table": ["eos", "iosxe", "nxos", "junos", "sonic"],
    "get_lldp_neighbors": ["eos", "iosxe", "nxos", "junos", "sonic"],
    "get_config": ["eos", "iosxe", "nxos", "junos", "sonic"],
    "get_vlans": ["eos", "iosxe", "nxos", "junos"],
    "get_vlan_detail": ["eos", "iosxe", "nxos"],
    "get_interface_counters": ["eos", "iosxe", "nxos", "junos"],
    "get_interface_errors": ["eos", "iosxe", "nxos", "junos"],
    "get_ip_interfaces": ["eos", "iosxe", "nxos", "junos"],
    "get_transceiver_info": ["eos", "iosxe"],
    "get_ospf_neighbors": ["eos", "iosxe", "nxos", "junos"],
    "get_ospf_interfaces": ["eos", "iosxe", "junos"],
    "get_bgp_neighbors": ["eos", "iosxe", "nxos", "junos"],
    "get_route_table": ["eos", "iosxe", "nxos", "junos", "sonic"],
    "get_route_summary": ["eos", "iosxe", "nxos"],
    "get_mac_table": ["eos", "iosxe", "nxos", "junos"],
    "get_stp_status": ["eos", "iosxe", "nxos"],
    "get_port_channels": ["eos", "iosxe", "nxos"],
    "get_environment": ["eos", "iosxe", "nxos", "junos"],
    "get_ntp_status": ["eos", "iosxe", "nxos", "junos"],
    "get_cpu_memory": ["eos", "iosxe", "nxos", "junos"],
    "get_acls": ["eos", "iosxe", "nxos"],
    "get_aaa_status": ["eos", "iosxe"],
    "get_snmp_config": ["eos", "iosxe", "nxos"],
    "get_user_sessions": ["eos", "iosxe"],
    "get_copp_policy": ["eos", "nxos"],
    "get_running_config_section": ["eos", "iosxe", "nxos", "junos"],
    "get_startup_config": ["eos", "iosxe", "nxos"],
    "get_vrfs": ["eos", "iosxe", "nxos", "junos"],
    "get_vrf_detail": ["eos", "iosxe", "nxos"],
    "get_trunk_interfaces": ["eos", "iosxe", "nxos"],
    "ping": ["eos", "iosxe", "nxos", "junos"],
    "traceroute": ["eos", "iosxe", "nxos", "junos"],
}


def make_not_supported_response(
    getter_name: str, host: str, vendor: str, platform: str
) -> dict:
    """Build a standardized not_supported response with platform guidance.

    Includes which platforms DO support the operation so users/LLMs know
    if the tool works on other devices in their inventory.
    """
    supported = GETTER_PLATFORM_SUPPORT.get(getter_name, [])
    return {
        "status": "not_supported",
        "device": host,
        "vendor": vendor,
        "platform": platform,
        "error": f"{getter_name} is not supported on {platform}",
        "supported_platforms": supported,
    }
