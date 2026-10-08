"""Session persistence: JSON save/resume/reset (architecture doc §38–39, B12).

Sessions are stored as JSON files (one per scenario id) under
``~/.local/share/netlab/sessions/`` (overridable with
``NETLAB_SESSIONS_DIR``). Decision B12 resolved SQLite-vs-JSON in favour of
JSON for portability; SQLite is deferred to the progress/analytics phase.

What is persisted:

- the scenario id (so resume can rebuild the grading contract),
- the device hostname the session was attached to,
- the authoritative :class:`NetworkState` (all devices, interfaces, VLANs,
  startup-config snapshots),
- the in-session device mode/context at save time.

What is deliberately **not** persisted: command history buffers (the
traversal buffer is session-scoped by design) and anything AI/telemetry —
those modules do not exist yet and must never leak into session files.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from netlab.core.errors import DomainError
from netlab.network.state import NetworkState
from netlab.network.topology import Topology

SESSIONS_DIR_ENV = "NETLAB_SESSIONS_DIR"
DEFAULT_SESSIONS_DIR = Path.home() / ".local" / "share" / "netlab" / "sessions"
SCHEMA_VERSION = 1


class PersistenceError(DomainError):
    """A session could not be saved, loaded, or reset."""


class SessionSnapshot(BaseModel):
    """Everything needed to resume a lab session from disk."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = SCHEMA_VERSION
    scenario_id: str = Field(min_length=1)
    hostname: str
    saved_at: datetime
    state: dict  # NetworkState.model_dump(mode="json")

    @classmethod
    def capture(
        cls, scenario_id: str, hostname: str, state: NetworkState
    ) -> SessionSnapshot:
        return cls(
            scenario_id=scenario_id,
            hostname=hostname,
            saved_at=datetime.now(UTC),
            state=state.model_dump(mode="json"),
        )


def sessions_dir() -> Path:
    """The directory session files live in (env-overridable)."""
    override = os.environ.get(SESSIONS_DIR_ENV)
    if override:
        return Path(override)
    return DEFAULT_SESSIONS_DIR


def session_path(scenario_id: str) -> Path:
    return sessions_dir() / f"{scenario_id}.json"


def save_session(scenario_id: str, hostname: str, state: NetworkState) -> Path:
    """Write ``state`` as the saved session for ``scenario_id``.

    Returns the path written. The directory is created on demand; a corrupt
    pre-existing file is overwritten rather than blocking the save.
    """
    snapshot = SessionSnapshot.capture(scenario_id, hostname, state)
    path = session_path(scenario_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise PersistenceError(
            f"could not save session {scenario_id!r} to {path}: {exc}"
        ) from exc
    return path


def load_session(scenario_id: str) -> tuple[SessionSnapshot, NetworkState]:
    """Load the saved session for ``scenario_id``.

    Returns the snapshot (metadata) and the reconstructed
    :class:`NetworkState`. Raises :class:`PersistenceError` when no session
    exists or the file is corrupt/incompatible.
    """
    path = session_path(scenario_id)
    if not path.is_file():
        raise PersistenceError(
            f"no saved session for scenario {scenario_id!r} "
            f"(expected {path}); start it with 'netlab start {scenario_id}'"
        )
    try:
        snapshot = SessionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PersistenceError(
            f"saved session {scenario_id!r} is corrupt ({path}): {exc}"
        ) from exc
    if snapshot.schema_version != SCHEMA_VERSION:
        raise PersistenceError(
            f"saved session {scenario_id!r} uses schema version "
            f"{snapshot.schema_version}, but this build understands "
            f"{SCHEMA_VERSION}; the session cannot be resumed"
        )
    try:
        state = _restore_state(snapshot.state)
    except Exception as exc:
        raise PersistenceError(
            f"saved session {scenario_id!r} could not be restored "
            f"(state invalid): {exc}"
        ) from exc
    return snapshot, state


def _restore_state(dump: dict) -> NetworkState:
    """Rebuild a :class:`NetworkState`, re-typing devices to their subclasses.

    A plain ``NetworkState.model_validate`` would instantiate every device as
    the base :class:`Device` (losing the VLAN database, default gateways,
    etc.); the concrete class is recovered from the serialized
    ``device_type`` discriminator — the same approach the startup-config
    renderer uses for single devices.
    """
    from netlab.devices.host import Host, Server
    from netlab.devices.router import Router
    from netlab.devices.switch import Switch

    dump = json.loads(json.dumps(dump))  # deep copy; never mutate the snapshot
    device_cls: dict[str, type] = {
        "router": Router,
        "switch": Switch,
        "pc": Host,
        "server": Server,
    }
    devices = dump.get("topology", {}).get("devices", {})
    re_typed: dict[str, object] = {}
    for hostname, device_dump in devices.items():
        cls = device_cls.get(device_dump.get("device_type", ""))
        if cls is None:
            raise ValueError(
                f"unknown device_type {device_dump.get('device_type')!r}"
            )
        re_typed[hostname] = cls.model_validate(device_dump)
    dump["topology"]["devices"] = re_typed
    topology = Topology.model_validate(dump["topology"])
    return NetworkState(topology=topology)


def delete_session(scenario_id: str) -> Path:
    """Delete the saved session file; returns the removed path.

    Raises :class:`PersistenceError` when nothing is saved for the scenario.
    """
    path = session_path(scenario_id)
    if not path.is_file():
        raise PersistenceError(f"no saved session for scenario {scenario_id!r}")
    try:
        path.unlink()
    except OSError as exc:
        raise PersistenceError(
            f"could not delete session {scenario_id!r}: {exc}"
        ) from exc
    return path


def has_session(scenario_id: str) -> bool:
    return session_path(scenario_id).is_file()


def reset_session(scenario_id: str, scenario_dir: Path, *, hard: bool) -> str:
    """Reset a scenario's session.

    - **soft** (default): restore the initial state — the saved session is
      replaced by a freshly built one from the scenario YAML, so ``resume``
      keeps working against pristine defaults.
    - **hard** (``--hard``): delete the saved session entirely; the next
      ``netlab start``/``resume`` begins from zero.

    Returns a human-readable description of what happened.
    """
    from netlab.scenarios import build_initial_state, resolve_scenario

    _path, scenario = resolve_scenario(scenario_dir, scenario_id)
    if hard:
        if has_session(scenario_id):
            delete_session(scenario_id)
            return (
                f"Session for {scenario_id!r} deleted; "
                "the next start begins from the scenario's initial state."
            )
        return f"No saved session for {scenario_id!r}; nothing to delete."
    # Soft reset: rebuild the pristine state and persist it over the save.
    state = build_initial_state(scenario)
    save_session(scenario_id, scenario.topology.devices[0].name, state)
    return (
        f"Session for {scenario_id!r} reset to the scenario's initial state."
    )


def reset_all_sessions(*, hard: bool) -> int:
    """Reset every saved session; returns how many were affected."""
    directory = sessions_dir()
    if not directory.is_dir():
        return 0
    count = 0
    for path in sorted(directory.glob("*.json")):
        scenario_id = path.stem
        try:
            if hard:
                delete_session(scenario_id)
            else:
                # Soft reset needs the scenario; fall back to deletion when it
                # is no longer discoverable.
                from netlab.scenarios import build_initial_state, resolve_scenario

                try:
                    _p, scenario = resolve_scenario(Path("scenarios"), scenario_id)
                except Exception:
                    delete_session(scenario_id)
                    count += 1
                    continue
                state = build_initial_state(scenario)
                save_session(scenario_id, scenario.topology.devices[0].name, state)
        except PersistenceError:
            continue
        count += 1
    return count


def wipe_sessions_dir() -> int:
    """Remove the whole sessions directory (used by hard ``--all`` resets)."""
    directory = sessions_dir()
    if not directory.is_dir():
        return 0
    count = len(list(directory.glob("*.json")))
    shutil.rmtree(directory)
    return count


__all__ = [
    "DEFAULT_SESSIONS_DIR",
    "SESSIONS_DIR_ENV",
    "SCHEMA_VERSION",
    "PersistenceError",
    "SessionSnapshot",
    "delete_session",
    "has_session",
    "load_session",
    "reset_all_sessions",
    "reset_session",
    "save_session",
    "session_path",
    "sessions_dir",
    "wipe_sessions_dir",
]
