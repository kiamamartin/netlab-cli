# Grading engine

Status: **implemented** (milestone 6). Deterministic: the same scenario plus
the same network state always produces the same assessment. The AI instructor
never assigns, adjusts, or overrides scores — it does not exist in the grading
path at all.

## Principle

The grader never asks an LLM or inspects rendered CLI text. It compares the
**authoritative network state** against the scenario's declarative
requirements:

```text
Scenario YAML (requirements, points sum to grading.total)
        ↓
netlab.grading.registry   (requirement type → validator, resolved once)
        ↓
netlab.grading.checks     (validators read NetworkState only)
        ↓
netlab.grading.engine     grade(scenario, state) → Assessment
        ↓
netlab.grading.reports    render_assessment(assessment) → terminal text
```

## Module map

| Module | Responsibility |
|---|---|
| `netlab.grading.models` | `CheckStatus`, `RequirementResult`, `CategoryScore`, `Assessment` — frozen Pydantic models, the machine-readable contract |
| `netlab.grading.checks` | One deterministic validator per requirement type; returns `CheckOutcome` (expected / actual / earned fraction / detail) |
| `netlab.grading.registry` | Maps each scenario requirement `type` to its validator; rejects unknown types with `GradingError` |
| `netlab.grading.scoring` | Earned-point calculation, status mapping, category rollup |
| `netlab.grading.engine` | `grade(scenario, state)` — resolve → evaluate → aggregate, single pass |
| `netlab.grading.reports` | Terminal rendering of a structured `Assessment`; calculates nothing |

## Requirement model

Requirements are pure data in scenario YAML (see
[scenario authoring](scenario-authoring.md) for all ten types and their
fields). The schema enforces `points > 0`, unique ids, and that requirement
points **sum to `grading.total`** — the weighting is entirely the scenario
author's decision; no Python-side scoring logic is scenario-specific.

## Check model

Each validator is a pure function over the authoritative state. Every outcome
answers the five grading questions: what was expected, what actually exists,
did it pass, how many points, and why (a `detail` string with the fixing
command, e.g. *"configure trunking first: switchport mode trunk"*).

Validators never read rendered `show` output, never parse text, and never call
an LLM. A missing device/interface or unregistered requirement type raises
`GradingError` rather than silently passing or failing.

## Scoring

`earned_points = floor(requirement.points × outcome.earned_fraction)` with
`fraction ∈ {0.0, (0,1), 1.0}`. Whole points per requirement; scenarios that
want finer partial credit split requirements. The result status maps directly:
`1.0 → PASS`, `(0,1) → PARTIAL`, `0.0 → FAIL`.

## Partial credit (explicit, doc §24)

Partial fractions are chosen by the check definition, never invented at
runtime:

| Situation | Fraction |
|---|---|
| Trunk expected, port is `access` (right command family, wrong mode) | 0.5 |
| Access VLAN assigned but `switchport mode access` never issued | 0.5 |
| Trunk up, required VLANs flow, allowed list never pruned (default 1–4094) | 0.5 |
| Correct subnet, wrong host address | 0.5 |
| Interface configured but `shutdown` (doc §25 critical error) | 0.5 on the IP requirement |

Deliberate non-credit rules (regression-tested):

- A port still in default `NONE` mode earns **nothing** on trunk/native/allowed
  checks — absence of configuration is not progress.
- Default state values never earn points: an unconfigured scenario scores 0,
  even where a default (native VLAN 1, all-VLAN allowed list) coincides with a
  requirement.

## Deterministic guarantees

- Results keep scenario requirement order; no dict-iteration dependence.
- No timestamps, randomness, or environment values in the assessment; equal
  assessments compare equal (`Assessment` is a frozen Pydantic model).
- `grade()` never mutates state (asserted by test).
- One evaluation pass per assessment: no repeated YAML parsing, no repeated
  topology reconstruction, no terminal rendering involved in scoring.
- Determinism test: the same state graded three times yields identical
  objects, summaries, ordering, and earned points.

## Golden and negative tests

`tests/scenarios/test_golden_ccna_vlan_001.py` is the hard gate (doc §52):

- **Golden** — the doc §74 router-on-a-stick configuration, executed through
  the real CLI engine, scores **100/100** with zero failed/partial checks.
- **Zero baseline** — a fresh scenario start scores exactly 0.
- **Negative mutations** — each deliberate defect (missing VLAN, wrong name,
  wrong access VLAN, trunk as access, wrong native VLAN, missing subinterface,
  wrong encapsulation, wrong IP, wrong mask, shutdown, wrong host IP/gateway)
  fails **exactly** the expected requirement ids and leaves unrelated
  requirements green.

## Report generation

`render_assessment(assessment)` renders the structured object (score,
PASSED/PARTIAL/FAILED groups, per-category rollup, recommended review list).
It performs no arithmetic — the label column is sized from the report's own
content and the renderer has no terminal-width assumptions, so the same report
works interactively, piped, and in tests.

In-session access: `assess` or `submit` in privileged EXEC (doc §55/§27). The
session delegates to the engine and renders the result; it owns no scoring
logic.

## AI integration boundary (future)

The assessment object is the contract. A future instructor may receive:
scenario, assessment, failed/partial checks, command history, verification
output — and may explain, hint, and remediate. It may never modify network
state, change scores, override checks, or silently repair configuration.
