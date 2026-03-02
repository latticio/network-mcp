"""Comprehensive tests for the NetBox DCIM integration module."""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures — mock pynetbox records and API client
# ---------------------------------------------------------------------------


def _make_nb_device(
    name="spine-01",
    primary_ip="10.0.0.1/32",
    platform="arista-eos",
    role="spine",
    site="dc1",
    serial="SN12345",
    tags=None,
    device_type="DCS-7280SR-48C6",
):
    """Build a mock object that behaves like a pynetbox device record."""
    dev = MagicMock()
    dev.name = name
    if primary_ip:
        dev.primary_ip = MagicMock()
        dev.primary_ip.__str__ = lambda self, _ip=primary_ip: _ip
    else:
        dev.primary_ip = None
    if platform:
        dev.platform = MagicMock()
        dev.platform.__str__ = lambda self, _p=platform: _p
    else:
        dev.platform = None
    if role:
        dev.role = MagicMock()
        dev.role.__str__ = lambda self, _r=role: _r
    else:
        dev.role = None
    if site:
        dev.site = MagicMock()
        dev.site.__str__ = lambda self, _s=site: _s
    else:
        dev.site = None
    dev.serial = serial
    if device_type:
        dev.device_type = MagicMock()
        dev.device_type.__str__ = lambda self, _dt=device_type: _dt
    else:
        dev.device_type = None
    tag_mocks = []
    for t in tags or []:
        tm = MagicMock()
        tm.__str__ = lambda self, _t=t: _t
        tag_mocks.append(tm)
    dev.tags = tag_mocks
    return dev


def _make_nb_cable(cable_id, a_device, a_iface, b_device, b_iface):
    """Build a mock cable record with termination objects."""
    cable = MagicMock()
    cable.id = cable_id

    a_obj = MagicMock()
    a_obj.device = MagicMock()
    a_obj.device.__str__ = lambda self, _d=a_device: _d
    a_obj.__str__ = lambda self, _i=a_iface: _i
    a_term = MagicMock()
    a_term.object = a_obj

    b_obj = MagicMock()
    b_obj.device = MagicMock()
    b_obj.device.__str__ = lambda self, _d=b_device: _d
    b_obj.__str__ = lambda self, _i=b_iface: _i
    b_term = MagicMock()
    b_term.object = b_obj

    cable.a_terminations = [a_term]
    cable.b_terminations = [b_term]
    return cable


@pytest.fixture
def mock_netbox():
    """Create a mock pynetbox API client with default device."""
    nb = MagicMock()
    nb.dcim.devices.filter.return_value = [_make_nb_device()]
    nb.dcim.cables.filter.return_value = []
    return nb


@pytest.fixture
def _patch_settings():
    """Patch settings so NETBOX_URL and NETBOX_TOKEN are configured."""
    with patch("network_mcp.integrations.netbox.settings") as mock_settings:
        mock_settings.net_demo_mode = False
        mock_settings.netbox_url = "https://netbox.example.com"
        mock_settings.netbox_token = MagicMock()
        mock_settings.netbox_token.get_secret_value.return_value = "test-token"
        yield mock_settings


@pytest.fixture
def _patch_settings_empty():
    """Patch settings so NETBOX_URL and NETBOX_TOKEN are NOT configured.

    Also patches pynetbox to be non-None so the pynetbox-installed check passes
    and we reach the not-configured check.
    """
    with (
        patch("network_mcp.integrations.netbox.settings") as mock_settings,
        patch("network_mcp.integrations.netbox.pynetbox", MagicMock()),
    ):
        mock_settings.net_demo_mode = False
        mock_settings.netbox_url = None
        mock_settings.netbox_token = None
        yield mock_settings


# ---------------------------------------------------------------------------
# net_sync_from_netbox tests
# ---------------------------------------------------------------------------


class TestSyncFromNetbox:
    """Tests for net_sync_from_netbox()."""

    def test_sync_all_active_devices(self, mock_netbox, _patch_settings):
        """Sync with no filters returns all active devices."""
        devices = [
            _make_nb_device(name="spine-01", primary_ip="10.0.0.1/32"),
            _make_nb_device(name="leaf-01", primary_ip="10.0.0.2/32", role="leaf"),
        ]
        mock_netbox.dcim.devices.filter.return_value = devices

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["status"] == "success"
        assert result["synced_count"] == 2
        assert len(result["devices"]) == 2
        # Verify default filter is status=active
        mock_netbox.dcim.devices.filter.assert_called_once_with(status="active")

    def test_sync_with_site_filter(self, mock_netbox, _patch_settings):
        """Sync with site filter only returns devices from that site."""
        mock_netbox.dcim.devices.filter.return_value = [_make_nb_device(site="dc1")]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox(site="dc1")

        assert result["status"] == "success"
        mock_netbox.dcim.devices.filter.assert_called_once_with(status="active", site="dc1")

    def test_sync_with_role_filter(self, mock_netbox, _patch_settings):
        """Sync with role filter only returns devices with that role."""
        mock_netbox.dcim.devices.filter.return_value = [_make_nb_device(role="spine")]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox(role="spine")

        assert result["status"] == "success"
        mock_netbox.dcim.devices.filter.assert_called_once_with(status="active", role="spine")

    def test_sync_with_tag_filter(self, mock_netbox, _patch_settings):
        """Sync with tag filter only returns tagged devices."""
        mock_netbox.dcim.devices.filter.return_value = [_make_nb_device(tags=["production"])]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox(tag="production")

        assert result["status"] == "success"
        mock_netbox.dcim.devices.filter.assert_called_once_with(status="active", tag="production")
        assert result["devices"][0]["tags"] == ["production"]

    @pytest.mark.parametrize(
        "nb_platform,expected",
        [
            ("arista-eos", "eos"),
            ("arista_eos", "eos"),
            ("cisco-ios-xe", "iosxe"),
            ("cisco_ios_xe", "iosxe"),
            ("cisco-nxos", "nxos"),
            ("cisco_nxos", "nxos"),
            ("juniper-junos", "junos"),
            ("juniper_junos", "junos"),
            ("Arista-EOS", "eos"),  # case insensitive
        ],
    )
    def test_platform_mapping(self, mock_netbox, _patch_settings, nb_platform, expected):
        """NetBox platform slugs are mapped to network-mcp platform identifiers."""
        mock_netbox.dcim.devices.filter.return_value = [_make_nb_device(platform=nb_platform)]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["devices"][0]["platform"] == expected

    def test_unknown_platform_defaults_to_eos(self, mock_netbox, _patch_settings):
        """Unknown platform slug defaults to 'eos'."""
        mock_netbox.dcim.devices.filter.return_value = [_make_nb_device(platform="custom-platform")]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["devices"][0]["platform"] == "eos"

    def test_no_platform_defaults_to_eos(self, mock_netbox, _patch_settings):
        """Device with no platform set defaults to 'eos'."""
        mock_netbox.dcim.devices.filter.return_value = [_make_nb_device(platform=None)]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["devices"][0]["platform"] == "eos"

    def test_device_with_no_primary_ip_uses_name(self, mock_netbox, _patch_settings):
        """Device with no primary_ip uses device name as host."""
        mock_netbox.dcim.devices.filter.return_value = [_make_nb_device(name="spine-01", primary_ip=None)]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["devices"][0]["host"] == "spine-01"

    def test_empty_result_set(self, mock_netbox, _patch_settings):
        """Empty result set returns synced_count: 0."""
        mock_netbox.dcim.devices.filter.return_value = []

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["status"] == "success"
        assert result["synced_count"] == 0
        assert result["devices"] == []

    def test_device_fields_populated(self, mock_netbox, _patch_settings):
        """Verify all expected fields are populated in the synced device dict."""
        mock_netbox.dcim.devices.filter.return_value = [
            _make_nb_device(
                name="leaf-01",
                primary_ip="10.0.1.1/32",
                platform="cisco-nxos",
                role="leaf",
                site="dc2",
                serial="SN99999",
                tags=["prod", "critical"],
            )
        ]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        dev = result["devices"][0]
        assert dev["name"] == "leaf-01"
        assert dev["host"] == "10.0.1.1"  # /32 stripped
        assert dev["platform"] == "nxos"
        assert dev["role"] == "leaf"
        assert dev["site"] == "dc2"
        assert dev["serial"] == "SN99999"
        assert dev["tags"] == ["prod", "critical"]


# ---------------------------------------------------------------------------
# net_push_to_netbox tests
# ---------------------------------------------------------------------------


class TestPushToNetbox:
    """Tests for net_push_to_netbox()."""

    def test_existing_device_updates_serial(self, mock_netbox, _patch_settings):
        """Existing device — updates serial number, returns NetBox ID."""
        nb_device = MagicMock()
        nb_device.id = 42
        mock_netbox.dcim.devices.get.return_value = nb_device

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info") as mock_info,
        ):
            mock_pynetbox.api.return_value = mock_netbox
            mock_info.return_value = {
                "status": "success",
                "data": {"hostname": "spine-01", "serial": "NEW_SERIAL_123"},
            }

            from network_mcp.integrations.netbox import net_push_to_netbox

            result = net_push_to_netbox("spine-01")

        assert result["status"] == "success"
        assert result["action"] == "updated"
        assert result["netbox_id"] == 42
        assert result["updated_fields"]["serial"] == "NEW_SERIAL_123"
        nb_device.save.assert_called_once()

    def test_missing_device_returns_error(self, mock_netbox, _patch_settings):
        """Missing device — returns error with clear message."""
        mock_netbox.dcim.devices.get.return_value = None

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info") as mock_info,
        ):
            mock_pynetbox.api.return_value = mock_netbox
            mock_info.return_value = {
                "status": "success",
                "data": {"hostname": "unknown-host", "serial": "SN999"},
            }

            from network_mcp.integrations.netbox import net_push_to_netbox

            result = net_push_to_netbox("unknown-host")

        assert result["status"] == "error"
        assert "not found in NetBox" in result["error"]

    def test_device_facts_fetch_fails(self, mock_netbox, _patch_settings):
        """Device facts fetch fails — propagates error."""
        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info") as mock_info,
        ):
            mock_pynetbox.api.return_value = mock_netbox
            mock_info.return_value = {
                "status": "error",
                "error": "Connection refused",
                "device": "spine-01",
            }

            from network_mcp.integrations.netbox import net_push_to_netbox

            result = net_push_to_netbox("spine-01")

        assert result["status"] == "error"
        assert "Connection refused" in result["error"]


# ---------------------------------------------------------------------------
# net_netbox_drift_check tests
# ---------------------------------------------------------------------------


class TestDriftCheck:
    """Tests for net_netbox_drift_check()."""

    def test_matching_state_no_drifts(self, mock_netbox, _patch_settings):
        """Matching state — no drifts reported."""
        dev = _make_nb_device(serial="SN12345", device_type="DCS-7280SR-48C6")
        mock_netbox.dcim.devices.filter.return_value = [dev]

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info") as mock_info,
        ):
            mock_pynetbox.api.return_value = mock_netbox
            mock_info.return_value = {
                "status": "success",
                "data": {"serial": "SN12345", "model": "DCS-7280SR-48C6"},
            }

            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()

        assert result["status"] == "success"
        assert result["drift_count"] == 0
        assert result["drifts"] == []
        assert result["devices_checked"] == 1

    def test_serial_number_mismatch(self, mock_netbox, _patch_settings):
        """Serial number mismatch — warning severity drift."""
        dev = _make_nb_device(serial="NB_SERIAL")
        mock_netbox.dcim.devices.filter.return_value = [dev]

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info") as mock_info,
        ):
            mock_pynetbox.api.return_value = mock_netbox
            mock_info.return_value = {
                "status": "success",
                "data": {"serial": "LIVE_SERIAL", "model": "DCS-7280SR-48C6"},
            }

            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()

        assert result["drift_count"] >= 1
        serial_drift = [d for d in result["drifts"] if d.get("field") == "serial"]
        assert len(serial_drift) == 1
        assert serial_drift[0]["severity"] == "warning"
        assert serial_drift[0]["netbox"] == "NB_SERIAL"
        assert serial_drift[0]["live"] == "LIVE_SERIAL"

    def test_unreachable_device_critical(self, mock_netbox, _patch_settings):
        """Unreachable device — critical severity drift."""
        dev = _make_nb_device()
        mock_netbox.dcim.devices.filter.return_value = [dev]

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info") as mock_info,
        ):
            mock_pynetbox.api.return_value = mock_netbox
            mock_info.return_value = {
                "status": "error",
                "error": "Connection timed out",
            }

            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()

        assert result["drift_count"] == 1
        drift = result["drifts"][0]
        assert drift["severity"] == "critical"
        assert drift["drift"] == "unreachable"
        assert "Connection timed out" in drift["detail"]

    def test_device_no_primary_ip_skipped(self, mock_netbox, _patch_settings):
        """Device with no primary_ip — reported as warning drift, not checked live."""
        dev = _make_nb_device(primary_ip=None)
        mock_netbox.dcim.devices.filter.return_value = [dev]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()

        assert result["status"] == "success"
        assert result["devices_checked"] == 0
        assert result["drift_count"] == 1
        assert result["drifts"][0]["field"] == "primary_ip"
        assert result["drifts"][0]["severity"] == "warning"

    def test_mixed_results(self, mock_netbox, _patch_settings):
        """Mixed results — some matching, some drifting."""
        good_dev = _make_nb_device(name="spine-01", serial="MATCH1", primary_ip="10.0.0.1/32")
        drift_dev = _make_nb_device(name="leaf-01", serial="WRONG_SN", primary_ip="10.0.0.2/32")
        no_ip_dev = _make_nb_device(name="mgmt-01", primary_ip=None)
        mock_netbox.dcim.devices.filter.return_value = [good_dev, drift_dev, no_ip_dev]

        def _mock_info(host):
            if host == "10.0.0.1":
                return {"status": "success", "data": {"serial": "MATCH1", "model": "DCS-7280SR-48C6"}}
            if host == "10.0.0.2":
                return {"status": "success", "data": {"serial": "ACTUAL_SN", "model": "DCS-7280SR-48C6"}}
            return {"status": "error", "error": "Unknown host"}

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info", side_effect=_mock_info),
        ):
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()

        assert result["status"] == "success"
        assert result["total_devices"] == 3
        assert result["devices_checked"] == 2  # no_ip_dev skipped
        assert result["drift_count"] == 2  # serial mismatch + no primary_ip

    def test_site_filter_passed(self, mock_netbox, _patch_settings):
        """Site filter is passed through to the NetBox query."""
        mock_netbox.dcim.devices.filter.return_value = []

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check(site="dc1")

        assert result["status"] == "success"
        mock_netbox.dcim.devices.filter.assert_called_once_with(status="active", site="dc1")

    def test_model_mismatch_detected(self, mock_netbox, _patch_settings):
        """Model mismatch between NetBox device_type and live model is detected."""
        dev = _make_nb_device(serial="SN12345", device_type="DCS-7280SR-48C6")
        mock_netbox.dcim.devices.filter.return_value = [dev]

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info") as mock_info,
        ):
            mock_pynetbox.api.return_value = mock_netbox
            mock_info.return_value = {
                "status": "success",
                "data": {"serial": "SN12345", "model": "DCS-7050SX3-48YC12"},
            }

            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()

        model_drift = [d for d in result["drifts"] if d.get("field") == "model"]
        assert len(model_drift) == 1
        assert model_drift[0]["severity"] == "warning"
        assert model_drift[0]["netbox"] == "DCS-7280SR-48C6"
        assert model_drift[0]["live"] == "DCS-7050SX3-48YC12"


# ---------------------------------------------------------------------------
# net_netbox_cable_audit tests
# ---------------------------------------------------------------------------


class TestCableAudit:
    """Tests for net_netbox_cable_audit()."""

    def test_matching_cables_no_mismatches(self, mock_netbox, _patch_settings):
        """Matching cables — no mismatches."""
        dev_a = _make_nb_device(name="spine-01", primary_ip="10.0.0.1/32")
        dev_b = _make_nb_device(name="leaf-01", primary_ip="10.0.0.2/32")
        mock_netbox.dcim.devices.filter.return_value = [dev_a, dev_b]

        cable = _make_nb_cable(1, "spine-01", "Ethernet1", "leaf-01", "Ethernet1")
        mock_netbox.dcim.cables.filter.return_value = [cable]

        lldp_result = {
            "status": "success",
            "data": {
                "Ethernet1": [{"hostname": "leaf-01", "port": "Ethernet1"}],
            },
        }

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.switching.net_get_lldp_neighbors", return_value=lldp_result),
        ):
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_netbox_cable_audit

            result = net_netbox_cable_audit()

        assert result["status"] == "success"
        assert result["mismatch_count"] == 0
        assert result["cables_checked"] == 1

    def test_mismatched_cable_endpoint(self, mock_netbox, _patch_settings):
        """Mismatched cable endpoint — reported in results."""
        dev_a = _make_nb_device(name="spine-01", primary_ip="10.0.0.1/32")
        dev_b = _make_nb_device(name="leaf-01", primary_ip="10.0.0.2/32")
        mock_netbox.dcim.devices.filter.return_value = [dev_a, dev_b]

        # Cable says spine-01:Eth1 <-> leaf-01:Eth1
        cable = _make_nb_cable(1, "spine-01", "Ethernet1", "leaf-01", "Ethernet1")
        mock_netbox.dcim.cables.filter.return_value = [cable]

        # But LLDP says spine-01:Eth1 neighbor is leaf-02 (not leaf-01)
        lldp_result = {
            "status": "success",
            "data": {
                "Ethernet1": [{"hostname": "leaf-02", "port": "Ethernet1"}],
            },
        }

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.switching.net_get_lldp_neighbors", return_value=lldp_result),
        ):
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_netbox_cable_audit

            result = net_netbox_cable_audit()

        assert result["status"] == "success"
        assert result["mismatch_count"] == 1
        mismatch = result["mismatches"][0]
        assert mismatch["cable_id"] == 1
        assert mismatch["side_a"]["device"] == "spine-01"
        assert mismatch["side_b_expected"]["device"] == "leaf-01"
        assert "leaf-02" in mismatch["side_b_actual"]

    def test_empty_cable_list(self, mock_netbox, _patch_settings):
        """Empty cable list — returns 0 checked."""
        mock_netbox.dcim.devices.filter.return_value = []
        mock_netbox.dcim.cables.filter.return_value = []

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_netbox_cable_audit

            result = net_netbox_cable_audit()

        assert result["status"] == "success"
        assert result["cables_checked"] == 0
        assert result["mismatch_count"] == 0

    def test_no_lldp_neighbor_reported_as_info(self, mock_netbox, _patch_settings):
        """Interface with cable but no LLDP neighbor — reported as info severity."""
        dev_a = _make_nb_device(name="spine-01", primary_ip="10.0.0.1/32")
        mock_netbox.dcim.devices.filter.return_value = [dev_a]

        cable = _make_nb_cable(1, "spine-01", "Ethernet1", "leaf-01", "Ethernet1")
        mock_netbox.dcim.cables.filter.return_value = [cable]

        # LLDP data has no entry for Ethernet1
        lldp_result = {
            "status": "success",
            "data": {},
        }

        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.switching.net_get_lldp_neighbors", return_value=lldp_result),
        ):
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_netbox_cable_audit

            result = net_netbox_cable_audit()

        assert result["mismatch_count"] == 1
        assert result["mismatches"][0]["severity"] == "info"
        assert "No LLDP neighbor" in result["mismatches"][0]["detail"]

    def test_cable_with_no_a_side_ip_skipped(self, mock_netbox, _patch_settings):
        """Cable where side-A device has no primary IP is skipped."""
        # Device without primary IP in the device list
        dev_a = _make_nb_device(name="spine-01", primary_ip=None)
        mock_netbox.dcim.devices.filter.return_value = [dev_a]

        cable = _make_nb_cable(1, "spine-01", "Ethernet1", "leaf-01", "Ethernet1")
        mock_netbox.dcim.cables.filter.return_value = [cable]

        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.return_value = mock_netbox
            from network_mcp.integrations.netbox import net_netbox_cable_audit

            result = net_netbox_cable_audit()

        assert result["status"] == "success"
        assert result["cables_checked"] == 0


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling across all NetBox tools."""

    def test_netbox_unreachable_sync(self, _patch_settings):
        """NetBox unreachable during sync — clear error message."""
        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.side_effect = ConnectionError("Connection refused")
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["status"] == "error"
        assert "NetBox sync failed" in result["error"]

    def test_netbox_unreachable_drift(self, _patch_settings):
        """NetBox unreachable during drift check — clear error message."""
        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.side_effect = ConnectionError("Connection refused")
            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()

        assert result["status"] == "error"
        assert "drift check failed" in result["error"]

    def test_netbox_unreachable_cable_audit(self, _patch_settings):
        """NetBox unreachable during cable audit — clear error message."""
        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_pynetbox.api.side_effect = ConnectionError("Connection refused")
            from network_mcp.integrations.netbox import net_netbox_cable_audit

            result = net_netbox_cable_audit()

        assert result["status"] == "error"
        assert "cable audit failed" in result["error"]

    def test_netbox_auth_failure_sync(self, _patch_settings):
        """NetBox auth failure (403) during sync — clear error message."""
        with patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox:
            mock_nb = MagicMock()
            mock_pynetbox.api.return_value = mock_nb
            mock_nb.dcim.devices.filter.side_effect = Exception("403 Forbidden")
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["status"] == "error"
        assert "403" in result["error"]

    def test_netbox_auth_failure_push(self, _patch_settings):
        """NetBox auth failure during push — clear error message."""
        with (
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
            patch("network_mcp.tools.common.device.net_get_device_info") as mock_info,
        ):
            mock_nb = MagicMock()
            mock_pynetbox.api.return_value = mock_nb
            mock_info.return_value = {"status": "success", "data": {"hostname": "spine-01", "serial": "SN1"}}
            mock_nb.dcim.devices.get.side_effect = Exception("403 Forbidden")
            from network_mcp.integrations.netbox import net_push_to_netbox

            result = net_push_to_netbox("spine-01")

        assert result["status"] == "error"
        assert "403" in result["error"]

    def test_pynetbox_not_installed_sync(self):
        """pynetbox not installed — sync returns install instructions."""
        with patch("network_mcp.integrations.netbox.pynetbox", None):
            from network_mcp.integrations.netbox import net_sync_from_netbox

            result = net_sync_from_netbox()

        assert result["status"] == "error"
        assert "pynetbox not installed" in result["error"]
        assert "pip install" in result["error"]

    def test_pynetbox_not_installed_push(self):
        """pynetbox not installed — push returns install instructions."""
        with patch("network_mcp.integrations.netbox.pynetbox", None):
            from network_mcp.integrations.netbox import net_push_to_netbox

            result = net_push_to_netbox("spine-01")

        assert result["status"] == "error"
        assert "pynetbox not installed" in result["error"]
        assert result["device"] == "spine-01"

    def test_pynetbox_not_installed_drift(self):
        """pynetbox not installed — drift check returns install instructions."""
        with patch("network_mcp.integrations.netbox.pynetbox", None):
            from network_mcp.integrations.netbox import net_netbox_drift_check

            result = net_netbox_drift_check()

        assert result["status"] == "error"
        assert "pynetbox not installed" in result["error"]

    def test_pynetbox_not_installed_cable(self):
        """pynetbox not installed — cable audit returns install instructions."""
        with patch("network_mcp.integrations.netbox.pynetbox", None):
            from network_mcp.integrations.netbox import net_netbox_cable_audit

            result = net_netbox_cable_audit()

        assert result["status"] == "error"
        assert "pynetbox not installed" in result["error"]


# ---------------------------------------------------------------------------
# Config / _get_netbox_client tests
# ---------------------------------------------------------------------------


class TestConfig:
    """Tests for NetBox configuration and client creation."""

    def test_not_configured_raises_runtime_error(self):
        """NETBOX_URL and NETBOX_TOKEN not set — RuntimeError from _get_netbox_client()."""
        with patch("network_mcp.integrations.netbox.settings") as mock_settings:
            mock_settings.netbox_url = None
            mock_settings.netbox_token = None

            from network_mcp.integrations.netbox import _get_netbox_client

            with pytest.raises(RuntimeError, match="NETBOX_URL and NETBOX_TOKEN must be set"):
                _get_netbox_client()

    def test_url_set_but_token_missing(self):
        """NETBOX_URL set but NETBOX_TOKEN missing — RuntimeError."""
        with patch("network_mcp.integrations.netbox.settings") as mock_settings:
            mock_settings.netbox_url = "https://netbox.example.com"
            mock_settings.netbox_token = None

            from network_mcp.integrations.netbox import _get_netbox_client

            with pytest.raises(RuntimeError, match="NETBOX_URL and NETBOX_TOKEN must be set"):
                _get_netbox_client()

    def test_client_created_with_valid_config(self):
        """Both NETBOX_URL and NETBOX_TOKEN set — client created successfully."""
        with (
            patch("network_mcp.integrations.netbox.settings") as mock_settings,
            patch("network_mcp.integrations.netbox.pynetbox") as mock_pynetbox,
        ):
            mock_settings.netbox_url = "https://netbox.example.com"
            mock_settings.netbox_token = MagicMock()
            mock_settings.netbox_token.get_secret_value.return_value = "test-token"
            mock_pynetbox.api.return_value = MagicMock()

            from network_mcp.integrations.netbox import _get_netbox_client

            client = _get_netbox_client()

        mock_pynetbox.api.assert_called_once_with("https://netbox.example.com", token="test-token")
        assert client is not None

    def test_not_configured_sync_returns_error(self, _patch_settings_empty):
        """Sync when NetBox is not configured returns error (not exception)."""
        from network_mcp.integrations.netbox import net_sync_from_netbox

        result = net_sync_from_netbox()

        assert result["status"] == "error"
        assert "not configured" in result["error"]

    def test_not_configured_push_returns_error(self, _patch_settings_empty):
        """Push when NetBox is not configured returns error (not exception)."""
        from network_mcp.integrations.netbox import net_push_to_netbox

        result = net_push_to_netbox("spine-01")

        assert result["status"] == "error"
        assert "not configured" in result["error"]

    def test_not_configured_drift_returns_error(self, _patch_settings_empty):
        """Drift check when NetBox is not configured returns error (not exception)."""
        from network_mcp.integrations.netbox import net_netbox_drift_check

        result = net_netbox_drift_check()

        assert result["status"] == "error"
        assert "not configured" in result["error"]

    def test_not_configured_cable_returns_error(self, _patch_settings_empty):
        """Cable audit when NetBox is not configured returns error (not exception)."""
        from network_mcp.integrations.netbox import net_netbox_cable_audit

        result = net_netbox_cable_audit()

        assert result["status"] == "error"
        assert "not configured" in result["error"]


# ---------------------------------------------------------------------------
# Platform map coverage
# ---------------------------------------------------------------------------


class TestPlatformMap:
    """Tests for the _PLATFORM_MAP constant."""

    def test_platform_map_has_all_vendors(self):
        """Verify the platform map covers all 4 vendor platforms."""
        from network_mcp.integrations.netbox import _PLATFORM_MAP

        platforms = set(_PLATFORM_MAP.values())
        assert "eos" in platforms
        assert "iosxe" in platforms
        assert "nxos" in platforms
        assert "junos" in platforms

    def test_platform_map_dash_and_underscore(self):
        """Both dash and underscore variants are mapped for each vendor."""
        from network_mcp.integrations.netbox import _PLATFORM_MAP

        assert _PLATFORM_MAP["arista-eos"] == _PLATFORM_MAP["arista_eos"]
        assert _PLATFORM_MAP["cisco-ios-xe"] == _PLATFORM_MAP["cisco_ios_xe"]
        assert _PLATFORM_MAP["cisco-nxos"] == _PLATFORM_MAP["cisco_nxos"]
        assert _PLATFORM_MAP["juniper-junos"] == _PLATFORM_MAP["juniper_junos"]
