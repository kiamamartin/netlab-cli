"""Device models: switches, routers, and end hosts."""

from netlab.devices.base import Config, Device
from netlab.devices.host import PC, Host, Server
from netlab.devices.interface import Interface
from netlab.devices.router import Router
from netlab.devices.switch import Switch
from netlab.devices.vlan import VLAN

__all__ = [
    "Config",
    "Device",
    "Host",
    "Interface",
    "PC",
    "Router",
    "Server",
    "Switch",
    "VLAN",
]
