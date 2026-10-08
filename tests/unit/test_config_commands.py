"""Unit tests for stage-4 configuration commands mutating network state."""

from ipaddress import IPv4Address
from pathlib import Path

from netlab.core.enums import SwitchportMode, TrunkEncapsulation
from netlab.devices.host import Host
from netlab.devices.switch import Switch
from netlab.ios.machine import CliSession
from netlab.scenarios import build_initial_state, load_scenario_file
from tests.unit.test_topology import build_topology

PROJECT_ROOT = Path(__file__).parents[2]
FIRST_SCENARIO = PROJECT_ROOT / "scenarios" / "vlan" / "ccna-vlan-001.yaml"


def make_switch_session() -> CliSession:
    session = CliSession(build_topology(), "SW1")
    session.execute("enable")
    session.execute("configure terminal")
    return session


def make_router_session() -> CliSession:
    session = CliSession(build_topology(), "R1")
    session.execute("enable")
    session.execute("configure terminal")
    return session


def make_host_session() -> CliSession:
    session = CliSession(build_topology(), "PC-A")
    session.execute("enable")
    return session


class TestSwitchportCommands:
    def test_access_mode_and_vlan(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/2")
        session.execute("switchport mode access")
        session.execute("switchport access vlan 10")
        interface = session.device.get_interface("gi0/2")
        assert interface.switchport_mode is SwitchportMode.ACCESS
        assert interface.access_vlan == 10

    def test_access_vlan_auto_creates_missing_vlan_b6(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/3")
        session.execute("switchport access vlan 20")
        switch = session.device
        assert isinstance(switch, Switch)
        assert 20 in switch.vlans  # B6: mirrored from real IOS
        assert switch.get_interface("gi0/3").access_vlan == 20

    def test_access_vlan_does_not_imply_mode(self) -> None:
        """Grading integrity: assigning an access VLAN is not 'switchport mode access'."""
        session = make_switch_session()
        session.execute("interface gi0/2")
        session.execute("switchport access vlan 10")
        assert session.device.get_interface("gi0/2").switchport_mode is not SwitchportMode.ACCESS

    def test_trunk_mode_allowed_and_native(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/1")
        session.execute("switchport mode trunk")
        session.execute("switchport trunk allowed vlan 10,20")
        session.execute("switchport trunk native vlan 1")
        interface = session.device.get_interface("gi0/1")
        assert interface.switchport_mode is SwitchportMode.TRUNK
        assert interface.allowed_vlans == {10, 20}
        assert interface.native_vlan == 1

    def test_trunk_encapsulation_dot1q(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/1")
        session.execute("switchport mode trunk")
        session.execute("switchport trunk encapsulation dot1q")
        interface = session.device.get_interface("gi0/1")
        assert interface.switchport_mode is SwitchportMode.TRUNK
        assert interface.trunk_encapsulation is TrunkEncapsulation.DOT1Q

    def test_trunk_encapsulation_prefix_and_case_insensitive(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/1")
        session.execute("switchport mode trunk")
        output = session.execute("SWITCHPORT TRUNK ENCAPSULATION DOT1Q")
        assert output == ""
        assert (
            session.device.get_interface("gi0/1").trunk_encapsulation
            is TrunkEncapsulation.DOT1Q
        )

    def test_trunk_encapsulation_requires_trunk_mode(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/1")
        output = session.execute("switchport trunk encapsulation dot1q")
        assert output.startswith("%")
        assert (
            session.device.get_interface("gi0/1").trunk_encapsulation
            is TrunkEncapsulation.NEGOTIATE
        )

    def test_trunk_encapsulation_unknown_kind_rejected(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/1")
        session.execute("switchport mode trunk")
        output = session.execute("switchport trunk encapsulation atm")
        assert output.startswith("% Invalid input")

    def test_trunk_encapsulation_missing_on_router(self) -> None:
        session = make_router_session()
        session.execute("interface g0/0/0")
        output = session.execute("switchport trunk encapsulation dot1q")
        assert output.startswith("% Invalid input")

    def test_trunk_allowed_vlan_verbs(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/1")
        session.execute("switchport mode trunk")
        session.execute("switchport trunk allowed vlan 10,20")
        session.execute("switchport trunk allowed vlan add 30")
        assert session.device.get_interface("gi0/1").allowed_vlans == {10, 20, 30}
        session.execute("switchport trunk allowed vlan remove 10")
        assert session.device.get_interface("gi0/1").allowed_vlans == {20, 30}
        session.execute("switchport trunk allowed vlan all")
        assert len(session.device.get_interface("gi0/1").allowed_vlans) == 4094
        session.execute("switchport trunk allowed vlan except 10,20")
        allowed = session.device.get_interface("gi0/1").allowed_vlans
        assert 10 not in allowed and 20 not in allowed and 30 in allowed

    def test_switchport_commands_missing_on_router(self) -> None:
        session = make_router_session()
        session.execute("interface g0/0/0")
        output = session.execute("switchport mode trunk")
        assert output.startswith("% Invalid input")


class TestIpCommands:
    def test_router_interface_ip(self) -> None:
        session = make_router_session()
        session.execute("interface g0/0/0")
        session.execute("ip address 192.168.1.1 255.255.255.0")
        interface = session.device.get_interface("g0/0/0")
        assert interface.ip_address == IPv4Address("192.168.1.1")
        assert interface.prefix_length == 24

    def test_router_subinterface_ip_and_encapsulation(self) -> None:
        session = make_router_session()
        session.execute("interface g0/0/0.10")
        session.execute("encapsulation dot1q 10")
        session.execute("ip address 192.168.10.1 255.255.255.0")
        interface = session.device.get_interface("g0/0/0.10")
        assert interface.encapsulation_vlan == 10
        assert interface.cidr == "192.168.10.1/24"

    def test_encapsulation_on_physical_interface_rejected(self) -> None:
        session = make_router_session()
        session.execute("interface g0/0/0")
        output = session.execute("encapsulation dot1q 10")
        assert output.startswith("%")

    def test_invalid_mask_rejected(self) -> None:
        session = make_router_session()
        session.execute("interface g0/0/0")
        output = session.execute("ip address 192.168.1.1 255.0.255.0")
        assert output.startswith("% Invalid input")
        assert session.device.get_interface("g0/0/0").has_ip is False

    def test_no_ip_address_clears(self) -> None:
        session = make_router_session()
        session.execute("interface g0/0/0")
        session.execute("ip address 10.0.0.1 255.0.0.0")
        session.execute("no ip address")
        assert session.device.get_interface("g0/0/0").has_ip is False


class TestDescriptionAndShutdown:
    def test_description_with_spaces(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/1")
        session.execute("description Uplink to R1")
        assert session.device.get_interface("gi0/1").description == "Uplink to R1"

    def test_shutdown_and_no_shutdown(self) -> None:
        session = make_switch_session()
        session.execute("interface gi0/2")
        session.execute("shutdown")
        assert session.device.get_interface("gi0/2").admin_enabled is False
        session.execute("no shutdown")
        assert session.device.get_interface("gi0/2").admin_enabled is True


class TestHostCommands:
    def test_host_ip_and_gateway_b9(self) -> None:
        session = make_host_session()
        session.execute("ip address 192.168.10.10 255.255.255.0")
        session.execute("ip default-gateway 192.168.10.1")
        host = session.device
        assert isinstance(host, Host)
        assert host.get_interface("Ethernet0").cidr == "192.168.10.10/24"
        assert host.default_gateway == IPv4Address("192.168.10.1")

    def test_host_commands_missing_on_switch(self) -> None:
        session = make_switch_session()  # global config mode; exit to EXEC first
        session.execute("end")
        output = session.execute("ip default-gateway 192.168.1.1")
        assert output.startswith("% Invalid input")

    def test_host_no_ip_address(self) -> None:
        session = make_host_session()
        session.execute("ip address 192.168.10.10 255.255.255.0")
        session.execute("no ip address")
        assert session.device.get_interface("Ethernet0").has_ip is False


GOLDEN_LINES = {
    "SW1": [
        "enable",
        "configure terminal",
        "vlan 10",
        "name ADMIN",
        "exit",
        "vlan 20",
        "name SALES",
        "exit",
        "interface gi0/1",
        "switchport mode trunk",
        "switchport trunk encapsulation dot1q",
        "exit",
        "interface gi0/2",
        "switchport mode access",
        "switchport access vlan 10",
        "exit",
        "interface gi0/3",
        "switchport mode access",
        "switchport access vlan 20",
        "end",
    ],
    "R1": [
        "enable",
        "configure terminal",
        "interface g0/0/0.10",
        "encapsulation dot1q 10",
        "ip address 192.168.10.1 255.255.255.0",
        "exit",
        "interface g0/0/0.20",
        "encapsulation dot1q 20",
        "ip address 192.168.20.1 255.255.255.0",
        "end",
    ],
    "PC-A": [
        "enable",
        "ip address 192.168.10.10 255.255.255.0",
        "ip default-gateway 192.168.10.1",
    ],
    "PC-B": [
        "enable",
        "ip address 192.168.20.10 255.255.255.0",
        "ip default-gateway 192.168.20.1",
    ],
}


class TestGoldenSmokeRun:
    """The full milestone configuration, executed through the CLI engine."""

    def test_milestone_scenario_accepts_full_configuration(self) -> None:
        scenario = load_scenario_file(FIRST_SCENARIO)
        state = build_initial_state(scenario)
        outputs: dict[str, list[str]] = {}
        for hostname, lines in GOLDEN_LINES.items():
            session = CliSession(state, hostname)
            outputs[hostname] = [
                out for line in lines if (out := session.execute(line))
            ]
        # The golden configuration produces no error output at all.
        assert outputs == {"SW1": [], "R1": [], "PC-A": [], "PC-B": []}

        switch = state.device("SW1")
        assert isinstance(switch, Switch)
        assert switch.get_vlan(10).name == "ADMIN"
        assert switch.get_vlan(20).name == "SALES"
        assert switch.get_interface("gi0/1").switchport_mode is SwitchportMode.TRUNK
        assert switch.get_interface("gi0/2").access_vlan == 10
        assert switch.get_interface("gi0/3").access_vlan == 20

        r1 = state.device("R1")
        assert r1.get_interface("g0/0/0.10").encapsulation_vlan == 10
        assert r1.get_interface("g0/0/0.10").cidr == "192.168.10.1/24"
        assert r1.get_interface("g0/0/0.20").encapsulation_vlan == 20
        assert r1.get_interface("g0/0/0.20").cidr == "192.168.20.1/24"

        pc_a = state.device("PC-A")
        assert isinstance(pc_a, Host)
        assert pc_a.default_gateway == IPv4Address("192.168.10.1")

    def test_incomplete_configuration_leaves_defaults(self) -> None:
        """A config that skips the trunk leaves Gi0/1 in its default state."""
        scenario = load_scenario_file(FIRST_SCENARIO)
        state = build_initial_state(scenario)
        session = CliSession(state, "SW1")
        for line in ("enable", "configure terminal", "vlan 10", "name ADMIN"):
            assert session.execute(line) == ""
        assert state.device("SW1").get_interface("gi0/1").switchport_mode is SwitchportMode.NONE
