"""Property-based tests using Hypothesis.

Verifies invariants that must hold for ALL possible inputs, not just handpicked examples:
- VLAN ID validation: valid range [1, 4094] accepted, everything else rejected
- Interface name sanitization: never raises unhandled exception
- CLI parameter validation: injection chars always rejected, safe strings always accepted
- Hostname validation: valid hostnames accepted, special chars rejected
"""

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from network_mcp.helpers import (
    validate_cli_param,
    validate_host,
    validate_interface_name,
    validate_vlan_id,
)

# ===========================================================================
# VLAN ID Validation Properties
# ===========================================================================


class TestVlanIdProperties:
    """Property-based tests for validate_vlan_id."""

    @given(vlan_id=st.integers(min_value=1, max_value=4094))
    @settings(max_examples=200)
    def test_valid_vlan_ids_always_accepted(self, vlan_id):
        """Any integer in [1, 4094] must be accepted (return None)."""
        result = validate_vlan_id(vlan_id)
        assert result is None, f"VLAN {vlan_id} should be valid but got: {result}"

    @given(vlan_id=st.integers(max_value=0))
    @settings(max_examples=200)
    def test_vlan_ids_below_1_always_rejected(self, vlan_id):
        """Any integer <= 0 must be rejected (return error string)."""
        result = validate_vlan_id(vlan_id)
        assert result is not None, f"VLAN {vlan_id} should be invalid"
        assert "1" in result and "4094" in result

    @given(vlan_id=st.integers(min_value=4095))
    @settings(max_examples=200)
    def test_vlan_ids_above_4094_always_rejected(self, vlan_id):
        """Any integer >= 4095 must be rejected (return error string)."""
        result = validate_vlan_id(vlan_id)
        assert result is not None, f"VLAN {vlan_id} should be invalid"
        assert "1" in result and "4094" in result

    def test_boundary_values(self):
        """Explicit boundary tests: 0, 1, 4094, 4095."""
        assert validate_vlan_id(0) is not None
        assert validate_vlan_id(1) is None
        assert validate_vlan_id(4094) is None
        assert validate_vlan_id(4095) is not None


# ===========================================================================
# Interface Name Validation Properties
# ===========================================================================


class TestInterfaceNameProperties:
    """Property-based tests for validate_interface_name."""

    @given(name=st.text(min_size=0, max_size=200))
    @settings(max_examples=300)
    def test_never_raises_unhandled_exception(self, name):
        """validate_interface_name must never raise; it returns None or a string."""
        result = validate_interface_name(name)
        assert result is None or isinstance(result, str)

    @given(suffix=st.text(alphabet=string.digits + "/", min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_valid_ethernet_names_accepted(self, suffix):
        """Ethernet<digits/slashes> should be accepted."""
        name = f"Ethernet{suffix}"
        result = validate_interface_name(name)
        assert result is None, f"'{name}' should be a valid interface but got: {result}"

    @given(suffix=st.text(alphabet=string.digits + "/", min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_valid_loopback_names_accepted(self, suffix):
        """Loopback<digits> should be accepted."""
        name = f"Loopback{suffix}"
        result = validate_interface_name(name)
        assert result is None, f"'{name}' should be a valid interface but got: {result}"

    @given(suffix=st.text(alphabet=string.digits, min_size=1, max_size=10))
    @settings(max_examples=100)
    def test_valid_vlan_interfaces_accepted(self, suffix):
        """Vlan<digits> should be accepted."""
        name = f"Vlan{suffix}"
        result = validate_interface_name(name)
        assert result is None, f"'{name}' should be a valid interface but got: {result}"

    @given(suffix=st.text(alphabet=string.digits, min_size=1, max_size=10))
    @settings(max_examples=100)
    def test_valid_port_channel_accepted(self, suffix):
        """Port-Channel<digits> should be accepted."""
        name = f"Port-Channel{suffix}"
        result = validate_interface_name(name)
        assert result is None, f"'{name}' should be a valid interface but got: {result}"

    @given(
        name=st.text(
            alphabet=string.ascii_lowercase + string.digits,
            min_size=1,
            max_size=50,
        ).filter(lambda s: not s.startswith(("mgmt", "port", "loopback", "nve")))
    )
    @settings(max_examples=100)
    def test_lowercase_nonprefix_names_rejected(self, name):
        """Names that don't start with a recognized prefix should be rejected."""
        result = validate_interface_name(name)
        assert result is not None, f"'{name}' should not be a valid EOS interface"

    def test_empty_string_rejected(self):
        """Empty string must be rejected."""
        result = validate_interface_name("")
        assert result is not None

    def test_whitespace_only_rejected(self):
        """Whitespace-only string must be rejected."""
        result = validate_interface_name("   ")
        assert result is not None


# ===========================================================================
# CLI Parameter Validation Properties
# ===========================================================================


class TestCliParamProperties:
    """Property-based tests for validate_cli_param."""

    # Characters that are dangerous for CLI injection
    INJECTION_CHARS = [";", "|", "`", "\n", "\r", "\x00"]
    INJECTION_PATTERNS = ["$(cmd)", "${var}"]

    @given(
        value=st.text(
            alphabet=string.ascii_letters + string.digits + "-_.",
            min_size=1,
            max_size=100,
        )
    )
    @settings(max_examples=300)
    def test_safe_strings_always_accepted(self, value):
        """Alphanumeric strings with hyphens, underscores, dots must be accepted."""
        result = validate_cli_param(value, "test_param")
        assert result is None, f"Safe string '{value}' was rejected: {result}"

    @given(
        prefix=st.text(alphabet=string.ascii_letters, min_size=1, max_size=20),
        injection=st.sampled_from(INJECTION_CHARS),
        suffix=st.text(alphabet=string.ascii_letters, min_size=0, max_size=20),
    )
    @settings(max_examples=200)
    def test_injection_chars_always_rejected(self, prefix, injection, suffix):
        """Any string containing ;, |, `, newlines, or null bytes must be rejected."""
        value = prefix + injection + suffix
        result = validate_cli_param(value, "test_param")
        assert result is not None, f"Injection string '{value!r}' was not rejected"
        assert "invalid" in result.lower()

    @given(
        prefix=st.text(alphabet=string.ascii_letters, min_size=1, max_size=20),
        pattern=st.sampled_from(INJECTION_PATTERNS),
        suffix=st.text(alphabet=string.ascii_letters, min_size=0, max_size=20),
    )
    @settings(max_examples=100)
    def test_subshell_patterns_always_rejected(self, prefix, pattern, suffix):
        """$() and ${} patterns must always be rejected."""
        value = prefix + pattern + suffix
        result = validate_cli_param(value, "test_param")
        assert result is not None, f"Subshell pattern '{value}' was not rejected"

    @given(value=st.text(min_size=0, max_size=200))
    @settings(max_examples=300)
    def test_never_raises_unhandled_exception(self, value):
        """validate_cli_param must never raise; it returns None or a string."""
        result = validate_cli_param(value, "param")
        assert result is None or isinstance(result, str)

    def test_empty_string_rejected(self):
        """Empty string must be rejected."""
        result = validate_cli_param("", "test")
        assert result is not None

    def test_whitespace_only_rejected(self):
        """Whitespace-only string must be rejected."""
        result = validate_cli_param("   ", "test")
        assert result is not None


# ===========================================================================
# Hostname Validation Properties
# ===========================================================================


class TestHostnameProperties:
    """Property-based tests for validate_host."""

    @given(hostname=st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,50}", fullmatch=True))
    @settings(max_examples=300)
    def test_valid_hostnames_accepted(self, hostname):
        """Hostnames matching [a-zA-Z0-9._:-]+ must be accepted."""
        result = validate_host(hostname)
        assert result is None, f"Valid hostname '{hostname}' was rejected: {result}"

    @given(
        base=st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=10),
        special=st.sampled_from(["!", "@", "#", "$", "%", "^", "&", "*", "(", ")", " ", "\t"]),
        tail=st.text(alphabet=string.ascii_letters, min_size=0, max_size=10),
    )
    @settings(max_examples=200)
    def test_special_chars_rejected(self, base, special, tail):
        """Hostnames containing special characters must be rejected."""
        hostname = base + special + tail
        result = validate_host(hostname)
        assert result is not None, f"Hostname with special char '{hostname}' should be rejected"

    @given(hostname=st.text(min_size=0, max_size=300))
    @settings(max_examples=300)
    def test_never_raises_unhandled_exception(self, hostname):
        """validate_host must never raise; it returns None or a string."""
        result = validate_host(hostname)
        assert result is None or isinstance(result, str)

    def test_empty_string_rejected(self):
        """Empty string must be rejected."""
        result = validate_host("")
        assert result is not None

    def test_whitespace_only_rejected(self):
        """Whitespace-only string must be rejected."""
        result = validate_host("   ")
        assert result is not None

    def test_ip_addresses_accepted(self):
        """Standard IPv4 addresses must be accepted."""
        assert validate_host("10.0.0.1") is None
        assert validate_host("192.168.1.1") is None
        assert validate_host("172.16.0.1") is None

    def test_ipv6_colons_accepted(self):
        """IPv6-style addresses with colons must be accepted."""
        assert validate_host("::1") is None
        assert validate_host("fe80::1") is None

    def test_too_long_rejected(self):
        """Hostnames over 253 characters must be rejected."""
        long_host = "a" * 254
        result = validate_host(long_host)
        assert result is not None
        assert "253" in result

    @given(
        octets=st.tuples(
            st.integers(min_value=0, max_value=255),
            st.integers(min_value=0, max_value=255),
            st.integers(min_value=0, max_value=255),
            st.integers(min_value=0, max_value=255),
        )
    )
    @settings(max_examples=100)
    def test_ipv4_addresses_always_accepted(self, octets):
        """Any valid IPv4 dotted-decimal address must be accepted."""
        ip = f"{octets[0]}.{octets[1]}.{octets[2]}.{octets[3]}"
        result = validate_host(ip)
        assert result is None, f"IPv4 address '{ip}' was rejected: {result}"
