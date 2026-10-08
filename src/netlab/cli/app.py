"""Typer application exposing the ``netlab`` console script (doc §10.1)."""

import sys
from pathlib import Path
from typing import Annotated

import typer

from netlab import __version__
from netlab.diagram import render_diagram
from netlab.scenarios import (
    build_initial_state,
    discover_scenarios,
    resolve_scenario,
    validate_requirements,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"netlab {__version__}")
        raise typer.Exit()


app = typer.Typer(
    help="NetLab CLI — Adaptive Cisco Networking Laboratory and Assessment Environment.",
    no_args_is_help=True,
)


@app.callback()
def _root(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the NetLab CLI version and exit.",
        ),
    ] = None,
) -> None:
    """NetLab CLI — Adaptive Cisco Networking Laboratory and Assessment Environment."""


@app.command()
def version() -> None:
    """Print the NetLab CLI version."""
    typer.echo(f"netlab {__version__}")


def _resolve_scenario_id(
    argument: str | None, option: str | None, *, command: str
) -> str:
    """Accept the scenario id as a positional argument *or* a long option.

    ``netlab start ccna-vlan-001`` and ``netlab start --scenario-id
    ccna-vlan-001`` are both accepted; giving both forms is an error so the
    intent is never ambiguous.
    """
    if argument is not None and option is not None:
        raise typer.BadParameter(
            f"give the scenario id either positionally or via the option, not both "
            f"(got {argument!r} and {option!r})",
        )
    scenario_id = argument if argument is not None else option
    if scenario_id is None:
        name = "--scenario" if command == "console" else "--scenario-id"
        raise typer.BadParameter(
            f"missing scenario id: "
            f"netlab {command} <scenario-id> or netlab {command} {name} <scenario-id>"
        )
    return scenario_id


def _difficulty_filter(value: str | None) -> str | None:
    """Validate the ``--difficulty`` filter value (kept lowercase for YAML ids)."""
    if value is None:
        return None
    lowered = value.lower()
    if lowered not in ("beginner", "intermediate", "advanced"):
        raise typer.BadParameter(
            f"invalid difficulty {value!r} "
            "(expected: beginner, intermediate or advanced)"
        )
    return lowered


def _device_summary(scenario: object) -> str:
    """Compact one-line summary of the scenario's topology for ``list`` output."""
    devices = getattr(scenario, "topology", None)
    if devices is None:
        return ""
    devs = getattr(devices, "devices", [])
    parts: list[str] = []
    for spec in devs:
        kind = getattr(spec, "type", "")
        ifaces = getattr(spec, "interfaces", [])
        n = len(ifaces)
        suffix = f" + {n} interfaces" if n else ""
        parts.append(
            f"{getattr(spec, 'name', '?')} ({kind}{suffix})"
        )
    return ", ".join(parts)


@app.command("list")
def list_scenarios(
    difficulty: Annotated[
        str | None,
        typer.Option(
            case_sensitive=False,
            help="Only show scenarios with this difficulty "
            "(beginner, intermediate or advanced).",
        ),
    ] = None,
    diagram: Annotated[
        bool,
        typer.Option(
            "--diagram/--no-diagram",
            help="Render an ASCII topology diagram under each scenario.",
        ),
    ] = True,
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """List available scenarios (invalid files are reported and skipped)."""
    difficulty_filter = _difficulty_filter(difficulty)
    loaded, errors = discover_scenarios(scenarios_dir)
    if difficulty_filter is not None:
        loaded = [
            entry
            for entry in loaded
            if entry[1].difficulty.value == difficulty_filter
        ]
    if not loaded and not errors:
        typer.echo(f"No scenarios found in {scenarios_dir}")
        return
    for _path, scenario in loaded:
        typer.secho(scenario.id, fg=typer.colors.CYAN, nl=False)
        typer.echo(f"  [{scenario.difficulty.value}]  {scenario.title}")
        # Topology hint + estimated time, if available
        meta = getattr(scenario, "metadata", None)
        minutes = getattr(meta, "estimated_minutes", None)
        if minutes:
            typer.echo(f"      devices: {_device_summary(scenario)}")
            typer.echo(f"      estimated: {minutes} min")
        else:
            typer.echo(f"      devices: {_device_summary(scenario)}")
        if diagram:
            typer.echo("")
            for line in render_diagram(scenario.topology).splitlines():
                typer.echo(f"      {line}" if line else "")
            typer.echo("")
        # Start hint
        typer.echo(f"      start:  netlab start {scenario.id}")
        typer.echo("")
    for _path, message in errors:
        typer.secho(f"{_path}: {message}", fg=typer.colors.RED, err=True)
    if errors:
        raise typer.Exit(code=1)


def _console_write(text: str) -> None:
    """Verbatim terminal writer for interactive console sessions.

    ``run_console``'s contract is a writer that appends exactly the text it is
    given (newlines included): the prompt is intentionally written inline so
    the line editor can anchor its redraws to it. ``typer.echo`` must not be
    used here — it appends its own newline, which pushes the prompt onto its
    own line and double-spaces command output.
    """
    sys.stdout.write(text)
    sys.stdout.flush()


def _pick_device(scenario: object, device: str | None) -> str:
    """Resolve the device hostname to connect to.

    When ``device`` is given it is used as-is. When omitted, pick a sensible
    default: a single switch or router if the scenario has exactly one, otherwise
    raise an error pointing at ``netlab devices`` / the scenario's device list.
    """
    devs = scenario.topology.devices
    names = [spec.name for spec in devs]
    if device is not None:
        if device not in names:
            raise typer.BadParameter(
                f"device {device!r} not in scenario. Devices: {', '.join(names)}"
            )
        return device
    if len(devs) == 1:
        return names[0]
    # Prefer a switch, then a router, then the first device — but only as a
    # guess when the student forgot to specify one; be explicit about it.
    for pref in ("switch", "router"):
        for spec in devs:
            if getattr(spec, "type", "") == pref:
                typer.echo(
                    f"Multiple devices in this scenario; connecting to {spec.name} "
                    f"(the {pref}) by default.",
                    err=True,
                )
                return spec.name
    typer.echo(
        f"Multiple devices in this scenario; connecting to {names[0]} by default.",
        err=True,
    )
    return names[0]


@app.command("devices")
def list_devices(
    scenario_id: Annotated[
        str, typer.Option(help="Scenario id to inspect (run ``netlab list`` to see ids).")
    ],
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """List the devices in a scenario (hostnames, types, interfaces).

    Example::

        netlab devices --scenario-id ccna-vlan-001
    """
    _path, scenario = resolve_scenario(scenarios_dir, scenario_id)
    typer.echo(f"Scenario: {scenario.id} — {scenario.title}")
    typer.echo("")
    for spec in scenario.topology.devices:
        kind = getattr(spec, "type", "")
        typer.secho(f"  {spec.name}", fg=typer.colors.CYAN, nl=False)
        typer.echo(f"  ({kind})")
        for iface in getattr(spec, "interfaces", []):
            name = getattr(iface, "name", "")
            description = getattr(iface, "description", "") or "(no description)"
            typer.echo(f"      {name}: {description}")
        typer.echo("")
    typer.echo("Topology:")
    for line in render_diagram(scenario.topology).splitlines():
        typer.echo(f"  {line}" if line else "")
    typer.echo("")


@app.command("start")
def start_scenario(
    scenario_argument: Annotated[
        str | None,
        typer.Argument(
            metavar="[SCENARIO-ID]",
            help="Scenario id to start, e.g. ccna-vlan-001 (run ``netlab list`` "
            "to see ids). Alternatively use --scenario-id.",
        ),
    ] = None,
    scenario_id_option: Annotated[
        str | None,
        typer.Option("--scenario-id", help="Scenario id to start (alternative to "
                     "the positional argument)."),
    ] = None,
    device: Annotated[
        str | None,
        typer.Option(
            help="Device hostname to connect to (e.g. SW1). Omitted when the "
            "scenario has one device — use ``netlab devices`` to see choices."
        ),
    ] = None,
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """Load a scenario and start an interactive IOS-like session.

    This is the friendliest way to begin a lab. If you omit ``--device`` and the
    scenario contains a single device, it is selected automatically. Otherwise you
    are told which device was chosen and can restart with an explicit ``--device``
    if you prefer a different one.

    Examples::

        netlab start ccna-vlan-001              # connects to the default device
        netlab start ccna-vlan-001 --device SW1 # connects to SW1 specifically
        netlab devices ccna-vlan-001            # list devices before choosing
    """
    from netlab.core.errors import NetLabError
    from netlab.ios import SessionExit, run_console

    scenario_id = _resolve_scenario_id(
        scenario_argument, scenario_id_option, command="start"
    )
    _path, scenario = resolve_scenario(scenarios_dir, scenario_id)
    # Semantic validation so students get a useful message before the session starts.
    problems = validate_requirements(scenario)
    if problems:
        typer.secho(
            f"Scenario {scenario.id!r} has grading issues:\n"
            + "\n".join(f"  - {p}" for p in problems),
            fg=typer.colors.YELLOW,
            err=True,
        )
    hostname = _pick_device(scenario, device)
    typer.echo(f"Starting scenario {scenario.id} — {scenario.title}")
    typer.echo(f"Device: {hostname}  |  Difficulty: {scenario.difficulty.value}")
    if scenario.objectives:
        typer.echo("Objectives:")
        for obj in scenario.objectives:
            typer.echo(f"  • {obj}")
        typer.echo("")
    try:
        state = build_initial_state(scenario)
        run_console(state, hostname, sys.stdin, _console_write, scenario=scenario)
    except SessionExit:
        pass
    except NetLabError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def console(
    scenario_argument: Annotated[
        str | None,
        typer.Argument(
            metavar="[SCENARIO-ID]",
            help="Scenario id to load, e.g. ccna-vlan-001. Alternatively use --scenario.",
        ),
    ] = None,
    scenario_id_option: Annotated[
        str | None,
        typer.Option(
            "--scenario", help="Scenario id to load (alternative to the positional "
            "argument)."
        ),
    ] = None,
    device: Annotated[
        str | None,
        typer.Option(
            help="Device hostname to connect to (e.g. SW1). If omitted, the "
            "default device is chosen automatically — use ``netlab devices`` "
            "to see choices."
        ),
    ] = None,
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """Start an interactive IOS-like session on one device of a scenario.

    ``netlab console`` is the low-level entry point. Most students should use
    ``netlab start <id>`` instead, which prints objectives and selects a device
    automatically when possible.

    Examples::

        netlab console --scenario ccna-vlan-001 --device SW1
        netlab console --scenario ccna-vlan-001          # device chosen automatically
    """
    from netlab.core.errors import NetLabError
    from netlab.ios import SessionExit, run_console

    scenario_id = _resolve_scenario_id(
        scenario_argument, scenario_id_option, command="console"
    )
    _path, scenario = resolve_scenario(scenarios_dir, scenario_id)
    hostname = _pick_device(scenario, device)
    try:
        state = build_initial_state(scenario)
        run_console(state, hostname, sys.stdin, _console_write, scenario=scenario)
    except SessionExit:
        pass
    except NetLabError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def save(
    scenario_argument: Annotated[
        str | None,
        typer.Argument(
            metavar="[SCENARIO-ID]",
            help="Scenario id whose current session should be saved. "
            "Alternatively use --scenario-id.",
        ),
    ] = None,
    scenario_id_option: Annotated[
        str | None,
        typer.Option("--scenario-id", help="Scenario id (alternative to the "
                     "positional argument)."),
    ] = None,
    device: Annotated[
        str | None,
        typer.Option(
            help="Device hostname to reconnect to on resume (e.g. SW1). "
            "Omitted when the scenario has one device."
        ),
    ] = None,
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """Save the current session state for a scenario (JSON, decision B12).

    ``netlab save`` snapshots the authoritative network state so a later
    ``netlab resume`` continues where you left off. The scenario's YAML must
    still be discoverable (use ``--scenarios-dir`` if it lives elsewhere).

    Examples::

        netlab save ccna-vlan-001
        netlab save ccna-vlan-001 --device SW1
    """
    from netlab.core.errors import NetLabError
    from netlab.persistence import PersistenceError, save_session, sessions_dir

    scenario_id = _resolve_scenario_id(
        scenario_argument, scenario_id_option, command="save"
    )
    try:
        _path, scenario = resolve_scenario(scenarios_dir, scenario_id)
        state = build_initial_state(scenario)
        hostname = device if device is not None else _pick_device(scenario, None)
        path = save_session(scenario_id, hostname, state)
        typer.echo(f"Session for {scenario_id} saved to {path}")
        typer.echo(f"Resume with: netlab resume {scenario_id}")
        typer.echo(f"Sessions directory: {sessions_dir()}")
    except NetLabError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except PersistenceError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def resume(
    scenario_argument: Annotated[
        str | None,
        typer.Argument(
            metavar="[SCENARIO-ID]",
            help="Scenario id to resume. Alternatively use --scenario-id.",
        ),
    ] = None,
    scenario_id_option: Annotated[
        str | None,
        typer.Option("--scenario-id", help="Scenario id (alternative to the "
                     "positional argument)."),
    ] = None,
    device: Annotated[
        str | None,
        typer.Option(
            help="Device hostname to connect to (e.g. SW1); overrides the "
            "hostname recorded at save time."
        ),
    ] = None,
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """Resume a saved session (restores the saved network state).

    The restored state is authoritative: every configuration you saved is
    back, and ``assess``/``submit`` inside the session grades it against the
    scenario's requirements.

    Examples::

        netlab resume ccna-vlan-001
        netlab resume ccna-vlan-001 --device R1
    """
    from netlab.core.errors import NetLabError
    from netlab.ios import SessionExit, run_console
    from netlab.persistence import PersistenceError, load_session

    scenario_id = _resolve_scenario_id(
        scenario_argument, scenario_id_option, command="resume"
    )
    try:
        _snapshot, state = load_session(scenario_id)
        _path, scenario = resolve_scenario(scenarios_dir, scenario_id)
        hostname = device if device is not None else _snapshot.hostname
        names = [spec.name for spec in scenario.topology.devices]
        if hostname not in names:
            raise PersistenceError(
                f"saved device {hostname!r} is not part of scenario "
                f"{scenario_id!r} (devices: {', '.join(names)})"
            )
        typer.echo(
            f"Resuming scenario {scenario.id} — {scenario.title} "
            f"(saved session)."
        )
        typer.echo(f"Device: {hostname}  |  Difficulty: {scenario.difficulty.value}")
        typer.echo("")
        run_console(state, hostname, sys.stdin, _console_write, scenario=scenario)
    except SessionExit:
        pass
    except NetLabError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def reset(
    scenario_argument: Annotated[
        str | None,
        typer.Argument(
            metavar="[SCENARIO-ID]",
            help="Scenario id whose saved session should be reset. "
            "Alternatively use --scenario-id, or pass --all.",
        ),
    ] = None,
    scenario_id_option: Annotated[
        str | None,
        typer.Option("--scenario-id", help="Scenario id (alternative to the "
                     "positional argument)."),
    ] = None,
    hard: Annotated[
        bool,
        typer.Option(
            "--hard",
            help="Delete the saved session entirely (default is a soft reset "
            "back to the scenario's initial state).",
        ),
    ] = False,
    reset_all: Annotated[
        bool,
        typer.Option("--all", help="Reset every saved session."),
    ] = False,
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """Reset saved sessions: soft (back to initial state) or hard (deleted).

    Examples::

        netlab reset ccna-vlan-001           # soft reset
        netlab reset ccna-vlan-001 --hard    # delete the saved session
        netlab reset --all --hard            # delete every saved session
    """
    from netlab.core.errors import NetLabError
    from netlab.persistence import (
        PersistenceError,
        reset_all_sessions,
        reset_session,
    )

    try:
        if reset_all:
            try:
                count = reset_all_sessions(hard=hard)
            except NetLabError as exc:
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc
            typer.echo(f"Reset {count} saved session(s) ({'hard' if hard else 'soft'}).")
            return
        scenario_id = _resolve_scenario_id(
            scenario_argument, scenario_id_option, command="reset"
        )
        message = reset_session(
            scenario_id, scenarios_dir, hard=hard
        )
        typer.echo(message)
    except NetLabError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except PersistenceError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def assess(
    scenario_argument: Annotated[
        str | None,
        typer.Argument(
            metavar="[SCENARIO-ID]",
            help="Scenario id whose saved session should be graded. "
            "Alternatively use --scenario-id.",
        ),
    ] = None,
    scenario_id_option: Annotated[
        str | None,
        typer.Option("--scenario-id", help="Scenario id (alternative to the "
                     "positional argument)."),
    ] = None,
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """Grade a saved session outside the device shell.

    Runs the deterministic grading engine against the saved network state and
    prints the assessment report — the same report ``submit`` renders inside
    a session.

    Example::

        netlab assess ccna-vlan-001
    """
    from netlab.core.errors import NetLabError
    from netlab.grading import grade, render_assessment
    from netlab.persistence import PersistenceError, load_session

    scenario_id = _resolve_scenario_id(
        scenario_argument, scenario_id_option, command="assess"
    )
    try:
        _snapshot, state = load_session(scenario_id)
        _path, scenario = resolve_scenario(scenarios_dir, scenario_id)
        assessment = grade(scenario, state)
        typer.echo(render_assessment(assessment))
    except NetLabError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except PersistenceError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
