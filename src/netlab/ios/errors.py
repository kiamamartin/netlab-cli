"""IOS-style rendering of command errors (doc §35).

Educational consistency over forensic IOS fidelity: every
:class:`CommandError` renders as a ``%`` message and, when a position is
known, a caret line pointing at the offending token.
"""

from netlab.core.errors import (
    AmbiguousCommandError,
    CommandError,
    IncompleteCommandError,
    InvalidInputError,
    InvalidInterfaceError,
    WrongModeError,
)

_MESSAGES: dict[type[CommandError], str] = {
    InvalidInputError: "% Invalid input detected at '^' marker.",
    IncompleteCommandError: "% Incomplete command.",
    AmbiguousCommandError: "% Ambiguous command.",
    InvalidInterfaceError: "% Invalid interface type and number.",
    WrongModeError: "% Command available in a different mode.",
}


def render_error(error: CommandError) -> str:
    """Render a command error as the IOS-style two-line message."""
    message = _MESSAGES.get(type(error))
    if message is None:  # subclasses or custom command errors
        message = error.message
    lines = [message]
    if error.position is not None and error.position >= 0:
        lines.append(f"{' ' * error.position}^")
    return "\n".join(lines)


__all__ = ["render_error"]
