"""Unit tests for stage-5 verification commands rendered from network state."""

from pathlib import Path

from netlab.ios.machine import CliSession
from netlab.network.state import NetworkState
from netlab.scenarios import build_initial_state, load_scenario_file
from tests.unit.test_config_commands import GOLDEN_LINES
from tests.unit.test_topology import build_topology

PROJECT_ROOT = Path(__file__).parents[2]
FIRST_SCENARIO = PROJECT_ROOT / "scenarios" / "vlan" / "ccna-vlan-001.yaml"


def fresh_session(hostname: str = "SW1") -> tuple[CliSession, NetworkState]:
    state = build_topology()
    return CliSession(state, hostname), state


def golden_state() -> NetworkState:
    """The milestone scenario with the full golden configuration applied."""
    scenario = load_scenario_file(FIRST_SCENARIO)
    state = build_initial_state(scenario)
    for hostname, lines in GOLDEN_LINES.items():
        session = CliSession(state, hostname)
        for line in lines:
            assert session.execute(line) == "", f"{hostname}: {line}"
    return state


class TestShowVlanBrief:
    def test_initial_state_lists_only_vlan_1(self) -> None:
        session, _state = fresh_session()
        output = session.execute("show vlan brief")
        assert "1    default" in output
        assert "Gi0/1, Gi0/2, Gi0/3" in output

    def test_golden_configuration_vlans_and_ports(self) -> None:
        state = golden_state()
        session = CliSession(state, "SW1")
        output = session.execute("show vlan brief")
        assert "10   ADMIN" in output and "Gi0/2" in output
        assert "20   SALES" in output and "Gi0/3" in output
        # The trunk appears under its native VLAN (1).
        vlan1_ports = next(
            text for text in output.splitlines() if text.startswith("1   ")
        )
        assert "Gi0/1" in vlan1_ports

    def test_output_changes_with_state(self) -> None:
        """Doc §18: show output must change when the student changes state."""
        session, _state = fresh_session()
        before = session.execute("show vlan brief")
        for line in ("enable", "configure terminal", "vlan 10", "name ADMIN", "end"):
            session.execute(line)
        after = session.execute("show vlan brief")
        assert before != after
        assert "10   ADMIN" in after

    def test_vlan_brief_on_router_is_error(self) -> None:
        session, _state = fresh_session("R1")
        output = session.execute("show vlan brief")
        assert output.startswith("%")


class TestShowInterfacesTrunk:
    def test_no_trunks_initially(self) -> None:
        session, _state = fresh_session()
        assert session.execute("show interfaces trunk") == ""

    def test_trunk_listed_after_configuration(self) -> None:
        state = golden_state()
        session = CliSession(state, "SW1")
        output = session.execute("show interfaces trunk")
        assert "Gi0/1" in output
        assert "802.1q" in output
        assert "trunking" in output
        # Allowed-and-active: full list intersected with VLANs {1,10,20}.
        assert "1,10,20" in output

    def test_trunk_allowed_list_follows_state(self) -> None:
        session, _state = fresh_session()
        for line in (
            "enable",
            "configure terminal",
            "interface gi0/1",
            "switchport mode trunk",
            "switchport trunk allowed vlan 10,20",
            "end",
        ):
            session.execute(line)
        output = session.execute("show interfaces trunk")
        # 'allowed vlan 10,20' REPLACES the list, so VLAN 1 is no longer
        # permitted; with neither VLAN created the active list is empty.
        assert "Gi0/1       none" in output
        # Create VLAN 10 and the active list shows it.
        for line in ("configure terminal", "vlan 10", "end"):
            session.execute(line)
        output = session.execute("show interfaces trunk")
        assert "Gi0/1       10" in output


class TestShowIpInterfaceBrief:
    def test_router_unconfigured_interfaces(self) -> None:
        session, _state = fresh_session("R1")
        output = session.execute("show ip interface brief")
        assert "unassigned" in output
        assert "Gi0/0/0" in output

    def test_router_subinterfaces_after_golden(self) -> None:
        state = golden_state()
        session = CliSession(state, "R1")
        output = session.execute("show ip interface brief")
        assert "192.168.10.1/24" in output
        assert "192.168.20.1/24" in output
        # Physical parent and both subinterfaces are up/up.
        assert output.count("up") >= 6

    def test_shutdown_shows_administratively_down(self) -> None:
        session, _state = fresh_session("R1")
        for line in (
            "enable",
            "configure terminal",
            "interface g0/0/0",
            "shutdown",
            "end",
        ):
            session.execute(line)
        output = session.execute("show ip interface brief")
        assert "administratively down" in output

    def test_host_addressing_visible(self) -> None:
        session, _state = fresh_session("PC-A")
        session.execute("ip address 192.168.10.10 255.255.255.0")
        output = session.execute("show ip interface brief")
        assert "192.168.10.10/24" in output


class TestRunningAndStartupConfig:
    def test_running_config_renders_state(self) -> None:
        session, _state = fresh_session()
        for line in (
            "enable",
            "configure terminal",
            "hostname CORE1",
            "interface gi0/2",
            "switchport mode access",
            "switchport access vlan 10",
            "end",
        ):
            session.execute(line)
        output = session.execute("show running-config")
        assert "hostname CORE1" in output
        assert "interface GigabitEthernet0/2" in output
        assert " switchport mode access" in output
        assert " switchport access vlan 10" in output

    def test_startup_config_absent_before_copy(self) -> None:
        session, _state = fresh_session()
        output = session.execute("show startup-config")
        assert "not present" in output

    def test_copy_run_start_and_isolation(self) -> None:
        """Startup is a frozen snapshot: later mutations leave it untouched."""
        session, _state = fresh_session()
        for line in (
            "enable",
            "configure terminal",
            "hostname CORE1",
            "end",
        ):
            session.execute(line)
        copied = session.execute("copy running-config startup-config")
        assert "[OK]" in copied

        startup_before = session.execute("show startup-config")
        assert "hostname CORE1" in startup_before

        # Mutate after the save.
        session.execute("configure terminal")
        session.execute("hostname NEWNAME")
        session.execute("end")

        running = session.execute("show running-config")
        startup_after = session.execute("show startup-config")
        assert "hostname NEWNAME" in running
        assert "hostname CORE1" in startup_after
        assert "hostname NEWNAME" not in startup_after
        assert startup_after == startup_before


class TestExecAccess:
    def test_show_works_in_user_exec(self) -> None:
        session, _state = fresh_session()
        # Session starts in USER_EXEC; no enable needed.
        assert "default" in session.execute("show vlan brief")

    def test_copy_requires_privileged_exec(self) -> None:
        session, _state = fresh_session()
        output = session.execute("copy running-config startup-config")
        assert output.startswith("%")
