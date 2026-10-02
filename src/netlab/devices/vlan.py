"""VLAN model (doc §15)."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from netlab.core.enums import VlanStatus
from netlab.devices.interface import MAX_VLAN, MIN_VLAN

MAX_VLAN_NAME_LENGTH = 32


class VLAN(BaseModel):
    """A VLAN in a switch's VLAN database."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    id: int = Field(ge=MIN_VLAN, le=MAX_VLAN)
    name: str = Field(default="", max_length=MAX_VLAN_NAME_LENGTH)
    status: VlanStatus = VlanStatus.ACTIVE

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return value.strip()
