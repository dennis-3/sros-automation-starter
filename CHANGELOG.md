# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-10

### Added
- Complete suite of 23 Jinja2 NETCONF templates targeting Nokia SR OS MD-CLI / YANG.
- Decoupled `rsvp_template.j2` for independent RSVP-TE and MPLS signaling.
- Pre-flight automated CI test suite (`tests/validate_templates.py`) parsing Jinja2 AST and XML schema validity.
- Multi-threaded pyATS test framework (`pyats_test_suite.py`) supporting baseline snapshots, state drift comparison, and HTML report generation.
- Integrated Containerlab topology (`topology.yaml`) with Nokia SR OS (SR-1), TACACS+ AAA, and Telegraf/Prometheus/Grafana telemetry stack.
- Ansible Execution Environment (`execution-environment.yml`) and `ansible-navigator.yml` containerized runner settings.
- GitHub Actions CI/CD workflows: `.github/workflows/ci.yml` (validation) and `.github/workflows/deploy.yml` (automated deployments and canary rollouts).
- Open source community files: `LICENSE.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `.editorconfig`.

### Changed
- Replaced artificial Jinja2 variable wrappers with native SR OS YANG data hierarchies across all templates.
- Streamlined `topology.yaml` using native `defaults` and `kinds` to eliminate redundant node configurations.
- Standardized management IP scheme (`r2` at `.2`, `r3` at `.3`, preserving default gateway at `.1`).
- Renamed CI workflow from `lint.yml` to `ci.yml` to accurately reflect full test execution.

### Security
- Untracked proprietary Nokia TiMOS license keys from git tracking and added `license/license.txt.example` and documentation.
- Isolated candidate datastore changes with automated `<discard-changes/>` rollback in case of staging failures.
