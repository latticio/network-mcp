# Compliance Checking Framework

network-mcp includes a multi-vendor compliance checking framework based on CIS (Center for Internet Security) benchmarks. The framework validates device configuration against security best practices and returns actionable pass/fail results.

## Supported Frameworks

| Framework | Platform | Checks | CIS Benchmark Reference |
|-----------|----------|--------|------------------------|
| `cis-eos` | Arista EOS | 15 | CIS Arista MLS EOS Benchmark |
| `cis-iosxe` | Cisco IOS-XE | 15 | CIS Cisco IOS Benchmark |
| `cis-nxos` | Cisco NX-OS | 15 | CIS Cisco NX-OS Benchmark |

## How Checks Work

Each compliance check follows a simple pattern:

1. **Command** — Run a `show running-config` command (with filter) against the device
2. **Evaluate** — Pass the text output to a check function that inspects the config
3. **Result** — Return PASS (compliant) or FAIL (non-compliant) with remediation guidance

All checks use text encoding (`show running-config | include ...` or `show running-config | section ...`) to inspect the device's running configuration.

## Usage

### Run compliance checks against a device

```python
# Run all EOS checks
eos_compliance_check(host="spine-01", framework="cis-eos")

# Run only management plane checks on an IOS-XE device
eos_compliance_check(host="router-01", framework="cis-iosxe", category="management")

# Run NX-OS data plane checks
eos_compliance_check(host="nexus-01", framework="cis-nxos", category="data")
```

### List available checks

```python
# List all frameworks and their checks
eos_list_compliance_checks()

# List checks for a specific framework
eos_list_compliance_checks(framework="cis-iosxe")
```

### Example output

```json
{
  "status": "success",
  "device": "spine-01",
  "data": {
    "framework": "cis-eos",
    "total_checks": 15,
    "passed": 12,
    "failed": 3,
    "pass_rate": "80.0%",
    "results": [
      {
        "id": "CIS-EOS-1.1.1",
        "title": "Ensure SSH server is enabled",
        "severity": "HIGH",
        "category": "management",
        "status": "PASS"
      },
      {
        "id": "CIS-EOS-2.3.1",
        "title": "Ensure SNMP community strings are not default",
        "severity": "HIGH",
        "category": "control",
        "status": "FAIL",
        "remediation": "Remove default communities: no snmp-server community public..."
      }
    ]
  }
}
```

## Check Categories

Each check belongs to one of three categories:

- **management** — Management plane controls (SSH, AAA, banners, password policies, API access)
- **control** — Control plane controls (NTP, logging, SNMP, enable secret, CoPP, VTY access)
- **data** — Data plane controls (unused interfaces, STP guards, DHCP snooping, ARP inspection)

## CIS EOS Checks (cis-eos)

| ID | Title | Severity | Category |
|----|-------|----------|----------|
| CIS-EOS-1.1.1 | Ensure SSH server is enabled | HIGH | management |
| CIS-EOS-1.1.2 | Ensure SSH idle timeout is configured | MEDIUM | management |
| CIS-EOS-1.2.1 | Ensure AAA authentication uses TACACS+ or RADIUS | HIGH | management |
| CIS-EOS-1.3.1 | Ensure password encryption is enabled | MEDIUM | management |
| CIS-EOS-1.3.2 | Ensure login banner is configured | LOW | management |
| CIS-EOS-1.4.1 | Ensure eAPI HTTPS is enabled and HTTP is disabled | HIGH | management |
| CIS-EOS-2.1.1 | Ensure NTP is configured with at least one server | HIGH | control |
| CIS-EOS-2.2.1 | Ensure logging to remote syslog server is configured | MEDIUM | control |
| CIS-EOS-2.3.1 | Ensure SNMP community strings are not default | HIGH | control |
| CIS-EOS-2.3.2 | Ensure enable secret is configured | HIGH | control |
| CIS-EOS-2.4.1 | Ensure console and VTY exec-timeout is configured | MEDIUM | control |
| CIS-EOS-3.1.1 | Ensure unused interfaces are shut down | MEDIUM | data |
| CIS-EOS-3.2.1 | Ensure STP BPDU guard or root guard is enabled | MEDIUM | data |
| CIS-EOS-3.3.1 | Ensure DHCP snooping or ARP inspection is enabled | MEDIUM | data |
| CIS-EOS-3.4.1 | Ensure Control Plane Policing (CoPP) is configured | HIGH | data |

## CIS IOS-XE Checks (cis-iosxe)

| ID | Title | Severity | Category |
|----|-------|----------|----------|
| CIS-IOSXE-1.1.1 | Ensure SSH version 2 is configured | HIGH | management |
| CIS-IOSXE-1.1.2 | Ensure SSH timeout is configured | MEDIUM | management |
| CIS-IOSXE-1.2.1 | Ensure AAA authentication is configured | HIGH | management |
| CIS-IOSXE-1.3.1 | Ensure service password-encryption is enabled | MEDIUM | management |
| CIS-IOSXE-1.3.2 | Ensure login banner is configured | LOW | management |
| CIS-IOSXE-1.4.1 | Ensure HTTP server is disabled and HTTPS is enabled | HIGH | management |
| CIS-IOSXE-2.1.1 | Ensure NTP is configured with authentication | HIGH | control |
| CIS-IOSXE-2.2.1 | Ensure logging to a remote syslog server is configured | MEDIUM | control |
| CIS-IOSXE-2.3.1 | Ensure SNMP community strings are not default | HIGH | control |
| CIS-IOSXE-2.3.2 | Ensure enable secret is configured | HIGH | control |
| CIS-IOSXE-2.4.1 | Ensure VTY transport input is restricted to SSH | HIGH | control |
| CIS-IOSXE-3.1.1 | Ensure unused interfaces are shut down | MEDIUM | data |
| CIS-IOSXE-3.2.1 | Ensure CDP is disabled globally | MEDIUM | data |
| CIS-IOSXE-3.3.1 | Ensure IP source routing is disabled | HIGH | data |
| CIS-IOSXE-3.4.1 | Ensure ICMP redirects are disabled on interfaces | MEDIUM | data |

## CIS NX-OS Checks (cis-nxos)

| ID | Title | Severity | Category |
|----|-------|----------|----------|
| CIS-NXOS-1.1.1 | Ensure SSH is enabled and configured | HIGH | management |
| CIS-NXOS-1.1.2 | Ensure SSH key exchange algorithm is hardened | MEDIUM | management |
| CIS-NXOS-1.2.1 | Ensure AAA authentication is configured | HIGH | management |
| CIS-NXOS-1.3.1 | Ensure password strength checking is enabled | MEDIUM | management |
| CIS-NXOS-1.3.2 | Ensure login banner is configured | LOW | management |
| CIS-NXOS-1.4.1 | Ensure HTTPS management only (no HTTP) | HIGH | management |
| CIS-NXOS-2.1.1 | Ensure NTP is configured and synchronized | HIGH | control |
| CIS-NXOS-2.2.1 | Ensure logging to a remote syslog server is configured | MEDIUM | control |
| CIS-NXOS-2.3.1 | Ensure SNMP community strings are not default | HIGH | control |
| CIS-NXOS-2.3.2 | Ensure Control Plane Policing (CoPP) is configured | HIGH | control |
| CIS-NXOS-2.4.1 | Ensure NX-API is secured or disabled if not needed | MEDIUM | control |
| CIS-NXOS-3.1.1 | Ensure unused interfaces are shut down | MEDIUM | data |
| CIS-NXOS-3.2.1 | Ensure DHCP snooping is enabled on access VLANs | MEDIUM | data |
| CIS-NXOS-3.3.1 | Ensure ARP inspection is enabled | MEDIUM | data |
| CIS-NXOS-3.4.1 | Ensure port security or 802.1X is configured | MEDIUM | data |

## Adding Custom Checks

To add a custom compliance check, define a check function and add a `ComplianceCheck` entry:

```python
from network_mcp.tools.compliance import ComplianceCheck, _FRAMEWORK_CHECKS

def _check_my_custom(output: str) -> bool:
    """Check for custom compliance requirement."""
    return "my-required-config" in output

# Add to an existing framework
_FRAMEWORK_CHECKS["cis-eos"].append(
    ComplianceCheck(
        id="CUSTOM-1.0.1",
        title="Ensure custom config is present",
        severity="MEDIUM",
        category="management",
        command="show running-config | include my-required-config",
        encoding="text",
        check_fn=_check_my_custom,
        remediation="Configure: my-required-config",
    )
)
```

For a plugin-based approach, see the [Plugin Development Guide](PLUGIN-DEVELOPMENT.md) for registering compliance packs as entry points.

## CIS Benchmark References

- [CIS Arista MLS EOS Benchmark](https://www.cisecurity.org/benchmark/arista)
- [CIS Cisco IOS Benchmark](https://www.cisecurity.org/benchmark/cisco)
- [CIS Cisco NX-OS Benchmark](https://www.cisecurity.org/benchmark/cisco)

> **Note**: CIS control IDs in this framework use a simplified numbering scheme
> (1.x Management, 2.x Control, 3.x Data). Verify against the official CIS
> documents for production compliance reporting.
