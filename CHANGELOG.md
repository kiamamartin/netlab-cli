# Changelog

All notable changes to NetLab CLI are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [Unreleased]

### Added
- Deterministic grading engine (`netlab.grading`, milestone 6):
  - `grade(scenario, state)` → frozen `Assessment` (results in scenario order,
    per-category rollup, percentage, summary) — no LLM, no rendered text, no
    timestamps; identical state always grades identically.
  - One deterministic validator per requirement type (10 types), resolved via a
    type→validator registry; unknown types raise `GradingError`.
  - Explicit partial credit (doc §24): half-configured trunk, access VLAN
    without the mode command, unpruned trunk allowed-list, right subnet with
    wrong host address, shutdown interface — each a fixed fraction chosen by
    the check, never invented at runtime. Default state values never earn
    points (an unconfigured lab scores exactly 0).
  - Terminal report renderer consuming the structured assessment
    (`render_assessment`); calculates nothing, no width assumptions.
  - `assess` / `submit` session commands (privileged EXEC) render the report.
  - Golden-configuration gate: doc §74 router-on-a-stick golden config through
    the real CLI engine scores 100/100; 13 negative mutations each fail exactly
    the expected requirements; determinism, state-immutability, and
    integration tests (`tests/scenarios/`, `tests/integration/`).
- Engineering audit (`docs/milestone-1-5-audit.md`) with measured baseline
  (tests, lint, CLI smoke run, performance), defect list D1–D8, milestone
  status table, and explicit deferred items.
- uv project scaffolding (`pyproject.toml`, dev dependency group, `netlab` console script).
- Phase 0 architecture review (`docs/architecture-review.md`).
- Domain model in `netlab.core` and `netlab.devices`:
  - IOS configuration modes (`ConfigMode`), device types, switchport modes, VLAN/link status enums.
  - `Command` model (raw/verb/arguments/mode) for the future CLI parser.
  - Error taxonomy (`NetLabError`, command errors, domain errors).
- Device layer: `Interface` (canonical IOS names, switchport state, IPv4 addressing, subinterfaces),
  `VLAN`, `Device` base with running/startup configuration snapshot (`Config`), `Switch` (VLAN
  database), `Router`, `Host` (PC/Server with default gateway).
- Network layer: `Endpoint`, `Link`, validated `Topology`, and the authoritative `NetworkState`
  aggregate.
- Scenario layer (`netlab.scenarios`):
  - Pydantic schema with 10 discriminated requirement types, fail-early validation, and
    points-must-sum-to-total enforcement.
  - YAML loader with file-context diagnostics; `discover_scenarios` tolerates broken files.
  - `build_initial_state` instantiates the topology and applies pre-configuration
    (VLANs, interface state, host gateways) for troubleshooting-style labs.
  - `validate_requirements` checks that grading rules reference real devices/interfaces.
- First milestone scenario `scenarios/vlan/ccna-vlan-001.yaml` (router-on-a-stick; 15 requirements
  across 5 categories summing to 100 points).
- IOS CLI engine (`netlab.ios`):
  - Five-mode state machine (`CliSession`) with context-aware prompts and full
    `exit`/`end`/`enable`/`disable` transitions.
  - IOS-style parser: per-word unique-prefix matching (`conf t`), case-insensitivity,
    ambiguity (`e` in EXEC → `% Ambiguous command.`), incomplete-command detection for
    truncated input, and caret-positioned `% Invalid input detected at '^' marker.` errors.
  - Structural commands: `hostname`, `interface` (with IOS-like creation of missing
    interfaces/subinterfaces), `vlan`, `name`, `help`/`?`; device gating registers VLAN
    commands only on switches.
  - Command history entries recorded per execution for later analytics (doc §34).
- `netlab console --scenario <id> --device <host>` interactive device session.
- Verification layer (stage 5) — show commands rendered from authoritative state:
  - `show vlan brief` (ports grouped per VLAN, trunks under native VLAN),
    `show interfaces trunk` (mode/encapsulation/status, allowed-and-active VLANs),
    `show ip interface brief` (addressing + derived up/down state),
    `show running-config` / `show startup-config` (IOS-style configuration language).
  - `copy running-config startup-config` snapshots state; startup output is rendered
    from the frozen snapshot with the same renderer, and later mutations cannot
    affect it (isolation tests).
- Configuration commands (stage 4) mutating the authoritative network state:
  - `switchport mode access|trunk`, `switchport access vlan` (auto-creates missing
    VLANs per decision B6; assigning the VLAN does not imply the mode), trunk
    `allowed vlan` with IOS `all`/`add`/`remove`/`except` verbs, `trunk native vlan`.
  - `ip address A.B.C.D M.M.M.M` (dot-mask or `/prefix`), `no ip address`,
    `encapsulation dot1q <vlan>` on subinterfaces only.
  - `description` (free text), `shutdown`/`no shutdown`.
  - End-host surface per decision B9: `ip address`, `no ip address`,
    `ip default-gateway` on PCs/servers (both EXEC modes, Packet Tracer style).
  - Parser upgraded for multi-token arguments, free-text arguments, and
    variable-length VLAN-list arguments with caret-positioned errors.
  - Golden-configuration smoke test: the entire milestone lab executes through
    the CLI engine with zero error output.
- Atomic `Interface.set_ip` / `Interface.clear_ip` operations for paired IPv4 state.
- Minimal `netlab` CLI entry point (`netlab version`), `netlab list` / `netlab devices` /
  `netlab start` / `netlab console`.
- Unit tests for the domain model and CLI entry point.

### Fixed
- Console loop now terminates at pipe EOF instead of re-prompting forever, and
  `run_console` propagates `SessionExit` (documented contract) — regression-tested.
- Removed left-over debug prints and a dead output buffer from the session.
- Restored IOS-style `% Ambiguous command.` for bare `e` in EXEC modes; duplicate
  command registration is now rejected at registration time instead of being
  deduplicated at resolve time.
- Command history is bounded (`HISTORY_BUFFER_SIZE`, deque-backed).
- `show ip interface brief` sizes the interface column from the widest short name,
  so long interface names cannot shift the remaining columns.

[Unreleased]: https://github.com/netlab-cli/netlab-cli/compare/v0.0.0...HEAD
