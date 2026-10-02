"""Unit tests for the Command model."""

import pytest
from pydantic import ValidationError

from netlab.core.command import Command
from netlab.core.enums import ConfigMode


def test_command_model() -> None:
    command = Command(
        raw="interface gi0/1",
        verb="interface",
        arguments=("gi0/1",),
        mode=ConfigMode.GLOBAL_CONFIG,
    )
    assert command.verb == "interface"
    assert command.arguments == ("gi0/1",)
    assert command.mode is ConfigMode.GLOBAL_CONFIG


def test_command_is_frozen() -> None:
    command = Command(
        raw="vlan 10", verb="vlan", arguments=("10",), mode=ConfigMode.GLOBAL_CONFIG
    )
    with pytest.raises(ValidationError):
        command.verb = "no"  # type: ignore[misc]


def test_config_mode_values() -> None:
    assert ConfigMode.USER_EXEC.value == "user-exec"
    assert ConfigMode.PRIVILEGED_EXEC.value == "privileged-exec"
    assert ConfigMode.GLOBAL_CONFIG.value == "global-config"
    assert ConfigMode.INTERFACE_CONFIG.value == "interface-config"
    assert ConfigMode.VLAN_CONFIG.value == "vlan-config"
