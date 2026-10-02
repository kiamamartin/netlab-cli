"""Link model connecting two device interfaces (doc §17)."""

from pydantic import BaseModel, ConfigDict, field_validator

from netlab.core.enums import LinkStatus
from netlab.devices.interface import canonical_interface_name


class Endpoint(BaseModel):
    """One side of a link: a device hostname plus a canonical interface name."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    device: str
    interface: str

    @field_validator("interface", mode="after")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return canonical_interface_name(value)

    @property
    def ref(self) -> str:
        """``HOSTNAME:Interface`` reference used in topology specs and messages."""
        return f"{self.device}:{self.interface}"


class Link(BaseModel):
    """A point-to-point link between two interfaces."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    source: Endpoint
    destination: Endpoint
    status: LinkStatus = LinkStatus.UP
