"""Typer application exposing the ``netlab`` console script (doc §10.1)."""

import sys
from pathlib import Path
from typing import Annotated

import typer

from netlab import __version__
from netlab.scenarios import build_initial_state, discover_scenarios, resolve_scenario

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
    for _path, message in errors:
        typer.secho(message, fg=typer.colors.RED, err=True)
    if errors:
        raise typer.Exit(code=1)


@app.command()
def console(
    scenario_id: Annotated[
        str, typer.Option("--scenario", help="Scenario id to load.")
    ],
    device: Annotated[
        str, typer.Option(help="Device hostname to connect to (e.g. SW1).")
    ],
    scenarios_dir: Annotated[
        Path,
        typer.Option(help="Directory containing scenario YAML files."),
    ] = Path("scenarios"),
) -> None:
    """Start an interactive IOS-like session on one device of a scenario."""
    from netlab.core.errors import NetLabError
    from netlab.ios import SessionExit, run_console

    _path, scenario = resolve_scenario(scenarios_dir, scenario_id)
    try:
        state = build_initial_state(scenario)
        run_console(state, device, sys.stdin, typer.echo)
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
