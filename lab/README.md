# Integration Testing with Containerlab

Real-device testing for network-mcp using containerlab + Arista cEOS-lab containers on macOS (Apple Silicon).

---

## Phase 1: Setup

### Step 1 — Install Docker runtime

OrbStack is recommended over Docker Desktop on Apple Silicon (faster, lighter):

```bash
brew install orbstack
```

Open OrbStack from Applications and let it finish its first-run setup. Verify:

```bash
docker info | head -5
```

### Step 2 — Install containerlab

```bash
brew install containerlab
```

Verify:

```bash
containerlab version
```

### Step 3 — Get the cEOS-lab image

1. **Register** (free) at https://www.arista.com/en/login
2. Go to **Software Downloads > cEOS-lab**
3. Download the **aarch64** (ARM64) `.tar.xz` file — you need the ARM build for Apple Silicon
   - Look for something like `cEOS64-lab-4.33.2F.tar.xz`
4. Import it:

```bash
# Decompress if .xz
unxz cEOS64-lab-4.33.2F.tar.xz

# Import into Docker
docker import cEOS64-lab-4.33.2F.tar ceos:4.33.2F

# Tag as latest (our topologies reference ceos:latest)
docker tag ceos:4.33.2F ceos:latest
```

Verify:

```bash
docker images | grep ceos
```

### Step 4 — Deploy the lab

```bash
cd /path/to/network-mcp

# Deploy the minimal 2-node lab (~3 GB RAM)
./lab/setup.sh

# OR deploy the 6-node spine-leaf fabric (~10 GB RAM)
./lab/setup.sh spine-leaf
```

The setup script will:
- Check all prerequisites
- Deploy the containerlab topology
- Wait for all nodes to become eAPI-reachable
- Generate `lab/devices.yaml` inventory

You should see output like:
```
[OK]    Docker is running
[OK]    containerlab installed
[OK]    cEOS image(s) found
[INFO]  Deploying Lab...
[OK]    leaf1 eAPI reachable (HTTPS, 45s)
[OK]    leaf2 eAPI reachable (HTTPS, 45s)
[OK]    Lab deployed and all nodes responding to eAPI
```

### Step 5 — Verify manually

```bash
# Check lab status
containerlab inspect --all

# Quick eAPI test from your terminal
curl -sk https://172.20.20.2/command-api \
  -u admin:admin \
  -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show version"],"format":"json"},"id":1}' \
  -H 'Content-Type: application/json' | python3 -m json.tool
```

---

## Phase 2: Manual Testing

### Option A — Run network-mcp against the lab

```bash
NET_INVENTORY_FILE=lab/devices.yaml \
NET_READ_ONLY=false \
NET_DEMO_MODE=false \
NET_VERIFY_SSL=false \
NET_USERNAME=admin \
NET_PASSWORD=admin \
uv run network-mcp
```

Then from Claude Desktop / Cursor / any MCP client, you can call real tools:
- `net_get_device_info(host="leaf1")`
- `net_get_interfaces(host="leaf1")`
- `net_get_vlans(host="leaf1")`
- `net_containerlab_discover()`

### Option B — Use the containerlab integration tools

The MCP server has built-in containerlab support. From the MCP client:

1. `net_containerlab_discover()` — find running labs
2. `net_containerlab_inventory(lab_name="netmcp-2node")` — auto-import all nodes
3. Now all tools work with the imported node names

### Option C — Direct pyeapi testing

```python
import pyeapi

node = pyeapi.connect(
    host="172.20.20.2",  # Use actual IP from containerlab inspect
    transport="https",
    username="admin",
    password="admin",
    return_node=True,
)
result = node.run_commands(["show version"], encoding="json")
print(result[0]["version"])
```

---

## Phase 3: Automated Testing

### Run smoke tests first

```bash
uv run pytest tests/integration/test_smoke.py -v --run-integration -n0
```

### Run all integration tests

```bash
uv run pytest tests/integration/ -v --run-integration -n0
```

### Run the full overnight suite

The test runner script executes all phases in dependency order, generates JUnit XML reports, and supports soak testing:

```bash
# Single run with full reporting
./lab/run-tests.sh

# Run 6 times with 10-minute gaps (overnight soak)
./lab/run-tests.sh --loop 6 --interval 600

# Smoke tests only
./lab/run-tests.sh smoke

# Specific phase
./lab/run-tests.sh show      # Show command parsing
./lab/run-tests.sh tools     # MCP tool functions
./lab/run-tests.sh write     # Write operations (VLAN CRUD etc.)
./lab/run-tests.sh pool      # Connection pool & concurrency
./lab/run-tests.sh clab      # Containerlab integration
```

Reports are saved to `lab/reports/`:
- `lab/reports/summary-YYYYMMDD-HHMMSS.txt` — human-readable summary
- `lab/reports/*-YYYYMMDD-HHMMSS.xml` — JUnit XML (importable by CI)
- `lab/reports/run-YYYYMMDD-HHMMSS.log` — full test output

### Test categories

| File | Tests | What it validates |
|------|-------|-------------------|
| `test_smoke.py` | ~10 | Basic connectivity, eAPI, driver, normalized getters |
| `test_show_commands.py` | ~15 | Every major `show` command parses correctly |
| `test_tools_live.py` | ~12 | MCP tools (device, interfaces, VLANs, routing, switching, config) |
| `test_connection_pool.py` | ~8 | Connect/disconnect, concurrency, latency benchmarks |
| `test_containerlab_integration.py` | ~7 | Containerlab discover, inventory backend, platform detection |
| `test_write_operations.py` | ~4 | VLAN CRUD, interface config, config sessions |

---

## Teardown

```bash
# Destroy all netmcp labs
./lab/setup.sh destroy

# Or manually
sudo containerlab destroy --name netmcp-2node --cleanup
```

---

## Topology Reference

### 2-node (default) — `lab/topologies/2-node.clab.yaml`
- 2 cEOS leaves connected via eth1
- ~3 GB RAM
- Good for: smoke tests, tool validation, quick iteration

### Spine-leaf — `lab/topologies/spine-leaf.clab.yaml`
- 2 spines + 4 leaves, full mesh uplinks + MLAG peer-links
- ~10 GB RAM
- Good for: LLDP, multi-device fabric tests, BGP/MLAG validation

---

## Troubleshooting

### "containerlab CLI not found"
```bash
brew install containerlab
```

### "cEOS image not found"
Download from arista.com (free registration), import with `docker import`.

### eAPI not reachable after deploy
cEOS nodes can take 60-120s to fully boot. The setup script waits up to 180s.
Check manually: `docker logs clab-netmcp-2node-leaf1`

### SSL certificate errors
cEOS uses self-signed certs. The tests and setup script use `NET_VERIFY_SSL=false`.

### "No running lab nodes found"
```bash
containerlab inspect --all
# If empty, deploy first:
./lab/setup.sh
```

### Permission denied on containerlab deploy
Containerlab requires `sudo` for network namespace operations:
```bash
sudo containerlab deploy -t lab/topologies/2-node.clab.yaml
```
The setup script handles this automatically.

---

## Fortinet Switch (Future)

You mentioned having a Fortinet 8-port switch. To add it:

1. Add it to `lab/devices.yaml`:
```yaml
  fortinet-sw:
    host: <switch-management-ip>
    platform: fortios
    username: admin
    password: <password>
    transport: https
    port: 443
    tags: [physical, fortinet]
```

2. Run integration tests targeting it:
```bash
uv run pytest tests/integration/ -v --run-integration -n0 -k "not write"
```

The FortiOS driver is already registered in `DRIVER_REGISTRY` — it will work with the same test infrastructure once the switch is reachable over the network.
