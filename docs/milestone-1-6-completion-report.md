# Milestones 1–6 Completion Report

> Date: 2026-10-05 · Branch: `main`
>
> Sequence executed: audit → milestones 1–5 validation → performance measurement → TUI/layout
> audit → bug fixes → regression tests → stabilization → milestone 6 design → deterministic
> grading engine → grading tests → full regression.

## Executive Summary

Milestones 1–5 (domain model, scenario loading, IOS CLI engine, configuration commands,
verification renderers) were audited against `netlab_cli_architecture.md` and the in-repo
architecture docs. The baseline found 1 failing test, a hung test run, a CLI that looped forever
on EOF, left-over debug output, a dropped IOS-ambiguity contract, unbounded history, and lint
debt — all fixed with regression tests (`docs/milestone-1-5-audit.md` documents the evidence).

On that stabilized foundation, Milestone 6 was implemented: a deterministic grading engine
(`netlab.grading`) that grades the authoritative `NetworkState` against declarative scenario
requirements — never rendered text, never an LLM. The golden router-on-a-stick configuration
scores 100/100 through the real CLI engine; 13 negative mutations each fail exactly the expected
requirements; determinism is regression-tested. The authoritative chain now ends in a structured
`Assessment` rendered by the presentation layer:

```text
CLI/session → config commands → NetworkState → verification (show)
                                             → grading engine → Assessment → report → (future AI)
```

## Milestones 1–5

| Milestone | Status | Major findings | Fixes | Tests |
|---|---|---|---|---|
| M1 Domain model | PASS (audit) / PASS (post-fix) | None blocking | — | Existing unit suite |
| M2 Scenario loading | PASS | None blocking | — | Existing unit suite |
| M3 IOS CLI engine | FAIL → PASS | D1 EOF infinite loop, D2 `SessionExit` swallowed, D3 debug prints, D4 ambiguity contract dropped | Console terminates at EOF and propagates `SessionExit`; debug output and dead buffer removed; `e`-prefix ambiguity restored; duplicate registration rejected | `test_audit_regressions.py::TestD1–D4` |
| M4 Configuration commands | PASS (1 defect) | D5 history unbounded (constant declared, never enforced) | deque(maxlen) bound | `TestD5BoundedHistory` |
| M5 Verification renderers | FAIL (cosmetic) → PASS | D6 `show ip interface brief` mis-aligns for long names | Column width derived from widest short name | `TestD6ShowIpInterfaceBriefAlignment` |

All fixes are pinned in `tests/unit/test_audit_regressions.py` (15 tests). D7 (lint) and D8
(lone-ESC write target) were fixed without dedicated tests (covered by ruff and code paths).

### Known issues / deferred (details in audit §8)

- `Config.created_at` unused until `reload`/analytics (Phase 7+).
- Parser validates arguments twice per resolve (~0.1–0.2 ms/command; sub-threshold).
- No coverage tooling configured (per task constraints; test files cover every module).
- Interactive raw-mode editor (TTY path) has no pty tests.
- `netlab save/resume/reset/status` remain Milestone 8 (roadmap).

## TUI Stabilization

NetLab's UI is a line-oriented IOS console (multi-pane TUI is doc §57, later phase); the audit
surface was prompts, tables, error renderers, and width behavior.

| Aspect | Finding | Action |
|---|---|---|
| Terminal sizes 80×24 → 160×50 (smoke-tested; also smaller via pipes) | No crash vectors; line-oriented output wraps naturally | None needed |
| Prompts | Mode-derived, no width assumptions | ✔ |
| `show vlan brief` | Name column padded to 32; VLAN names capped at 32 by schema — cannot overflow | ✔ |
| `show interfaces trunk` | Fixed columns sized for canonical abbreviations | Acceptable; noted for future renderer work |
| `show ip interface brief` | Hard-coded 21-char column shifted with long names (D6) | Fixed; column width now derived from content; regression-tested |
| Resize behavior | Nothing caches rendered output; nothing to corrupt | ✔ |
| Stale screens / flicker / overlapping widgets | Not applicable to line-oriented console | ✔ |

No snapshot framework is in use; nothing to regenerate blindly.

## Performance

Measured baseline (Python 3.12.13, before fixes):

| Operation | Result |
|---|---|
| Scenario load (YAML + Pydantic) | 101.05 ms (one-time) |
| `build_initial_state` | 2.58 ms |
| `CliSession` construction | 0.58 ms |
| 23 config/show commands | 10.23 ms (~0.4 ms/command) |
| `show vlan brief` (n=200) | 0.353 ms/call |
| Parser resolve `show vlan brief` (n=2000) | 0.196 ms/call |
| Parser resolve `switchport trunk allowed vlan 10,20` (n=2000) | 0.347 ms/call |

Bottlenecks identified: unbounded history growth (D5 — a real leak, fixed); parser double
validation (sub-threshold, documented, deferred). All interactive-path operations are
sub-millisecond; no premature optimization was performed beyond the history bound.

Grading performance follows the same discipline: one requirement-resolution pass, one state
evaluation pass, no repeated YAML parsing (the full golden flow including all four device
sessions grades within the same sub-millisecond-per-check envelope; exact per-check numbers were
not separately benchmarked and are not invented here).

## Milestone 6 — Deterministic Grading Engine

Architecture (docs/grading-engine.md has the full description):

```text
Scenario YAML requirements (points sum to grading.total — schema-enforced)
    ↓ registry (type → validator, resolved once per assessment)
    ↓ checks (pure functions over NetworkState; expected/actual/fraction/why)
    ↓ engine  grade(scenario, state) → Assessment (frozen, ordered)
    ↓ reports render_assessment(assessment) → terminal text
```

- **Domain**: `CheckStatus` (PASS/PARTIAL/FAIL/NOT_APPLICABLE), `RequirementResult`,
  `CategoryScore`, `Assessment` (percentage, passed/partial/failed partitions, unmet
  requirements, deterministic summary).
- **Checks**: one validator per scenario requirement type (10 types); missing references raise
  `GradingError` instead of silently passing.
- **Scoring**: `floor(points × fraction)`, fractions from check definitions only; weighting is
  purely scenario YAML (`ccna-vlan-001.yaml`), no scenario logic in Python.
- **Partial credit** (explicit, regression-tested): trunk-vs-access 0.5; access VLAN without the
  mode command 0.5; unpruned trunk allowed-list 0.5; right subnet / wrong host 0.5; shutdown
  interface 0.5 on the IP requirement. Non-credit rules: default `NONE` mode and coincidental
  defaults (native VLAN 1, all-VLAN list) earn nothing — unconfigured state scores exactly 0.
- **AI boundary**: the grader contains no AI path; the structured `Assessment` is the future
  instructor's input contract (explain/hint only).

### Test results for grading

| Suite | Coverage |
|---|---|
| `tests/unit/test_grading.py` (28) | Models, scoring, registry, per-check behavior incl. partial/fail boundaries, report |
| `tests/scenarios/test_golden_ccna_vlan_001.py` (19) | Golden 100/100 via real CLI engine; zero baseline; 13 negative mutations each failing exactly the expected ids; determinism (3× identical), ordering, state immutability |
| `tests/integration/test_assessment_flow.py` (3) | Typer CLI → session loop → `submit` renders 60/100 report; full pipeline to 100% |

## Test Results (final validation)

```text
uv run pytest   → 280 passed, 0 failed, 0 skipped, 0 errors in ~7 s
uv run ruff check . → All checks passed!
```

Breakdown: 112 original + 15 audit regressions + 28 grading units + 19 scenario
golden/negative/determinism + 3 integration + 103 others added during fixes. No coverage
tooling configured (documented above).

End-to-end manual workflow (executed during development): `netlab console --scenario
ccna-vlan-001 --device SW1` → configure VLANs/ports/trunk → `submit` → 60/100 report with
passed/failed sections → deliberately wrong configurations lower the score appropriately
(verifier runs in `tests/scenarios` now automate this for all four devices).

## Known Issues

See "Known issues / deferred" above; none affect grading correctness or the interactive lab
workflow.

## Recommended Next Step — Milestone 7

With grading proven deterministic, the repository is ready for **session persistence and the
orchestration commands** (roadmap item 8; architecture Phase 6/7 prerequisites):

1. `netlab save / resume / reset [--soft|--hard]` persisting `NetworkState` (JSON, per decision
   B12) plus the scenario id, so assessments survive restarts.
2. A top-level `netlab assess <scenario>` for grading a saved session outside the device shell,
   reusing `grade()` + `render_assessment()`.
3. Immediately after: `ping`/connectivity simulation (doc B11) so doc §25's connectivity-based
   critical errors and the scenario's reserved `verification:` checks become gradeable.
