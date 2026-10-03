# Scenario authoring

Status: **implemented** — the schema lives in `netlab/scenarios/schema.py`, the loader in
`netlab/scenarios/loader.py`. Scenarios are validated fail-early: YAML parse → Pydantic schema
(`extra="forbid"` everywhere) → topology build (links checked against real interfaces) →
initial-state application. The reference scenario is
[`scenarios/vlan/ccna-vlan-001.yaml`](../scenarios/vlan/ccna-vlan-001.yaml).

## Principles

- Scenarios are **declarative YAML** — no embedded code, no hard-coded grading logic.
- **Prompt-free learning**: objectives describe outcomes ("Configure VLAN 10 for the Administration
  department"), never command transcripts.
- Invalid scenarios **fail early** with file context.
- One scenario may later produce **variants** (different VLAN numbers/networks) to defeat
  memorization.

## File shape

```yaml
id: ccna-vlan-001              # lowercase kebab-case
title: Department VLAN Deployment
version: 1
difficulty: beginner           # beginner|intermediate|advanced|exam|troubleshooting

metadata:
  estimated_minutes: 20
  skills: [vlan, access-ports, trunking]   # recognized skill tags

topology:
  devices:
    - name: R1
      type: router             # router|switch|pc|server
      interfaces:
        - name: GigabitEthernet0/0/0   # long or short form; canonicalized on load
    - name: SW1
      type: switch
      interfaces:
        - name: GigabitEthernet0/1
          description: Uplink-to-R1
    - name: PC-A
      type: pc
      interfaces:
        - name: Ethernet0

  links:
    - source: R1:Gi0/0/0       # plain "HOSTNAME:Interface" strings (either end may be abbreviated)
      destination: SW1:Gi0/1

initial_state:                 # optional pre-configuration (troubleshooting labs)
  vlans:
    - { device: SW1, vlan_id: 10, name: ADMIN }
  interfaces:
    - device: SW1
      interface: Gi0/2
      shutdown: true
      switchport: { mode: access, access_vlan: 10 }
      ip: { address: 192.168.10.5, prefix: 24 }
  hosts:
    - { device: PC-A, gateway: 192.168.10.1 }

objectives:
  - Create VLAN 10 for the Administration department.

requirements:                  # deterministic grading rules; points MUST sum to grading.total
  - id: vlan10-exists          # unique within the scenario
    category: VLAN Configuration
    description: VLAN 10 must exist on SW1
    points: 10
    type: vlan_exists
    device: SW1
    vlan_id: 10

verification: []               # reserved for connectivity checks (Phase 3 ping engine)
grading:
  total: 100
```

## Requirement types

All requirement types validate their `device`/`interface` references when the loader runs
(`validate_requirements`), so scenarios cannot ship with ungradeable rules. Interfaces are stored
canonically; subinterface names (`GigabitEthernet0/0/0.10`) are allowed to reference interfaces the
student creates during the lab.

| Type | Fields | Checks at grading time |
|---|---|---|
| `vlan_exists` | `device`, `vlan_id`, optional `name` | VLAN exists (and name matches if given) |
| `vlan_name` | `device`, `vlan_id`, `name` | VLAN name equals `name` |
| `interface_mode` | `device`, `interface`, `mode: access\|trunk` | Switchport mode |
| `access_vlan` | `device`, `interface`, `vlan_id` | Access VLAN assignment |
| `trunk_native_vlan` | `device`, `interface`, `vlan_id` | Native VLAN on a trunk |
| `trunk_allowed_vlans` | `device`, `interface`, `vlans` (list) | Allowed VLAN set contains exactly these |
| `ip_address` | `device`, `interface`, `address`, `prefix` | Interface IPv4 address/prefix |
| `host_ip` | `device`, `interface`, `address`, `prefix` | End-host interface IPv4 |
| `host_gateway` | `device`, `gateway` | End-host default gateway |
| `subinterface_encapsulation` | `device`, `interface`, `vlan_id` | 802.1Q tag on a router subinterface |

## Validation rules

- `id` matches `^[a-z0-9]+(-[a-z0-9]+)*$`; `title` non-empty; `version >= 1`.
- `difficulty` is one of the five levels (doc §20).
- Device hostnames are unique; subinterface `parent` must be declared in the same device.
- Link endpoints must be `HOSTNAME:Interface` strings naming existing interfaces (built and checked
  against real devices at load time — duplicate endpoints, unknown devices/interfaces fail here).
- Requirement `id`s are unique; `points > 0`; requirement points **must sum to `grading.total`**.
- Every field list is closed: unknown keys anywhere in the file are rejected.

## Loading scenarios programmatically

```python
from netlab.scenarios import build_initial_state, load_scenario_file

scenario = load_scenario_file("scenarios/vlan/ccna-vlan-001.yaml")
state = build_initial_state(scenario)   # NetworkState ready for a lab session
```

`discover_scenarios(directory)` returns `(loaded, errors)` so one broken file cannot hide the rest
of the library; `netlab list` uses this.
