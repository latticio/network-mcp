"""Tests for monitoring tools: CPU, memory, environment, NTP, logging, and reload cause."""

import pyeapi.eapilib
import pytest

from network_mcp.tools.monitoring import (
    eos_get_cpu_usage,
    eos_get_environment_cooling,
    eos_get_environment_power,
    eos_get_environment_temp,
    eos_get_logging,
    eos_get_memory_usage,
    eos_get_ntp_status,
    eos_get_reload_cause,
)
from tests.conftest import MOCK_SHOW_VERSION

# --- Mock responses specific to monitoring ---

MOCK_PROCESSES_TOP = {
    "cpuInfo": {
        "%Cpu(s)": {"idle": 95.2, "user": 3.1, "system": 1.7},
    },
    "processes": [
        {"pid": 1, "command": "init", "runtime": 100, "cpu": 0.1},
    ],
}

MOCK_ENV_TEMP = {
    "systemStatus": "temperatureOk",
    "tempSensors": [
        {"name": "DomainSensor-Board", "hwStatus": "ok", "currentTemperature": 35.0, "maxTemperature": 45.0},
        {"name": "DomainSensor-CPU", "hwStatus": "ok", "currentTemperature": 42.0, "maxTemperature": 75.0},
    ],
}

MOCK_ENV_POWER = {
    "powerSupplies": {
        "1": {"state": "ok", "modelName": "PWR-500AC", "outputPower": 150.0, "capacity": 500.0},
        "2": {"state": "ok", "modelName": "PWR-500AC", "outputPower": 145.0, "capacity": 500.0},
    }
}

MOCK_ENV_COOLING = {
    "fanTraySlots": [
        {"label": "Fan1", "status": "ok", "speed": 50},
        {"label": "Fan2", "status": "ok", "speed": 50},
    ]
}

MOCK_NTP_STATUS = {
    "status": "synchronised",
    "peerStatus": {"configured": 2, "synced": 1},
}

MOCK_NTP_ASSOCIATIONS = {
    "peers": {
        "10.0.0.1": {"condition": "sys.peer", "stratum": 2, "refId": "10.0.0.100"},
    }
}

MOCK_LOGGING_TEXT = {"output": "Jan  1 00:00:00 switch %SYS-5-CONFIG_I: Configured from console\n"}

MOCK_RELOAD_CAUSE = {
    "resetCauses": [
        {"description": "Reload requested by user", "timestamp": 1700000000},
    ]
}


# --- eos_get_cpu_usage ---


class TestGetCpuUsage:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_PROCESSES_TOP]
        result = eos_get_cpu_usage("test-switch")
        assert result["status"] == "success"
        assert result["device"] == "test-switch"
        assert result["data"] == MOCK_PROCESSES_TOP
        mock_node.run_commands.assert_called_once_with(["show processes top once"], encoding="json")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_cpu_usage("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_invalid_host(self, mock_conn_mgr, mock_node):
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError):
            eos_get_cpu_usage("")


# --- eos_get_memory_usage ---


class TestGetMemoryUsage:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_SHOW_VERSION]
        result = eos_get_memory_usage("test-switch")
        assert result["status"] == "success"
        data = result["data"]
        assert data["total_kb"] == 2048000
        assert data["free_kb"] == 1024000
        assert data["used_kb"] == 1024000
        assert data["used_percent"] == 50.0

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_memory_usage("test-switch")
        assert result["status"] == "error"
        assert "Connection to" in result["error"]

    def test_zero_memory(self, mock_conn_mgr, mock_node):
        zero_version = {**MOCK_SHOW_VERSION, "memTotal": 0, "memFree": 0}
        mock_node.run_commands.return_value = [zero_version]
        result = eos_get_memory_usage("test-switch")
        assert result["status"] == "success"
        assert result["data"]["used_percent"] == 0


# --- eos_get_environment_temp ---


class TestGetEnvironmentTemp:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_ENV_TEMP]
        result = eos_get_environment_temp("test-switch")
        assert result["status"] == "success"
        assert result["data"] == MOCK_ENV_TEMP
        mock_node.run_commands.assert_called_once_with(["show environment temperature"], encoding="json")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_environment_temp("test-switch")
        assert result["status"] == "error"


# --- eos_get_environment_power ---


class TestGetEnvironmentPower:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_ENV_POWER]
        result = eos_get_environment_power("test-switch")
        assert result["status"] == "success"
        assert result["data"] == MOCK_ENV_POWER
        mock_node.run_commands.assert_called_once_with(["show environment power"], encoding="json")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_environment_power("test-switch")
        assert result["status"] == "error"


# --- eos_get_environment_cooling ---


class TestGetEnvironmentCooling:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_ENV_COOLING]
        result = eos_get_environment_cooling("test-switch")
        assert result["status"] == "success"
        assert result["data"] == MOCK_ENV_COOLING
        mock_node.run_commands.assert_called_once_with(["show environment cooling"], encoding="json")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_environment_cooling("test-switch")
        assert result["status"] == "error"


# --- eos_get_ntp_status ---


class TestGetNtpStatus:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_NTP_STATUS, MOCK_NTP_ASSOCIATIONS]
        result = eos_get_ntp_status("test-switch")
        assert result["status"] == "success"
        assert result["data"]["ntp_status"] == MOCK_NTP_STATUS
        assert result["data"]["associations"] == MOCK_NTP_ASSOCIATIONS
        mock_node.run_commands.assert_called_once_with(["show ntp status", "show ntp associations"], encoding="json")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_ntp_status("test-switch")
        assert result["status"] == "error"


# --- eos_get_logging ---


class TestGetLogging:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LOGGING_TEXT]
        result = eos_get_logging("test-switch")
        assert result["status"] == "success"
        assert "CONFIG_I" in result["data"]["output"]

    def test_uses_text_encoding(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LOGGING_TEXT]
        eos_get_logging("test-switch")
        mock_node.run_commands.assert_called_once_with(["show logging last 50"], encoding="text")

    def test_custom_lines(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_LOGGING_TEXT]
        eos_get_logging("test-switch", lines=100)
        mock_node.run_commands.assert_called_once_with(["show logging last 100"], encoding="text")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_logging("test-switch")
        assert result["status"] == "error"

    def test_empty_output(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [{"output": ""}]
        result = eos_get_logging("test-switch")
        assert result["status"] == "success"
        assert result["data"]["output"] == ""


# --- eos_get_reload_cause ---


class TestGetReloadCause:
    def test_success(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.return_value = [MOCK_RELOAD_CAUSE]
        result = eos_get_reload_cause("test-switch")
        assert result["status"] == "success"
        assert result["data"] == MOCK_RELOAD_CAUSE
        mock_node.run_commands.assert_called_once_with(["show reload cause"], encoding="json")

    def test_connection_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.ConnectionError("host", "refused")
        result = eos_get_reload_cause("test-switch")
        assert result["status"] == "error"

    def test_command_error(self, mock_conn_mgr, mock_node):
        mock_node.run_commands.side_effect = pyeapi.eapilib.CommandError(1, "invalid command")
        result = eos_get_reload_cause("test-switch")
        assert result["status"] == "error"
        assert "Command error" in result["error"]


# --- Timeout and malformed response tests ---


class TestMonitoringErrorScenarios:
    def test_cpu_usage_timeout(self, mock_conn_mgr, mock_node_timeout):
        """CPU usage returns error on timeout."""
        result = eos_get_cpu_usage("test-switch")
        assert result["status"] == "error"

    def test_memory_usage_timeout(self, mock_conn_mgr, mock_node_timeout):
        """Memory usage returns error on timeout."""
        result = eos_get_memory_usage("test-switch")
        assert result["status"] == "error"

    def test_ntp_status_timeout(self, mock_conn_mgr, mock_node_timeout):
        """NTP status returns error on timeout."""
        result = eos_get_ntp_status("test-switch")
        assert result["status"] == "error"

    def test_environment_temp_malformed(self, mock_conn_mgr, mock_node):
        """Malformed temp response (missing expected keys) still succeeds."""
        mock_node.run_commands.return_value = [{"unexpected_key": "value"}]
        result = eos_get_environment_temp("test-switch")
        assert result["status"] == "success"
        # Returns whatever the device sends — tool doesn't validate structure
        assert result["data"] == {"unexpected_key": "value"}

    def test_environment_power_empty(self, mock_conn_mgr, mock_node_empty):
        """Empty power response succeeds with empty data."""
        result = eos_get_environment_power("test-switch")
        assert result["status"] == "success"
        assert result["data"] == {}

    def test_logging_connection_refused(self, mock_conn_mgr, mock_node_connection_error):
        """Logging returns error on connection failure."""
        result = eos_get_logging("test-switch")
        assert result["status"] == "error"
