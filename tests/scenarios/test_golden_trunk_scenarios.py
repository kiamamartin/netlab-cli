"""Golden/negative/determinism tests for the trunking scenario pack.

Mirrors tests/scenarios/test_golden_ccna_vlan_001.py for ccna-trunk-001 and
ccna-trunk-002: the golden configuration must score 100/100 through the real
CLI engine, every scenario must load and validate, and grading must be
deterministic. ccna-trunk-002 ships with pre-configuration, so its fresh
state earns partial credit by design — the test pins that exactly.
"""

from __future__ import annotations

import pytest

from netlab.grading import grade
from netlab.ios.machine import CliSession
from netlab.network.state import NetworkState
from netlab.scenarios import build_initial_state, load_scenario_file
from netlab.scenarios.schema import Scenario

TRUNK_001 = "scenarios/trunking/ccna-trunk-001.yaml"
TRUNK_002 = "scenarios/trunking/ccna-trunk-002.yaml"

GOLDEN_TRUNK_001: dict[str, list[str]] = {
    "SW1": [
        "enable",
        "configure terminal",
        "vlan 10",
        "name ADMIN",
        "exit",
        "vlan 20",
        "name SALES",
        "exit",
        "interface GigabitEthernet0/1",
        "switchport mode trunk",
        "switchport trunk encapsulation dot1q",
        "switchport trunk allowed vlan 10,20",
        "end",
        "configure terminal",
        "interface GigabitEthernet0/2",
        "switchport mode access",
        "switchport access vlan 10",
        "end",
    ],
    "SW2": [
        "enable",
        "configure terminal",
        "vlan 10",
        "name ADMIN",
        "exit",
        "vlan 20",
        "name SALES",
        "exit",
        "interface GigabitEthernet0/1",
        "switchport mode trunk",
        "switchport trunk encapsulation dot1q",
        "switchport trunk allowed vlan 10,20",
        "end",
        "configure terminal",
        "interface GigabitEthernet0/2",
        "switchport mode access",
        "switchport access vlan 20",
        "end",
    ],
    "PC-A": ["ip address 192.168.10.10 255.255.255.0", "ip default-gateway 192.168.10.1"],
    "PC-B": ["ip address 192.168.20.10 255.255.255.0"],
}

# ccna-trunk-002 ships with VLANs, access ports and the trunk already built;
# the graded work is the trunk pruning plus everything on R1 and the hosts.
GOLDEN_TRUNK_002: dict[str, list[str]] = {
    "SW1": [
        "enable",
        "configure terminal",
        "interface GigabitEthernet0/1",
        "switchport trunk allowed vlan 10,20",
        "end",
    ],
    "R1": [
        "enable",
        "configure terminal",
        "interface GigabitEthernet0/0/0.10",
        "encapsulation dot1q 10",
        "ip address 192.168.10.1 255.255.255.0",
        "exit",
        "interface GigabitEthernet0/0/0.20",
        "encapsulation dot1q 20",
        "ip address 192.168.20.1 255.255.255.0",
        "end",
    ],
    "PC-A": ["ip address 192.168.10.10 255.255.255.0", "ip default-gateway 192.168.10.1"],
    "PC-B": ["ip address 192.168.20.10 255.255.255.0", "ip default-gateway 192.168.20.1"],
}


def apply_golden(
    scenario: Scenario, golden: dict[str, list[str]]
) -> NetworkState:
    """Apply a golden configuration through the real CLI engine."""
    state = build_initial_state(scenario)
    for hostname, commands in golden.items():
        session = CliSession(state, hostname)
        for command in commands:
            output = session.execute(command)
            assert not output.startswith("%"), f"{hostname}: {command!r} -> {output}"
    return state


class TestScenarioPackLoads:
    def test_trunk_001_loads_and_validates(self) -> None:
        scenario = load_scenario_file(TRUNK_001)
        assert scenario.id == "ccna-trunk-001"
        assert sum(r.points for r in scenario.requirements) == scenario.grading.total

    def test_trunk_002_loads_and_validates(self) -> None:
        scenario = load_scenario_file(TRUNK_002)
        assert scenario.id == "ccna-trunk-002"
        assert sum(r.points for r in scenario.requirements) == scenario.grading.total

    def test_both_scenarios_are_discoverable_together(self) -> None:
        from pathlib import Path

        from netlab.scenarios import discover_scenarios

        loaded, errors = discover_scenarios(Path("scenarios"))
        ids = {scenario.id for _path, scenario in loaded}
        assert not errors
        assert {"ccna-vlan-001", "ccna-trunk-001", "ccna-trunk-002"} <= ids


@pytest.fixture(scope="module")
def trunk_001() -> Scenario:
    return load_scenario_file(TRUNK_001)


class TestGoldenTrunk001:
    def test_golden_scores_100(self, trunk_001: Scenario) -> None:
        state = apply_golden(trunk_001, GOLDEN_TRUNK_001)
        assessment = grade(trunk_001, state)
        assert assessment.percentage == 100
        assert assessment.failed_checks == []
        assert assessment.partial_checks == []

    def test_unconfigured_state_scores_zero(self, trunk_001: Scenario) -> None:
        state = build_initial_state(trunk_001)
        assessment = grade(trunk_001, state)
        assert assessment.earned_points == 0

    def test_trunk_encapsulation_command_is_accepted(self, trunk_001: Scenario) -> None:
        """The scenario's trunk requires 'switchport trunk encapsulation dot1q'."""
        state = build_initial_state(trunk_001)
        session = CliSession(state, "SW1")
        for command in (
            "enable",
            "configure terminal",
            "interface GigabitEthernet0/1",
            "switchport mode trunk",
            "switchport trunk encapsulation dot1q",
            "end",
        ):
            assert session.execute(command) == ""
        output = session.execute("show interfaces trunk")
        assert "802.1q" in output

    def test_unpruned_trunk_is_partial(self, trunk_001: Scenario) -> None:
        state = apply_golden(trunk_001, GOLDEN_TRUNK_001)
        for hostname in ("SW1", "SW2"):
            session = CliSession(state, hostname)
            for command in ("enable", "configure terminal", "interface GigabitEthernet0/1",
                            "switchport trunk allowed vlan all", "end"):
                session.execute(command)
        assessment = grade(trunk_001, state)
        partial = {r.requirement_id for r in assessment.partial_checks}
        assert partial == {
            "sw1-gi0-1-trunk-allows-user-vlans",
            "sw2-gi0-1-trunk-allows-user-vlans",
        }
        assert assessment.percentage < 100

    def test_grading_is_deterministic(self, trunk_001: Scenario) -> None:
        state = apply_golden(trunk_001, GOLDEN_TRUNK_001)
        first = grade(trunk_001, state)
        for _ in range(3):
            assert grade(trunk_001, state) == first


@pytest.fixture(scope="module")
def trunk_002() -> Scenario:
    return load_scenario_file(TRUNK_002)


class TestGoldenTrunk002:
    def test_golden_scores_100(self, trunk_002: Scenario) -> None:
        state = apply_golden(trunk_002, GOLDEN_TRUNK_002)
        assessment = grade(trunk_002, state)
        assert assessment.percentage == 100
        assert assessment.failed_checks == []
        assert assessment.partial_checks == []

    def test_fresh_state_earns_preconfigured_points_only(self, trunk_002: Scenario) -> None:
        """VLANs, access ports, trunk mode and native VLAN are pre-configured."""
        state = build_initial_state(trunk_002)
        assessment = grade(trunk_002, state)
        passed = {r.requirement_id for r in assessment.passed_checks}
        # Trunk mode (10) + native VLAN (5) + access ports are pre-configured.
        assert passed == {"sw1-gi0-1-trunk", "sw1-gi0-1-trunk-native-vlan1"}
        # The unpruned allowed list is partial by definition.
        assert "sw1-gi0-1-trunk-allows-user-vlans" in {
            r.requirement_id for r in assessment.partial_checks
        }
        assert 0 < assessment.earned_points < assessment.total_points

    def test_missing_subinterface_fails_exactly(self, trunk_002: Scenario) -> None:
        state = apply_golden(trunk_002, GOLDEN_TRUNK_002)
        router = state.device("R1")
        router.interfaces.pop("GigabitEthernet0/0/0.20")  # type: ignore[union-attr]
        assessment = grade(trunk_002, state)
        failed = {r.requirement_id for r in assessment.failed_checks}
        assert failed == {"r1-subif-20-encapsulation", "r1-subif-20-ip"}

    def test_grading_is_deterministic(self, trunk_002: Scenario) -> None:
        state = apply_golden(trunk_002, GOLDEN_TRUNK_002)
        first = grade(trunk_002, state)
        for _ in range(3):
            assert grade(trunk_002, state) == first
