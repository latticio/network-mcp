"""Tests for multi-vendor workflow tools (net_diagnose_bgp_session, etc.).

Validates that the vendor-agnostic workflow tools in tools/common/workflows.py
work correctly with mock drivers from all 4 vendors (EOS, IOS-XE, NX-OS, JunOS).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.mock_data.eos.mock_driver import MockEosDriver
from tests.mock_data.iosxe.mock_driver import MockIosXeDriver
from tests.mock_data.junos.mock_driver import MockJunosDriver
from tests.mock_data.nxos.mock_driver import MockNxosDriver

ALL_DRIVERS = [MockEosDriver, MockIosXeDriver, MockNxosDriver, MockJunosDriver]
DRIVER_IDS = ["eos", "iosxe", "nxos", "junos"]


def _patch_conn_mgr_get_driver(driver_instance):
    """Patch conn_mgr.get_driver to return a mock driver instance."""
    from network_mcp.server import conn_mgr

    return patch.object(conn_mgr, "get_driver", return_value=driver_instance)


# --- net_diagnose_bgp_session ---


class TestDiagnoseBgpSession:
    """Test net_diagnose_bgp_session workflow tool."""

    @pytest.fixture(params=ALL_DRIVERS, ids=DRIVER_IDS)
    def driver(self, request):
        d = request.param()
        d.connect()
        yield d
        d.close()

    def test_established_bgp_neighbor(self, driver):
        """Diagnose a known Established BGP neighbor."""
        from network_mcp.tools.common.workflows import net_diagnose_bgp_session

        bgp = driver.get_bgp_summary()
        established_peer = None
        for ip, data in bgp["peers"].items():
            # IOS-XE returns "established" (lowercase), others use "Established"
            if data["state"].lower() == "established":
                established_peer = ip
                break
        assert established_peer is not None, "Mock data should have an Established peer"

        with _patch_conn_mgr_get_driver(driver):
            result = net_diagnose_bgp_session("test-device", established_peer)

        assert result["status"] == "success"
        assert result["device"] == "test-device"
        assert "diagnosis" in result
        diagnosis = result["diagnosis"]
        assert diagnosis["bgp_state"].lower() == "established"
        assert diagnosis["neighbor_ip"] == established_peer
        assert len(diagnosis["findings"]) > 0

    def test_unknown_bgp_neighbor(self, driver):
        """Diagnose an unknown BGP neighbor."""
        from network_mcp.tools.common.workflows import net_diagnose_bgp_session

        with _patch_conn_mgr_get_driver(driver):
            result = net_diagnose_bgp_session("test-device", "192.168.99.99")

        assert result["status"] == "success"
        diagnosis = result["diagnosis"]
        assert diagnosis["bgp_state"] == "not_found"
        assert diagnosis["severity"] == "critical"
        assert any("not found" in f for f in diagnosis["findings"])

    def test_active_bgp_neighbor(self, driver):
        """Diagnose an Active (non-established) BGP neighbor."""
        from network_mcp.tools.common.workflows import net_diagnose_bgp_session

        bgp = driver.get_bgp_summary()
        active_peer = None
        for ip, data in bgp["peers"].items():
            if data["state"].lower() != "established":
                active_peer = ip
                break

        if active_peer is None:
            pytest.skip("No non-Established peer in mock data")

        with _patch_conn_mgr_get_driver(driver):
            result = net_diagnose_bgp_session("test-device", active_peer)

        assert result["status"] == "success"
        diagnosis = result["diagnosis"]
        assert diagnosis["bgp_state"].lower() != "established"
        assert diagnosis["severity"] in ("warning", "critical")

    def test_result_structure(self, driver):
        """Verify result dict has all expected keys."""
        from network_mcp.tools.common.workflows import net_diagnose_bgp_session

        with _patch_conn_mgr_get_driver(driver):
            result = net_diagnose_bgp_session("test-device", "10.0.0.2")

        assert "status" in result
        assert "device" in result
        assert "vendor" in result
        assert "platform" in result
        assert "diagnosis" in result

    def test_error_handling(self):
        """Driver errors should return error status."""
        from network_mcp.server import conn_mgr
        from network_mcp.tools.common.workflows import net_diagnose_bgp_session

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("mock error")):
            result = net_diagnose_bgp_session("test-device", "10.0.0.2")

        assert result["status"] == "error"
        assert "mock error" in result["error"]


# --- net_troubleshoot_interface ---


class TestTroubleshootInterface:
    """Test net_troubleshoot_interface workflow tool."""

    @pytest.fixture(params=ALL_DRIVERS, ids=DRIVER_IDS)
    def driver(self, request):
        d = request.param()
        d.connect()
        yield d
        d.close()

    def test_up_interface(self, driver):
        """Troubleshoot an interface that is up."""
        from network_mcp.tools.common.workflows import net_troubleshoot_interface

        interfaces = driver.get_interfaces()
        up_intf = None
        for name, data in interfaces.items():
            if data["is_up"]:
                up_intf = name
                break
        assert up_intf is not None, "Mock data should have an up interface"

        with _patch_conn_mgr_get_driver(driver):
            result = net_troubleshoot_interface("test-device", up_intf)

        assert result["status"] == "success"
        report = result["report"]
        assert report["link_state"] == "up"
        assert report["admin_state"] == "enabled"
        assert report["severity"] == "healthy"

    def test_down_interface(self, driver):
        """Troubleshoot an interface that is down."""
        from network_mcp.tools.common.workflows import net_troubleshoot_interface

        interfaces = driver.get_interfaces()
        down_intf = None
        for name, data in interfaces.items():
            if not data["is_up"]:
                down_intf = name
                break
        assert down_intf is not None, "Mock data should have a down interface"

        with _patch_conn_mgr_get_driver(driver):
            result = net_troubleshoot_interface("test-device", down_intf)

        assert result["status"] == "success"
        report = result["report"]
        assert report["link_state"] == "down"
        assert report["severity"] in ("warning", "info")

    def test_nonexistent_interface(self, driver):
        """Troubleshoot a nonexistent interface returns error."""
        from network_mcp.tools.common.workflows import net_troubleshoot_interface

        with _patch_conn_mgr_get_driver(driver):
            result = net_troubleshoot_interface("test-device", "NonExistent99")

        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_result_structure(self, driver):
        """Verify result dict has all expected keys."""
        from network_mcp.tools.common.workflows import net_troubleshoot_interface

        interfaces = driver.get_interfaces()
        first_intf = next(iter(interfaces))

        with _patch_conn_mgr_get_driver(driver):
            result = net_troubleshoot_interface("test-device", first_intf)

        assert "status" in result
        assert "device" in result
        assert "vendor" in result
        assert "platform" in result
        assert "report" in result
        report = result["report"]
        assert "interface" in report
        assert "findings" in report
        assert "link_state" in report
        assert "severity" in report

    def test_error_handling(self):
        """Driver errors should return error status."""
        from network_mcp.server import conn_mgr
        from network_mcp.tools.common.workflows import net_troubleshoot_interface

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("mock error")):
            result = net_troubleshoot_interface("test-device", "Ethernet1")

        assert result["status"] == "error"
        assert "mock error" in result["error"]


# --- net_validate_fabric ---


class TestValidateFabric:
    """Test net_validate_fabric workflow tool."""

    def _mock_inventory(self, drivers_map: dict[str, object]):
        """Create patches for conn_mgr inventory methods and get_driver."""
        from network_mcp.server import conn_mgr

        patches = {}

        def mock_get_driver(host, **kwargs):
            return drivers_map[host]

        patches["get_driver"] = patch.object(conn_mgr, "get_driver", side_effect=mock_get_driver)
        patches["list_devices"] = patch.object(conn_mgr, "list_devices", return_value=list(drivers_map.keys()))
        patches["get_devices_by_role"] = patch.object(
            conn_mgr, "get_devices_by_role", return_value=list(drivers_map.keys())
        )
        return patches

    def test_single_healthy_device(self):
        """Fabric with one healthy device should report status."""
        from network_mcp.tools.common.workflows import net_validate_fabric

        driver = MockEosDriver()
        driver.connect()
        patches = self._mock_inventory({"spine-01": driver})

        with patches["get_driver"], patches["list_devices"]:
            result = net_validate_fabric()

        assert result["status"] == "success"
        assert result["summary"]["total"] == 1
        assert result["summary"]["succeeded"] == 1
        assert result["summary"]["bgp_peers_total"] > 0
        assert len(result["results"]) == 1

    def test_multi_vendor_fabric(self):
        """Fabric with multiple vendors."""
        from network_mcp.tools.common.workflows import net_validate_fabric

        eos = MockEosDriver()
        eos.connect()
        nxos = MockNxosDriver()
        nxos.connect()
        junos = MockJunosDriver()
        junos.connect()

        drivers_map = {
            "eos-spine-01": eos,
            "nxos-leaf-01": nxos,
            "junos-border-01": junos,
        }
        patches = self._mock_inventory(drivers_map)

        with patches["get_driver"], patches["list_devices"]:
            result = net_validate_fabric()

        assert result["status"] == "success"
        assert result["summary"]["total"] == 3
        assert result["summary"]["succeeded"] == 3
        assert len(result["results"]) == 3
        vendors = {d["vendor"] for d in result["results"].values()}
        assert "arista" in vendors
        assert "cisco" in vendors
        assert "juniper" in vendors

    def test_role_filter(self):
        """Fabric validation with role filter."""
        from network_mcp.tools.common.workflows import net_validate_fabric

        driver = MockJunosDriver()
        driver.connect()
        patches = self._mock_inventory({"junos-spine-01": driver})

        with patches["get_driver"], patches["get_devices_by_role"]:
            result = net_validate_fabric(role="spine")

        assert result["status"] == "success"
        assert result["summary"]["role_filter"] == "spine"

    def test_empty_inventory(self):
        """Empty inventory should return error."""
        from network_mcp.server import conn_mgr
        from network_mcp.tools.common.workflows import net_validate_fabric

        with patch.object(conn_mgr, "list_devices", return_value=[]):
            result = net_validate_fabric()

        assert result["status"] == "error"
        assert "No devices found" in result["error"]

    def test_device_error_handling(self):
        """Fabric validation should handle individual device errors gracefully."""
        from network_mcp.server import conn_mgr
        from network_mcp.tools.common.workflows import net_validate_fabric

        good_driver = MockEosDriver()
        good_driver.connect()

        def mock_get_driver(host, **kwargs):
            if host == "bad-device":
                raise ConnectionError("unreachable")
            return good_driver

        with (
            patch.object(conn_mgr, "list_devices", return_value=["good-device", "bad-device"]),
            patch.object(conn_mgr, "get_driver", side_effect=mock_get_driver),
        ):
            result = net_validate_fabric()

        assert result["status"] in ("success", "partial")
        assert result["fabric_healthy"] is False
        assert len(result["errors"]) == 1
        assert result["summary"]["total"] == 2

    def test_result_structure(self):
        """Verify result dict has all expected keys."""
        from network_mcp.tools.common.workflows import net_validate_fabric

        driver = MockEosDriver()
        driver.connect()
        patches = self._mock_inventory({"test-01": driver})

        with patches["get_driver"], patches["list_devices"]:
            result = net_validate_fabric()

        assert "status" in result
        assert "fabric_healthy" in result
        assert "summary" in result
        assert "results" in result
        summary = result["summary"]
        assert "total" in summary
        assert "succeeded" in summary
        assert "failed" in summary
        assert "bgp_peers_total" in summary
        assert "bgp_peers_established" in summary
        assert "interfaces_total" in summary
        assert "interfaces_up" in summary


# --- handle_tool_errors decorator tests ---


class TestHandleToolErrorsOnWorkflows:
    """Verify @handle_tool_errors wraps workflow tools with standardized error dicts."""

    def test_diagnose_bgp_session_timeout_error(self):
        from network_mcp.server import conn_mgr
        from network_mcp.tools.common.workflows import net_diagnose_bgp_session

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_diagnose_bgp_session("spine-01", "10.0.0.2")
        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "Timeout" in result["error"]
        assert "timed out" in result["error"]

    def test_troubleshoot_interface_timeout_error(self):
        from network_mcp.server import conn_mgr
        from network_mcp.tools.common.workflows import net_troubleshoot_interface

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("read timeout")):
            result = net_troubleshoot_interface("leaf-01", "Ethernet1")
        assert result["status"] == "error"
        assert result["device"] == "leaf-01"
        assert "Timeout" in result["error"]

    def test_validate_fabric_unexpected_error(self):
        from network_mcp.server import conn_mgr
        from network_mcp.tools.common.workflows import net_validate_fabric

        with patch.object(conn_mgr, "list_devices", side_effect=RuntimeError("inventory error")):
            result = net_validate_fabric()
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]
        assert "inventory error" in result["error"]
