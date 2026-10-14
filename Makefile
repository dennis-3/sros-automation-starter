.PHONY: help install lab-up lab-down deploy deploy-verified canary-deploy canary-deploy-verified check confirm rollback snapshot test-pre test-post test-post-invariants build-ee validate lint sync-yang clean

help:
	@echo "Available commands in sros-automation-starter:"
	@echo "  make install                - Install Python dependencies, Ansible collections & pre-commit hooks"
	@echo "  make lab-up                 - Deploy Containerlab lab topology"
	@echo "  make lab-down               - Destroy Containerlab lab topology"
	@echo "  make deploy                 - Deploy configuration to all routers via Ansible"
	@echo "  make deploy-verified        - Deploy with pre/post validation and auto-rollback"
	@echo "  make canary-deploy          - Execute canary deployment (1 router per batch)"
	@echo "  make canary-deploy-verified - Canary deployment with pre/post validation"
	@echo "  make check                  - Dry-run deployment in check mode with candidate diff"
	@echo "  make confirm                - Confirm pending candidate commit"
	@echo "  make rollback               - Cancel unconfirmed commit and discard changes"
	@echo "  make snapshot               - Snapshot router operational state to JSON"
	@echo "  make test-pre               - Capture baseline network state via pyATS"
	@echo "  make test-post              - Capture post-change state and generate HTML drift report"
	@echo "  make test-post-invariants   - Post-change validation with invariant checks"
	@echo "  make build-ee               - Build containerized Execution Environment (sros-ee:latest)"
	@echo "  make validate               - Fast pre-flight check: Jinja2 templates, XML render, and schemas"
	@echo "  make lint                   - Full static analysis: validate, pre-commit hooks, and ansible-lint"
	@echo "  make sync-yang              - Sync official Nokia SR OS 25.7 YANG models to .yang/sros_25.7"
	@echo "  make clean                  - Remove cached files, pyATS snapshots, and artifacts"

install:
	pip install -r requirements.txt
	ansible-galaxy collection install -r ansible/requirements.yml
	pre-commit install

sync-yang:
	@git clone -b sros_25.7 --depth 1 https://github.com/nokia/7x50_YangModels.git .yang/sros_25.7 || (cd .yang/sros_25.7 && git pull)

lab-up:
	sudo containerlab deploy -t topology.yaml

lab-down:
	sudo containerlab destroy -t topology.yaml

deploy:
	ansible-playbook ansible/playbooks/deploy.yml

deploy-verified:
	$(PYTHON) run_verified_deployment.py

canary-deploy:
	ansible-playbook ansible/playbooks/deploy.yml -e sros_serial_batch=1

canary-deploy-verified:
	$(PYTHON) run_verified_deployment.py --batch-size 1

check:
	ansible-playbook ansible/playbooks/deploy.yml --check -e sros_show_diff=true

confirm:
	ansible-playbook ansible/playbooks/confirm.yml

rollback:
	ansible-playbook ansible/playbooks/rollback.yml

snapshot:
	ansible-playbook ansible/playbooks/snapshot.yml --tags state-json

PYTHON ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)

test-pre:
	$(PYTHON) pyats_test_suite.py --mode pre

test-post:
	$(PYTHON) pyats_test_suite.py --mode post

test-post-invariants:
	$(PYTHON) pyats_test_suite.py --mode post --enforce-invariants

EE_VERBOSITY ?= 3

build-ee:
	BUILDKIT_PROGRESS=plain ansible-builder build -f ansible/execution-environment.yml -t sros-ee:latest -v $(EE_VERBOSITY)

validate:
	$(PYTHON) tests/validate_templates.py

lint: validate
	pre-commit run --all-files
	@command -v ansible-lint >/dev/null 2>&1 && ansible-lint || echo "[INFO] Skipping ansible-lint (not installed or running on native Windows). Run inside WSL, container, or CI."

clean:
	rm -rf snapshots ansible/playbooks/snapshots context/ .cache/ __pycache__/ tests/__pycache__/
