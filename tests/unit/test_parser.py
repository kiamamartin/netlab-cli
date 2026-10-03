"""Unit tests for the IOS command parser and error rendering."""

import pytest

from netlab.core.enums import ConfigMode
from netlab.core.errors import (
    AmbiguousCommandError,
    IncompleteCommandError,
    InvalidInputError,
)
from netlab.ios.errors import render_error
from netlab.ios.parser import (
    CommandRegistry,
    CommandSpec,
    validate_hostname_argument,
    validate_interface_argument,
    validate_vlan_id_argument,
)


def make_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(CommandSpec(
        name="enable", mode=ConfigMode.USER_EXEC, pattern=("e",),
        description="Enter privileged EXEC mode",
    ))
    registry.register(CommandSpec(
        name="exit", mode=ConfigMode.USER_EXEC, pattern=("e",),
        description="End the session",
    ))
    registry.register(CommandSpec(
        name="configure terminal", mode=ConfigMode.PRIVILEGED_EXEC, pattern=("conf", "t"),
        description="Enter global configuration mode",
    ))
    registry.register(CommandSpec(
        name="hostname", mode=ConfigMode.GLOBAL_CONFIG, pattern=("host",),
        argument="WORD", argument_validator=validate_hostname_argument,
    ))
    registry.register(CommandSpec(
        name="vlan", mode=ConfigMode.GLOBAL_CONFIG, pattern=("vl",),
        argument="VLAN_ID", argument_validator=validate_vlan_id_argument,
    ))
    registry.register(CommandSpec(
        name="interface", mode=ConfigMode.GLOBAL_CONFIG, pattern=("int",),
        argument="TYPE/NUM", argument_validator=validate_interface_argument,
    ))
    registry.register(CommandSpec(
        name="name", mode=ConfigMode.VLAN_CONFIG, pattern=("na",),
        argument="NAME", free_text=True,
    ))
    return registry


class TestMatching:
    def test_exact_word(self) -> None:
        command, argument = make_registry().resolve("enable", ConfigMode.USER_EXEC)
        assert command.verb == "enable"
        assert argument is None

    def test_min_prefix(self) -> None:
        command, _ = make_registry().resolve("en", ConfigMode.USER_EXEC)
        assert command.verb == "enable"

    def test_case_insensitive(self) -> None:
        command, _ = make_registry().resolve("ENABLE", ConfigMode.USER_EXEC)
        assert command.verb == "enable"

    def test_multiword_abbreviations(self) -> None:
        registry = make_registry()
        for raw in ("conf t", "conf term", "configure terminal", "conf terminal"):
            command, _ = registry.resolve(raw, ConfigMode.PRIVILEGED_EXEC)
            assert command.verb == "configure terminal"

    def test_prefix_beyond_word_fails(self) -> None:
        # "ena" is beyond "en" but still a prefix of "enable" — accepted.
        command, _ = make_registry().resolve("ena", ConfigMode.USER_EXEC)
        assert command.verb == "enable"

    def test_wrong_mode_gating(self) -> None:
        # "configure" is privileged-only; in user EXEC it must not resolve.
        with pytest.raises(InvalidInputError):
            make_registry().resolve("configure terminal", ConfigMode.USER_EXEC)

    def test_unknown_command(self) -> None:
        with pytest.raises(InvalidInputError):
            make_registry().resolve("frobnicate", ConfigMode.USER_EXEC)


class TestErrors:
    def test_ambiguous(self) -> None:
        with pytest.raises(AmbiguousCommandError):
            make_registry().resolve("e", ConfigMode.USER_EXEC)

    def test_invalid_interface_argument(self) -> None:
        with pytest.raises(InvalidInputError):
            make_registry().resolve("interface foogle0/1", ConfigMode.GLOBAL_CONFIG)

    def test_invalid_vlan_range(self) -> None:
        with pytest.raises(InvalidInputError):
            make_registry().resolve("vlan 5000", ConfigMode.GLOBAL_CONFIG)

    def test_invalid_hostname(self) -> None:
        with pytest.raises(InvalidInputError):
            make_registry().resolve("hostname 9bad-host", ConfigMode.GLOBAL_CONFIG)

    def test_incomplete_argument(self) -> None:
        with pytest.raises(IncompleteCommandError):
            make_registry().resolve("hostname", ConfigMode.GLOBAL_CONFIG)

    def test_incomplete_truncated_command(self) -> None:
        with pytest.raises(IncompleteCommandError):
            make_registry().resolve("conf", ConfigMode.PRIVILEGED_EXEC)

    def test_extra_token_rejected(self) -> None:
        with pytest.raises(InvalidInputError):
            make_registry().resolve("enable now", ConfigMode.USER_EXEC)

    def test_empty_input(self) -> None:
        with pytest.raises(InvalidInputError):
            make_registry().resolve("   ", ConfigMode.USER_EXEC)


class TestPositions:
    def test_invalid_input_caret_under_token(self) -> None:
        raw = "hostname 9bad"
        with pytest.raises(InvalidInputError) as excinfo:
            make_registry().resolve(raw, ConfigMode.GLOBAL_CONFIG)
        rendered = render_error(excinfo.value)
        marker_line = rendered.splitlines()[1]
        assert marker_line[raw.index("9bad")] == "^"

    def test_render_error_variants(self) -> None:
        assert render_error(InvalidInputError("x", position=0)) == (
            "% Invalid input detected at '^' marker.\n^"
        )
        assert render_error(IncompleteCommandError("x")) == "% Incomplete command."
        assert render_error(AmbiguousCommandError("x")) == "% Ambiguous command."


class TestArgumentValidators:
    def test_hostname_valid(self) -> None:
        assert validate_hostname_argument("core-sw-1") == "core-sw-1"

    def test_vlan_valid(self) -> None:
        assert validate_vlan_id_argument("10") == 10

    def test_vlan_non_numeric(self) -> None:
        with pytest.raises(ValueError):
            validate_vlan_id_argument("ten")

    def test_interface_canonicalized(self) -> None:
        assert validate_interface_argument("gi0/1") == "GigabitEthernet0/1"

    def test_interface_invalid(self) -> None:
        from netlab.core.errors import InvalidInterfaceError

        with pytest.raises(InvalidInterfaceError):
            validate_interface_argument("gizmo0/1")
