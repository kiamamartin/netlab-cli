"""Layer-3 and configuration verification renderers (doc §26)."""

from __future__ import annotations

from typing import Any

from netlab.devices.base import Device
from netlab.devices.host import Host, Server
from netlab.devices.router import Router
from netlab.devices.switch import Switch
from netlab.network.state import NetworkState


def _prefix_to_mask(prefix: int) -> str:
    """Convert a prefix length to a dotted-decimal mask (``24`` -> ``255.255.255.0``)."""
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return ".".join(str((bits >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _config_lines(device: Device) -> list[str]:
    """The canonical configuration lines describing a device's state.

    Shared by ``show running-config`` and ``show startup-config`` so both
    render the same configuration language.
    """
    lines = [f"hostname {device.hostname}"]
    for name in device.interface_names:
        interface = device.get_interface(name)
        lines.append(f"interface {name}")
        if interface.description:
            lines.append(f" description {interface.description}")
        if not interface.admin_enabled:
            lines.append(" shutdown")
        if interface.switchport_mode.value != "none":
            lines.append(f" switchport mode {interface.switchport_mode.value}")
            if interface.switchport_mode.value == "access" and interface.access_vlan != 1:
                lines.append(f" switchport access vlan {interface.access_vlan}")
            if interface.switchport_mode.value == "trunk":
                # 'negotiate' is the default: real IOS omits it from the config.
                if interface.trunk_encapsulation.value != "negotiate":
                    lines.append(
                        f" switchport trunk encapsulation {interface.trunk_encapsulation.value}"
                    )
                lines.append(f" switchport trunk native vlan {interface.native_vlan}")
                from netlab.devices.interface import ALL_VLANS, format_vlan_list

                if set(interface.allowed_vlans) != set(ALL_VLANS):
                    vlans = format_vlan_list(interface.allowed_vlans)
                    lines.append(f" switchport trunk allowed vlan {vlans}")
        if interface.has_ip:
            mask = _prefix_to_mask(interface.prefix_length or 0)
            lines.append(f" ip address {interface.ip_address} {mask}")
        if interface.encapsulation_vlan is not None:
            lines.append(f" encapsulation dot1q {interface.encapsulation_vlan}")
    if isinstance(device, Host) and device.default_gateway is not None:
        lines.append(f"ip default-gateway {device.default_gateway}")
    return lines


def _device_from_snapshot(snapshot: dict[str, Any]) -> Device | None:
    """Reconstruct a device from a stored :class:`Config` snapshot dict."""
    device_type = snapshot.get("device_type")
    cls = {"router": Router, "switch": Switch, "pc": Host, "server": Server}.get(device_type)
    if cls is None:
        return None
    return cls.model_validate(snapshot)


def show_ip_interface_brief(state: NetworkState, device: Device) -> str:
    """Render ``show ip interface brief``.

    The interface column is sized from the widest name actually present, so
    long interface names (``TenGigabitEthernet0/0/0.255``) cannot shift the
    remaining columns the way a hard-coded 21-character column would.
    """
    rows: list[tuple[str, str, str, str]] = []
    width = len("Interface")
    for name in device.interface_names:
        interface = device.get_interface(name)
        width = max(width, len(interface.short_name))
        address = interface.cidr if interface.has_ip else "unassigned"
        if state.interface_is_up(device.hostname, name):
            status = protocol = "up"
        elif not interface.admin_enabled:
            status, protocol = "administratively down", "down"
        else:
            status = protocol = "down"
        rows.append((interface.short_name, address, status, protocol))
    lines = [
        f"{'Interface':<{width}} {'IP-Address/Mask':<19} "
        f"{'Status':<19} Protocol",
        f"{'-' * width} {'-' * 19} {'-' * 19} ---------",
    ]
    for short_name, address, status, protocol in rows:
        lines.append(
            f"{short_name:<{width}} {address:<19} {status:<19} {protocol}"
        )
    return "\n".join(lines)


def show_running_config(state: NetworkState, device: Device) -> str:
    """Render ``show running-config`` from the live state."""
    lines = ["Building configuration...", "", "Current configuration:", "!"]
    lines.extend(_config_lines(device))
    lines.append("!end")
    return "\n".join(lines)


def show_startup_config(state: NetworkState, device: Device) -> str:
    """Render ``show startup-config`` from the saved snapshot, if any.

    The snapshot is a frozen serialization of past state; the device is
    reconstructed from it and rendered with the same renderer as the running
    config, so startup output is never synthesized independently.
    """
    if device.startup_config is None:
        return "startup-config is not present"
    restored = _device_from_snapshot(device.startup_config.snapshot)
    if restored is None:
        return "startup-config is not present"
    lines = ["Using saved configuration:", "!"]
    lines.extend(_config_lines(restored))
    lines.append("!end")
    return "\n".join(lines)


__all__ = [
    "_config_lines",
    "show_ip_interface_brief",
    "show_running_config",
    "show_startup_config",
]
