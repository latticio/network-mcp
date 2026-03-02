"""BFD (Bidirectional Forwarding Detection) tools for Arista EOS."""

from network_mcp.helpers import READ_ONLY, run_show_command, validate_cli_param, validate_ip_or_hostname
from network_mcp.server import conn_mgr, mcp


@mcp.tool(annotations=READ_ONLY)
def eos_get_bfd_peers(host: str, vrf: str = "default") -> dict:
    """Get all BFD sessions with peer addresses, protocol, status, and intervals from an Arista EOS device.

    BFD (Bidirectional Forwarding Detection) provides sub-second failure detection for routing
    protocols like BGP and OSPF. Use this to verify BFD sessions are up and check negotiated timers.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        vrf: VRF name (default: 'default').
    """
    if vrf != "default":
        vrf_err = validate_cli_param(vrf, "vrf")
        if vrf_err:
            return {"status": "error", "device": host, "error": vrf_err}
    cmd = "show bfd peers"
    if vrf != "default":
        cmd = f"show bfd peers vrf {vrf}"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }


@mcp.tool(annotations=READ_ONLY)
def eos_get_bfd_peer_detail(host: str, peer: str, vrf: str = "default") -> dict:
    """Get detailed BFD session info with counters for a specific peer on an Arista EOS device.

    Returns detailed BFD session information including negotiated timers, hardware acceleration
    status, packet counters, and registered protocols. Useful for diagnosing BFD flaps or
    timer mismatches.

    Args:
        host: Hostname, IP address, or inventory name of the Arista EOS device.
        peer: BFD peer IP address to query.
        vrf: VRF name (default: 'default').
    """
    peer_err = validate_ip_or_hostname(peer, "peer")
    if peer_err:
        return {"status": "error", "device": host, "error": peer_err}
    if vrf != "default":
        vrf_err = validate_cli_param(vrf, "vrf")
        if vrf_err:
            return {"status": "error", "device": host, "error": vrf_err}
    cmd = f"show bfd peers {peer} detail"
    if vrf != "default":
        cmd = f"show bfd peers {peer} vrf {vrf} detail"
    result = run_show_command(conn_mgr, host, [cmd])
    if result["status"] == "error":
        return result
    return {
        "status": "success",
        "device": host,
        "data": result["data"][0],
    }
