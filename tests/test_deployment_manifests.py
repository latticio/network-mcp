"""Validate deployment manifest YAML files are syntactically correct."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> list:
    """Load all YAML documents from a file, return list of parsed docs."""
    with open(path) as f:
        return list(yaml.safe_load_all(f))


class TestDockerCompose:
    def test_docker_compose_valid(self):
        docs = _load_yaml(ROOT / "docker-compose.yml")
        assert len(docs) == 1
        compose = docs[0]
        assert "services" in compose
        assert "network-mcp" in compose["services"]

    def test_docker_compose_services(self):
        compose = _load_yaml(ROOT / "docker-compose.yml")[0]
        mcp = compose["services"]["network-mcp"]
        assert mcp["command"] == ["--transport", "streamable-http"]
        assert "healthcheck" in mcp
        assert "8000:8000" in mcp["ports"]

    def test_docker_compose_monitoring_profiles(self):
        compose = _load_yaml(ROOT / "docker-compose.yml")[0]
        assert compose["services"]["prometheus"]["profiles"] == ["monitoring"]
        assert compose["services"]["grafana"]["profiles"] == ["monitoring"]


class TestPrometheusConfig:
    def test_prometheus_yml_valid(self):
        docs = _load_yaml(ROOT / "deploy" / "prometheus.yml")
        assert len(docs) == 1
        cfg = docs[0]
        assert "scrape_configs" in cfg
        assert cfg["scrape_configs"][0]["job_name"] == "network-mcp"


class TestGrafanaConfigs:
    def test_grafana_datasource_valid(self):
        docs = _load_yaml(ROOT / "deploy" / "grafana-datasource.yml")
        assert len(docs) == 1
        cfg = docs[0]
        assert "datasources" in cfg
        assert cfg["datasources"][0]["type"] == "prometheus"

    def test_grafana_dashboard_provider_valid(self):
        docs = _load_yaml(ROOT / "deploy" / "grafana-dashboard.yml")
        assert len(docs) == 1
        cfg = docs[0]
        assert "providers" in cfg


class TestKubernetesManifests:
    def test_deployment_valid(self):
        docs = _load_yaml(ROOT / "k8s" / "deployment.yaml")
        assert len(docs) == 1
        dep = docs[0]
        assert dep["kind"] == "Deployment"
        container = dep["spec"]["template"]["spec"]["containers"][0]
        assert container["resources"]["requests"]["memory"] == "256Mi"
        assert container["resources"]["limits"]["memory"] == "512Mi"

    def test_deployment_replicas_ha(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        assert dep["spec"]["replicas"] >= 2

    def test_deployment_security_context(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        sc = dep["spec"]["template"]["spec"]["securityContext"]
        assert sc["runAsUser"] == 1000
        assert sc["runAsNonRoot"] is True
        assert sc["fsGroup"] == 1000

    def test_deployment_seccomp_profile(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        sc = dep["spec"]["template"]["spec"]["securityContext"]
        assert sc["seccompProfile"]["type"] == "RuntimeDefault"

    def test_deployment_probes(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        container = dep["spec"]["template"]["spec"]["containers"][0]
        assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
        assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
        assert container["livenessProbe"]["periodSeconds"] == 10
        assert container["livenessProbe"]["failureThreshold"] == 3
        assert container["readinessProbe"]["periodSeconds"] == 5
        assert container["readinessProbe"]["failureThreshold"] == 3
        assert container["readinessProbe"]["initialDelaySeconds"] == 10

    def test_deployment_startup_probe(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        container = dep["spec"]["template"]["spec"]["containers"][0]
        assert "startupProbe" in container
        startup = container["startupProbe"]
        assert startup["httpGet"]["path"] == "/health/ready"
        assert startup["httpGet"]["port"] == 8000
        assert startup["failureThreshold"] == 30
        assert startup["periodSeconds"] == 5

    def test_deployment_anti_affinity(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        spec = dep["spec"]["template"]["spec"]
        assert "affinity" in spec
        anti = spec["affinity"]["podAntiAffinity"]
        preferred = anti["preferredDuringSchedulingIgnoredDuringExecution"]
        assert len(preferred) >= 1
        term = preferred[0]
        assert term["weight"] == 100
        assert term["podAffinityTerm"]["topologyKey"] == "kubernetes.io/hostname"
        expressions = term["podAffinityTerm"]["labelSelector"]["matchExpressions"]
        assert any(e["key"] == "app" and "network-mcp" in e["values"] for e in expressions)

    def test_deployment_termination_grace_period(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        spec = dep["spec"]["template"]["spec"]
        assert spec["terminationGracePeriodSeconds"] == 60

    def test_deployment_namespace(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        assert dep["metadata"]["namespace"] == "network-mcp"

    def test_deployment_env_from(self):
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        container = dep["spec"]["template"]["spec"]["containers"][0]
        env_from = container["envFrom"]
        config_refs = [e["configMapRef"]["name"] for e in env_from if "configMapRef" in e]
        secret_refs = [e["secretRef"]["name"] for e in env_from if "secretRef" in e]
        assert "network-mcp-config" in config_refs
        assert "network-mcp-secrets" in secret_refs

    def test_service_valid(self):
        docs = _load_yaml(ROOT / "k8s" / "service.yaml")
        assert len(docs) == 1
        svc = docs[0]
        assert svc["kind"] == "Service"
        assert svc["spec"]["type"] == "ClusterIP"
        assert svc["spec"]["ports"][0]["port"] == 8000

    def test_configmap_valid(self):
        docs = _load_yaml(ROOT / "k8s" / "configmap.yaml")
        assert len(docs) == 2
        cfg = docs[0]
        assert cfg["kind"] == "ConfigMap"
        assert cfg["data"]["NET_READ_ONLY"] == "true"
        devices_cm = docs[1]
        assert "devices.yaml" in devices_cm["data"]

    def test_configmap_uses_net_prefix(self):
        cfg = _load_yaml(ROOT / "k8s" / "configmap.yaml")[0]
        for key in cfg["data"]:
            assert not key.startswith("EOS_"), f"ConfigMap key {key} should use NET_* prefix, not EOS_*"

    def test_configmap_distributed_settings(self):
        cfg = _load_yaml(ROOT / "k8s" / "configmap.yaml")[0]
        assert "NET_DISTRIBUTED_BACKEND" in cfg["data"]
        assert "NET_REDIS_URL" in cfg["data"]

    def test_configmap_otel_settings(self):
        cfg = _load_yaml(ROOT / "k8s" / "configmap.yaml")[0]
        assert "NET_OTEL_ENABLED" in cfg["data"]
        assert "NET_OTEL_ENDPOINT" in cfg["data"]

    def test_configmap_change_mgmt_setting(self):
        cfg = _load_yaml(ROOT / "k8s" / "configmap.yaml")[0]
        assert "NET_CHANGE_MGMT_ENABLED" in cfg["data"]

    def test_secret_valid(self):
        docs = _load_yaml(ROOT / "k8s" / "secret.yaml")
        assert len(docs) == 1
        secret = docs[0]
        assert secret["kind"] == "Secret"
        assert "NET_PASSWORD" in secret["data"]

    def test_ingress_valid(self):
        docs = _load_yaml(ROOT / "k8s" / "ingress.yaml")
        assert len(docs) == 1
        ing = docs[0]
        assert ing["kind"] == "Ingress"
        assert ing["spec"]["tls"][0]["secretName"] == "network-mcp-tls"


class TestPodDisruptionBudget:
    def test_pdb_valid_yaml(self):
        docs = _load_yaml(ROOT / "k8s" / "pdb.yaml")
        assert len(docs) == 1

    def test_pdb_kind(self):
        pdb = _load_yaml(ROOT / "k8s" / "pdb.yaml")[0]
        assert pdb["kind"] == "PodDisruptionBudget"
        assert pdb["apiVersion"] == "policy/v1"

    def test_pdb_min_available(self):
        pdb = _load_yaml(ROOT / "k8s" / "pdb.yaml")[0]
        assert pdb["spec"]["minAvailable"] >= 1

    def test_pdb_selector_matches_deployment(self):
        pdb = _load_yaml(ROOT / "k8s" / "pdb.yaml")[0]
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        pdb_labels = pdb["spec"]["selector"]["matchLabels"]
        dep_labels = dep["spec"]["selector"]["matchLabels"]
        assert pdb_labels == dep_labels

    def test_pdb_namespace(self):
        pdb = _load_yaml(ROOT / "k8s" / "pdb.yaml")[0]
        assert pdb["metadata"]["namespace"] == "network-mcp"


class TestHorizontalPodAutoscaler:
    def test_hpa_valid_yaml(self):
        docs = _load_yaml(ROOT / "k8s" / "hpa.yaml")
        assert len(docs) == 1

    def test_hpa_kind(self):
        hpa = _load_yaml(ROOT / "k8s" / "hpa.yaml")[0]
        assert hpa["kind"] == "HorizontalPodAutoscaler"
        assert hpa["apiVersion"] == "autoscaling/v2"

    def test_hpa_min_replicas(self):
        hpa = _load_yaml(ROOT / "k8s" / "hpa.yaml")[0]
        assert hpa["spec"]["minReplicas"] >= 2

    def test_hpa_max_replicas_reasonable(self):
        hpa = _load_yaml(ROOT / "k8s" / "hpa.yaml")[0]
        assert hpa["spec"]["maxReplicas"] >= hpa["spec"]["minReplicas"]
        assert hpa["spec"]["maxReplicas"] <= 20

    def test_hpa_targets_deployment(self):
        hpa = _load_yaml(ROOT / "k8s" / "hpa.yaml")[0]
        ref = hpa["spec"]["scaleTargetRef"]
        assert ref["apiVersion"] == "apps/v1"
        assert ref["kind"] == "Deployment"
        assert ref["name"] == "network-mcp"

    def test_hpa_has_cpu_metric(self):
        hpa = _load_yaml(ROOT / "k8s" / "hpa.yaml")[0]
        metrics = hpa["spec"]["metrics"]
        cpu_metrics = [m for m in metrics if m["resource"]["name"] == "cpu"]
        assert len(cpu_metrics) == 1
        assert cpu_metrics[0]["resource"]["target"]["averageUtilization"] == 70

    def test_hpa_has_memory_metric(self):
        hpa = _load_yaml(ROOT / "k8s" / "hpa.yaml")[0]
        metrics = hpa["spec"]["metrics"]
        mem_metrics = [m for m in metrics if m["resource"]["name"] == "memory"]
        assert len(mem_metrics) == 1
        assert mem_metrics[0]["resource"]["target"]["averageUtilization"] == 80

    def test_hpa_namespace(self):
        hpa = _load_yaml(ROOT / "k8s" / "hpa.yaml")[0]
        assert hpa["metadata"]["namespace"] == "network-mcp"


class TestNetworkPolicy:
    def test_netpol_valid_yaml(self):
        docs = _load_yaml(ROOT / "k8s" / "networkpolicy.yaml")
        assert len(docs) == 1

    def test_netpol_kind(self):
        np = _load_yaml(ROOT / "k8s" / "networkpolicy.yaml")[0]
        assert np["kind"] == "NetworkPolicy"
        assert np["apiVersion"] == "networking.k8s.io/v1"

    def test_netpol_restricts_ingress(self):
        np = _load_yaml(ROOT / "k8s" / "networkpolicy.yaml")[0]
        assert "Ingress" in np["spec"]["policyTypes"]
        ingress = np["spec"]["ingress"]
        assert len(ingress) >= 1
        assert ingress[0]["ports"][0]["port"] == 8000

    def test_netpol_allows_egress_to_device_ports(self):
        np = _load_yaml(ROOT / "k8s" / "networkpolicy.yaml")[0]
        assert "Egress" in np["spec"]["policyTypes"]
        egress = np["spec"]["egress"]
        assert len(egress) >= 1
        egress_ports = [p["port"] for p in egress[0]["ports"]]
        assert 443 in egress_ports  # eAPI / RESTCONF
        assert 830 in egress_ports  # NETCONF
        assert 6030 in egress_ports  # gNMI

    def test_netpol_allows_redis_egress(self):
        np = _load_yaml(ROOT / "k8s" / "networkpolicy.yaml")[0]
        egress = np["spec"]["egress"]
        egress_ports = [p["port"] for p in egress[0]["ports"]]
        assert 6379 in egress_ports  # Redis

    def test_netpol_ingress_requires_namespace_label(self):
        np = _load_yaml(ROOT / "k8s" / "networkpolicy.yaml")[0]
        ingress_from = np["spec"]["ingress"][0]["from"]
        ns_selector = ingress_from[0]["namespaceSelector"]
        assert ns_selector["matchLabels"]["network-mcp-access"] == "true"

    def test_netpol_selector_matches_deployment(self):
        np = _load_yaml(ROOT / "k8s" / "networkpolicy.yaml")[0]
        dep = _load_yaml(ROOT / "k8s" / "deployment.yaml")[0]
        np_labels = np["spec"]["podSelector"]["matchLabels"]
        dep_labels = dep["spec"]["selector"]["matchLabels"]
        assert np_labels == dep_labels

    def test_netpol_namespace(self):
        np = _load_yaml(ROOT / "k8s" / "networkpolicy.yaml")[0]
        assert np["metadata"]["namespace"] == "network-mcp"


class TestDockerfile:
    def test_dockerfile_has_stopsignal(self):
        content = (ROOT / "Dockerfile").read_text()
        assert "STOPSIGNAL SIGTERM" in content

    def test_dockerfile_healthcheck_has_start_period(self):
        content = (ROOT / "Dockerfile").read_text()
        assert "--start-period=30s" in content

    def test_dockerfile_healthcheck_exists(self):
        content = (ROOT / "Dockerfile").read_text()
        assert "HEALTHCHECK" in content
        assert "localhost:8000/health" in content


class TestHelmChart:
    def test_chart_yaml_valid(self):
        docs = _load_yaml(ROOT / "deploy" / "helm" / "network-mcp" / "Chart.yaml")
        assert len(docs) == 1
        chart = docs[0]
        assert chart["name"] == "network-mcp"
        assert chart["apiVersion"] == "v2"
        assert chart["version"] == "1.0.0"
        assert chart["appVersion"] == "4.0.0"

    def test_values_yaml_valid(self):
        docs = _load_yaml(ROOT / "deploy" / "helm" / "network-mcp" / "values.yaml")
        assert len(docs) == 1
        values = docs[0]
        assert values["replicaCount"] == 1
        assert values["image"]["repository"] == "network-mcp"
        assert values["service"]["type"] == "ClusterIP"
        assert values["service"]["port"] == 8000
        assert values["config"]["readOnly"] == "true"
        assert values["resources"]["requests"]["memory"] == "256Mi"

    def test_all_templates_are_valid_yaml_or_gotmpl(self):
        """Verify template files exist and are non-empty."""
        templates_dir = ROOT / "deploy" / "helm" / "network-mcp" / "templates"
        expected = ["deployment.yaml", "service.yaml", "configmap.yaml", "secret.yaml", "ingress.yaml", "NOTES.txt"]
        for name in expected:
            path = templates_dir / name
            assert path.exists(), f"Missing template: {name}"
            assert path.stat().st_size > 0, f"Empty template: {name}"
