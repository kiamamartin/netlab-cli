"""TTY-mode console tests over a real pseudo-terminal.

``run_console`` selects its raw-input/cooked-output code path only when stdin
is a tty. These tests drive the genuine code path through ``os.openpty`` with
no editor mocking: the session reads from the pty slave and writes back into
it, and the test reads the *kernel-translated* byte stream from the master —
so ``OPOST``/``ONLCR`` behaviour is exercised for real, not assumed.

Pins the milestone layout contract at the byte level:

- output mode keeps ``OPOST``/``ONLCR``: every ``\\n`` the session writes
  reaches the terminal as ``\\r\\n`` — the direct regression test for the
  diagonal-drift defect (full raw mode swallowed the carriage return);
- prompts always start at column 0, even after command output;
- ↑ recalls history, Enter executes the recalled buffer, and traversal alone
  never executes anything;
- Ctrl-C aborts a half-typed line without executing it; Ctrl-C/Ctrl-D on an
  empty line end the session like EOF.
"""

import os
import re
import select
import sys
import threading
import time

from netlab.devices.interface import Interface
from netlab.devices.switch import Switch
from netlab.ios.machine import run_console
from netlab.network.state import NetworkState
from netlab.network.topology import Topology


def build_topology() -> Topology:
    switch = Switch(hostname="SW1")
    switch.add_interface(Interface(name="GigabitEthernet0/1"))
    switch.add_interface(Interface(name="GigabitEthernet0/2"))
    return Topology(devices={"SW1": switch}, links=[])


def drive_tty_session(keys: bytes, write_pause: float = 0.35) -> str:
    """Run a real console session on a pty with ``keys``; return its output.

    ``sys.stdin`` and ``sys.stdout`` both point at the pty slave (so
    ``run_console`` takes its TTY path and all session output — including the
    editor's direct echoes — passes through the kernel line discipline), and
    the test drains the master side for the translated bytes.
    """
    master, slave = os.openpty()
    os.set_blocking(master, False)
    stdin_backup, stdout_backup = sys.stdin, sys.stdout
    sys.stdin = os.fdopen(os.dup(slave), "r")
    sys.stdout = os.fdopen(os.dup(slave), "w")
    failures: list[BaseException] = []
    callback_out: list[str] = []

    try:
        def run() -> None:
            try:
                run_console(
                    NetworkState(topology=build_topology()),
                    "SW1",
                    None,
                    lambda s: (callback_out.append(s), sys.stdout.write(s)),
                )
            except OSError:
                pass  # master closed under a still-blocked read: harmless
            except BaseException as exc:  # surfaced after join, never swallowed
                failures.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        time.sleep(write_pause)  # let the session enter raw mode first
        os.write(master, keys)
        worker.join(timeout=5.0)
        if failures:
            raise AssertionError(f"console session crashed: {failures[0]!r}") from failures[0]
        transcript = _drain_master(master)
        if "SW1>" not in transcript and "SW1#" not in transcript:
            raise AssertionError(
                "pty produced no prompt; "
                f"write-callback stream: {''.join(callback_out)!r}"
            )
        return transcript
    finally:
        sys.stdin.close()
        sys.stdout.close()
        sys.stdin, sys.stdout = stdin_backup, stdout_backup
        os.close(master)
        os.close(slave)


def _drain_master(master: int, quiet: float = 0.6, timeout: float = 4.0) -> str:
    """Read everything available on the master until a quiet gap."""
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout * 4
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.1)
        if not ready:
            if chunks and (time.monotonic() - last_data) > quiet:
                break
            continue
        try:
            data = os.read(master, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
        last_data = time.monotonic()
    return b"".join(chunks).decode("utf-8", errors="replace")


class TestRawModeLayout:
    def test_lf_newlines_translate_to_crlf(self) -> None:
        """Every ``\\n`` the session writes must reach the terminal as ``\\r\\n``.

        With ``OPOST``/``ONLCR`` disabled (the pre-milestone ``tty.setraw``
        behaviour) a bare ``\\n`` moves down without returning the carriage —
        exactly the observed diagonal drift.
        """
        transcript = drive_tty_session(b"enable\r" + b"\x04")
        assert transcript, "session produced no output"
        assert "\r\n" in transcript, "no CRLF translation: OPOST is disabled"
        bare = re.search(r"(?<!\r)\n", transcript)
        assert bare is None, (
            f"bare LF at offset {bare.start() if bare else -1}: {transcript!r}"
        )

    def test_prompt_stays_at_column_zero_after_output(self) -> None:
        transcript = drive_tty_session(b"show vlan brief\r" + b"\x04")
        prompts = [match.start() for match in re.finditer(r"SW1[>#]", transcript)]
        assert len(prompts) >= 2, transcript
        for start in prompts:
            if start > 0:
                assert transcript[start - 1] in "\r\n", (
                    f"prompt not at column 0: {transcript[max(0, start - 15) : start + 15]!r}"
                )
            assert transcript[start + 4] == " ", "prompt missing separator"


class TestTraversalOverPty:
    def test_up_arrow_recalls_and_enter_executes(self) -> None:
        transcript = drive_tty_session(
            b"enable\r" b"disable\r" b"\x1b[A\x1b[A" b"\r" b"\x04"
        )
        # enable -> SW1#, disable -> SW1>, recalled enable -> SW1# again.
        assert transcript.count("SW1#") == 2, transcript

    def test_traversal_without_enter_does_not_execute(self) -> None:
        # After traversal the buffer holds 'enable' (non-empty), so the
        # session ends via Ctrl-C (abort the recalled buffer, never executed)
        # followed by Ctrl-D on the now-empty prompt.
        transcript = drive_tty_session(
            b"enable\r" b"disable\r" b"\x1b[A\x1b[A" b"\x03" b"\x04"
        )
        # Only the explicitly submitted 'enable' took effect: exactly one '#'
        # prompt, and the session is back in user EXEC when it ends.
        assert transcript.count("SW1#") == 1, transcript
        assert "^C" in transcript
        assert "SW1>" in transcript


class TestCtrlSemantics:
    def test_ctrl_c_on_line_aborts_without_executing(self) -> None:
        transcript = drive_tty_session(b"enable\r" b"sho\x03" b"\x04")
        assert "^C" in transcript, transcript
        # The half-typed 'sho' never resolved: no error output, no execution,
        # and the session ends from user EXEC.
        assert "Invalid" not in transcript
        assert "SW1>" in transcript

    def test_ctrl_d_on_empty_line_ends_session(self) -> None:
        transcript = drive_tty_session(b"enable\r" + b"\x04")
        # 'enable' executed once, then Ctrl-D on the empty privileged prompt.
        assert transcript.count("SW1#") == 1, transcript
        assert transcript.count("SW1>") == 1, transcript
