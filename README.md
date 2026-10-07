# SROS Automation Starter

Starter repository for automating Nokia SR OS using Ansible, NETCONF, and pyATS. Contains Jinja2 NETCONF templates, playbooks, Containerlab topology integration, and automated state-drift testing.

## Directory Structure

```text
sros-automation-starter/
├── ansible/
│   ├── group_vars/
│   │   └── all.yml               # Management credentials & connection parameters
│   ├── host_vars/
│   │   └── r1.yml                # Host-specific variables (hardware, interfaces, services)
│   ├── inventory.yml             # Static inventory
│   ├── netbox_inventory.yml      # Dynamic NetBox inventory plugin config
│   ├── playbooks/
│   │   ├── deploy.yml            # Main deployment playbook
│   │   ├── ansible-netconf.yml   # Raw NETCONF execution
│   │   └── sros-ansible-get.yml  # Operational state retrieval
│   ├── templates/                # 22 Jinja2 NETCONF XML templates
│   ├── execution-environment.yml # Ansible Execution Environment (EE) definition
│   └── ansible-navigator.yml     # Containerized execution settings
├── topology.yaml                 # Containerlab topology
├── pyats_testbed.yml             # pyATS testbed definition
├── pyats_test_suite.py           # State snapshot & diff comparison script
├── requirements.txt              # Python dependencies
└── README.md
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\Activate.ps1  # Windows PowerShell

pip install -r requirements.txt
```

If using NetBox inventory:
```bash
export NETBOX_TOKEN="your_token"
```

## Running Ansible

### Full Deployment
```bash
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml
```

### Dry Run (Check Mode)
```bash
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml --check
```

### Running with ansible-navigator
```bash
ansible-navigator run ansible/playbooks/deploy.yml -i ansible/inventory.yml --mode stdout
```

### Running Specific Tasks by Tag
You do not need to supply all tags. Provide any single tag to run matching tasks:

```bash
# Run only interface tasks:
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml --tags interfaces

# Run all routing tasks (ISIS, OSPF, BGP, policy):
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml --tags routing

# Run a specific protocol:
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml --tags isis
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml --tags bgp

# Run services:
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml --tags services
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml --tags vprn
```

Available tags:
`system`, `hardware`, `filters`, `acl`, `interfaces`, `ip`, `mpls_labels`, `bier`, `isis`, `ospf`, `routing`, `policy_options`, `bgp`, `mpls`, `rsvp`, `ldp`, `sdp`, `pw_templates`, `epipe`, `vpls`, `vprn`, `ies`, `services`, `qos`, `igmp`, `pim`, `multicast`, `mirror_destination`, `mirror_source`, `telemetry`.

## Testing & Verification (pyATS)

Capture pre-change and post-change router state, then generate an HTML diff:

```bash
# 1. Capture baseline state:
python pyats_test_suite.py --testbed pyats_testbed.yml --mode pre

# 2. Deploy changes via Ansible:
ansible-playbook -i ansible/inventory.yml ansible/playbooks/deploy.yml --tags interfaces

# 3. Capture post-change state and compare:
python pyats_test_suite.py --testbed pyats_testbed.yml --mode post
```

Optional flags:
- `--paths`: Comma-separated YANG state paths for targeted capture (defaults to `/nokia-state:state`).
- `--max-workers`: Concurrent thread count for multi-router testbeds (defaults to `10`).

The report is saved to `snapshots/network_drift_report.html`.

## Configuration Order

Tasks in `deploy.yml` run in this sequence:

1. System name & hardware provisioning (card, MDA, port breakout)
2. IP filters & ACLs
3. IP interfaces (IPv4 & IPv6 dual-stack)
4. MPLS label ranges
5. BIER global configuration
6. IGP (IS-IS or OSPF)
7. Route policies & prefix lists
8. BGP
9. MPLS & RSVP-TE
10. LDP
11. SDP (Service Distribution Points)
12. Pseudowire templates
13. Layer 2 services (EPIPE, VPLS)
14. Layer 3 services (VPRN, IES)
15. QoS policies
16. Multicast (IGMP, PIM)
17. Port mirroring (destinations & sources)

## Templates Included

- `hardware_and_system_name_template.j2`
- `filter_template.j2`
- `interface_template.j2`
- `mpls_labels_template.j2`
- `bier_template.j2`
- `isis_template.j2`
- `ospf_template.j2`
- `policy_options_template.j2`
- `bgp_template.j2`
- `mpls_template.j2`
- `ldp_template.j2`
- `sdp_template.j2`
- `pw_template.j2`
- `epipe_deploy.j2`
- `vpls_deploy.j2`
- `vprn_deploy.j2`
- `ies_deploy.j2`
- `qos_template.j2`
- `igmp_template.j2`
- `pim_template.j2`
- `mirror_destination_deploy.j2`
- `mirror_source_deploy.j2`
