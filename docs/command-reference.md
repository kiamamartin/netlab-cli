# Command reference

Status: **specification** for the first milestone. The IOS CLI engine lands in an upcoming stage;
commands below are marked accordingly. Do not rely on any command marked *planned*.

## `netlab` application commands

| Command | Description | Status |
|---|---|---|
| `netlab version` | Print the NetLab CLI version | ✅ implemented |
| `netlab list` / `netlab scenarios` | List available scenarios (invalid files reported and skipped) | ✅ `netlab list` implemented |
| `netlab start <scenario>` | Load a scenario and start a lab session | ⏳ planned |
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
| `enable` | Enter privileged EXEC | ⏳ planned |
| `disable` | Return to user EXEC | ⏳ planned |
| `exit` | Leave device session | ⏳ planned |

### Privileged EXEC

| Command | Effect | Status |
|---|---|---|
| `configure terminal` | Enter global configuration | ⏳ planned |
| `show running-config` | Render configuration from live state | ⏳ planned |
| `show startup-config` | Render saved configuration snapshot | ⏳ planned |
| `show vlan brief` | VLAN table incl. access-port membership | ⏳ planned |
| `show interfaces trunk` | Trunk ports: native/allowed/active VLANs | ⏳ planned |
| `show ip interface brief` | Interface IPv4 + up/down table | ⏳ planned |
| `show mac address-table` | MAC table (populated once L2 simulation exists) | ⏳ planned |
| `copy running-config startup-config` | Snapshot running state into startup-config | ⏳ planned |

### Global configuration

| Command | Effect | Status |
|---|---|---|
| `hostname <name>` | Rename device (validated) | ⏳ planned |
| `interface <if>` | Enter interface config (accepts `gi0/1` style) | ⏳ planned |
| `vlan <id>` | Create/select VLAN → VLAN config mode | ⏳ planned |
| `exit` / `end` | Leave mode / all configuration modes | ⏳ planned |

### VLAN configuration

| Command | Effect | Status |
|---|---|---|
| `name <name>` | Set VLAN name | ⏳ planned |
| `exit` | Return to global config | ⏳ planned |

### Interface configuration

| Command | Effect | Status |
|---|---|---|
| `description <text>` | Set description | ⏳ planned |
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
`show` output must always be rendered from that state, never hard-coded.

## IOS-style error behaviour (educational consistency)

```text
% Invalid input detected at '^' marker.
% Incomplete command.
% Ambiguous command.
% Invalid interface type and number.
```

Exact IOS fidelity is not the goal; consistent, explainable behavior is.
