"""The authoritative network state aggregate (doc §16)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from netlab.devices.base import Device
from netlab.devices.interface import Interface
from netlab.network.link import Endpoint, Link
from netlab.network.topology import Topology


class NetworkState(BaseModel):
    """Single authoritative representation of the simulated network.

    Everything that reads or affects the network goes through this object:
    IOS command handlers mutate it, show renderers read it, validators grade
    against it, and (in a later phase) the AI instructor receives findings
    derived from it. Dynamic state beyond the topology — routes, ARP, MAC
    tables, DHCP — will be added here as those phases land.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    topology: Topology

    def device(self, hostname: str) -> Device:
        return self.topology.device(hostname)

    def get_interface(self, hostname: str, interface: str) -> Interface:
        return self.device(hostname).get_interface(interface)

    def link_for(self, hostname: str, interface: str) -> Link | None:
        return self.topology.link_for(hostname, interface)

    def connected_endpoint(self, hostname: str, interface: str) -> Endpoint | None:
        return self.topology.connected_endpoint(hostname, interface)

    def interface_is_up(self, hostname: str, interface: str) -> bool:
        return self.topology.interface_is_up(hostname, interface)
