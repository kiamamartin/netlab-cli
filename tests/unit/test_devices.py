"""Unit tests for Device, Switch, Router, Host, and configuration snapshots."""

from ipaddress import IPv4Address

import pytest
from pydantic import ValidationError

from netlab.core.enums import DeviceType
from netlab.core.errors import (
    DuplicateVlanError,
    InvalidVlanIdError,
    UnknownInterfaceError,
    UnknownVlanError,
)
from netlab.devices.base import Config
from netlab.devices.host import PC, Host, Server
from netlab.devices.interface import Interface
from netlab.devices.router import Router
from netlab.devices.switch import Switch
from netlab.devices.vlan import VLAN


def make_switch() -> Switch:
    switch = Switch(hostname="SW1")
    for name in ("GigabitEthernet0/1", "GigabitEthernet0/2", "GigabitEthernet0/3"):
        switch.add_interface(Interface(name=name))
    return switch


class TestHostname:
    @pytest.mark.parametrize("hostname", ["SW1", "PC-A", "R", "r1-core-2"])
    def test_valid_hostnames(self, hostname: str) -> None:
        assert Switch(hostname=hostname).hostname == hostname

    @pytest.mark.parametrize("hostname", ["1SW", "SW_1", "-SW", "SW-", "x" * 64, "   "])
    def test_invalid_hostnames(self, hostname: str) -> None:
        with pytest.raises(ValidationError):
            Switch(hostname=hostname)


class TestSwitchVlans:
    def test_switch_starts_with_vlan_1(self) -> None:
        switch = Switch(hostname="SW1")
        assert switch.vlans[1] == VLAN(id=1, name="default")

    def test_create_and_get_vlan(self) -> None:
        switch = Switch(hostname="SW1")
        vlan = switch.create_vlan(10, "ADMIN")
        assert switch.get_vlan(10) is vlan
        assert vlan.name == "ADMIN"

    def test_create_duplicate_vlan_raises(self) -> None:
        switch = make_switch()
        switch.create_vlan(10)
        with pytest.raises(DuplicateVlanError):
            switch.create_vlan(10)

    def test_get_unknown_vlan_raises(self) -> None:
        with pytest.raises(UnknownVlanError):
            make_switch().get_vlan(42)

    def test_get_vlan_rejects_invalid_id(self) -> None:
        with pytest.raises(InvalidVlanIdError):
            make_switch().get_vlan(5000)

    def test_set_vlan_name(self) -> None:
        switch = Switch(hostname="SW1")
        switch.create_vlan(20, "TEMP")
        switch.set_vlan_name(20, "SALES")
        assert switch.get_vlan(20).name == "SALES"


class TestInterfaces:
    def test_get_interface_accepts_abbreviations(self) -> None:
        switch = make_switch()
        assert switch.get_interface("gi0/2").name == "GigabitEthernet0/2"

    def test_get_unknown_interface_raises(self) -> None:
        with pytest.raises(UnknownInterfaceError):
            make_switch().get_interface("gi0/9")

    def test_add_duplicate_interface_raises(self) -> None:
        switch = make_switch()
        with pytest.raises(ValueError, match="already exists"):
            switch.add_interface(Interface(name="GigabitEthernet0/1"))

    def test_subinterface_requires_existing_parent(self) -> None:
        router = Router(hostname="R1")
        with pytest.raises(ValueError, match="parent"):
            router.add_interface(
                Interface(name="GigabitEthernet0/0/0.10", parent="GigabitEthernet0/0/0")
            )
        router.add_interface(Interface(name="GigabitEthernet0/0/0"))
        sub = router.add_interface(
            Interface(
                name="GigabitEthernet0/0/0.10",
                parent="GigabitEthernet0/0/0",
                encapsulation_vlan=10,
            )
        )
        assert router.get_interface("g0/0/0.10") is sub
        assert router.interface_names == [
            "GigabitEthernet0/0/0",
            "GigabitEthernet0/0/0.10",
        ]

    def test_mismatched_interface_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Switch(hostname="SW1", interfaces={"Gi0/1": Interface(name="GigabitEthernet0/1")})


class TestHosts:
    def test_host_types(self) -> None:
        assert PC(hostname="PC-A").device_type is DeviceType.PC
        assert Server(hostname="SRV1").device_type is DeviceType.SERVER
        assert isinstance(PC(hostname="PC-A"), Host)

    def test_default_gateway(self) -> None:
        pc = PC(hostname="PC-A", default_gateway="192.168.10.1")
        assert pc.default_gateway == IPv4Address("192.168.10.1")


class TestStartupConfig:
    def test_copy_running_to_startup_snapshots_state(self) -> None:
        switch = make_switch()
        switch.get_interface("gi0/1").description = "Uplink-to-R1"
        snapshot = switch.copy_running_to_startup()
        assert isinstance(snapshot, Config)
        assert switch.startup_config is snapshot
        assert (
            snapshot.snapshot["interfaces"]["GigabitEthernet0/1"]["description"]
            == "Uplink-to-R1"
        )

    def test_startup_config_is_isolated_from_later_mutation(self) -> None:
        switch = make_switch()
        switch.copy_running_to_startup()
        frozen = switch.startup_config.snapshot

        switch.get_interface("gi0/1").description = "changed later"
        switch.create_vlan(10, "ADMIN")

        restored = Switch.model_validate(frozen)
        assert restored.get_interface("gi0/1").description == ""
        assert 10 not in restored.vlans
        assert restored.vlans[1].name == "default"

    def test_capture_excludes_startup_config(self) -> None:
        switch = make_switch()
        switch.copy_running_to_startup()
        switch.copy_running_to_startup()
        assert "startup_config" not in switch.startup_config.snapshot

    def test_config_snapshot_is_frozen(self) -> None:
        config = Config.capture(Switch(hostname="SW1"))
        with pytest.raises(ValidationError):
            config.snapshot = {}  # type: ignore[misc]
