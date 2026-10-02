"""Device base model and configuration snapshots (doc §13, §36)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from netlab.core.enums import DeviceType
from netlab.core.errors import UnknownInterfaceError
from netlab.devices.interface import Interface, canonical_interface_name

HOSTNAME_MAX_LENGTH = 63
_HOSTNAME_RE = re.compile(r"^[A-Za-z]([A-Za-z0-9-]*[A-Za-z0-9])?$")


class Config(BaseModel):
    """Immutable snapshot of a device's configuration (the startup-config content).

    The network state is authoritative: ``show running-config`` is rendered from
    live state by the IOS module, while ``startup-config`` is one of these frozen
    snapshots, captured by ``copy running-config startup-config``. A future
    ``reload`` will restore device state from this snapshot.
    """

    model_config = ConfigDict(frozen=True)

    created_at: datetime
    snapshot: dict[str, Any]

    @classmethod
    def capture(cls, device: Device) -> Config:
        """Capture the device's current configuration state.

        ``startup_config`` itself is excluded so successive captures never nest
        snapshots inside snapshots.
        """
        return cls(
            created_at=datetime.now(UTC),
            snapshot=device.model_dump(mode="json", exclude={"startup_config"}),
        )


class Device(BaseModel):
    """Base class for all simulated devices.

    Interfaces are stored in a dict keyed by their canonical name; a model
    validator keeps keys and names consistent and requires subinterface parents
    to exist, whether the device was built incrementally or from a snapshot.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    device_type: DeviceType
    hostname: str
    interfaces: dict[str, Interface] = Field(default_factory=dict)
    startup_config: Config | None = None

    @field_validator("hostname")
    @classmethod
    def _valid_hostname(cls, value: str) -> str:
        hostname = value.strip()
        if not hostname or len(hostname) > HOSTNAME_MAX_LENGTH:
            raise ValueError(f"hostname must be 1-{HOSTNAME_MAX_LENGTH} characters")
        if _HOSTNAME_RE.match(hostname) is None:
            raise ValueError(
                "hostname must start with a letter and contain only letters, digits or hyphens"
            )
        return hostname

    @model_validator(mode="after")
    def _validate_interfaces(self) -> Device:
        for key, interface in self.interfaces.items():
            if key != interface.name:
                raise ValueError(
                    f"interface {interface.name!r} stored under mismatched key {key!r}"
                )
            if interface.parent is not None and interface.parent not in self.interfaces:
                raise ValueError(
                    "subinterface "
                    f"{interface.name!r} references missing parent {interface.parent!r}"
                )
        return self

    def get_interface(self, name: str) -> Interface:
        """Return the interface, accepting IOS abbreviations (``gi0/1``)."""
        canonical = canonical_interface_name(name)
        interface = self.interfaces.get(canonical)
        if interface is None:
            raise UnknownInterfaceError(f"{self.hostname}: unknown interface {canonical}")
        return interface

    def add_interface(self, interface: Interface) -> Interface:
        """Register an interface; rejects duplicates and missing subinterface parents."""
        if interface.name in self.interfaces:
            raise ValueError(f"{self.hostname}: interface {interface.name} already exists")
        if interface.parent is not None and interface.parent not in self.interfaces:
            raise ValueError(
                f"{self.hostname}: subinterface {interface.name} parent "
                f"{interface.parent} does not exist"
            )
        self.interfaces[interface.name] = interface
        return interface

    @property
    def interface_names(self) -> list[str]:
        """Canonical interface names in display order."""
        return sorted(self.interfaces)

    def copy_running_to_startup(self) -> Config:
        """Snapshot the running state into startup-config (``copy run start``)."""
        snapshot = Config.capture(self)
        self.startup_config = snapshot
        return snapshot
