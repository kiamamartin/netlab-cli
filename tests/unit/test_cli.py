"""Tests for the `netlab` console script entry point."""

from typer.testing import CliRunner

from netlab import __version__
from netlab.cli.app import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_version() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.output
