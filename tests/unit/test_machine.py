"""Unit tests for the IOS CLI session state machine and console loop."""

import pytest

from netlab.core.enums import ConfigMode
from netlab.core.errors import InvalidInputError
from netlab.devices.switch import Switch
from netlab.ios.machine import CliSession, SessionExit, run_console
from netlab.network.state import NetworkState
from tests.unit.test_topology import build_topology


def make_session(hostname: str = "SW1") -> CliSession:
    return CliSession(build_topology(), hostname)


class TestPrompts:
    def test_prompt_reflects_mode(self) -> None:
        session = make_session()
        assert session.prompt() == "SW1>"
        session.execute("enable")
        assert session.prompt() == "SW1#"
        session.execute("configure terminal")
        assert session.prompt() == "SW1(config)#"
        session.execute("interface gi0/1")
        assert session.prompt() == "SW1(config-if)#"
        session.execute("exit")
        session.execute("vlan 10")
        assert session.prompt() == "SW1(config-vlan)#"

    def test_prompt_follows_hostname_change(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("hostname CORE1")
        assert session.prompt() == "CORE1(config)#"


class TestTransitions:
    def test_enable_disable_cycle(self) -> None:
        session = make_session()
        session.execute("enable")
        assert session.mode is ConfigMode.PRIVILEGED_EXEC
        session.execute("disable")
        assert session.mode is ConfigMode.USER_EXEC

    def test_exit_ladder(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("interface gi0/1")
        session.execute("exit")  # interface -> global
        assert session.mode is ConfigMode.GLOBAL_CONFIG
        session.execute("exit")  # global -> privileged
        assert session.mode is ConfigMode.PRIVILEGED_EXEC
        session.execute("exit")  # privileged -> user
        assert session.mode is ConfigMode.USER_EXEC

    def test_end_returns_to_privileged_from_any_config_mode(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("vlan 10")
        session.execute("end")
        assert session.mode is ConfigMode.PRIVILEGED_EXEC
        assert session.context is None

    def test_context_cleared_on_exit(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("interface gi0/2")
        assert session.context == "GigabitEthernet0/2"
        session.execute("exit")
        assert session.context is None


class TestCommands:
    def test_hostname(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("hostname CORE1")
        assert session.hostname == "CORE1"

    def test_vlan_creation_and_naming(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("vlan 10")
        session.execute("name ADMIN")
        session.execute("exit")
        switch = session.device
        assert isinstance(switch, Switch)
        assert switch.get_vlan(10).name == "ADMIN"

    def test_vlan_creation_mirrors_doc_example_session(self) -> None:
        """The doc §27 example session works verbatim on the state."""
        session = make_session()
        for line in (
            "enable",
            "configure terminal",
            "vlan 10",
            "name ADMIN",
            "exit",
            "vlan 20",
            "name SALES",
            "exit",
        ):
            session.execute(line)
        switch = session.device
        assert isinstance(switch, Switch)
        assert switch.get_vlan(10).name == "ADMIN"
        assert switch.get_vlan(20).name == "SALES"

    def test_vlan_on_router_rejected(self) -> None:
        """Real IOS: 'vlan' is not a router command, so it is invalid input."""
        session = make_session("R1")
        session.execute("enable")
        session.execute("configure terminal")
        output = session.execute("vlan 10")
        assert output.startswith("% Invalid input")
        assert session.mode is ConfigMode.GLOBAL_CONFIG

    def test_vlan_name_without_context_rejected(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        # 'name' is vlan-config-only; from global config it must not resolve.
        with pytest.raises(InvalidInputError):
            session.registry.resolve("name ADMIN", ConfigMode.GLOBAL_CONFIG)


class TestModeGating:
    def test_exec_command_rejected_in_config_mode(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        # 'enable' is not registered in global config.
        with pytest.raises(InvalidInputError):
            session.registry.resolve("enable", ConfigMode.GLOBAL_CONFIG)
        output = session.execute("enable")
        assert output.startswith("% Invalid input")

    def test_config_command_rejected_in_user_exec(self) -> None:
        session = make_session()
        # Brief §10 example: 'vlan 10' from user EXEC is an IOS-like error.
        output = session.execute("vlan 10")
        assert output == "% Invalid input detected at '^' marker.\n^"

    def test_interface_command_rejected_in_vlan_mode(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("vlan 10")
        with pytest.raises(InvalidInputError):
            session.registry.resolve("interface gi0/1", ConfigMode.VLAN_CONFIG)


class TestInterfaceCreation:
    def test_existing_interface_context(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("interface gi0/2")
        assert session.context == "GigabitEthernet0/2"

    def test_subinterface_created_when_parent_exists(self) -> None:
        session = make_session("R1")
        session.execute("enable")
        session.execute("configure terminal")
        session.execute("interface g0/0/0.10")
        assert session.context == "GigabitEthernet0/0/0.10"
        r1 = session.device
        assert r1.get_interface("g0/0/0.10").parent == "GigabitEthernet0/0/0"

    def test_subinterface_without_parent_rejected(self) -> None:
        session = make_session("R1")
        session.execute("enable")
        session.execute("configure terminal")
        output = session.execute("interface g0/1.10")
        assert "% Invalid interface type and number." in output

    def test_host_interface_creation_rejected(self) -> None:
        session = make_session("PC-A")
        session.execute("enable")
        session.execute("configure terminal")
        output = session.execute("interface Ethernet5")
        assert output.startswith("%")


class TestHistory:
    def test_successful_commands_recorded(self) -> None:
        session = make_session()
        session.execute("enable")
        session.execute("vlan 10")  # fails: wrong mode
        assert len(session.history) == 2
        assert session.history[0].ok is True
        assert session.history[1].ok is False
        assert session.history[1].error is not None

    def test_empty_line_not_recorded(self) -> None:
        session = make_session()
        session.execute("   ")
        assert list(session.history) == []


class TestConsole:
    def test_console_runs_doc_example_and_exits(self) -> None:
        """Doc §27 session flow through the console loop, exiting cleanly."""
        state = NetworkState(topology=build_topology())
        input_lines = iter(
            [
                "enable",
                "configure terminal",
                "vlan 10",
                "name ADMIN",
                "exit",
                "exit",  # vlan -> global
                "exit",  # global -> privileged
                "exit",  # privileged -> user -> session end
            ]
        )
        output: list[str] = []

        def write(text: str) -> None:
            output.append(text)

        with pytest.raises(SessionExit):
            run_console(state, "SW1", input_lines, write)
        joined = "".join(output)
        assert "SW1>" in joined
        assert "SW1(config)#" in joined
        assert "SW1(config-vlan)#" in joined

    def test_console_ends_on_input_exhaustion(self) -> None:
        state = NetworkState(topology=build_topology())
        output: list[str] = []
        run_console(state, "SW1", iter([]), output.append)
        assert "".join(output).endswith("SW1>\n")
