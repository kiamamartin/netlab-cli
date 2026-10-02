"""Typer application exposing the ``netlab`` console script (doc §10.1)."""

import typer

from netlab import __version__

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


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
