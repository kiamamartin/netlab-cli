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


class TestGlobalOptions:
    def test_root_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output


class TestListOptions:
    def test_difficulty_filter_matching(self) -> None:
        result = runner.invoke(
            app, ["list", "--difficulty", "beginner", "--scenarios-dir", str(SCENARIOS_DIR)]
        )
        assert result.exit_code == 0
        assert "ccna-vlan-001" in result.output

    def test_difficulty_filter_no_match(self) -> None:
        result = runner.invoke(
            app, ["list", "--difficulty", "advanced", "--scenarios-dir", str(SCENARIOS_DIR)]
        )
        assert result.exit_code == 0
        assert "No scenarios found" in result.output

    def test_difficulty_filter_case_insensitive(self) -> None:
        result = runner.invoke(
            app, ["list", "--difficulty", "BEGINNER", "--scenarios-dir", str(SCENARIOS_DIR)]
        )
        assert result.exit_code == 0
        assert "ccna-vlan-001" in result.output

    def test_difficulty_filter_invalid_value_rejected(self) -> None:
        result = runner.invoke(app, ["list", "--difficulty", "expert"])
        assert result.exit_code != 0
        assert "beginner" in result.output.lower()


class TestScenarioIdForms:
    def test_devices_lists_scenario_devices(self) -> None:
        result = runner.invoke(
            app,
            ["devices", "--scenario-id", "ccna-vlan-001", "--scenarios-dir", str(SCENARIOS_DIR)],
        )
        assert result.exit_code == 0, result.output
        assert "SW1" in result.output
        assert "R1" in result.output

    def test_devices_unknown_scenario_errors(self) -> None:
        result = runner.invoke(
            app,
            ["devices", "--scenario-id", "no-such-lab", "--scenarios-dir", str(SCENARIOS_DIR)],
        )
        assert result.exit_code == 1

    def test_start_positional_and_option_conflict_rejected(self) -> None:
        result = runner.invoke(
            app,
            [
                "start", "ccna-vlan-001", "--scenario-id", "ccna-vlan-001",
                "--scenarios-dir", str(SCENARIOS_DIR),
            ],
        )
        assert result.exit_code != 0
        assert "not both" in result.output

    def test_start_missing_scenario_id_rejected(self) -> None:
        result = runner.invoke(app, ["start", "--scenarios-dir", str(SCENARIOS_DIR)])
        assert result.exit_code != 0
        assert "scenario id" in result.output.lower()

    def test_console_missing_scenario_id_rejected(self) -> None:
        result = runner.invoke(app, ["console", "--scenarios-dir", str(SCENARIOS_DIR)])
        assert result.exit_code != 0
        assert "scenario id" in result.output.lower()


class TestTopologyDiagram:
    def test_list_shows_diagram_under_each_scenario(self) -> None:
        result = runner.invoke(app, ["list", "--scenarios-dir", str(SCENARIOS_DIR)])
        assert result.exit_code == 0
        assert "┌" in result.output
        assert "Links:" in result.output
        # Diagram lines are indented under the scenario entry.
        assert "      ┌" in result.output

    def test_list_no_diagram_flag_hides_it(self) -> None:
        result = runner.invoke(
            app, ["list", "--no-diagram", "--scenarios-dir", str(SCENARIOS_DIR)]
        )
        assert result.exit_code == 0
        assert "┌" not in result.output
        assert "ccna-vlan-001" in result.output

    def test_devices_shows_topology_diagram(self) -> None:
        result = runner.invoke(
            app,
            ["devices", "--scenario-id", "ccna-vlan-001", "--scenarios-dir", str(SCENARIOS_DIR)],
        )
        assert result.exit_code == 0, result.output
        assert "Topology:" in result.output
        assert "┌" in result.output
        assert "  ┌" in result.output
