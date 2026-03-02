# Integration Guides

network-mcp is designed to work alongside other MCP servers. Because all MCP servers expose tools through the same protocol, AI agents can compose tools from multiple servers in a single conversation — combining network device data with IPAM records, monitoring alerts, or incident tickets without custom integration code.

## Available Guides

- [NetBox](netbox.md) — Drift detection, inventory reconciliation, and topology validation using NetBox as the source of truth and network-mcp for live device state
- [Grafana](grafana.md) — Alert-driven diagnostics using Grafana metrics and alerts to trigger network-mcp troubleshooting workflows
- [ServiceNow](servicenow.md) — Incident-driven workflows that pull device diagnostics into ServiceNow tickets and automate change validation
- Prometheus — Correlating network state with application metrics via the built-in Prometheus integration module
- Ansible — Inventory export and playbook integration for network automation workflows
- HashiCorp Vault — Secure credential management for production deployments
