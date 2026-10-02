"""Switch model with a VLAN database (doc §15)."""

from __future__ import annotations

from pydantic import Field, model_validator

from netlab.core.enums import DeviceType
from netlab.core.errors import DuplicateVlanError, UnknownVlanError
from netlab.devices.base import Device
from netlab.devices.interface import validate_vlan_id
from netlab.devices.vlan import VLAN


class Switch(Device):
    """A Layer-2 switch. Every switch starts with VLAN 1 (name ``default``)."""

    device_type: DeviceType = DeviceType.SWITCH
    vlans: dict[int, VLAN] = Field(default_factory=lambda: {1: VLAN(id=1, name="default")})

    @model_validator(mode="after")
    def _validate_vlans(self) -> Switch:
        for vlan_id, vlan in self.vlans.items():
            if vlan_id != vlan.id:
                raise ValueError(f"VLAN {vlan.id} stored under mismatched key {vlan_id}")
        if 1 not in self.vlans:
            raise ValueError("a switch must always contain VLAN 1")
        return self

    def create_vlan(self, vlan_id: int, name: str | None = None) -> VLAN:
        """Create a VLAN; raises :class:`DuplicateVlanError` if it already exists."""
        validate_vlan_id(vlan_id)
        if vlan_id in self.vlans:
            raise DuplicateVlanError(f"{self.hostname}: VLAN {vlan_id} already exists")
        vlan = VLAN(id=vlan_id, name=name or "")
        self.vlans[vlan_id] = vlan
        return vlan

    def get_vlan(self, vlan_id: int) -> VLAN:
        """Return the VLAN; raises :class:`UnknownVlanError` if it does not exist."""
        validate_vlan_id(vlan_id)
        vlan = self.vlans.get(vlan_id)
        if vlan is None:
            raise UnknownVlanError(f"{self.hostname}: VLAN {vlan_id} does not exist")
        return vlan

    def set_vlan_name(self, vlan_id: int, name: str) -> VLAN:
        """Rename an existing VLAN."""
        vlan = self.get_vlan(vlan_id)
        vlan.name = name
        return vlan
