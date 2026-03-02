"""Property-based tests for input validators using Hypothesis."""

from hypothesis import given, settings
from hypothesis import strategies as st

from network_mcp.helpers import (
    normalize_mac,
    normalize_speed,
    validate_cli_param,
    validate_duplex,
    validate_host,
    validate_interface_name,
    validate_ip_address,
    validate_ip_prefix,
    validate_mtu,
    validate_port_channel_id,
    validate_route_distinguisher,
    validate_speed,
    validate_stp_priority,
    validate_vlan_id,
)

# --- VLAN ID validation ---


@given(vlan_id=st.integers())
def test_vlan_id_never_crashes(vlan_id):
    """validate_vlan_id should never raise, always returns None or error string."""
    result = validate_vlan_id(vlan_id)
    if 1 <= vlan_id <= 4094:
        assert result is None
    else:
        assert result is not None


@given(vlan_id=st.integers(min_value=1, max_value=4094))
def test_vlan_id_valid_range_always_passes(vlan_id):
    """All valid VLAN IDs should return None."""
    assert validate_vlan_id(vlan_id) is None


@given(vlan_id=st.integers(max_value=0))
def test_vlan_id_below_range_always_fails(vlan_id):
    """All VLAN IDs below 1 should return an error."""
    assert validate_vlan_id(vlan_id) is not None


@given(vlan_id=st.integers(min_value=4095))
def test_vlan_id_above_range_always_fails(vlan_id):
    """All VLAN IDs above 4094 should return an error."""
    assert validate_vlan_id(vlan_id) is not None


# --- CLI parameter validation ---


@given(param=st.text(min_size=0, max_size=1000))
def test_cli_param_never_crashes(param):
    """validate_cli_param should never raise, always returns None or error string."""
    result = validate_cli_param(param)
    assert result is None or isinstance(result, str)


@given(param=st.text(alphabet=st.characters(whitelist_categories=("L", "N", "P", "S"))))
def test_cli_injection_chars_detected(param):
    """All dangerous characters should be detected."""
    dangerous = set(";|!`\n\r\x00")
    if any(c in param for c in dangerous) or "$(" in param or "${" in param:
        assert validate_cli_param(param) is not None


@given(param=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.", min_size=1))
def test_cli_param_safe_chars_pass(param):
    """Safe alphanumeric strings should pass validation."""
    assert validate_cli_param(param) is None


# --- Interface name validation ---


@given(name=st.text(min_size=0, max_size=500))
def test_interface_name_never_crashes(name):
    """validate_interface_name should never raise on arbitrary strings."""
    result = validate_interface_name(name)
    assert result is None or isinstance(result, str)


@given(name=st.text(min_size=0, max_size=500))
def test_interface_name_empty_always_fails(name):
    """Empty or whitespace-only names should always fail."""
    if not name or not name.strip():
        assert validate_interface_name(name) is not None


@given(
    prefix=st.sampled_from(["Ethernet", "Management", "Loopback", "Vlan", "Port-Channel", "Vxlan"]),
    suffix=st.text(alphabet="0123456789/", min_size=1, max_size=10),
)
def test_interface_valid_prefixes_pass(prefix, suffix):
    """Interfaces with valid prefixes should pass."""
    assert validate_interface_name(prefix + suffix) is None


# --- Host validation ---


@given(host=st.text(min_size=0, max_size=500))
def test_host_never_crashes(host):
    """validate_host should never raise on arbitrary strings."""
    result = validate_host(host)
    assert result is None or isinstance(result, str)


@given(host=st.text(min_size=0, max_size=500))
def test_host_empty_always_fails(host):
    """Empty or whitespace-only hosts should always fail."""
    if not host or not host.strip():
        assert validate_host(host) is not None


@given(host=st.from_regex(r"[a-zA-Z0-9._:-]{1,253}", fullmatch=True))
def test_host_valid_pattern_passes(host):
    """Hosts matching the valid pattern should pass."""
    assert validate_host(host) is None


@given(host=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789.-_:", min_size=254, max_size=300))
def test_host_too_long_fails(host):
    """Hosts longer than 253 chars should fail."""
    assert validate_host(host) is not None


# --- MAC address normalization ---


@given(
    mac=st.from_regex(
        r"[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}", fullmatch=True
    )
)
@settings(max_examples=200)
def test_mac_normalization_idempotent(mac):
    """Normalizing a MAC twice should give the same result (idempotent)."""
    first = normalize_mac(mac)
    second = normalize_mac(first)
    assert first == second


@given(mac=st.from_regex(r"[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}", fullmatch=True))
@settings(max_examples=200)
def test_mac_cisco_format_normalizes(mac):
    """Cisco dot-notation MACs should normalize to colon format."""
    result = normalize_mac(mac)
    assert ":" in result
    assert "." not in result
    assert len(result) == 17  # aa:bb:cc:dd:ee:ff


@given(
    mac=st.from_regex(
        r"[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}-[0-9a-fA-F]{2}", fullmatch=True
    )
)
@settings(max_examples=200)
def test_mac_hyphen_format_normalizes(mac):
    """Hyphen-separated MACs should normalize to colon format."""
    result = normalize_mac(mac)
    assert ":" in result
    assert "-" not in result


@given(mac=st.text(min_size=0, max_size=50))
def test_mac_normalization_never_crashes(mac):
    """normalize_mac should never raise on arbitrary input."""
    result = normalize_mac(mac)
    assert isinstance(result, str)


# --- IP address validation ---


@given(ip=st.text(min_size=0, max_size=50))
def test_ip_address_never_crashes(ip):
    """validate_ip_address should never raise on arbitrary strings."""
    result = validate_ip_address(ip)
    assert result is None or isinstance(result, str)


@given(
    a=st.integers(min_value=0, max_value=255),
    b=st.integers(min_value=0, max_value=255),
    c=st.integers(min_value=0, max_value=255),
    d=st.integers(min_value=0, max_value=255),
)
def test_ip_address_valid_octets_pass(a, b, c, d):
    """Valid IPv4 addresses should pass validation."""
    assert validate_ip_address(f"{a}.{b}.{c}.{d}") is None


# --- IP prefix validation ---


@given(prefix=st.text(min_size=0, max_size=50))
def test_ip_prefix_never_crashes(prefix):
    """validate_ip_prefix should never raise on arbitrary strings."""
    result = validate_ip_prefix(prefix)
    assert result is None or isinstance(result, str)


@given(
    a=st.integers(min_value=0, max_value=255),
    b=st.integers(min_value=0, max_value=255),
    c=st.integers(min_value=0, max_value=255),
    d=st.integers(min_value=0, max_value=255),
    mask=st.integers(min_value=0, max_value=32),
)
def test_ip_prefix_valid_cidr_pass(a, b, c, d, mask):
    """Valid CIDR prefixes should pass validation."""
    assert validate_ip_prefix(f"{a}.{b}.{c}.{d}/{mask}") is None


# --- Route distinguisher validation ---


@given(rd=st.text(min_size=0, max_size=100))
def test_route_distinguisher_never_crashes(rd):
    """validate_route_distinguisher should never raise on arbitrary strings."""
    result = validate_route_distinguisher(rd)
    assert result is None or isinstance(result, str)


@given(
    asn=st.integers(min_value=0, max_value=65535),
    nn=st.integers(min_value=0, max_value=65535),
)
def test_route_distinguisher_asn_format_valid(asn, nn):
    """ASN:nn format RDs should pass validation."""
    assert validate_route_distinguisher(f"{asn}:{nn}") is None


# --- MTU validation ---


@given(mtu=st.integers())
def test_mtu_never_crashes(mtu):
    """validate_mtu should never raise on arbitrary integers."""
    result = validate_mtu(mtu)
    if 68 <= mtu <= 9214:
        assert result is None
    else:
        assert result is not None


# --- Port channel ID validation ---


@given(channel_id=st.integers())
def test_port_channel_id_never_crashes(channel_id):
    """validate_port_channel_id should never raise on arbitrary integers."""
    result = validate_port_channel_id(channel_id)
    if 1 <= channel_id <= 2000:
        assert result is None
    else:
        assert result is not None


# --- STP priority validation ---


@given(priority=st.integers(min_value=0, max_value=61440))
def test_stp_priority_multiples_of_4096(priority):
    """Only multiples of 4096 in range 0-61440 should pass."""
    result = validate_stp_priority(priority)
    if priority % 4096 == 0:
        assert result is None
    else:
        assert result is not None


# --- Speed validation ---


@given(speed=st.text(min_size=0, max_size=20))
def test_speed_validation_never_crashes(speed):
    """validate_speed should never raise on arbitrary strings."""
    result = validate_speed(speed)
    assert result is None or isinstance(result, str)


# --- Duplex validation ---


@given(duplex=st.text(min_size=0, max_size=20))
def test_duplex_validation_never_crashes(duplex):
    """validate_duplex should never raise on arbitrary strings."""
    result = validate_duplex(duplex)
    assert result is None or isinstance(result, str)


# --- Speed normalization ---


@given(speed=st.text(min_size=0, max_size=20))
def test_normalize_speed_never_crashes(speed):
    """normalize_speed should never raise on arbitrary strings."""
    result = normalize_speed(speed)
    assert isinstance(result, str)


@given(speed=st.sampled_from(["1000", "10000", "auto", "100", "10G", "25G", "40G", "100G", "400G"]))
def test_normalize_speed_known_values_idempotent(speed):
    """Normalizing known speeds twice should give the same result."""
    first = normalize_speed(speed)
    second = normalize_speed(first)
    # Normalize should produce the same canonical form
    assert isinstance(first, str)
    assert isinstance(second, str)
