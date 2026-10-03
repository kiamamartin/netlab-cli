"""IOS CLI session: mode state machine and command dispatch (doc §11).

The session owns the configuration-mode state machine. Commands are validated
against the current mode by the registry; transition commands
(``enable``, ``configure terminal``, ``interface``, ``vlan``, ``exit``,
``end``) move between modes, and the prompt always reflects the current
context. State mutation happens only through the domain models — never on raw
dictionaries.

Stage scope: mode machinery plus the structural commands needed for the
architecture document's example session (§27) — ``hostname``, ``vlan``,
``name``. Switchport/IP/show commands arrive with the configuration and
verification stages.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from netlab.core.enums import ConfigMode, DeviceType
from netlab.core.errors import (
    CommandError,
    DomainError,
    InvalidInputError,
    InvalidInterfaceError,
    UnknownInterfaceError,
)
from netlab.devices.base import Device
from netlab.devices.interface import Interface
from netlab.devices.switch import Switch
from netlab.ios.errors import render_error
from netlab.ios.parser import (
    CommandRegistry,
    CommandSpec,
    validate_hostname_argument,
    validate_interface_argument,
    validate_vlan_id_argument,
)
from netlab.network.state import NetworkState


class SessionExit(Exception):
    """Raised when ``exit`` is entered in user EXEC: the session ends."""


@dataclass(frozen=True)
class HistoryEntry:
    """One executed command, recorded for later analytics (doc §34)."""

    timestamp: datetime
    raw: str
    mode: ConfigMode
    ok: bool
    error: str | None = None


_PROMPTS = {
    ConfigMode.USER_EXEC: "{name}>",
    ConfigMode.PRIVILEGED_EXEC: "{name}#",
    ConfigMode.GLOBAL_CONFIG: "{name}(config)#",
    ConfigMode.INTERFACE_CONFIG: "{name}(config-if)#",
    ConfigMode.VLAN_CONFIG: "{name}(config-vlan)#",
}

_EXIT_TARGET = {
    ConfigMode.INTERFACE_CONFIG: ConfigMode.GLOBAL_CONFIG,
    ConfigMode.VLAN_CONFIG: ConfigMode.GLOBAL_CONFIG,
    ConfigMode.GLOBAL_CONFIG: ConfigMode.PRIVILEGED_EXEC,
    ConfigMode.PRIVILEGED_EXEC: ConfigMode.USER_EXEC,
}


def build_registry(device_type: DeviceType | None = None) -> CommandRegistry:
    """Commands available in each mode for this stage.

    ``device_type`` gates device-specific commands: VLAN commands are only
    registered for switches, mirroring real IOS where the command does not
    exist on other device types.
    

    Patterns are minimal accept prefixes per word (IOS unique-prefix
    behavior); ``exit``/``end`` intentionally overlap on ``e`` so that a bare
    ``e`` in configuration modes is ambiguous, as in real IOS.
    """
    registry = CommandRegistry()
    register = registry.register

    register(CommandSpec(name="enable", mode=ConfigMode.USER_EXEC, pattern=("e",),
                         description="Enter privileged EXEC mode"))
    register(CommandSpec(name="exit", mode=ConfigMode.USER_EXEC, pattern=("e",),
                         description="End the session"))
    register(CommandSpec(name="help", mode=ConfigMode.USER_EXEC, pattern=("h",),
                         description="List available commands"))
    register(CommandSpec(name="?", mode=ConfigMode.USER_EXEC,
                         description="List available commands"))

    register(CommandSpec(name="disable", mode=ConfigMode.PRIVILEGED_EXEC, pattern=("dis",),
                         description="Return to user EXEC mode"))
    register(CommandSpec(name="configure terminal", mode=ConfigMode.PRIVILEGED_EXEC,
                         pattern=("conf", "t"),
                         description="Enter global configuration mode"))
    register(CommandSpec(name="exit", mode=ConfigMode.PRIVILEGED_EXEC, pattern=("e",),
                         description="Return to user EXEC mode"))
    register(CommandSpec(name="help", mode=ConfigMode.PRIVILEGED_EXEC, pattern=("h",),
                         description="List available commands"))
    register(CommandSpec(name="?", mode=ConfigMode.PRIVILEGED_EXEC,
                         description="List available commands"))

    for mode in (ConfigMode.GLOBAL_CONFIG, ConfigMode.INTERFACE_CONFIG,
                 ConfigMode.VLAN_CONFIG):
        register(CommandSpec(name="exit", mode=mode, pattern=("e",),
                             description="Leave this mode"))
        register(CommandSpec(name="end", mode=mode, pattern=("e",),
                             description="Return to privileged EXEC mode"))
        register(CommandSpec(name="help", mode=mode, pattern=("h",),
                             description="List available commands"))
        register(CommandSpec(name="?", mode=mode,
                             description="List available commands"))

    register(CommandSpec(name="hostname", mode=ConfigMode.GLOBAL_CONFIG,
                         pattern=("host",), argument="WORD",
                         argument_validator=validate_hostname_argument,
                         description="Set the device hostname"))
    register(CommandSpec(name="interface", mode=ConfigMode.GLOBAL_CONFIG,
                         pattern=("int",), argument="TYPE/NUM",
                         argument_validator=validate_interface_argument,
                         description="Enter interface configuration mode"))

    if device_type is None or device_type is DeviceType.SWITCH:
        register(CommandSpec(name="vlan", mode=ConfigMode.GLOBAL_CONFIG,
                             pattern=("vl",), argument="VLAN_ID",
                             argument_validator=validate_vlan_id_argument,
                             description="Enter VLAN configuration mode"))
        register(CommandSpec(name="name", mode=ConfigMode.VLAN_CONFIG,
                             pattern=("na",), argument="NAME", argument_help=True,
                             description="Name the VLAN"))

    return registry


class CliSession:
    """An interactive IOS-like session attached to one device.

    The session never executes student input as anything other than parsed
    :class:`Command` objects dispatched to registered handlers.
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
        self.history: list[HistoryEntry] = []
        self._handlers: dict[str, Callable[[object], str]] = self._build_handlers()

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

    def prompt(self) -> str:
        """The IOS-style prompt reflecting the current mode."""
        return _PROMPTS[self.mode].format(name=self._device.hostname)

    # --- Execution -----------------------------------------------------------

    def execute(self, raw: str) -> str:
        """Execute one input line and return the output (``""`` for silence)."""
        stripped = raw.strip()
        if not stripped:
            return ""
        entry = HistoryEntry(
            timestamp=datetime.now(UTC), raw=stripped, mode=self.mode, ok=True
        )
        try:
            command, argument = self.registry.resolve(stripped, self.mode)
        except CommandError as exc:
            entry = HistoryEntry(entry.timestamp, entry.raw, entry.mode, False, exc.message)
            self.history.append(entry)
            return render_error(exc)

        try:
            output = self._handlers[command.verb](argument)
        except SessionExit:
            self.history.append(entry)
            raise
        except CommandError as exc:
            entry = HistoryEntry(entry.timestamp, entry.raw, entry.mode, False, exc.message)
            self.history.append(entry)
            return render_error(exc)
        except DomainError as exc:
            entry = HistoryEntry(entry.timestamp, entry.raw, entry.mode, False, exc.message)
            self.history.append(entry)
            return f"% {exc.message}"
        self.history.append(entry)
        return output

    # --- Handlers ------------------------------------------------------------

    def _build_handlers(self) -> dict[str, Callable[[object], str]]:
        return {
            "enable": self._cmd_enable,
            "disable": self._cmd_disable,
            "configure terminal": self._cmd_configure_terminal,
            "exit": self._cmd_exit,
            "end": self._cmd_end,
            "hostname": self._cmd_hostname,
            "interface": self._cmd_interface,
            "vlan": self._cmd_vlan,
            "name": self._cmd_name,
            "help": self._cmd_help,
            "?": self._cmd_help,
        }

    def _cmd_enable(self, _argument: object) -> str:
        self.mode = ConfigMode.PRIVILEGED_EXEC
        return ""

    def _cmd_disable(self, _argument: object) -> str:
        self.mode = ConfigMode.USER_EXEC
        return ""

    def _cmd_configure_terminal(self, _argument: object) -> str:
        self.mode = ConfigMode.GLOBAL_CONFIG
        return ""

    def _cmd_exit(self, _argument: object) -> str:
        if self.mode is ConfigMode.USER_EXEC:
            raise SessionExit()
        self.mode = _EXIT_TARGET[self.mode]
        self._context = None
        return ""

    def _cmd_end(self, _argument: object) -> str:
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
        if not isinstance(self._device, Switch):
            raise InvalidInputError(
                "VLAN configuration is only available on switches"
            )
        if vlan_id not in self._device.vlans:
            self._device.create_vlan(vlan_id)
        self._context = vlan_id
        self.mode = ConfigMode.VLAN_CONFIG
        return ""

    def _cmd_name(self, argument: object) -> str:
        if not isinstance(self._device, Switch) or not isinstance(self._context, int):
            raise InvalidInputError("no VLAN selected")
        self._device.set_vlan_name(self._context, str(argument))
        return ""

    def _cmd_help(self, _argument: object) -> str:
        lines = []
        for spec in self.registry.specs_for(self.mode):
            text = spec.name
            if spec.argument:
                shown = spec.argument if spec.argument_help else f"<{spec.argument}>"
                text = f"{text} {shown}"
            if spec.description:
                text = f"{text:38} {spec.description}"
            lines.append(text)
        return "\n".join(lines)

    # --- Helpers -------------------------------------------------------------

    def _create_interface(self, canonical: str) -> None:
        """IOS-like creation of missing interfaces on configurable devices.

        Subinterfaces (``...`` names) require their physical parent to exist.
        End hosts do not support interface creation.
        """
        if self._device.device_type.value in ("pc", "server"):
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
    lines: Iterable[str],
    write: Callable[[str], None],
) -> None:
    """Run an interactive device session over ``lines``/``write``.

    ``lines`` supplies input (normally ``sys.stdin``); ``write`` receives all
    output including prompts. Ends on ``exit`` in user EXEC or end of input.
    """
    session = CliSession(state, hostname)
    write(f"Connecting to {hostname}...\n")
    while True:
        write(session.prompt())
        try:
            line = next(lines)  # type: ignore[arg-type]
        except StopIteration:
            write("\n")
            return
        output = session.execute(line)
        if output:
            write(f"{output}\n")


__all__ = ["CliSession", "HistoryEntry", "SessionExit", "build_registry", "run_console"]
