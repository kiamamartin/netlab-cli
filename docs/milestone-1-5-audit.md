# Milestones 1–5 Engineering Audit

> Date: 2026-10-05 · Branch: `main` · Commit: `83d8d2b` (plus unstaged WIP on 4 files)
>
> Source of truth: `netlab_cli_architecture.md` (§21–26 grading, §55 commands, §74 first
> milestone) and `docs/architecture.md`. The architecture file itself lives outside the
> repository (`~/Downloads/netlab_cli_architecture.md`); the in-repo docs mirror it.

## 1. Repository state

| Item | Value |
|---|---|
| Branch / commit | `main` @ `83d8d2b` ("feat(verification): render show commands from state; add copy run start") |
| Uncommitted changes | `src/netlab/cli/app.py`, `src/netlab/ios/{machine,parser,commands}.py` (+600/−36) |
| Python | 3.12.13 (project venv, pinned by `.python-version`); system 3.14.6 |
| uv | 0.11.19 |
| Dependencies | typer ≥0.12, rich ≥13.7, pydantic ≥2.7, pyyaml ≥6.0; dev: pytest, ruff (no coverage tooling configured) |
| Package layout | `src/netlab/{core,devices,network,scenarios,ios,verification,cli}` — matches the architecture module map (grading/persistence/telemetry deliberately absent, "no placeholder modules") |
| Scenarios | 1 (`scenarios/vlan/ccna-vlan-001.yaml`, 15 requirements, 100 points) |
| Tests | 15 files, 112 tests (11 parser, 5 CLI, 18 config, 16 show, rest domain) |

**Milestone mapping** (the task brief's M1–M5 correspond to the architecture's Phases 0–5 /
CHANGELOG stages): M1 domain model, M2 scenario loading, M3 IOS CLI engine, M4 configuration
commands, M5 verification (`show`) layer.

## 2. Baseline (measured before any fix)

```text
uv run pytest            → 111 passed, 1 failed, then HANGS (killed at 120 s / 300 s)
uv run ruff check .      → 8 errors (5×E501 app.py, I001+F823 machine.py, 1 fixable)
```

CLI smoke test (`printf 'enable\nshow vlan brief\nexit\n' | netlab console …`) reaches a
correct-looking banner and `show vlan brief` output, then **loops `SW1>` forever** instead of
exiting — EOF is not honored.

### Baseline performance (measured, Python 3.12.13)

| Operation | Result |
|---|---|
| `load_scenario_file` (YAML + Pydantic validation) | 101.05 ms (one-time import/validation cost) |
| `build_initial_state` | 2.58 ms |
| `CliSession` construction | 0.58 ms |
| 23 config + show commands through `session.execute` | 10.23 ms (~0.4 ms/command) |
| `show vlan brief` (n=200) | 0.353 ms/call |
| `CommandRegistry.resolve` — `show vlan brief` (n=2000) | 0.196 ms/call |
| `CommandRegistry.resolve` — `switchport trunk allowed vlan 10,20` (n=2000) | 0.347 ms/call |

Command parsing is dominated by linear scans over per-mode spec lists (52 specs / 5 modes),
running the argument validator twice per resolve. Absolute numbers are fine for an interactive
CLI (sub-millisecond per command); no optimization is warranted for its own sake.

## 3. Milestone status

| Milestone | Expected functionality | Implemented | Tested | Issues | Status |
|---|---|---|---|---|---|
| M1 | Domain model: devices, interfaces (canonical names, VLAN state, IPv4, subinterfaces), VLAN database, links, validated topology, authoritative `NetworkState`, run/startup snapshots | Yes | Yes | Startup snapshot stores `created_at` (unused by renderers; harmless) | **PASS** |
| M2 | Scenario YAML schema, fail-early loader, topology builder, `initial_state`, requirement validation, `netlab list` | Yes | Yes | None blocking | **PASS** |
| M3 | Mode state machine, prefix parser, IOS-style errors, session loop, hostname/interface/vlan/name | Yes | Yes | `run_console` swallows `SessionExit`; pipe path never terminates at EOF; debug prints to stderr; CLI script loops forever on EOF (see §4) | **FAIL** |
| M4 | VLAN/switchport/IP/encapsulation/shutdown/description config commands mutating state; host addressing surface | Yes | Yes | History unbounded (constant claims 100, none enforced) | **PASS** (1 defect) |
| M5 | `show vlan brief`, `show interfaces trunk`, `show ip interface brief`, running/startup config rendered from state; `copy run start` | Yes | Yes | `show ip interface brief` columns mis-align for long interface names (see §4) | **FAIL** (cosmetic) |

Nothing is marked PASS merely because files exist: each row above reflects the executed test
suite and manual CLI runs.

## 4. Defects found (audit evidence)

| # | Defect | Evidence | Severity |
|---|---|---|---|
| D1 | **Pipe EOF never terminates the session.** `_read_pipe_line` returns `""` at EOF, but `run_console` neither breaks nor executes it — the loop re-prompts forever. The committed code raised `StopIteration` → the regression was introduced by uncommitted WIP. | Manual CLI run loops `SW1>` forever; `test_console_ends_on_input_exhaustion` only passes because of an unrelated behavior change | High (CLI unusable non-interactively) |
| D2 | **`run_console` swallows `SessionExit`.** WIP added `except SessionExit: return`, breaking the documented contract ("ends on `exit` in user EXEC, raising `SessionExit`") and failing `test_console_runs_doc_example_and_exits`. | Baseline pytest failure | High |
| D3 | **Left-over debug output.** `machine.py` `_read_line` prints `DEBUG: ch=...` / `DEBUG: result=...` to stderr on every interactive keystroke; `run_console` writes into `session._output_buffer` which nothing consumes. | ruff F823 (`sys` shadowed by `import sys` inside the loop); code inspection | High (must not ship) |
| D4 | **IOS ambiguity contract dropped.** WIP narrowed `enable`/`exit` prefixes to `en`/`ex`, so bare `e` resolves as "unrecognized" instead of `% Ambiguous command.`; a dedup hack in `CommandRegistry.resolve` masks double registration instead of preventing it. | `docs/command-reference.md` documents the `e` ambiguity; `test_parser.py::test_ambiguous` pins it | Medium |
| D5 | **Command history unbounded.** `HISTORY_BUFFER_SIZE = 100` is declared but never enforced; `session.history` grows without limit (223 entries in a 25-command smoke run incl. pipe EOF duplicates). | Measured in baseline perf run | Medium |
| D6 | **`show ip interface brief` mis-aligns with long names.** `short_name:<21>` is shorter than `TenGigabitEthernet0/0/0.255` (24 chars) and the row hard-codes 21; header dashes are fixed width. | Code inspection (`verification/show_ip.py`) | Low (cosmetic) |
| D7 | **Lint debt:** 5×E501 + 1 long option-help string in `app.py`, unsorted imports in `machine.py`. | `uv run ruff check` | Low |
| D8 | `_TerminalEditor` uses `self.stdin.write` for a lone ESC (input object, not an output stream). | Code inspection | Low (unreachable in practice; fixed with D3 cleanup) |

## 5. TUI rendering / layout audit

NetLab's "TUI" is deliberately a line-oriented IOS console (doc §57 places multi-pane TUI after
the basic CLI). Audit surface = prompts, tables, error renderers, width behavior.

Findings:

- Prompts (`SW1>`, `SW1(config-if)#`) are mode-derived with no width assumptions. ✔
- `show vlan brief` pads the name column to 32; IOS VLAN names are ≤32 chars (schema-enforced),
  so the table cannot overflow. ✔
- `show interfaces trunk` / `show ip interface brief` use fixed columns sized for canonical
  abbreviations; long names (e.g. `TenGigabitEthernet…` subinterfaces) can shift columns —
  D6. Renderers must never assume a terminal width (no Rich width dependence) since output is
  also consumed by pipes and graders. ✔ (after D6)
- No full-screen redraw, no stale regions, no nested panels exist; resize cannot corrupt state
  because nothing caches rendered output. The interactive editor (WIP) writes only to stdout
  and restores termios on exit. ✔
- Terminal sizes 80×24 → 160×50: line-oriented output wraps naturally; no crash vectors found
  (verified by smoke runs; no snapshot framework is in use, so none to regenerate).

## 6. Network-state integrity (grading readiness)

- One authoritative aggregate: `NetworkState → Topology → Device → Interface/VLAN`. Config
  commands mutate only domain models; show renderers and (future) graders read the same state.
  No duplicated state, no TUI coupling, no scenario-specific logic in the engine. ✔
- Operational state is derived (`Topology.interface_is_up`), never stored — cannot diverge. ✔
- `copy run start` snapshots via frozen `Config`; isolation tests exist. ✔
- Gaps relevant to Milestone 6: the grading engine must consume `NetworkState` directly (never
  rendered text), and the CLI needs a session-level `assess` command. Neither exists yet. ✔ (planned)

## 7. Stabilization plan (pre-M6)

1. Fix D1/D2/D3 by restoring the pre-WIP console contract (raise `SessionExit`, break at EOF,
   remove debug prints and dead buffers) — keep the useful parts of the WIP (raw-mode editor
   plumbing, `lines=None` default) where they don't change the contract.
2. Fix D4: restore `e`-prefix ambiguity, move show specs to be registered once per EXEC mode,
   and make double registration an explicit error (`register` rejects duplicate name+mode)
   so the dedup hack is unnecessary.
3. Fix D5: enforce `HISTORY_BUFFER_SIZE` as a deque-style bound.
4. Fix D6: size `show ip interface brief` columns from the longest interface name.
5. Regression tests for each fix (EOF termination, session exit, ambiguity, history bound,
   long-name alignment).
6. Re-run pytest + ruff to green, then implement Milestone 6 on top.

## 8. Deferred items (explicit, not hidden)

| Issue | Impact | Reason deferred | Risk to M6 | Recommended future fix |
|---|---|---|---|---|
| `Config.created_at` timestamp never used | None | Snapshot equality tests intentionally ignore it (determinism) | None | Use when `reload`/analytics land (Phase 7) |
| Parser runs argument validators twice per resolve | ~0.1–0.2 ms/command overhead | Sub-millisecond; interactive workload | None | Single validation pass if a benchmark ever shows it matters |
| No coverage tooling configured | Unknown % coverage | Task instructions: do not add tooling without architectural reason; test files cover every module | None | Add `pytest-cov` with the CI phase |
| Interactive raw-mode editor is minimally tested (TTY path untestable in CI) | Arrow keys only | TTY-only code path; pipe path fully tested | None | pty-based tests with a terminal-testing phase |
| `netlab save/resume/reset/status` (doc §55) | Session persistence missing | Milestone 8 per roadmap; not required for M6 | None — `assess` grades in-memory state | Milestone 8 |
