"""End-host models (doc §13)."""

from ipaddress import IPv4Address

from netlab.core.enums import DeviceType
from netlab.devices.base import Device


class Host(Device):
    """An end host with host-level IPv4 settings.

    The host command surface (how a student addresses a PC) is decided at the
    CLI milestone; the state model only stores the resulting values.
    """

    default_gateway: IPv4Address | None = None


class PC(Host):
    device_type: DeviceType = DeviceType.PC


class Server(Host):
    device_type: DeviceType = DeviceType.SERVER
