#!/usr/bin/env python3
"""
Orchestrates Nokia SR OS deployment with pre/post validation and auto-rollback:
  1. Static validation (validate_templates.py)
  2. Pre-change state snapshot (pyats_test_suite.py --mode pre)
  3. Candidate deployment with confirmed commit (deploy.yml)
  4. Protocol convergence soak
  5. Post-change validation with invariants (pyats_test_suite.py --mode post --enforce-invariants)
  6. Confirm on pass (confirm.yml) or rollback on failure (rollback.yml)
"""

import argparse
import subprocess
import sys
import time

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_stage(title):
    print(f"\n{BOLD}{CYAN}=== {title} ==={RESET}\n")


def run_cmd(cmd_list, desc, check=True):
    print(f"{BOLD}[RUN]{RESET} {desc}")
    print(f"  $ {' '.join(cmd_list)}")
    start_time = time.time()
    res = subprocess.run(cmd_list)
    elapsed = time.time() - start_time

    if res.returncode == 0:
        print(f"{GREEN}[OK]{RESET} {desc} ({elapsed:.1f}s)\n")
        return True
    else:
        print(f"{RED}[FAIL]{RESET} {desc} (exit {res.returncode}, {elapsed:.1f}s)\n")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Nokia SR OS deployment with pre/post verification and auto-rollback"
    )
    parser.add_argument(
        "--inventory",
        default="ansible/inventory.yml",
        help="Path to Ansible inventory file (default: ansible/inventory.yml)",
    )
    parser.add_argument(
        "--confirm-timeout",
        type=int,
        default=600,
        help="Confirmed commit timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--soak-seconds",
        type=int,
        default=30,
        help="Soak delay in seconds for convergence (default: 30)",
    )
    parser.add_argument(
        "--batch-size",
        default="100%",
        help="Canary batch size (e.g. 1 or 100%%, default: 100%%)",
    )
    parser.add_argument(
        "--limit",
        default=None,
        help="Limit execution to specific host(s) or group",
    )
    parser.add_argument(
        "--skip-pre",
        action="store_true",
        help="Skip pre-change snapshot",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run deployment in dry-run check mode",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show candidate configuration diff",
    )

    args = parser.parse_args()
    py_bin = sys.executable

    print_stage("Deployment Parameters")
    print(f"Inventory:         {args.inventory}")
    print(f"Batch Size:        {args.batch_size}")
    print(f"Confirmed Timeout: {args.confirm_timeout}s")
    print(f"Soak Window:       {args.soak_seconds}s")
    if args.limit:
        print(f"Limit:             {args.limit}")

    # Stage 1: Static Validation
    print_stage("Stage 1: Pre-flight Validation")
    if not run_cmd(
        [py_bin, "tests/validate_templates.py"], "Template and schema validation"
    ):
        print(f"{RED}[ERROR] Static validation failed. Halting deployment.{RESET}")
        sys.exit(1)

    # Stage 2: Pre-Change Snapshot
    if not args.skip_pre:
        print_stage("Stage 2: Pre-Change Snapshot")
        pre_cmd = [
            py_bin,
            "pyats_test_suite.py",
            "--mode",
            "pre",
            "--inventory",
            args.inventory,
        ]
        if args.limit:
            pre_cmd.extend(["--limit", args.limit])
        if not run_cmd(pre_cmd, "pyATS pre-change snapshot"):
            print(
                f"{RED}[ERROR] Pre-change snapshot failed. Halting deployment.{RESET}"
            )
            sys.exit(1)
    else:
        print(f"{YELLOW}[INFO] Skipping pre-change snapshot (--skip-pre set).{RESET}")

    # Stage 3: Candidate Deployment with Confirmed Commit
    print_stage("Stage 3: Candidate Deployment")
    deploy_cmd = [
        "ansible-playbook",
        "ansible/playbooks/deploy.yml",
        "-i",
        args.inventory,
        "-e",
        f"sros_confirm_timeout={args.confirm_timeout}",
        "-e",
        "sros_auto_confirm=false",
        "-e",
        f"sros_serial_batch={args.batch_size}",
    ]
    if args.limit:
        deploy_cmd.extend(["--limit", args.limit])
    if args.check:
        deploy_cmd.append("--check")
    if args.diff:
        deploy_cmd.extend(["-e", "sros_show_diff=true"])

    if not run_cmd(deploy_cmd, "Ansible deployment", check=False):
        print(f"{RED}[ERROR] Deployment failed. Candidate changes discarded.{RESET}")
        sys.exit(1)

    if args.check:
        print(f"{GREEN}[OK] Check mode completed successfully.{RESET}")
        sys.exit(0)

    # Stage 4: Soak Interval
    if args.soak_seconds > 0:
        print_stage("Stage 4: Protocol Convergence Soak")
        print(
            f"[INFO] Waiting {args.soak_seconds}s for protocol and FIB convergence..."
        )
        for remaining in range(args.soak_seconds, 0, -5):
            print(f"  ... {remaining}s remaining ...", flush=True)
            time.sleep(min(5, remaining))
        print(f"{GREEN}[OK] Soak interval elapsed.{RESET}")

    # Stage 5: Post-Change Verification
    print_stage("Stage 5: Post-Change Verification")
    post_cmd = [
        py_bin,
        "pyats_test_suite.py",
        "--mode",
        "post",
        "--inventory",
        args.inventory,
        "--enforce-invariants",
    ]
    if args.limit:
        post_cmd.extend(["--limit", args.limit])

    verification_ok = run_cmd(post_cmd, "pyATS post-change verification", check=False)

    # Stage 6: Decision Gate
    if verification_ok:
        print_stage("Stage 6: Confirm Commit")
        confirm_cmd = [
            "ansible-playbook",
            "ansible/playbooks/confirm.yml",
            "-i",
            args.inventory,
        ]
        if args.limit:
            confirm_cmd.extend(["--limit", args.limit])

        if run_cmd(confirm_cmd, "Confirm commit RPC"):
            print(f"{GREEN}[OK] Deployment verified and confirmed permanently.{RESET}")
            print("Report: snapshots/network_drift_report.html\n")
            sys.exit(0)
        else:
            print(f"{RED}[ERROR] Commit confirmation failed.{RESET}")
            sys.exit(1)
    else:
        print_stage("Stage 6: Rollback")
        rollback_cmd = [
            "ansible-playbook",
            "ansible/playbooks/rollback.yml",
            "-i",
            args.inventory,
        ]
        if args.limit:
            rollback_cmd.extend(["--limit", args.limit])

        run_cmd(rollback_cmd, "Rollback unconfirmed commit", check=False)
        print(f"{RED}[ROLLBACK] Verification failed. Rollback executed.{RESET}")
        print("Report: snapshots/network_drift_report.html\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
