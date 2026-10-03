"""IOS command registry and parser.

The parser turns raw student input into a :class:`Command` using IOS-like
semantics: case-insensitive matching with full-word and unique-prefix forms,
ambiguity detection, required-argument enforcement, and ``% Incomplete
command.`` for input that ends mid-command. All failures raise
:class:`CommandError` subclasses that the CLI layer renders as IOS-style
messages (doc §35).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from netlab.core.command import Command
from netlab.core.enums import ConfigMode
from netlab.core.errors import (
    AmbiguousCommandError,
    CommandError,
    IncompleteCommandError,
    InvalidInputError,
)
from netlab.devices.interface import canonical_interface_name

_TOKEN_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class CommandSpec:
    """A command available in one configuration mode.

    ``name`` is the canonical full form (e.g. ``configure terminal``); when
    ``pattern`` is set it gives the minimal accept prefix for each word, so
    ``configure terminal`` with ``("conf", "t")`` accepts ``conf t`` and
    ``configure terminal`` but not ``conf te``. Commands with ``argument``
    require exactly one argument token (validated by ``argument_validator``
    when provided), unless ``argument_help`` marks a free-text argument that
    may contain spaces.
    """

    name: str
    mode: ConfigMode
    pattern: tuple[str, ...] | None = None
    argument: str = ""
    argument_validator: Callable[[str], object] | None = None
    argument_help: bool = False
    description: str = ""

    def words(self) -> tuple[str, ...]:
        return tuple(self.name.split())


@dataclass
class CommandRegistry:
    """Commands available per configuration mode."""

    commands: dict[ConfigMode, list[CommandSpec]] = field(default_factory=dict)

    def register(self, spec: CommandSpec) -> None:
        self.commands.setdefault(spec.mode, []).append(spec)

    def specs_for(self, mode: ConfigMode) -> list[CommandSpec]:
        return self.commands.get(mode, [])

    def resolve(self, raw: str, mode: ConfigMode) -> tuple[Command, object | None]:
        """Resolve raw input entered in ``mode``.

        Returns the parsed :class:`Command` and the validated argument (or
        ``None`` for argument-less commands). Raises, in order of precedence:

        - :class:`AmbiguousCommandError` when several commands match
        - :class:`IncompleteCommandError` when the input ends before a
          complete command (missing argument or mid-command-name)
        - :class:`InvalidInputError` otherwise, with the caret positioned at
          the offending token of the deepest-matching command
        """
        tokens = _TOKEN_RE.findall(raw)
        if not tokens:
            raise InvalidInputError("unrecognized command", position=0)

        spans = _token_spans(raw, tokens)
        matches: list[CommandSpec] = []
        incomplete = False
        deepest_depth = -1
        failure_position: int | None = None

        for spec in self.specs_for(mode):
            matched, position, is_incomplete, depth = _match(spec, tokens, spans)
            if matched:
                matches.append(spec)
                continue
            if is_incomplete:
                incomplete = True
            if position is not None and depth >= deepest_depth:
                if depth > deepest_depth or position < failure_position:
                    deepest_depth = depth
                    failure_position = position

        if len(matches) > 1:
            raise AmbiguousCommandError("ambiguous command", position=failure_position)
        if incomplete:
            raise IncompleteCommandError("incomplete command", position=failure_position)
        if len(matches) == 1:
            return self._build(matches[0], raw, tokens, spans, mode)

        raise InvalidInputError("unrecognized command", position=failure_position)

    def _build(
        self,
        spec: CommandSpec,
        raw: str,
        tokens: list[str],
        spans: list[tuple[int, int]],
        mode: ConfigMode,
    ) -> tuple[Command, object | None]:
        argument: object | None = None
        if spec.argument:
            arg_token = tokens[len(spec.words())]
            if spec.argument_validator is not None:
                try:
                    argument = spec.argument_validator(arg_token)
                except CommandError as exc:
                    if exc.position is None:
                        exc.position = spans[len(spec.words())][0]
                    raise
                except Exception as exc:
                    raise InvalidInputError(
                        str(exc), position=spans[len(spec.words())][0]
                    ) from exc
            else:
                argument = arg_token
        return (
            Command(raw=raw, verb=spec.name, arguments=tuple(tokens[1:]), mode=mode),
            argument,
        )


def _token_spans(raw: str, tokens: list[str]) -> list[tuple[int, int]]:
    """(start, end) offsets of each token in the raw line, for caret markers."""
    spans: list[tuple[int, int]] = []
    offset = 0
    for token in tokens:
        start = raw.find(token, offset)
        spans.append((start, start + len(token)))
        offset = start + len(token)
    return spans


def _word_matches(
    word: str, token: str, pattern: tuple[str, ...] | None, index: int
) -> bool:
    """IOS-style matching of one input token against one command word."""
    if token.lower() == word.lower():
        return True
    if pattern is None or index >= len(pattern):
        return False
    min_prefix = pattern[index]
    return (
        token.lower().startswith(min_prefix.lower())
        and len(token) <= len(word)
        and word.lower().startswith(token.lower())
    )


def _match(
    spec: CommandSpec,
    tokens: list[str],
    spans: list[tuple[int, int]],
) -> tuple[bool, int | None, bool, int]:
    """Try to match ``spec`` against the input.

    Returns ``(matched, failure_position_or_None, is_incomplete, depth)``
    where ``depth`` is the number of command words matched before failing
    (used to position the caret at the deepest partial match).
    """
    words = spec.words()
    for index, word in enumerate(words):
        if index >= len(tokens):
            # Input ended mid-command-name: incomplete, not invalid.
            return False, (spans[-1][1] if spans else 0), True, index
        if not _word_matches(word, tokens[index], spec.pattern, index):
            return False, spans[index][0], False, index

    if spec.argument:
        if len(tokens) == len(words):
            return False, None, True, len(words)  # name complete, argument missing
        if not spec.argument_help:
            if len(tokens) > len(words) + 1:
                return False, spans[len(words) + 1][0], False, len(words)
            arg_token = tokens[len(words)]
            if spec.argument_validator is not None:
                try:
                    spec.argument_validator(arg_token)
                except Exception:
                    return False, spans[len(words)][0], False, len(words)
        return True, None, False, len(words)

    if len(tokens) > len(words):
        return False, spans[len(words)][0], False, len(words)
    return True, None, False, len(words)


# --- Shared argument validators -------------------------------------------------


def validate_hostname_argument(value: str) -> str:
    """Validate a hostname argument, raising ``ValueError`` when malformed."""
    from netlab.devices.base import validate_hostname

    return validate_hostname(value)


def validate_vlan_id_argument(value: str) -> int:
    """Validate a VLAN id argument, raising on out-of-range or non-numeric."""
    from netlab.devices.interface import validate_vlan_id

    return validate_vlan_id(int(value))


def validate_interface_argument(value: str) -> str:
    """Canonicalize an interface argument; raises on malformed tokens."""
    return canonical_interface_name(value)


__all__ = [
    "CommandRegistry",
    "CommandSpec",
    "validate_hostname_argument",
    "validate_interface_argument",
    "validate_vlan_id_argument",
]
