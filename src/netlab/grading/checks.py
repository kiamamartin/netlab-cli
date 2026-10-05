"""Deterministic checks (doc §17, §22, §49).

A check compares one requirement against the authoritative
:class:`~netlab.network.state.NetworkState` — never against rendered CLI text
and never via an LLM. Each validator answers: what was expected, what actually
exists, did it pass, and why.

Validators return :class:`CheckOutcome` with an explicit earned-point split so
partial credit (doc §24) is decided by the check definition, not invented at
runtime. Pure functions over state: same state in, same outcome out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from netlab.core.enums import SwitchportMode
from netlab.core.errors import GradingError, UnknownInterfaceError, UnknownVlanError
from netlab.devices.base import Device
from netlab.devices.host import Host
from netlab.devices.switch import Switch
from netlab.network.state import NetworkState
from netlab.scenarios.schema import (
    AccessVlanRequirement,
    HostGatewayRequirement,
    HostIpRequirement,
    InterfaceModeRequirement,
    IpAddressRequirement,
    SubinterfaceEncapsulationRequirement,
    TrunkAllowedVlansRequirement,
    TrunkNativeVlanRequirement,
    VLANExistsRequirement,
    VLANNameRequirement,
)


@dataclass(frozen=True)
class CheckOutcome:
    """Result of one check against state (doc: expected / actual / pass / why).

    ``earned_fraction`` is the fraction of the requirement's points awarded:
    ``1.0`` pass, ``0.0`` fail, anything between is explicit partial credit
    chosen by the check definition (doc §24) — never invented at runtime.
    """

    passed: bool
    expected: str
    actual: str
    earned_fraction: float = 1.0
    detail: str = ""

    @classmethod
    def fail(cls, expected: str, actual: str, detail: str = "") -> CheckOutcome:
        return cls(
            passed=False, expected=expected, actual=actual, earned_fraction=0.0, detail=detail
        )

    @classmethod
    def partial(cls, expected: str, actual: str, fraction: float, detail: str) -> CheckOutcome:
        return cls(
            passed=False,
            expected=expected,
            actual=actual,
            earned_fraction=fraction,
            detail=detail,
        )


class Validator(Protocol):
    """A check validates a requirement against authoritative state."""

    def __call__(self, state: NetworkState, requirement: object) -> CheckOutcome: ...


def _device(state: NetworkState, hostname: str) -> Device:
    try:
        return state.device(hostname)
    except Exception as exc:  # unknown device — loader validation prevents this
        raise GradingError(f"grading: unknown device {hostname!r}") from exc


# --- Validators (one per requirement type) ---------------------------------------


def check_vlan_exists(state: NetworkState, requirement: object) -> CheckOutcome:
    req: VLANExistsRequirement = requirement  # type: ignore[assignment]
    device = _device(state, req.device)
    if not isinstance(device, Switch):
        return CheckOutcome.fail(
            f"VLAN {req.vlan_id} exists on {req.device}", f"{req.device} is not a switch"
        )
    try:
        vlan = device.get_vlan(req.vlan_id)
    except UnknownVlanError:
        return CheckOutcome.fail(
            f"VLAN {req.vlan_id} exists on {req.device}",
            "VLAN does not exist",
            detail=f"Create it with: vlan {req.vlan_id}",
        )
    if req.name is not None and vlan.name != req.name:
        return CheckOutcome.fail(
            f"VLAN {req.vlan_id} named {req.name!r}",
            f"named {vlan.name!r}" if vlan.name else "unnamed",
        )
    return CheckOutcome(
        passed=True,
        expected=f"VLAN {req.vlan_id}",
        actual=f"VLAN {req.vlan_id} ({vlan.name or 'unnamed'})",
    )


def check_vlan_name(state: NetworkState, requirement: object) -> CheckOutcome:
    req: VLANNameRequirement = requirement  # type: ignore[assignment]
    device = _device(state, req.device)
    if not isinstance(device, Switch):
        return CheckOutcome.fail(
            f"VLAN {req.vlan_id} named {req.name!r}", f"{req.device} is not a switch"
        )
    try:
        vlan = device.get_vlan(req.vlan_id)
    except UnknownVlanError:
        return CheckOutcome.fail(
            f"VLAN {req.vlan_id} named {req.name!r}",
            "VLAN does not exist",
            detail=f"Create it with: vlan {req.vlan_id}",
        )
    if vlan.name != req.name:
        return CheckOutcome.fail(
            f"VLAN {req.vlan_id} named {req.name!r}",
            f"named {vlan.name!r}" if vlan.name else "unnamed",
        )
    return CheckOutcome(passed=True, expected=req.name, actual=vlan.name)


def _switch_interface(
    state: NetworkState, hostname: str, interface: str
) -> tuple[Switch, object] | CheckOutcome:
    """Resolve the device+interface, or return a failing CheckOutcome."""
    device = _device(state, hostname)
    if not isinstance(device, Switch):
        return CheckOutcome.fail(
            f"{hostname} {interface} configured", f"{hostname} is not a switch"
        )
    try:
        return device, device.get_interface(interface)
    except UnknownInterfaceError:
        return CheckOutcome.fail(
            f"{hostname} {interface} exists", "interface does not exist"
        )


def check_interface_mode(state: NetworkState, requirement: object) -> CheckOutcome:
    req: InterfaceModeRequirement = requirement  # type: ignore[assignment]
    resolved = _switch_interface(state, req.device, req.interface)
    if isinstance(resolved, CheckOutcome):
        return resolved
    device, interface = resolved  # type: ignore[misc]
    expected_mode = SwitchportMode(req.mode)
    actual_mode: SwitchportMode = interface.switchport_mode  # type: ignore[attr-defined]
    if actual_mode is not expected_mode:
        if expected_mode is SwitchportMode.TRUNK and actual_mode is SwitchportMode.ACCESS:
            # Half-configured trunk: the student reached for trunking but the
            # port is still access — partial credit is explicit (doc §24).
            # A port still in default NONE mode earns nothing: absence of
            # configuration is not progress towards the requirement.
            return CheckOutcome.partial(
                expected_mode.value,
                actual_mode.value,
                fraction=0.5,
                detail="port is in access mode; 'switchport mode trunk' is missing",
            )
        return CheckOutcome.fail(
            f"switchport mode {expected_mode.value}", f"switchport mode {actual_mode.value}"
        )
    return CheckOutcome(
        passed=True,
        expected=expected_mode.value,
        actual=actual_mode.value,
    )


def check_access_vlan(state: NetworkState, requirement: object) -> CheckOutcome:
    req: AccessVlanRequirement = requirement  # type: ignore[assignment]
    resolved = _switch_interface(state, req.device, req.interface)
    if isinstance(resolved, CheckOutcome):
        return resolved
    device, interface = resolved  # type: ignore[misc]
    access_vlan: int = interface.access_vlan  # type: ignore[attr-defined]
    mode: SwitchportMode = interface.switchport_mode  # type: ignore[attr-defined]
    if access_vlan == req.vlan_id and mode is SwitchportMode.ACCESS:
        return CheckOutcome(
            passed=True,
            expected=f"access VLAN {req.vlan_id}",
            actual=f"access VLAN {access_vlan}",
        )
    if access_vlan == req.vlan_id:
        # Right VLAN, wrong mode: half the concept demonstrated (doc §24).
        return CheckOutcome.partial(
            f"access VLAN {req.vlan_id} in access mode",
            f"VLAN {access_vlan} assigned but mode is {mode.value}",
            fraction=0.5,
            detail="'switchport mode access' is missing",
        )
    return CheckOutcome.fail(
        f"access VLAN {req.vlan_id}",
        f"access VLAN {access_vlan}",
        detail=f"Configure: switchport access vlan {req.vlan_id}",
    )


def check_trunk_native_vlan(state: NetworkState, requirement: object) -> CheckOutcome:
    req: TrunkNativeVlanRequirement = requirement  # type: ignore[assignment]
    resolved = _switch_interface(state, req.device, req.interface)
    if isinstance(resolved, CheckOutcome):
        return resolved
    device, interface = resolved  # type: ignore[misc]
    native_vlan: int = interface.native_vlan  # type: ignore[attr-defined]
    mode: SwitchportMode = interface.switchport_mode  # type: ignore[attr-defined]
    if native_vlan == req.vlan_id and mode is SwitchportMode.TRUNK:
        return CheckOutcome(
            passed=True,
            expected=f"native VLAN {req.vlan_id}",
            actual=f"native VLAN {native_vlan}",
        )
    if mode is not SwitchportMode.TRUNK:
        # The port is not even a trunk: the native-VLAN requirement is
        # unmet regardless of the stored default (VLAN 1), so a fresh
        # unconfigured port must not earn points for doing nothing.
        return CheckOutcome.fail(
            f"native VLAN {req.vlan_id} on a trunk",
            f"port is in {mode.value} mode",
            detail="configure trunking first: switchport mode trunk",
        )
    return CheckOutcome.fail(
        f"native VLAN {req.vlan_id}",
        f"native VLAN {native_vlan}",
        detail=f"Configure: switchport trunk native vlan {req.vlan_id}",
    )


def check_trunk_allowed_vlans(state: NetworkState, requirement: object) -> CheckOutcome:
    req: TrunkAllowedVlansRequirement = requirement  # type: ignore[assignment]
    resolved = _switch_interface(state, req.device, req.interface)
    if isinstance(resolved, CheckOutcome):
        return resolved
    device, interface = resolved  # type: ignore[misc]
    allowed: set[int] = set(interface.allowed_vlans)  # type: ignore[attr-defined]
    expected = set(req.vlans)
    mode: SwitchportMode = interface.switchport_mode  # type: ignore[attr-defined]
    if mode is not SwitchportMode.TRUNK:
        # An unpruned default list on a non-trunk port demonstrates nothing;
        # the default all-VLAN allowed list must not earn partial credit.
        return CheckOutcome.fail(
            f"allowed VLANs {sorted(expected)} on a trunk",
            f"port is in {mode.value} mode",
            detail="configure trunking first: switchport mode trunk",
        )
    if expected <= allowed:
        extra = sorted(allowed - expected)
        if not extra:
            return CheckOutcome(
                passed=True,
                expected=f"allowed VLANs {sorted(expected)}",
                actual=f"allowed VLANs {sorted(allowed)}",
            )
        # Unpruned superset (e.g. the default 1-4094 trunk): the required
        # VLANs flow but the list was never pruned. Explicit fixed fraction
        # (doc §24) — deliberately not a ratio, which would round 2/4094
        # to zero and silently turn a partial into a fail.
        return CheckOutcome.partial(
            f"allowed VLANs exactly {sorted(expected)}",
            f"allowed VLANs {sorted(allowed)}",
            fraction=0.5,
            detail="prune the allowed list: switchport trunk allowed vlan "
            + ",".join(str(v) for v in sorted(expected)),
        )
    missing = sorted(expected - allowed)
    return CheckOutcome.fail(
        f"allowed VLANs {sorted(expected)}",
        f"allowed VLANs {sorted(allowed)}",
        detail=f"missing VLAN(s) {missing}",
    )


def check_ip_address(state: NetworkState, requirement: object) -> CheckOutcome:
    req: IpAddressRequirement = requirement  # type: ignore[assignment]
    device = _device(state, req.device)
    try:
        interface = device.get_interface(req.interface)
    except UnknownInterfaceError:
        return CheckOutcome.fail(
            f"{req.device} {req.interface} is {req.address}/{req.prefix}",
            "interface does not exist",
            detail="Create the subinterface first: interface "
            + req.interface,
        )
    if not interface.has_ip:
        return CheckOutcome.fail(
            f"{req.address}/{req.prefix}",
            "no address configured",
            detail=f"Configure: ip address {req.address} 255.255.255.0",
        )
    if not interface.admin_enabled:
        # Doc §25 lists 'shutdown interface' as a critical error: a
        # configured address on an administratively down interface must not
        # earn full credit (the interface cannot pass traffic).
        return CheckOutcome.partial(
            f"{req.address}/{req.prefix} with the interface up",
            f"{interface.ip_address}/{interface.prefix_length} but administratively down",
            fraction=0.5,
            detail="interface is shut down; configure: no shutdown",
        )
    actual = f"{interface.ip_address}/{interface.prefix_length}"
    if interface.ip_address != req.address or interface.prefix_length != req.prefix:
        # Address on the right subnet but wrong host part earns partial
        # credit; everything else is a clean fail (wrong subnet is critical).
        if (
            interface.ip_address is not None
            and interface.prefix_length == req.prefix
            and str(interface.ip_address).rsplit(".", 1)[0]
            == str(req.address).rsplit(".", 1)[0]
        ):
            return CheckOutcome.partial(
                f"{req.address}/{req.prefix}",
                actual,
                fraction=0.5,
                detail="correct subnet, wrong host address",
            )
        return CheckOutcome.fail(f"{req.address}/{req.prefix}", actual)
    return CheckOutcome(
        passed=True, expected=f"{req.address}/{req.prefix}", actual=actual
    )


def check_subinterface_encapsulation(state: NetworkState, requirement: object) -> CheckOutcome:
    req: SubinterfaceEncapsulationRequirement = requirement  # type: ignore[assignment]
    device = _device(state, req.device)
    try:
        interface = device.get_interface(req.interface)
    except UnknownInterfaceError:
        return CheckOutcome.fail(
            f"{req.interface} encapsulates 802.1Q VLAN {req.vlan_id}",
            "subinterface does not exist",
            detail=f"Configure: interface {req.interface} then encapsulation dot1q {req.vlan_id}",
        )
    if interface.encapsulation_vlan is None:
        return CheckOutcome.fail(
            f"encapsulation dot1q {req.vlan_id}",
            "no encapsulation configured",
            detail=f"Configure: encapsulation dot1q {req.vlan_id}",
        )
    if interface.encapsulation_vlan != req.vlan_id:
        return CheckOutcome.fail(
            f"encapsulation dot1q {req.vlan_id}",
            f"encapsulation dot1q {interface.encapsulation_vlan}",
        )
    return CheckOutcome(
        passed=True,
        expected=f"dot1q {req.vlan_id}",
        actual=f"dot1q {interface.encapsulation_vlan}",
    )


def check_host_ip(state: NetworkState, requirement: object) -> CheckOutcome:
    req: HostIpRequirement = requirement  # type: ignore[assignment]
    device = _device(state, req.device)
    if not isinstance(device, Host):
        return CheckOutcome.fail(
            f"{req.device} {req.interface} is {req.address}/{req.prefix}",
            f"{req.device} is not an end host",
        )
    try:
        interface = device.get_interface(req.interface)
    except UnknownInterfaceError:
        return CheckOutcome.fail(
            f"{req.device} {req.interface} is {req.address}/{req.prefix}",
            "interface does not exist",
        )
    if not interface.has_ip:
        return CheckOutcome.fail(
            f"{req.address}/{req.prefix}",
            "no address configured",
            detail=f"Configure from EXEC: ip address {req.address} 255.255.255.0",
        )
    actual = f"{interface.ip_address}/{interface.prefix_length}"
    if interface.ip_address != req.address or interface.prefix_length != req.prefix:
        return CheckOutcome.fail(f"{req.address}/{req.prefix}", actual)
    return CheckOutcome(
        passed=True, expected=f"{req.address}/{req.prefix}", actual=actual
    )


def check_host_gateway(state: NetworkState, requirement: object) -> CheckOutcome:
    req: HostGatewayRequirement = requirement  # type: ignore[assignment]
    device = _device(state, req.device)
    if not isinstance(device, Host):
        return CheckOutcome.fail(
            f"{req.device} default gateway {req.gateway}", f"{req.device} is not an end host"
        )
    if device.default_gateway is None:
        return CheckOutcome.fail(
            f"default gateway {req.gateway}",
            "no gateway configured",
            detail=f"Configure: ip default-gateway {req.gateway}",
        )
    if device.default_gateway != req.gateway:
        return CheckOutcome.fail(
            f"default gateway {req.gateway}",
            f"default gateway {device.default_gateway}",
        )
    return CheckOutcome(
        passed=True,
        expected=str(req.gateway),
        actual=str(device.default_gateway),
    )


__all__ = [
    "CheckOutcome",
    "Validator",
    "check_access_vlan",
    "check_host_gateway",
    "check_host_ip",
    "check_interface_mode",
    "check_ip_address",
    "check_subinterface_encapsulation",
    "check_trunk_allowed_vlans",
    "check_trunk_native_vlan",
    "check_vlan_exists",
    "check_vlan_name",
]
