# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-15

### Added

- Complete suite of 22 Jinja2 NETCONF templates targeting Nokia SR OS MD-CLI / YANG.
- Pre-provisioned Grafana streaming telemetry dashboard (`dashboards/sros_telemetry_dashboard.json`) and provider configuration (`grafana_dashboards.yaml`) for IOM CPU load and port throughput metrics.
- Whole-pipeline exclusive NETCONF candidate datastore locking (`sros_lock_candidate: true`) to prevent concurrent staging collisions.
- Two-tier local verification architecture: fast pre-flight check (`make validate`) and full static analysis (`make lint`).
- Synthetic Jinja2 rendering against data models with XML `ElementTree` schema validation across all 22 templates (`tests/validate_templates.py`).
- Model-driven pyATS invariant test framework (`pyats_test_suite.py`) supporting baseline snapshots, state drift comparison, and HTML report generation.
- Closed-loop deployment orchestrator with automated rollback guard (`run_verified_deployment.py`).
- Integrated Containerlab topology (`topology.yaml`) with Nokia SR OS (SR-1) and Telegraf/Prometheus/Grafana telemetry stack.
- Automated secret and credential scanning with Gitleaks in GitHub Actions CI workflow.
- Candidate datastore diff detection via Nokia native `md-compare` RPC to guarantee commit idempotency and skip no-op transactions.
- Ansible Execution Environment (`execution-environment.yml`) and `ansible-navigator.yml` containerized runner settings.
- GitHub Actions CI/CD workflows: `.github/workflows/ci.yml` (validation) and `.github/workflows/deploy.yml` (automated deployments and canary rollouts).
- Open source community files: `LICENSE.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `.editorconfig`.

### Changed

- Replaced artificial Jinja2 variable wrappers with native SR OS YANG data hierarchies across all templates.
- Streamlined `topology.yaml` using native `defaults` and `kinds` to eliminate redundant node configurations.
- Standardized management IP scheme (`r2` at `.2`, `r3` at `.3`, preserving default gateway at `.1`).
- Modernized hardware provisioning documentation in `README.md` to reflect native SR OS YANG `cards` and `ports` hierarchy.

### Security

- Untracked proprietary Nokia TiMOS license keys from git tracking and added `license/license.txt.example` and documentation.
- Isolated candidate datastore changes with automated `<discard-changes/>` rollback and confirmed commit in case of staging failures.
