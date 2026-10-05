"""Integration: CLI → console session → network state → grading engine.

Exercises the full authoritative chain (architecture layering diagram): the
student configures devices through the real Typer CLI session loop, then the
deterministic grader assesses the resulting state — presentation never
touches the score.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from netlab.cli.app import app
from netlab.grading import grade
from netlab.ios.machine import CliSession
from netlab.network.state import NetworkState
from netlab.scenarios import build_initial_state, load_scenario_file

runner = CliRunner()

PROJECT_ROOT = Path(__file__).parents[2]
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"

SW1_CONFIG = [
    "enable",
    "configure terminal",
    "vlan 10",
    "name ADMIN",
    "exit",
    "vlan 20",
    "name SALES",
    "exit",
    "interface GigabitEthernet0/1",
    "switchport mode trunk",
    "switchport trunk allowed vlan 10,20",
    "exit",
    "interface GigabitEthernet0/2",
    "switchport mode access",
    "switchport access vlan 10",
    "exit",
    "interface GigabitEthernet0/3",
    "switchport mode access",
    "switchport access vlan 20",
    "end",
    "submit",
    "exit",
]


def test_console_session_grades_through_submit() -> None:
    """`submit` inside the session renders a report from structured state."""
    result = runner.invoke(
        app,
        [
            "console",
            "--scenario",
            "ccna-vlan-001",
            "--device",
            "SW1",
            "--scenarios-dir",
            str(SCENARIOS_DIR),
        ],
        input="\n".join(SW1_CONFIG) + "\n",
    )
    assert result.exit_code == 0, result.output
    assert "NETLAB ASSESSMENT" in result.output
    assert "60 / 100" in result.output  # switch-side requirements only
    assert "Final Score: 60%" in result.output


def test_pipeline_scenario_to_assessment() -> None:
    """Scenario → state → golden config via engine → 100% assessment."""
    scenario = load_scenario_file(SCENARIOS_DIR / "vlan" / "ccna-vlan-001.yaml")
    state: NetworkState = build_initial_state(scenario)

    sessions = {
        "SW1": [
            "enable", "configure terminal", "vlan 10", "name ADMIN", "exit",
            "vlan 20", "name SALES", "exit",
            "interface GigabitEthernet0/1", "switchport mode trunk",
            "switchport trunk allowed vlan 10,20", "exit",
            "interface GigabitEthernet0/2", "switchport mode access",
            "switchport access vlan 10", "exit",
            "interface GigabitEthernet0/3", "switchport mode access",
            "switchport access vlan 20", "end",
        ],
        "R1": [
            "enable", "configure terminal",
            "interface GigabitEthernet0/0/0.10", "encapsulation dot1q 10",
            "ip address 192.168.10.1 255.255.255.0", "exit",
            "interface GigabitEthernet0/0/0.20", "encapsulation dot1q 20",
            "ip address 192.168.20.1 255.255.255.0", "end",
        ],
        "PC-A": ["ip address 192.168.10.10 255.255.255.0", "ip default-gateway 192.168.10.1"],
        "PC-B": ["ip address 192.168.20.10 255.255.255.0"],
    }
    for hostname, commands in sessions.items():
        session = CliSession(state, hostname)
        for command in commands:
            output = session.execute(command)
            assert not output.startswith("%"), f"{hostname}: {command}"

    assessment = grade(scenario, state)
    assert assessment.percentage == 100
    assert assessment.summary.startswith("100 / 100")
    assert all(
        category.earned == category.possible for category in assessment.categories
    )


def test_cli_scenarios_dir_option_used_by_assess_flow() -> None:
    """`netlab list` still works after the grading additions (no regressions)."""
    result = runner.invoke(app, ["list", "--scenarios-dir", str(SCENARIOS_DIR)])
    assert result.exit_code == 0
    assert "ccna-vlan-001" in result.output
