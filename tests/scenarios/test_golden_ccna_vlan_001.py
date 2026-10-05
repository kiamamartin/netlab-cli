"""Golden-configuration and mutation tests for the first graded scenario.

Hard gate from the architecture (doc §51–52, §74): the golden configuration
must score 100/100 through the real CLI engine, every deliberate defect must
fail exactly the expected requirements, and the same state must always grade
identically (determinism).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from netlab.grading import grade
from netlab.ios.machine import CliSession
from netlab.network.state import NetworkState
from netlab.scenarios import build_initial_state, load_scenario_file
from netlab.scenarios.schema import Scenario

SCENARIO_PATH = "scenarios/vlan/ccna-vlan-001.yaml"

GOLDEN_SW1 = [
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
    "switchport trunk allowed vlan 10,20",
    "exit",
    "interface GigabitEthernet0/2",
    "switchport mode access",
    "switchport access vlan 10",
    "exit",
    "interface GigabitEthernet0/3",
    "switchport mode access",
    "switchport access vlan 20",
    "end",
]

GOLDEN_R1 = [
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
]

GOLDEN_HOSTS: dict[str, list[str]] = {
    "PC-A": ["ip address 192.168.10.10 255.255.255.0", "ip default-gateway 192.168.10.1"],
    "PC-B": ["ip address 192.168.20.10 255.255.255.0"],
}


def golden_configuration(state: NetworkState) -> None:
    """Apply the known-correct configuration through the real CLI engine."""
    for hostname, commands in (
        ("SW1", GOLDEN_SW1),
        ("R1", GOLDEN_R1),
        *GOLDEN_HOSTS.items(),
    ):
        session = CliSession(state, hostname)
        for command in commands:
            output = session.execute(command)
            assert not output.startswith("%"), f"{hostname}: {command!r} -> {output}"


@pytest.fixture(scope="module")
def scenario() -> Scenario:
    return load_scenario_file(SCENARIO_PATH)


class TestGoldenConfiguration:
    def test_golden_scores_100(self, scenario: Scenario) -> None:
        """Hard requirement: golden configuration → 100/100 (doc §52)."""
        state = build_initial_state(scenario)
        golden_configuration(state)
        assessment = grade(scenario, state)
        assert assessment.percentage == 100
        assert assessment.earned_points == assessment.total_points
        assert assessment.failed_checks == []
        assert assessment.partial_checks == []

    def test_unconfigured_state_scores_zero(self, scenario: Scenario) -> None:
        """A fresh scenario start must earn nothing (defaults are not credit)."""
        state = build_initial_state(scenario)
        assessment = grade(scenario, state)
        assert assessment.earned_points == 0
        assert len(assessment.failed_checks) == len(scenario.requirements)


class TestNegativeMutations:
    """Each deliberate defect fails exactly the expected requirements.

    Every mutation starts from the golden configuration, applies one defect,
    and asserts the score drops and *which* checks fail — unrelated
    requirements must stay green.
    """

    @staticmethod
    def mutated_assessment(
        scenario: Scenario, mutate: Callable[[NetworkState], None]
    ):
        state = build_initial_state(scenario)
        golden_configuration(state)
        mutate(state)
        return grade(scenario, state), state

    @staticmethod
    def assert_only_failing(
        assessment, expected_ids: set[str], expected_partials: set[str] = frozenset()
    ) -> None:
        failed = {result.requirement_id for result in assessment.failed_checks}
        partial = {result.requirement_id for result in assessment.partial_checks}
        assert failed == set(expected_ids), f"failed: {sorted(failed)}"
        assert partial == set(expected_partials), f"partial: {sorted(partial)}"

    def test_vlan10_missing(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            switch = state.device("SW1")
            switch.vlans.pop(10)  # type: ignore[union-attr]

        assessment, _ = self.mutated_assessment(scenario, mutate)
        assert assessment.percentage < 100
        self.assert_only_failing(assessment, {"vlan10-exists", "vlan10-name"})

    def test_vlan20_missing(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            state.device("SW1").vlans.pop(20)  # type: ignore[union-attr]

        assessment, _ = self.mutated_assessment(scenario, mutate)
        assert assessment.percentage < 100
        self.assert_only_failing(assessment, {"vlan20-exists"})

    def test_wrong_vlan_name(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            switch = state.device("SW1")
            switch.set_vlan_name(10, "WRONG")  # type: ignore[union-attr]

        assessment, _ = self.mutated_assessment(scenario, mutate)
        self.assert_only_failing(assessment, {"vlan10-name"})

    def test_access_port_wrong_vlan(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            interface = state.device("SW1").get_interface("GigabitEthernet0/2")
            interface.access_vlan = 20

        assessment, _ = self.mutated_assessment(scenario, mutate)
        self.assert_only_failing(assessment, {"sw1-gi0-2-access-vlan10"})

    def test_trunk_configured_as_access(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            interface = state.device("SW1").get_interface("GigabitEthernet0/1")
            interface.switchport_mode = "access"

        assessment, _ = self.mutated_assessment(scenario, mutate)
        # Wrong trunk mode is critical: the mode check itself earns partial
        # (right command family, wrong mode), while native-VLAN and
        # allowed-VLAN checks fail outright because the port is not trunking.
        self.assert_only_failing(
            assessment,
            {"sw1-gi0-1-trunk-native-vlan1", "sw1-gi0-1-trunk-allows-user-vlans"},
            expected_partials={"sw1-gi0-1-trunk"},
        )

    def test_wrong_native_vlan(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            interface = state.device("SW1").get_interface("GigabitEthernet0/1")
            interface.native_vlan = 99

        assessment, _ = self.mutated_assessment(scenario, mutate)
        self.assert_only_failing(assessment, {"sw1-gi0-1-trunk-native-vlan1"})

    def test_missing_subinterface(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            router = state.device("R1")
            router.interfaces.pop("GigabitEthernet0/0/0.20")  # type: ignore[union-attr]

        assessment, _ = self.mutated_assessment(scenario, mutate)
        self.assert_only_failing(
            assessment, {"r1-subif-20-encapsulation", "r1-subif-20-ip"}
        )

    def test_incorrect_encapsulation(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            interface = state.device("R1").get_interface("GigabitEthernet0/0/0.10")
            interface.encapsulation_vlan = 99

        assessment, _ = self.mutated_assessment(scenario, mutate)
        self.assert_only_failing(assessment, {"r1-subif-10-encapsulation"})

    def test_incorrect_ip_address(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            interface = state.device("R1").get_interface("GigabitEthernet0/0/0.20")
            interface.set_ip("192.168.20.254", 24)

        assessment, _ = self.mutated_assessment(scenario, mutate)
        # Right subnet, wrong host part → partial credit, not a full fail.
        self.assert_only_failing(
            assessment, set(), expected_partials={"r1-subif-20-ip"}
        )
        assert assessment.percentage < 100

    def test_wrong_subnet_mask(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            interface = state.device("R1").get_interface("GigabitEthernet0/0/0.10")
            interface.set_ip("192.168.10.1", 25)

        assessment, _ = self.mutated_assessment(scenario, mutate)
        self.assert_only_failing(assessment, {"r1-subif-10-ip"})

    def test_interface_shutdown(self, scenario: Scenario) -> None:
        """Shutdown is a critical error (doc §25): the L3 address exists but
        the interface cannot pass traffic, so the IP requirement only earns
        partial credit. Encapsulation (L2 tag) is unaffected."""
        def mutate(state: NetworkState) -> None:
            interface = state.device("R1").get_interface("GigabitEthernet0/0/0.10")
            interface.admin_enabled = False

        assessment, _ = self.mutated_assessment(scenario, mutate)
        assert assessment.percentage < 100
        self.assert_only_failing(
            assessment, set(), expected_partials={"r1-subif-10-ip"}
        )

    def test_wrong_host_ip(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            interface = state.device("PC-A").get_interface("Ethernet0")
            interface.set_ip("192.168.10.11", 24)

        assessment, _ = self.mutated_assessment(scenario, mutate)
        self.assert_only_failing(assessment, {"pc-a-ip"})

    def test_wrong_host_gateway(self, scenario: Scenario) -> None:
        def mutate(state: NetworkState) -> None:
            host = state.device("PC-A")
            host.default_gateway = "192.168.10.254"  # type: ignore[assignment]

        assessment, _ = self.mutated_assessment(scenario, mutate)
        self.assert_only_failing(assessment, {"pc-a-gateway"})

    def test_no_configuration_at_all(self, scenario: Scenario) -> None:
        state = build_initial_state(scenario)
        assessment = grade(scenario, state)
        assert assessment.earned_points == 0
        assert assessment.percentage == 0


class TestDeterminism:
    def test_repeated_grading_is_identical(self, scenario: Scenario) -> None:
        """state → grade three times: identical assessment objects (doc §23)."""
        state = build_initial_state(scenario)
        # Partially configure: interesting mix of pass/partial/fail.
        session = CliSession(state, "SW1")
        for command in GOLDEN_SW1[:-1]:  # everything but the trunk allowed list
            session.execute(command)
        session = CliSession(state, "R1")
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("interface GigabitEthernet0/0/0.10")
        session.execute("encapsulation dot1q 10")
        session.execute("end")

        first = grade(scenario, state)
        for _ in range(3):
            again = grade(scenario, state)
            assert again == first
            assert again.earned_points == first.earned_points
            assert again.summary == first.summary
            assert [r.status for r in again.results] == [
                r.status for r in first.results
            ]
            assert [r.points_earned for r in again.results] == [
                r.points_earned for r in first.results
            ]
            assert [r.requirement_id for r in again.results] == [
                r.requirement_id for r in first.results
            ]

    def test_results_preserve_scenario_order(self, scenario: Scenario) -> None:
        state = build_initial_state(scenario)
        assessment = grade(scenario, state)
        expected_ids = [requirement.id for requirement in scenario.requirements]
        assert [r.requirement_id for r in assessment.results] == expected_ids

    def test_grading_never_mutates_state(self, scenario: Scenario) -> None:
        state = build_initial_state(scenario)
        golden_configuration(state)
        before = state.model_dump(mode="json")
        grade(scenario, state)
        assert state.model_dump(mode="json") == before
