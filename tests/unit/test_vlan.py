"""Unit tests for the VLAN model."""

import pytest
from pydantic import ValidationError

from netlab.core.enums import VlanStatus
from netlab.devices.vlan import VLAN


def test_vlan_defaults() -> None:
    vlan = VLAN(id=10)
    assert vlan.name == ""
    assert vlan.status is VlanStatus.ACTIVE


def test_named_vlan() -> None:
    vlan = VLAN(id=10, name="ADMIN")
    assert vlan.id == 10
    assert vlan.name == "ADMIN"


@pytest.mark.parametrize("vlan_id", [0, -1, 4095])
def test_vlan_rejects_invalid_ids(vlan_id: int) -> None:
    with pytest.raises(ValidationError):
        VLAN(id=vlan_id)


def test_vlan_name_strips_and_limits_length() -> None:
    assert VLAN(id=10, name="  ADMIN ").name == "ADMIN"
    with pytest.raises(ValidationError):
        VLAN(id=10, name="X" * 33)
