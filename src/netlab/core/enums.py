"""Shared enumerations for the domain model."""

from enum import StrEnum


class DeviceType(StrEnum):
    """Supported device categories (doc §13)."""

    ROUTER = "router"
    SWITCH = "switch"
    PC = "pc"
    SERVER = "server"


class ConfigMode(StrEnum):
    """IOS-like configuration modes for the milestone-1 CLI state machine (doc §11)."""

    USER_EXEC = "user-exec"
    PRIVILEGED_EXEC = "privileged-exec"
    GLOBAL_CONFIG = "global-config"
    INTERFACE_CONFIG = "interface-config"
    VLAN_CONFIG = "vlan-config"


class SwitchportMode(StrEnum):
    """Layer-2 switchport mode.

    ``NONE`` marks a routed (L3) interface or an unconfigured switch port; the
    CLI layer decides how ``NONE`` behaves for a switch port.
    """

    NONE = "none"
    ACCESS = "access"
    TRUNK = "trunk"


class TrunkEncapsulation(StrEnum):
    """Trunking encapsulation negotiated on a switchport trunk (doc §26).

    ``NEGOTIATE`` mirrors the IOS default when the ``switchport trunk
    encapsulation`` command has never been entered; ``DOT1Q`` records the
    command form actually taught in the labs.
    """

    NEGOTIATE = "negotiate"
    DOT1Q = "dot1q"
    ISL = "isl"


class LinkStatus(StrEnum):
    """Physical link status."""

    UP = "up"
    DOWN = "down"


class VlanStatus(StrEnum):
    """VLAN operational status (doc §15)."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
