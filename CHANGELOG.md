# Changelog

All notable changes to NetLab CLI are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [Unreleased]

### Added
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
- Atomic `Interface.set_ip` / `Interface.clear_ip` operations for paired IPv4 state.
- Minimal `netlab` CLI entry point (`netlab version`).
- Unit tests for the domain model and CLI entry point.

[Unreleased]: https://github.com/netlab-cli/netlab-cli/compare/v0.0.0...HEAD
