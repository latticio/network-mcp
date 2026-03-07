"""Shared fixtures and mock device responses for testing."""

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from network_mcp.config import NetworkSettings
from network_mcp.helpers import circuit_breaker_registry, command_cache, device_rate_limiter
from network_mcp.observability import metrics_collector
from network_mcp.server import conn_mgr


class _NodeSpec:
    """Minimal spec for pyeapi Node-like objects used by EOS tools.

    Constrains MagicMock so that accessing undefined attributes raises
    AttributeError, catching accidental typos and wrong method calls.
    """

    _connection: Any = None

    @property
    def platform(self) -> str: ...  # type: ignore[override]

    def run_commands(self, commands: list[str], encoding: str = "json") -> list[dict]: ...

    def config(self, commands: list[str], **kwargs: Any) -> list[str]: ...

    def enable(self, commands: list[str], encoding: str = "json") -> list[dict]: ...

    def run_show(self, commands: list[str], encoding: str = "json") -> list[dict]: ...

    def run_config(self, commands: list[str], **kwargs: Any) -> list[str]: ...

    def is_connected(self) -> bool: ...

    def close(self) -> None: ...

    def connect(self, host: str, transport: str, username: str, password: str, **kwargs: Any) -> None: ...

    def capabilities(self) -> dict[str, bool]: ...

    def reconnect(self) -> None: ...


def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False, help="run integration tests")
    parser.addoption("--run-chaos", action="store_true", default=False, help="run chaos/concurrency/stress tests")
    parser.addoption("--run-slow", action="store_true", default=False, help="run slow tests (>1s each)")


def pytest_collection_modifyitems(config, items):
    skip_markers = []
    if not config.getoption("--run-integration", default=False):
        skip_markers.append(("integration", "need --run-integration to run"))
    if not config.getoption("--run-chaos", default=False):
        skip_markers.append(("chaos", "need --run-chaos to run"))
    if not config.getoption("--run-slow", default=False):
        skip_markers.append(("slow", "need --run-slow to run"))

    for marker_name, reason in skip_markers:
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker_name in item.keywords:
                item.add_marker(skip)


def make_test_settings(**overrides) -> NetworkSettings:
    """Create an NetworkSettings instance with test defaults. Pass overrides as kwargs."""
    defaults = {
        "net_username": "admin",
        "net_password": "",
        "net_transport": "https",
        "timeout_connect": 10.0,
    }
    defaults.update(overrides)
    return NetworkSettings(**defaults)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset rate limiter buckets and restore module reference after each test.

    Some tests patch ``network_mcp.helpers.device_rate_limiter`` with a mock.
    If the patch leaks, ``run_show_command``/``run_config_command`` will use the
    mock instead of the real rate limiter.  Restoring the module attribute at
    teardown prevents cross-test contamination.
    """
    import network_mcp.helpers as helpers_mod

    device_rate_limiter._show_buckets.clear()
    device_rate_limiter._config_buckets.clear()
    yield
    helpers_mod.device_rate_limiter = device_rate_limiter
    device_rate_limiter._show_buckets.clear()
    device_rate_limiter._config_buckets.clear()


@pytest.fixture(autouse=True)
def _reset_command_cache():
    """Reset command cache and restore module reference after each test."""
    import network_mcp.helpers as helpers_mod

    command_cache.clear()
    yield
    helpers_mod.command_cache = command_cache
    command_cache.clear()


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """Reset circuit breaker registry and restore module reference after each test."""
    import network_mcp.helpers as helpers_mod

    circuit_breaker_registry.reset()
    yield
    helpers_mod.circuit_breaker_registry = circuit_breaker_registry
    circuit_breaker_registry.reset()


@pytest.fixture(autouse=True)
def _reset_metrics_collector():
    """Reset metrics collector before each test to prevent cross-test contamination."""
    metrics_collector.reset()


@pytest.fixture(autouse=True)
def _reset_bulkhead():
    """Reset bulkhead manager before each test."""
    import network_mcp.connection as conn_mod

    old = conn_mod._bulkhead
    conn_mod._bulkhead = None
    yield
    conn_mod._bulkhead = old


@pytest.fixture(autouse=True)
def _reset_plugin_security_state():
    """Reset plugin hash allowlist and sandboxes before each test."""
    from network_mcp.plugins import _reset_plugin_sandboxes, set_plugin_hash_allowlist

    set_plugin_hash_allowlist({})
    _reset_plugin_sandboxes()


@pytest.fixture(autouse=True)
def _reset_helpers_state():
    """Reset helpers module state after each test to prevent cross-test leakage.

    Guards against four contamination vectors:
    1. ``helpers._settings`` replaced with a different ``NetworkSettings`` object
    2. ``settings.net_read_only`` left as ``False`` by write-mode tests
    3. ``helpers._get_settings`` replaced by ``unittest.mock.patch`` that was not
       properly cleaned up (the primary cause of full-suite write-test failures)
    4. ``helpers._change_manager`` cached from a test that enabled change management
    """
    import network_mcp.helpers as helpers_mod
    from network_mcp.server import settings

    original_get_settings = helpers_mod._get_settings
    original_change_mgmt = settings.net_change_mgmt_enabled
    original_require_approval = getattr(settings, "net_change_require_approval", False)
    yield
    # Always force helpers._settings back to the canonical server.settings object
    helpers_mod._settings = settings
    # Always restore net_read_only to its production default (True)
    settings.net_read_only = True
    # Always restore change management to disabled (default) and clear singleton
    settings.net_change_mgmt_enabled = False
    if hasattr(settings, "net_change_require_approval"):
        settings.net_change_require_approval = False
    helpers_mod._change_manager = None
    # Always restore _get_settings to the real function (not a mock)
    helpers_mod._get_settings = original_get_settings


@pytest.fixture
def mock_node():
    """Create a spec-constrained mock that satisfies the DeviceDriver interface.

    Uses ``_NodeSpec`` so that accessing undefined attributes raises
    ``AttributeError``, catching accidental misuse.

    Wires run_show → run_commands and run_config → config so that
    both the new driver API (helpers.py) and legacy pyeapi API
    (tool modules calling get_node) share the same mock methods.
    """
    node = MagicMock(spec=_NodeSpec)
    # Alias driver methods to pyeapi-compatible methods so tests can
    # set up mock_node.run_commands.return_value and have helpers.py's
    # driver.run_show() call see the same return value.
    node.run_show = node.run_commands
    node.run_config = node.config
    node.platform = "eos"
    node.is_connected.return_value = True
    return node


@pytest.fixture
def mock_conn_mgr(mock_node):
    """Patch both get_driver and acquire on the real ConnectionManager instance.

    helpers.py uses conn_mgr.acquire() (context manager) while some tool
    modules may still use get_driver/get_node directly. We patch both so
    all code paths return the same mock_node.
    """

    @contextmanager
    def _mock_acquire(host):  # noqa: ARG001
        yield mock_node

    with (
        patch.object(conn_mgr, "get_driver", return_value=mock_node) as patched,
        patch.object(conn_mgr, "acquire", side_effect=_mock_acquire),
    ):
        yield patched


# --- Mock responses for common EOS show commands ---

MOCK_SHOW_VERSION = {
    "modelName": "vEOS-lab",
    "version": "4.32.1F",
    "serialNumber": "TEST123",
    "systemMacAddress": "00:1c:73:00:00:01",
    "hostname": "test-switch",
    "uptime": 86400,
    "memTotal": 2048000,
    "memFree": 1024000,
    "hardwareRevision": "1.00",
}

MOCK_INTERFACES_STATUS = {
    "interfaceStatuses": {
        "Ethernet1": {
            "linkStatus": "connected",
            "vlanInformation": {"interfaceMode": "routed"},
            "bandwidth": 1000000000,
            "interfaceType": "1000BASE-T",
            "description": "to-spine-01",
        },
        "Ethernet2": {
            "linkStatus": "notconnect",
            "vlanInformation": {"interfaceMode": "access", "vlanId": 100},
            "bandwidth": 1000000000,
            "interfaceType": "1000BASE-T",
            "description": "",
        },
    }
}

MOCK_INTERFACE_DETAIL = {
    "interfaces": {
        "Ethernet1": {
            "name": "Ethernet1",
            "lineProtocolStatus": "up",
            "interfaceStatus": "connected",
            "hardware": "Ethernet",
            "mtu": 9214,
            "bandwidth": 1000000000,
            "description": "to-spine-01",
            "interfaceCounters": {
                "inOctets": 123456789,
                "outOctets": 987654321,
                "inUcastPkts": 1000000,
                "outUcastPkts": 2000000,
            },
        }
    }
}

MOCK_VLANS = {
    "vlans": {
        "1": {"name": "default", "status": "active", "interfaces": {}},
        "100": {
            "name": "SERVERS",
            "status": "active",
            "interfaces": {"Ethernet1": {}, "Ethernet2": {}},
        },
    }
}

MOCK_BGP_SUMMARY = {
    "vrfs": {
        "default": {
            "routerId": "10.0.0.1",
            "asn": "65000",
            "peers": {
                "10.0.0.2": {
                    "peerState": "Established",
                    "prefixReceived": 150,
                    "upDownTime": 1234567,
                    "asn": "65001",
                },
                "10.0.0.3": {
                    "peerState": "Active",
                    "prefixReceived": 0,
                    "upDownTime": 100,
                    "asn": "65002",
                },
            },
        }
    }
}

MOCK_MLAG = {
    "domainId": "mlag-domain-01",
    "localInterface": "Vlan4094",
    "peerAddress": "169.254.1.1",
    "peerLink": "Port-Channel10",
    "state": "active",
    "negStatus": "connected",
    "mlagPorts": {
        "Active-full": 8,
        "Active-partial": 0,
        "Disabled": 0,
        "Inactive": 0,
    },
}

MOCK_ROUTE_SUMMARY = {
    "vrfs": {
        "default": {
            "totalRoutes": 250,
            "routes": {
                "connected": 10,
                "static": 2,
                "bgp": 200,
                "ospf": 38,
            },
        }
    }
}

MOCK_MLAG_DETAIL = {
    "localInterface": "Vlan4094",
    "peerAddress": "169.254.1.1",
}

MOCK_MLAG_CONFIG_SANITY = {
    "mlagActive": True,
    "response": "consistent",
    "detail": {},
}

MOCK_MLAG_INTERFACES = {
    "interfaces": {
        "Port-Channel10": {
            "localInterface": "Port-Channel10",
            "localLinkStatus": "up",
            "peerLinkStatus": "up",
            "mlagId": 10,
        }
    }
}

MOCK_TRUNK_INTERFACES = {
    "trunkGroups": {},
    "trunks": {
        "Ethernet1": {
            "allowedVlans": "1-4094",
            "nativeVlan": 1,
        }
    },
}

MOCK_SPANNING_TREE = {
    "spanningTreeInstances": {},
}

MOCK_MAC_ADDRESS_TABLE = {
    "unicastTable": {
        "tableEntries": [],
    }
}

MOCK_LLDP_NEIGHBORS = {
    "lldpNeighbors": [],
}

MOCK_LLDP_NEIGHBOR_DETAIL = {
    "lldpNeighbors": {},
}

MOCK_PORT_CHANNELS = {
    "portChannels": {},
}

MOCK_HARDWARE_CAPACITY = {
    "tables": [
        {
            "table": "IPv4 /32 hosts",
            "chip": "memory",
            "maxLimit": 131072,
            "highWatermark": 100,
            "used": 50,
            "free": 131022,
        }
    ]
}

MOCK_HARDWARE_DROPS = {
    "totalPacketProcessor": {
        "sobcDrop": 0,
        "sobcQueue": 0,
    }
}

MOCK_TERMINATTR = {
    "daemons": {
        "TerminAttr": {
            "running": True,
            "data": {},
        }
    }
}

MOCK_VRRP = {
    "virtualRouters": {},
}

MOCK_VARP = {
    "virtualMac": "00:1c:73:00:00:99",
    "interfaces": {},
}

# --- EVPN/VXLAN mock responses ---
# Structures derived from docs/research/dc_fabric_patterns.md section 5

MOCK_VXLAN_INTERFACE = {
    "interfaces": {
        "Vxlan1": {
            "name": "Vxlan1",
            "interfaceStatus": "connected",
            "lineProtocolStatus": "up",
            "srcIpIntf": "Loopback1",
            "srcIpAddr": "10.111.253.3",
            "udpPort": 4789,
            "replicationMode": "headendEvpn",
            "controllerClientMode": False,
            "vlanToVniMap": {
                "112": {"vni": 112, "source": "static"},
                "134": {"vni": 134, "source": "static"},
            },
            "vrfToVniMap": {
                "TENANT": {"vni": 5001},
            },
            "floodVtepList": ["10.111.253.1", "10.111.253.4"],
        }
    }
}

MOCK_VXLAN_VTEP = {
    "interfaces": {
        "Vxlan1": {
            "vteps": ["10.111.253.1", "10.111.253.4"],
        }
    }
}

MOCK_VXLAN_VNI = {
    "vxlanIntfs": {
        "Vxlan1": {
            "vniBindings": {
                "112": {"vni": 112, "vlan": 112, "source": "static", "dot1qTag": 0},
                "134": {"vni": 134, "vlan": 134, "source": "static", "dot1qTag": 0},
            },
            "vniBindingsToVrf": {
                "5001": {
                    "vni": 5001,
                    "vlan": 4093,
                    "vrfName": "TENANT",
                    "source": "evpn",
                },
            },
        }
    }
}

MOCK_VXLAN_ADDRESS_TABLE = {
    "addresses": {
        "112": [
            {
                "macAddress": "00:1c:73:c0:c6:17",
                "type": "DYNAMIC",
                "port": "Vxlan1",
                "vtepAddr": "10.111.253.1",
                "moves": 0,
                "lastMove": "",
            }
        ]
    }
}

MOCK_VXLAN_CONFIG_SANITY = {
    "categories": {
        "localVtep": {
            "allCheckPass": True,
            "detail": {
                "loopbackIpAddr": {"checkPass": True},
                "vlanVniMap": {"checkPass": True},
                "routing": {"checkPass": True},
                "vniVrfAcl": {"checkPass": True},
                "decapVrfVniMap": {"checkPass": True},
                "vrfVniDynamicVlan": {"checkPass": True},
            },
        },
        "mlag": {
            "allCheckPass": True,
            "detail": {
                "peerVtepIp": {"checkPass": True},
                "mlagVtepIp": {"checkPass": True},
                "peerVlanVni": {"checkPass": True},
                "virtualVtepIp": {"checkPass": True},
            },
        },
        "pd": {
            "allCheckPass": True,
            "detail": {
                "vxlanBridging": {"checkPass": True},
                "vxlanRouting": {"checkPass": True},
            },
        },
    }
}

MOCK_BGP_EVPN_SUMMARY = {
    "vrfs": {
        "default": {
            "routerId": "10.111.0.3",
            "asn": "65103",
            "peers": {
                "10.111.0.1": {
                    "peerState": "Established",
                    "prefixReceived": 42,
                    "prefixAccepted": 42,
                    "upDownTime": 1234567.0,
                    "asn": "65001",
                    "peerGroup": "EVPN-OVERLAY-PEERS",
                    "msgReceived": 5000,
                    "msgSent": 4500,
                },
                "10.111.0.2": {
                    "peerState": "Established",
                    "prefixReceived": 42,
                    "prefixAccepted": 42,
                    "upDownTime": 1234567.0,
                    "asn": "65001",
                    "peerGroup": "EVPN-OVERLAY-PEERS",
                    "msgReceived": 5000,
                    "msgSent": 4500,
                },
            },
        }
    }
}

MOCK_BGP_EVPN_INSTANCE = {
    "evpnInstances": {
        "VLAN 112": {
            "rd": "10.111.254.3:112",
            "importRts": ["112:112"],
            "exportRts": ["112:112"],
            "serviceInterface": "VLAN-based",
            "localIpAddr": "10.111.253.3",
            "encapType": "vxlan",
            "designatedForwarder": "10.111.253.3",
            "nonDesignatedForwarder": "10.111.253.4",
        }
    }
}

MOCK_EVPN_ROUTE_TYPE_MACIP = {
    "evpnRoutes": {
        "RD: 10.111.254.3:112 mac-ip 00:1c:73:c0:c6:17": {
            "routeKeyDetail": {
                "rd": "10.111.254.3:112",
                "domain": "local",
            },
            "evpnRoutePaths": [
                {
                    "nextHop": "10.111.253.3",
                    "routeType": {"active": True, "valid": True},
                    "routeDetail": {
                        "extCommunities": [
                            "Route-Target-AS:112:112",
                            "TunnelEncap:tunnelTypeVxlan",
                        ]
                    },
                    "weight": 0,
                }
            ],
        }
    }
}

# --- Security mock responses ---

MOCK_IP_ACCESS_LISTS = {
    "aclList": [
        {
            "name": "MGMT-ACCESS",
            "type": "Standard",
            "sequence": [{"sequenceNumber": 10, "action": "permit", "source": "10.0.0.0/8"}],
        }
    ]
}

MOCK_AAA = {
    "authentication": {},
    "authorization": {},
    "accounting": {},
}

MOCK_RADIUS = {"servers": {}}

MOCK_TACACS = {"servers": {}}

MOCK_COPP_POLICY = {"policyMaps": {}}

MOCK_EAPI_STATUS = {
    "enabled": True,
    "httpServer": {"running": True, "port": 80},
    "httpsServer": {"running": True, "port": 443},
    "connectedClients": [],
}

# --- VRF mock responses ---

MOCK_VRFS = {
    "vrfs": {
        "default": {"routeDistinguisher": "", "protocols": {"ipv4": True}},
        "PROD": {"routeDistinguisher": "10.0.0.1:1", "protocols": {"ipv4": True}},
    }
}

MOCK_VRF_DETAIL = {
    "vrfs": {
        "PROD": {
            "routeDistinguisher": "10.0.0.1:1",
            "interfaces": ["Vlan100", "Vlan200"],
            "protocols": {"ipv4": True, "ipv6": False},
        }
    }
}

# --- Workflow tool mock responses ---

MOCK_BGP_NEIGHBORS_DETAIL = {
    "vrfBgpNeighbors": {
        "default": {
            "peerList": [
                {
                    "peerAddress": "10.0.0.2",
                    "peerState": "Established",
                    "localAsn": "65000",
                    "remoteAsn": "65001",
                    "localInterface": "Ethernet1",
                    "prefixReceived": 150,
                    "prefixAccepted": 148,
                    "upDownTime": 1234567,
                    "establishedTransitions": 2,
                    "lastRead": "00:00:05",
                    "lastWrite": "00:00:10",
                    "msgReceived": 50000,
                    "msgSent": 49500,
                    "routeMapInbound": "IMPORT-MAP",
                    "routeMapOutbound": "EXPORT-MAP",
                }
            ]
        }
    }
}

MOCK_BGP_NEIGHBORS_DETAIL_IDLE = {
    "vrfBgpNeighbors": {
        "default": {
            "peerList": [
                {
                    "peerAddress": "10.0.0.3",
                    "peerState": "Idle",
                    "localAsn": "65000",
                    "remoteAsn": "65002",
                    "localInterface": "Ethernet2",
                    "prefixReceived": 0,
                    "prefixAccepted": 0,
                    "upDownTime": 100,
                    "establishedTransitions": 0,
                }
            ]
        }
    }
}

MOCK_IP_ROUTE_TO_PEER = {
    "vrfs": {
        "default": {
            "routes": {
                "10.0.0.2/32": {
                    "routeType": "connected",
                    "directlyConnected": True,
                    "interfaceName": "Ethernet1",
                }
            }
        }
    }
}

MOCK_IP_ROUTE_EMPTY = {"vrfs": {"default": {"routes": {}}}}

MOCK_INTERFACE_ERRORS = {
    "interfaceErrorCounters": {
        "Ethernet1": {
            "inputErrors": 500,
            "outputErrors": 10,
            "fcsErrors": 250,
            "frameTooLongs": 50,
            "alignmentErrors": 5,
            "runtFrames": 0,
            "rxPause": 0,
            "txPause": 0,
        }
    }
}

MOCK_INTERFACE_ERRORS_CLEAN = {
    "interfaceErrorCounters": {
        "Ethernet1": {
            "inputErrors": 0,
            "outputErrors": 0,
            "fcsErrors": 0,
            "frameTooLongs": 0,
            "alignmentErrors": 0,
            "runtFrames": 0,
            "rxPause": 0,
            "txPause": 0,
        }
    }
}

MOCK_TRANSCEIVER_INFO = {
    "interfaces": {
        "Ethernet1": {
            "rxPower": -5.2,
            "txPower": -2.1,
            "txBias": 6.5,
            "temperature": 35.0,
            "voltage": 3.3,
            "mediaType": "10GBASE-SR",
        }
    }
}

MOCK_TRANSCEIVER_LOW_POWER = {
    "interfaces": {
        "Ethernet1": {
            "rxPower": -28.5,
            "txPower": -2.1,
            "txBias": 6.5,
            "temperature": 35.0,
            "voltage": 3.3,
            "mediaType": "10GBASE-LR",
        }
    }
}

MOCK_INTERFACE_DETAIL_DOWN = {
    "interfaces": {
        "Ethernet1": {
            "name": "Ethernet1",
            "lineProtocolStatus": "down",
            "interfaceStatus": "connected",
            "hardware": "Ethernet",
            "mtu": 9214,
            "bandwidth": 1000000000,
            "description": "to-spine-01",
            "interfaceCounters": {
                "inOctets": 0,
                "outOctets": 0,
                "inUcastPkts": 0,
                "outUcastPkts": 0,
            },
        }
    }
}

MOCK_INTERFACE_DETAIL_ADMIN_DOWN = {
    "interfaces": {
        "Ethernet1": {
            "name": "Ethernet1",
            "lineProtocolStatus": "down",
            "interfaceStatus": "disabled",
            "hardware": "Ethernet",
            "mtu": 9214,
            "bandwidth": 1000000000,
            "description": "DISABLED",
            "interfaceCounters": {
                "inOctets": 0,
                "outOctets": 0,
                "inUcastPkts": 0,
                "outUcastPkts": 0,
            },
        }
    }
}

MOCK_INTERFACE_STATUS_SINGLE = {
    "interfaceStatuses": {
        "Ethernet1": {
            "linkStatus": "connected",
            "vlanInformation": {"interfaceMode": "routed"},
            "bandwidth": 1000000000,
            "interfaceType": "1000BASE-T",
            "description": "to-spine-01",
        }
    }
}

MOCK_RUNNING_CONFIG_BGP_HOST1 = {
    "output": (
        "router bgp 65000\n"
        "   router-id 10.0.0.1\n"
        "   neighbor 10.0.0.2 remote-as 65001\n"
        "   neighbor 10.0.0.2 maximum-routes 12000\n"
    )
}

MOCK_RUNNING_CONFIG_BGP_HOST2 = {
    "output": (
        "router bgp 65000\n"
        "   router-id 10.0.0.2\n"
        "   neighbor 10.0.0.1 remote-as 65001\n"
        "   neighbor 10.0.0.1 maximum-routes 10000\n"
        "   neighbor 10.0.0.3 remote-as 65002\n"
    )
}

MOCK_RUNNING_CONFIG_IDENTICAL = {
    "output": "router bgp 65000\n   router-id 10.0.0.1\n   neighbor 10.0.0.2 remote-as 65001\n"
}

MOCK_MLAG_ERROR_STATE = {
    "domainId": "mlag-domain-01",
    "localInterface": "Vlan4094",
    "peerAddress": "169.254.1.1",
    "peerLink": "Port-Channel10",
    "state": "active",
    "negStatus": "connected",
    "configSanity": "inconsistent",
    "mlagPorts": {
        "Active-full": 6,
        "Active-partial": 0,
        "Disabled": 0,
        "Inactive": 2,
    },
}

MOCK_MLAG_HEALTHY = {
    "domainId": "mlag-domain-01",
    "localInterface": "Vlan4094",
    "peerAddress": "169.254.1.1",
    "peerLink": "Port-Channel10",
    "state": "active",
    "negStatus": "connected",
    "configSanity": "consistent",
    "mlagPorts": {
        "Active-full": 8,
        "Active-partial": 0,
        "Disabled": 0,
        "Inactive": 0,
    },
}

MOCK_BGP_SUMMARY_HEALTHY = {
    "vrfs": {
        "default": {
            "routerId": "10.0.0.1",
            "asn": "65000",
            "peers": {
                "10.0.0.2": {
                    "peerState": "Established",
                    "prefixReceived": 150,
                    "upDownTime": 1234567,
                    "asn": "65001",
                },
                "10.0.0.3": {
                    "peerState": "Established",
                    "prefixReceived": 200,
                    "upDownTime": 1234567,
                    "asn": "65002",
                },
            },
        }
    }
}

MOCK_BGP_SUMMARY_WITH_DOWN_PEER = {
    "vrfs": {
        "default": {
            "routerId": "10.0.0.1",
            "asn": "65000",
            "peers": {
                "10.0.0.2": {
                    "peerState": "Established",
                    "prefixReceived": 150,
                    "upDownTime": 1234567,
                    "asn": "65001",
                },
                "10.0.0.3": {
                    "peerState": "Idle",
                    "prefixReceived": 0,
                    "upDownTime": 100,
                    "asn": "65002",
                },
            },
        }
    }
}

MOCK_EVPN_ROUTE_TYPE_IMET = {
    "evpnRoutes": {
        "RD: 10.111.254.3:112 imet 10.111.253.3": {
            "routeKeyDetail": {
                "rd": "10.111.254.3:112",
                "domain": "local",
            },
            "evpnRoutePaths": [
                {
                    "nextHop": "10.111.253.3",
                    "routeType": {"active": True, "valid": True},
                    "routeDetail": {
                        "extCommunities": [
                            "Route-Target-AS:112:112",
                            "TunnelEncap:tunnelTypeVxlan",
                        ]
                    },
                    "weight": 0,
                }
            ],
        }
    }
}

# --- Error scenario mock constants ---

MOCK_TIMEOUT_RESPONSE = {
    "error": "Command timed out after 30 seconds",
    "code": 408,
}

MOCK_MALFORMED_RESPONSE = {
    "vlans": {"100": {"name": "SERVERS"}},
    # Missing expected keys like "interfaces"
}

MOCK_EMPTY_RESPONSE = {}

MOCK_CONNECTION_ERROR_MSG = "Connection refused: [Errno 111] Connection refused"


# --- Error scenario fixtures ---


@pytest.fixture
def mock_node_timeout(mock_node):
    """Node that always times out."""
    mock_node.run_commands.side_effect = TimeoutError("Command timed out")
    return mock_node


@pytest.fixture
def mock_node_connection_error(mock_node):
    """Node that can't connect."""
    mock_node.run_commands.side_effect = ConnectionError(MOCK_CONNECTION_ERROR_MSG)
    return mock_node


@pytest.fixture
def mock_node_empty(mock_node):
    """Node that returns empty responses."""
    mock_node.run_commands.return_value = [MOCK_EMPTY_RESPONSE]
    return mock_node
