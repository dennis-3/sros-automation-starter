@[Omni-OS Governance](../mindset-os/AGENTS.md)

# AGENTS.md: Governance Contract for sros-automation-starter

This repository is Dennis's Nokia SR OS automation starter kit and verified deployment pipeline (`sros-automation-starter`). All AI pair programmers and automated harnesses operating in `sros-automation-starter` must strictly adhere to this operating contract.

---

## 1. Non-Negotiable Automation & Deployment Standards

1. **Carrier-Grade Verified Deployment:**
   * Configuration deployments must adhere to candidate staging, `md-compare` pre-flight checks, confirmed commit timeouts, and automated pyATS/DeepDiff state drift validation.
2. **Containerlab Topology Hygiene:**
   * Every lab topology file (`topology.yaml`) must specify explicit node definitions, interface link mappings, and valid license mounts.
3. **pyATS Automated State Validation:**
   * Run automated state validation via [`pyats_test_suite.py`](pyats_test_suite.py) before and after configuration deployments. All validation checks must exit with code `0`.
