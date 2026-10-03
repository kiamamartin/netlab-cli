"""Mode transitions and prompts — a leaf module shared by command handlers
and the session machinery (kept dependency-free to avoid import cycles)."""

from netlab.core.enums import ConfigMode


class SessionExit(Exception):
    """Raised when ``exit`` is entered in user EXEC: the session ends."""


_PROMPTS: dict[ConfigMode, str] = {
    ConfigMode.USER_EXEC: "{name}>",
    ConfigMode.PRIVILEGED_EXEC: "{name}#",
    ConfigMode.GLOBAL_CONFIG: "{name}(config)#",
    ConfigMode.INTERFACE_CONFIG: "{name}(config-if)#",
    ConfigMode.VLAN_CONFIG: "{name}(config-vlan)#",
}

_EXIT_TARGET: dict[ConfigMode, ConfigMode] = {
    ConfigMode.INTERFACE_CONFIG: ConfigMode.GLOBAL_CONFIG,
    ConfigMode.VLAN_CONFIG: ConfigMode.GLOBAL_CONFIG,
    ConfigMode.GLOBAL_CONFIG: ConfigMode.PRIVILEGED_EXEC,
    ConfigMode.PRIVILEGED_EXEC: ConfigMode.USER_EXEC,
}


def prompt_for(mode: ConfigMode, hostname: str) -> str:
    """The IOS-style prompt reflecting the current mode."""
    return _PROMPTS[mode].format(name=hostname)


def exit_target(mode: ConfigMode) -> ConfigMode:
    """The mode entered by ``exit`` from ``mode``."""
    return _EXIT_TARGET[mode]


__all__ = ["SessionExit", "exit_target", "prompt_for"]
