# SROS Automation Starter

Starter repository for automating Nokia SR OS using Ansible, NETCONF, and pyATS. Contains Jinja2 NETCONF templates, playbooks, Containerlab topology integration, and automated state-drift testing.

## Directory Structure

```text
sros-automation-starter/
├── ansible.cfg                   # Tuned Ansible settings (forks, timeouts, default inventory)
├── ansible/
│   ├── group_vars/
│   │   ├── all.yml               # Global credentials, connection parameters, commit toggles
│   │   ├── edge_pe.yml           # Edge PE role settings
│   │   └── core_p.yml            # Core P role settings
│   ├── host_vars/
│   │   └── r1.yml                # Host-specific variables (hardware, dual-stack IPs, services)
│   ├── inventory.yml             # Role-based inventory (edge_pe, core_p, access_ce)
│   ├── netbox_inventory.yml      # Dynamic NetBox inventory plugin config
│   ├── playbooks/
│   │   ├── deploy.yml            # Main deployment playbook (atomic candidate staging)
│   │   ├── ansible-netconf.yml   # Raw NETCONF execution
│   │   └── sros-ansible-get.yml  # Operational state retrieval
│   ├── templates/                # 22 Jinja2 NETCONF XML templates
│   ├── execution-environment.yml # Ansible Execution Environment (EE) definition
│   └── ansible-navigator.yml     # Containerized execution settings
├── topology.yaml                 # Containerlab topology
├── pyats_testbed.yml             # pyATS testbed definition
├── pyats_test_suite.py           # Multi-threaded snapshot & diff test suite
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

### Environment Variables & Credentials

Credentials can be injected dynamically via environment variables for Ansible and pyATS (falling back to lab defaults if unset):

```bash
export SROS_USER="admin"
export SROS_PASSWORD="your_secure_password"
```

If using NetBox dynamic inventory:
```bash
export NETBOX_URL="http://172.17.0.1:8000"  # NetBox API endpoint
export NETBOX_TOKEN="your_token"           # NetBox REST API token
```

## Running Ansible

The repository includes a root `ansible.cfg` configured with `forks = 20`, persistent connection timeouts (`connect_timeout = 60s`, `command_timeout = 180s`), and default inventory pointing to `ansible/inventory.yml`.

### Full Deployment
```bash
ansible-playbook ansible/playbooks/deploy.yml
```

### Dry Run (Check Mode)
```bash
ansible-playbook ansible/playbooks/deploy.yml --check
```

### Target Specific Device Roles
The inventory defines role groups (`edge_pe`, `core_p`, `access_ce`). Use `--limit` to target specific tiers:
```bash
ansible-playbook ansible/playbooks/deploy.yml --limit edge_pe
ansible-playbook ansible/playbooks/deploy.yml --limit core_p
```

### Running with Containerized Execution Environment (EE)
To build the execution environment image locally using `ansible-builder`:
```bash
ansible-builder build -f ansible/execution-environment.yml -t sros-ee:latest
```

Run playbooks via `ansible-navigator` (auto-detects Podman or Docker):
```bash
ansible-navigator run ansible/playbooks/deploy.yml --mode stdout
```

### Running Specific Tasks by Tag
You do not need to supply all tags. Provide any single tag to run matching tasks:

```bash
# Run only interface tasks:
ansible-playbook ansible/playbooks/deploy.yml --tags interfaces

# Run all routing tasks (ISIS, OSPF, BGP, policy):
ansible-playbook ansible/playbooks/deploy.yml --tags routing

# Run a specific protocol:
ansible-playbook ansible/playbooks/deploy.yml --tags isis
ansible-playbook ansible/playbooks/deploy.yml --tags bgp

# Run services:
ansible-playbook ansible/playbooks/deploy.yml --tags services
ansible-playbook ansible/playbooks/deploy.yml --tags vprn
```

Available tags:
`system`, `hardware`, `filters`, `acl`, `interfaces`, `ip`, `mpls_labels`, `bier`, `isis`, `ospf`, `routing`, `policy_options`, `bgp`, `mpls`, `rsvp`, `ldp`, `sdp`, `pw_templates`, `epipe`, `vpls`, `vprn`, `ies`, `services`, `qos`, `igmp`, `pim`, `multicast`, `mirror_destination`, `mirror_source`, `telemetry`.

### Candidate Datastore & Atomic Commits
By default, `deploy.yml` operates in atomic mode:
- All configuration tasks stage changes to the Nokia SR OS candidate datastore without committing (`commit: false`).
- A final commit task tagged `always` locks the candidate store, verifies, and executes a single atomic commit.
- If any staging task or validation fails, an automated `rescue` block triggers `<discard-changes/>` via NETCONF RPC, rolling back the candidate buffer to prevent dirty state accumulation.
- If task-by-task committing is required for troubleshooting, pass `-e sros_commit_per_task=true`:
```bash
ansible-playbook ansible/playbooks/deploy.yml --tags interfaces -e sros_commit_per_task=true
```

## Hardware Provisioning

Hardware definitions in `host_vars/<hostname>.yml` support both modular multi-card chassis and flat single-card syntax.

Modular multi-card format:
```yaml
hardware:
  chassis_class: router
  chassis_type: 7750 SR-1
  cards:
    - slot: 1
      type: iom-1
      mdas:
        - slot: 1
          type: me6-100gb-qsfp28
```

Flat legacy format (also supported):
```yaml
chassis_class: router
chassis_type: 7750 SR-1
card_slot: 1
card_type: iom-1
mda_slot: 1
mda_type: me6-100gb-qsfp28
```

### Resource Decommissioning (Preventing Config Drift)

To remove interfaces, filters, or services without leaving orphaned configurations on the router, set `state: absent`:

```yaml
interfaces:
  - name: "toR2"
    state: absent

epipe_services:
  - id: "epipe-100"
    state: absent
```

Templates render NETCONF `operation="remove"` to delete the target element from the Nokia SR OS datastore during the atomic transaction.

## Testing & Verification (pyATS)

Capture pre-change and post-change router state, then generate an HTML diff:

```bash
# 1. Capture baseline state:
python pyats_test_suite.py --testbed pyats_testbed.yml --mode pre

# 2. Deploy changes via Ansible:
ansible-playbook ansible/playbooks/deploy.yml --tags interfaces

# 3. Capture post-change state and compare:
python pyats_test_suite.py --testbed pyats_testbed.yml --mode post
```

Optional flags:
- `--paths`: Comma-separated YANG state paths for targeted operational state capture (defaults to `/nokia-state:state`). Example: `--paths "/nokia-state:state/router[router-name='Base']/interface,/nokia-state:state/service"`.
- `--max-workers`: Number of concurrent worker threads for parallel multi-router state collection (defaults to `10`).
- `--testbed`: Testbed YAML path (auto-detects `pyats_testbed.yml` or `testbed.yml`).

The drift report is saved to `snapshots/network_drift_report.html`.

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
18. Atomic candidate commit (`tags: [always]`)

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
