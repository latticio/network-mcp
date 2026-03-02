"""Prometheus metrics export and Grafana dashboard generation."""

from __future__ import annotations

import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from network_mcp.helpers import READ_ONLY
from network_mcp.server import conn_mgr, mcp, settings

logger = logging.getLogger("network-mcp.prometheus")

try:
    from prometheus_client import CollectorRegistry, GaugeMetricFamily, generate_latest
except ImportError:
    CollectorRegistry = None  # type: ignore[assignment,misc]
    GaugeMetricFamily = None  # type: ignore[assignment,misc]
    generate_latest = None  # type: ignore[assignment]

_PROMETHEUS_MISSING = {
    "status": "error",
    "error": "prometheus-client not installed. Install with: pip install latticio[prometheus]",
}


def _get_all_inventory_hosts() -> list[str]:
    """Get all host names from the connection manager inventory."""
    return conn_mgr.list_devices()


def _collect_device_metrics(host: str) -> dict[str, Any]:
    """Collect health metrics from a single device.

    Uses the vendor-agnostic common tools to gather device facts,
    interface data, and BGP summary for metric extraction.

    Returns:
        dict with collected metric values or an error indicator.
    """
    from network_mcp.tools.common.device import net_get_device_info
    from network_mcp.tools.common.interfaces import net_get_interfaces
    from network_mcp.tools.common.routing import net_get_bgp_summary

    result: dict[str, Any] = {"host": host, "errors": []}

    # Device facts (uptime, platform)
    info = net_get_device_info(host)
    if info["status"] == "success":
        data = info["data"]
        result["platform"] = info.get("platform", "unknown")
        result["uptime_seconds"] = data.get("uptime_seconds", 0)
        # CPU and memory are not in get_facts(); leave as None to skip
        result["cpu_percent"] = data.get("cpu_percent")
        result["memory_percent"] = data.get("memory_percent")
    else:
        result["errors"].append(f"device_info: {info.get('error', 'unknown')}")
        result["platform"] = "unknown"

    # BGP peers
    bgp = net_get_bgp_summary(host)
    if bgp["status"] == "success":
        bgp_data = bgp["data"]
        peers = bgp_data.get("peers", {})
        established = sum(1 for p in peers.values() if p.get("state", "").lower() == "established")
        result["bgp_peers_established"] = established
        result["bgp_vrf"] = "default"
    else:
        result["errors"].append(f"bgp: {bgp.get('error', 'unknown')}")

    # Interface errors
    ifaces = net_get_interfaces(host)
    if ifaces["status"] == "success":
        iface_errors: list[dict[str, Any]] = []
        for iface_name, iface_data in ifaces["data"].items():
            in_errors = iface_data.get("input_errors", 0)
            out_errors = iface_data.get("output_errors", 0)
            if in_errors or out_errors:
                iface_errors.append({"interface": iface_name, "input_errors": in_errors, "output_errors": out_errors})
        result["interface_errors"] = iface_errors
    else:
        result["errors"].append(f"interfaces: {ifaces.get('error', 'unknown')}")

    return result


@mcp.tool(annotations=READ_ONLY)
def net_collect_prometheus_metrics(hosts: list[str] | None = None) -> dict[str, Any]:
    """Collect device health metrics and expose in Prometheus text format.

    Gathers CPU, memory, BGP peer count, interface errors, and uptime from
    network devices and formats them as Prometheus exposition text. Uses a
    separate CollectorRegistry to avoid conflicts with any existing collectors.

    Args:
        hosts: List of hostnames or IPs to collect from. If None, collects from all inventory devices.
    """
    if settings.net_demo_mode:
        from network_mcp.mock_data.integrations import PROMETHEUS_METRICS_RESPONSE

        return copy.deepcopy(PROMETHEUS_METRICS_RESPONSE)

    if CollectorRegistry is None:
        return _PROMETHEUS_MISSING

    target_hosts = hosts if hosts else _get_all_inventory_hosts()
    if not target_hosts:
        return {"status": "error", "error": "No hosts specified and inventory is empty."}

    registry = CollectorRegistry()

    # Collect metrics from all hosts in parallel
    all_metrics: list[dict[str, Any]] = []
    device_errors: list[dict[str, str]] = []
    max_workers = min(settings.net_prometheus_max_workers, len(target_hosts))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_host = {executor.submit(_collect_device_metrics, host): host for host in target_hosts}
        for future in as_completed(future_to_host):
            host = future_to_host[future]
            try:
                metrics = future.result()
                all_metrics.append(metrics)
                if metrics["errors"]:
                    device_errors.append({"host": host, "errors": metrics["errors"]})
            except Exception as e:
                device_errors.append({"host": host, "errors": [str(e)]})

    # Build Prometheus gauge families
    cpu_gauge = GaugeMetricFamily(
        "network_device_cpu_percent",
        "Device CPU utilization percentage",
        labels=["host", "platform"],
    )
    memory_gauge = GaugeMetricFamily(
        "network_device_memory_percent",
        "Device memory utilization percentage",
        labels=["host", "platform"],
    )
    bgp_gauge = GaugeMetricFamily(
        "network_bgp_peers_established",
        "Number of established BGP peers",
        labels=["host", "vrf"],
    )
    iface_errors_gauge = GaugeMetricFamily(
        "network_interface_errors_total",
        "Total interface errors",
        labels=["host", "interface", "direction"],
    )
    uptime_gauge = GaugeMetricFamily(
        "network_device_uptime_seconds",
        "Device uptime in seconds",
        labels=["host", "platform"],
    )

    for m in all_metrics:
        host = m["host"]
        platform = m.get("platform", "unknown")

        if m.get("cpu_percent") is not None:
            cpu_gauge.add_metric([host, platform], float(m["cpu_percent"]))

        if m.get("memory_percent") is not None:
            memory_gauge.add_metric([host, platform], float(m["memory_percent"]))

        if "bgp_peers_established" in m:
            bgp_gauge.add_metric([host, m.get("bgp_vrf", "default")], float(m["bgp_peers_established"]))

        if m.get("uptime_seconds") is not None:
            uptime_gauge.add_metric([host, platform], float(m["uptime_seconds"]))

        for ie in m.get("interface_errors", []):
            iface_errors_gauge.add_metric([host, ie["interface"], "input"], float(ie["input_errors"]))
            iface_errors_gauge.add_metric([host, ie["interface"], "output"], float(ie["output_errors"]))

    # Register custom collector that yields our gauge families
    class _NetworkCollector:
        def collect(self):
            yield cpu_gauge
            yield memory_gauge
            yield bgp_gauge
            yield iface_errors_gauge
            yield uptime_gauge

    registry.register(_NetworkCollector())
    prometheus_text = generate_latest(registry).decode("utf-8")

    return {
        "status": "success",
        "hosts_collected": len(all_metrics),
        "hosts_with_errors": len(device_errors),
        "errors": device_errors if device_errors else None,
        "prometheus_text": prometheus_text,
    }


# ---------------------------------------------------------------------------
# Grafana dashboard generation
# ---------------------------------------------------------------------------

_DATASOURCE_TEMPLATE = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}


def _template_var_host() -> dict[str, Any]:
    """Build a Grafana template variable for host selection."""
    return {
        "name": "host",
        "label": "Host",
        "type": "query",
        "query": "label_values(network_device_uptime_seconds, host)",
        "refresh": 2,
        "includeAll": True,
        "multi": True,
        "current": {"text": "All", "value": "$__all"},
    }


def _panel(title: str, expr: str, *, panel_id: int, y: int, panel_type: str = "timeseries") -> dict[str, Any]:
    """Build a generic Grafana panel definition."""
    return {
        "id": panel_id,
        "type": panel_type,
        "title": title,
        "datasource": _DATASOURCE_TEMPLATE,
        "gridPos": {"h": 8, "w": 12, "x": (panel_id % 2) * 12, "y": y},
        "targets": [{"expr": expr, "legendFormat": "{{host}}", "refId": "A"}],
        "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
    }


def _stat_panel(title: str, expr: str, *, panel_id: int, y: int, unit: str = "short") -> dict[str, Any]:
    """Build a Grafana stat panel."""
    return {
        "id": panel_id,
        "type": "stat",
        "title": title,
        "datasource": _DATASOURCE_TEMPLATE,
        "gridPos": {"h": 4, "w": 6, "x": ((panel_id - 1) % 4) * 6, "y": y},
        "targets": [{"expr": expr, "legendFormat": "{{host}}", "refId": "A"}],
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
    }


def _wrap_dashboard(title: str, panels: list[dict], *, uid: str) -> dict[str, Any]:
    """Wrap panels into a complete importable Grafana dashboard JSON."""
    return {
        "__inputs": [{"name": "DS_PROMETHEUS", "label": "Prometheus", "type": "datasource", "pluginId": "prometheus"}],
        "uid": uid,
        "title": title,
        "tags": ["network-mcp", "network"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "templating": {"list": [_template_var_host()]},
        "panels": panels,
    }


def _build_overview_dashboard() -> dict[str, Any]:
    """Network overview: CPU, memory, uptime, BGP peer count."""
    panels = [
        _stat_panel(
            "Total Devices",
            'count(network_device_uptime_seconds{host=~"$host"})',
            panel_id=1,
            y=0,
        ),
        _stat_panel(
            "Avg CPU %",
            'avg(network_device_cpu_percent{host=~"$host"})',
            panel_id=2,
            y=0,
            unit="percent",
        ),
        _stat_panel(
            "Avg Memory %",
            'avg(network_device_memory_percent{host=~"$host"})',
            panel_id=3,
            y=0,
            unit="percent",
        ),
        _stat_panel(
            "Total BGP Peers",
            'sum(network_bgp_peers_established{host=~"$host"})',
            panel_id=4,
            y=0,
        ),
        _panel(
            "CPU Utilization",
            'network_device_cpu_percent{host=~"$host"}',
            panel_id=5,
            y=4,
        ),
        _panel(
            "Memory Utilization",
            'network_device_memory_percent{host=~"$host"}',
            panel_id=6,
            y=4,
        ),
        _panel(
            "Device Uptime",
            'network_device_uptime_seconds{host=~"$host"}',
            panel_id=7,
            y=12,
        ),
        _panel(
            "BGP Established Peers",
            'network_bgp_peers_established{host=~"$host"}',
            panel_id=8,
            y=12,
        ),
    ]
    return _wrap_dashboard("Network Overview", panels, uid="network-mcp-overview")


def _build_bgp_dashboard() -> dict[str, Any]:
    """BGP fabric health: peer states, prefix counts."""
    panels = [
        _stat_panel(
            "Established Peers",
            'sum(network_bgp_peers_established{host=~"$host"})',
            panel_id=1,
            y=0,
        ),
        _stat_panel(
            "Devices with BGP",
            'count(network_bgp_peers_established{host=~"$host"})',
            panel_id=2,
            y=0,
        ),
        _stat_panel(
            "Min Peers per Device",
            'min(network_bgp_peers_established{host=~"$host"})',
            panel_id=3,
            y=0,
        ),
        _stat_panel(
            "Max Peers per Device",
            'max(network_bgp_peers_established{host=~"$host"})',
            panel_id=4,
            y=0,
        ),
        _panel(
            "BGP Established Peers Over Time",
            'network_bgp_peers_established{host=~"$host"}',
            panel_id=5,
            y=4,
        ),
        _panel(
            "BGP Peer Changes (delta)",
            'delta(network_bgp_peers_established{host=~"$host"}[5m])',
            panel_id=6,
            y=4,
        ),
    ]
    return _wrap_dashboard("BGP Fabric Health", panels, uid="network-mcp-bgp")


def _build_interfaces_dashboard() -> dict[str, Any]:
    """Interface monitoring: errors, discards."""
    panels = [
        _stat_panel(
            "Total Input Errors",
            'sum(network_interface_errors_total{host=~"$host", direction="input"})',
            panel_id=1,
            y=0,
        ),
        _stat_panel(
            "Total Output Errors",
            'sum(network_interface_errors_total{host=~"$host", direction="output"})',
            panel_id=2,
            y=0,
        ),
        _stat_panel(
            "Interfaces with Errors",
            'count(network_interface_errors_total{host=~"$host"} > 0)',
            panel_id=3,
            y=0,
        ),
        _stat_panel(
            "Monitored Devices",
            'count(network_device_uptime_seconds{host=~"$host"})',
            panel_id=4,
            y=0,
        ),
        _panel(
            "Input Errors by Interface",
            'network_interface_errors_total{host=~"$host", direction="input"}',
            panel_id=5,
            y=4,
        ),
        _panel(
            "Output Errors by Interface",
            'network_interface_errors_total{host=~"$host", direction="output"}',
            panel_id=6,
            y=4,
        ),
        _panel(
            "Top Error Interfaces (rate)",
            'topk(10, rate(network_interface_errors_total{host=~"$host"}[5m]))',
            panel_id=7,
            y=12,
        ),
        _panel(
            "Error Rate by Device",
            'sum by (host) (rate(network_interface_errors_total{host=~"$host"}[5m]))',
            panel_id=8,
            y=12,
        ),
    ]
    return _wrap_dashboard("Interface Monitoring", panels, uid="network-mcp-interfaces")


_DASHBOARD_BUILDERS: dict[str, Any] = {
    "overview": _build_overview_dashboard,
    "bgp": _build_bgp_dashboard,
    "interfaces": _build_interfaces_dashboard,
}


@mcp.tool(annotations=READ_ONLY)
def net_get_grafana_dashboard(dashboard_type: str) -> dict[str, Any]:
    """Generate a ready-to-import Grafana dashboard JSON.

    Creates complete Grafana dashboard definitions with PromQL queries targeting
    the metrics exposed by net_collect_prometheus_metrics. The generated JSON
    can be imported directly into Grafana via the UI or provisioning API.

    Args:
        dashboard_type: Dashboard type to generate. One of: 'overview' (CPU, memory,
            uptime, BGP peer count), 'bgp' (BGP fabric health with peer states),
            or 'interfaces' (interface error monitoring).
    """
    valid_types = sorted(_DASHBOARD_BUILDERS.keys())
    if dashboard_type not in _DASHBOARD_BUILDERS:
        return {
            "status": "error",
            "error": f"Unknown dashboard type '{dashboard_type}'. Valid types: {valid_types}",
        }

    dashboard = _DASHBOARD_BUILDERS[dashboard_type]()
    return {
        "status": "success",
        "dashboard_type": dashboard_type,
        "dashboard_json": json.dumps(dashboard, indent=2),
        "import_instructions": (
            "Import this dashboard into Grafana: Dashboards → Import → paste the dashboard_json content. "
            "Select your Prometheus data source when prompted."
        ),
    }
