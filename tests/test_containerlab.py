"""Tests for the Containerlab integration module.

All subprocess calls are mocked so no real containerlab CLI is required.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.integrations.containerlab import (
    ContainerlabInventoryBackend,
    _detect_platform,
    _extract_ip,
    _normalise_inspect_output,
    _parse_node_name,
    _run_inspect,
    net_containerlab_discover,
    net_containerlab_inventory,
)

# ---------------------------------------------------------------------------
# Sample JSON payloads produced by containerlab inspect --format json
# ---------------------------------------------------------------------------

_FLAT_CONTAINERS = [
    {
        "lab_name": "mylab",
        "name": "clab-mylab-leaf1",
        "kind": "ceos",
        "image": "ceos:4.28.0F",
        "state": "running",
        "ipv4_address": "172.20.20.2/24",
        "ipv6_address": "2001:172:20:20::2/64",
    },
    {
        "lab_name": "mylab",
        "name": "clab-mylab-leaf2",
        "kind": "ceos",
        "image": "ceos:4.28.0F",
        "state": "running",
        "ipv4_address": "172.20.20.3/24",
        "ipv6_address": "2001:172:20:20::3/64",
    },
    {
        "lab_name": "mylab",
        "name": "clab-mylab-spine1",
        "kind": "crpd",
        "image": "crpd:23.4R1",
        "state": "running",
        "ipv4_address": "172.20.20.4/24",
        "ipv6_address": "2001:172:20:20::4/64",
    },
]

_MIXED_LABS_CONTAINERS = [
    {
        "lab_name": "lab-a",
        "name": "clab-lab-a-r1",
        "kind": "vr-csr",
        "image": "vr-csr:17.3",
        "state": "running",
        "ipv4_address": "10.0.0.2/24",
    },
    {
        "lab_name": "lab-b",
        "name": "clab-lab-b-sw1",
        "kind": "vr-nxos",
        "image": "vr-nxos:9.3.1",
        "state": "running",
        "ipv4_address": "10.0.1.2/24",
    },
    {
        "lab_name": "lab-b",
        "name": "clab-lab-b-sw2",
        "kind": "vr-nxos",
        "image": "vr-nxos:9.3.1",
        "state": "exited",  # should be skipped
        "ipv4_address": "10.0.1.3/24",
    },
]


def _make_proc(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    """Build a mock CompletedProcess object."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# _detect_platform
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    @pytest.mark.parametrize(
        "image_or_kind,expected",
        [
            ("ceos", "eos"),
            ("ceos:4.28.0F", "eos"),
            ("arista/ceos:latest", "eos"),
            ("veos:4.27.0F", "eos"),
            ("arista-veos:latest", "eos"),
            ("crpd", "junos"),
            ("crpd:23.4R1", "junos"),
            ("vjunos-router:23.2R1", "junos"),
            ("vr-junos:21.4R1", "junos"),
            ("juniper/junos:latest", "junos"),
            ("vr-nxos", "nxos"),
            ("vr-nxos:9.3.1", "nxos"),
            ("nxos:9.3.1", "nxos"),
            ("vr-csr", "iosxe"),
            ("vr-csr:17.3", "iosxe"),
            ("cisco-xrd:7.9.1", "iosxe"),
            ("iosxe:17.6", "iosxe"),
            ("ios-xe:17.6", "iosxe"),
            ("unknown-image:latest", "eos"),  # default
            ("", "eos"),  # empty string defaults
        ],
    )
    def test_image_to_platform(self, image_or_kind, expected):
        assert _detect_platform(image_or_kind) == expected

    def test_case_insensitive(self):
        assert _detect_platform("CEOS:4.28.0F") == "eos"
        assert _detect_platform("CRPD:23.4R1") == "junos"
        assert _detect_platform("VR-NXOS:9.3.1") == "nxos"


# ---------------------------------------------------------------------------
# _parse_node_name
# ---------------------------------------------------------------------------


class TestParseNodeName:
    def test_standard_format(self):
        assert _parse_node_name("clab-mylab-leaf1", "mylab") == "leaf1"

    def test_lab_with_hyphen(self):
        assert _parse_node_name("clab-lab-a-r1", "lab-a") == "r1"

    def test_no_prefix_match(self):
        # Falls back to full container name
        assert _parse_node_name("some-other-container", "mylab") == "some-other-container"

    def test_empty_lab_name(self):
        assert _parse_node_name("clab-mylab-leaf1", "") == "clab-mylab-leaf1"


# ---------------------------------------------------------------------------
# _extract_ip
# ---------------------------------------------------------------------------


class TestExtractIp:
    def test_with_cidr(self):
        assert _extract_ip("172.20.20.2/24") == "172.20.20.2"

    def test_without_cidr(self):
        assert _extract_ip("172.20.20.2") == "172.20.20.2"

    def test_ipv6_with_cidr(self):
        assert _extract_ip("2001:172:20:20::2/64") == "2001:172:20:20::2"

    def test_empty_string(self):
        assert _extract_ip("") == ""


# ---------------------------------------------------------------------------
# _normalise_inspect_output
# ---------------------------------------------------------------------------


class TestNormaliseInspectOutput:
    def test_flat_list(self):
        result = _normalise_inspect_output(_FLAT_CONTAINERS)
        assert len(result) == 3
        assert result[0]["name"] == "clab-mylab-leaf1"

    def test_wrapped_dict(self):
        wrapped = {"containers": _FLAT_CONTAINERS}
        result = _normalise_inspect_output(wrapped)
        assert len(result) == 3

    def test_per_lab_dict(self):
        # Use containers WITHOUT pre-set lab_name so setdefault injects it from the dict key
        c1 = {"name": "clab-mylab-leaf1", "state": "running", "ipv4_address": "172.20.20.2/24"}
        c2 = {"name": "clab-mylab-leaf2", "state": "running", "ipv4_address": "172.20.20.3/24"}
        c3 = {"name": "clab-otherlab-spine1", "state": "running", "ipv4_address": "172.20.20.4/24"}
        per_lab = {
            "mylab": {"containers": [c1, c2]},
            "otherlab": {"containers": [c3]},
        }
        result = _normalise_inspect_output(per_lab)
        assert len(result) == 3
        # lab_name injected for per-lab format
        lab_names = {c["lab_name"] for c in result}
        assert "mylab" in lab_names
        assert "otherlab" in lab_names

    def test_per_lab_list_values(self):
        per_lab = {
            "mylab": _FLAT_CONTAINERS[:2],
        }
        result = _normalise_inspect_output(per_lab)
        assert len(result) == 2
        for c in result:
            assert c.get("lab_name") == "mylab"

    def test_unknown_format_returns_empty(self):
        assert _normalise_inspect_output("not-a-list-or-dict") == []
        assert _normalise_inspect_output(None) == []
        assert _normalise_inspect_output(42) == []

    def test_empty_list(self):
        assert _normalise_inspect_output([]) == []

    def test_skips_non_dict_entries(self):
        data = [_FLAT_CONTAINERS[0], "garbage", None, 42, _FLAT_CONTAINERS[1]]
        result = _normalise_inspect_output(data)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _run_inspect
# ---------------------------------------------------------------------------


class TestRunInspect:
    def test_all_labs(self):
        stdout = json.dumps(_FLAT_CONTAINERS)
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            result = _run_inspect()
        assert len(result) == 3

    def test_specific_lab(self):
        stdout = json.dumps(_FLAT_CONTAINERS[:2])
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)) as mock_run:
            result = _run_inspect("mylab")
        assert len(result) == 2
        call_args = mock_run.call_args[0][0]
        assert "--name" in call_args
        assert "mylab" in call_args
        assert "--all" not in call_args

    def test_no_lab_uses_all_flag(self):
        stdout = json.dumps([])
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)) as mock_run:
            _run_inspect()
        call_args = mock_run.call_args[0][0]
        assert "--all" in call_args

    def test_empty_output_returns_empty_list(self):
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc("")):
            result = _run_inspect()
        assert result == []

    def test_non_zero_exit_raises(self):
        with patch(
            "network_mcp.integrations.containerlab.subprocess.run",
            return_value=_make_proc("", returncode=1, stderr="lab not found"),
        ):
            with pytest.raises(RuntimeError, match="containerlab inspect failed"):
                _run_inspect("missing-lab")

    def test_cli_not_found_raises(self):
        with patch(
            "network_mcp.integrations.containerlab.subprocess.run",
            side_effect=FileNotFoundError("no such file"),
        ):
            with pytest.raises(RuntimeError, match="containerlab CLI not found"):
                _run_inspect()

    def test_timeout_raises(self):
        with patch(
            "network_mcp.integrations.containerlab.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="containerlab", timeout=30),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                _run_inspect()

    def test_invalid_json_raises(self):
        with patch(
            "network_mcp.integrations.containerlab.subprocess.run",
            return_value=_make_proc("not-json"),
        ):
            with pytest.raises(RuntimeError, match="Failed to parse"):
                _run_inspect()


# ---------------------------------------------------------------------------
# ContainerlabInventoryBackend
# ---------------------------------------------------------------------------


class TestContainerlabInventoryBackend:
    def _make_backend(self, containers: list[dict], lab_name: str | None = None) -> ContainerlabInventoryBackend:
        stdout = json.dumps(containers)
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            return ContainerlabInventoryBackend(lab_name=lab_name)

    def test_load_running_nodes(self):
        backend = self._make_backend(_FLAT_CONTAINERS)
        devices = backend.get_devices()
        assert len(devices) == 3
        assert "leaf1" in devices
        assert "leaf2" in devices
        assert "spine1" in devices

    def test_skips_non_running_nodes(self):
        backend = self._make_backend(_MIXED_LABS_CONTAINERS)
        devices = backend.get_devices()
        # sw2 is "exited", should be skipped
        assert "sw2" not in devices
        assert len(devices) == 2

    def test_platform_detected_from_kind(self):
        backend = self._make_backend(_FLAT_CONTAINERS)
        devices = backend.get_devices()
        assert devices["leaf1"].platform == "eos"
        assert devices["spine1"].platform == "junos"

    def test_platform_detected_from_image_when_no_kind(self):
        containers = [
            {
                "lab_name": "lab",
                "name": "clab-lab-r1",
                "kind": "",  # empty kind
                "image": "crpd:23.4R1",
                "state": "running",
                "ipv4_address": "10.0.0.2/24",
            }
        ]
        backend = self._make_backend(containers)
        devices = backend.get_devices()
        assert devices["r1"].platform == "junos"

    def test_host_ip_stripped_of_cidr(self):
        backend = self._make_backend(_FLAT_CONTAINERS)
        devices = backend.get_devices()
        assert devices["leaf1"].host == "172.20.20.2"
        assert "/" not in devices["leaf1"].host

    def test_mgmt_ipv4_field_fallback(self):
        containers = [
            {
                "lab_name": "lab",
                "name": "clab-lab-r1",
                "kind": "ceos",
                "image": "ceos:latest",
                "state": "running",
                "mgmt_ipv4": "192.168.1.5/24",  # alternate field name
            }
        ]
        backend = self._make_backend(containers)
        devices = backend.get_devices()
        assert "r1" in devices
        assert devices["r1"].host == "192.168.1.5"

    def test_default_credentials_applied(self):
        backend = self._make_backend(_FLAT_CONTAINERS[:1])
        devices = backend.get_devices()
        leaf1 = devices["leaf1"]
        assert leaf1.username == "admin"
        assert leaf1.password.get_secret_value() == "admin"
        assert leaf1.transport == "https"
        assert leaf1.port == 443

    def test_custom_credentials_applied(self):
        stdout = json.dumps(_FLAT_CONTAINERS[:1])
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            backend = ContainerlabInventoryBackend(
                default_username="netops",
                default_password="s3cr3t",
                default_transport="http",
                default_port=80,
            )
        devices = backend.get_devices()
        leaf1 = devices["leaf1"]
        assert leaf1.username == "netops"
        assert leaf1.password.get_secret_value() == "s3cr3t"
        assert leaf1.transport == "http"
        assert leaf1.port == 80

    def test_tags_include_containerlab_and_lab(self):
        backend = self._make_backend(_FLAT_CONTAINERS[:1])
        devices = backend.get_devices()
        tags = devices["leaf1"].tags
        assert "containerlab" in tags
        assert "lab:mylab" in tags

    def test_get_device_returns_correct_entry(self):
        backend = self._make_backend(_FLAT_CONTAINERS)
        assert backend.get_device("leaf1") is not None
        assert backend.get_device("leaf1").host == "172.20.20.2"

    def test_get_device_returns_none_for_missing(self):
        backend = self._make_backend(_FLAT_CONTAINERS)
        assert backend.get_device("nonexistent") is None

    def test_empty_containers_results_in_empty_backend(self):
        backend = self._make_backend([])
        assert backend.get_devices() == {}

    def test_skips_container_without_ip(self):
        containers = [
            {
                "lab_name": "lab",
                "name": "clab-lab-r1",
                "kind": "ceos",
                "state": "running",
                "ipv4_address": "",  # no IP
            }
        ]
        backend = self._make_backend(containers)
        assert backend.get_devices() == {}

    def test_multi_lab_discovery(self):
        backend = self._make_backend(_MIXED_LABS_CONTAINERS)
        devices = backend.get_devices()
        assert "r1" in devices
        assert "sw1" in devices
        assert devices["r1"].platform == "iosxe"
        assert devices["sw1"].platform == "nxos"

    def test_runtime_error_propagates(self):
        with patch(
            "network_mcp.integrations.containerlab.subprocess.run",
            side_effect=FileNotFoundError("no clab"),
        ):
            with pytest.raises(RuntimeError, match="containerlab CLI not found"):
                ContainerlabInventoryBackend()


# ---------------------------------------------------------------------------
# net_containerlab_discover
# ---------------------------------------------------------------------------


class TestNetContainerlabDiscover:
    def test_success_flat_output(self):
        stdout = json.dumps(_FLAT_CONTAINERS)
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            result = net_containerlab_discover()

        assert result["status"] == "success"
        assert result["lab_count"] == 1
        assert result["total_nodes"] == 3
        assert "mylab" in result["labs"]
        assert result["labs"]["mylab"]["node_count"] == 3

    def test_node_details_present(self):
        stdout = json.dumps(_FLAT_CONTAINERS[:1])
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            result = net_containerlab_discover()

        node = result["labs"]["mylab"]["nodes"][0]
        assert node["name"] == "leaf1"
        assert node["platform"] == "eos"
        assert node["ip"] == "172.20.20.2"
        assert node["kind"] == "ceos"

    def test_multi_lab_counted_correctly(self):
        stdout = json.dumps(_MIXED_LABS_CONTAINERS)
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            result = net_containerlab_discover()

        assert result["status"] == "success"
        assert result["lab_count"] == 2
        # Only running nodes counted
        assert result["total_nodes"] == 2
        assert result["labs"]["lab-a"]["node_count"] == 1
        assert result["labs"]["lab-b"]["node_count"] == 1

    def test_no_running_labs_returns_empty(self):
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc("[]")):
            result = net_containerlab_discover()

        assert result["status"] == "success"
        assert result["lab_count"] == 0
        assert result["total_nodes"] == 0
        assert result["labs"] == {}

    def test_cli_not_found_returns_error(self):
        with patch(
            "network_mcp.integrations.containerlab.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            result = net_containerlab_discover()

        assert result["status"] == "error"
        assert "containerlab CLI not found" in result["error"]

    def test_inspect_failure_returns_error(self):
        with patch(
            "network_mcp.integrations.containerlab.subprocess.run",
            return_value=_make_proc("", returncode=1, stderr="permission denied"),
        ):
            result = net_containerlab_discover()

        assert result["status"] == "error"
        assert "inspect failed" in result["error"]

    def test_demo_mode_returns_mock_data(self):
        with patch("network_mcp.integrations.containerlab.settings") as mock_settings:
            mock_settings.net_demo_mode = True
            result = net_containerlab_discover()

        assert result["status"] == "success"
        assert result["lab_count"] == 1
        assert "demo-lab" in result["labs"]

    def test_exited_nodes_excluded_from_results(self):
        containers = [
            {
                "lab_name": "lab",
                "name": "clab-lab-r1",
                "kind": "ceos",
                "image": "ceos:latest",
                "state": "running",
                "ipv4_address": "10.0.0.2/24",
            },
            {
                "lab_name": "lab",
                "name": "clab-lab-r2",
                "kind": "ceos",
                "image": "ceos:latest",
                "state": "exited",
                "ipv4_address": "10.0.0.3/24",
            },
        ]
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(json.dumps(containers))):
            result = net_containerlab_discover()

        assert result["total_nodes"] == 1
        assert result["labs"]["lab"]["node_count"] == 1


# ---------------------------------------------------------------------------
# net_containerlab_inventory
# ---------------------------------------------------------------------------


class TestNetContainerlabInventory:
    def _run_with_mock(self, containers: list[dict], lab_name: str = "mylab") -> dict:
        stdout = json.dumps(containers)
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            with patch("network_mcp.integrations.containerlab.conn_mgr") as mock_cm:
                result = net_containerlab_inventory(lab_name)
                self._mock_cm = mock_cm
        return result

    def test_success_returns_device_list(self):
        result = self._run_with_mock(_FLAT_CONTAINERS)

        assert result["status"] == "success"
        assert result["lab_name"] == "mylab"
        assert result["imported_count"] == 3
        assert len(result["devices"]) == 3

    def test_device_entries_have_required_fields(self):
        result = self._run_with_mock(_FLAT_CONTAINERS[:1])

        device = result["devices"][0]
        assert "name" in device
        assert "host" in device
        assert "platform" in device

    def test_inventory_loaded_into_conn_mgr(self):
        stdout = json.dumps(_FLAT_CONTAINERS)
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            with patch("network_mcp.integrations.containerlab.conn_mgr") as mock_cm:
                net_containerlab_inventory("mylab")
                mock_cm.load_inventory_from_backend.assert_called_once()

    def test_empty_lab_returns_error(self):
        result = self._run_with_mock([])

        assert result["status"] == "error"
        assert result["lab_name"] == "mylab"
        assert "No running nodes" in result["error"]

    def test_cli_not_found_returns_error(self):
        with patch(
            "network_mcp.integrations.containerlab.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            result = net_containerlab_inventory("mylab")

        assert result["status"] == "error"
        assert result["lab_name"] == "mylab"
        assert "containerlab CLI not found" in result["error"]

    def test_devices_sorted_by_name(self):
        result = self._run_with_mock(_FLAT_CONTAINERS)

        names = [d["name"] for d in result["devices"]]
        assert names == sorted(names)

    def test_demo_mode_returns_mock_data(self):
        with patch("network_mcp.integrations.containerlab.settings") as mock_settings:
            mock_settings.net_demo_mode = True
            result = net_containerlab_inventory("demo-lab")

        assert result["status"] == "success"
        assert result["lab_name"] == "demo-lab"
        assert result["imported_count"] == 2

    def test_custom_credentials_passed_to_backend(self):
        stdout = json.dumps(_FLAT_CONTAINERS[:1])
        captured_backend = None

        def capture_backend(backend):
            nonlocal captured_backend
            captured_backend = backend

        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            with patch("network_mcp.integrations.containerlab.conn_mgr") as mock_cm:
                mock_cm.load_inventory_from_backend.side_effect = capture_backend
                net_containerlab_inventory("mylab", username="netops", password="secret", transport="http", port=80)

        assert captured_backend is not None
        devices = captured_backend.get_devices()
        leaf1 = devices["leaf1"]
        assert leaf1.username == "netops"
        assert leaf1.password.get_secret_value() == "secret"
        assert leaf1.transport == "http"
        assert leaf1.port == 80

    def test_multi_vendor_lab_platforms(self):
        containers = [
            {
                "lab_name": "mixed",
                "name": "clab-mixed-eos1",
                "kind": "ceos",
                "image": "ceos:latest",
                "state": "running",
                "ipv4_address": "10.0.0.2/24",
            },
            {
                "lab_name": "mixed",
                "name": "clab-mixed-junos1",
                "kind": "crpd",
                "image": "crpd:latest",
                "state": "running",
                "ipv4_address": "10.0.0.3/24",
            },
            {
                "lab_name": "mixed",
                "name": "clab-mixed-nxos1",
                "kind": "vr-nxos",
                "image": "vr-nxos:latest",
                "state": "running",
                "ipv4_address": "10.0.0.4/24",
            },
            {
                "lab_name": "mixed",
                "name": "clab-mixed-csr1",
                "kind": "vr-csr",
                "image": "vr-csr:latest",
                "state": "running",
                "ipv4_address": "10.0.0.5/24",
            },
        ]
        stdout = json.dumps(containers)
        with patch("network_mcp.integrations.containerlab.subprocess.run", return_value=_make_proc(stdout)):
            with patch("network_mcp.integrations.containerlab.conn_mgr"):
                result = net_containerlab_inventory("mixed")

        assert result["status"] == "success"
        platform_map = {d["name"]: d["platform"] for d in result["devices"]}
        assert platform_map["eos1"] == "eos"
        assert platform_map["junos1"] == "junos"
        assert platform_map["nxos1"] == "nxos"
        assert platform_map["csr1"] == "iosxe"
