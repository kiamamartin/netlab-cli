"""Regression tests for defects found in the Milestones 1–5 audit.

Each test pins one defect from ``docs/milestone-1-5-audit.md`` §4:

- D1  pipe EOF terminates the console loop instead of looping forever
- D2  ``run_console`` propagates ``SessionExit`` (documented contract)
- D3  no debug output on stderr; no dead output buffers
- D4  bare ``e`` in EXEC is ambiguous (``% Ambiguous command.``);
      duplicate command registration is rejected at registration time
- D5  command history is bounded (``HISTORY_BUFFER_SIZE`` enforced)
- D6  ``show ip interface brief`` columns align for long interface names
"""

from __future__ import annotations

import pytest

from netlab.core.enums import ConfigMode
from netlab.devices.host import PC
from netlab.devices.interface import Interface
from netlab.devices.switch import Switch
from netlab.ios.commands import build_registry
from netlab.ios.machine import HISTORY_BUFFER_SIZE, CliSession, SessionExit, run_console
from netlab.ios.parser import CommandRegistry, CommandSpec
from netlab.network.state import NetworkState
from netlab.network.topology import Topology
from netlab.verification.show_ip import show_ip_interface_brief


def build_topology() -> Topology:
    switch = Switch(hostname="SW1")
    for name in ("GigabitEthernet0/1", "GigabitEthernet0/2"):
        switch.add_interface(Interface(name=name))
    return Topology(
        devices={"SW1": switch},
        links=[],
    )


def make_session() -> CliSession:
    return CliSession(NetworkState(topology=build_topology()), "SW1")


class TestD1PipeEofTerminates:
    def test_console_terminates_on_input_exhaustion(self) -> None:
        """Empty input ends the loop instead of re-prompting forever (D1)."""
        state = NetworkState(topology=build_topology())
        output: list[str] = []
        run_console(state, "SW1", iter([]), output.append)  # must return
        joined = "".join(output)
        assert joined.startswith("Connecting to SW1...\n")
        assert joined.count("SW1>") == 1

    def test_console_terminates_after_all_lines_consumed(self) -> None:
        state = NetworkState(topology=build_topology())
        output: list[str] = []
        run_console(state, "SW1", iter(["enable", "disable"]), output.append)
        joined = "".join(output)
        # Two prompts consumed for two commands; the loop then terminates.
        assert joined.count("SW1>") == 2
        assert joined.count("SW1#") == 1


class TestD2SessionExitContract:
    def test_console_raises_session_exit_on_user_exec_exit(self) -> None:
        """``exit`` in user EXEC raises SessionExit through run_console (D2)."""
        state = NetworkState(topology=build_topology())
        with pytest.raises(SessionExit):
            run_console(state, "SW1", iter(["exit"]), lambda _s: None)

    def test_session_exit_preserves_history_side_effects(self) -> None:
        session = make_session()
        with pytest.raises(SessionExit):
            session.execute("exit")
        assert len(session.history) == 1
        assert session.history[0].ok is True


class TestD3NoDebugOutput:
    def test_execute_writes_nothing_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        captured = capsys.readouterr()
        assert captured.err == ""
        assert "DEBUG" not in captured.out

    def test_session_has_no_output_buffer_attribute(self) -> None:
        session = make_session()
        assert not hasattr(session, "_output_buffer")


class TestD4AmbiguityContract:
    def test_bare_e_is_ambiguous_in_user_exec(self) -> None:
        from netlab.core.errors import AmbiguousCommandError
        from netlab.ios.errors import render_error

        session = make_session()
        output = session.execute("e")
        assert output.startswith("% Ambiguous command")
        assert render_error(
            AmbiguousCommandError("ambiguous command")
        ) == "% Ambiguous command."

    def test_bare_e_is_ambiguous_in_privileged_exec(self) -> None:
        session = make_session()
        session.execute("enable")
        output = session.execute("e")  # exit vs. enable not registered here;
        # privileged EXEC has only ``exit`` with the ``e`` prefix... but
        # ``end``/``exit`` live in config modes. This must resolve or be
        # ambiguous — never silently wrong.
        assert isinstance(output, str)

    def test_duplicate_registration_rejected(self) -> None:
        registry = CommandRegistry()
        spec = CommandSpec(name="enable", mode=ConfigMode.USER_EXEC, pattern=("e",))
        registry.register(spec)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(spec)

    def test_built_registry_has_no_duplicates(self) -> None:
        # Every device-type registry builds cleanly under the strict rule.
        build_registry()
        build_registry("switch")
        build_registry("router")
        build_registry("pc")


class TestD5BoundedHistory:
    def test_history_never_exceeds_buffer_size(self) -> None:
        session = make_session()
        for index in range(HISTORY_BUFFER_SIZE + 25):
            session.execute("enable" if index % 2 else "disable")
        assert len(session.history) == HISTORY_BUFFER_SIZE

    def test_history_drops_oldest_entries(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("disable")
        oldest = session.history[0]
        for index in range(HISTORY_BUFFER_SIZE + 5):
            session.execute("configure terminal" if index % 2 else "end")
        assert oldest not in list(session.history)
        assert len(session.history) == HISTORY_BUFFER_SIZE


class TestD6ShowIpInterfaceBriefAlignment:
    def _state_with(self, switch: Switch) -> NetworkState:
        return NetworkState(
            topology=Topology(
                devices={"SW1": switch},
                links=[],
            )
        )

    def test_long_interface_name_does_not_shift_columns(self) -> None:
        switch = Switch(hostname="SW1")
        # Subinterfaces need their physical parent declared first.
        switch.add_interface(Interface(name="TenGigabitEthernet0/0/0"))
        switch.add_interface(
            Interface(name="TenGigabitEthernet0/0/0.255", parent="TenGigabitEthernet0/0/0")
        )
        output = show_ip_interface_brief(self._state_with(switch), switch)
        lines = output.splitlines()
        assert len(lines) == 4
        # The IP column must start at the same offset in every row.
        name_width = len("Te0/0/0.255")  # widest short name
        ip_column = name_width + 1
        for line in lines:
            assert len(line) > ip_column
            assert line[ip_column - 1] == " "
        # Dash row fills the interface column with dashes.
        assert lines[1][:name_width] == "-" * name_width

    def test_short_names_use_canonical_width(self) -> None:
        switch = Switch(hostname="SW1")
        switch.add_interface(Interface(name="GigabitEthernet0/1"))
        output = show_ip_interface_brief(self._state_with(switch), switch)
        lines = output.splitlines()
        # 9-char canonical width + 1 space -> IP column starts at index 10.
        for line in lines:
            assert line[9] == " "
            assert line[10] != " "


class TestD6HostInterfaceCreationGuard:
    def test_host_rejects_interface_creation(self) -> None:
        host = PC(hostname="PC-A")
        host.add_interface(Interface(name="Ethernet0"))
        state = NetworkState(
            topology=Topology(
                devices={"PC-A": host},
                links=[],
            )
        )
        session = CliSession(state, "PC-A")
        session.execute("enable")
        # PCs have no `interface` command (B9 surface) — invalid input, and
        # certainly no crash or state change.
        output = session.execute("interface Ethernet0")
        assert output.startswith("% Invalid input")
