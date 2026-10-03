"""Scenario loading: YAML → validated :class:`Scenario` → initial :class:`NetworkState`.

All failures are raised as :class:`ScenarioError` with file context so invalid
scenarios fail early with useful diagnostics (brief §13).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from netlab.core.enums import DeviceType, SwitchportMode
from netlab.core.errors import ScenarioError
from netlab.devices.base import Device
from netlab.devices.host import PC, Host, Server
from netlab.devices.interface import (
    Interface,
    canonical_interface_name,
    parse_vlan_list,
)
from netlab.devices.router import Router
from netlab.devices.switch import Switch
from netlab.network.link import Endpoint, Link
from netlab.network.state import NetworkState
from netlab.network.topology import Topology
from netlab.scenarios.schema import (
    DeviceSpec,
    InitialInterfaceSpec,
    Scenario,
    TopologySpec,
)

_YAML_SUFFIXES = (".yaml", ".yml")


def _scenario_error(path: Path | None, message: str) -> ScenarioError:
    prefix = f"{path}: " if path is not None else ""
    return ScenarioError(f"{prefix}{message}")


def _format_validation_error(exc: ValidationError, indent: str = "  ") -> str:
    lines = []
    for error in exc.errors()[:10]:
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"{indent}{location}: {error['msg']}")
    remaining = len(exc.errors()) - 10
    if remaining > 0:
        lines.append(f"{indent}... and {remaining} more error(s)")
    return "\n".join(lines)


def parse_scenario(data: Any, path: Path | None = None) -> Scenario:
    """Validate raw scenario data against the schema."""
    if not isinstance(data, dict):
        raise _scenario_error(path, "scenario must be a YAML mapping at the top level")
    try:
        return Scenario.model_validate(data)
    except ValidationError as exc:
        raise _scenario_error(
            path, f"scenario validation failed:\n{_format_validation_error(exc)}"
        ) from exc


def load_scenario_file(path: str | Path) -> Scenario:
    """Load and validate a single scenario YAML file."""
    file_path = Path(path)
    if not file_path.is_file():
        raise _scenario_error(file_path, "scenario file not found")
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise _scenario_error(file_path, f"invalid YAML: {exc}") from exc
    return parse_scenario(data, file_path)


def discover_scenarios(
    directory: str | Path,
) -> tuple[list[tuple[Path, Scenario]], list[tuple[Path, str]]]:
    """Discover scenario files under ``directory``.

    Returns successfully loaded scenarios and per-file error messages for
    invalid ones, so a single broken file cannot hide the rest of the library.
    """
    root = Path(directory)
    loaded: list[tuple[Path, Scenario]] = []
    errors: list[tuple[Path, str]] = []
    if not root.is_dir():
        return loaded, errors
    for file_path in sorted(
        child for child in root.rglob("*") if child.suffix in _YAML_SUFFIXES
    ):
        try:
            loaded.append((file_path, load_scenario_file(file_path)))
        except ScenarioError as exc:
            errors.append((file_path, str(exc)))
    return loaded, errors


# --- Initial state builder ------------------------------------------------------


def _instantiate_device(spec: DeviceSpec) -> Device:
    constructors: dict[DeviceType, type[Device]] = {
        DeviceType.ROUTER: Router,
        DeviceType.SWITCH: Switch,
        DeviceType.PC: PC,
        DeviceType.SERVER: Server,
    }
    device = constructors[spec.type](hostname=spec.name)
    for interface_spec in spec.interfaces:
        device.add_interface(
            Interface(
                name=interface_spec.name,
                parent=interface_spec.parent,
                encapsulation_vlan=interface_spec.encapsulation_vlan,
                description=interface_spec.description,
            )
        )
    return device


def _endpoint_of(ref: str) -> Endpoint:
    hostname, _, interface = ref.rpartition(":")
    return Endpoint(device=hostname, interface=canonical_interface_name(interface))


def _build_topology(spec: TopologySpec) -> Topology:
    devices: dict[str, Device] = {}
    for device_spec in spec.devices:
        devices[device_spec.name] = _instantiate_device(device_spec)
    links = [
        Link(source=_endpoint_of(link_spec.source.ref),
             destination=_endpoint_of(link_spec.destination.ref))
        for link_spec in spec.links
    ]
    try:
        return Topology(devices=devices, links=links)
    except Exception as exc:
        raise _scenario_error(None, str(exc)) from exc


def _apply_initial_interfaces(state: NetworkState, specs: list[InitialInterfaceSpec]) -> None:
    for spec in specs:
        try:
            device = state.device(spec.device)
            interface = device.get_interface(spec.interface)
            if spec.description is not None:
                interface.description = spec.description
            if spec.shutdown is not None:
                interface.admin_enabled = not spec.shutdown
            if spec.switchport is not None:
                if spec.switchport.mode is not None:
                    interface.switchport_mode = SwitchportMode(spec.switchport.mode)
                if spec.switchport.access_vlan is not None:
                    interface.access_vlan = spec.switchport.access_vlan
                if spec.switchport.native_vlan is not None:
                    interface.native_vlan = spec.switchport.native_vlan
                if spec.switchport.allowed is not None:
                    interface.allowed_vlans = (
                        set(parse_vlan_list(spec.switchport.allowed))
                        if spec.switchport.allowed
                        else set()
                    )
            if spec.ip is not None:
                interface.set_ip(spec.ip.address, spec.ip.prefix)
        except Exception as exc:
            raise _scenario_error(
                None, f"initial_state: {spec.device}:{spec.interface}: {exc}"
            ) from exc


def build_initial_state(scenario: Scenario) -> NetworkState:
    """Build the scenario's starting :class:`NetworkState`.

    Instantiates devices from the topology spec, validates links against real
    interfaces, then applies ``initial_state`` (VLANs, interface configuration,
    host gateways) for troubleshooting-style labs.
    """
    try:
        topology = _build_topology(scenario.topology)
    except ScenarioError:
        raise
    except Exception as exc:
        raise _scenario_error(None, str(exc)) from exc

    state = NetworkState(topology=topology)
    initial = scenario.initial_state

    if initial is not None:
        for vlan_spec in initial.vlans:
            try:
                device = state.device(vlan_spec.device)
                if not isinstance(device, Switch):
                    raise TypeError(f"{vlan_spec.device} is not a switch")
                if vlan_spec.vlan_id in device.vlans:
                    if vlan_spec.name is not None:
                        device.set_vlan_name(vlan_spec.vlan_id, vlan_spec.name)
                else:
                    device.create_vlan(vlan_spec.vlan_id, vlan_spec.name)
            except Exception as exc:
                raise _scenario_error(
                    None,
                    f"initial_state: VLAN {vlan_spec.vlan_id} on {vlan_spec.device}: {exc}",
                ) from exc

        _apply_initial_interfaces(state, initial.interfaces)

        for host_spec in initial.hosts:
            try:
                device = state.device(host_spec.device)
                if not isinstance(device, Host):
                    raise TypeError(f"{host_spec.device} is not an end host")
                device.default_gateway = host_spec.gateway
            except Exception as exc:
                raise _scenario_error(
                    None, f"initial_state: gateway on {host_spec.device}: {exc}"
                ) from exc

    return state


def validate_requirements(scenario: Scenario) -> list[str]:
    """Semantic checks beyond the schema; returns human-readable problems.

    Checks that requirement devices/interfaces exist in the topology so
    scenarios cannot ship with ungradeable rules. The grading engine performs
    the actual state comparisons at assessment time.
    """
    problems: list[str] = []
    devices = {spec.name: spec for spec in scenario.topology.devices}
    interface_names: dict[str, set[str]] = {
        name: {iface.name for iface in spec.interfaces} for name, spec in devices.items()
    }

    for requirement in scenario.requirements:
        device = getattr(requirement, "device", None)
        if device is None:
            continue
        if device not in devices:
            problems.append(f"{requirement.id}: unknown device {device!r}")
            continue
        interface = getattr(requirement, "interface", None)
        if interface is None:
            continue
        # Subinterfaces (names containing '.') are created by the student during
        # the lab, so only their declared parent is verified here.
        if "." not in interface and interface not in interface_names[device]:
            problems.append(f"{requirement.id}: {device} has no interface {interface!r}")

    for check in scenario.verification:
        if check.source not in devices:
            problems.append(
                f"verification {check.id}: unknown source device {check.source!r}"
            )

    return problems


__all__ = [
    "build_initial_state",
    "discover_scenarios",
    "load_scenario_file",
    "parse_scenario",
    "validate_requirements",
]
