"""Verification command renderers (doc §26).

Every ``show`` command is *rendered from the authoritative network state* —
never hard-coded. If a student changes the state, the output changes with it.
Renderers are pure functions over the state so the grading engine and future
backends can reuse them.
"""

from __future__ import annotations

from netlab.core.enums import SwitchportMode
from netlab.devices.base import Device
from netlab.devices.interface import format_vlan_list
from netlab.devices.switch import Switch
from netlab.network.state import NetworkState


def _effective_vlan(device: Device, interface_name: str) -> int:
    """The VLAN a port belongs to for ``show vlan brief`` purposes.

    Trunk ports appear under their native VLAN, everything else under its
    access VLAN (which defaults to 1 — so unconfigured ports appear under
    VLAN 1, as in real IOS).
    """
    interface = device.get_interface(interface_name)
    if interface.switchport_mode is SwitchportMode.TRUNK:
        return interface.native_vlan
    return interface.access_vlan


def show_vlan_brief(state: NetworkState, device: Device) -> str:
    """Render ``show vlan brief`` for a switch."""
    if not isinstance(device, Switch):
        return f"% Invalid input detected at 'show vlan brief' on {device.hostname}"
    lines = [
        "VLAN Name                             Status    Ports",
        "---- -------------------------------- --------- -------------------------------",
    ]
    port_of: dict[int, list[str]] = {}
    for name in device.interface_names:
        if "." in name:
            continue  # switches have no subinterfaces in this model
        port_of.setdefault(_effective_vlan(device, name), []).append(
            device.get_interface(name).short_name
        )
    for vlan_id in sorted(device.vlans):
        vlan = device.vlans[vlan_id]
        ports = ", ".join(port_of.get(vlan_id, []))
        name = vlan.name or f"VLAN{vlan_id:04d}"
        lines.append(
            f"{vlan_id:<4} {name:<32} {vlan.status.value:<9} {ports}"
        )
    return "\n".join(lines)


def show_interfaces_trunk(state: NetworkState, device: Device) -> str:
    """Render ``show interfaces trunk`` for a switch."""
    if not isinstance(device, Switch):
        return f"% Invalid input detected at 'show interfaces trunk' on {device.hostname}"
    trunks = [
        name
        for name in device.interface_names
        if device.get_interface(name).switchport_mode is SwitchportMode.TRUNK
        and "." not in name
    ]
    if not trunks:
        return ""
    header = (
        "Port        Mode             Encapsulation  Status        Native vlan\n"
        "----------- ---------------- -------------- ------------- ------------"
    )
    first: list[str] = []
    for name in trunks:
        interface = device.get_interface(name)
        status = "trunking" if state.interface_is_up(device.hostname, name) else "down"
        first.append(
            f"{interface.short_name:<11} {'on':<16} {'802.1q':<14} {status:<13} "
            f"{interface.native_vlan}"
        )
    second_header = (
        "\nPort        Vlans allowed and active in management domain\n"
        "----------- --------------------------------------------------------"
    )
    second: list[str] = []
    third_header = (
        "\nPort        Vlans in spanning tree forwarding state and not pruned\n"
        "----------- --------------------------------------------------------"
    )
    third: list[str] = []
    for name in trunks:
        interface = device.get_interface(name)
        short = interface.short_name
        active = sorted(set(interface.allowed_vlans) & set(device.vlans))
        second.append(f"{short:<11} {format_vlan_list(active) or 'none'}")
        third.append(f"{short:<11} {format_vlan_list(active) or 'none'}")
    return "\n".join([header, *first, second_header, *second, third_header, *third])
