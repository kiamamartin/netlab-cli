"""Unit tests for the ASCII topology diagram renderer (netlab.diagram).

Covers geometry (box layout, cable rows, junction glyphs), determinism, the
no-links and unknown-endpoint edge cases, and rendering of every shipped
scenario.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netlab.core.enums import DeviceType
from netlab.diagram import render_diagram
from netlab.scenarios import discover_scenarios, load_scenario_file
from netlab.scenarios.schema import (
    DeviceSpec,
    EndpointSpec,
    InterfaceSpec,
    LinkSpec,
    TopologySpec,
)

PROJECT_ROOT = Path(__file__).parents[2]
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"


def _spec(
    name: str, kind: DeviceType, *interfaces: str
) -> DeviceSpec:
    return DeviceSpec(
        name=name,
        type=kind,
        interfaces=[InterfaceSpec(name=iface) for iface in interfaces],
    )


def test_two_device_golden_rendering() -> None:
    """Exact output for the simplest topology: one link between two boxes.

    Geometry derived by hand: inner width 8 (kind label 'router' + 2 padding),
    box width 10, gap 2, so box centres sit at columns 5 and 17 and the single
    cable row draws ``└ + 11─ + ┘`` between them.
    """
    topology = TopologySpec(
        devices=[
            _spec("R1", DeviceType.ROUTER, "GigabitEthernet0/0/0"),
            _spec("SW1", DeviceType.SWITCH, "GigabitEthernet0/1"),
        ],
        links=[
            LinkSpec(
                source=EndpointSpec(ref="R1:Gi0/0/0"),
                destination=EndpointSpec(ref="SW1:Gi0/1"),
            )
        ],
    )
    expected = "\n".join(
        [
            "┌────────┐  ┌────────┐",
            "│   R1   │  │  SW1   │",
            "│ router │  │ switch │",
            "└────┬───┘  └────┬───┘",
            "     └───────────┘",
            "",
            "Links:",
            "  R1 Gi0/0/0 ↔ SW1 Gi0/1",
        ]
    )
    assert render_diagram(topology) == expected


def test_no_trailing_whitespace_and_deterministic() -> None:
    topology = TopologySpec(
        devices=[
            _spec("R1", DeviceType.ROUTER, "GigabitEthernet0/0/0"),
            _spec("SW1", DeviceType.SWITCH, "GigabitEthernet0/1"),
            _spec("PC-A", DeviceType.PC, "Ethernet0"),
        ],
        links=[
            LinkSpec(
                source=EndpointSpec(ref="R1:Gi0/0/0"),
                destination=EndpointSpec(ref="SW1:Gi0/1"),
            ),
            LinkSpec(
                source=EndpointSpec(ref="SW1:Gi0/1"),
                destination=EndpointSpec(ref="PC-A:Ethernet0"),
            ),
        ],
    )
    first = render_diagram(topology)
    assert render_diagram(topology) == first  # deterministic
    assert first.startswith("┌")
    assert all(line == line.rstrip() for line in first.splitlines())


def test_topology_without_links_renders_boxes_only() -> None:
    topology = TopologySpec(devices=[_spec("SW1", DeviceType.SWITCH, "GigabitEthernet0/1")])
    diagram = render_diagram(topology)
    assert "SW1" in diagram
    assert "switch" in diagram
    assert "Links:" not in diagram
    assert "┬" not in diagram  # no cable stubs without links


def test_unknown_endpoint_device_raises() -> None:
    """A link to a device that isn't in the topology fails loudly."""
    topology = TopologySpec(
        devices=[_spec("SW1", DeviceType.SWITCH, "GigabitEthernet0/1")],
        links=[
            LinkSpec(
                source=EndpointSpec(ref="SW1:Gi0/1"),
                destination=EndpointSpec(ref="GHOST:Gi0/1"),
            )
        ],
    )
    with pytest.raises(ValueError, match="unknown device"):
        render_diagram(topology)


def test_every_shipped_scenario_renders() -> None:
    loaded, errors = discover_scenarios(SCENARIOS_DIR)
    assert errors == []
    assert loaded, "expected at least one shipped scenario"
    for _path, scenario in loaded:
        diagram = render_diagram(scenario.topology)
        for device in scenario.topology.devices:
            assert device.name in diagram
            assert device.type.value in diagram
        for link in scenario.topology.links:
            source_host, _sep, source_iface = link.source.ref.rpartition(":")
            destination_host, _sep, destination_iface = link.destination.ref.rpartition(":")
            assert (
                f"{source_host} {source_iface} ↔ "
                f"{destination_host} {destination_iface}"
            ) in diagram
        assert all(line == line.rstrip() for line in diagram.splitlines())


def test_trunk_scenario_draws_crossing_glyph() -> None:
    """SW2's drop must cross the SW1—PC-A cable as ``┼``, not a false junction.

    ccna-trunk-001 links (YAML order): SW1—SW2 (depth 0), SW1—PC-A (depth 1),
    SW2—PC-B (depth 2): the depth-1 cable spans from SW1 past SW2's centre,
    and SW2's drop continues below it — a genuine crossing.
    """
    scenario = load_scenario_file(SCENARIOS_DIR / "trunking" / "ccna-trunk-001.yaml")
    diagram = render_diagram(scenario.topology)
    assert "┼" in diagram


def test_shared_device_drop_renders_tee_junction() -> None:
    """A box with a deeper link turns with ``├``/``┤``, not ``└``/``┘``."""
    topology = TopologySpec(
        devices=[
            _spec("SW1", DeviceType.SWITCH, "GigabitEthernet0/1", "GigabitEthernet0/2"),
            _spec("PC-A", DeviceType.PC, "Ethernet0"),
            _spec("PC-B", DeviceType.PC, "Ethernet0"),
        ],
        links=[
            LinkSpec(
                source=EndpointSpec(ref="SW1:Gi0/1"),
                destination=EndpointSpec(ref="PC-A:Ethernet0"),
            ),
            LinkSpec(
                source=EndpointSpec(ref="SW1:Gi0/2"),
                destination=EndpointSpec(ref="PC-B:Ethernet0"),
            ),
        ],
    )
    diagram = render_diagram(topology)
    # Depth-0 row: SW1's drop continues to depth 1 → ├.
    assert "├" in diagram
