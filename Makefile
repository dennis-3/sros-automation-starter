.PHONY: help install lab-up lab-down deploy canary-deploy check snapshot test-pre test-post build-ee validate lint clean

help:
	@echo "Available commands in sros-automation-starter:"
	@echo "  make install       - Install Python dependencies, Ansible collections & pre-commit hooks"
	@echo "  make lab-up        - Deploy Containerlab lab topology"
	@echo "  make lab-down      - Destroy Containerlab lab topology"
	@echo "  make deploy        - Deploy configuration to all routers via Ansible"
	@echo "  make canary-deploy - Execute canary deployment (1 router per batch)"
	@echo "  make check         - Dry-run deployment in check mode with candidate diff"
	@echo "  make snapshot      - Snapshot router operational state to JSON"
	@echo "  make test-pre      - Capture baseline network state via pyATS"
	@echo "  make test-post     - Capture post-change state and generate HTML drift report"
	@echo "  make build-ee      - Build containerized Execution Environment (sros-ee:latest)"
	@echo "  make validate      - Fast pre-flight check: Jinja2 templates, XML render, and schemas"
	@echo "  make lint          - Comprehensive linting: validate, pre-commit hooks, and ansible-lint"
	@echo "  make clean         - Remove cached files, pyATS snapshots, and artifacts"

install:
	pip install -r requirements.txt
	ansible-galaxy collection install -r ansible/requirements.yml
	pre-commit install

lab-up:
	sudo containerlab deploy -t topology.yaml

lab-down:
	sudo containerlab destroy -t topology.yaml

deploy:
	ansible-playbook ansible/playbooks/deploy.yml

canary-deploy:
	ansible-playbook ansible/playbooks/deploy.yml -e sros_serial_batch=1

check:
	ansible-playbook ansible/playbooks/deploy.yml --check -e sros_show_diff=true

snapshot:
	ansible-playbook ansible/playbooks/snapshot.yml --tags state-json

test-pre:
	python pyats_test_suite.py --mode pre

test-post:
	python pyats_test_suite.py --mode post

build-ee:
	ansible-builder build -f ansible/execution-environment.yml -t sros-ee:latest

validate:
	python tests/validate_templates.py

lint: validate
	pre-commit run --all-files
	@command -v ansible-lint >/dev/null 2>&1 && ansible-lint || echo "[INFO] Skipping ansible-lint (not installed or running on native Windows). Run inside WSL, container, or CI."

clean:
	rm -rf snapshots/*.json snapshots/*.html .cache/ __pycache__/
