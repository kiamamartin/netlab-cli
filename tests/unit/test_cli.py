"""Tests for the `netlab` console script entry point."""

from pathlib import Path

from typer.testing import CliRunner

from netlab import __version__
from netlab.cli.app import app

runner = CliRunner()

PROJECT_ROOT = Path(__file__).parents[2]
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.output
    assert "list" in result.output


def test_list_shows_first_milestone_scenario() -> None:
    result = runner.invoke(app, ["list", "--scenarios-dir", str(SCENARIOS_DIR)])
    assert result.exit_code == 0
    assert "ccna-vlan-001" in result.output
    assert "Department VLAN Deployment" in result.output


def test_list_reports_missing_directory() -> None:
    result = runner.invoke(app, ["list", "--scenarios-dir", "/nonexistent/dir"])
    assert result.exit_code == 0
    assert "No scenarios found" in result.output


def test_list_reports_invalid_scenarios(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    result = runner.invoke(app, ["list", "--scenarios-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "broken.yaml" in result.output
