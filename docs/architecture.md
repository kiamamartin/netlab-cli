# Architecture

NetLab CLI is a terminal-first CCNA laboratory. The architecture separates three concerns that must
never be conflated:

1. **Deterministic simulation** — an authoritative network state mutated only by parsed IOS-style
   commands.
2. **Deterministic grading** — validators that compare state against scenario requirements.
3. **AI instruction** — an explanation layer that consumes structured assessment evidence and never
   decides correctness or mutates state.

## Authoritative chain

```text
Student commands
      ↓
CLI parser
      ↓
Network state mutation
      ↓
Network state            (single source of truth)
      ↓
Verification  (show commands rendered from state)
      ↓
Deterministic grading
      ↓
Structured assessment
      ↓
AI explanation
```

## Layering

```text
CLI (Typer/Rich)
 |
 v
Application services (sessions, orchestration)
 |
 v
IOS CLI engine (mode state machine, parser, command registry)
 |
 v
Domain model (devices, network)  ←  NetworkState (authoritative aggregate)
 |
 v
Verification engine ── Grading engine ── AI instructor
```

Domain objects never depend on the terminal UI; the CLI depends on the domain, never the reverse.

## Module map

| Module | Responsibility | Status |
|---|---|---|
| `netlab.core` | Shared primitives: configuration modes, `Command`, error taxonomy | ✅ implemented |
| `netlab.devices` | `Device`, `Switch`, `Router`, `Host`, `Interface`, `VLAN`, configuration snapshots | ✅ implemented |
| `netlab.network` | `Endpoint`, `Link`, `Topology` (validated), `NetworkState` aggregate | ✅ implemented |
| `netlab.cli` | Typer application, `netlab` console script | ✅ minimal (`version`); sessions/scenarios pending |
| `netlab.ios` | Mode state machine, parser, command registry, IOS-style errors, show renderers | ⏳ planned |
| `netlab.scenarios` | Scenario schema, YAML loader, fail-early validation | ⏳ planned |
| `netlab.grading` | `Check`/validator architecture, `Assessment`, scoring | ⏳ planned |
| `netlab.verification` | Verification commands generated from state | ⏳ planned |
| `netlab.persistence` | Session save/resume (JSON), later SQLite records | ⏳ planned |
| `netlab.telemetry` | Command history, skill mastery (Phase 5+) | ⏳ deferred |
| `netlab.instructor` | AI instructor consuming structured assessment evidence (Phase 6) | ⏳ deferred |
| `netlab.cml` | CML/hardware execution backends (Phase 9+) | ⏳ deferred |

Modules are created only when they have a defined responsibility (no placeholder packages).

## Domain concepts

- **Device** — hostname (IOS rules), device type, interfaces, startup-config snapshot. Subclasses:
  `Switch` (VLAN database), `Router`, `Host` (PC/Server, default gateway).
- **Interface** — canonical long name (`GigabitEthernet0/1`, `GigabitEthernet0/0/0.10`, `Vlan10`),
  description, administrative state, switchport mode/access/native/allowed VLANs, IPv4
  address/prefix, and for subinterfaces: parent interface + 802.1Q encapsulation VLAN.
- **VLAN** — id (1–4094), name, status. Every switch starts with VLAN 1 (`default`).
- **Link** — two endpoints (device + canonical interface) and link status. Topology validation
  guarantees endpoints exist and each interface belongs to at most one link.
- **Topology** — devices + links; computes derived operational state
  (`interface_is_up = admin up ∧ link up ∧ connected`).
- **NetworkState** — the authoritative aggregate handed to verification, grading, and (later)
  the AI instructor.
- **Config** — frozen snapshot of a device's configuration; startup-config is one of these, captured
  by `copy running-config startup-config` (a future `reload` restores it).

## Running vs startup configuration

The network state is authoritative. `show running-config` is *rendered* from state (renderer arrives
with the IOS module). `startup-config` is a frozen snapshot of that state; mutation of the device
after `copy running-config startup-config` cannot affect the snapshot.

## Future backends

The grading engine consumes normalized state, not raw CLI text (doc §49). The simulator produces
`NetworkState` directly; CML and SSH/Netmiko hardware backends will parse device output into the same
normalized state, so one grader serves all execution backends.
