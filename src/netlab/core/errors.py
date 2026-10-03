"""NetLab error taxonomy.

Two families:

- :class:`CommandError` — problems with user-entered commands. The CLI layer
  renders these as IOS-style messages (``% Incomplete command.`` etc.).
- :class:`DomainError` — violations of domain invariants raised by the state
  model. The CLI layer maps these to consistent educational feedback.

Both keep a ``message`` attribute so presentation code never parses exception
strings.
"""


class NetLabError(Exception):
    """Base class for all NetLab errors."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


# --- Command-level errors (CLI layer) -----------------------------------------


class CommandError(NetLabError):
    """A user-entered command could not be accepted.

    ``position`` optionally records the character offset in the raw input line
    where the problem was detected, so the CLI layer can render the IOS-style
    ``^`` marker under the offending token.
    """

    def __init__(self, message: str, *, position: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.position = position


class InvalidInputError(CommandError):
    """Unrecognized command ('% Invalid input detected at ^ marker.')."""


class IncompleteCommandError(CommandError):
    """Missing required arguments ('% Incomplete command.')."""


class AmbiguousCommandError(CommandError):
    """Command matches several registered commands ('% Ambiguous command.')."""


class InvalidInterfaceError(CommandError):
    """Malformed interface token ('% Invalid interface type and number.')."""


class WrongModeError(CommandError):
    """Command is not valid in the current configuration mode."""


# --- Domain errors (state model) -----------------------------------------------


class DomainError(NetLabError):
    """A domain invariant was violated."""


class UnknownDeviceError(DomainError):
    """Referenced device does not exist in the topology."""


class UnknownInterfaceError(DomainError):
    """Referenced interface does not exist on the device."""


class UnknownVlanError(DomainError):
    """Referenced VLAN does not exist in the switch's VLAN database."""


class DuplicateVlanError(DomainError):
    """The VLAN already exists."""


class InvalidVlanIdError(DomainError):
    """VLAN id outside the valid 1-4094 range."""


class InvalidVlanListError(DomainError):
    """A VLAN list (e.g. '10,20-24') could not be parsed."""


class TopologyError(DomainError):
    """The topology is structurally invalid."""


class ScenarioError(DomainError):
    """A scenario file is missing, malformed, or fails validation."""


class DuplicateEndpointError(TopologyError):
    """An interface is used by more than one link."""
