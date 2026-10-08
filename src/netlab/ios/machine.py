"""IOS CLI session: mode state machine and command dispatch (doc §11).

The session owns the configuration-mode state machine. Commands are validated
against the current mode by the registry; transition commands move between
modes, and the prompt always reflects the current context. Command handlers
live in :mod:`netlab.ios.commands` as mixins and mutate the authoritative
network state only through the domain models.
"""

from __future__ import annotations

import os
import shutil
import sys
import termios
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

from pydantic import ValidationError

from netlab.core.enums import ConfigMode, DeviceType
from netlab.core.errors import (
    CommandError,
    DomainError,
    InvalidInputError,
    InvalidInterfaceError,
    UnknownInterfaceError,
)
from netlab.devices.base import Device
from netlab.devices.host import Host
from netlab.devices.interface import Interface
from netlab.devices.switch import Switch
from netlab.ios.commands import (
    DeviceConfigMixin,
    HostConfigMixin,
    SwitchConfigMixin,
    build_registry,
)
from netlab.ios.errors import render_error
from netlab.ios.parser import CommandRegistry
from netlab.ios.transitions import SessionExit, exit_target, prompt_for
from netlab.network.state import NetworkState
from netlab.scenarios.schema import Scenario
from netlab.verification.show import show_interfaces_trunk, show_vlan_brief
from netlab.verification.show_ip import (
    show_ip_interface_brief,
    show_running_config,
    show_startup_config,
)


class _InputMode(Enum):
    """Whether the console is reading from a TTY or a pipe."""

    TTY = "tty"
    PIPE = "pipe"


@dataclass(frozen=True)
class HistoryEntry:
    """One executed command, recorded for later analytics (doc §34)."""

    timestamp: datetime
    raw: str
    mode: ConfigMode
    ok: bool
    error: str | None = None


HISTORY_BUFFER_SIZE = 100
"""Maximum number of retained history entries (bounded memory, doc §34)."""


class _EditorState(StrEnum):
    """States of the minimal line editor."""

    EDIT = "edit"  # normal command composition
    COMPLETE = "complete"  # line is ready for execution


class _TerminalEditor:
    """Prompt-aware, wrap-aware line editor backed by a file object.

    Rendering model (inline terminal stream): the session writes the prompt at
    column 0 and the editor owns everything after it on the current line. The
    editor re-renders the *whole line* anchored at the prompt — never relative
    to previous commands — and output mode stays cooked, so repeated commands
    can never accumulate horizontal offset.

    Input is raw (per-key reads, no echo); the editor echoes keys itself.
    Lines longer than the terminal width wrap with a continuation indent equal
    to the prompt width (readline-style); the indent is local to the command
    being edited and never leaks into the transcript.

    History traversal (session-scoped, in-memory only):

    - ``Up`` moves to the previous entry, ``Down`` to the next. With a
      non-empty input buffer, traversal is prefix-filtered (similar-command
      recall); an empty buffer traverses the full chronological history.
    - Moving past the newest (matching) entry restores the buffer as it was
      before traversal began (empty unless a prefix was in use).
    - Traversal never executes a command; editing a recalled line never
      mutates the stored entry; duplicate submissions are retained (IOS-like).
    """

    def __init__(
        self, stdin: Any, history: Sequence[str], stdout: Any = None, prompt: str = ""
    ) -> None:
        self.stdin = stdin
        self.stdout = stdout or sys.stdout
        self.history = history  # written to by the session, read by the editor
        self.prompt = prompt
        self.state = _EditorState.EDIT
        self.text = ""
        self.cursor = 0
        self.eof_requested = False  # Ctrl-C / Ctrl-D on an empty line
        # Traversal state; None means "sitting on the fresh input line".
        self.history_index: int | None = None
        self.search_prefix: str | None = None
        self.draft = ""
        # Visual cursor position within the rendered line (row 0 = prompt row).
        self._vis_row = 0
        self._vis_col = len(prompt)
        self._render_rows = 1

    # -- public -------------------------------------------------------------

    def process(self, ch: str) -> None:
        """Feed one decoded key into the editor."""
        if self.state is _EditorState.COMPLETE:
            return

        if ch in ("\x0d", "\x0a"):  # Enter submits the current buffer
            self.stdout.write("\n")
            self.stdout.flush()
            self.state = _EditorState.COMPLETE
            return

        if ch in ("\x03", "\x04"):  # Ctrl-C / Ctrl-D
            if not self.text:
                # On an empty line both act like EOF and end the session.
                self.eof_requested = True
                self.state = _EditorState.COMPLETE
                return
            if ch == "\x03":
                self._abort_line()  # abandon the buffer; it never executes
            else:
                self._delete_under_cursor()  # Ctrl-D deletes under the cursor
            return

        if ch in ("\x01", "\x05"):  # Ctrl-A / Ctrl-E -> start / end of line
            self.cursor = 0 if ch == "\x01" else len(self.text)
            self._place_cursor()
            return

        if ch == "\x0b":  # Ctrl-K -> kill to end of line
            self.text = self.text[: self.cursor]
            self._redraw()
            return

        if ch == "\x15":  # Ctrl-U -> kill whole line
            self.text = ""
            self.cursor = 0
            self._redraw()
            return

        if ch == "\x17":  # Ctrl-W -> delete previous word
            self.text = self._delete_word(self.text, self.cursor)
            self._redraw()
            return

        if len(ch) == 1 and ord(ch) < 32:
            # Ignored control keys (Ctrl-L, Ctrl-S, Ctrl-Q, ...).
            return

        if len(ch) == 1:
            if ord(ch) == 127 or ord(ch) == 8:  # BS / DEL
                self._backspace()
            else:
                self._insert(ch)
            return

        # Sequential-escape sequences (arrows, Home, End, ...)
        if ch.startswith("\x1b"):
            self._handle_escape(ch)

    # -- internal -----------------------------------------------------------

    def _insert(self, ch: str) -> None:
        at_end = self.cursor == len(self.text)
        self.text = self.text[: self.cursor] + ch + self.text[self.cursor :]
        self.cursor += 1
        if not at_end:
            self._redraw()  # a mid-line insert shifts the tail: re-render
            return
        # Fast path: echo the key and keep the cursor on our wrap grid.
        self.stdout.write(ch)
        self.stdout.flush()
        self._settle_after_write()

    def _settle_after_write(self) -> None:
        """Pre-empt terminal auto-wrap after an echoed key.

        When the key filled the row's last cell the terminal enters
        pending-wrap; writing the continuation indent now keeps the cursor on
        our own wrap grid instead of letting it drop to column 0.
        """
        logical = len(self.prompt) + self.cursor
        _row, col = self._cell(logical)
        if col == len(self.prompt) and logical > len(self.prompt):
            self.stdout.write("\n" + " " * len(self.prompt))
            self.stdout.flush()
        self._vis_row, self._vis_col = self._cell(logical)

    def _backspace(self) -> None:
        if self.cursor > 0:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
            self.cursor -= 1
            self._redraw()

    def _delete_under_cursor(self) -> None:
        if self.cursor < len(self.text):
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]
            self._redraw()

    def _abort_line(self) -> None:
        """Ctrl-C on a non-empty line: abandon the buffer, show a fresh prompt.

        The pending command is never executed (IOS ``^C`` behaviour) and any
        traversal state is dropped with it.
        """
        self._redraw()  # clear any stale wrapped rows first
        self.stdout.write("^C\n")
        self.text = ""
        self.cursor = 0
        self.history_index = None
        self.search_prefix = None
        self.draft = ""
        self.stdout.write(self.prompt)
        self.stdout.flush()
        self._vis_row, self._vis_col = 0, len(self.prompt)
        self._render_rows = 1

    def _delete_word(self, text: str, cursor: int) -> str:
        # Scan backwards from cursor to find the start of the previous word.
        i = cursor
        while i > 0 and text[i - 1] in " \t":
            i -= 1
        while i > 0 and text[i - 1] not in " \t":
            i -= 1
        return text[:i] + text[cursor:]

    # -- rendering ---------------------------------------------------------------

    def _width(self) -> int:
        """Current terminal width (queried per render so resizes are honoured)."""
        return max(shutil.get_terminal_size().columns, 1)

    def _cap(self) -> int:
        """Text capacity of one wrapped row (continuations are prompt-indented)."""
        return max(self._width() - len(self.prompt), 1)

    def _cell(self, logical: int) -> tuple[int, int]:
        """Map a logical column (prompt + text offset) to a (row, col) cell.

        Row 0 holds the prompt plus the first ``width - len(prompt)`` text
        characters; every continuation row is indented by the prompt width.
        """
        width = self._width()
        prompt_len = len(self.prompt)
        if logical < width:
            return (0, logical)
        offset = logical - width
        return (
            1 + offset // max(width - prompt_len, 1),
            prompt_len + offset % max(width - prompt_len, 1),
        )

    def _compose(self) -> str:
        """The rendered line: prompt + text with continuation indents.

        Wrap points get an explicit newline plus prompt-width indent, and a
        row-filling render is followed by the indent proactively so the
        terminal never auto-wraps to column 0 behind our back.
        """
        prompt_len = len(self.prompt)
        cap = self._cap()
        chunks = [self.text[i : i + cap] for i in range(0, len(self.text), cap)] or [""]
        parts = [self.prompt, chunks[0]]
        for chunk in chunks[1:]:
            parts.append("\n" + " " * prompt_len + chunk)
        end = prompt_len + len(self.text)
        if end >= self._width() and (end - self._width()) % cap == 0:
            parts.append("\n" + " " * prompt_len)
        return "".join(parts)

    def _redraw(self) -> None:
        """Re-render the whole input line, anchored at the prompt.

        Walks the cursor back to the first row of the line, erases the line
        and every stale wrapped row below it, rewrites prompt + buffer, then
        positions the cursor. This keeps editing and history recall from
        corrupting the prompt or the transcript.
        """
        if self._vis_row > 0:
            # Walk the cursor back up to the first row of this line, then
            # erase it and every stale wrapped row below it.
            self.stdout.write(f"\x1b[{self._vis_row}A")
        self.stdout.write("\r\x1b[J")
        self.stdout.write(self._compose())
        # _compose leaves the terminal cursor exactly at the cell of the
        # line's end, so tracking syncs there before the delta move.
        row, col = self._cell(len(self.prompt) + len(self.text))
        self._render_rows = row + 1
        self._vis_row, self._vis_col = row, col
        self._place_cursor()
        self.stdout.flush()

    def _sync_after_prompt(self) -> None:
        """Re-anchor cursor tracking after the session rendered a new prompt.

        The prompt can change length between lines (mode transitions), so the
        editor re-reads it at the start of every line.
        """
        self._vis_row = 0
        self._vis_col = len(self.prompt)
        self._render_rows = 1

    def _place_cursor(self) -> None:
        """Move the terminal cursor to the cell for ``self.cursor``."""
        target_row, target_col = self._cell(len(self.prompt) + self.cursor)
        d_row = target_row - self._vis_row
        d_col = target_col - self._vis_col
        if d_row < 0:
            self.stdout.write(f"\x1b[{-d_row}A")
        elif d_row > 0:
            self.stdout.write(f"\x1b[{d_row}B")
        if d_col < 0:
            self.stdout.write(f"\x1b[{-d_col}D")
        elif d_col > 0:
            self.stdout.write(f"\x1b[{d_col}C")
        self._vis_row, self._vis_col = target_row, target_col
        self.stdout.flush()

    def _handle_escape(self, ch: str) -> None:
        """Handle ESC-prefixed sequences: arrows, Home, End, and other edits."""
        if ch == "\x1b":
            # Lone ESC: compose prefix for the next key (written to stdout,
            # never to the input object).
            self.stdout.write("\x1b")
            self.stdout.flush()
            return

        # ESC [ <letter> -> arrow keys
        if ch.startswith("\x1b["):
            key = ch[2] if len(ch) > 2 else ""
            if key == "A":  # Up -> previous (prefix-matching) history entry
                self._browse_history(-1)
            elif key == "B":  # Down -> next history entry / back to the draft
                self._browse_history(1)
            elif key == "C":  # Right -> move cursor right
                if self.cursor < len(self.text):
                    self.cursor += 1
                    self._place_cursor()
            elif key == "D":  # Left -> move cursor left
                if self.cursor > 0:
                    self.cursor -= 1
                    self._place_cursor()
            return

        # ESC O <letter> -> Home / End on terminals that send SS3
        if ch.startswith("\x1bO"):
            key = ch[2] if len(ch) > 2 else ""
            if key == "H":  # Home
                self.cursor = 0
                self._place_cursor()
            elif key == "F":  # End
                self.cursor = len(self.text)
                self._place_cursor()

    # -- key input ------------------------------------------------------------

    def read_key(self) -> str:
        """Read one key, decoding ANSI escape sequences (arrows, Home, End)."""
        ch = self.stdin.read(1)
        if not ch:
            return "\x04"  # EOF -> treat as Ctrl-D
        if ch == "\x1b":  # ESC -> start of an escape sequence
            ch2 = self.stdin.read(1)
            if ch2 == "[":
                ch3 = self.stdin.read(1)
                if ch3 in ("A", "B", "C", "D"):
                    return f"\x1b[{ch3}"
                # Anything else after ESC [ (function keys etc.): pass through
                return f"\x1b[{ch3}" if ch3 else "\x1b["
            if ch2 == "O":
                ch3 = self.stdin.read(1)
                return f"\x1bO{ch3}" if ch3 else "\x1bO"
            return ch + ch2
        return ch

    # -- history ------------------------------------------------------------

    def _browse_history(self, delta: int) -> None:
        """Move through the session's command buffer.

        ``delta`` < 0 means "up/older", ``delta`` > 0 means "down/newer".
        With a non-empty input buffer, only entries that start with it are
        recalled (similar-command recall); an empty buffer traverses the full
        chronological history. Moving past the newest (matching) entry
        restores the buffer as it was before traversal began.
        """
        if delta < 0:  # Up
            if self.history_index is None:
                self.history_index = len(self.history)
                self.search_prefix = self.text
                self.draft = self.text
            for index in range(self.history_index - 1, -1, -1):
                entry = self.history[index]
                if entry.startswith(self.search_prefix or ""):
                    self.history_index = index
                    self._recall(entry)
                    return
            return  # no older match: stay on the current entry
        # Down
        if self.history_index is None:
            return  # not browsing; nothing newer exists
        for index in range(self.history_index + 1, len(self.history)):
            entry = self.history[index]
            if entry.startswith(self.search_prefix or ""):
                self.history_index = index
                self._recall(entry)
                return
        # Past the newest (matching) entry: restore the pre-traversal buffer.
        self.history_index = None
        self.search_prefix = None
        self._recall(self.draft)

    def _recall(self, text: str) -> None:
        """Load ``text`` into the buffer.

        The entry is copied — editing the line afterwards never modifies the
        stored history, and recalling never executes anything.
        """
        self.text = text
        self.cursor = len(text)
        self._redraw()

    def reset(self) -> None:
        """Clear the current line and prepare for a fresh command."""
        self.text = ""
        self.cursor = 0
        self.history_index = None
        self.search_prefix = None
        self.draft = ""
        self.eof_requested = False
        self._vis_row = 0
        self._vis_col = len(self.prompt)
        self._render_rows = 1
        self.state = _EditorState.EDIT

    def get_text(self) -> str:
        return self.text


class CliSession(DeviceConfigMixin, SwitchConfigMixin, HostConfigMixin):
    """An interactive IOS-like session attached to one device.

    The session never executes student input as anything other than parsed
    :class:`Command` objects dispatched to their handlers.

    Input is read line-by-line with a tiny line editor that supports:

    - cursor movement inside the current line (left/right arrows)
    - buffer traversal (up/down arrows) through the command history
    - vi-insert-mode helpers: ``Home`` / ``End`` (beginning / end of line)
    - ``Backspace`` to delete the character before the cursor
    - ``Delete`` to delete the character under the cursor
    - ``Ctrl-A`` / ``Ctrl-E`` / ``Ctrl-K`` / ``Ctrl-U`` / ``Ctrl-W``
      for quick editing shortcuts

    Everything is drawn in raw terminal mode so that the kitty-style
    terminal stays responsive while the user is composing a command.
    """

    def __init__(
        self,
        state: NetworkState,
        hostname: str,
        registry: CommandRegistry | None = None,
        scenario: Scenario | None = None,
    ) -> None:
        self._state = state
        self._scenario = scenario
        self._device: Device = state.device(hostname)
        self.registry = registry or build_registry(self._device.device_type)
        self.mode = ConfigMode.USER_EXEC
        self._context: str | int | None = None
        self.history: deque[HistoryEntry] = deque(maxlen=HISTORY_BUFFER_SIZE)
        self.session: CliSession = self  # bound reference used by the mixins
        self._handlers: dict[str, Callable[[object], str]] = self._build_handlers()
        self._stdin = sys.stdin
        self._input_mode = _InputMode.PIPE
        self._terminal_editor: _TerminalEditor | None = None
        # In-memory traversal buffer for ↑/↓ (session-scoped, never persisted).
        # Bounded like the analytics history so long sessions cannot grow it.
        self._buffer: deque[str] = deque(maxlen=HISTORY_BUFFER_SIZE)

    # --- Introspection -------------------------------------------------------

    @property
    def device(self) -> Device:
        return self._device

    @property
    def hostname(self) -> str:
        return self._device.hostname

    @property
    def context(self) -> str | int | None:
        """Current sub-mode context (canonical interface name or VLAN id)."""
        return self._context

    # --- Terminal raw mode ---------------------------------------------------

    def prompt(self) -> str:
        """The IOS-style prompt reflecting the current mode."""
        return prompt_for(self.mode, self._device.hostname)

    def _enable_raw_mode(self) -> None:
        """Raw *input*, cooked output: the terminal layout contract.

        Input flags mirror ``tty.setraw`` (per-key reads, no echo — the editor
        echoes keys itself). Output post-processing (``OPOST``/``ONLCR``) is
        deliberately preserved so every ``\\n`` the session writes still
        returns the carriage. Disabling it was the root cause of progressive
        horizontal drift: in full raw mode each prompt/output line started
        where the previous line ended instead of at column 0.
        """
        if sys.platform == "win32":
            # Windows: minimal support is not worth the complexity in stage 6.
            # The pipe-based code path is used instead.
            return
        fd = sys.stdin.fileno()
        self._old_termios = termios.tcgetattr(fd)
        attrs = termios.tcgetattr(fd)
        try:
            iflag, lflag, cc = 0, 3, 6
            attrs[iflag] &= ~(
                termios.BRKINT
                | termios.ICRNL
                | termios.INPCK
                | termios.ISTRIP
                | termios.IXON
            )
            attrs[lflag] &= ~(
                termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG
            )
            attrs[cc][termios.VMIN] = 1
            attrs[cc][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSAFLUSH, attrs)
        except Exception:
            termios.tcsetattr(fd, termios.TCSAFLUSH, self._old_termios)
            raise

    def _disable_raw_mode(self) -> None:
        """Restore the terminal to its previous state."""
        if sys.platform == "win32":
            return
        try:
            termios.tcsetattr(
                sys.stdin.fileno(), termios.TCSAFLUSH, self._old_termios
            )
        except Exception:
            pass

    # --- Line editor ---------------------------------------------------------

    def _read_line(self, prompt: str) -> tuple[str, bool]:
        """Read one line with the line editor; return ``(text, eof_requested)``.

        ``prompt`` is the already-rendered prompt for this line; the editor
        needs it to anchor redraws (history recall, line editing) at the same
        column the session wrote it to. ``\r`` is accepted for Enter so any
        terminal that returns CR works identically to the pipe code path.
        ``eof_requested`` is ``True`` when the line ended by Ctrl-C/Ctrl-D on
        an empty buffer — it must be captured *before* the editor resets.
        """
        if self._terminal_editor is None:
            self._terminal_editor = _TerminalEditor(
                self._stdin, self._buffer, sys.stdout, prompt
            )
        editor = self._terminal_editor
        editor.prompt = prompt
        editor._sync_after_prompt()
        while True:
            ch = editor.read_key()
            editor.process(ch)
            if editor.state is _EditorState.COMPLETE:
                result = editor.get_text()
                eof_requested = editor.eof_requested
                editor.reset()
                return result, eof_requested

    # --- Accessors shared with the command mixins ------------------------------

    def selected_interface(self) -> Interface:
        """The interface selected by ``interface <if>`` in the current context."""
        if self.mode is not ConfigMode.INTERFACE_CONFIG or not isinstance(
            self._context, str
        ):
            raise InvalidInputError("no interface selected")
        return self._device.get_interface(self._context)

    def require_switch(self) -> Switch:
        if not isinstance(self._device, Switch):
            raise InvalidInputError("this command requires a switch")
        return self._device

    def require_host(self) -> Host:
        if not isinstance(self._device, Host):
            raise InvalidInputError("this command requires an end host")
        return self._device

    def default_host_interface(self) -> Interface:
        """The first host interface, used by ``ip address`` on end hosts."""
        device = self.require_host()
        if not device.interfaces:
            raise InvalidInputError(f"{device.hostname} has no interfaces")
        return next(iter(device.interfaces.values()))

    # --- Execution ------------------------------------------------------------

    def execute(self, raw: str) -> str:
        """Execute one input line and return the output (``""`` for silence).

        Command errors are rendered as IOS-style output; only infrastructure
        problems and session exit raise.
        """
        stripped = raw.strip()
        if not stripped:
            return ""
        try:
            command, argument = self.registry.resolve(stripped, self.mode)
        except CommandError as exc:
            self._record(stripped, ok=False, error=exc.message)
            return render_error(exc)
        assert command.verb in self._handlers, (
            f"no handler for {command.verb!r} in {self.mode} "
            f"(registry has {sorted(self._handlers)})"
        )
        try:
            output = self._handlers[command.verb](argument)
        except SessionExit:
            self._record(stripped, ok=True)
            raise
        except CommandError as exc:
            self._record(stripped, ok=False, error=exc.message)
            return render_error(exc)
        except ValidationError as exc:
            message = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()[:2]
            )
            self._record(stripped, ok=False, error=message)
            return f"% Invalid input: {message}"
        except DomainError as exc:
            self._record(stripped, ok=False, error=exc.message)
            return f"% {exc.message}"
        self._record(stripped, ok=True)
        return output

    def _record(self, raw: str, *, ok: bool, error: str | None = None) -> None:
        self.history.append(
            HistoryEntry(
                timestamp=datetime.now(UTC), raw=raw, mode=self.mode, ok=ok, error=error
            )
        )
        while len(self.history) > HISTORY_BUFFER_SIZE:
            self.history.popleft()
        if ok:
            self._buffer.append(raw.strip())

    # --- Transition and structural handlers -----------------------------------

    def _cmd_enable(self, _argument: object) -> str:
        """Enter privileged EXEC from user EXEC."""
        self.mode = ConfigMode.PRIVILEGED_EXEC
        return ""

    def _cmd_disable(self, _argument: object) -> str:
        """Return to user EXEC from privileged EXEC."""
        self.mode = ConfigMode.USER_EXEC
        return ""

    def _cmd_configure_terminal(self, _argument: object) -> str:
        """Enter global configuration mode from privileged EXEC."""
        self.mode = ConfigMode.GLOBAL_CONFIG
        return ""

    def _cmd_exit(self, _argument: object) -> str:
        """Leave the current mode, or exit the session from user EXEC."""
        if self.mode is ConfigMode.USER_EXEC:
            raise SessionExit()
        self.mode = exit_target(self.mode)
        self._context = None
        return ""

    def _cmd_end(self, _argument: object) -> str:
        """Return to privileged EXEC from any configuration mode."""
        self.mode = ConfigMode.PRIVILEGED_EXEC
        self._context = None
        return ""

    def _cmd_hostname(self, argument: object) -> str:
        self._device.hostname = str(argument)  # validated by the parser
        return ""

    def _cmd_interface(self, argument: object) -> str:
        canonical = str(argument)
        try:
            self._device.get_interface(canonical)
        except UnknownInterfaceError:
            self._create_interface(canonical)
        self._context = canonical
        self.mode = ConfigMode.INTERFACE_CONFIG
        return ""

    def _cmd_vlan(self, argument: object) -> str:
        vlan_id = int(argument)  # type: ignore[arg-type]
        switch = self.require_switch()
        if vlan_id not in switch.vlans:
            switch.create_vlan(vlan_id)
        self._context = vlan_id
        self.mode = ConfigMode.VLAN_CONFIG
        return ""

    def _cmd_name(self, argument: object) -> str:
        vlan_id = self._context
        if not isinstance(vlan_id, int):
            raise InvalidInputError("no VLAN selected")
        self.require_switch().set_vlan_name(vlan_id, str(argument))
        return ""

    def _cmd_show(self, argument: object) -> str:
        """``show`` by itself is incomplete in IOS — it needs a subcommand."""
        raise InvalidInputError("incomplete command")

    def _cmd_help(self, _argument: object) -> str:
        lines = []
        for spec in self.registry.specs_for(self.mode):
            text = spec.name
            if spec.argument:
                text = f"{text} {spec.argument}"
            if spec.description:
                text = f"{text:38} {spec.description}"
            lines.append(text)
        return "\n".join(lines)

    # --- Verification handlers (rendered from state, never hard-coded) --------

    def _cmd_show_vlan_brief(self, _argument: object) -> str:
        return show_vlan_brief(self._state, self._device)

    def _cmd_show_interfaces_trunk(self, _argument: object) -> str:
        return show_interfaces_trunk(self._state, self._device)

    def _cmd_show_ip_interface_brief(self, _argument: object) -> str:
        return show_ip_interface_brief(self._state, self._device)

    def _cmd_show_running_config(self, _argument: object) -> str:
        return show_running_config(self._state, self._device)

    def _cmd_show_startup_config(self, _argument: object) -> str:
        return show_startup_config(self._state, self._device)

    def _cmd_copy_run_start(self, _argument: object) -> str:
        self._device.copy_running_to_startup()
        return "Building configuration...\n[OK]"

    def _cmd_assess(self, _argument: object) -> str:
        """Grade the lab: session renders the structured assessment (doc §55).

        The session owns no grading logic — it delegates to the deterministic
        engine, which reads the authoritative state, and only renders the
        resulting :class:`Assessment`.
        """
        from netlab.grading import grade, render_assessment
        from netlab.scenarios.schema import Scenario

        scenario = self._scenario
        if scenario is None:
            raise InvalidInputError("no scenario is attached to this session")
        assert isinstance(scenario, Scenario)
        assessment = grade(scenario, self._state)
        report = render_assessment(assessment)
        return report

    # --- Dispatch table ---------------------------------------------------------

    def _build_handlers(self) -> dict[str, Callable[[object], str]]:
        handlers: dict[str, Callable[[object], str]] = {
            "enable": self._cmd_enable,
            "disable": self._cmd_disable,
            "configure terminal": self._cmd_configure_terminal,
            "exit": self._cmd_exit,
            "end": self._cmd_end,
            "hostname": self._cmd_hostname,
            "interface": self._cmd_interface,
            "help": self._cmd_help,
            "?": self._cmd_help,
            # Verification (available to every device)
            "show vlan brief": self._cmd_show_vlan_brief,
            "show interfaces trunk": self._cmd_show_interfaces_trunk,
            "show ip interface brief": self._cmd_show_ip_interface_brief,
            "show running-config": self._cmd_show_running_config,
            "show startup-config": self._cmd_show_startup_config,
            "copy running-config startup-config": self._cmd_copy_run_start,
            "assess": self._cmd_assess,
            "submit": self._cmd_assess,  # alias: doc §27 command list
            "show": self._cmd_show,
            # Interface configuration (DeviceConfigMixin)
            "description": self.cmd_description,
            "shutdown": self.cmd_shutdown,
            "no shutdown": self.cmd_no_shutdown,
            "ip address": self.cmd_ip_address,
            "no ip address": self.cmd_no_ip_address,
            "encapsulation": self.cmd_encapsulation,
        }
        if isinstance(self._device, Switch):
            handlers.update(
                {
                    "vlan": self._cmd_vlan,
                    "name": self._cmd_name,
                    # Switchport configuration (SwitchConfigMixin)
                    "switchport mode": self.cmd_switchport_mode,
                    "switchport access vlan": self.cmd_switchport_access_vlan,
                    "switchport trunk allowed vlan": self.cmd_switchport_trunk_allowed,
                    "switchport trunk encapsulation": self.cmd_switchport_trunk_encapsulation,
                    "switchport trunk native vlan": self.cmd_switchport_trunk_native,
                }
            )
        if isinstance(self._device, Host):
            handlers.update(
                {
                    # End-host surface (HostConfigMixin, decision B9)
                    "ip address": self.cmd_host_ip_address,
                    "no ip address": self.cmd_host_no_ip_address,
                    "ip default-gateway": self.cmd_host_gateway,
                }
            )
        return handlers

    # --- Helpers -------------------------------------------------------------

    def _create_interface(self, canonical: str) -> None:
        """IOS-like creation of missing interfaces on configurable devices.

        Subinterfaces require their physical parent to exist. End hosts do not
        support interface creation.
        """
        if self._device.device_type in (DeviceType.PC, DeviceType.SERVER):
            raise InvalidInterfaceError("Invalid interface type and number.")
        parent: str | None = None
        if "." in canonical:
            parent = canonical.rsplit(".", 1)[0]
            if parent not in self._device.interfaces:
                raise InvalidInterfaceError("Invalid interface type and number.")
        self._device.add_interface(Interface(name=canonical, parent=parent))


# --- Console loop ----------------------------------------------------------------


def run_console(
    state: NetworkState,
    hostname: str,
    lines: Iterable[str] | None = None,
    write: Callable[[str], None] = lambda s: None,
    scenario: Scenario | None = None,
) -> None:
    """Run an interactive device session over ``lines``/``write``.

    ``lines`` supplies input (normally ``sys.stdin``) and is used when no
    interactive TTY is attached. ``write`` is a *verbatim* terminal writer:
    strings are appended exactly as given (newlines included — the loop adds
    them where it wants them). On a TTY the prompt is written inline and the
    line editor takes over the rest of that row; raw mode applies to input
    only, so output keeps ``OPOST``/``ONLCR`` and every prompt stays anchored
    at column 0. The editor supports cursor movement, prompt-aware wrapping
    with continuation indent, and ↑/↓ command-buffer traversal over a
    session-scoped, in-memory history. ``scenario`` enables
    ``assess``/``submit``.

    Ends on ``exit`` in user EXEC (raising :class:`SessionExit`), on Ctrl-C or
    Ctrl-D on an empty line (TTY), or on end of input (pipe).
    """
    session = CliSession(state, hostname, scenario=scenario)

    # Detect whether we are on a TTY and can use raw terminal mode.
    input_mode: _InputMode = _InputMode.PIPE
    if sys.platform != "win32":
        try:
            if sys.stdin is not None and hasattr(sys.stdin, "fileno"):
                fileno = sys.stdin.fileno()
                if os.isatty(fileno):
                    input_mode = _InputMode.TTY
        except Exception:
            input_mode = _InputMode.PIPE
    session._input_mode = input_mode

    write(f"Connecting to {hostname}...\n")

    if input_mode is _InputMode.TTY:
        # Enter raw mode so arrow keys, backspace, and control keys behave
        # interactively. Output stays cooked (see _enable_raw_mode), which is
        # what keeps every prompt anchored at column 0.
        session._enable_raw_mode()
        session._terminal_editor = _TerminalEditor(
            sys.stdin, session._buffer, sys.stdout, session.prompt()
        )

    try:
        while True:
            prompt = session.prompt()
            if input_mode is _InputMode.TTY:
                # TTY: the prompt is rendered inline (with the conventional
                # single-space separator) and the editor owns the rest of
                # that row.
                tty_prompt = f"{prompt} "
                write(tty_prompt)
                line, eof_requested = session._read_line(tty_prompt)
                if eof_requested:
                    # Ctrl-C / Ctrl-D on an empty line ends the session,
                    # exactly like pipe EOF.
                    write("\n")
                    return
            else:
                # Pipe: prompts are transcript lines, each on its own row.
                write(f"{prompt}\n")
                line, had_input = _read_pipe_line(lines)
                if not had_input:
                    # EOF: end the session exactly like the committed contract
                    # (input exhaustion terminates, without raising).
                    return
            output = session.execute(line)
            if output:
                write(f"{output}\n")
    finally:
        if input_mode is _InputMode.TTY:
            session._disable_raw_mode()


def _read_pipe_line(lines: Iterable[str] | None) -> tuple[str, bool]:
    """Read one line from a pipe (or the non-TTY code path).

    ``lines`` is an iterable of pre-supplied lines (for example, when the
    student runs ``netlab console < input.txt``) or ``sys.stdin`` when no
    interactive TTY is present. Returns ``(line, had_input)``; ``had_input``
    is ``False`` only at EOF, which the session loop treats as termination.
    """
    if lines is None:
        lines = sys.stdin
    for line in lines:
        return line, True
    return "", False
