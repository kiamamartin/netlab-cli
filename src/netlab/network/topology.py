"""Topology model with referential validation and derived operational state."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netlab.core.enums import LinkStatus
from netlab.core.errors import (
    DuplicateEndpointError,
    UnknownDeviceError,
    UnknownInterfaceError,
)
from netlab.devices.base import Device
from netlab.devices.interface import canonical_interface_name
from netlab.network.link import Endpoint, Link


class Topology(BaseModel):
    """Devices and the links between them, validated for referential integrity.

    Validation is fail-early: every link endpoint must reference an existing
    device interface, and each interface may belong to at most one link.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    devices: dict[str, Device]
    links: list[Link] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> Topology:
        for key, device in self.devices.items():
            if key != device.hostname:
                raise ValueError(f"device {device.hostname!r} stored under mismatched key {key!r}")
        used: set[tuple[str, str]] = set()
        for link in self.links:
            for endpoint in (link.source, link.destination):
                device = self.devices.get(endpoint.device)
                if device is None:
                    raise UnknownDeviceError(
                        f"link references unknown device {endpoint.device!r}"
                    )
                if endpoint.interface not in device.interfaces:
                    raise UnknownInterfaceError(
                        f"{endpoint.device}: link references unknown "
                        f"interface {endpoint.interface}"
                    )
                ref = (endpoint.device, endpoint.interface)
                if ref in used:
                    raise DuplicateEndpointError(
                        f"{endpoint.ref} is used by more than one link"
                    )
                used.add(ref)
        return self

    def device(self, hostname: str) -> Device:
        """Return the device; raises :class:`UnknownDeviceError` otherwise."""
        device = self.devices.get(hostname)
        if device is None:
            raise UnknownDeviceError(f"unknown device {hostname!r}")
        return device

    def link_for(self, hostname: str, interface: str) -> Link | None:
        """Return the link attached to the interface, or ``None`` if unconnected."""
        canonical = canonical_interface_name(interface)
        for link in self.links:
            for endpoint in (link.source, link.destination):
                if endpoint.device == hostname and endpoint.interface == canonical:
                    return link
        return None

    def connected_endpoint(self, hostname: str, interface: str) -> Endpoint | None:
        """Return the far end of the attached link, or ``None`` if unconnected."""
        link = self.link_for(hostname, interface)
        if link is None:
            return None
        canonical = canonical_interface_name(interface)
        if link.source.device == hostname and link.source.interface == canonical:
            return link.destination
        return link.source

    def interface_is_up(self, hostname: str, interface: str) -> bool:
        """Derived operational state: admin up AND connected AND link up.

        Subinterfaces follow their physical parent. This is never stored,
        preventing divergence between administrative and operational state.
        """
        iface = self.device(hostname).get_interface(interface)
        if not iface.admin_enabled:
            return False
        if iface.is_subinterface:
            return self.interface_is_up(hostname, iface.parent or "")
        link = self.link_for(hostname, iface.name)
        return link is not None and link.status == LinkStatus.UP
