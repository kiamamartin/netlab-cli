# Grading model

Status: **draft** — the grading engine lands after the IOS CLI and verification stages, per the
architecture's development order. Grading is **deterministic**; the AI instructor never assigns,
adjusts, or overrides scores.

## Principle

The grader never asks an LLM or inspects raw CLI text. It compares the **actual network state**
against the scenario's required state:

```text
Scenario specification → expected state
                              |
                              v
Actual NetworkState  →  validators  →  Assessment (score + reasons)
```

## Check architecture

Each requirement becomes an independently testable check:

```python
Check(
    id="vlan-10-exists",
    category="switching",
    description="VLAN 10 must exist",
    points=5,
    validator=VLANExists(device="SW1", vlan=10),
)
```

Validators read typed state (the normalized-state boundary from architecture §49), so the same
grader can later serve CML/hardware backends. Every check answers:

- What was expected?
- What actually exists?
- Did it pass?
- How many points?
- Why?

## Scoring and partial credit

The scenario author fixes category weights (doc §23); partial credit is explicit per check (§24) —
never invented at runtime. Example:

```text
VLAN 10 exists            5/5
VLAN 20 exists            5/5
VLAN names correct        4/5
Access ports correct      5/10
Trunk configured         10/10
Allowed VLANs correct     0/5
```

## Critical errors

Scenario files may flag critical failure policies (wrong default gateway, duplicate IP, missing
trunk, shutdown interface, wrong trunk mode, incorrect encapsulation) that cap a category score or
raise severity in the report (doc §25).

## Assessment model

The assessment is a **structured object** (scenario, per-category scores, passed/failed checks with
reasons, later: command history, verification results, skill tags) that the terminal presentation
renders — the presentation may evolve, the object is the contract (doc §17, §64).

## Golden configuration testing

Every scenario ships with a known-good configuration and mutation tests:

```text
golden configuration  → grader → 100/100          (hard requirement)
remove trunk          → expected trunk checks fail
wrong access VLAN     → expected access checks fail
wrong IP / subnet     → expected IP checks fail
admin-down interface  → expected up-state checks fail
```

No AI integration proceeds until golden + negative tests are reliable (hard gate from the
architecture document).
