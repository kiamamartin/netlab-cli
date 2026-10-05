# Command reference

Status: the IOS CLI engine (stage 3) is **implemented** for mode machinery and structural commands;
configuration and `show` commands follow in stages 4–5. Do not rely on any command marked *planned*.

## IOS-like semantics

- **Unique-prefix matching** per word: `conf t`, `conf term`, and `configure terminal` are all
  accepted; a prefix beyond the word (`conf ta`) is not. Matching is case-insensitive.
- **Ambiguity** follows real IOS: a bare `e` in EXEC modes matches both `enable` and `exit` →
  `% Ambiguous command.`
- **Mode gating**: commands are registered per configuration mode; `vlan 10` from user EXEC
  produces `% Invalid input detected at '^' marker.` with the caret under the offending token
  (brief §10).
- **Device gating**: `vlan` / `name` are registered only on switches — on a router they are simply
  not commands, so they yield invalid input rather than a runtime error.
- Input that ends mid-command (`conf` alone, `hostname` without a name) yields
  `% Incomplete command.`

## `netlab` application commands

| Command | Description | Status |
|---|---|---|
| `netlab version` | Print the NetLab CLI version | ✅ implemented |
| `netlab list` / `netlab scenarios` | List available scenarios (invalid files reported and skipped) | ✅ `netlab list` implemented |
| `netlab console --scenario <id> --device <host>` | Interactive IOS-like session on one device of a scenario | ✅ implemented |
| `netlab start <scenario>` | Load a scenario and start a managed lab session | ⏳ planned |
| `netlab devices` | List session devices; connect interactively | ⏳ planned |
| `netlab status` | Session status | ⏳ planned |
| `netlab assess` / `netlab submit` | Run deterministic grading, show assessment | ⏳ planned |
| `netlab save` / `netlab resume` | Persist / restore a session | ⏳ planned |
| `netlab reset [--soft|--hard]` | Restore initial state / delete session | ⏳ planned |
| `netlab progress` / `netlab history` | Learning analytics (SQLite phase) | ⏳ deferred |

## IOS-like device commands (milestone 1)

### EXEC (user)

| Command | Effect | Status |
|---|---|---|
| `enable` | Enter privileged EXEC | ✅ implemented |
| `disable` | Return to user EXEC | ✅ implemented |
| `exit` | Leave device session (user EXEC) | ✅ implemented |
| `help` / `?` | List commands available in the current mode | ✅ implemented |

### Privileged EXEC

| Command | Effect | Status |
|---|---|---|
| `configure terminal` | Enter global configuration | ✅ implemented |
| `exit` | Return to user EXEC | ✅ implemented |
| `show running-config` | Render configuration from live state | ✅ implemented |
| `show startup-config` | Render saved configuration snapshot | ✅ implemented |
| `show vlan brief` | VLAN table incl. port membership (trunks under native VLAN) | ✅ implemented |
| `show interfaces trunk` | Trunk ports: mode/encapsulation/status, allowed-and-active VLANs | ✅ implemented |
| `show ip interface brief` | Interface IPv4 + up/down table (derived operational state) | ✅ implemented |
| `show mac address-table` | MAC table (populated once L2 simulation exists) | ⏳ planned |
| `copy running-config startup-config` | Snapshot running state into startup-config | ✅ implemented (privileged EXEC) |
| `assess` / `submit` | Run the deterministic grader against current state and render the assessment report | ✅ implemented (privileged EXEC; requires a scenario-backed session) |

### Global configuration

| Command | Effect | Status |
|---|---|---|
| `hostname <name>` | Rename device (validated) | ✅ implemented |
| `interface <if>` | Enter interface config (accepts `gi0/1` style) | ✅ implemented |
| `vlan <id>` | Create/select VLAN → VLAN config mode (switches) | ✅ implemented |
| `exit` / `end` | Leave mode / all configuration modes | ✅ implemented |

### VLAN configuration

| Command | Effect | Status |
|---|---|---|
| `name <name>` | Set VLAN name | ✅ implemented |
| `exit` | Return to global config | ✅ implemented |

### Interface configuration

| Command | Effect | Status |
|---|---|---|
| `description <text>` | Set description (free text) | ✅ implemented |
| `shutdown` / `no shutdown` | Administrative state | ✅ implemented |
| `switchport mode access` | Access mode | ✅ implemented (switches) |
| `switchport access vlan <id>` | Assign access VLAN (auto-creates missing VLAN, decision B6; does **not** imply mode) | ✅ implemented |
| `switchport mode trunk` | Trunk mode | ✅ implemented (switches) |
| `switchport trunk allowed vlan <list>` | Replace list; IOS verbs `all`/`add`/`remove`/`except` supported | ✅ implemented |
| `switchport trunk native vlan <id>` | Set native VLAN | ✅ implemented |
| `ip address <addr> <mask>` | Configure IPv4 on L3 interface/subinterface (mask or `/prefix`) | ✅ implemented |
| `no ip address` | Clear IPv4 | ✅ implemented |
| `encapsulation dot1q <vlan>` | 802.1Q tag on router subinterfaces | ✅ implemented |
| `exit` | Return to global config | ✅ implemented |
| `shutdown` / `no shutdown` | Administrative state | ⏳ planned |
| `switchport mode access` | Access mode | ⏳ planned |
| `switchport access vlan <id>` | Assign access VLAN | ⏳ planned |
| `switchport mode trunk` | Trunk mode | ⏳ planned |
| `switchport trunk allowed vlan <list>` | Replace allowed list (`all`/`add`/`remove`/`except` verbs in IOS style) | ⏳ planned |
| `switchport trunk native vlan <id>` | Set native VLAN | ⏳ planned |
| `ip address <addr> <mask>` | Configure IPv4 on L3 interface/subinterface | ⏳ planned |
| `no ip address` | Clear IPv4 | ⏳ planned |
| `exit` | Return to global config | ⏳ planned |

Commands are only implemented when their effects are represented correctly in network state —
`show` output must always be rendered from that state, never hard-coded. `show` commands are
available in both EXEC modes; `copy running-config startup-config` requires privileged EXEC.
`show` from configuration mode requires IOS `do` syntax (not implemented) — `end` first, as on
a real device.

## End-host commands (decision B9)

PCs and servers accept a minimal, non-mode-based surface (mirroring Packet Tracer's IP
Configuration utility):

| Command | Effect | Status |
|---|---|---|
| `ip address <addr> <mask>` | Address the host's first interface | ✅ implemented |
| `no ip address` | Clear host addressing | ✅ implemented |
| `ip default-gateway <addr>` | Set the default gateway | ✅ implemented |

## IOS-style error behaviour (educational consistency)

```text
% Invalid input detected at '^' marker.
% Incomplete command.
% Ambiguous command.
% Invalid interface type and number.
```

Exact IOS fidelity is not the goal; consistent, explainable behavior is.
