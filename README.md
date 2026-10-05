# NetLab CLI

**NetLab CLI — Adaptive Cisco Networking Laboratory and Assessment Environment**

A terminal-first CCNA training environment that provides realistic IOS-style configuration practice,
deterministic network simulation and assessment, and (in a later phase) AI-assisted instruction based
on authoritative network state.

> **Status: Phase 0–4 — simulator, IOS CLI engine, verification, and deterministic grading are
> implemented and gated by golden/negative tests.** Session persistence and the AI instructor are
> next. See [docs/architecture.md](docs/architecture.md) for the module map,
> [docs/architecture-review.md](docs/architecture-review.md) for the Phase 0 review, and
> [docs/milestone-1-5-audit.md](docs/milestone-1-5-audit.md) for the latest engineering audit.

## How it will work

```text
Student → NetLab CLI → IOS-like shell → Network state (authoritative)
                                          ├──→ show commands (rendered from state)
                                          └──→ deterministic grader → structured assessment
                                                                     └──→ AI instructor (explain only)
```

The simulator and grading engine are deterministic. The AI instructor never scores work and never
modifies network state.

## Installation

Requires [Python 3.12+](https://www.python.org/) and [uv](https://docs.astral.sh/uv/).

```bash
git clone <repository-url> netlab-cli
cd netlab-cli
uv sync
```

## Quick start

```bash
uv run netlab version
uv run netlab list
uv run netlab start ccna-vlan-001 --device SW1
```

Inside a device session, `enable`, configure IOS-style, then `submit` (privileged EXEC) to see the
deterministic assessment.

## Running the tests

```bash
uv run pytest
uv run ruff check .
```

## Documentation

- [Architecture](docs/architecture.md)
- [Architecture review (Phase 0)](docs/architecture-review.md)
- [Development guide](docs/development.md)
- [Command reference](docs/command-reference.md)
- [Scenario authoring](docs/scenario-authoring.md)
- [Grading model](docs/grading.md)
- [Grading engine](docs/grading-engine.md)
- [Milestones 1–5 audit](docs/milestone-1-5-audit.md)

## Roadmap

1. Domain model & network state ✅
2. Scenario + topology YAML loading (`netlab list`) ✅
3. IOS CLI state machine ✅
4. Configuration commands (VLANs, switchports, IPv4) ✅
5. Verification (`show`) commands rendered from state ✅
6. Deterministic grading engine ✅
7. Golden configuration & negative tests ✅
8. Session persistence (save / resume / reset)
9. AI instructor (only after grading is proven deterministic)
10. CML / hardware execution backends

## License

[MIT](LICENSE)
