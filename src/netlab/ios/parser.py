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
    ``configure terminal`` but not ``conf ta``.

    Argument handling:

    - ``argument=""`` — no argument; surplus tokens are invalid input.
    - ``argument="X"`` with ``argument_max=1`` — exactly one token.
    - ``argument_max > 1`` — 1..N tokens consumed by ``multi_validator``
      (``argument_min`` sets the lower bound, e.g. ``ip address`` needs 2).
    - ``free_text=True`` — the remainder of the line, joined with spaces
      (e.g. a VLAN name containing spaces).
    """

    name: str
    mode: ConfigMode
    pattern: tuple[str, ...] | None = None
    argument: str = ""
    argument_min: int = 1
    argument_max: int = 1
    free_text: bool = False
    argument_validator: Callable[[str], object] | None = None
    multi_validator: Callable[[list[str]], object] | None = None
    description: str = ""

    def words(self) -> tuple[str, ...]:
        return tuple(self.name.split())


@dataclass
class CommandRegistry:
    """Commands available per configuration mode."""

    commands: dict[ConfigMode, list[CommandSpec]] = field(default_factory=dict)

    def register(self, spec: CommandSpec) -> None:
        """Register a command; rejects duplicate (mode, name) registrations.

        Duplicate registration would otherwise produce spurious ambiguity at
        resolve time, so it is rejected here where the mistake is visible.
        """
        existing = self.commands.get(spec.mode, [])
        if any(registered.name == spec.name for registered in existing):
            raise ValueError(
                f"command {spec.name!r} is already registered in mode {spec.mode.value!r}"
            )
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
        if incomplete and not matches:
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
            arg_start = len(spec.words())
            arg_tokens = tokens[arg_start:]
            fail_at = spans[arg_start][0]
            if spec.free_text:
                joined = " ".join(arg_tokens)
                if spec.argument_validator is not None:
                    try:
                        argument = spec.argument_validator(joined)
                    except CommandError as exc:
                        if exc.position is None:
                            exc.position = fail_at
                        raise
                    except Exception as exc:
                        raise InvalidInputError(str(exc), position=fail_at) from exc
                else:
                    argument = joined
            elif spec.multi_validator is not None:
                try:
                    argument = spec.multi_validator(arg_tokens[: spec.argument_max])
                except CommandError as exc:
                    if exc.position is None:
                        exc.position = fail_at
                    raise
                except Exception as exc:
                    raise InvalidInputError(str(exc), position=fail_at) from exc
            elif spec.argument_validator is not None:
                try:
                    argument = spec.argument_validator(arg_tokens[0])
                except CommandError as exc:
                    if exc.position is None:
                        exc.position = fail_at
                    raise
                except Exception as exc:
                    raise InvalidInputError(str(exc), position=fail_at) from exc
            else:
                argument = arg_tokens[0]
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
    """IOS-style matching of one input token against one command word.

    A token matches when it is a unique prefix of the command word: the token
    must be no longer than the word, the word must start with the token, and
    the token must start with the registered minimal prefix for that word (if
    one is registered). Full-word input always matches.

    Matching is case-insensitive, mirroring IOS.
    """
    if token.lower() == word.lower():
        return True
    if pattern is None or index >= len(pattern):
        return False
    min_prefix = pattern[index]
    token_lower = token.lower()
    word_lower = word.lower()
    # Unique-prefix form: token must be a strict prefix of the word (shorter
    # than the word) and must itself start with the registered minimal prefix.
    # This is what lets ``conf t`` match ``configure terminal`` while rejecting
    # ``conf ta``.
    if len(token_lower) >= len(word_lower):
        return False
    if not word_lower.startswith(token_lower):
        return False
    if not token_lower.startswith(min_prefix.lower()):
        return False
    return True


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
        available = len(tokens) - len(words)
        if available == 0:
            return False, None, True, len(words)  # name complete, argument missing
        if spec.free_text:
            return True, None, False, len(words)
        if available < spec.argument_min:
            return False, (spans[-1][1] if spans else 0), True, len(words)
        if available > spec.argument_max:
            return False, spans[len(words) + spec.argument_max][0], False, len(words)
        arg_tokens = tokens[len(words) : len(words) + available]
        if spec.multi_validator is not None:
            try:
                spec.multi_validator(arg_tokens)
            except Exception:
                return False, spans[len(words)][0], False, len(words)
        elif spec.argument_validator is not None:
            try:
                spec.argument_validator(arg_tokens[0])
            except Exception:
                return False, spans[len(words)][0], False, len(words)
        return True, None, False, len(words)

    # No argument expected: surplus tokens are invalid input.
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
