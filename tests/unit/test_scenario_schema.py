"""Unit tests for the scenario schema."""

import pytest
from pydantic import ValidationError

from netlab.scenarios.schema import Scenario


def minimal_scenario_dict() -> dict:
    """A smallest valid scenario used as a base for mutation tests."""
    return {
        "id": "test-scenario",
        "title": "Test Scenario",
        "difficulty": "beginner",
        "topology": {
            "devices": [
                {"name": "SW1", "type": "switch", "interfaces": [{"name": "Gi0/1"}]},
            ],
            "links": [],
        },
        "requirements": [
            {
                "id": "vlan10",
                "category": "VLAN Configuration",
                "points": 100,
                "type": "vlan_exists",
                "device": "SW1",
                "vlan_id": 10,
            }
        ],
    }


def test_minimal_scenario_loads_with_defaults() -> None:
    scenario = Scenario.model_validate(minimal_scenario_dict())
    assert scenario.version == 1
    assert scenario.grading.total == 100
    assert scenario.objectives == []
    assert scenario.verification == []
    assert scenario.initial_state is None
    assert scenario.metadata.estimated_minutes is None


@pytest.mark.parametrize(
    "scenario_id", ["ccna-vlan-001", "test", "a1-b2-c3", "vlan001"]
)
def test_valid_scenario_ids(scenario_id: str) -> None:
    data = minimal_scenario_dict()
    data["id"] = scenario_id
    assert Scenario.model_validate(data).id == scenario_id


@pytest.mark.parametrize("scenario_id", ["Bad_ID", "-lead", "trail-", "UPPER", "a--b", ""])
def test_invalid_scenario_ids(scenario_id: str) -> None:
    data = minimal_scenario_dict()
    data["id"] = scenario_id
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_unknown_requirement_type_rejected() -> None:
    data = minimal_scenario_dict()
    data["requirements"][0]["type"] = "magic_check"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_requirement_points_must_sum_to_total() -> None:
    data = minimal_scenario_dict()
    data["requirements"].append(
        {
            "id": "vlan20",
            "category": "VLAN Configuration",
            "points": 50,
            "type": "vlan_exists",
            "device": "SW1",
            "vlan_id": 20,
        }
    )
    with pytest.raises(ValidationError, match="sum to 150"):
        Scenario.model_validate(data)


def test_duplicate_requirement_ids_rejected() -> None:
    data = minimal_scenario_dict()
    data["requirements"].append(dict(data["requirements"][0]))
    with pytest.raises(ValidationError, match="duplicate requirement ids"):
        Scenario.model_validate(data)


def test_extra_fields_rejected() -> None:
    data = minimal_scenario_dict()
    data["hacker_field"] = True
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_invalid_difficulty_rejected() -> None:
    data = minimal_scenario_dict()
    data["difficulty"] = "impossible"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_empty_requirements_rejected() -> None:
    data = minimal_scenario_dict()
    data["requirements"] = []
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_requirement_interface_canonicalized() -> None:
    data = minimal_scenario_dict()
    data["requirements"][0] = {
        "id": "trunk",
        "category": "Trunking",
        "points": 100,
        "type": "interface_mode",
        "device": "SW1",
        "interface": "gi0/1",
        "mode": "trunk",
    }
    scenario = Scenario.model_validate(data)
    assert scenario.requirements[0].interface == "GigabitEthernet0/1"  # type: ignore[union-attr]


def test_duplicate_device_hostnames_rejected() -> None:
    data = minimal_scenario_dict()
    data["topology"]["devices"].append({"name": "SW1", "type": "switch"})
    with pytest.raises(ValidationError, match="unique"):
        Scenario.model_validate(data)


def test_subinterface_parent_must_exist_in_device_spec() -> None:
    data = minimal_scenario_dict()
    data["topology"]["devices"][0]["interfaces"].append(
        {"name": "Gi0/1.10", "parent": "Gi0/2"}
    )
    with pytest.raises(ValidationError, match="missing parent"):
        Scenario.model_validate(data)


def test_endpoint_without_colon_rejected() -> None:
    data = minimal_scenario_dict()
    data["topology"]["links"] = [{"source": "SW1-Gi0/1", "destination": "SW1:Gi0/1"}]
    with pytest.raises(ValidationError, match="HOSTNAME:Interface"):
        Scenario.model_validate(data)


def test_endpoint_with_unknown_interface_type_rejected() -> None:
    data = minimal_scenario_dict()
    data["topology"]["links"] = [{"source": "SW1:foo0/1", "destination": "SW1:Gi0/1"}]
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_trunk_allowed_vlans_range_enforced() -> None:
    data = minimal_scenario_dict()
    data["requirements"][0] = {
        "id": "trunk-vlans",
        "category": "Trunking",
        "points": 100,
        "type": "trunk_allowed_vlans",
        "device": "SW1",
        "interface": "Gi0/1",
        "vlans": [10, 5000],
    }
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_points_by_category() -> None:
    data = minimal_scenario_dict()
    data.setdefault("grading", {})["total"] = 150
    data["requirements"] = [
        {
            "id": "a",
            "category": "VLAN Configuration",
            "points": 50,
            "type": "vlan_exists",
            "device": "SW1",
            "vlan_id": 10,
        },
        {
            "id": "b",
            "category": "VLAN Configuration",
            "points": 50,
            "type": "vlan_exists",
            "device": "SW1",
            "vlan_id": 20,
        },
        {
            "id": "c",
            "category": "Trunking",
            "points": 50,
            "type": "interface_mode",
            "device": "SW1",
            "interface": "Gi0/1",
            "mode": "trunk",
        },
    ]
    scenario = Scenario.model_validate(data)
    assert scenario.points_by_category == {
        "VLAN Configuration": 100,
        "Trunking": 50,
    }
