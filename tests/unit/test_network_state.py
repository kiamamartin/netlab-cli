"""Unit tests for the authoritative NetworkState aggregate."""

from netlab.network.state import NetworkState
from tests.unit.test_topology import build_topology


def test_state_delegates_to_topology() -> None:
    state = NetworkState(topology=build_topology())
    assert state.device("SW1").hostname == "SW1"
    assert state.get_interface("PC-A", "Ethernet0").name == "Ethernet0"
    assert state.interface_is_up("R1", "g0/0/0") is True
    assert state.link_for("R1", "g0/0/0") is not None


def test_connected_endpoint_via_state() -> None:
    state = NetworkState(topology=build_topology())
    far_end = state.connected_endpoint("SW1", "gi0/1")
    assert far_end is not None
    assert far_end.device == "R1"
    assert far_end.ref == "R1:GigabitEthernet0/0/0"
