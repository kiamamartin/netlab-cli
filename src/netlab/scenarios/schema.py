"""Declarative scenario schema (doc §18, §40).

Scenarios are pure data: a topology, optional initial state (for
troubleshooting labs), human-readable objectives, and deterministic grading
requirements. The loader (``netlab.scenarios.loader``) validates this schema
fail-early and builds the initial :class:`NetworkState`. The grading engine
(later stage) consumes the ``requirements`` list; no requirement logic lives
here.
"""

from __future__ import annotations

from enum import StrEnum
from ipaddress import IPv4Address
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from netlab.core.enums import DeviceType
from netlab.devices.interface import (
    MAX_VLAN,
    MIN_VLAN,
    canonical_interface_name,
)

SCENARIO_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class Difficulty(StrEnum):
    """Scenario difficulty levels 1-5 (doc §20)."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXAM = "exam"
    TROUBLESHOOTING = "troubleshooting"


class SkillTag(StrEnum):
    """Recognized skill tags used later for adaptive learning (doc §33)."""

    VLAN = "vlan"
    ACCESS_PORTS = "access-ports"
    TRUNKING = "trunking"
    NATIVE_VLAN = "native-vlan"
    ROUTER_ON_A_STICK = "router-on-a-stick"
    IPV4_ADDRESSING = "ipv4-addressing"
    SUBNETTING = "subnetting"
    STATIC_ROUTING = "static-routing"
    DEFAULT_GATEWAY = "default-gateway"
    MANAGEMENT = "management"
    SSH = "ssh"
    TROUBLESHOOTING = "troubleshooting"


class MetadataSpec(BaseModel):
    """Scenario metadata (difficulty lives at the scenario top level)."""

    model_config = ConfigDict(extra="forbid")

    estimated_minutes: int | None = Field(default=None, gt=0)
    skills: list[SkillTag] = Field(default_factory=list)


class InterfaceSpec(BaseModel):
    """Declaration of a device interface in the topology.

    Only structural properties are declared here (name, subinterface parent,
    802.1Q encapsulation). Everything the student configures — switchport
    state, IP addressing, shutdown — starts at defaults or comes from
    ``initial_state``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    parent: str | None = None
    encapsulation_vlan: int | None = Field(default=None, ge=MIN_VLAN, le=MAX_VLAN)

    @field_validator("name", "parent", mode="after")
    @classmethod
    def _canonical(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class DeviceSpec(BaseModel):
    """Declaration of a device in the topology."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: DeviceType
    interfaces: list[InterfaceSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _subinterface_parents_exist(self) -> Self:
        names = {spec.name for spec in self.interfaces}
        for spec in self.interfaces:
            if spec.parent is not None and spec.parent not in names:
                raise ValueError(
                    f"{self.name}: subinterface {spec.name} references missing "
                    f"parent {spec.parent}"
                )
        return self


class EndpointSpec(BaseModel):
    """``HOSTNAME:Interface`` reference to one end of a link.

    Accepts a plain string (``R1:Gi0/0/0``, as in the architecture document)
    or a mapping (``ref: R1:Gi0/0/0``).
    """

    model_config = ConfigDict(extra="forbid")

    ref: str

    @model_validator(mode="before")
    @classmethod
    def _wrap_plain_string(cls, data: object) -> object:
        if isinstance(data, str):
            return {"ref": data}
        return data

    @field_validator("ref", mode="after")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError(
                f"endpoint {value!r} must be HOSTNAME:Interface (e.g. R1:Gi0/0)"
            )
        hostname, _, interface = value.rpartition(":")
        if not hostname or not interface:
            raise ValueError(f"endpoint {value!r} must be HOSTNAME:Interface")
        try:
            canonical_interface_name(interface)
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        return value


class LinkSpec(BaseModel):
    """Declaration of a link between two endpoints."""

    model_config = ConfigDict(extra="forbid")

    source: EndpointSpec
    destination: EndpointSpec


class TopologySpec(BaseModel):
    """Devices and links, validated for referential integrity."""

    model_config = ConfigDict(extra="forbid")

    devices: list[DeviceSpec] = Field(min_length=1)
    links: list[LinkSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_hostnames(self) -> Self:
        hostnames = [spec.name for spec in self.devices]
        if len(hostnames) != len(set(hostnames)):
            raise ValueError("device hostnames must be unique")
        return self


# --- Initial state (pre-configuration, e.g. broken networks) -------------------


class InitialVlanSpec(BaseModel):
    """A VLAN that exists before the lab starts."""

    model_config = ConfigDict(extra="forbid")

    device: str
    vlan_id: int = Field(ge=MIN_VLAN, le=MAX_VLAN)
    name: str | None = None


class InitialSwitchportSpec(BaseModel):
    """Switchport settings applied to an interface at load time."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["access", "trunk"] | None = None
    access_vlan: int | None = Field(default=None, ge=MIN_VLAN, le=MAX_VLAN)
    native_vlan: int | None = Field(default=None, ge=MIN_VLAN, le=MAX_VLAN)
    allowed: str | None = None  # IOS VLAN list, e.g. "10,20-24" ("" = none)

    @field_validator("allowed")
    @classmethod
    def _valid_vlan_list(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from netlab.devices.interface import parse_vlan_list

        try:
            parse_vlan_list(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        return value


class InitialIpSpec(BaseModel):
    """IPv4 settings applied to an interface at load time."""

    model_config = ConfigDict(extra="forbid")

    address: IPv4Address
    prefix: int = Field(ge=0, le=32)


class InitialInterfaceSpec(BaseModel):
    """Configuration applied to one interface before the lab starts."""

    model_config = ConfigDict(extra="forbid")

    device: str
    interface: str
    description: str | None = None
    shutdown: bool | None = None
    switchport: InitialSwitchportSpec | None = None
    ip: InitialIpSpec | None = None

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class InitialHostSpec(BaseModel):
    """Host-level settings (default gateway) applied at load time."""

    model_config = ConfigDict(extra="forbid")

    device: str
    gateway: IPv4Address


class InitialStateSpec(BaseModel):
    """Pre-configuration applied when the scenario loads (doc §44)."""

    model_config = ConfigDict(extra="forbid")

    vlans: list[InitialVlanSpec] = Field(default_factory=list)
    interfaces: list[InitialInterfaceSpec] = Field(default_factory=list)
    hosts: list[InitialHostSpec] = Field(default_factory=list)


# --- Grading requirements (deterministic; consumed by the grading engine) ------


class RequirementBase(BaseModel):
    """Common shape of every grading requirement."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: str = Field(min_length=1)
    points: int = Field(gt=0)
    description: str | None = None

    @field_validator("id")
    @classmethod
    def _non_empty_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requirement id must not be empty")
        return value.strip()


class VLANExistsRequirement(RequirementBase):
    type: Literal["vlan_exists"] = "vlan_exists"
    device: str
    vlan_id: int = Field(ge=MIN_VLAN, le=MAX_VLAN)
    name: str | None = None  # when set, the VLAN name must match too


class VLANNameRequirement(RequirementBase):
    type: Literal["vlan_name"] = "vlan_name"
    device: str
    vlan_id: int = Field(ge=MIN_VLAN, le=MAX_VLAN)
    name: str = Field(min_length=1)


class InterfaceModeRequirement(RequirementBase):
    type: Literal["interface_mode"] = "interface_mode"
    device: str
    interface: str
    mode: Literal["access", "trunk"]

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class AccessVlanRequirement(RequirementBase):
    type: Literal["access_vlan"] = "access_vlan"
    device: str
    interface: str
    vlan_id: int = Field(ge=MIN_VLAN, le=MAX_VLAN)

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class TrunkNativeVlanRequirement(RequirementBase):
    type: Literal["trunk_native_vlan"] = "trunk_native_vlan"
    device: str
    interface: str
    vlan_id: int = Field(ge=MIN_VLAN, le=MAX_VLAN)

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class TrunkAllowedVlansRequirement(RequirementBase):
    type: Literal["trunk_allowed_vlans"] = "trunk_allowed_vlans"
    device: str
    interface: str
    vlans: list[int] = Field(min_length=1)

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("vlans")
    @classmethod
    def _valid_ids(cls, value: list[int]) -> list[int]:
        for vlan_id in value:
            if not MIN_VLAN <= vlan_id <= MAX_VLAN:
                raise ValueError(f"VLAN id {vlan_id} outside {MIN_VLAN}-{MAX_VLAN}")
        return value


class IpAddressRequirement(RequirementBase):
    type: Literal["ip_address"] = "ip_address"
    device: str
    interface: str
    address: IPv4Address
    prefix: int = Field(ge=0, le=32)

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class HostGatewayRequirement(RequirementBase):
    type: Literal["host_gateway"] = "host_gateway"
    device: str
    gateway: IPv4Address


class HostIpRequirement(RequirementBase):
    """IPv4 addressing on an end-host interface."""

    type: Literal["host_ip"] = "host_ip"
    device: str
    interface: str
    address: IPv4Address
    prefix: int = Field(ge=0, le=32)

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class SubinterfaceEncapsulationRequirement(RequirementBase):
    """802.1Q encapsulation VLAN on a router subinterface."""

    type: Literal["subinterface_encapsulation"] = "subinterface_encapsulation"
    device: str
    interface: str
    vlan_id: int = Field(ge=MIN_VLAN, le=MAX_VLAN)

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_interface_name(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


Requirement = Annotated[
    VLANExistsRequirement
    | VLANNameRequirement
    | InterfaceModeRequirement
    | AccessVlanRequirement
    | TrunkNativeVlanRequirement
    | TrunkAllowedVlansRequirement
    | IpAddressRequirement
    | HostGatewayRequirement
    | HostIpRequirement
    | SubinterfaceEncapsulationRequirement,
    Field(discriminator="type"),
]


class GradingSpec(BaseModel):
    """Author-decided weighting (doc §23); requirements must sum to ``total``."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(default=100, gt=0)


# --- Verification (reserved; the connectivity engine lands with Phase 3) -------


class ConnectivityCheckSpec(BaseModel):
    """A connectivity verification the student (or tests) may run later."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["connectivity"] = "connectivity"
    source: str
    destination: str
    expected: bool


VerificationCheck = Annotated[ConnectivityCheckSpec, Field(discriminator="type")]


# --- Scenario root --------------------------------------------------------------


class Scenario(BaseModel):
    """A complete declarative scenario specification."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=SCENARIO_ID_PATTERN)
    title: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    difficulty: Difficulty
    metadata: MetadataSpec = Field(default_factory=MetadataSpec)

    topology: TopologySpec
    initial_state: InitialStateSpec | None = None

    objectives: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(min_length=1)
    grading: GradingSpec = Field(default_factory=GradingSpec)
    verification: list[VerificationCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_requirements(self) -> Self:
        ids = [requirement.id for requirement in self.requirements]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate requirement ids: {', '.join(duplicates)}")
        earned = sum(requirement.points for requirement in self.requirements)
        if earned != self.grading.total:
            raise ValueError(
                f"requirement points sum to {earned}, but grading.total is "
                f"{self.grading.total}"
            )
        return self

    @property
    def points_by_category(self) -> dict[str, int]:
        """Total points available per assessment category."""
        totals: dict[str, int] = {}
        for requirement in self.requirements:
            totals[requirement.category] = (
                totals.get(requirement.category, 0) + requirement.points
            )
        return totals
