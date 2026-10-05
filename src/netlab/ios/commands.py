"""Stage-4 configuration command handlers.

Each mixin is bound to a :class:`~netlab.ios.machine.CliSession` by
``machine.py``; handlers mutate the authoritative network state through the
domain models only. Registration is device-type gated so commands that do not
exist on a device type are plain invalid input there, mirroring real IOS.

Decision records (architecture review §B):

- **B6** — ``switchport access vlan <id>`` on a missing VLAN auto-creates it,
  mirroring real IOS; the deterministic grader decides whether that was wanted.
- **B9** — end hosts use the minimal IOS-like surface ``ip address A B`` and
  ``ip default-gateway A`` from EXEC, mirroring Packet Tracer's IP
  Configuration desktop utility.
"""

from __future__ import annotations

from ipaddress import IPv4Address, ip_network
from typing import TYPE_CHECKING

from netlab.core.enums import DeviceType, SwitchportMode
from netlab.core.errors import (
    InvalidOperationError,
    InvalidVlanIdError,
)
from netlab.devices.interface import Interface, parse_vlan_list, validate_vlan_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from netlab.ios.machine import CliSession
    from netlab.ios.parser import CommandRegistry


# --- Argument validators (module level: CommandSpec requires callables) ---------


def _strip_leading_no(tokens: list[str]) -> list[str]:
    return tokens[1:] if tokens and tokens[0].lower() == "no" else tokens


def validate_ip_address_mask(tokens: list[str]) -> tuple[str, int]:
    """Validate ``A.B.C.D M.M.M.M`` (or ``A.B.C.D /prefix``); returns (addr, prefix)."""
    if len(tokens) != 2:
        raise ValueError("expected an address and a mask")
    address, mask_text = tokens
    try:
        if mask_text.startswith("/"):
            prefix = ip_network(f"0.0.0.0{mask_text}", strict=False).prefixlen
        else:
            prefix = ip_network(f"0.0.0.0/{mask_text}", strict=False).prefixlen
    except ValueError as exc:
        raise ValueError(f"invalid mask {mask_text!r}") from exc
    IPv4Address(address)  # raises on malformed addresses
    return address, prefix


def validate_switchport_mode(value: str) -> SwitchportMode:
    if value.lower() == "access":
        return SwitchportMode.ACCESS
    if value.lower() == "trunk":
        return SwitchportMode.TRUNK
    raise ValueError(f"unknown switchport mode {value!r}")


def validate_access_vlan(value: str) -> int:
    try:
        return validate_vlan_id(int(value))
    except ValueError as exc:
        if isinstance(exc, InvalidVlanIdError):
            raise
        raise ValueError(f"invalid VLAN id {value!r}") from exc


def validate_allowed_vlan_argument(tokens: list[str]) -> object:
    """Validate ``switchport trunk allowed vlan`` argument forms.

    ``no``-prefixed forms arrive stripped; remaining tokens are one of:
    ``all`` | ``except LIST`` | ``add LIST`` | ``remove LIST`` | ``LIST``.
    """
    if not tokens:
        raise ValueError("missing VLAN list")
    verb = tokens[0].lower()
    if verb == "all":
        return ("all", frozenset())
    if verb in ("add", "remove"):
        if len(tokens) != 2:
            raise ValueError(f"expected a VLAN list after {verb!r}")
        return (verb, parse_vlan_list(tokens[1]))
    if verb == "except":
        if len(tokens) != 2:
            raise ValueError("expected a VLAN list after 'except'")
        return ("except", parse_vlan_list(tokens[1]))
    return ("set", parse_vlan_list(" ".join(tokens)))


def validate_encapsulation_argument(tokens: list[str]) -> int:
    """Validate ``dot1q <vlan>``; returns the VLAN id."""
    if len(tokens) != 2 or tokens[0].lower() != "dot1q":
        raise ValueError("expected 'dot1q <vlan>'")
    try:
        return validate_vlan_id(int(tokens[1]))
    except InvalidVlanIdError:
        raise
    except ValueError as exc:
        raise ValueError(f"invalid VLAN id {tokens[1]!r}") from exc


def validate_gateway_argument(value: str) -> IPv4Address:
    return IPv4Address(value)


# --- Shared mixin: interface-mode commands valid on any device -------------------


class DeviceConfigMixin:
    """Commands valid inside INTERFACE_CONFIG on any device type."""

    session: CliSession

    def _interface(self) -> Interface:
        return self.session.selected_interface()

    def cmd_description(self, text: object) -> str:
        self._interface().description = str(text)
        return ""

    def cmd_shutdown(self, _argument: object) -> str:
        self._interface().admin_enabled = False
        return ""

    def cmd_no_shutdown(self, _argument: object) -> str:
        self._interface().admin_enabled = True
        return ""

    def cmd_ip_address(self, argument: object) -> str:
        address, prefix = argument  # type: ignore[misc]
        self._interface().set_ip(address, prefix)
        return ""

    def cmd_no_ip_address(self, _argument: object) -> str:
        self._interface().clear_ip()
        return ""

    def cmd_encapsulation(self, vlan_id: object) -> str:
        interface = self._interface()
        if not interface.is_subinterface:
            raise InvalidOperationError(
                "encapsulation is only valid on subinterfaces"
            )
        interface.encapsulation_vlan = int(vlan_id)  # type: ignore[arg-type]
        return ""


# --- Switch-only mixin -----------------------------------------------------------


class SwitchConfigMixin:
    """Commands valid only on switches (VLAN and switchport configuration)."""

    session: CliSession

    def _interface(self) -> Interface:
        return self.session.selected_interface()

    def cmd_switchport_mode(self, mode: object) -> str:
        self._interface().switchport_mode = mode  # type: ignore[assignment]
        return ""

    def cmd_switchport_access_vlan(self, vlan_id: object) -> str:
        vlan = int(vlan_id)  # type: ignore[arg-type]
        switch = self.session.require_switch()
        if vlan not in switch.vlans:
            # Decision B6: mirror real IOS and auto-create the missing VLAN.
            switch.create_vlan(vlan)
        # NOTE: this does NOT set the switchport mode — 'switchport mode
        # access' is a separate command, and grading checks both independently.
        self._interface().access_vlan = vlan
        return ""

    def cmd_switchport_trunk_allowed(self, argument: object) -> str:
        verb, vlans = argument  # type: ignore[misc]
        interface = self._interface()
        current = set(interface.allowed_vlans)
        if verb == "all":
            interface.allowed_vlans = set(range(1, 4095))
        elif verb == "set":
            interface.allowed_vlans = set(vlans)
        elif verb == "add":
            interface.allowed_vlans = current | set(vlans)
        elif verb == "remove":
            interface.allowed_vlans = current - set(vlans)
        elif verb == "except":
            interface.allowed_vlans = set(range(1, 4095)) - set(vlans)
        return ""

    def cmd_switchport_trunk_native(self, vlan_id: object) -> str:
        vlan = int(vlan_id)  # type: ignore[arg-type]
        switch = self.session.require_switch()
        if vlan not in switch.vlans:
            switch.create_vlan(vlan)
        self._interface().native_vlan = vlan
        return ""


# --- End-host mixin (decision B9) -------------------------------------------------


class HostConfigMixin:
    """Minimal IOS-like host addressing surface for PCs and servers.

    Mirrors Packet Tracer's IP Configuration utility rather than a full
    router CLI: ``ip address A.B.C.D M.M.M.D`` and ``ip default-gateway A.B.C.D``
    from EXEC context.
    """

    session: CliSession

    def cmd_host_ip_address(self, argument: object) -> str:
        address, prefix = argument  # type: ignore[misc]
        interface = self.session.default_host_interface()
        interface.set_ip(address, prefix)
        return ""

    def cmd_host_no_ip_address(self, _argument: object) -> str:
        self.session.default_host_interface().clear_ip()
        return ""

    def cmd_host_gateway(self, address: object) -> str:
        self.session.require_host().default_gateway = address  # type: ignore[assignment]
        return ""


# --- Registration -----------------------------------------------------------------


def interface_config_specs() -> list:
    """CommandSpecs registered in INTERFACE_CONFIG on every device."""
    from netlab.core.enums import ConfigMode
    from netlab.ios.parser import CommandSpec

    mode = ConfigMode.INTERFACE_CONFIG
    return [
        CommandSpec(name="description", mode=mode, argument="TEXT", free_text=True),
        CommandSpec(name="shutdown", mode=mode, pattern=("sh",)),
        CommandSpec(name="no shutdown", mode=mode, pattern=("no", "sh")),
        CommandSpec(
            name="ip address",
            mode=mode,
            pattern=("ip", "a"),
            argument="A.B.C.D MASK",
            argument_min=2,
            argument_max=2,
            multi_validator=validate_ip_address_mask,
        ),
        CommandSpec(name="no ip address", mode=mode, pattern=("no", "ip", "a")),
        CommandSpec(
            name="encapsulation",
            mode=mode,
            pattern=("en",),
            argument="DOT1Q VLAN",
            argument_min=2,
            argument_max=2,
            multi_validator=validate_encapsulation_argument,
        ),
    ]


def switch_specs() -> list:
    """CommandSpecs registered only on switches (stage 2-4 switch surface)."""
    from netlab.core.enums import ConfigMode
    from netlab.ios.parser import CommandSpec, validate_vlan_id_argument

    return [
        CommandSpec(
            name="switchport mode",
            mode=ConfigMode.INTERFACE_CONFIG,
            pattern=("switchport", "m"),
            argument="access|trunk",
            argument_validator=validate_switchport_mode,
        ),
        CommandSpec(
            name="switchport access vlan",
            mode=ConfigMode.INTERFACE_CONFIG,
            pattern=("switchport", "a", "vl"),
            argument="VLAN_ID",
            argument_validator=validate_access_vlan,
        ),
        CommandSpec(
            name="switchport trunk allowed vlan",
            mode=ConfigMode.INTERFACE_CONFIG,
            pattern=("switchport", "trunk", "al"),
            argument="VLAN_LIST",
            argument_min=1,
            argument_max=4,
            multi_validator=validate_allowed_vlan_argument,
        ),
        CommandSpec(
            name="switchport trunk native vlan",
            mode=ConfigMode.INTERFACE_CONFIG,
            pattern=("switchport", "trunk", "na"),
            argument="VLAN_ID",
            argument_validator=validate_access_vlan,
        ),
        CommandSpec(
            name="vlan",
            mode=ConfigMode.GLOBAL_CONFIG,
            pattern=("vl",),
            argument="VLAN_ID",
            argument_validator=validate_vlan_id_argument,
        ),
        CommandSpec(
            name="name",
            mode=ConfigMode.VLAN_CONFIG,
            pattern=("na",),
            argument="NAME",
            free_text=True,
        ),
    ]


def host_specs() -> list:
    """CommandSpecs registered only on end hosts (decision B9).

    Host addressing mirrors Packet Tracer's IP Configuration utility, which is
    not mode-based, so the commands are available in both EXEC modes.
    """
    from netlab.core.enums import ConfigMode
    from netlab.ios.parser import CommandSpec

    specs: list = []
    for exec_mode in (ConfigMode.USER_EXEC, ConfigMode.PRIVILEGED_EXEC):
        specs.extend(
            [
                CommandSpec(
                    name="ip address",
                    mode=exec_mode,
                    pattern=("ip", "a"),
                    argument="A.B.C.D MASK",
                    argument_min=2,
                    argument_max=2,
                    multi_validator=validate_ip_address_mask,
                ),
                CommandSpec(
                    name="no ip address", mode=exec_mode, pattern=("no", "ip", "a")
                ),
                CommandSpec(
                    name="ip default-gateway",
                    mode=exec_mode,
                    pattern=("ip", "default-gateway"),
                    argument="A.B.C.D",
                    argument_validator=validate_gateway_argument,
                ),
            ]
        )
    return specs


def show_specs() -> list:
    """``show`` verification commands, available to every device in both EXEC modes."""
    from netlab.core.enums import ConfigMode
    from netlab.ios.parser import CommandSpec

    specs: list = []
    for exec_mode in (ConfigMode.USER_EXEC, ConfigMode.PRIVILEGED_EXEC):
        specs.extend(
            [
                CommandSpec(
                    name="show vlan brief", mode=exec_mode, pattern=("sh", "vl", "b")
                ),
                CommandSpec(
                    name="show interfaces trunk",
                    mode=exec_mode,
                    pattern=("sh", "in", "t"),
                ),
                CommandSpec(
                    name="show ip interface brief",
                    mode=exec_mode,
                    pattern=("sh", "ip", "i", "b"),
                ),
                CommandSpec(name="show running-config", mode=exec_mode, pattern=("sh", "ru")),
                CommandSpec(name="show startup-config", mode=exec_mode, pattern=("sh", "st")),
            ]
        )
    specs.append(
        CommandSpec(
            name="copy running-config startup-config",
            mode=ConfigMode.PRIVILEGED_EXEC,
            pattern=("cop", "ru", "st"),
        )
    )
    # Assessment commands (doc §55): run from privileged EXEC like `copy`.
    specs.append(
        CommandSpec(
            name="assess",
            mode=ConfigMode.PRIVILEGED_EXEC,
            pattern=("ass",),
            description="Grade the lab and show the assessment",
        )
    )
    specs.append(
        CommandSpec(
            name="submit",
            mode=ConfigMode.PRIVILEGED_EXEC,
            pattern=("sub",),
            description="Submit the lab for grading (alias of assess)",
        )
    )
    return specs


def base_specs(device_type: DeviceType | None) -> list:
    """Transition and structural commands, gated by device type.

    ``None`` registers everything (used by tests and tooling).
    """
    from netlab.core.enums import ConfigMode
    from netlab.ios.parser import (
        CommandSpec,
        validate_hostname_argument,
        validate_interface_argument,
    )

    specs: list = []

    specs.append(CommandSpec(name="enable", mode=ConfigMode.USER_EXEC, pattern=("e",),
                             description="Enter privileged EXEC mode"))
    specs.append(CommandSpec(name="exit", mode=ConfigMode.USER_EXEC, pattern=("e",),
                             description="End the session"))
    specs.append(CommandSpec(name="help", mode=ConfigMode.USER_EXEC, pattern=("h",),
                             description="List available commands"))
    specs.append(CommandSpec(name="?", mode=ConfigMode.USER_EXEC,
                             description="List available commands"))

    specs.append(CommandSpec(name="disable", mode=ConfigMode.PRIVILEGED_EXEC, pattern=("dis",),
                             description="Return to user EXEC mode"))
    specs.append(CommandSpec(name="configure terminal", mode=ConfigMode.PRIVILEGED_EXEC,
                             pattern=("conf", "t"),
                             description="Enter global configuration mode"))
    specs.append(CommandSpec(name="exit", mode=ConfigMode.PRIVILEGED_EXEC, pattern=("e",),
                             description="Return to user EXEC mode"))
    specs.append(CommandSpec(name="help", mode=ConfigMode.PRIVILEGED_EXEC, pattern=("h",),
                             description="List available commands"))
    specs.append(CommandSpec(name="?", mode=ConfigMode.PRIVILEGED_EXEC,
                             description="List available commands"))

    for mode in (ConfigMode.GLOBAL_CONFIG, ConfigMode.INTERFACE_CONFIG,
                 ConfigMode.VLAN_CONFIG):
        specs.append(CommandSpec(name="exit", mode=mode, pattern=("e",),
                                 description="Leave this mode"))
        specs.append(CommandSpec(name="end", mode=mode, pattern=("e",),
                                 description="Return to privileged EXEC mode"))
        specs.append(CommandSpec(name="help", mode=mode, pattern=("h",),
                                 description="List available commands"))
        specs.append(CommandSpec(name="?", mode=mode,
                                 description="List available commands"))

    specs.append(CommandSpec(name="hostname", mode=ConfigMode.GLOBAL_CONFIG,
                             pattern=("host",), argument="WORD",
                             argument_validator=validate_hostname_argument,
                             description="Set the device hostname"))
    specs.append(CommandSpec(name="interface", mode=ConfigMode.GLOBAL_CONFIG,
                             pattern=("int",), argument="TYPE/NUM",
                             argument_validator=validate_interface_argument,
                             description="Enter interface configuration mode"))

    if device_type is None or device_type is DeviceType.SWITCH:
        specs.extend(switch_specs())
    if device_type is None or device_type in (DeviceType.PC, DeviceType.SERVER):
        specs.extend(host_specs())
    specs.extend(show_specs())
    return specs


def build_registry(device_type: DeviceType | None = None) -> CommandRegistry:
    """Assemble the command registry for a device type (``None`` = all)."""
    from netlab.ios.parser import CommandRegistry

    registry = CommandRegistry()
    for spec in base_specs(device_type) + interface_config_specs():
        registry.register(spec)
    return registry


__all__ = [
    "DeviceConfigMixin",
    "HostConfigMixin",
    "SwitchConfigMixin",
    "build_registry",
    "host_specs",
    "interface_config_specs",
    "switch_specs",
]
