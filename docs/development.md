# Development guide

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (project and dependency management — do not use `pip install`)

## Setup

```bash
uv sync            # create .venv, install project + dev dependencies, write uv.lock
uv run netlab version
```

## Testing

```bash
uv run pytest              # all tests
uv run pytest tests/unit   # domain model tests
```

Test layout (directories appear as they gain content):

```text
tests/
├── unit/          # domain models, parsers, validators — no I/O
├── integration/   # CLI → parser → state → verification → grading
├── scenarios/     # scenario loading, golden configurations, negative mutations
└── regression/    # fixed regressions
```

Every feature lands with tests. Scenario grading is only trusted once the golden configuration
scores 100/100 and each negative mutation fails its expected checks.

## Linting / formatting

```bash
uv run ruff check .
```

Type checking (mypy/pyright) is adopted before the IOS CLI milestone — see the quality gates in
`docs/architecture-review.md`.

## Conventions

- **Domain-first.** The CLI and application services depend on the domain; the domain depends on
  nothing UI-related. No arbitrary dictionaries crossing module boundaries — explicit Pydantic models.
- **Single source of state.** All state lives in `NetworkState`. `show` output is rendered from it;
  graders read it. Never hard-code command output.
- **Type hints everywhere**, small functions, descriptive exceptions (`netlab.core.errors`),
  no global mutable state, no circular imports.
- **Deterministic first.** No LLM involvement in simulation or grading. AI consumes structured
  evidence only (Phase 6).
- **No placeholder modules.** Packages are created when they gain responsibility.

## Git discipline

Logical commits with conventional scopes, e.g.:

```text
feat(core): add network state models
feat(ios): implement CLI mode state machine
feat(grading): add deterministic validators
test(scenarios): add VLAN lab
```

Never commit: `.env`, credentials, API keys, CML passwords, SSH keys, local databases, venvs
(enforced by `.gitignore`).

## Workflow per feature

1. Understand the requirement and the architecture.
2. Plan the smallest coherent change.
3. Implement + write tests.
4. Run `uv run pytest && uv run ruff check .`.
5. Review the implementation.
6. Update the relevant `docs/` page.
