"""Tests for Prometheus metrics export and Grafana dashboard generation."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Dashboard JSON file paths
# ---------------------------------------------------------------------------

DASHBOARDS_DIR = Path(__file__).resolve().parent.parent / "dashboards"

DASHBOARD_FILES = [
    DASHBOARDS_DIR / "network-overview.json",
    DASHBOARDS_DIR / "bgp-fabric.json",
    DASHBOARDS_DIR / "interface-monitoring.json",
]


# ---------------------------------------------------------------------------
# Helpers — mock device data builders
# ---------------------------------------------------------------------------


def _mock_device_info(host, *, cpu=25.0, memory=60.0, uptime=86400, platform="eos"):
    """Build a successful net_get_device_info return value."""
    return {
        "status": "success",
        "device": host,
        "platform": platform,
        "data": {
            "hostname": host,
            "cpu_percent": cpu,
            "memory_percent": memory,
            "uptime_seconds": uptime,
        },
    }


def _mock_device_info_error(host, error="connection refused"):
    return {"status": "error", "device": host, "error": error}


def _mock_bgp_summary(host, *, established=2, total=3):
    peers = {}
    for i in range(established):
        peers[f"10.0.0.{i + 10}"] = {"state": "Established", "prefixReceived": 100}
    for i in range(total - established):
        peers[f"10.0.0.{i + 100}"] = {"state": "Active", "prefixReceived": 0}
    return {
        "status": "success",
        "device": host,
        "data": {"peers": peers},
    }


def _mock_bgp_summary_error(host, error="bgp not configured"):
    return {"status": "error", "device": host, "error": error}


def _mock_interfaces(host, *, interfaces=None):
    if interfaces is None:
        interfaces = {
            "Ethernet1": {"input_errors": 5, "output_errors": 2},
            "Ethernet2": {"input_errors": 0, "output_errors": 0},
        }
    return {
        "status": "success",
        "device": host,
        "data": interfaces,
    }


def _mock_interfaces_error(host, error="interfaces unavailable"):
    return {"status": "error", "device": host, "error": error}


# ---------------------------------------------------------------------------
# Fake prometheus_client — used when real library is not installed
# ---------------------------------------------------------------------------


class _FakeGaugeMetricFamily:
    """Minimal stand-in for prometheus_client.GaugeMetricFamily."""

    def __init__(self, name, documentation, labels=None):
        self.name = name
        self.documentation = documentation
        self.labels = labels or []
        self.samples: list[tuple[list[str], float]] = []

    def add_metric(self, label_values, value):
        self.samples.append((label_values, value))


class _FakeCollectorRegistry:
    """Minimal stand-in for prometheus_client.CollectorRegistry."""

    def __init__(self):
        self._collectors: list = []

    def register(self, collector):
        self._collectors.append(collector)


def _fake_generate_latest(registry):
    """Produce Prometheus text exposition from fake registry."""
    lines: list[str] = []
    for collector in registry._collectors:
        for gauge in collector.collect():
            lines.append(f"# HELP {gauge.name} {gauge.documentation}")
            lines.append(f"# TYPE {gauge.name} gauge")
            for label_values, value in gauge.samples:
                label_pairs = ",".join(
                    f'{label}="{val}"' for label, val in zip(gauge.labels, label_values, strict=False)
                )
                lines.append(f"{gauge.name}{{{label_pairs}}} {value}")
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture()
def _patch_prometheus_client():
    """Patch prometheus_client references in the prometheus module with fakes.

    The _collect_device_metrics function does lazy imports from
    network_mcp.tools.common.*, so we patch at those module paths.
    """
    with (
        patch(
            "network_mcp.integrations.prometheus.CollectorRegistry",
            _FakeCollectorRegistry,
        ),
        patch(
            "network_mcp.integrations.prometheus.GaugeMetricFamily",
            _FakeGaugeMetricFamily,
        ),
        patch(
            "network_mcp.integrations.prometheus.generate_latest",
            _fake_generate_latest,
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# Metric collection tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_patch_prometheus_client")
class TestMetricCollection:
    """Tests for net_collect_prometheus_metrics tool."""

    def test_single_device_metrics(self):
        """Metrics are collected and formatted as Prometheus text for a single device."""
        with (
            patch(
                "network_mcp.tools.common.device.net_get_device_info",
                return_value=_mock_device_info("spine-01"),
            ),
            patch(
                "network_mcp.tools.common.routing.net_get_bgp_summary",
                return_value=_mock_bgp_summary("spine-01"),
            ),
            patch(
                "network_mcp.tools.common.interfaces.net_get_interfaces",
                return_value=_mock_interfaces("spine-01"),
            ),
            patch(
                "network_mcp.integrations.prometheus._get_all_inventory_hosts",
                return_value=["spine-01"],
            ),
        ):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics()

        assert result["status"] == "success"
        assert result["hosts_collected"] == 1
        assert result["hosts_with_errors"] == 0

        text = result["prometheus_text"]
        assert "network_device_cpu_percent" in text
        assert "network_device_memory_percent" in text
        assert "network_device_uptime_seconds" in text
        assert "network_bgp_peers_established" in text
        assert 'host="spine-01"' in text

    def test_multiple_devices(self):
        """All devices contribute metrics when multiple hosts are collected."""

        def _info_side_effect(host):
            return _mock_device_info(host, cpu=30.0 if host == "spine-01" else 50.0)

        def _bgp_side_effect(host):
            return _mock_bgp_summary(host, established=2 if host == "spine-01" else 3)

        def _ifaces_side_effect(host):
            return _mock_interfaces(host)

        with (
            patch(
                "network_mcp.tools.common.device.net_get_device_info",
                side_effect=_info_side_effect,
            ),
            patch(
                "network_mcp.tools.common.routing.net_get_bgp_summary",
                side_effect=_bgp_side_effect,
            ),
            patch(
                "network_mcp.tools.common.interfaces.net_get_interfaces",
                side_effect=_ifaces_side_effect,
            ),
        ):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics(hosts=["spine-01", "leaf-01"])

        assert result["status"] == "success"
        assert result["hosts_collected"] == 2
        text = result["prometheus_text"]
        assert 'host="spine-01"' in text
        assert 'host="leaf-01"' in text

    def test_device_failure_records_error_others_still_collected(self):
        """A failing device records an error but does not prevent other devices from being collected."""

        def _info_side_effect(host):
            if host == "bad-device":
                return _mock_device_info_error(host)
            return _mock_device_info(host)

        def _bgp_side_effect(host):
            if host == "bad-device":
                return _mock_bgp_summary_error(host)
            return _mock_bgp_summary(host)

        def _ifaces_side_effect(host):
            if host == "bad-device":
                return _mock_interfaces_error(host)
            return _mock_interfaces(host)

        with (
            patch(
                "network_mcp.tools.common.device.net_get_device_info",
                side_effect=_info_side_effect,
            ),
            patch(
                "network_mcp.tools.common.routing.net_get_bgp_summary",
                side_effect=_bgp_side_effect,
            ),
            patch(
                "network_mcp.tools.common.interfaces.net_get_interfaces",
                side_effect=_ifaces_side_effect,
            ),
        ):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics(hosts=["spine-01", "bad-device"])

        assert result["status"] == "success"
        assert result["hosts_collected"] == 2
        assert result["hosts_with_errors"] == 1
        # Successful device still present in output
        assert 'host="spine-01"' in result["prometheus_text"]

    def test_device_exception_recorded(self):
        """An exception during device collection is captured without crashing."""

        def _info_side_effect(host):
            if host == "crash-device":
                raise RuntimeError("connection timeout")
            return _mock_device_info(host)

        with (
            patch(
                "network_mcp.tools.common.device.net_get_device_info",
                side_effect=_info_side_effect,
            ),
            patch(
                "network_mcp.tools.common.routing.net_get_bgp_summary",
                return_value=_mock_bgp_summary("spine-01"),
            ),
            patch(
                "network_mcp.tools.common.interfaces.net_get_interfaces",
                return_value=_mock_interfaces("spine-01"),
            ),
        ):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics(hosts=["spine-01", "crash-device"])

        assert result["status"] == "success"
        assert result["hosts_with_errors"] == 1
        error_hosts = [e["host"] for e in result["errors"]]
        assert "crash-device" in error_hosts

    def test_prometheus_text_format_valid(self):
        """The output is valid Prometheus exposition text (lines start with metric name or #)."""
        with (
            patch(
                "network_mcp.tools.common.device.net_get_device_info",
                return_value=_mock_device_info("spine-01"),
            ),
            patch(
                "network_mcp.tools.common.routing.net_get_bgp_summary",
                return_value=_mock_bgp_summary("spine-01"),
            ),
            patch(
                "network_mcp.tools.common.interfaces.net_get_interfaces",
                return_value=_mock_interfaces("spine-01"),
            ),
        ):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics(hosts=["spine-01"])

        text = result["prometheus_text"]
        for line in text.strip().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Prometheus lines are either comments (#) or metric samples
            assert stripped.startswith("#") or stripped.startswith("network_"), f"Invalid Prometheus line: {stripped!r}"

    def test_no_hosts_returns_error(self):
        """Empty host list (and empty inventory) returns an error."""
        with patch(
            "network_mcp.integrations.prometheus._get_all_inventory_hosts",
            return_value=[],
        ):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics()

        assert result["status"] == "error"
        assert "No hosts" in result["error"]

    def test_interface_errors_in_prometheus_text(self):
        """Interface errors are exported with host, interface, and direction labels."""
        with (
            patch(
                "network_mcp.tools.common.device.net_get_device_info",
                return_value=_mock_device_info("spine-01"),
            ),
            patch(
                "network_mcp.tools.common.routing.net_get_bgp_summary",
                return_value=_mock_bgp_summary("spine-01"),
            ),
            patch(
                "network_mcp.tools.common.interfaces.net_get_interfaces",
                return_value=_mock_interfaces(
                    "spine-01",
                    interfaces={
                        "Ethernet1": {"input_errors": 42, "output_errors": 7},
                    },
                ),
            ),
        ):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics(hosts=["spine-01"])

        text = result["prometheus_text"]
        assert "network_interface_errors_total" in text
        assert 'interface="Ethernet1"' in text
        assert 'direction="input"' in text
        assert 'direction="output"' in text


# ---------------------------------------------------------------------------
# Graceful degradation — prometheus_client not installed
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Tests for behavior when prometheus_client is not installed."""

    def test_returns_install_instructions_when_prometheus_client_missing(self):
        """When prometheus_client is not importable, the tool returns install instructions."""
        with (
            patch("network_mcp.integrations.prometheus.CollectorRegistry", None),
            patch("network_mcp.integrations.prometheus.GaugeMetricFamily", None),
            patch("network_mcp.integrations.prometheus.generate_latest", None),
        ):
            from network_mcp.integrations.prometheus import net_collect_prometheus_metrics

            result = net_collect_prometheus_metrics(hosts=["spine-01"])

        assert result["status"] == "error"
        assert "prometheus-client not installed" in result["error"]
        assert "pip install" in result["error"]


# ---------------------------------------------------------------------------
# Dashboard generation tool tests
# ---------------------------------------------------------------------------


class TestGrafanaDashboardTool:
    """Tests for net_get_grafana_dashboard MCP tool."""

    def test_overview_dashboard(self):
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        result = net_get_grafana_dashboard("overview")
        assert result["status"] == "success"
        assert result["dashboard_type"] == "overview"

        dashboard = json.loads(result["dashboard_json"])
        assert dashboard["title"] == "Network Overview"
        assert len(dashboard["panels"]) > 0
        assert "templating" in dashboard
        assert "list" in dashboard["templating"]

    def test_bgp_dashboard(self):
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        result = net_get_grafana_dashboard("bgp")
        assert result["status"] == "success"
        assert result["dashboard_type"] == "bgp"

        dashboard = json.loads(result["dashboard_json"])
        assert dashboard["title"] == "BGP Fabric Health"
        assert len(dashboard["panels"]) > 0
        assert "templating" in dashboard

    def test_interfaces_dashboard(self):
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        result = net_get_grafana_dashboard("interfaces")
        assert result["status"] == "success"
        assert result["dashboard_type"] == "interfaces"

        dashboard = json.loads(result["dashboard_json"])
        assert dashboard["title"] == "Interface Monitoring"
        assert len(dashboard["panels"]) > 0
        assert "templating" in dashboard

    def test_unknown_dashboard_returns_error(self):
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        result = net_get_grafana_dashboard("unknown")
        assert result["status"] == "error"
        assert "Unknown dashboard type" in result["error"]
        assert "unknown" in result["error"]

    def test_dashboard_has_required_structure(self):
        """All dashboard types contain uid, panels, templating, and refresh fields."""
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        for dtype in ("overview", "bgp", "interfaces"):
            result = net_get_grafana_dashboard(dtype)
            dashboard = json.loads(result["dashboard_json"])
            assert "uid" in dashboard, f"{dtype}: missing uid"
            assert "panels" in dashboard, f"{dtype}: missing panels"
            assert isinstance(dashboard["panels"], list), f"{dtype}: panels not a list"
            assert "templating" in dashboard, f"{dtype}: missing templating"
            assert "list" in dashboard["templating"], f"{dtype}: missing templating.list"
            assert "refresh" in dashboard, f"{dtype}: missing refresh"

    def test_dashboard_panels_have_targets(self):
        """Every panel has at least one target with an expr field."""
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        for dtype in ("overview", "bgp", "interfaces"):
            result = net_get_grafana_dashboard(dtype)
            dashboard = json.loads(result["dashboard_json"])
            for panel in dashboard["panels"]:
                assert "targets" in panel, f"{dtype}/{panel['title']}: missing targets"
                assert len(panel["targets"]) >= 1, f"{dtype}/{panel['title']}: no targets"
                assert "expr" in panel["targets"][0], f"{dtype}/{panel['title']}: target missing expr"

    def test_dashboard_import_instructions(self):
        """Result includes import instructions for Grafana."""
        from network_mcp.integrations.prometheus import net_get_grafana_dashboard

        result = net_get_grafana_dashboard("overview")
        assert "import_instructions" in result
        assert "Grafana" in result["import_instructions"]


# ---------------------------------------------------------------------------
# Dashboard JSON file validation
# ---------------------------------------------------------------------------


class TestDashboardFiles:
    """Validate the static dashboard JSON files in the dashboards/ directory."""

    @pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
    def test_file_is_valid_json(self, path):
        """Each dashboard file is valid JSON."""
        assert path.exists(), f"Dashboard file not found: {path}"
        content = path.read_text()
        data = json.loads(content)  # raises on invalid JSON
        assert isinstance(data, dict)

    @pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
    def test_file_has_title_and_panels(self, path):
        """Each dashboard file has a title and panels list at the top level."""
        data = json.loads(path.read_text())
        assert "title" in data, f"{path.name}: missing 'title'"
        assert "panels" in data, f"{path.name}: missing 'panels'"
        assert isinstance(data["panels"], list), f"{path.name}: 'panels' is not a list"
        assert len(data["panels"]) > 0, f"{path.name}: 'panels' is empty"

    @pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
    def test_file_has_templating(self, path):
        """Each dashboard file has a templating section with variables."""
        data = json.loads(path.read_text())
        assert "templating" in data, f"{path.name}: missing 'templating'"
        assert "list" in data["templating"], f"{path.name}: missing 'templating.list'"
        assert len(data["templating"]["list"]) > 0, f"{path.name}: no template variables"

    @pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
    def test_file_has_datasource_input(self, path):
        """Each dashboard file has a Prometheus datasource __input."""
        data = json.loads(path.read_text())
        assert "__inputs" in data, f"{path.name}: missing '__inputs'"
        ds_inputs = [i for i in data["__inputs"] if i.get("pluginId") == "prometheus"]
        assert len(ds_inputs) >= 1, f"{path.name}: no Prometheus datasource input"

    @pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
    def test_file_has_uid(self, path):
        """Each dashboard file has a uid field."""
        data = json.loads(path.read_text())
        assert "uid" in data, f"{path.name}: missing 'uid'"
        assert isinstance(data["uid"], str), f"{path.name}: uid is not a string"

    @pytest.mark.parametrize("path", DASHBOARD_FILES, ids=lambda p: p.name)
    def test_panels_have_valid_structure(self, path):
        """Each panel has id, type, title, and gridPos."""
        data = json.loads(path.read_text())
        for panel in data["panels"]:
            assert "id" in panel, f"{path.name}: panel missing 'id'"
            assert "type" in panel, f"{path.name}: panel missing 'type'"
            assert "title" in panel, f"{path.name}: panel missing 'title'"
            assert "gridPos" in panel, f"{path.name}: panel missing 'gridPos'"

    def test_overview_has_host_variable(self):
        """Network overview dashboard uses label_values(network_device_cpu_percent, host) for host selector."""
        data = json.loads((DASHBOARDS_DIR / "network-overview.json").read_text())
        host_vars = [v for v in data["templating"]["list"] if v.get("name") == "host"]
        assert len(host_vars) == 1
        assert "network_device_cpu_percent" in host_vars[0]["query"]

    def test_bgp_has_vrf_variable(self):
        """BGP fabric dashboard has a vrf template variable."""
        data = json.loads((DASHBOARDS_DIR / "bgp-fabric.json").read_text())
        vrf_vars = [v for v in data["templating"]["list"] if v.get("name") == "vrf"]
        assert len(vrf_vars) == 1

    def test_interfaces_has_interface_variable(self):
        """Interface monitoring dashboard has an interface template variable."""
        data = json.loads((DASHBOARDS_DIR / "interface-monitoring.json").read_text())
        iface_vars = [v for v in data["templating"]["list"] if v.get("name") == "interface"]
        assert len(iface_vars) == 1
