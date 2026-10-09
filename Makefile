.PHONY: help install lab-up lab-down deploy check test-pre test-post build-ee lint clean

help:
	@echo "Available commands in sros-automation-starter:"
	@echo "  make install     - Install Python dependencies and Ansible collections"
	@echo "  make lab-up      - Deploy Containerlab lab topology"
	@echo "  make lab-down    - Destroy Containerlab lab topology"
	@echo "  make deploy      - Deploy configuration to all routers via Ansible"
	@echo "  make canary-deploy - Execute canary deployment (1 router per batch)"
	@echo "  make check       - Dry-run deployment in check mode with candidate diff"
	@echo "  make test-pre    - Capture baseline network state via pyATS"
	@echo "  make test-post   - Capture post-change state and generate HTML drift report"
	@echo "  make build-ee    - Build containerized Execution Environment (sros-ee:latest)"
	@echo "  make lint        - Run ansible-lint on all playbooks and templates"
	@echo "  make clean       - Remove cached files, pyATS snapshots, and artifacts"

install:
	pip install -r requirements.txt
	ansible-galaxy collection install -r ansible/requirements.yml

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

test-pre:
	python pyats_test_suite.py --mode pre

test-post:
	python pyats_test_suite.py --mode post

build-ee:
	ansible-builder build -f ansible/execution-environment.yml -t sros-ee:latest

lint:
	ansible-lint

clean:
	rm -rf snapshots/*.json snapshots/*.html .cache/ __pycache__/
