# SROS Automation Starter Project

This is a universal starter project for building Nokia SR OS (SROS) network automations. It contains all the possible variables, templates, and playbooks aggregated from the srsim project labs.

## Structure

- `ansible/` - Ansible automation framework
  - `templates/` - Jinja2 NETCONF XML templates (21 templates)
  - `group_vars/all.yml` - Global variables
  - `host_vars/` - Example host-specific variables (r1.yml)
  - `playbooks/deploy.yml` - Universal deployment playbook
  - `inventory.yml` - Sample Ansible inventory
  - `execution-environment.yml` - Ansible Execution Environment config
  - `ansible-navigator.yml` - Ansible Navigator configuration

## Usage

1. Copy this folder to start a new SROS automation project.
2. Customize the inventory.yml, host_vars, and group_vars for your topology.
3. Modify the templates as needed.
4. Run the playbook: `ansible-navigator run ansible/playbooks/deploy.yml -i ansible/inventory.yml`
5. Use the testing framework to validate configurations. This can be found in the srsim-labs repository on GitHub.

## Configuration Order

The project follows a logical Nokia SR OS configuration sequence that mirrors how network engineers typically configure routers. This ensures proper dependencies and follows best practices:

### 1. System and Hardware Configuration
- System identity and basic router setup
- Hardware provisioning (cards, ports, breakout)

### 2. Traffic Filters
- Access Control Lists (ACLs) and traffic filters
- Configured before interfaces since filters are applied to interfaces

### 3. Physical Interfaces
- IP interface configuration and addressing
- Network connectivity setup

### 4. BIER 
- Bit Index Explicit Replication (BIER) global configuration
- Must be defined before enabling BIER extensions in IGP

### 5. Interior Gateway Protocols (IGP)
- ISIS or OSPF for underlay routing
- Establishes basic reachability and BIER sub-domain participation

### 6. Multicast Routing
- PIM and IGMP for multicast routing and membership
- Configured after IGP reachability is established

### 7. Routing Policies
- Prefix lists, route policies, and community handling
- BGP policies and filtering rules

### 8. Border Gateway Protocol (BGP)
- External routing and peering configurations
- VPN and label exchange setups (including MVPN)

### 9. MPLS Infrastructure
- Label Switched Paths (LSPs) and path definitions
- RSVP and LDP for label distribution

### 10. Service Distribution Points (SDP)
- Transport tunnels for service delivery
- MPLS pseudowire infrastructure

### 11. Pseudowire Templates
- Pseudowire templates for L2 service construction
- Used in advanced VPLS configurations

### 12. Layer 2 Services
- EPIPE (Point-to-Point L2 VPN)
- VPLS (Multipoint L2 VPN)

### 13. Layer 3 Services
- VPRN (L3 VPN with VRFs) including MVPN configuration
- IES (Internet Enhanced Service)

### 14. QoS
- SAP and interface QoS: ingress/egress policies, queues, policing, and dot1p mappings
- Apply QoS profiles to SAPs/interfaces and service attachments after core services are provisioned

### 15. Mirroring and Analysis Services
- Mirror destinations for traffic analysis
- Mirror sources for traffic capture

This order ensures that foundational configurations (hardware, interfaces, routing) are completed before dependent services (MPLS, VPNs) are configured, and that QoS policies are applied before final monitoring/analysis tooling is deployed.


## Templates Included

- bgp_template.j2
- bier_template.j2
- epipe_deploy.j2
- filter_template.j2
- hardware_and_system_name_template.j2
- ies_deploy.j2
- igmp_template.j2
- interface_template.j2
- isis_template.j2
- ldp_template.j2
- mirror_destination_deploy.j2
- mirror_source_deploy.j2
- mpls_template.j2
- ospf_template.j2
- pim_template.j2
- policy_options_template.j2
- pw_template.j2
- qos_template.j2
- sdp_template.j2
- vpls_deploy.j2
- vprn_deploy.j2

## Updating the Starter

As you work on projects and add new templates or variables, update this starter project to keep it current.
