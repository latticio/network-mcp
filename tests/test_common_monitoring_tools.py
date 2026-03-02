"""Tests for vendor-agnostic monitoring and environment tools.

Tests net_get_cpu_usage, net_get_memory_usage, net_get_environment_temp,
net_get_environment_power, net_get_environment_cooling, net_get_ntp_status,
net_get_device_health, and net_get_logging.
Validates response structure, NotSupportedError handling, error handling,
and health aggregation logic.
"""

from unittest.mock import MagicMock, patch

import pytest

from network_mcp.drivers.base import NotSupportedError
from network_mcp.server import conn_mgr

# --- Mock data ---

MOCK_CPU_MEMORY = {
    "cpu_percent": 23.5,
    "memory_total": 8_000_000,
    "memory_used": 4_500_000,
    "memory_free": 3_500_000,
}

MOCK_CPU_MEMORY_HIGH = {
    "cpu_percent": 95.0,
    "memory_total": 8_000_000,
    "memory_used": 7_500_000,
    "memory_free": 500_000,
}

MOCK_CPU_MEMORY_ZERO_TOTAL = {
    "cpu_percent": 0.0,
    "memory_total": 0,
    "memory_used": 0,
    "memory_free": 0,
}

MOCK_ENVIRONMENT = {
    "temperature": [
        {"sensor": "CPU", "value": 45.0, "status": "ok"},
        {"sensor": "Inlet", "value": 28.5, "status": "ok"},
        {"sensor": "Outlet", "value": 38.0, "status": "ok"},
    ],
    "fans": [
        {"name": "Fan1", "status": "ok"},
        {"name": "Fan2", "status": "ok"},
        {"name": "Fan3", "status": "ok"},
    ],
    "power": [
        {"name": "PSU1", "status": "ok", "output_watts": 350.0},
        {"name": "PSU2", "status": "ok", "output_watts": 0.0},
    ],
}

MOCK_ENVIRONMENT_ISSUES = {
    "temperature": [
        {"sensor": "CPU", "value": 85.0, "status": "critical"},
        {"sensor": "Inlet", "value": 55.0, "status": "warning"},
    ],
    "fans": [
        {"name": "Fan1", "status": "ok"},
        {"name": "Fan2", "status": "failed"},
    ],
    "power": [
        {"name": "PSU1", "status": "ok", "output_watts": 350.0},
        {"name": "PSU2", "status": "failed", "output_watts": 0.0},
    ],
}

MOCK_ENVIRONMENT_EMPTY = {
    "temperature": [],
    "fans": [],
    "power": [],
}

MOCK_NTP_STATUS = {
    "synchronized": True,
    "reference": "10.0.0.1",
    "peers": [
        {"peer": "10.0.0.1", "stratum": 2, "offset": 0.5, "jitter": 1.2},
        {"peer": "10.0.0.2", "stratum": 3, "offset": -1.0, "jitter": 2.5},
    ],
}

MOCK_NTP_UNSYNC = {
    "synchronized": False,
    "reference": "",
    "peers": [],
}

MOCK_LOGGING_OUTPUT = {"output": "Jan  1 00:00:01 spine-01 Ebra: %LINEPROTO-5-UPDOWN: Line protocol on Ethernet1\n"}

# Vendor-specific mock data sets
VENDOR_CONFIGS = {
    "arista": {"vendor": "arista", "platform": "eos"},
    "cisco_iosxe": {"vendor": "cisco", "platform": "iosxe"},
    "cisco_nxos": {"vendor": "cisco", "platform": "nxos"},
    "juniper": {"vendor": "juniper", "platform": "junos"},
}


@pytest.fixture
def mock_driver():
    """Create a mock NetworkDriver with monitoring getters."""
    driver = MagicMock()
    driver.vendor = "arista"
    driver.platform = "eos"
    driver.get_cpu_memory.return_value = MOCK_CPU_MEMORY
    driver.get_environment.return_value = MOCK_ENVIRONMENT
    driver.get_ntp_status.return_value = MOCK_NTP_STATUS
    driver.run_show.return_value = [MOCK_LOGGING_OUTPUT]
    return driver


@pytest.fixture
def mock_common_conn_mgr(mock_driver):
    """Patch conn_mgr.get_driver to return the mock NetworkDriver."""
    with patch.object(conn_mgr, "get_driver", return_value=mock_driver) as patched:
        yield patched


# --- net_get_cpu_usage ---


class TestNetGetCpuUsage:
    """Test the net_get_cpu_usage common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        result = net_get_cpu_usage("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"
        assert result["vendor"] == "arista"
        assert result["platform"] == "eos"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        result = net_get_cpu_usage("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_cpu_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        result = net_get_cpu_usage("spine-01")
        assert result["data"]["cpu_percent"] == 23.5

    def test_calls_get_cpu_memory(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        net_get_cpu_usage("spine-01")
        mock_driver.get_cpu_memory.assert_called_once()

    def test_only_cpu_fields_returned(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        result = net_get_cpu_usage("spine-01")
        assert set(result["data"].keys()) == {"cpu_percent"}

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        mock_driver.get_cpu_memory.side_effect = NotSupportedError("not available")
        result = net_get_cpu_usage("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_cpu_usage("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_cpu_usage("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_cpu_usage("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    @pytest.mark.parametrize("vendor_key", VENDOR_CONFIGS.keys())
    def test_multi_vendor(self, vendor_key, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_cpu_usage

        cfg = VENDOR_CONFIGS[vendor_key]
        mock_driver.vendor = cfg["vendor"]
        mock_driver.platform = cfg["platform"]
        result = net_get_cpu_usage("device-01")
        assert result["status"] == "success"
        assert result["vendor"] == cfg["vendor"]
        assert result["platform"] == cfg["platform"]


# --- net_get_memory_usage ---


class TestNetGetMemoryUsage:
    """Test the net_get_memory_usage common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        result = net_get_memory_usage("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        result = net_get_memory_usage("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_memory_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        result = net_get_memory_usage("spine-01")
        data = result["data"]
        assert data["memory_total_kb"] == 8_000_000
        assert data["memory_used_kb"] == 4_500_000
        assert data["memory_free_kb"] == 3_500_000

    def test_memory_percent_calculation(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        result = net_get_memory_usage("spine-01")
        assert result["data"]["memory_percent"] == 56.2  # 4500000/8000000 * 100

    def test_memory_percent_zero_total(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        mock_driver.get_cpu_memory.return_value = MOCK_CPU_MEMORY_ZERO_TOTAL
        result = net_get_memory_usage("spine-01")
        assert result["data"]["memory_percent"] == 0.0

    def test_calls_get_cpu_memory(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        net_get_memory_usage("spine-01")
        mock_driver.get_cpu_memory.assert_called_once()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        mock_driver.get_cpu_memory.side_effect = NotSupportedError("not available")
        result = net_get_memory_usage("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_memory_usage("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_memory_usage("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_memory_usage("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    @pytest.mark.parametrize("vendor_key", VENDOR_CONFIGS.keys())
    def test_multi_vendor(self, vendor_key, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_memory_usage

        cfg = VENDOR_CONFIGS[vendor_key]
        mock_driver.vendor = cfg["vendor"]
        mock_driver.platform = cfg["platform"]
        result = net_get_memory_usage("device-01")
        assert result["status"] == "success"
        assert result["vendor"] == cfg["vendor"]


# --- net_get_environment_temp ---


class TestNetGetEnvironmentTemp:
    """Test the net_get_environment_temp common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        result = net_get_environment_temp("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        result = net_get_environment_temp("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_temperature_data(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        result = net_get_environment_temp("spine-01")
        temps = result["data"]["temperature"]
        assert len(temps) == 3
        assert temps[0]["sensor"] == "CPU"
        assert temps[0]["value"] == 45.0
        assert temps[0]["status"] == "ok"

    def test_calls_get_environment(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        net_get_environment_temp("spine-01")
        mock_driver.get_environment.assert_called_once()

    def test_only_temperature_returned(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        result = net_get_environment_temp("spine-01")
        assert set(result["data"].keys()) == {"temperature"}

    def test_empty_temperature(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        mock_driver.get_environment.return_value = MOCK_ENVIRONMENT_EMPTY
        result = net_get_environment_temp("spine-01")
        assert result["status"] == "success"
        assert result["data"]["temperature"] == []

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        mock_driver.get_environment.side_effect = NotSupportedError("not available")
        result = net_get_environment_temp("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_environment_temp("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_environment_temp("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_environment_temp("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    @pytest.mark.parametrize("vendor_key", VENDOR_CONFIGS.keys())
    def test_multi_vendor(self, vendor_key, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_temp

        cfg = VENDOR_CONFIGS[vendor_key]
        mock_driver.vendor = cfg["vendor"]
        mock_driver.platform = cfg["platform"]
        result = net_get_environment_temp("device-01")
        assert result["status"] == "success"
        assert result["vendor"] == cfg["vendor"]


# --- net_get_environment_power ---


class TestNetGetEnvironmentPower:
    """Test the net_get_environment_power common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        result = net_get_environment_power("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        result = net_get_environment_power("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_power_data(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        result = net_get_environment_power("spine-01")
        power = result["data"]["power"]
        assert len(power) == 2
        assert power[0]["name"] == "PSU1"
        assert power[0]["status"] == "ok"
        assert power[0]["output_watts"] == 350.0

    def test_calls_get_environment(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        net_get_environment_power("spine-01")
        mock_driver.get_environment.assert_called_once()

    def test_only_power_returned(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        result = net_get_environment_power("spine-01")
        assert set(result["data"].keys()) == {"power"}

    def test_empty_power(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        mock_driver.get_environment.return_value = MOCK_ENVIRONMENT_EMPTY
        result = net_get_environment_power("spine-01")
        assert result["status"] == "success"
        assert result["data"]["power"] == []

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        mock_driver.get_environment.side_effect = NotSupportedError("not available")
        result = net_get_environment_power("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_environment_power("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_environment_power("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    @pytest.mark.parametrize("vendor_key", VENDOR_CONFIGS.keys())
    def test_multi_vendor(self, vendor_key, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_power

        cfg = VENDOR_CONFIGS[vendor_key]
        mock_driver.vendor = cfg["vendor"]
        mock_driver.platform = cfg["platform"]
        result = net_get_environment_power("device-01")
        assert result["status"] == "success"
        assert result["vendor"] == cfg["vendor"]


# --- net_get_environment_cooling ---


class TestNetGetEnvironmentCooling:
    """Test the net_get_environment_cooling common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        result = net_get_environment_cooling("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        result = net_get_environment_cooling("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_fan_data(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        result = net_get_environment_cooling("spine-01")
        fans = result["data"]["fans"]
        assert len(fans) == 3
        assert fans[0]["name"] == "Fan1"
        assert fans[0]["status"] == "ok"

    def test_calls_get_environment(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        net_get_environment_cooling("spine-01")
        mock_driver.get_environment.assert_called_once()

    def test_only_fans_returned(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        result = net_get_environment_cooling("spine-01")
        assert set(result["data"].keys()) == {"fans"}

    def test_empty_fans(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        mock_driver.get_environment.return_value = MOCK_ENVIRONMENT_EMPTY
        result = net_get_environment_cooling("spine-01")
        assert result["status"] == "success"
        assert result["data"]["fans"] == []

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        mock_driver.get_environment.side_effect = NotSupportedError("not available")
        result = net_get_environment_cooling("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_environment_cooling("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_environment_cooling("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_environment_cooling("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    @pytest.mark.parametrize("vendor_key", VENDOR_CONFIGS.keys())
    def test_multi_vendor(self, vendor_key, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_environment_cooling

        cfg = VENDOR_CONFIGS[vendor_key]
        mock_driver.vendor = cfg["vendor"]
        mock_driver.platform = cfg["platform"]
        result = net_get_environment_cooling("device-01")
        assert result["status"] == "success"
        assert result["vendor"] == cfg["vendor"]


# --- net_get_ntp_status ---


class TestNetGetNtpStatus:
    """Test the net_get_ntp_status common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        result = net_get_ntp_status("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        result = net_get_ntp_status("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_ntp_data_synchronized(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        result = net_get_ntp_status("spine-01")
        data = result["data"]
        assert data["synchronized"] is True
        assert data["reference"] == "10.0.0.1"
        assert len(data["peers"]) == 2

    def test_ntp_peer_data_structure(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        result = net_get_ntp_status("spine-01")
        peer = result["data"]["peers"][0]
        assert peer["peer"] == "10.0.0.1"
        assert peer["stratum"] == 2
        assert peer["offset"] == 0.5
        assert peer["jitter"] == 1.2

    def test_ntp_unsynchronized(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        mock_driver.get_ntp_status.return_value = MOCK_NTP_UNSYNC
        result = net_get_ntp_status("spine-01")
        assert result["status"] == "success"
        assert result["data"]["synchronized"] is False
        assert result["data"]["peers"] == []

    def test_calls_get_ntp_status(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        net_get_ntp_status("spine-01")
        mock_driver.get_ntp_status.assert_called_once()

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        mock_driver.get_ntp_status.side_effect = NotSupportedError("not available")
        result = net_get_ntp_status("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_ntp_status("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_ntp_status("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_ntp_status("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    @pytest.mark.parametrize("vendor_key", VENDOR_CONFIGS.keys())
    def test_multi_vendor(self, vendor_key, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_ntp_status

        cfg = VENDOR_CONFIGS[vendor_key]
        mock_driver.vendor = cfg["vendor"]
        mock_driver.platform = cfg["platform"]
        result = net_get_ntp_status("device-01")
        assert result["status"] == "success"
        assert result["vendor"] == cfg["vendor"]


# --- net_get_device_health ---


class TestNetGetDeviceHealth:
    """Test the net_get_device_health common tool."""

    def test_success_response_healthy(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_healthy_overall(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert result["data"]["overall"] == "healthy"
        assert result["data"]["issues"] == []

    def test_cpu_data_included(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert result["data"]["cpu_percent"] == 23.5

    def test_memory_data_included(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert result["data"]["memory_total_kb"] == 8_000_000
        assert result["data"]["memory_used_kb"] == 4_500_000
        assert result["data"]["memory_free_kb"] == 3_500_000

    def test_memory_percent_calculation(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert result["data"]["memory_percent"] == 56.2

    def test_temperature_data_included(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert len(result["data"]["temperature"]) == 3

    def test_fans_data_included(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert len(result["data"]["fans"]) == 3

    def test_power_data_included(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_device_health

        result = net_get_device_health("spine-01")
        assert len(result["data"]["power"]) == 2

    def test_high_cpu_issue(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_cpu_memory.return_value = MOCK_CPU_MEMORY_HIGH
        result = net_get_device_health("spine-01")
        assert result["data"]["overall"] == "degraded"
        issues = result["data"]["issues"]
        assert any("High CPU" in i for i in issues)

    def test_high_memory_issue(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_cpu_memory.return_value = MOCK_CPU_MEMORY_HIGH
        result = net_get_device_health("spine-01")
        assert result["data"]["overall"] == "degraded"
        issues = result["data"]["issues"]
        assert any("High memory" in i for i in issues)

    def test_critical_temperature_issue(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_environment.return_value = MOCK_ENVIRONMENT_ISSUES
        result = net_get_device_health("spine-01")
        assert result["data"]["overall"] == "degraded"
        issues = result["data"]["issues"]
        assert any("Critical temperature" in i for i in issues)

    def test_warning_temperature_issue(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_environment.return_value = MOCK_ENVIRONMENT_ISSUES
        result = net_get_device_health("spine-01")
        issues = result["data"]["issues"]
        assert any("Warning temperature" in i for i in issues)

    def test_fan_failure_issue(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_environment.return_value = MOCK_ENVIRONMENT_ISSUES
        result = net_get_device_health("spine-01")
        issues = result["data"]["issues"]
        assert any("Fan issue" in i for i in issues)

    def test_psu_failure_issue(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_environment.return_value = MOCK_ENVIRONMENT_ISSUES
        result = net_get_device_health("spine-01")
        issues = result["data"]["issues"]
        assert any("PSU issue" in i for i in issues)

    def test_multiple_issues(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_cpu_memory.return_value = MOCK_CPU_MEMORY_HIGH
        mock_driver.get_environment.return_value = MOCK_ENVIRONMENT_ISSUES
        result = net_get_device_health("spine-01")
        assert result["data"]["overall"] == "degraded"
        # CPU high + memory high + critical temp + warning temp + fan failure + PSU failure = 6
        assert len(result["data"]["issues"]) == 6

    def test_cpu_memory_not_supported_graceful(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_cpu_memory.side_effect = NotSupportedError("not available")
        result = net_get_device_health("spine-01")
        assert result["status"] == "success"
        assert result["data"]["cpu_memory"] == "not_supported"
        # Environment still populated
        assert len(result["data"]["temperature"]) == 3

    def test_environment_not_supported_graceful(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_environment.side_effect = NotSupportedError("not available")
        result = net_get_device_health("spine-01")
        assert result["status"] == "success"
        assert result["data"]["environment"] == "not_supported"
        # CPU/memory still populated
        assert result["data"]["cpu_percent"] == 23.5

    def test_both_not_supported_graceful(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_cpu_memory.side_effect = NotSupportedError("not available")
        mock_driver.get_environment.side_effect = NotSupportedError("not available")
        result = net_get_device_health("spine-01")
        assert result["status"] == "success"
        assert result["data"]["cpu_memory"] == "not_supported"
        assert result["data"]["environment"] == "not_supported"
        assert result["data"]["overall"] == "healthy"

    def test_memory_percent_zero_total(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        mock_driver.get_cpu_memory.return_value = MOCK_CPU_MEMORY_ZERO_TOTAL
        result = net_get_device_health("spine-01")
        assert result["data"]["memory_percent"] == 0.0

    def test_connection_error(self):
        from network_mcp.tools.common.monitoring import net_get_device_health

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_device_health("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.monitoring import net_get_device_health

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_device_health("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.monitoring import net_get_device_health

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_device_health("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    @pytest.mark.parametrize("vendor_key", VENDOR_CONFIGS.keys())
    def test_multi_vendor(self, vendor_key, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        cfg = VENDOR_CONFIGS[vendor_key]
        mock_driver.vendor = cfg["vendor"]
        mock_driver.platform = cfg["platform"]
        result = net_get_device_health("device-01")
        assert result["status"] == "success"
        assert result["vendor"] == cfg["vendor"]

    def test_calls_both_getters(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_device_health

        net_get_device_health("spine-01")
        mock_driver.get_cpu_memory.assert_called_once()
        mock_driver.get_environment.assert_called_once()


# --- net_get_logging ---


class TestNetGetLogging:
    """Test the net_get_logging common tool."""

    def test_success_response(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_logging

        result = net_get_logging("spine-01")
        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    def test_response_keys(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_logging

        result = net_get_logging("spine-01")
        assert set(result.keys()) == {"status", "device", "vendor", "platform", "data"}

    def test_default_lines(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        net_get_logging("spine-01")
        mock_driver.run_show.assert_called_once_with(["show logging last 50"], encoding="text")

    def test_custom_lines(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        net_get_logging("spine-01", lines=100)
        mock_driver.run_show.assert_called_once_with(["show logging last 100"], encoding="text")

    def test_logging_output_dict(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_logging

        result = net_get_logging("spine-01")
        assert result["data"]["lines_requested"] == 50
        assert "LINEPROTO" in result["data"]["output"]

    def test_logging_output_string(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        mock_driver.run_show.return_value = ["raw log text output"]
        result = net_get_logging("spine-01")
        assert result["data"]["output"] == "raw log text output"

    def test_lines_less_than_1(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_logging

        result = net_get_logging("spine-01", lines=0)
        assert result["status"] == "error"
        assert "lines must be between 1 and 10000" in result["error"]

    def test_lines_greater_than_10000(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_logging

        result = net_get_logging("spine-01", lines=10001)
        assert result["status"] == "error"
        assert "lines must be between 1 and 10000" in result["error"]

    def test_lines_negative(self, mock_common_conn_mgr):
        from network_mcp.tools.common.monitoring import net_get_logging

        result = net_get_logging("spine-01", lines=-5)
        assert result["status"] == "error"
        assert "lines must be between 1 and 10000" in result["error"]

    def test_lines_exactly_1(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        net_get_logging("spine-01", lines=1)
        mock_driver.run_show.assert_called_once_with(["show logging last 1"], encoding="text")

    def test_lines_exactly_10000(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        net_get_logging("spine-01", lines=10000)
        mock_driver.run_show.assert_called_once_with(["show logging last 10000"], encoding="text")

    def test_eos_command(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        mock_driver.platform = "eos"
        net_get_logging("spine-01", lines=25)
        mock_driver.run_show.assert_called_once_with(["show logging last 25"], encoding="text")

    def test_iosxe_command(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        mock_driver.platform = "iosxe"
        net_get_logging("csr-01", lines=25)
        mock_driver.run_show.assert_called_once_with(["show logging last 25"], encoding="text")

    def test_nxos_command(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        mock_driver.platform = "nxos"
        net_get_logging("nexus-01", lines=25)
        mock_driver.run_show.assert_called_once_with(["show logging last 25"], encoding="text")

    def test_junos_command(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        mock_driver.platform = "junos"
        net_get_logging("mx-01", lines=25)
        mock_driver.run_show.assert_called_once_with(["show log messages | last 25"], encoding="text")

    def test_unknown_platform_fallback(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        mock_driver.platform = "unknown"
        net_get_logging("device-01", lines=25)
        mock_driver.run_show.assert_called_once_with(["show logging last 25"], encoding="text")

    def test_not_supported(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        mock_driver.run_show.side_effect = NotSupportedError("not available")
        result = net_get_logging("spine-01")
        assert result["status"] == "not_supported"
        assert "not supported" in result["error"]

    def test_connection_error(self):
        from network_mcp.tools.common.monitoring import net_get_logging

        with patch.object(conn_mgr, "get_driver", side_effect=ConnectionError("unreachable")):
            result = net_get_logging("bad-host")
        assert result["status"] == "error"
        assert "unreachable" in result["error"]

    def test_timeout_error(self):
        from network_mcp.tools.common.monitoring import net_get_logging

        with patch.object(conn_mgr, "get_driver", side_effect=TimeoutError("timed out")):
            result = net_get_logging("slow-host")
        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    def test_unexpected_error(self):
        from network_mcp.tools.common.monitoring import net_get_logging

        with patch.object(conn_mgr, "get_driver", side_effect=RuntimeError("crash")):
            result = net_get_logging("crash-host")
        assert result["status"] == "error"
        assert "Unexpected error" in result["error"]

    def test_empty_output(self, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        mock_driver.run_show.return_value = [{"output": ""}]
        result = net_get_logging("spine-01")
        assert result["status"] == "success"
        assert result["data"]["output"] == ""

    @pytest.mark.parametrize("vendor_key", VENDOR_CONFIGS.keys())
    def test_multi_vendor_success(self, vendor_key, mock_common_conn_mgr, mock_driver):
        from network_mcp.tools.common.monitoring import net_get_logging

        cfg = VENDOR_CONFIGS[vendor_key]
        mock_driver.vendor = cfg["vendor"]
        mock_driver.platform = cfg["platform"]
        result = net_get_logging("device-01")
        assert result["status"] == "success"
        assert result["vendor"] == cfg["vendor"]
