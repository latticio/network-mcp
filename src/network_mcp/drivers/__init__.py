"""Multi-vendor driver framework.

Exports:
    NetworkDriver: Protocol defining the vendor-neutral driver interface.
    EosDriver: Arista EOS implementation of NetworkDriver.
    DeviceDriver: Backward-compatible alias for NetworkDriver.
    DRIVER_REGISTRY: Mapping of platform strings to driver classes.
"""

import logging

from network_mcp.driver import EosDriver
from network_mcp.drivers.base import NetworkDriver, NotSupportedError

logger = logging.getLogger("network-mcp")

# Backward-compatible alias — existing code using DeviceDriver still works
DeviceDriver = NetworkDriver

# Driver registry — maps platform string to driver class.
# ConnectionManager uses this to instantiate the correct driver based on
# the device's `platform` field in inventory.
DRIVER_REGISTRY: dict[str, type[NetworkDriver]] = {
    "eos": EosDriver,  # type: ignore[dict-item]
}

# Conditionally register Cisco drivers when httpx is available (cisco extra)
try:
    from network_mcp.drivers.cisco_iosxe import IosXeDriver

    DRIVER_REGISTRY["iosxe"] = IosXeDriver  # type: ignore[assignment]
except ImportError:
    logger.debug("IOS-XE driver not available (install 'cisco' extra for httpx support)")

try:
    from network_mcp.drivers.cisco_nxos import NxosDriver

    DRIVER_REGISTRY["nxos"] = NxosDriver  # type: ignore[assignment]
except ImportError:
    logger.debug("NX-OS driver not available (install 'cisco' extra for httpx support)")

# Conditionally register JunOS driver when scrapli-netconf is available (juniper extra)
try:
    from network_mcp.drivers.juniper_junos import JunosDriver

    DRIVER_REGISTRY["junos"] = JunosDriver  # type: ignore[assignment]
except ImportError:
    logger.debug("JunOS driver not available (install 'juniper' extra for scrapli-netconf support)")

# Conditionally register SONiC driver when httpx is available (sonic extra)
try:
    from network_mcp.drivers.sonic import SonicDriver

    DRIVER_REGISTRY["sonic"] = SonicDriver  # type: ignore[assignment]
except ImportError:
    logger.debug("SONiC driver not available (install 'sonic' extra for httpx support)")

# Conditionally register PAN-OS driver when httpx is available (cisco or sonic extra)
try:
    from network_mcp.drivers.palo_alto_panos import PanosDriver

    DRIVER_REGISTRY["panos"] = PanosDriver  # type: ignore[assignment]
except ImportError:
    logger.debug("PAN-OS driver not available (install 'cisco' extra for httpx and defusedxml support)")

# Conditionally register FortiOS driver when httpx is available (cisco or sonic extra)
try:
    from network_mcp.drivers.fortinet_fortios import FortiosDriver

    DRIVER_REGISTRY["fortios"] = FortiosDriver  # type: ignore[assignment]
except ImportError:
    logger.debug("FortiOS driver not available (install 'cisco' extra for httpx support)")

__all__ = [
    "DRIVER_REGISTRY",
    "DeviceDriver",
    "EosDriver",
    "NetworkDriver",
    "NotSupportedError",
]
