"""Unit tests for the deterministic grading domain (milestone 6).

Covers: result models, scoring/partial credit, the validator registry, and
individual checks against hand-built authoritative state.
"""

from __future__ import annotations

import pytest

from netlab.core.errors import GradingError
from netlab.devices.interface import Interface
from netlab.devices.switch import Switch
from netlab.grading import (
    Assessment,
    CheckStatus,
    registered_types,
    render_assessment,
    validator_for,
)
from netlab.grading.checks import (
    check_access_vlan,
    check_interface_mode,
    check_ip_address,
    check_trunk_allowed_vlans,
    check_trunk_native_vlan,
    check_vlan_exists,
)
from netlab.grading.scoring import aggregate_categories, earned_points, status_for
from netlab.network.state import NetworkState
from netlab.network.topology import Topology
from netlab.scenarios.schema import (
    AccessVlanRequirement,
    InterfaceModeRequirement,
    IpAddressRequirement,
    TrunkAllowedVlansRequirement,
    TrunkNativeVlanRequirement,
    VLANExistsRequirement,
)


def make_switch() -> Switch:
    switch = Switch(hostname="SW1")
    for name in ("GigabitEthernet0/1", "GigabitEthernet0/2", "GigabitEthernet0/3"):
        switch.add_interface(Interface(name=name))
    return switch


def make_state() -> NetworkState:
    return NetworkState(topology=Topology(devices={"SW1": make_switch()}, links=[]))


VLAN_REQ = VLANExistsRequirement(
    id="vlan10", category="VLAN", points=10, device="SW1", vlan_id=10
)


class TestResultModels:
    def test_assessment_percentage(self) -> None:
        assessment = Assessment(
            scenario_id="s",
            scenario_title="S",
            total_points=10,
            earned_points=8,
            results=[],
            categories=[],
        )
        assert assessment.percentage == 80

    def test_assessment_partition_properties(self) -> None:
        from netlab.grading.models import RequirementResult

        results = [
            RequirementResult(
                requirement_id="a",
                category="C",
                description="A",
                status=CheckStatus.PASS,
                points_possible=5,
                points_earned=5,
            ),
            RequirementResult(
                requirement_id="b",
                category="C",
                description="B",
                status=CheckStatus.PARTIAL,
                points_possible=5,
                points_earned=2,
            ),
            RequirementResult(
                requirement_id="c",
                category="C",
                description="C",
                status=CheckStatus.FAIL,
                points_possible=5,
                points_earned=0,
            ),
        ]
        assessment = Assessment(
            scenario_id="s",
            scenario_title="S",
            total_points=15,
            earned_points=7,
            results=results,
            categories=aggregate_categories(results),
        )
        assert len(assessment.passed_checks) == 1
        assert len(assessment.partial_checks) == 1
        assert len(assessment.failed_checks) == 1
        assert assessment.unmet_requirements == ["B", "C"]
        assert "7 / 15" in assessment.summary


class TestScoring:
    def test_earned_points_full_and_zero(self) -> None:
        assert earned_points(VLAN_REQ, 1.0) == 10
        assert earned_points(VLAN_REQ, 0.0) == 0

    def test_earned_points_partial_is_whole(self) -> None:
        assert earned_points(VLAN_REQ, 0.5) == 5

    def test_status_mapping(self) -> None:
        assert status_for(1.0) is CheckStatus.PASS
        assert status_for(0.5) is CheckStatus.PARTIAL
        assert status_for(0.0) is CheckStatus.FAIL

    def test_aggregate_categories(self) -> None:
        from netlab.grading.models import RequirementResult

        results = [
            RequirementResult(
                requirement_id="a", category="Trunking", description="",
                status=CheckStatus.PASS, points_possible=10, points_earned=10,
            ),
            RequirementResult(
                requirement_id="b", category="Trunking", description="",
                status=CheckStatus.FAIL, points_possible=5, points_earned=0,
            ),
            RequirementResult(
                requirement_id="c", category="VLAN", description="",
                status=CheckStatus.PARTIAL, points_possible=10, points_earned=5,
            ),
        ]
        categories = aggregate_categories(results)
        by_name = {c.category: c for c in categories}
        assert by_name["Trunking"].earned == 10
        assert by_name["Trunking"].possible == 15
        assert by_name["VLAN"].earned == 5
        assert by_name["VLAN"].possible == 10


class TestRegistry:
    def test_all_scenario_types_registered(self) -> None:
        expected = {
            "vlan_exists", "vlan_name", "interface_mode", "access_vlan",
            "trunk_native_vlan", "trunk_allowed_vlans", "ip_address",
            "host_ip", "host_gateway", "subinterface_encapsulation",
        }
        assert set(registered_types()) == expected

    def test_unknown_type_raises(self) -> None:
        class Fake:
            type = "nonexistent"

        with pytest.raises(GradingError):
            validator_for(Fake())  # type: ignore[arg-type]


class TestVlanChecks:
    def test_vlan_missing_fails(self) -> None:
        outcome = check_vlan_exists(make_state(), VLAN_REQ)
        assert not outcome.passed
        assert outcome.earned_fraction == 0.0
        assert "vlan 10" in outcome.detail.lower()

    def test_vlan_present_passes(self) -> None:
        state = make_state()
        state.device("SW1").create_vlan(10)  # type: ignore[union-attr]
        outcome = check_vlan_exists(state, VLAN_REQ)
        assert outcome.passed

    def test_vlan_wrong_name_fails(self) -> None:
        state = make_state()
        state.device("SW1").create_vlan(10, "WRONG")  # type: ignore[union-attr]
        outcome = check_vlan_exists(state, VLAN_REQ.model_copy(update={"name": "ADMIN"}))
        assert not outcome.passed
        assert "WRONG" in outcome.actual


class TestSwitchportChecks:
    MODE_REQ = InterfaceModeRequirement(
        id="trunk", category="Trunking", points=10,
        device="SW1", interface="GigabitEthernet0/1", mode="trunk",
    )

    def test_access_when_trunk_expected_is_partial(self) -> None:
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/1")  # type: ignore[union-attr]
        interface.switchport_mode = "access"  # type: ignore[assignment]
        outcome = check_interface_mode(state, self.MODE_REQ)
        assert outcome.status if False else outcome.earned_fraction == 0.5
        assert "switchport mode trunk" in outcome.detail

    def test_none_mode_fails_cleanly(self) -> None:
        outcome = check_interface_mode(make_state(), self.MODE_REQ)
        assert outcome.earned_fraction == 0.0

    def test_trunk_passes(self) -> None:
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/1")  # type: ignore[union-attr]
        interface.switchport_mode = "trunk"  # type: ignore[assignment]
        outcome = check_interface_mode(state, self.MODE_REQ)
        assert outcome.passed

    ACCESS_REQ = AccessVlanRequirement(
        id="acc", category="Access", points=10,
        device="SW1", interface="GigabitEthernet0/2", vlan_id=10,
    )

    def test_access_vlan_right_vlan_wrong_mode_partial(self) -> None:
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/2")  # type: ignore[union-attr]
        interface.access_vlan = 10  # type: ignore[assignment]
        outcome = check_access_vlan(state, self.ACCESS_REQ)
        assert outcome.earned_fraction == 0.5

    def test_access_vlan_correct_full_pass(self) -> None:
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/2")  # type: ignore[union-attr]
        interface.switchport_mode = "access"  # type: ignore[assignment]
        interface.access_vlan = 10  # type: ignore[assignment]
        outcome = check_access_vlan(state, self.ACCESS_REQ)
        assert outcome.passed

    def test_access_vlan_wrong_vlan_fails(self) -> None:
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/2")  # type: ignore[union-attr]
        interface.switchport_mode = "access"  # type: ignore[assignment]
        interface.access_vlan = 20  # type: ignore[assignment]
        outcome = check_access_vlan(state, self.ACCESS_REQ)
        assert outcome.earned_fraction == 0.0
        assert "20" in outcome.actual

    NATIVE_REQ = TrunkNativeVlanRequirement(
        id="native", category="Trunking", points=5,
        device="SW1", interface="GigabitEthernet0/1", vlan_id=1,
    )

    def test_native_vlan_not_trunk_fails_even_when_default_matches(self) -> None:
        """Unconfigured port must not earn the native-VLAN point (default=1)."""
        outcome = check_trunk_native_vlan(make_state(), self.NATIVE_REQ)
        assert outcome.earned_fraction == 0.0

    def test_native_vlan_on_trunk_passes(self) -> None:
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/1")  # type: ignore[union-attr]
        interface.switchport_mode = "trunk"  # type: ignore[assignment]
        outcome = check_trunk_native_vlan(state, self.NATIVE_REQ)
        assert outcome.passed

    ALLOWED_REQ = TrunkAllowedVlansRequirement(
        id="allowed", category="Trunking", points=5,
        device="SW1", interface="GigabitEthernet0/1", vlans=[10, 20],
    )

    def test_allowed_default_all_is_partial(self) -> None:
        """Default 1-4094 trunk contains the VLANs but was never pruned."""
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/1")  # type: ignore[union-attr]
        interface.switchport_mode = "trunk"  # type: ignore[assignment]
        outcome = check_trunk_allowed_vlans(state, self.ALLOWED_REQ)
        assert not outcome.passed
        assert outcome.earned_fraction == 0.5  # explicit, not a rounding artifact

    def test_allowed_exact_passes(self) -> None:
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/1")  # type: ignore[union-attr]
        interface.switchport_mode = "trunk"  # type: ignore[assignment]
        interface.allowed_vlans = {10, 20}  # type: ignore[assignment]
        outcome = check_trunk_allowed_vlans(state, self.ALLOWED_REQ)
        assert outcome.passed

    def test_allowed_missing_vlan_fails(self) -> None:
        state = make_state()
        interface = state.device("SW1").get_interface("GigabitEthernet0/1")  # type: ignore[union-attr]
        interface.switchport_mode = "trunk"  # type: ignore[assignment]
        interface.allowed_vlans = {10}  # type: ignore[assignment]
        outcome = check_trunk_allowed_vlans(state, self.ALLOWED_REQ)
        assert outcome.earned_fraction == 0.0
        assert "missing" in outcome.detail


class TestIpCheck:
    def test_missing_subinterface_fails(self) -> None:
        req = IpAddressRequirement(
            id="ip", category="Routing", points=8,
            device="SW1", interface="GigabitEthernet0/1.10",
            address="192.168.10.1", prefix=24,
        )
        outcome = check_ip_address(make_state(), req)
        assert outcome.earned_fraction == 0.0
        assert "does not exist" in outcome.actual

    def test_wrong_host_partial_right_subnet(self) -> None:
        switch = make_switch()
        switch.add_interface(Interface(name="GigabitEthernet0/1.10", parent="GigabitEthernet0/1"))
        state = NetworkState(topology=Topology(devices={"SW1": switch}, links=[]))
        switch.get_interface("GigabitEthernet0/1.10").set_ip("192.168.10.99", 24)
        req = IpAddressRequirement(
            id="ip", category="Routing", points=8,
            device="SW1", interface="GigabitEthernet0/1.10",
            address="192.168.10.1", prefix=24,
        )
        outcome = check_ip_address(state, req)
        assert outcome.earned_fraction == 0.5
        assert "wrong host address" in outcome.detail

    def test_wrong_subnet_clean_fail(self) -> None:
        switch = make_switch()
        switch.add_interface(Interface(name="GigabitEthernet0/1.10", parent="GigabitEthernet0/1"))
        state = NetworkState(topology=Topology(devices={"SW1": switch}, links=[]))
        switch.get_interface("GigabitEthernet0/1.10").set_ip("192.168.99.1", 24)
        req = IpAddressRequirement(
            id="ip", category="Routing", points=8,
            device="SW1", interface="GigabitEthernet0/1.10",
            address="192.168.10.1", prefix=24,
        )
        outcome = check_ip_address(state, req)
        assert outcome.earned_fraction == 0.0

    def test_exact_match_passes(self) -> None:
        switch = make_switch()
        switch.add_interface(Interface(name="GigabitEthernet0/1.10", parent="GigabitEthernet0/1"))
        state = NetworkState(topology=Topology(devices={"SW1": switch}, links=[]))
        switch.get_interface("GigabitEthernet0/1.10").set_ip("192.168.10.1", 24)
        req = IpAddressRequirement(
            id="ip", category="Routing", points=8,
            device="SW1", interface="GigabitEthernet0/1.10",
            address="192.168.10.1", prefix=24,
        )
        outcome = check_ip_address(state, req)
        assert outcome.passed


class TestReportRendering:
    def test_report_contains_score_and_marks(self) -> None:
        from netlab.grading.models import RequirementResult

        results = [
            RequirementResult(
                requirement_id="a", category="C", description="VLAN 10 exists",
                status=CheckStatus.PASS, points_possible=10, points_earned=10,
            ),
            RequirementResult(
                requirement_id="b", category="C", description="Trunk configured",
                status=CheckStatus.FAIL, points_possible=10, points_earned=0,
            ),
        ]
        assessment = Assessment(
            scenario_id="s", scenario_title="Scenario S",
            total_points=20, earned_points=10,
            results=results, categories=aggregate_categories(results),
        )
        report = render_assessment(assessment)
        assert "Score: 10 / 20" in report
        assert "✓ VLAN 10 exists" in report
        assert "✗ Trunk configured" in report
        assert "Final Score: 50%" in report

    def test_report_calculates_nothing(self) -> None:
        """Renderer must derive from the assessment; zero-point edge included."""
        assessment = Assessment(
            scenario_id="s", scenario_title="S", total_points=10,
            earned_points=0, results=[], categories=[],
        )
        report = render_assessment(assessment)
        assert "0%" in report
