"""Console editor tests: rendering layout, wrapping, and buffer traversal.

The editor is exercised against :class:`FakeTerm`, a minimal cooked-output
terminal emulator, so the ANSI stream the editor emits can be asserted as an
actual screen layout. This pins the milestone rendering contract:

- every prompt is anchored at column 0 — repeated commands never accumulate
  horizontal offset;
- command output never shifts the origin of subsequent prompts;
- long input wraps with a prompt-width continuation indent that is local to
  the command being edited, and stale wrapped rows are cleared on redraw;
- ↑/↓ traverse the session's command buffer without executing anything.

``term_width`` pins ``shutil.get_terminal_size`` so wrap math is deterministic
(including a mid-session resize).
"""

import io
import re
import shutil
from collections import deque, namedtuple
from collections.abc import Sequence

import pytest

from netlab.devices.interface import Interface
from netlab.devices.switch import Switch
from netlab.ios.machine import HISTORY_BUFFER_SIZE, CliSession, _EditorState, _TerminalEditor
from netlab.network.state import NetworkState
from netlab.network.topology import Topology

PROMPT = "R1#"
RENDERED = "R1# "  # the interactive console renders the prompt + one space

_ESC_RE = re.compile(r"\x1b\[(?P<params>\d*)(?P<letter>[A-Za-z])")
_Size = namedtuple("_Size", ["columns", "lines"])


class FakeTerm:
    """Minimal cooked-output terminal emulator.

    Interprets exactly the sequences the console emits: ``\\r``, ``\\n`` (a
    newline also returns the carriage — output is cooked, which is the layout
    contract the session relies on), backspace, ``\\x1b[K`` (erase to end of
    line), ``\\x1b[J`` (erase from cursor to end of screen), and the relative
    cursor moves ``\\x1b[<n>A/B/C/D``. Auto-wrap emulates a real terminal's
    pending-wrap: after a character fills the last column the cursor stays on
    that column until the next character, newline, or cursor move.
    """

    def __init__(self, width: int = 80, height: int = 40) -> None:
        self.width = width
        self.height = height
        self.screen = ["" for _ in range(height)]
        self.row = 0
        self.col = 0
        self.pending_wrap = False

    def flush(self) -> None:
        """The editor flushes stdout after every render."""

    def write(self, text: str) -> None:
        index = 0
        while index < len(text):
            if text[index] == "\x1b":
                index = self._escape(text, index)
                continue
            ch = text[index]
            if ch == "\r":
                self.col = 0
                self.pending_wrap = False
            elif ch == "\n":
                self.col = 0
                self.pending_wrap = False
                self._newline()
            elif ch == "\b":
                self.col = max(0, self.col - 1)
                self.pending_wrap = False
            elif ch == "\x07":
                pass  # bell
            else:
                self._put(ch)
            index += 1

    def _escape(self, text: str, index: int) -> int:
        match = _ESC_RE.match(text, index)
        assert match is not None, f"unhandled escape at {index}: {text[index : index + 8]!r}"
        params, letter = match.group("params"), match.group("letter")
        count = int(params) if params else 1
        if letter == "K":
            self.screen[self.row] = self.screen[self.row][: self.col]
        elif letter == "J":
            self.screen[self.row] = self.screen[self.row][: self.col]
            for row in range(self.row + 1, self.height):
                self.screen[row] = ""
        elif letter == "A":
            self.row = max(0, self.row - count)
            self.pending_wrap = False
        elif letter == "B":
            self.row = min(self.height - 1, self.row + count)
            self.pending_wrap = False
        elif letter == "C":
            self.col = min(self.width - 1, self.col + count)
            self.pending_wrap = False
        elif letter == "D":
            self.col = max(0, self.col - count)
            self.pending_wrap = False
        elif letter == "G":
            self.col = min(self.width - 1, count - 1)
            self.pending_wrap = False
        else:  # pragma: no cover - guards against future editor sequences
            raise AssertionError(f"unhandled CSI {letter!r}")
        return match.end()

    def _newline(self) -> None:
        if self.row + 1 >= self.height:
            self.screen.pop(0)
            self.screen.append("")
        else:
            self.row += 1

    def _put(self, ch: str) -> None:
        if self.pending_wrap:
            self.col = 0
            self.pending_wrap = False
            self._newline()
        line = self.screen[self.row]
        if len(line) < self.col:
            line = line + " " * (self.col - len(line))
        self.screen[self.row] = line[: self.col] + ch + line[self.col + 1 :]
        if self.col + 1 >= self.width:
            self.pending_wrap = True  # cursor parked on the last column
        else:
            self.col += 1

    def rows(self) -> list[str]:
        """Non-empty screen rows, top to bottom."""
        return [row for row in self.screen if row]


@pytest.fixture()
def term_width(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Pin ``shutil.get_terminal_size`` so wrap math is deterministic."""
    holder = {"columns": 80}
    monkeypatch.setattr(shutil, "get_terminal_size", lambda: _Size(holder["columns"], 24))
    return holder


def make_editor(
    keys: str, history: Sequence[str], prompt: str = RENDERED, term: FakeTerm | None = None
) -> tuple[_TerminalEditor, FakeTerm]:
    term = term or FakeTerm()
    editor = _TerminalEditor(io.StringIO(keys), history, term, prompt)
    return editor, term


def run_line(
    editor: _TerminalEditor, term: FakeTerm, command: str, output: str = ""
) -> None:
    """One interactive line, exactly like ``run_console``'s TTY branch."""
    term.write(editor.prompt)
    editor._sync_after_prompt()
    while editor.state is not _EditorState.COMPLETE:
        editor.process(editor.read_key())
    editor.reset()
    if output:
        term.write(f"{output}\n")


def drive_lines(
    commands: list[str],
    history: Sequence[str],
    outputs: list[str] | None = None,
    prompt: str = RENDERED,
    term: FakeTerm | None = None,
) -> tuple[_TerminalEditor, FakeTerm]:
    """Run several command lines through the editor like the session loop."""
    keys = "".join(f"{command}\r" for command in commands)
    editor, term = make_editor(keys, history, prompt, term=term)
    for index, command in enumerate(commands):
        run_line(editor, term, command, outputs[index] if outputs else "")
    return editor, term


def feed(
    keys: str, history: Sequence[str], prompt: str = RENDERED, term: FakeTerm | None = None
) -> tuple[_TerminalEditor, FakeTerm]:
    """Feed raw keys (no implicit Enter) until the stream is exhausted."""
    editor, term = make_editor(keys, history, prompt, term)
    term.write(prompt)
    editor._sync_after_prompt()
    while editor.stdin.tell() < len(keys):
        editor.process(editor.read_key())
    return editor, term


# --- Rendering ----------------------------------------------------------------


class TestRendering:
    def test_first_command_renders_at_column_zero(self, term_width) -> None:
        _editor, term = drive_lines(["show vlan brief"], deque())
        assert term.screen[0] == "R1# show vlan brief"

    def test_repeated_commands_keep_the_same_origin(self, term_width) -> None:
        commands = ["show vlan brief", "show ip interface brief", "show running-config"]
        outputs = [
            "VLAN Name                             Status",
            "Interface      IP-Address/Mask        Status",
            "Building configuration...",
        ]
        _editor, term = drive_lines(commands, deque(), outputs=outputs)
        prompt_rows = [row for row in term.rows() if row.startswith(PROMPT)]
        assert len(prompt_rows) == 3
        for row in term.rows():
            if PROMPT in row:
                assert row.startswith(PROMPT), f"prompt drifted: {row!r}"

    def test_output_blocks_do_not_shift_subsequent_prompts(self, term_width) -> None:
        wide_output = "\n".join(f"line {index:02d} " + "x" * 70 for index in range(3))
        _editor, term = drive_lines(
            ["show running-config", "show vlan brief"],
            deque(),
            outputs=[wide_output, ""],
        )
        # The wide output block wrapped over several rows; the next prompt
        # still starts at column 0 of its own row.
        prompt_rows = [row for row in term.rows() if row.startswith(PROMPT)]
        assert len(prompt_rows) == 2
        assert prompt_rows[1] == "R1# show vlan brief"

    def test_long_command_wraps_with_prompt_width_indent(self, term_width) -> None:
        long_command = "show interfaces GigabitEthernet0/0 description " + "tail " * 8
        term = FakeTerm(width=80)
        assert len(RENDERED) + len(long_command) > term.width  # really wraps
        _editor, term = drive_lines([long_command], deque(), term=term)
        capacity = term.width - len(RENDERED)
        assert term.screen[0] == RENDERED + long_command[:capacity]
        # Continuation aligns under the start of the command text.
        assert term.screen[1] == " " * len(RENDERED) + long_command[capacity:]

    def test_wrapped_command_then_prompt_returns_to_left_margin(self, term_width) -> None:
        _editor, term = drive_lines(["x" * 150], deque())
        assert term.screen[1].startswith(" " * len(RENDERED))
        for row in term.rows():
            if PROMPT in row:
                assert row.startswith(PROMPT)

    def test_recall_after_wrapped_entry_clears_stale_rows(self, term_width) -> None:
        long_cmd = "show interfaces GigabitEthernet0/0 description " + "tail " * 8
        assert len(RENDERED) + len(long_cmd) > 80  # wraps on the default width
        history = deque(["show vlan brief", long_cmd])  # long entry is newest
        editor, term = feed("\x1b[A", history)
        assert len(term.rows()) >= 2  # long command wrapped
        editor.process("\x1b[A")  # recall the shorter entry
        assert [row for row in term.rows() if row.strip()] == ["R1# show vlan brief"]

    def test_resize_does_not_introduce_horizontal_drift(self, term_width) -> None:
        commands = ["show vlan brief", "show interfaces trunk", "show version", "help"]
        editor, term = make_editor(
            "".join(f"{command}\r" for command in commands), deque()
        )
        for index, command in enumerate(commands):
            if index == 2:
                term_width["columns"] = 120  # terminal resized mid-session
            run_line(editor, term, command)
        for row in term.rows():
            if PROMPT in row:
                assert row.startswith(PROMPT), f"drift after resize: {row!r}"

    def test_multiple_consecutive_executions_stay_stable(self, term_width) -> None:
        commands = ["show vlan brief"] * 10
        _editor, term = drive_lines(commands, deque())
        prompt_rows = [row for row in term.rows() if row.startswith(PROMPT)]
        assert prompt_rows == [f"{RENDERED}show vlan brief"] * 10


# --- History traversal ----------------------------------------------------------


class TestHistoryTraversal:
    def test_up_recalls_most_recent_command(self, term_width) -> None:
        history = deque(["show vlan brief", "show ip interface brief"])
        editor, term = feed("\x1b[A", history)
        assert editor.text == "show ip interface brief"
        # The prompt is re-rendered intact in front of the recalled command.
        assert term.screen[0] == "R1# show ip interface brief"

    def test_repeated_up_traverses_backward(self, term_width) -> None:
        history = deque(["show vlan brief", "show ip interface brief"])
        editor, _term = feed("\x1b[A\x1b[A", history)
        assert editor.text == "show vlan brief"

    def test_up_stops_at_the_oldest_entry(self, term_width) -> None:
        history = deque(["show vlan brief", "show ip interface brief"])
        editor, _term = feed("\x1b[A\x1b[A\x1b[A", history)
        assert editor.text == "show vlan brief"

    def test_down_traverses_forward(self, term_width) -> None:
        history = deque(["show vlan brief", "show ip interface brief"])
        editor, _term = feed("\x1b[A\x1b[A\x1b[B", history)
        assert editor.text == "show ip interface brief"

    def test_down_past_newest_restores_empty_buffer(self, term_width) -> None:
        history = deque(["show vlan brief", "show ip interface brief"])
        editor, term = feed("\x1b[A\x1b[B", history)
        assert editor.text == ""
        assert term.screen[0] == RENDERED

    def test_down_without_traversal_is_a_noop(self, term_width) -> None:
        history = deque(["show vlan brief"])
        editor, _term = feed("\x1b[B", history)
        assert editor.text == ""

    def test_up_with_empty_history_is_a_noop(self, term_width) -> None:
        editor, _term = feed("\x1b[A", deque())
        assert editor.text == ""

    def test_traversal_never_executes_or_mutates_history(self, term_width) -> None:
        history = deque(["show vlan brief", "show ip interface brief"])
        before = list(history)
        editor, _term = feed("\x1b[A\x1b[A\x1b[B\x1b[B", history)
        assert editor.state is _EditorState.EDIT
        assert list(history) == before

    def test_editing_recalled_command_does_not_mutate_history(self, term_width) -> None:
        history = deque(["show vlan brief", "show ip interface brief"])
        editor, _term = feed("\x1b[A" + " extra", history)
        assert editor.text == "show ip interface brief extra"
        assert list(history) == ["show vlan brief", "show ip interface brief"]


# --- Prefix / similar-command recall ------------------------------------------------


PREFIX_HISTORY = [
    "show vlan brief",
    "show ip interface brief",
    "show running-config",
    "configure terminal",
    "show interfaces",
]


class TestPrefixRecall:
    def test_up_with_prefix_recalls_most_recent_match(self, term_width) -> None:
        editor, _term = feed("show\x1b[A", deque(PREFIX_HISTORY))
        assert editor.text == "show interfaces"

    def test_prefix_traversal_skips_nonmatching_commands(self, term_width) -> None:
        editor, _term = feed("show\x1b[A\x1b[A", deque(PREFIX_HISTORY))
        assert editor.text == "show running-config"

    def test_prefix_traversal_cycles_through_matches(self, term_width) -> None:
        editor, _term = feed("show" + "\x1b[A" * 4, deque(PREFIX_HISTORY))
        assert editor.text == "show vlan brief"
        # No older matching entry: traversal stays on the oldest match.
        editor, _term = feed("show" + "\x1b[A" * 5, deque(PREFIX_HISTORY))
        assert editor.text == "show vlan brief"

    def test_prefix_down_traverses_forward_through_matches(self, term_width) -> None:
        editor, _term = feed(
            "show" + "\x1b[A" * 3 + "\x1b[B", deque(PREFIX_HISTORY)
        )
        assert editor.text == "show running-config"

    def test_prefix_down_past_newest_restores_typed_prefix(self, term_width) -> None:
        editor, _term = feed("show" + "\x1b[A\x1b[A" + "\x1b[B\x1b[B", deque(PREFIX_HISTORY))
        assert editor.text == "show"

    def test_no_prefix_traverses_full_history_chronologically(self, term_width) -> None:
        editor, _term = feed("\x1b[A", deque(PREFIX_HISTORY))
        assert editor.text == "show interfaces"
        editor, _term = feed("\x1b[A\x1b[A", deque(PREFIX_HISTORY))
        assert editor.text == "configure terminal"


# --- Keyboard conflict semantics --------------------------------------------------


class TestKeyboardConflicts:
    def test_ctrl_c_on_recalled_command_aborts_without_executing(self, term_width) -> None:
        history = deque(["show vlan brief"])
        editor, term = feed("\x1b[A", history)
        assert editor.text == "show vlan brief"
        editor.process("\x03")
        assert editor.state is _EditorState.EDIT  # still editing
        assert editor.text == ""  # buffer abandoned
        assert list(history) == ["show vlan brief"]  # nothing executed
        assert term.screen[0].startswith("R1# show vlan brief^C")
        assert term.screen[1] == RENDERED  # fresh prompt at column 0

    def test_ctrl_c_on_empty_line_requests_eof(self, term_width) -> None:
        editor, _term = feed("", deque())
        editor.process("\x03")
        assert editor.eof_requested is True
        assert editor.state is _EditorState.COMPLETE

    def test_ctrl_d_on_empty_line_requests_eof(self, term_width) -> None:
        editor, _term = feed("", deque())
        editor.process("\x04")
        assert editor.eof_requested is True
        assert editor.state is _EditorState.COMPLETE

    def test_ctrl_d_on_nonempty_line_deletes_under_cursor(self, term_width) -> None:
        editor, _term = feed("showx\x1b[D\x04", deque())  # cursor under 'x'
        assert editor.text == "show"

    def test_ctrl_l_is_ignored(self, term_width) -> None:
        editor, _term = feed("sh\x0cow", deque())
        assert editor.text == "show"

    def test_ctrl_u_clears_the_line(self, term_width) -> None:
        editor, term = feed("show vlan\x15", deque())
        assert editor.text == ""
        assert term.screen[0] == RENDERED

    def test_arrows_edit_mid_line(self, term_width) -> None:
        editor, term = feed("abc\x1b[D\x1b[Dx", deque())
        assert editor.text == "axbc"
        assert term.screen[0] == "R1# axbc"
        editor.process("\x01")
        assert editor.cursor == 0
        editor.process("\x05")
        assert editor.cursor == len(editor.text)


# --- Session-level history semantics ------------------------------------------------


def build_topology() -> Topology:
    switch = Switch(hostname="SW1")
    switch.add_interface(Interface(name="GigabitEthernet0/1"))
    switch.add_interface(Interface(name="GigabitEthernet0/2"))
    return Topology(devices={"SW1": switch}, links=[])


class TestSessionHistoryBuffer:
    def make_session(self) -> CliSession:
        return CliSession(NetworkState(topology=build_topology()), "SW1")

    def test_command_stored_after_successful_execution(self) -> None:
        session = self.make_session()
        session.execute("enable")
        assert list(session._buffer) == ["enable"]

    def test_empty_commands_are_not_stored(self) -> None:
        session = self.make_session()
        session.execute("")
        session.execute("   ")
        assert list(session._buffer) == []

    def test_failed_commands_are_not_stored(self) -> None:
        session = self.make_session()
        session.execute("vlan 10")  # invalid in user EXEC
        assert list(session._buffer) == []

    def test_traversal_buffer_is_bounded(self) -> None:
        session = self.make_session()
        for index in range(HISTORY_BUFFER_SIZE + 25):
            session.execute("enable" if index % 2 else "disable")
        assert len(session._buffer) == HISTORY_BUFFER_SIZE

    def test_history_is_isolated_between_sessions(self) -> None:
        first = self.make_session()
        second = self.make_session()
        first.execute("enable")
        assert list(second._buffer) == []
        second.execute("disable")
        assert list(first._buffer) == ["enable"]

    def test_history_is_in_memory_only(self) -> None:
        session = self.make_session()
        session.execute("enable")
        # A fresh session starts from an empty buffer: nothing was persisted.
        assert list(self.make_session()._buffer) == []
        assert isinstance(session._buffer, deque)

    def test_recalled_command_can_be_resubmitted(self) -> None:
        session = self.make_session()
        session.execute("enable")
        session.execute("disable")
        editor, _term = feed("\x1b[A\x1b[A\r", session._buffer, prompt="SW1# ")
        session.execute(editor.get_text())
        # Duplicate submissions are retained (IOS-like history).
        assert list(session._buffer) == ["enable", "disable", "enable"]
