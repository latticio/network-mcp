"""Integration improvement tests.

Tests for NetBox retry logic, Prometheus parallel collection and partial
failure handling, and ServiceNow configurable CI class.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# NetBox retry logic tests
# ---------------------------------------------------------------------------


class TestNetBoxRetryLogic:
    """Tests for NetBox API error handling and resilience."""

    @patch("network_mcp.integrations.netbox.settings")
    @patch("network_mcp.integrations.netbox.pynetbox")
    def test_netbox_api_error_returns_error_dict(self, mock_pynetbox, mock_settings):
        """NetBox API raises an exception → returns error dict with details."""
        mock_settings.net_demo_mode = False
        mock_settings.netbox_url = "https://netbox.example.com"
        mock_settings.netbox_token = MagicMock()
        mock_settings.netbox_token.get_secret_value.return_value = "test-token"

        mock_api = MagicMock()
        mock_pynetbox.api.return_value = mock_api
        mock_api.dcim.devices.filter.side_effect = Exception("429 Too Many Requests")

        from network_mcp.integrations.netbox import net_sync_from_netbox

        result = net_sync_from_netbox()

        assert result["status"] == "error"
        assert "429" in result["error"]

    @patch("network_mcp.integrations.netbox.settings")
    @patch("network_mcp.integrations.netbox.pynetbox")
    def test_netbox_api_503_returns_error(self, mock_pynetbox, mock_settings):
        """NetBox API returns 503 → error returned with details."""
        mock_settings.net_demo_mode = False
        mock_settings.netbox_url = "https://netbox.example.com"
        mock_settings.netbox_token = MagicMock()
        mock_settings.netbox_token.get_secret_value.return_value = "test-token"

        mock_api = MagicMock()
        mock_pynetbox.api.return_value = mock_api
        mock_api.dcim.devices.filter.side_effect = Exception("503 Service Unavailable")

        from network_mcp.integrations.netbox import net_sync_from_netbox

        result = net_sync_from_netbox()

        assert result["status"] == "error"
        assert "503" in result["error"]

    @patch("network_mcp.integrations.netbox.settings")
    @patch("network_mcp.integrations.netbox.pynetbox")
    def test_netbox_sync_success(self, mock_pynetbox, mock_settings):
        """NetBox sync succeeds → devices returned with correct platform mapping."""
        mock_settings.net_demo_mode = False
        mock_settings.netbox_url = "https://netbox.example.com"
        mock_settings.netbox_token = MagicMock()
        mock_settings.netbox_token.get_secret_value.return_value = "test-token"

        # Mock devices
        mock_device1 = MagicMock()
        mock_device1.name = "spine-01"
        mock_device1.primary_ip = "10.0.0.1/32"
        mock_device1.platform = MagicMock()
        mock_device1.platform.__str__ = lambda self: "arista-eos"
        mock_device1.role = MagicMock()
        mock_device1.role.__str__ = lambda self: "spine"
        mock_device1.site = MagicMock()
        mock_device1.site.__str__ = lambda self: "dc1"
        mock_device1.serial = "SN001"
        mock_device1.tags = []

        mock_api = MagicMock()
        mock_pynetbox.api.return_value = mock_api
        mock_api.dcim.devices.filter.return_value = [mock_device1]

        from network_mcp.integrations.netbox import net_sync_from_netbox

        result = net_sync_from_netbox(site="dc1")

        assert result["status"] == "success"
        assert result["synced_count"] == 1
        assert result["devices"][0]["name"] == "spine-01"
        assert result["devices"][0]["host"] == "10.0.0.1"
        assert result["devices"][0]["platform"] == "eos"

    @patch("network_mcp.integrations.netbox.settings")
    @patch("network_mcp.integrations.netbox.pynetbox", MagicMock())
    def test_netbox_not_configured_returns_error(self, mock_settings):
        """NetBox URL/token not configured → returns configuration error."""
        mock_settings.net_demo_mode = False
        mock_settings.netbox_url = None
        mock_settings.netbox_token = None

        from network_mcp.integrations.netbox import net_sync_from_netbox

        result = net_sync_from_netbox()

        assert result["status"] == "error"
        assert "not configured" in result["error"].lower()

    @patch("network_mcp.integrations.netbox.settings")
    def test_netbox_demo_mode(self, mock_settings):
        """Demo mode → returns mock data without contacting NetBox."""
        mock_settings.net_demo_mode = True

        from network_mcp.integrations.netbox import net_sync_from_netbox

        result = net_sync_from_netbox()

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Prometheus collection tests
# ---------------------------------------------------------------------------


class TestPrometheusCollection:
    """Tests for Prometheus metrics collection and partial failure handling."""

    @patch("network_mcp.tools.common.device.net_get_device_info")
    @patch("network_mcp.tools.common.interfaces.net_get_interfaces")
    @patch("network_mcp.tools.common.routing.net_get_bgp_summary")
    def test_all_devices_succeed(self, mock_bgp, mock_ifaces, mock_info):
        """All devices report metrics successfully → combined results returned."""
        mock_info.return_value = {
            "status": "success",
            "platform": "eos",
            "data": {
                "hostname": "spine-01",
                "uptime_seconds": 864000,
                "cpu_percent": 45.0,
                "memory_percent": 60.0,
            },
        }
        mock_bgp.return_value = {
            "status": "success",
            "data": {
                "peers": {
                    "10.0.0.2": {"state": "Established"},
                    "10.0.0.3": {"state": "Established"},
                },
            },
        }
        mock_ifaces.return_value = {
            "status": "success",
            "data": {
                "Ethernet1": {"input_errors": 0, "output_errors": 0},
            },
        }

        from network_mcp.integrations.prometheus import _collect_device_metrics

        result = _collect_device_metrics("spine-01")

        assert result["host"] == "spine-01"
        assert result["errors"] == []
        assert result["uptime_seconds"] == 864000
        assert result["cpu_percent"] == 45.0
        assert result["bgp_peers_established"] == 2

    @patch("network_mcp.tools.common.device.net_get_device_info")
    @patch("network_mcp.tools.common.interfaces.net_get_interfaces")
    @patch("network_mcp.tools.common.routing.net_get_bgp_summary")
    def test_partial_device_failure(self, mock_bgp, mock_ifaces, mock_info):
        """Device info succeeds but BGP fails → partial result with error details."""
        mock_info.return_value = {
            "status": "success",
            "platform": "eos",
            "data": {
                "hostname": "leaf-01",
                "uptime_seconds": 3600,
            },
        }
        mock_bgp.return_value = {
            "status": "error",
            "error": "Connection refused",
        }
        mock_ifaces.return_value = {
            "status": "success",
            "data": {},
        }

        from network_mcp.integrations.prometheus import _collect_device_metrics

        result = _collect_device_metrics("leaf-01")

        assert result["host"] == "leaf-01"
        assert result["uptime_seconds"] == 3600
        assert len(result["errors"]) == 1
        assert "bgp" in result["errors"][0]

    @patch("network_mcp.tools.common.device.net_get_device_info")
    @patch("network_mcp.tools.common.interfaces.net_get_interfaces")
    @patch("network_mcp.tools.common.routing.net_get_bgp_summary")
    def test_all_data_sources_fail(self, mock_bgp, mock_ifaces, mock_info):
        """All data sources fail → errors collected, partial result returned."""
        mock_info.return_value = {"status": "error", "error": "Device unreachable"}
        mock_bgp.return_value = {"status": "error", "error": "Connection refused"}
        mock_ifaces.return_value = {"status": "error", "error": "Timeout"}

        from network_mcp.integrations.prometheus import _collect_device_metrics

        result = _collect_device_metrics("unreachable-01")

        assert result["host"] == "unreachable-01"
        assert len(result["errors"]) == 3
        assert result["platform"] == "unknown"

    @patch("network_mcp.integrations.prometheus.settings")
    @patch("network_mcp.integrations.prometheus._collect_device_metrics")
    @patch("network_mcp.integrations.prometheus.CollectorRegistry")
    @patch("network_mcp.integrations.prometheus.GaugeMetricFamily")
    @patch("network_mcp.integrations.prometheus.generate_latest")
    def test_collect_prometheus_metrics_multiple_hosts(
        self, mock_gen, mock_gauge, mock_registry, mock_collect, mock_settings
    ):
        """Collect from multiple hosts → all results gathered."""
        mock_settings.net_demo_mode = False
        mock_settings.net_prometheus_max_workers = 5

        mock_collect.side_effect = [
            {"host": "host1", "errors": [], "platform": "eos", "uptime_seconds": 100},
            {"host": "host2", "errors": ["bgp: error"], "platform": "eos", "uptime_seconds": 200},
        ]
        mock_gen.return_value = b"# HELP test\n"

        from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

        result = net_collect_prometheus_metrics(hosts=["host1", "host2"])

        assert result["status"] == "success"
        assert result["hosts_collected"] == 2
        assert result["hosts_with_errors"] == 1

    @patch("network_mcp.integrations.prometheus.settings")
    @patch("network_mcp.integrations.prometheus._collect_device_metrics")
    @patch("network_mcp.integrations.prometheus.CollectorRegistry")
    @patch("network_mcp.integrations.prometheus.GaugeMetricFamily")
    @patch("network_mcp.integrations.prometheus.generate_latest")
    def test_collect_prometheus_exception_in_device(
        self, mock_gen, mock_gauge, mock_registry, mock_collect, mock_settings
    ):
        """Device metrics collection raises exception → captured as error, not propagated."""
        mock_settings.net_demo_mode = False
        mock_settings.net_prometheus_max_workers = 5

        mock_collect.side_effect = [
            {"host": "ok-host", "errors": [], "platform": "eos"},
            Exception("Connection reset"),
        ]
        mock_gen.return_value = b"# HELP test\n"

        from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

        result = net_collect_prometheus_metrics(hosts=["ok-host", "bad-host"])

        assert result["status"] == "success"
        assert result["hosts_collected"] == 1
        assert result["hosts_with_errors"] == 1

    @patch("network_mcp.integrations.prometheus.CollectorRegistry", MagicMock())
    @patch("network_mcp.integrations.prometheus.settings")
    def test_collect_prometheus_no_hosts(self, mock_settings):
        """No hosts and empty inventory → error returned."""
        mock_settings.net_demo_mode = False

        with patch("network_mcp.integrations.prometheus._get_all_inventory_hosts", return_value=[]):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics()

        assert result["status"] == "error"
        assert "empty" in result["error"].lower()

    @patch("network_mcp.integrations.prometheus.settings")
    def test_prometheus_demo_mode(self, mock_settings):
        """Demo mode → returns mock Prometheus data."""
        mock_settings.net_demo_mode = True

        from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

        result = net_collect_prometheus_metrics()
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Prometheus _collect_device_metrics with interface errors
# ---------------------------------------------------------------------------


class TestPrometheusInterfaceErrors:
    """Tests for interface error metrics collection."""

    @patch("network_mcp.tools.common.device.net_get_device_info")
    @patch("network_mcp.tools.common.interfaces.net_get_interfaces")
    @patch("network_mcp.tools.common.routing.net_get_bgp_summary")
    def test_interfaces_with_errors(self, mock_bgp, mock_ifaces, mock_info):
        """Interfaces report non-zero errors → captured in metrics."""
        mock_info.return_value = {
            "status": "success",
            "platform": "eos",
            "data": {"hostname": "err-host", "uptime_seconds": 100},
        }
        mock_bgp.return_value = {"status": "success", "data": {"peers": {}}}
        mock_ifaces.return_value = {
            "status": "success",
            "data": {
                "Ethernet1": {"input_errors": 100, "output_errors": 50},
                "Ethernet2": {"input_errors": 0, "output_errors": 0},
            },
        }

        from network_mcp.integrations.prometheus import _collect_device_metrics

        result = _collect_device_metrics("err-host")

        assert len(result["interface_errors"]) == 1
        assert result["interface_errors"][0]["interface"] == "Ethernet1"
        assert result["interface_errors"][0]["input_errors"] == 100
        assert result["interface_errors"][0]["output_errors"] == 50


# ---------------------------------------------------------------------------
# ServiceNow tests
# ---------------------------------------------------------------------------


class TestServiceNowClient:
    """Tests for ServiceNow REST API client."""

    def test_servicenow_client_create_incident(self):
        """ServiceNowClient.create_incident sends correct POST request."""
        from network_mcp.integrations.servicenow import ServiceNowClient

        with patch("network_mcp.integrations.servicenow.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "result": {
                    "number": "INC0010001",
                    "sys_id": "abc123",
                    "state": "1",
                }
            }
            mock_resp.raise_for_status.return_value = None
            mock_client.post.return_value = mock_resp

            client = ServiceNowClient("test-instance", "admin", "password")
            result = client.create_incident("Test issue", "Full description", urgency=1)

            assert result["number"] == "INC0010001"
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "/table/incident" in call_args[0][0]

    def test_servicenow_client_update_incident(self):
        """ServiceNowClient.update_incident sends correct PATCH request."""
        from network_mcp.integrations.servicenow import ServiceNowClient

        with patch("network_mcp.integrations.servicenow.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "result": {
                    "number": "INC0010001",
                    "sys_id": "abc123",
                    "state": "2",
                }
            }
            mock_resp.raise_for_status.return_value = None
            mock_client.patch.return_value = mock_resp

            client = ServiceNowClient("test-instance", "admin", "password")
            result = client.update_incident("abc123", work_notes="Test note", state=2)

            assert result["state"] == "2"
            mock_client.patch.assert_called_once()

    def test_servicenow_client_create_or_update_ci_existing(self):
        """create_or_update_ci finds existing CI → updates it."""
        from network_mcp.integrations.servicenow import ServiceNowClient

        with patch("network_mcp.integrations.servicenow.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            # GET returns existing CI
            mock_get_resp = MagicMock()
            mock_get_resp.json.return_value = {"result": [{"sys_id": "ci-123", "name": "spine-01"}]}
            mock_get_resp.raise_for_status.return_value = None
            mock_client.get.return_value = mock_get_resp

            # PATCH updates it
            mock_patch_resp = MagicMock()
            mock_patch_resp.json.return_value = {
                "result": {"sys_id": "ci-123", "name": "spine-01", "serial_number": "SN001"}
            }
            mock_patch_resp.raise_for_status.return_value = None
            mock_client.patch.return_value = mock_patch_resp

            client = ServiceNowClient("test-instance", "admin", "password")
            result = client.create_or_update_ci("spine-01", {"serial_number": "SN001"})

            assert result["sys_id"] == "ci-123"
            mock_client.patch.assert_called_once()

    def test_servicenow_client_create_or_update_ci_new(self):
        """create_or_update_ci no existing CI → creates new one."""
        from network_mcp.integrations.servicenow import ServiceNowClient

        with patch("network_mcp.integrations.servicenow.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            # GET returns no existing CI
            mock_get_resp = MagicMock()
            mock_get_resp.json.return_value = {"result": []}
            mock_get_resp.raise_for_status.return_value = None
            mock_client.get.return_value = mock_get_resp

            # POST creates it
            mock_post_resp = MagicMock()
            mock_post_resp.json.return_value = {"result": {"sys_id": "new-ci-456", "name": "leaf-01"}}
            mock_post_resp.raise_for_status.return_value = None
            mock_client.post.return_value = mock_post_resp

            client = ServiceNowClient("test-instance", "admin", "password")
            result = client.create_or_update_ci("leaf-01", {"serial_number": "SN002"})

            assert result["sys_id"] == "new-ci-456"
            mock_client.post.assert_called_once()


# ---------------------------------------------------------------------------
# ServiceNow tool-level tests
# ---------------------------------------------------------------------------


class TestServiceNowToolIntegration:
    """Tests for ServiceNow MCP tool functions."""

    @patch("network_mcp.integrations.servicenow.settings")
    def test_create_incident_not_configured(self, mock_settings):
        """ServiceNow not configured → returns configuration error."""
        mock_settings.net_demo_mode = False
        mock_settings.snow_instance = None
        mock_settings.snow_username = None
        mock_settings.snow_password = None

        from network_mcp.integrations.servicenow import net_create_incident

        result = net_create_incident("spine-01", "Test issue")

        assert result["status"] == "error"
        assert result["device"] == "spine-01"
        assert "not configured" in result["error"].lower()

    @patch("network_mcp.integrations.servicenow.settings")
    def test_update_incident_no_params(self, mock_settings):
        """Update incident with no work_notes or state → returns error."""
        mock_settings.net_demo_mode = False
        mock_settings.snow_instance = "test"
        mock_settings.snow_username = "admin"
        mock_settings.snow_password = MagicMock()

        from network_mcp.integrations.servicenow import net_update_incident

        result = net_update_incident("spine-01", "sys123")

        assert result["status"] == "error"
        assert "at least one" in result["error"].lower()

    @patch("network_mcp.integrations.servicenow.settings")
    def test_create_incident_demo_mode(self, mock_settings):
        """Demo mode → returns mock incident data."""
        mock_settings.net_demo_mode = True

        from network_mcp.integrations.servicenow import net_create_incident

        result = net_create_incident("spine-01", "Test issue")

        assert result["status"] == "success"
        assert result["device"] == "spine-01"

    @patch("network_mcp.integrations.servicenow.settings")
    def test_validate_change_no_commands(self, mock_settings):
        """Validate change with no pre/post commands → returns error."""
        mock_settings.net_demo_mode = False
        mock_settings.snow_instance = "test"
        mock_settings.snow_username = "admin"
        mock_settings.snow_password = MagicMock()

        from network_mcp.integrations.servicenow import net_validate_change

        result = net_validate_change("spine-01", "CHG001")

        assert result["status"] == "error"
        assert "at least one" in result["error"].lower()


# ---------------------------------------------------------------------------
# Grafana dashboard generation
# ---------------------------------------------------------------------------


class TestGrafanaDashboard:
    """Tests for Grafana dashboard generation."""

    def test_valid_dashboard_types(self):
        """Each valid dashboard type → returns success with JSON."""
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        for dashboard_type in ("overview", "bgp", "interfaces"):
            result = net_get_grafana_dashboard(dashboard_type)
            assert result["status"] == "success"
            assert result["dashboard_type"] == dashboard_type
            assert "dashboard_json" in result
            assert "import_instructions" in result

    def test_invalid_dashboard_type(self):
        """Invalid dashboard type → returns error with valid types list."""
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        result = net_get_grafana_dashboard("invalid_type")
        assert result["status"] == "error"
        assert "Unknown dashboard type" in result["error"]
        assert "overview" in result["error"]
