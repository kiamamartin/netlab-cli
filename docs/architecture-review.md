# NetLab CLI — Architecture Review (Phase 0)

> Status: initial review produced before implementation of the first milestone.
> Source of truth: `netlab_cli_architecture.md` (read in full; 2,702 lines).
> This document records confirmed decisions, ambiguities, risks, and the decisions taken where the
> architecture is underspecified. Controversial changes are NOT implemented until justified.

---

## Repository assessment

| Item | Finding |
|---|---|
| Repository state | No existing project. Working directory was the user home (`/home/marto`), not a git repository. `~/Projects/netops-platform` exists but is an unrelated Nornir-based automation project. |
| Python version | 3.14.6 (system default); 3.12 also installed at `/usr/bin/python3.12`. Architecture floor is 3.12+, so the project pins `.python-version = 3.12`. |
| uv | 0.11.19 — present and current. |
| Existing project / dependencies / tests | None for NetLab. Everything is created fresh. |
| Potential conflicts | None identified. |

**Assumption:** the repository lives at `~/Projects/netlab-cli`, alongside existing projects. Moving the
directory has no architectural consequence.

---

## A. Confirmed architectural decisions

These decisions are read from the architecture document and will be followed without alteration:

1. **Separation of concerns.** Freebuff is the development agent only. The chain is strictly:
   student command → CLI parser → state mutation → network state → verification → deterministic
   grading → structured assessment → AI explanation. The AI is never authoritative for correctness
   and never mutates state.
2. **Domain-first development.** `CLI → application service → domain model → network state`.
   No arbitrary dictionary manipulation from CLI classes.
3. **Single authoritative network state.** One `NetworkState` aggregate owns devices, interfaces,
   links, VLANs and (later) routes/ARP/MAC/STP/DHCP state. `show` output is rendered from state,
   never hard-coded; grading reads state, never raw CLI text.
4. **IOS CLI as a mode state machine.** USER_EXEC → PRIVILEGED_EXEC → GLOBAL_CONFIG →
   INTERFACE_CONFIG / VLAN_CONFIG (ROUTER_CONFIG later). Prompts reflect the mode; commands are
   validated against the current mode.
5. **Milestone-1 command scope** (brief §11): `enable`, `disable`, `exit`; `configure terminal`,
   `show running-config|startup-config|vlan brief|interfaces trunk|ip interface brief|
   mac address-table`, `copy running-config startup-config`; global config `hostname`, `interface`,
   `vlan`; VLAN config `name`; interface config `description`, `shutdown`/`no shutdown`,
   `switchport mode access|trunk`, `switchport access vlan`, `switchport trunk allowed vlan`,
   `switchport trunk native vlan`, `ip address`, `no ip address`. Nothing else until the state
   represents it correctly.
6. **IOS-style error behaviour** as a reusable system (`% Invalid input detected at '^' marker.`,
   `% Incomplete command.`, `% Ambiguous command.`, `% Invalid interface type and number.`) with
   consistent educational behavior rather than forensic IOS fidelity.
7. **First scenario fixed**: router-on-a-stick (R1 ↔ trunk ↔ SW1 → PC-A VLAN 10 / PC-B VLAN 20),
   VLAN 10 = ADMIN, VLAN 20 = SALES, SW1 Gi0/1 trunk, Gi0/2 → VLAN 10, Gi0/3 → VLAN 20,
   R1 Gi0/0/0.10 = 192.168.10.1/24, Gi0/0/0.20 = 192.168.20.1/24. No DHCP/OSPF/ACL/STP/EtherChannel.
8. **Deterministic grading.** `Check(id, category, points, validator)` objects; the grader answers
   what was expected / what exists / pass-fail / points / why. LLM never assigns or adjusts points.
9. **Golden configuration testing**: known-good config → 100/100, plus negative mutations
   (missing VLAN, wrong name, wrong access VLAN, missing trunk, wrong trunk VLANs, wrong IP,
   admin-down) each failing the expected checks. No AI integration until this is reliable.
10. **Technology stack**: Python 3.12+, uv, Typer, Rich, Pydantic, PyYAML, SQLite, pytest.
    Standard library preferred where suitable; no web framework; terminal-first.
11. **Normalized state model** (doc §49): simulator, CML, and hardware backends all feed the same
    normalized state into one grader. Domain objects must not leak IOS text-parsing concerns.
12. **Security**: student input is never executed as host shell commands; credentials never live in
    scenario files; AI receives structured evidence only.
13. **Development order** (doc §75): DOMAIN MODEL → NETWORK STATE → CLI ENGINE → SIMULATION →
    VERIFICATION → DETERMINISTIC GRADING → SCENARIO LIBRARY → TROUBLESHOOTING → AI INSTRUCTOR →
    ADAPTIVE LEARNING → CML BACKEND → HARDWARE BACKEND. Never reversed.

---

## B. Ambiguities

| # | Ambiguity | Status |
|---|---|---|
| B1 | Project location is unspecified (doc lives in `~/Downloads`; no repo exists). | **Resolved by assumption:** `~/Projects/netlab-cli`. Documented; trivially reversible. |
| B2 | License not specified anywhere. | **Resolved by assumption:** MIT (permissive; suitable for an educational tool). Recorded in `LICENSE` and `pyproject.toml`. |
| B3 | Running-config representation: command log vs rendered-from-state. Doc says state is authoritative and `show` output must change with state. | **Resolved:** running-config is *rendered* from state (renderer in the IOS module, later stage). startup-config is a frozen snapshot of the device's configuration state taken by `copy running-config startup-config`; a future `reload` restores from that snapshot. |
| B4 | Canonical interface naming. Doc YAML uses `GigabitEthernet0/1`; the brief also says `Gi0/1`. | **Resolved:** domain stores canonical long names (`GigabitEthernet0/1`, `GigabitEthernet0/0/0.10`, `Vlan10`, `Ethernet0`); the CLI layer accepts IOS abbreviations (`gi0/1`, `gig 0/2`) and canonicalizes before state mutation. |
| B5 | Router subinterfaces (`Gi0/0/0.10`) — separate entity or attribute of the physical port? | **Resolved:** separate `Interface` entries whose name contains a `.`, with `parent` (canonical physical name) and `encapsulation_vlan` (802.1Q tag). Matches IOS semantics and makes `show ip interface brief` / grading straightforward. |
| B6 | `switchport access vlan 10` when VLAN 10 does not exist: IOS creates the VLAN (with a log message); some simulators reject. | **Resolved in stage 4:** mirror IOS — the VLAN is auto-created so students experience real behavior; the deterministic grader judges whether the resulting state matches the goal. |
| B7 | Trunk allowed-VLAN list semantics (`switchport trunk allowed vlan 10,20` *replaces*; IOS also has `add`, `remove`, `all`, `except`). | **Resolved:** state stores a `set[int]` (plus "all" default = 1-4094). CLI stage implements IOS verbs exactly (`all`/`add`/`remove`/`except`/replace). |
| B8 | Operational state of an interface: no STP/link-latency simulation exists yet. | **Resolved (v1):** `oper-up = admin_enabled AND link.status == up AND link exists`. Unconnected → down. STP states are a later phase; trunks are always explicitly configured (no DTP negotiation modeled). |
| B9 | PC configuration surface. Real Packet Tracer configures PCs via a desktop GUI; the doc does not define a PC CLI. Milestone-1 requires PC gateways. | **Resolved in stage 4:** minimal IOS-like host surface — `ip address A.B.C.D M.M.M.M` and `ip default-gateway A.B.C.D` (available in both EXEC modes, not mode-based, like Packet Tracer's IP Configuration utility). Registered only on PC/Server device types. |
| B10 | "Verification" grading category (doc §23) implies grading whether the student *ran* verification, which needs command history (telemetry, Phase 5). | **Resolved for milestone 1:** grading is purely state-based; the verification category enters with the telemetry/history phase. Scenario schema keeps a `verification:` section (doc §40) reserved but not graded yet. |
| B11 | Connectivity (`ping`) requires an L2/L3 simulation (MAC learning, ARP). Doc phases put ping in Phase 3 but the first scenario's student steps include "verify connectivity". | **Open — bounded by phases.** Milestone-1 grading is configuration-state-based; ping lands with the Phase 3 simulation work. The scenario's graded checks do not depend on ping. |
| B12 | Persistence: SQLite vs JSON. Doc suggests session files as JSON (§38–39) *and* SQLite for records. | **Resolved:** sessions saved as JSON files (`~/.local/share/netlab/sessions/`) for portability; SQLite introduced with the progress/analytics phase (Phase 7). |
| B13 | Difficulty naming: doc uses both words (`beginner`) and levels (1–5). | **Resolved:** schema stores the word form (`beginner|intermediate|advanced|exam|troubleshooting`) mapped to levels 1–5; documented in `docs/scenario-authoring.md`. |
| B14 | `CONTRIBUTING.md` appears in the doc's tree but not the brief's. `scripts/seed_scenarios.py` has no purpose until there is a scenario library. | **Resolved:** defer both until meaningful (per "no placeholder modules"). Noted here so the omission is deliberate. |
| B15 | VLAN 1 semantics: undeletable, default name `default`. | **Resolved:** every switch is constructed with VLAN 1 present; domain rejects its removal. |

---

## C. Technical risks

1. **IOS command parsing breadth.** Abbreviations (`conf t`), case-insensitivity, whitespace, and
   ambiguous prefixes (`sh` vs `show`) can quickly become a regex swamp.
   *Mitigation:* explicit command registry per mode with longest-prefix matching and mode gating;
   structured `Command` objects; a single error taxonomy (`core/errors.py`) mapped to IOS-style
   messages at the presentation layer.
2. **Command injection / shell interpretation.** Student text must never reach a shell, filesystem
   path, or `eval`. *Mitigation:* the parser only ever produces `Command` dataclasses consumed by
   registered handlers; no dynamic dispatch on raw strings; no `os.system`/`subprocess` on input.
3. **State drift** between simulator, `show` renderers, and grader.
   *Mitigation:* renderers and validators may only read `NetworkState`. Integration tests assert
   `show` output matches state mutations, and golden/negative scenario tests lock grading to state.
4. **Snapshot aliasing** (startup-config accidentally sharing objects with running state, or
   snapshots embedding volatile metadata). *Mitigation:* `Config` is a frozen Pydantic model built
   from `model_dump(mode="json", exclude={"startup_config"})`; tests verify snapshot isolation.
5. **Grading the wrong thing** (text matching instead of state).
   *Mitigation:* validators receive typed state objects only; the normalized-state boundary (§49) is
   the grader's input contract, which also keeps CML/hardware backends possible.
6. **YAML scenario schema drift** as features grow.
   *Mitigation:* Pydantic schema with `extra="forbid"`, a `validate_scenarios` script, and
   fail-early loader errors with file/line context (schema arrives in stage 2).
7. **Deterministic scoring vs "Verification" category** (see B10): grading student *behavior*
   requires command history; without it, verification points would be invented.
   *Mitigation:* milestone grading is state-only; behavior-based points deferred explicitly.
8. **Subinterface edge cases** (encapsulation without subinterface, subinterface without parent,
   parent-of-parent). *Mitigation:* model invariants enforced in `Interface`/`Device` validators
   and re-checked by topology validation.
9. **IP model mismatch with later routing phase.** Storing `(ip, prefix)` per interface and deriving
   the network keeps `ip route` additions (Phase 3) additive rather than a refactor.
10. **Test determinism.** Timestamps inside `Config` snapshots must not affect equality checks;
    tests compare configuration fields, not `created_at`.

---

## D. Recommended decisions (underspecified areas)

| Area | Recommendation |
|---|---|
| Domain framework | Pydantic v2 models with `validate_assignment=True`, `extra="forbid"`; frozen models for `Command` and `Config` snapshots. Validation lives in the domain, not the CLI. |
| Package layout | `src/netlab/` with `core` (shared primitives: modes, command model, errors), `devices`, `network`, `ios`, `scenarios`, `grading`, `verification`, `persistence`, `cli`. `instructor`, `telemetry`, `cml`, `protocols` are created only when they gain responsibility (per brief §7). |
| Errors | `NetLabError` root; `CommandError` family (invalid input / incomplete / ambiguous / invalid interface / wrong-mode) for the CLI layer; `DomainError` family (unknown device/interface/VLAN, duplicate VLAN, topology errors) for state. CLI maps domain+command errors to IOS-style text. |
| VLAN range helpers | `parse_vlan_list("10,20-24")` / `format_vlan_list({...})` as domain utilities shared by CLI, `show` renderers, and validators. |
| Operational state | Derived (`Topology.interface_is_up`), never stored — prevents admin/link state divergence. |
| Trunk allowed VLANs | `set[int]`, default full 1-4094; rendering as IOS ranges handled by `format_vlan_list`. |
| Scenario validation pipeline | Loader: YAML parse → Pydantic schema → topology build (validates links/interfaces exist) → grading rules → session start. All failures reported with file context. |
| Testing | pytest with `tests/unit` now; `tests/integration`, `tests/scenarios`, `tests/regression` added when content exists. Dev group: `pytest`, `ruff`; type checking (mypy/pyright) adopted before the CLI milestone to satisfy the quality gates. |
| CLI entry | `netlab` console script → Typer app in `netlab.cli.app`; subcommands (`list`, `start`, `assess`, …) added with the session milestone. |

---

## Implementation plan

1. **Foundation + domain model** *(this review + first code)*: uv project, docs skeleton,
   `core` primitives (modes, `Command`, errors), device models (`Interface`, `VLAN`,
   `Device`/`Switch`/`Router`/`Host`, `Config` snapshot), network models (`Endpoint`, `Link`,
   `Topology`, `NetworkState`), minimal `netlab` CLI entry, unit tests.
2. **Scenario + topology loading**: Pydantic scenario schema, YAML loader with fail-early
   diagnostics, first scenario YAML (`ccna-vlan-001`), topology builder from spec.
3. **IOS CLI engine**: mode state machine, parser, command registry, IOS-style error mapping,
   device-session loop.
4. **Configuration commands**: VLAN/switchport/IP commands mutating state per the milestone-1 scope.
5. **Verification layer**: `show` commands rendered from state; running/startup rendering; `copy`.
6. **Deterministic grading**: `Check`/validator architecture, `Assessment` model, scoring, partial credit.
7. **Golden configuration + negative tests**: first scenario → 100/100; mutations → expected failures.
8. **Sessions**: save/resume/reset, `netlab` orchestration commands.
9. **Documentation + quality gates**: type checking, lint, docs finalized; DoD checklist reviewed.

Stages 6–7 gate AI integration, per the architecture document.
