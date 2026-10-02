"""Structured representation of an IOS-like command line (doc §12)."""

from pydantic import BaseModel, ConfigDict

from netlab.core.enums import ConfigMode


class Command(BaseModel):
    """A parsed IOS-style command line, bound to the mode it was entered in.

    The CLI parser produces these objects; command handlers consume them. Raw
    student input is never executed or interpreted beyond this structure.
    """

    model_config = ConfigDict(frozen=True)

    raw: str
    verb: str
    arguments: tuple[str, ...]
    mode: ConfigMode
