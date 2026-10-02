"""Router model (doc §13)."""

from netlab.core.enums import DeviceType
from netlab.devices.base import Device


class Router(Device):
    """A Layer-3 router.

    Routing-table state (static routes, connected routes) arrives with the
    IPv4/routing phase; subinterfaces are modelled as ``Interface`` entries
    with a ``parent`` and an ``encapsulation_vlan``.
    """

    device_type: DeviceType = DeviceType.ROUTER
