"""IOS CLI session: mode state machine and command dispatch (doc §11).

The session owns the configuration-mode state machine. Commands are validated
against the current mode by the registry; transition commands move between
modes, and the prompt always reflects the current context. Command handlers
live in :mod:`netlab.ios.commands` as mixins and mutate the authoritative
network state only through the domain models.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

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


@dataclass(frozen=True)
class HistoryEntry:
    """One executed command, recorded for later analytics (doc §34)."""

    timestamp: datetime
    raw: str
    mode: ConfigMode
    ok: bool
    error: str | None = None


class CliSession(DeviceConfigMixin, SwitchConfigMixin, HostConfigMixin):
    """An interactive IOS-like session attached to one device.

    The session never executes student input as anything other than parsed
    :class:`Command` objects dispatched to their handlers.
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
        self.session: CliSession = self  # bound reference used by the mixins
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
        return prompt_for(self.mode, self._device.hostname)

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

    # --- Transition and structural handlers -----------------------------------

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
        self.mode = exit_target(self.mode)
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
    lines: Iterable[str],
    write: Callable[[str], None],
) -> None:
    """Run an interactive device session over ``lines``/``write``.

    ``lines`` supplies input (normally ``sys.stdin``); ``write`` receives all
    output including prompts. Ends on ``exit`` in user EXEC (raising
    :class:`SessionExit`) or end of input.
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
