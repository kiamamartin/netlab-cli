"""Unit tests for the scenario loader and the initial-state builder."""

from pathlib import Path

import pytest

from netlab.core.enums import DeviceType, SwitchportMode
from netlab.core.errors import ScenarioError
from netlab.devices.host import Host
from netlab.devices.switch import Switch
from netlab.network.state import NetworkState
from netlab.scenarios import (
    build_initial_state,
    discover_scenarios,
    load_scenario_file,
    parse_scenario,
    validate_requirements,
)
from tests.unit.test_scenario_schema import minimal_scenario_dict

PROJECT_ROOT = Path(__file__).parents[2]
FIRST_SCENARIO = PROJECT_ROOT / "scenarios" / "vlan" / "ccna-vlan-001.yaml"


def test_parse_scenario_minimal() -> None:
    scenario = parse_scenario(minimal_scenario_dict())
    assert scenario.id == "test-scenario"


@pytest.mark.parametrize("data", [None, [], "scenario", 42])
def test_parse_scenario_rejects_non_mapping(data: object) -> None:
    with pytest.raises(ScenarioError, match="YAML mapping"):
        parse_scenario(data)


def test_build_initial_state_minimal() -> None:
    scenario = parse_scenario(minimal_scenario_dict())
    state = build_initial_state(scenario)
    assert isinstance(state, NetworkState)
    switch = state.device("SW1")
    assert isinstance(switch, Switch)
    assert switch.device_type is DeviceType.SWITCH
    assert switch.get_interface("gi0/1").name == "GigabitEthernet0/1"
    assert list(switch.vlans) == [1]  # only the default VLAN


def test_link_endpoints_canonicalized_against_real_interfaces() -> None:
    data = minimal_scenario_dict()
    data["topology"]["devices"][0]["interfaces"].append({"name": "Gi0/2"})
    data["topology"]["devices"].append(
        {"name": "PC-A", "type": "pc", "interfaces": [{"name": "Ethernet0"}]}
    )
    data["topology"]["links"] = [
        {"source": "SW1:gi0/2", "destination": "PC-A:Ethernet0"}
    ]
    scenario = parse_scenario(data)
    state = build_initial_state(scenario)
    far_end = state.connected_endpoint("SW1", "gi0/2")
    assert far_end is not None
    assert far_end.device == "PC-A"
    assert far_end.interface == "Ethernet0"


def test_link_to_missing_interface_fails_early() -> None:
    data = minimal_scenario_dict()
    data["topology"]["devices"].append(
        {"name": "PC-A", "type": "pc", "interfaces": [{"name": "Ethernet0"}]}
    )
    data["topology"]["links"] = [
        {"source": "SW1:Gi0/9", "destination": "PC-A:Ethernet0"}
    ]
    scenario = parse_scenario(data)
    with pytest.raises(ScenarioError):
        build_initial_state(scenario)


def test_initial_state_applies_preconfiguration() -> None:
    """Troubleshooting-style scenarios start from a broken network (doc §44)."""
    data = minimal_scenario_dict()
    data["topology"]["devices"][0]["interfaces"].append({"name": "Gi0/2"})
    data["topology"]["devices"].append(
        {"name": "PC-A", "type": "pc", "interfaces": [{"name": "Ethernet0"}]}
    )
    data["initial_state"] = {
        "vlans": [{"device": "SW1", "vlan_id": 10, "name": "ADMIN"}],
        "interfaces": [
            {
                "device": "SW1",
                "interface": "gi0/2",
                "description": "broken port",
                "shutdown": True,
                "switchport": {"mode": "access", "access_vlan": 10},
                "ip": {"address": "192.168.10.5", "prefix": 24},
            }
        ],
        "hosts": [{"device": "PC-A", "gateway": "192.168.10.1"}],
    }
    scenario = parse_scenario(data)
    state = build_initial_state(scenario)

    switch = state.device("SW1")
    assert isinstance(switch, Switch)
    assert switch.get_vlan(10).name == "ADMIN"

    interface = switch.get_interface("gi0/2")
    assert interface.description == "broken port"
    assert interface.admin_enabled is False
    assert interface.switchport_mode is SwitchportMode.ACCESS
    assert interface.access_vlan == 10
    assert str(interface.ip_address) == "192.168.10.5"

    pc = state.device("PC-A")
    assert isinstance(pc, Host)
    assert str(pc.default_gateway) == "192.168.10.1"


def test_initial_state_unknown_device_fails_early() -> None:
    data = minimal_scenario_dict()
    data["initial_state"] = {"vlans": [{"device": "SW2", "vlan_id": 10}]}
    scenario = parse_scenario(data)
    with pytest.raises(ScenarioError, match="SW2"):
        build_initial_state(scenario)


def test_initial_state_unknown_interface_fails_early() -> None:
    data = minimal_scenario_dict()
    data["initial_state"] = {
        "interfaces": [
            {"device": "SW1", "interface": "Gi0/9", "ip": {"address": "10.0.0.1", "prefix": 24}}
        ]
    }
    scenario = parse_scenario(data)
    with pytest.raises(ScenarioError, match="GigabitEthernet0/9"):
        build_initial_state(scenario)


def test_load_scenario_file_missing() -> None:
    with pytest.raises(ScenarioError, match="not found"):
        load_scenario_file("/nonexistent/scenario.yaml")


def test_load_scenario_file_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("topology: [unclosed\n", encoding="utf-8")
    with pytest.raises(ScenarioError, match="invalid YAML"):
        load_scenario_file(bad)


def test_first_milestone_scenario_loads() -> None:
    scenario = load_scenario_file(FIRST_SCENARIO)
    assert scenario.id == "ccna-vlan-001"
    assert scenario.title == "Department VLAN Deployment"
    assert scenario.difficulty.value == "beginner"
    assert len(scenario.topology.devices) == 4
    assert len(scenario.topology.links) == 3
    assert len(scenario.requirements) == 15
    assert sum(r.points for r in scenario.requirements) == scenario.grading.total == 100
    assert scenario.points_by_category == {
        "VLAN Configuration": 20,
        "Access Ports": 20,
        "Trunking": 20,
        "Inter-VLAN Routing": 25,
        "Host Addressing": 15,
    }


def test_first_milestone_scenario_requirements_reference_existing_state() -> None:
    scenario = load_scenario_file(FIRST_SCENARIO)
    assert validate_requirements(scenario) == []


def test_first_milestone_scenario_initial_state() -> None:
    state = build_initial_state(load_scenario_file(FIRST_SCENARIO))
    # Physical interfaces exist and are up; nothing is pre-configured.
    assert state.interface_is_up("R1", "g0/0/0") is True
    assert state.interface_is_up("SW1", "gi0/1") is True
    assert state.interface_is_up("PC-A", "Ethernet0") is True
    # Router subinterfaces are created by the student, not pre-provisioned.
    r1 = state.device("R1")
    assert [name for name in r1.interface_names if "." in name] == []
    switch = state.device("SW1")
    assert isinstance(switch, Switch)
    assert list(switch.vlans) == [1]
    assert switch.get_interface("gi0/1").description == "Uplink-to-R1"
    # PCs start unaddressed.
    pc_a = state.device("PC-A")
    assert isinstance(pc_a, Host)
    assert pc_a.default_gateway is None
    assert pc_a.get_interface("Ethernet0").has_ip is False


def test_validate_requirements_reports_unknown_device() -> None:
    data = minimal_scenario_dict()
    data["requirements"][0]["device"] = "SW2"
    scenario = parse_scenario(data)
    problems = validate_requirements(scenario)
    assert problems == ["vlan10: unknown device 'SW2'"]


def test_validate_requirements_reports_unknown_physical_interface() -> None:
    data = minimal_scenario_dict()
    data["requirements"][0] = {
        "id": "trunk",
        "category": "Trunking",
        "points": 100,
        "type": "interface_mode",
        "device": "SW1",
        "interface": "Gi0/7",
        "mode": "trunk",
    }
    scenario = parse_scenario(data)
    problems = validate_requirements(scenario)
    assert problems == ["trunk: SW1 has no interface 'GigabitEthernet0/7'"]


def test_validate_requirements_allows_student_created_subinterfaces() -> None:
    data = minimal_scenario_dict()
    data["topology"]["devices"].insert(
        0, {"name": "R1", "type": "router", "interfaces": [{"name": "Gi0/0"}]}
    )
    data["requirements"][0] = {
        "id": "subif-ip",
        "category": "Inter-VLAN Routing",
        "points": 100,
        "type": "ip_address",
        "device": "R1",
        "interface": "Gi0/0.10",
        "address": "192.168.10.1",
        "prefix": 24,
    }
    scenario = parse_scenario(data)
    assert validate_requirements(scenario) == []


def test_discover_scenarios_finds_all_pinned_scenarios() -> None:
        loaded, errors = discover_scenarios(PROJECT_ROOT / "scenarios")
        assert errors == []
        assert {scenario.id for _path, scenario in loaded} == {
            "ccna-vlan-001",
            "ccna-trunk-001",
            "ccna-trunk-002",
        }


def test_discover_scenarios_skips_broken_files(tmp_path: Path) -> None:
    (tmp_path / "good.yaml").write_text(
        "\n".join(
            line
            for line in __import__("yaml").safe_dump(minimal_scenario_dict()).splitlines()
        ),
        encoding="utf-8",
    )
    (tmp_path / "bad.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    loaded, errors = discover_scenarios(tmp_path)
    assert [scenario.id for _path, scenario in loaded] == ["test-scenario"]
    assert len(errors) == 1
    assert "bad.yaml" in errors[0][0].name
