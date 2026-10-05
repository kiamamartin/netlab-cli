"""IOS CLI session: mode state machine and command dispatch (doc §11).

The session owns the configuration-mode state machine. Commands are validated
against the current mode by the registry; transition commands move between
modes, and the prompt always reflects the current context. Command handlers
live in :mod:`netlab.ios.commands` as mixins and mutate the authoritative
network state only through the domain models.
"""

from __future__ import annotations

import os
import sys
import termios
import tty
from collections import deque
from collections.abc import Callable, Iterable
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
    """Minimal screen-based line editor backed by a file object.

    This is intentionally small: it implements cursor movement, buffer
    traversal, backspace, delete, and a few control-key shortcuts. It is
    enough to make ``netlab console`` feel like a real interactive terminal
    rather than a line-buffered pipe reader.
    """

    def __init__(self, stdin: Any, history: list[str], stdout: Any = None) -> None:
        self.stdin = stdin
        self.stdout = stdout or sys.stdout
        self.history = history  # written to by the session, read by the editor
        self.state = _EditorState.EDIT
        self.text = ""
        self.cursor = 0
        self.history_index = 0  # 0 = latest; -1 = not browsing history

    # -- public -------------------------------------------------------------

    def process(self, ch: str, chars: list[str]) -> None:
        """Feed one decoded key into the editor."""
        if self.state is _EditorState.COMPLETE:
            # After a command is submitted the editor resets; further keys
            # are still read but ignored (the loop will exit on SessionExit
            # for the final line anyway).
            return

        if ch in ("\x0d", "\x0a", "\x03", "\x04"):  # CR / LF / Ctrl-C / Ctrl-D
            if ch in ("\x03", "\x04") and not self.text:
                # Ctrl-C or Ctrl-D on an empty line acts like EOF.
                chars.append("\x04")
                self.state = _EditorState.COMPLETE
                return
            self.stdout.write("\n")
            self.stdout.flush()
            chars.append("\n")
            self.state = _EditorState.COMPLETE
            return

        if ch in ("\x01", "\x05"):  # Ctrl-A / Ctrl-E -> start / end
            new_cursor = 0 if ch == "\x01" else len(self.text)
            if new_cursor != self.cursor:
                self.stdout.write(f"\x1b[{new_cursor + 1}G")
                self.stdout.flush()
                self.cursor = new_cursor
            return

        if ch == "\x0b":  # Ctrl-K -> kill to end of line
            self.text = self.text[: self.cursor]
            self.stdout.write("\x1b[K")
            self.stdout.flush()
            return

        if ch == "\x15":  # Ctrl-U -> kill whole line
            self.text = ""
            self.cursor = 0
            self.stdout.write("\r\x1b[K")
            self.stdout.flush()
            return

        if ch == "\x17":  # Ctrl-W -> delete previous word
            self.text = self._delete_word(self.text, self.cursor)
            self.stdout.write("\r\x1b[K")
            self.stdout.write(self.text)
            self.stdout.flush()
            return

        if len(ch) == 1 and ord(ch) < 32:
            # Ignored control keys (Ctrl-S, Ctrl-Q, etc.)
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

    def _backspace(self) -> None:
        if self.cursor > 0:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1
            self.stdout.write("\b \b")
            self.stdout.flush()

    def _insert(self, ch: str) -> None:
        self.text = self.text[: self.cursor] + ch + self.text[self.cursor:]
        self.cursor += 1
        self.stdout.write(ch)
        self.stdout.flush()

    def _delete_word(self, text: str, cursor: int) -> str:
        # Scan backwards from cursor to find the start of the previous word.
        i = cursor
        while i > 0 and text[i - 1] in " \t":
            i -= 1
        while i > 0 and text[i - 1] not in " \t":
            i -= 1
        return text[:i] + text[cursor:]

    def _handle_escape(self, ch: str) -> None:
        """Handle ESC-prefixed sequences: arrows, End, and other edits."""
        if ch == "\x1b":
            # Lone ESC: compose prefix for the next key (written to stdout,
            # never to the input object).
            self.stdout.write("\x1b")
            self.stdout.flush()
            return

        # ESC [ <letter> -> arrow keys
        if ch.startswith("\x1b["):
            key = ch[2] if len(ch) > 2 else ""
            if key == "A":  # Up -> previous history entry
                self._browse_history(-1)
            elif key == "B":  # Down -> next history entry
                self._browse_history(1)
            elif key == "C":  # Right -> move cursor right
                if self.cursor < len(self.text):
                    self.cursor += 1
                    self.stdout.write("\x1b[C")
                    self.stdout.flush()
            elif key == "D":  # Left -> move cursor left
                if self.cursor > 0:
                    self.cursor -= 1
                    self.stdout.write("\x1b[D")
                    self.stdout.flush()
            else:
                # Unknown escape sequence: drop the prefix
                return
            return

        # ESC O <letter> -> other keys (often used by terminators)
        if ch.startswith("\x1bO"):
            key = ch[2] if len(ch) > 2 else ""
            if key == "H":  # Home
                if self.cursor > 0:
                    self.stdout.write(f"\x1b[{self.cursor}D")
                    self.stdout.flush()
                    self.cursor = 0
            elif key == "F":  # End
                if self.cursor < len(self.text):
                    self.stdout.write(f"\x1b[{len(self.text) - self.cursor}C")
                    self.stdout.flush()
                    self.cursor = len(self.text)
            else:
                return
            return

    # -- history ------------------------------------------------------------

    def _browse_history(self, delta: int) -> None:
        """Move through the in-memory command history.

        ``delta`` < 0 means "up/older", ``delta`` > 0 means "down/newer".
        """
        if delta < 0:
            # Going up: load the previous command (if any)
            if self.history_index <= 0:
                return  # already at the oldest history entry
            self.history_index -= 1
            candidate = (
                self.history[self.history_index]
                if 0 <= self.history_index < len(self.history)
                else ""
            )
            if candidate:
                self.text = candidate
                self.cursor = len(self.text)
                self.stdout.write("\r\x1b[K")
                self.stdout.write(self.text)
                self.stdout.flush()
        else:
            # Going down: return to a fresh empty command line
            if self.history_index > 0:
                self.history_index = 0
                self.text = ""
                self.cursor = 0
                self.stdout.write("\r\x1b[K")
                self.stdout.flush()
            # else: not browsing history yet; ignore

    def reset(self) -> None:
        """Clear the current line and prepare for a fresh command."""
        self.text = ""
        self.cursor = 0
        self.history_index = 0
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
    ) -> None:
        self._state = state
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
        self._buffer: list[str] = []  # in-memory history of submitted lines

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
        """Put the terminal in raw mode so arrow keys and backspace are fine."""
        if sys.platform == "win32":
            # Windows: minimal support is not worth the complexity in stage 6.
            # The pipe-based code path is used instead.
            return
        self._old_termios = termios.tcgetattr(sys.stdin.fileno())
        try:
            tty.setraw(sys.stdin.fileno())
        except Exception:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, self._old_termios)
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

    def _read_key(self) -> str:
        """Read a single key, decoding ANSI escape sequences for arrows."""
        ch = self._stdin.read(1)
        if not ch:
            return "\x04"  # Ctrl-D -> EOF
        if ch == "\x1b":  # ESC -> start of an escape sequence
            ch2 = self._stdin.read(1)
            if ch2 == "[":
                ch3 = self._stdin.read(1)
                if ch3 in ("A", "B", "C", "D"):
                    return {
                        "A": "\x1b[A",
                        "B": "\x1b[B",
                        "C": "\x1b[C",
                        "D": "\x1b[D",
                    }[ch3]
                # Ignore anything else after ESC [ (e.g. function keys)
                return ch2 + ch3
            return ch + ch2
        return ch

    def _read_line(self) -> str:
        """Read one line with a minimal line editor and return the text.

        ``\r`` is translated to ``\n`` so any terminal that returns CR for
        Enter works identically to the pipe-based code path below.
        """
        if self._terminal_editor is None:
            self._terminal_editor = _TerminalEditor(self._stdin, self._buffer, sys.stdout)
        editor = self._terminal_editor
        chars: list[str] = []
        while True:
            ch = self._read_key()
            editor.process(ch, chars)
            if editor.state is _EditorState.COMPLETE:
                result = editor.get_text()
                editor.reset()
                return result

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
) -> None:
    """Run an interactive device session over ``lines``/``write``.

    ``lines`` supplies input (normally ``sys.stdin``) and is used when no
    interactive TTY is attached. When the session is attached to a TTY and the
    terminal supports ANSI escapes, raw mode plus a small line editor is
    enabled, giving the student cursor control with arrow keys and previous-
    command buffer traversal.

    Ends on ``exit`` in user EXEC (raising :class:`SessionExit`) or end of
    input.
    """
    session = CliSession(state, hostname)

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
        # interactively.
        session._enable_raw_mode()
        session._terminal_editor = _TerminalEditor(sys.stdin, session._buffer, sys.stdout)

    try:
        while True:
            write(session.prompt())
            if input_mode is _InputMode.TTY:
                line = session._read_line()
            else:
                line, had_input = _read_pipe_line(lines)
                if not had_input:
                    # EOF: end the session exactly like the committed contract
                    # (input exhaustion terminates, without raising).
                    write("\n")
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
