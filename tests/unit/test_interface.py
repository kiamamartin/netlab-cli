"""Unit tests for interface naming, VLAN lists, and L2/L3 attributes."""

from ipaddress import IPv4Address

import pytest
from pydantic import ValidationError

from netlab.core.enums import SwitchportMode
from netlab.core.errors import (
    InvalidInterfaceError,
    InvalidVlanIdError,
    InvalidVlanListError,
)
from netlab.devices.interface import (
    ALL_VLANS,
    Interface,
    canonical_interface_name,
    format_vlan_list,
    parse_vlan_list,
    short_interface_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GigabitEthernet0/1", "GigabitEthernet0/1"),
        ("gi0/1", "GigabitEthernet0/1"),
        ("gig 0/2", "GigabitEthernet0/2"),
        ("GigabitEthernet0/0/0.10", "GigabitEthernet0/0/0.10"),
        ("vlan10", "Vlan10"),
        ("Ethernet0", "Ethernet0"),
        ("fa0/5", "FastEthernet0/5"),
        ("te1/0/1", "TenGigabitEthernet1/0/1"),
    ],
)
def test_canonical_interface_name_accepts_abbreviations(raw: str, expected: str) -> None:
    assert canonical_interface_name(raw) == expected


@pytest.mark.parametrize(
    "raw", ["gigabitethernet", "foo0/1", "gi/", "0/1", "gi0/1.2.3", "gi0/1/"]
)
def test_canonical_interface_name_rejects_invalid_tokens(raw: str) -> None:
    with pytest.raises(InvalidInterfaceError):
        canonical_interface_name(raw)


def test_short_interface_name() -> None:
    assert short_interface_name("GigabitEthernet0/1") == "Gi0/1"
    assert short_interface_name("GigabitEthernet0/0/0.10") == "Gi0/0/0.10"
    assert short_interface_name("FastEthernet0/5") == "Fa0/5"


def test_parse_vlan_list_handles_ranges_and_lists() -> None:
    assert parse_vlan_list("10,20-22") == frozenset({10, 20, 21, 22})
    assert parse_vlan_list("1-3,5") == frozenset({1, 2, 3, 5})
    assert parse_vlan_list(" 10 , 20 ") == frozenset({10, 20})


@pytest.mark.parametrize("raw", ["10,,20", "20-10", "4095", "abc", "10-20-30"])
def test_parse_vlan_list_rejects_invalid_input(raw: str) -> None:
    with pytest.raises((InvalidVlanListError, InvalidVlanIdError)):
        parse_vlan_list(raw)


def test_format_vlan_list_compresses_ranges() -> None:
    assert format_vlan_list([20, 10, 22, 21]) == "10,20-22"
    assert format_vlan_list(ALL_VLANS) == "1-4094"
    assert format_vlan_list([]) == ""


def test_format_vlan_list_round_trips() -> None:
    original = {1, 5, 6, 7, 20, 999, 4094}
    assert parse_vlan_list(format_vlan_list(original)) == frozenset(original)


def test_interface_defaults() -> None:
    iface = Interface(name="GigabitEthernet0/1")
    assert iface.admin_enabled is True
    assert iface.switchport_mode is SwitchportMode.NONE
    assert iface.access_vlan == 1
    assert iface.native_vlan == 1
    assert iface.allowed_vlans == set(ALL_VLANS)
    assert iface.has_ip is False
    assert iface.cidr is None
    assert iface.is_subinterface is False


def test_interface_canonicalizes_name_on_construction() -> None:
    assert Interface(name="gi0/1").name == "GigabitEthernet0/1"


def test_interface_ip_configuration() -> None:
    iface = Interface(name="GigabitEthernet0/0", ip_address="192.168.10.1", prefix_length=24)
    assert iface.ip_address == IPv4Address("192.168.10.1")
    assert iface.has_ip is True
    assert iface.cidr == "192.168.10.1/24"


def test_ip_requires_prefix_pair() -> None:
    with pytest.raises(ValidationError):
        Interface(name="GigabitEthernet0/0", ip_address="192.168.10.1")
    with pytest.raises(ValidationError):
        Interface(name="GigabitEthernet0/0", prefix_length=24)


def test_set_ip_is_atomic() -> None:
    iface = Interface(name="GigabitEthernet0/0")
    iface.set_ip("192.168.10.1", 24)
    assert iface.ip_address == IPv4Address("192.168.10.1")
    assert iface.prefix_length == 24
    assert iface.cidr == "192.168.10.1/24"


def test_set_ip_accepts_ipv4address_and_revalidates() -> None:
    iface = Interface(name="GigabitEthernet0/0")
    iface.set_ip(IPv4Address("10.0.0.1"), 30)
    assert iface.cidr == "10.0.0.1/30"
    with pytest.raises(ValueError, match="prefix"):
        iface.set_ip("10.0.0.1", 33)


def test_clear_ip_is_atomic() -> None:
    iface = Interface(name="GigabitEthernet0/0", ip_address="192.168.10.1", prefix_length=24)
    iface.clear_ip()
    assert iface.has_ip is False
    assert iface.ip_address is None
    assert iface.prefix_length is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "Gi0/1", "access_vlan": 4095},
        {"name": "Gi0/1", "native_vlan": 0},
        {"name": "Gi0/1", "ip_address": "192.168.10.1", "prefix_length": 33},
        {"name": "Gi0/1", "unknown_field": 1},
    ],
)
def test_interface_rejects_invalid_configuration(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        Interface(**kwargs)


def test_subinterface_with_parent_and_encapsulation() -> None:
    sub = Interface(
        name="GigabitEthernet0/0/0.10",
        parent="GigabitEthernet0/0/0",
        encapsulation_vlan=10,
    )
    assert sub.is_subinterface is True
    assert sub.parent == "GigabitEthernet0/0/0"
    assert sub.encapsulation_vlan == 10


def test_subinterface_without_parent_rejected() -> None:
    with pytest.raises(ValidationError):
        Interface(name="GigabitEthernet0/0/0.10")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "GigabitEthernet0/0/0.10", "parent": "GigabitEthernet0/0/0.20"},
        {"name": "GigabitEthernet0/0", "encapsulation_vlan": 10},
    ],
)
def test_invalid_subinterface_relationships(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        Interface(**kwargs)
