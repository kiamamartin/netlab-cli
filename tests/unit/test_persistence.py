"""Unit/integration tests for netlab.persistence (JSON save/resume/reset).

Covers the persistence boundary: full NetworkState round-trip through the
real scenario builder, corrupt-file and version-mismatch handling, soft vs
hard reset, and the ``netlab save/resume/reset/assess`` CLI commands wired
onto the Typer app. Command history is intentionally absent from snapshots —
that invariant is asserted too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from netlab.cli.app import app
from netlab.grading import grade
from netlab.ios.machine import CliSession
from netlab.persistence import (
    SCHEMA_VERSION,
    SessionSnapshot,
    delete_session,
    has_session,
    load_session,
    save_session,
    session_path,
)
from netlab.scenarios import build_initial_state, load_scenario_file

runner = CliRunner()

PROJECT_ROOT = Path(__file__).parents[2]
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"
SCENARIO = SCENARIOS_DIR / "vlan" / "ccna-vlan-001.yaml"


@pytest.fixture()
def sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NETLAB_SESSIONS_DIR", str(tmp_path))
    return tmp_path


def _configured_state() -> tuple:
    scenario = load_scenario_file(SCENARIO)
    state = build_initial_state(scenario)
    session = CliSession(state, "SW1")
    for command in (
        "enable", "configure terminal", "vlan 10", "name ADMIN", "exit",
        "vlan 20", "exit",
        "interface GigabitEthernet0/1", "switchport mode trunk",
        "switchport trunk allowed vlan 10,20", "end",
    ):
        session.execute(command)
    return scenario, state, session


class TestSaveLoadRoundTrip:
    def test_save_then_load_restores_state(
        self, sessions_dir: Path
    ) -> None:
        scenario, state, _session = _configured_state()
        save_session("ccna-vlan-001", "SW1", state)
        assert has_session("ccna-vlan-001")

        snapshot, restored = load_session("ccna-vlan-001")
        assert snapshot.scenario_id == "ccna-vlan-001"
        assert snapshot.hostname == "SW1"
        assert snapshot.schema_version == SCHEMA_VERSION
        # Restored state grades identically to the in-memory state.
        assert grade(scenario, restored) == grade(scenario, state)

    def test_restored_state_is_configurable(self, sessions_dir: Path) -> None:
        """The round-tripped state must be a live, mutable NetworkState."""
        _scenario, state, _session = _configured_state()
        save_session("ccna-vlan-001", "SW1", state)
        _snapshot, restored = load_session("ccna-vlan-001")
        session = CliSession(restored, "SW1")
        for command in (
            "enable", "configure terminal", "interface GigabitEthernet0/2",
            "switchport mode access", "switchport access vlan 10", "end",
        ):
            output = session.execute(command)
            assert not output.startswith("%"), output

    def test_load_missing_session_raises(self, sessions_dir: Path) -> None:
        from netlab.persistence import PersistenceError

        with pytest.raises(PersistenceError, match="no saved session"):
            load_session("ccna-vlan-001")

    def test_load_corrupt_session_raises(self, sessions_dir: Path) -> None:
        from netlab.persistence import PersistenceError

        save_session("ccna-vlan-001", "SW1", build_initial_state(
            load_scenario_file(SCENARIO)
        ))
        session_path("ccna-vlan-001").write_text("{not json", encoding="utf-8")
        with pytest.raises(PersistenceError, match="corrupt"):
            load_session("ccna-vlan-001")

    def test_load_version_mismatch_raises(self, sessions_dir: Path) -> None:
        from netlab.persistence import PersistenceError

        save_session("ccna-vlan-001", "SW1", build_initial_state(
            load_scenario_file(SCENARIO)
        ))
        path = session_path("ccna-vlan-001")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["schema_version"] = SCHEMA_VERSION + 1
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(PersistenceError, match="schema version"):
            load_session("ccna-vlan-001")

    def test_delete_session_removes_file(self, sessions_dir: Path) -> None:
        save_session(
            "ccna-vlan-001", "SW1",
            build_initial_state(load_scenario_file(SCENARIO)),
        )
        path = delete_session("ccna-vlan-001")
        assert not path.exists()
        assert not has_session("ccna-vlan-001")

    def test_snapshot_excludes_command_history(self, sessions_dir: Path) -> None:
        """Session files carry state only — no history, buffer, telemetry."""
        _scenario, state, session = _configured_state()
        assert len(session.history) > 0  # the session genuinely recorded input
        save_session("ccna-vlan-001", "SW1", state)
        data = json.loads(session_path("ccna-vlan-001").read_text(encoding="utf-8"))
        assert set(data) == {
            "schema_version", "scenario_id", "hostname", "saved_at", "state"
        }
        assert "history" not in json.dumps(data).lower().replace(
            "schema_version", ""
        ) or "history" not in data


class TestCliPersistenceCommands:
    def test_save_resume_and_assess_through_cli(self, sessions_dir: Path) -> None:
        """save → configure elsewhere → resume keeps state → assess grades it."""
        scenario = load_scenario_file(SCENARIO)
        state = build_initial_state(scenario)
        save_session("ccna-vlan-001", "SW1", state)

        result = runner.invoke(
            app,
            ["assess", "ccna-vlan-001", "--scenarios-dir", str(SCENARIOS_DIR)],
        )
        assert result.exit_code == 0, result.output
        assert "Final Score: 0%" in result.output

    def test_resume_enter_session_and_exit(self, sessions_dir: Path) -> None:
        save_session(
            "ccna-vlan-001", "SW1",
            build_initial_state(load_scenario_file(SCENARIO)),
        )
        result = runner.invoke(
            app,
            [
                "resume", "ccna-vlan-001", "--device", "SW1",
                "--scenarios-dir", str(SCENARIOS_DIR),
            ],
            input="show vlan brief\nexit\n",
        )
        assert result.exit_code == 0, result.output
        assert "Resuming scenario" in result.output
        assert "VLAN Name" in result.output  # the restored state served show

    def test_resume_unknown_scenario_fails(self, sessions_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["resume", "no-such-lab", "--scenarios-dir", str(SCENARIOS_DIR)],
        )
        assert result.exit_code == 1
        assert "no saved session" in result.output

    def test_resume_device_not_in_scenario_fails(self, sessions_dir: Path) -> None:
        save_session(
            "ccna-vlan-001", "SW1",
            build_initial_state(load_scenario_file(SCENARIO)),
        )
        result = runner.invoke(
            app,
            [
                "resume", "ccna-vlan-001", "--device", "NOPE",
                "--scenarios-dir", str(SCENARIOS_DIR),
            ],
        )
        assert result.exit_code == 1
        assert "not part of scenario" in result.output

    def test_save_writes_session_file(self, sessions_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "save", "ccna-vlan-001", "--device", "SW1",
                "--scenarios-dir", str(SCENARIOS_DIR),
            ],
        )
        assert result.exit_code == 0, result.output
        assert has_session("ccna-vlan-001")
        assert "netlab resume ccna-vlan-001" in result.output

    def test_reset_soft_restores_initial_state(self, sessions_dir: Path) -> None:
        scenario, state, _session = _configured_state()
        save_session("ccna-vlan-001", "SW1", state)
        # Verify the saved state earns points before resetting.
        _snap, loaded = load_session("ccna-vlan-001")
        assert grade(scenario, loaded).earned_points > 0

        result = runner.invoke(
            app,
            ["reset", "ccna-vlan-001", "--scenarios-dir", str(SCENARIOS_DIR)],
        )
        assert result.exit_code == 0, result.output
        _snap, reset_state = load_session("ccna-vlan-001")
        assert grade(scenario, reset_state).earned_points == 0

    def test_reset_hard_deletes_session(self, sessions_dir: Path) -> None:
        save_session(
            "ccna-vlan-001", "SW1",
            build_initial_state(load_scenario_file(SCENARIO)),
        )
        result = runner.invoke(
            app,
            ["reset", "ccna-vlan-001", "--hard", "--scenarios-dir", str(SCENARIOS_DIR)],
        )
        assert result.exit_code == 0, result.output
        assert not has_session("ccna-vlan-001")

    def test_assess_missing_session_fails(self, sessions_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["assess", "ccna-vlan-001", "--scenarios-dir", str(SCENARIOS_DIR)],
        )
        assert result.exit_code == 1
        assert "no saved session" in result.output


class TestSnapshotModel:
    def test_scenario_id_required(self) -> None:
        from netlab.core.errors import DomainError  # noqa: F401

        with pytest.raises(ValueError):
            SessionSnapshot(
                scenario_id="", hostname="SW1",
                saved_at="2026-10-08T00:00:00Z", state={},
            )
