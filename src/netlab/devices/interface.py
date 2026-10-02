"""Interface model with canonical IOS naming and VLAN list helpers (doc §14)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from ipaddress import IPv4Address

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from netlab.core.enums import SwitchportMode
from netlab.core.errors import (
    InvalidInterfaceError,
    InvalidVlanIdError,
    InvalidVlanListError,
)

MIN_VLAN = 1
MAX_VLAN = 4094
ALL_VLANS: frozenset[int] = frozenset(range(MIN_VLAN, MAX_VLAN + 1))

# Interface type followed by slot/port numbers and an optional subinterface id.
_INTERFACE_RE = re.compile(r"^(?P<prefix>[A-Za-z]+)\s*(?P<number>\d+(?:/\d+)*(?:\.\d+)?)$")

_INTERFACE_TYPE_ALIASES: dict[str, str] = {
    "e": "Ethernet",
    "eth": "Ethernet",
    "ethernet": "Ethernet",
    "f": "FastEthernet",
    "fa": "FastEthernet",
    "fastethernet": "FastEthernet",
    "g": "GigabitEthernet",
    "gi": "GigabitEthernet",
    "gig": "GigabitEthernet",
    "gigabitethernet": "GigabitEthernet",
    "t": "TenGigabitEthernet",
    "te": "TenGigabitEthernet",
    "tengigabitethernet": "TenGigabitEthernet",
    "s": "Serial",
    "se": "Serial",
    "serial": "Serial",
    "v": "Vlan",
    "vlan": "Vlan",
    "lo": "Loopback",
    "loopback": "Loopback",
}

_SHORT_NAME_PREFIXES: dict[str, str] = {
    "Ethernet": "Eth",
    "FastEthernet": "Fa",
    "GigabitEthernet": "Gi",
    "TenGigabitEthernet": "Te",
    "Serial": "Se",
    "Vlan": "Vlan",
    "Loopback": "Lo",
}


def canonical_interface_name(raw: str) -> str:
    """Normalize an IOS-style interface token to its canonical long form.

    Accepts IOS abbreviations such as ``gi0/1``, ``gig 0/2`` or
    ``GigabitEthernet0/0/0.10`` and returns the canonical name. Raises
    :class:`InvalidInterfaceError` for anything that does not parse.
    """
    match = _INTERFACE_RE.match(raw.strip())
    if match is None:
        raise InvalidInterfaceError(f"{raw!r} is not a valid interface type and number")
    canonical_type = _INTERFACE_TYPE_ALIASES.get(match.group("prefix").lower())
    if canonical_type is None:
        raise InvalidInterfaceError(f"{raw!r} is not a valid interface type and number")
    return f"{canonical_type}{match.group('number')}"


def short_interface_name(name: str) -> str:
    """Return the abbreviated display form (``GigabitEthernet0/1`` -> ``Gi0/1``)."""
    match = _INTERFACE_RE.match(name.strip())
    if match is None:
        raise InvalidInterfaceError(f"{name!r} is not a valid interface type and number")
    canonical_type = _INTERFACE_TYPE_ALIASES.get(match.group("prefix").lower())
    if canonical_type is None:
        raise InvalidInterfaceError(f"{name!r} is not a valid interface type and number")
    return f"{_SHORT_NAME_PREFIXES[canonical_type]}{match.group('number')}"


def validate_vlan_id(vlan_id: int) -> int:
    """Return the id if valid, otherwise raise :class:`InvalidVlanIdError`."""
    if not MIN_VLAN <= vlan_id <= MAX_VLAN:
        raise InvalidVlanIdError(
            f"VLAN id must be in range {MIN_VLAN}-{MAX_VLAN}, got {vlan_id}"
        )
    return vlan_id


def parse_vlan_list(raw: str) -> frozenset[int]:
    """Parse an IOS VLAN list such as ``10,20-24`` into a set of ids."""
    vlans: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            raise InvalidVlanListError(f"empty element in VLAN list {raw!r}")
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            if not start_text.isdigit() or not end_text.isdigit():
                raise InvalidVlanListError(f"invalid VLAN range {part!r} in {raw!r}")
            start, end = int(start_text), int(end_text)
            if start > end:
                raise InvalidVlanListError(f"descending VLAN range {part!r} in {raw!r}")
            for vlan_id in range(start, end + 1):
                validate_vlan_id(vlan_id)
            vlans.update(range(start, end + 1))
        else:
            if not part.isdigit():
                raise InvalidVlanListError(f"invalid VLAN {part!r} in {raw!r}")
            validate_vlan_id(int(part))
            vlans.add(int(part))
    return frozenset(vlans)


def format_vlan_list(vlans: Iterable[int]) -> str:
    """Render VLAN ids as an IOS-style compressed list (``1,10,20-24``)."""
    ordered = sorted(set(vlans))
    if not ordered:
        return ""
    parts: list[str] = []
    run_start = previous = ordered[0]
    for vlan_id in ordered[1:]:
        if vlan_id == previous + 1:
            previous = vlan_id
            continue
        parts.append(f"{run_start}-{previous}" if previous > run_start else str(run_start))
        run_start = previous = vlan_id
    parts.append(f"{run_start}-{previous}" if previous > run_start else str(run_start))
    return ",".join(parts)


class Interface(BaseModel):
    """A physical interface, subinterface, or SVI on a device.

    The stored ``name`` is always the canonical long form; abbreviations are
    canonicalized on construction and assignment. Operational (up/down) state
    is *derived* from the topology, never stored here.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    name: str
    description: str = ""
    admin_enabled: bool = True

    # Layer-2 switchport state (switches)
    switchport_mode: SwitchportMode = SwitchportMode.NONE
    access_vlan: int = Field(default=1, ge=MIN_VLAN, le=MAX_VLAN)
    native_vlan: int = Field(default=1, ge=MIN_VLAN, le=MAX_VLAN)
    allowed_vlans: set[int] = Field(default_factory=lambda: set(ALL_VLANS))

    # Layer-3 state
    ip_address: IPv4Address | None = None
    prefix_length: int | None = Field(default=None, ge=0, le=32)

    # Subinterface state (routers)
    parent: str | None = None  # canonical name of the physical parent
    encapsulation_vlan: int | None = Field(default=None, ge=MIN_VLAN, le=MAX_VLAN)

    @field_validator("name", mode="after")
    @classmethod
    def _canonical_name(cls, value: str) -> str:
        return canonical_interface_name(value)

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _validate_invariants(self) -> Interface:
        if self.is_subinterface:
            if self.parent is None:
                raise ValueError(f"subinterface {self.name} requires a parent interface")
            if "." in self.parent:
                raise ValueError(f"parent {self.parent} must be a physical interface")
        if self.encapsulation_vlan is not None and not self.is_subinterface:
            raise ValueError(
                f"encapsulation VLAN is only valid on subinterfaces, not {self.name}"
            )
        if (self.ip_address is None) != (self.prefix_length is None):
            raise ValueError("ip_address and prefix_length must be configured together")
        return self

    @property
    def is_subinterface(self) -> bool:
        return "." in self.name

    @property
    def short_name(self) -> str:
        return short_interface_name(self.name)

    @property
    def has_ip(self) -> bool:
        return self.ip_address is not None

    @property
    def cidr(self) -> str | None:
        """Address/prefix as ``192.168.10.1/24``, or ``None`` when unconfigured."""
        if self.ip_address is None or self.prefix_length is None:
            return None
        return f"{self.ip_address}/{self.prefix_length}"
