"""Typer application exposing the ``netlab`` console script (doc §10.1)."""

import sys
from pathlib import Path
from typing import Annotated

import typer

from netlab import __version__
from netlab.scenarios import (
    build_initial_state,
    discover_scenarios,
    resolve_scenario,
    validate_requirements,
)

app = typer.Typer(
    help="NetLab CLI — Adaptive Cisco Networking Laboratory and Assessment Environment.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """NetLab CLI — Adaptive Cisco Networking Laboratory and Assessment Environment."""


@app.command()
def version() -> None:
    """Print the NetLab CLI version."""
    typer.echo(f"netlab {__version__}")


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
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """List available scenarios (invalid files are reported and skipped)."""
    loaded, errors = discover_scenarios(scenarios_dir)
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
        # Start hint
        typer.echo(f"      start:  netlab start {scenario.id}")
        typer.echo("")
    for _path, message in errors:
        typer.secho(f"{_path}: {message}", fg=typer.colors.RED, err=True)
    if errors:
        raise typer.Exit(code=1)


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
        str, typer.Option(help="Scenario id to inspect.")
    ],
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """List the devices in a scenario (hostnames, types, interfaces)."""
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


@app.command("start")
def start_scenario(
    scenario_id: Annotated[
        str, typer.Option(help="Scenario id to start (run ``netlab list`` to see ids).")
    ],
    device: Annotated[
        str | None,
        typer.Option(
            help="Device hostname to connect to (e.g. SW1). Omitted when the "
            "scenario has one device — use ``netlab devices <id>`` to see choices."
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
        run_console(state, hostname, sys.stdin, typer.echo)
    except SessionExit:
        pass
    except NetLabError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def console(
    scenario_id: Annotated[
        str,
        typer.Option(
            "--scenario", help="Scenario id to load (run ``netlab list`` to see ids)."
        ),
    ],
    device: Annotated[
        str | None,
        typer.Option(
            help="Device hostname to connect to (e.g. SW1). If omitted, the "
            "default device is chosen automatically — use ``netlab devices <id>`` "
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

    _path, scenario = resolve_scenario(scenarios_dir, scenario_id)
    hostname = _pick_device(scenario, device)
    try:
        state = build_initial_state(scenario)
        run_console(state, hostname, sys.stdin, typer.echo)
    except SessionExit:
        pass
    except NetLabError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
