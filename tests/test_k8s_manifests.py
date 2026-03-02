"""Tests for Kubernetes manifest validation.

Loads and parses all YAML files in k8s/ and validates production-readiness
requirements: resource limits, probe paths, image tags, HA settings,
PDB, NetworkPolicy, port consistency, no hardcoded secrets, and label
consistency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

K8S_DIR = Path(__file__).resolve().parent.parent / "k8s"


def load_all_manifests() -> list[dict]:
    """Load all YAML documents from all files in k8s/."""
    docs: list[dict] = []
    for fpath in sorted(K8S_DIR.glob("*.yaml")):
        with open(fpath) as fh:
            for doc in yaml.safe_load_all(fh):
                if doc is not None:
                    doc["_source_file"] = fpath.name
                    docs.append(doc)
    return docs


def find_by_kind(docs: list[dict], kind: str) -> list[dict]:
    """Return all documents with the given 'kind'."""
    return [d for d in docs if d.get("kind") == kind]


def get_containers(deployment: dict) -> list[dict]:
    """Extract container specs from a Deployment."""
    return deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifests() -> list[dict]:
    return load_all_manifests()


@pytest.fixture(scope="module")
def deployments(manifests) -> list[dict]:
    return find_by_kind(manifests, "Deployment")


@pytest.fixture(scope="module")
def services(manifests) -> list[dict]:
    return find_by_kind(manifests, "Service")


@pytest.fixture(scope="module")
def pdbs(manifests) -> list[dict]:
    return find_by_kind(manifests, "PodDisruptionBudget")


@pytest.fixture(scope="module")
def network_policies(manifests) -> list[dict]:
    return find_by_kind(manifests, "NetworkPolicy")


@pytest.fixture(scope="module")
def hpas(manifests) -> list[dict]:
    return find_by_kind(manifests, "HorizontalPodAutoscaler")


# ===================================================================
# 1. Manifests load successfully
# ===================================================================


class TestManifestsLoad:
    def test_k8s_directory_exists(self):
        assert K8S_DIR.is_dir(), f"Expected k8s/ directory at {K8S_DIR}"

    def test_yaml_files_exist(self):
        yamls = list(K8S_DIR.glob("*.yaml"))
        assert len(yamls) >= 4, f"Expected at least 4 YAML files in k8s/, found {len(yamls)}"

    def test_all_yamls_parse(self, manifests):
        assert len(manifests) >= 4, f"Expected at least 4 YAML docs, got {len(manifests)}"

    def test_deployment_exists(self, deployments):
        assert len(deployments) >= 1, "No Deployment found in k8s/"

    def test_service_exists(self, services):
        assert len(services) >= 1, "No Service found in k8s/"


# ===================================================================
# 2. Resource limits present
# ===================================================================


class TestResourceLimits:
    """Every container in every deployment must have CPU/memory requests and limits."""

    def test_all_containers_have_resource_requests(self, deployments):
        for deploy in deployments:
            name = deploy["metadata"]["name"]
            for container in get_containers(deploy):
                cname = container.get("name", "unknown")
                resources = container.get("resources", {})
                requests = resources.get("requests", {})
                assert "memory" in requests, f"{name}/{cname} missing resources.requests.memory"
                assert "cpu" in requests, f"{name}/{cname} missing resources.requests.cpu"

    def test_all_containers_have_resource_limits(self, deployments):
        for deploy in deployments:
            name = deploy["metadata"]["name"]
            for container in get_containers(deploy):
                cname = container.get("name", "unknown")
                resources = container.get("resources", {})
                limits = resources.get("limits", {})
                assert "memory" in limits, f"{name}/{cname} missing resources.limits.memory"
                assert "cpu" in limits, f"{name}/{cname} missing resources.limits.cpu"


# ===================================================================
# 3. Probe paths valid
# ===================================================================


class TestProbePaths:
    """All health probes must reference paths that start with /health."""

    def _collect_probe_paths(self, deployments) -> list[tuple[str, str, str]]:
        """Return (deployment_name, probe_type, path) tuples."""
        results = []
        for deploy in deployments:
            name = deploy["metadata"]["name"]
            for container in get_containers(deploy):
                for probe_type in ("livenessProbe", "readinessProbe", "startupProbe"):
                    probe = container.get(probe_type)
                    if probe and "httpGet" in probe:
                        path = probe["httpGet"].get("path", "")
                        results.append((name, probe_type, path))
        return results

    def test_probes_exist(self, deployments):
        probes = self._collect_probe_paths(deployments)
        assert len(probes) >= 1, "No HTTP probes found in any deployment"

    def test_probe_paths_start_with_health(self, deployments):
        for name, probe_type, path in self._collect_probe_paths(deployments):
            assert path.startswith("/health"), f"{name} {probe_type} path '{path}' does not start with /health"


# ===================================================================
# 4. Image tag not 'latest'
# ===================================================================


class TestImageTags:
    """Container image tags should not be :latest."""

    def test_no_latest_tag(self, deployments):
        for deploy in deployments:
            name = deploy["metadata"]["name"]
            for container in get_containers(deploy):
                image = container.get("image", "")
                cname = container.get("name", "unknown")
                # Image is "name:tag" — check tag is not "latest"
                if ":" in image:
                    tag = image.rsplit(":", 1)[1]
                    assert tag != "latest", (
                        f"{name}/{cname} uses image tag ':latest' — pin to a specific version or SHA"
                    )
                else:
                    # No tag means Docker defaults to :latest
                    pytest.fail(f"{name}/{cname} image '{image}' has no tag — pin to a specific version or SHA")


# ===================================================================
# 5. Replicas >= 2 for HA
# ===================================================================


class TestHighAvailability:
    """Deployment replicas must be >= 2, or HPA minReplicas >= 2."""

    def test_deployment_replicas_at_least_2(self, deployments):
        for deploy in deployments:
            name = deploy["metadata"]["name"]
            replicas = deploy.get("spec", {}).get("replicas", 1)
            assert replicas >= 2, f"{name} has replicas={replicas}, expected >= 2 for HA"

    def test_hpa_min_replicas_at_least_2(self, hpas):
        for hpa in hpas:
            name = hpa["metadata"]["name"]
            min_replicas = hpa.get("spec", {}).get("minReplicas", 1)
            assert min_replicas >= 2, f"{name} has minReplicas={min_replicas}, expected >= 2"


# ===================================================================
# 6. PDB exists and matches deployment labels
# ===================================================================


class TestPodDisruptionBudget:
    def test_pdb_exists(self, pdbs):
        assert len(pdbs) >= 1, "No PodDisruptionBudget found in k8s/"

    def test_pdb_matches_deployment_labels(self, deployments, pdbs):
        for deploy in deployments:
            deploy_labels = deploy.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
            app_label = deploy_labels.get("app")
            if not app_label:
                continue
            # Find a PDB whose selector matches
            matching = [
                pdb
                for pdb in pdbs
                if pdb.get("spec", {}).get("selector", {}).get("matchLabels", {}).get("app") == app_label
            ]
            assert len(matching) >= 1, f"No PDB found matching deployment label app={app_label}"


# ===================================================================
# 7. NetworkPolicy exists with ingress rules
# ===================================================================


class TestNetworkPolicy:
    def test_network_policy_exists(self, network_policies):
        assert len(network_policies) >= 1, "No NetworkPolicy found in k8s/"

    def test_has_ingress_rules(self, network_policies):
        for np in network_policies:
            name = np["metadata"]["name"]
            ingress = np.get("spec", {}).get("ingress", [])
            assert len(ingress) >= 1, f"NetworkPolicy {name} has no ingress rules"


# ===================================================================
# 8. Service port matches container port
# ===================================================================


class TestServicePortAlignment:
    """The Service targetPort must match a containerPort in the deployment."""

    def test_target_port_matches_container_port(self, deployments, services):
        # Collect all container ports from all deployments
        container_ports: set[int] = set()
        for deploy in deployments:
            for container in get_containers(deploy):
                for port_spec in container.get("ports", []):
                    container_ports.add(port_spec.get("containerPort"))

        for svc in services:
            svc_name = svc["metadata"]["name"]
            for port in svc.get("spec", {}).get("ports", []):
                target = port.get("targetPort")
                if isinstance(target, int):
                    assert target in container_ports, (
                        f"Service {svc_name} targetPort {target} does not match any containerPort {container_ports}"
                    )


# ===================================================================
# 9. No hardcoded secrets
# ===================================================================


class TestNoHardcodedSecrets:
    """No YAML file should contain password/secret/token with a literal string value.

    Environment variable references (valueFrom, secretKeyRef) are acceptable.
    Base64-encoded values in Secret kind resources are acceptable (that is
    the standard K8s pattern).
    """

    # Patterns that indicate a hardcoded secret in a non-Secret resource.
    _SECRET_PATTERNS = [
        re.compile(r'^\s*password:\s+"[^"]+"\s*$', re.IGNORECASE),
        re.compile(r"^\s*password:\s+'[^']+'\s*$", re.IGNORECASE),
        re.compile(r"^\s*password:\s+[^\s{][^\s]*\s*$", re.IGNORECASE),
    ]

    def test_no_plaintext_passwords_in_non_secret_yamls(self):
        """Scan all non-Secret YAML files for plaintext password values."""
        for fpath in sorted(K8S_DIR.glob("*.yaml")):
            # Secret resources legitimately contain base64-encoded values
            if fpath.name == "secret.yaml":
                continue
            content = fpath.read_text()
            for line_no, line in enumerate(content.splitlines(), 1):
                # Skip comments and env var references
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "secretKeyRef" in line or "valueFrom" in line:
                    continue
                if "configMapKeyRef" in line:
                    continue
                # Check for literal password assignment
                for pattern in self._SECRET_PATTERNS:
                    assert not pattern.match(stripped), (
                        f"{fpath.name}:{line_no} appears to contain a hardcoded secret: {stripped}"
                    )

    def test_secret_resource_uses_opaque_type(self):
        """Secret resources should use type: Opaque (not plain text)."""
        for fpath in sorted(K8S_DIR.glob("*.yaml")):
            with open(fpath) as fh:
                for doc in yaml.safe_load_all(fh):
                    if doc and doc.get("kind") == "Secret":
                        secret_type = doc.get("type", "")
                        assert secret_type == "Opaque", (
                            f"Secret in {fpath.name} has type={secret_type}, expected Opaque"
                        )


# ===================================================================
# 10. Labels consistent across all resources
# ===================================================================


class TestLabelsConsistent:
    """All resources should share a common label such as app: network-mcp."""

    def test_all_resources_have_app_label(self, manifests):
        for doc in manifests:
            kind = doc.get("kind", "Unknown")
            name = doc.get("metadata", {}).get("name", "unknown")
            # Skip documents without standard label expectations (e.g., Namespace)
            if kind in ("Namespace",):
                continue
            labels = doc.get("metadata", {}).get("labels", {})
            # Check for app label or app.kubernetes.io/name label
            has_app = "app" in labels or "app.kubernetes.io/name" in labels
            assert has_app, (
                f"{kind}/{name} in {doc.get('_source_file', '?')} missing app or app.kubernetes.io/name label"
            )

    def test_consistent_app_label_value(self, manifests):
        """All resources with an 'app' label should use the same value."""
        app_values: set[str] = set()
        for doc in manifests:
            labels = doc.get("metadata", {}).get("labels", {})
            if "app" in labels:
                app_values.add(labels["app"])
        if app_values:
            assert len(app_values) == 1, f"Inconsistent app label values across manifests: {app_values}"
            assert "network-mcp" in app_values, f"Expected app=network-mcp, got {app_values}"


# ===================================================================
# 11. Additional structural validations
# ===================================================================


class TestAdditionalStructure:
    """Bonus checks for manifest correctness."""

    def test_deployment_has_selector(self, deployments):
        for deploy in deployments:
            selector = deploy.get("spec", {}).get("selector", {}).get("matchLabels", {})
            assert len(selector) >= 1, f"Deployment {deploy['metadata']['name']} missing selector.matchLabels"

    def test_deployment_selector_matches_template_labels(self, deployments):
        for deploy in deployments:
            selector = deploy.get("spec", {}).get("selector", {}).get("matchLabels", {})
            template_labels = deploy.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
            for key, value in selector.items():
                assert template_labels.get(key) == value, (
                    f"Deployment {deploy['metadata']['name']}: selector {key}={value} does not match template labels"
                )

    def test_all_manifests_have_api_version(self, manifests):
        for doc in manifests:
            assert "apiVersion" in doc, f"Document missing apiVersion: {doc.get('kind', '?')}"

    def test_all_manifests_have_kind(self, manifests):
        for doc in manifests:
            assert "kind" in doc, f"Document missing kind in {doc.get('_source_file', '?')}"

    def test_all_manifests_have_metadata(self, manifests):
        for doc in manifests:
            assert "metadata" in doc, f"{doc.get('kind', '?')} missing metadata"
            assert "name" in doc["metadata"], f"{doc.get('kind', '?')} missing metadata.name"
