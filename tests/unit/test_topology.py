"""Unit tests for Topology validation and derived operational state."""

import pytest

from netlab.core.enums import LinkStatus
from netlab.core.errors import (
    DuplicateEndpointError,
    UnknownDeviceError,
    UnknownInterfaceError,
)
from netlab.devices.host import PC
from netlab.devices.interface import Interface
from netlab.devices.router import Router
from netlab.devices.switch import Switch
from netlab.network.link import Endpoint, Link
from netlab.network.topology import Topology


def build_topology() -> Topology:
    """The milestone-1 router-on-a-stick topology (doc §74)."""
    r1 = Router(hostname="R1")
    r1.add_interface(Interface(name="GigabitEthernet0/0/0"))
    r1.add_interface(
        Interface(
            name="GigabitEthernet0/0/0.10",
            parent="GigabitEthernet0/0/0",
            encapsulation_vlan=10,
            ip_address="192.168.10.1",
            prefix_length=24,
        )
    )

    sw1 = Switch(hostname="SW1")
    for name in ("GigabitEthernet0/1", "GigabitEthernet0/2", "GigabitEthernet0/3"):
        sw1.add_interface(Interface(name=name))

    pc_a = PC(hostname="PC-A")
    pc_a.add_interface(Interface(name="Ethernet0"))
    pc_b = PC(hostname="PC-B")
    pc_b.add_interface(Interface(name="Ethernet0"))

    return Topology(
        devices={"R1": r1, "SW1": sw1, "PC-A": pc_a, "PC-B": pc_b},
        links=[
            Link(
                source=Endpoint(device="R1", interface="GigabitEthernet0/0/0"),
                destination=Endpoint(device="SW1", interface="GigabitEthernet0/1"),
            ),
            Link(
                source=Endpoint(device="SW1", interface="GigabitEthernet0/2"),
                destination=Endpoint(device="PC-A", interface="Ethernet0"),
            ),
            Link(
                source=Endpoint(device="SW1", interface="GigabitEthernet0/3"),
                destination=Endpoint(device="PC-B", interface="Ethernet0"),
            ),
        ],
    )


def test_device_lookup_and_unknown_device() -> None:
    topology = build_topology()
    assert topology.device("SW1") is topology.devices["SW1"]
    with pytest.raises(UnknownDeviceError):
        topology.device("SW2")


def test_link_lookup_accepts_abbreviations() -> None:
    topology = build_topology()
    link = topology.link_for("R1", "g0/0/0")
    assert link is not None
    assert link.destination.device == "SW1"
    assert topology.link_for("PC-A", "Ethernet0") is not None
    # Subinterfaces have no link of their own; the physical parent carries it.
    assert topology.link_for("R1", "g0/0/0.10") is None


def test_connected_endpoint() -> None:
    topology = build_topology()
    far_end = topology.connected_endpoint("SW1", "gi0/1")
    assert far_end is not None
    assert far_end.device == "R1"
    assert topology.connected_endpoint("SW1", "gi0/4") is None


def test_operational_state_derivation() -> None:
    topology = build_topology()
    assert topology.interface_is_up("R1", "g0/0/0") is True
    assert topology.interface_is_up("SW1", "gi0/2") is True
    # Subinterface follows its physical parent.
    assert topology.interface_is_up("R1", "g0/0/0.10") is True


def test_admin_down_propagates_to_subinterface() -> None:
    topology = build_topology()
    topology.device("R1").get_interface("g0/0/0").admin_enabled = False
    assert topology.interface_is_up("R1", "g0/0/0") is False
    assert topology.interface_is_up("R1", "g0/0/0.10") is False


def test_link_down_propagates() -> None:
    topology = build_topology()
    topology.links[1].status = LinkStatus.DOWN
    assert topology.interface_is_up("SW1", "gi0/2") is False
    assert topology.interface_is_up("PC-A", "eth0") is False
    assert topology.interface_is_up("SW1", "gi0/3") is True


def test_unconnected_interface_is_down() -> None:
    topology = build_topology()
    topology.device("SW1").add_interface(Interface(name="GigabitEthernet0/4"))
    assert topology.interface_is_up("SW1", "gi0/4") is False


def test_link_to_unknown_device_rejected() -> None:
    topology = build_topology()
    with pytest.raises(UnknownDeviceError):
        Topology(
            devices=topology.devices,
            links=[
                Link(
                    source=Endpoint(device="SW2", interface="GigabitEthernet0/1"),
                    destination=Endpoint(device="SW1", interface="GigabitEthernet0/1"),
                )
            ],
        )


def test_link_to_unknown_interface_rejected() -> None:
    topology = build_topology()
    with pytest.raises(UnknownInterfaceError):
        Topology(
            devices=topology.devices,
            links=[
                Link(
                    source=Endpoint(device="SW1", interface="GigabitEthernet0/9"),
                    destination=Endpoint(device="PC-A", interface="Ethernet0"),
                )
            ],
        )


def test_interface_on_two_links_rejected() -> None:
    topology = build_topology()
    with pytest.raises(DuplicateEndpointError):
        Topology(
            devices=topology.devices,
            links=[
                Link(
                    source=Endpoint(device="SW1", interface="GigabitEthernet0/1"),
                    destination=Endpoint(device="R1", interface="GigabitEthernet0/0/0"),
                ),
                Link(
                    source=Endpoint(device="SW1", interface="GigabitEthernet0/1"),
                    destination=Endpoint(device="PC-A", interface="Ethernet0"),
                ),
            ],
        )


def test_self_loop_rejected() -> None:
    topology = build_topology()
    with pytest.raises(DuplicateEndpointError):
        Topology(
            devices=topology.devices,
            links=[
                Link(
                    source=Endpoint(device="SW1", interface="GigabitEthernet0/1"),
                    destination=Endpoint(device="SW1", interface="GigabitEthernet0/1"),
                )
            ],
        )
