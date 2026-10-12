# Contributing to sros-automation-starter

Thank you for your interest in contributing to **sros-automation-starter**! We welcome community contributions to improve Nokia SR OS automation, templates, testing pipelines, and documentation.

---

## Code of Conduct

This project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

---

## Getting Started

1. **Fork and Clone**:

   ```bash
   git clone https://github.com/dennis-3/sros-automation-starter.git
   cd sros-automation-starter
   ```

2. **Environment Setup**:

   - **Linux / macOS**:

     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     make install
     ```

   - **Windows (PowerShell)**:

     ```powershell
     python -m venv .venv
     .venv\Scripts\Activate.ps1
     pip install -r requirements.txt
     ansible-galaxy collection install -r ansible/requirements.yml
     pre-commit install
     ```

3. **License File & Environment Variables**:

   - Copy your Nokia SR OS simulation license:

     ```bash
     cp /path/to/your/timos_license.txt license/license.txt
     ```

   - Initialize environment variables:

     ```bash
     cp .env.example .env
     ```

   > See [`license/README.md`](license/README.md) for license details.

---

## Development Workflow

### Task Runner Commands

We use `make` for common workflow operations:

| Command | Action |
| :--- | :--- |
| `make install` | Install Python dependencies, Ansible collections & pre-commit hooks |
| `make lab-up` | Launch Containerlab topology |
| `make check` | Run Ansible in dry-run mode with candidate diffs |
| `make deploy` | Deploy configuration across routers |
| `make canary-deploy` | Deploy in rolling batches of 1 router |
| `make snapshot` | Capture operational state snapshot in JSON |
| `make test-pre` | Capture pyATS baseline state |
| `make test-post` | Capture pyATS post-change state and generate HTML drift report |
| `make build-ee` | Build containerized Execution Environment (`sros-ee:latest`) |
| `make validate` | Fast pre-flight check: Jinja2 AST, XML schema rendering, Python & YAML syntax |
| `make lint` | Full static analysis: validate, pre-commit hooks (Ruff, YAML, Markdown), and ansible-lint |
| `make lab-down` | Destroy Containerlab topology |
| `make clean` | Clean temporary artifacts and test caches |

---

## Contribution Guidelines

### 1. Architectural Principles

- **Native YANG Data Models**: Variables in `host_vars/`, `group_vars/`, and `examples/data_models/` must reflect native Nokia SR OS YANG hierarchies. Avoid artificial nesting or custom abstractions.
- **Atomic NETCONF Transactions**: Tasks in `deploy.yml` should stage candidate XML without committing until task `09_commit.yml` executes the atomic commit.
- **Idempotency & Safe Rollbacks**: All tasks must be safe to re-run. In case of staging failures, the candidate store must be cleanly discarded via `<discard-changes/>`.
- **Line Length**: Keep line lengths reasonable (recommended $\le 160$ characters).

### 2. Pre-Flight CI & Quality Assurance

All pull requests must pass our automated CI pipeline before merging. We provide a two-tier verification workflow to catch issues locally before pushing:

#### Tier 1: Fast Pre-Flight Validation (`make validate`)

Executes [`tests/validate_templates.py`](tests/validate_templates.py) in $< 2$ seconds on any operating system:

- **Jinja2 AST & Delimiter Balance**: Parses template syntax trees and ensures all statements (`{% if %}`, `{% for %}`) and delimiters (`{{ }}`, `{# #}`) are balanced.
- **Synthetic XML Rendering & Parsing**: Dynamically renders all 23 Jinja2 templates using test context from [`examples/data_models/`](examples/data_models/) and [`ansible/host_vars/`](ansible/host_vars/), parsing the resulting XML with `xml.etree.ElementTree` to verify schema validity.
- **Syntax Verification**: Validates Python compilation across all `.py` files and YAML document structure across playbooks, inventories, and topologies.

```bash
make validate
```

#### Tier 2: Full Static Analysis (`make lint`)

Runs the full local linting pipeline prior to submitting a PR:

- Runs `make validate` first.
- Executes all pre-commit hooks (`pre-commit run --all-files`) for Python code formatting (Ruff), Markdown formatting (markdownlint), code quality checks, trailing whitespace, end-of-file fixers, and merge conflict markers.
- Executes `ansible-lint` to ensure playbook standards and Ansible best practices (gracefully skips if running natively on Windows where `ansible-lint` is unsupported; run inside WSL or Docker for local Ansible linting).

```bash
make lint
```

#### Continuous Integration Pipeline (GitHub Actions)

When you open a Pull Request, [`.github/workflows/ci.yml`](.github/workflows/ci.yml) automatically validates:

1. **Security & Secret Scanning**: Scans all commits using `gitleaks` to prevent accidental credential or private key exposure.
2. **Template & Schema Integrity**: Runs `tests/validate_templates.py`.
3. **Formatting & Style**: Runs `pre-commit run --all-files`.
4. **Ansible Standards**: Runs `ansible-lint` on an Ubuntu runner.

> [!TIP]
> If `make lint` fails due to Python formatting, run `ruff format .` to format your code automatically before committing.

### 3. Commit Message Convention

We adhere to [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New automation features, templates, or playbooks
- `fix:` Bug fixes in templates, schemas, or tasks
- `docs:` Documentation improvements or updates
- `refactor:` Code reorganization without behavioral change
- `test:` Test suite additions or enhancements
- `ci:` Changes to GitHub Actions workflows or pre-commit hooks

---

## Pull Request Process

1. Create a feature branch from `main`:

   ```bash
   git checkout -b feat/your-feature-name
   ```

2. Commit your changes following conventional commit syntax.
3. Push to your fork and submit a Pull Request to `main`.
4. Ensure all GitHub Actions CI checks pass.
5. Provide a clear description of the change, test results, and any relevant MD-CLI verification outputs.

---

## Reporting Issues

If you find a bug or have a feature request:

- Open an issue on GitHub with a clear description.
- Include router software version (e.g. SR OS 25.7.R1), Python/Ansible versions, and relevant logs.
- Do **not** post proprietary license keys or production network secrets in issues.
