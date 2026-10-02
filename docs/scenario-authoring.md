# Scenario authoring

Status: **draft** — the Pydantic schema and loader land with the scenario-loading stage. The shape
below reflects the architecture document (§13, §18, §40) and will be validated by
`netlab.scenarios` with fail-early diagnostics.

## Principles

- Scenarios are **declarative YAML** — no embedded code, no hard-coded grading logic.
- **Prompt-free learning**: objectives describe outcomes ("Configure VLAN 10 for the Administration
  department"), never command transcripts.
- Invalid scenarios **fail early** with file context: schema → topology → grading rules →
  automated test, before a scenario becomes available.
- One scenario may produce **variants** (different VLAN numbers/networks) to defeat memorization.

## File shape

```yaml
id: ccna-vlan-001
title: Department VLAN Deployment
version: 1

metadata:
  difficulty: beginner        # beginner|intermediate|advanced|exam|troubleshooting (levels 1–5)
  estimated_minutes: 20
  skills:
    - vlan
    - access-ports
    - trunking

topology:                     # devices, interfaces, links (see below)
  ...

initial_state:                # optional starting configuration (broken networks for
  ...                         # troubleshooting scenarios go here)

objectives:
  - Create VLAN 10 (ADMIN) and VLAN 20 (SALES)
  - Configure access ports for PC-A and PC-B
  - Configure the uplink to R1 as an 802.1Q trunk
  - Configure router-on-a-stick inter-VLAN routing

requirements:                 # deterministic grading rules
  - id: vlan10-exists
    type: vlan_exists
    device: SW1
    vlan: 10
    points: 10

verification:                 # reserved (connectivity checks arrive with the ping engine)
  - id: pca-gateway
    type: connectivity
    source: PC-A
    destination: 192.168.10.1
    expected: true
```

## Topology

Devices declare their type and interfaces; links reference canonical endpoints.

```yaml
topology:
  devices:
    - name: R1
      type: router
      interfaces: [GigabitEthernet0/0/0]
    - name: SW1
      type: switch
      interfaces: [GigabitEthernet0/1, GigabitEthernet0/2, GigabitEthernet0/3]
    - name: PC-A
      type: pc
      interfaces: [Ethernet0]
    - name: PC-B
      type: pc
      interfaces: [Ethernet0]

  links:
    - source: R1:GigabitEthernet0/0/0
      destination: SW1:GigabitEthernet0/1
    - source: SW1:GigabitEthernet0/2
      destination: PC-A:Ethernet0
    - source: SW1:GigabitEthernet0/3
      destination: PC-B:Ethernet0
```

## First milestone scenario (fixed)

```text
                R1
                 |
              trunk (802.1Q)
                 |
                SW1
               /    \
        Gi0/2 /      \ Gi0/3
             /        \
         PC-A         PC-B
        VLAN 10       VLAN 20
        192.168.10.x  192.168.20.x
```

- VLAN 10 = `ADMIN`, VLAN 20 = `SALES`
- SW1: Gi0/1 → trunk to R1, Gi0/2 → VLAN 10, Gi0/3 → VLAN 20
- R1: `GigabitEthernet0/0/0.10` = 192.168.10.1/24, `GigabitEthernet0/0/0.20` = 192.168.20.1/24
- PCs: addressing + default gateway

No DHCP, OSPF, ACLs, STP, or EtherChannel in this scenario.
