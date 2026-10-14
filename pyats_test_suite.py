#!/usr/bin/env python3
import argparse
import concurrent.futures
import html
import json
import logging
import os
import re
import shutil
import sys

from deepdiff import DeepDiff
from pyats import aetest, topology
from pysros.management import connect as sros_connect

log = logging.getLogger(__name__)


# --- DATA CONVERSION & PATH HELPERS ---
def extract_path_value(obj, path_str):
    """
    Safely navigates a nested dict/list using DeepDiff path strings
    (e.g., "root['configure']['router']['interface'][0]['admin-state']")
    without using eval(), avoiding syntax errors on hyphenated YANG keys
    and eliminating arbitrary code evaluation risks (CWE-95).
    """
    tokens = re.findall(r"\[(?:'([^']*)'|\"([^\"]*)\"|(\d+))\]", str(path_str))
    curr = obj
    for token in tokens:
        if token[2]:
            key = int(token[2])
        else:
            key = token[0] if token[0] != "" else token[1]

        if curr is None:
            return None
        if isinstance(curr, list) and isinstance(key, int):
            if 0 <= key < len(curr):
                curr = curr[key]
            else:
                return None
        elif isinstance(curr, dict) and key in curr:
            curr = curr[key]
        else:
            return None
    return curr


def prune_excluded(node, current_path="root", exclude_patterns=None):
    """
    Recursively strips nested structures whose paths match exclude_patterns,
    and removes empty dictionary leaves, preventing giant 400+ line JSON dumps
    for newly added or deleted network elements (e.g., ports, interfaces).
    """
    if exclude_patterns is None:
        exclude_patterns = []

    if isinstance(node, dict):
        pruned = {}
        for k, v in node.items():
            child_path = f"{current_path}['{k}']"
            if any(re.search(pat, child_path) for pat in exclude_patterns):
                continue
            pruned_child = prune_excluded(v, child_path, exclude_patterns)
            # Drop empty dictionaries left over after nested pruning
            if (
                isinstance(pruned_child, dict)
                and not pruned_child
                and isinstance(v, dict)
                and v
            ):
                continue
            pruned[k] = pruned_child
        return pruned
    elif isinstance(node, list):
        return [
            prune_excluded(item, f"{current_path}[{i}]", exclude_patterns)
            for i, item in enumerate(node)
        ]
    else:
        return node


def nokia_to_dict(data):
    """
    Recursively unwraps Nokia YANG container and leaf structural metadata objects
    into standard Python native dictionary and array data frameworks.
    """
    if hasattr(data, "keys"):
        return {str(k): nokia_to_dict(data[k]) for k in data.keys()}
    elif isinstance(data, list):
        return [nokia_to_dict(item) for item in data]
    elif hasattr(data, "value"):
        return nokia_to_dict(data.value)
    elif hasattr(data, "__str__") and not isinstance(
        data, (str, int, float, bool, type(None))
    ):
        return str(data)
    else:
        return data


def _unwrap_state_root(data):
    """
    Normalizes root container whether it's raw, wrapped under 'state',
    or keyed by full YANG path like '/nokia-state:state'.
    """
    if not isinstance(data, dict):
        return {}
    for k in ("state", "/nokia-state:state", "nokia-state:state"):
        if k in data and isinstance(data[k], dict):
            return data[k]
    return data


def _get_router_entries(data):
    root = _unwrap_state_root(data)
    routers = root.get("router", {})
    entries = []
    if isinstance(routers, dict):
        for r_name, r_val in routers.items():
            if isinstance(r_val, dict):
                entries.append((r_name, r_val))
    elif isinstance(routers, list):
        for item in routers:
            if isinstance(item, dict):
                r_name = str(item.get("router-name", "Base"))
                entries.append((r_name, item))
    return entries


def check_operational_invariants(pre_json, post_json, dev_label="", diff=None):
    """
    Analyzes pre- vs post-change state to detect operational regressions:
    - Downed interfaces (previously operational interfaces or sub-protocols dropping)
    - Downed ports (previously operational physical ports dropping)
    - Dropped BGP sessions (previously established peers leaving established state)
    - Routing table collapse (unexpected massive loss of active routes)
    - DeepDiff leaf regressions for any oper-state or session-state
    Returns a list of regression descriptions. Empty list indicates clean pass.
    """
    regressions = []
    if not isinstance(pre_json, dict) or not isinstance(post_json, dict):
        return regressions

    # 1. Interface Operational State Invariant
    def _extract_interfaces(data):
        ifs = {}
        for r_name, r_val in _get_router_entries(data):
            if_val = r_val.get("interface", {})
            if_items = []
            if isinstance(if_val, dict):
                for if_name, if_obj in if_val.items():
                    if isinstance(if_obj, dict):
                        if_items.append((if_name, if_obj))
            elif isinstance(if_val, list):
                for if_obj in if_val:
                    if isinstance(if_obj, dict):
                        if_name = str(if_obj.get("interface-name", ""))
                        if if_name:
                            if_items.append((if_name, if_obj))

            for if_name, if_obj in if_items:
                oper = str(if_obj.get("oper-state", "")).lower()
                ipv4_oper = ""
                if isinstance(if_obj.get("ipv4"), dict):
                    ipv4_oper = str(if_obj["ipv4"].get("oper-state", "")).lower()
                ipv6_oper = ""
                if isinstance(if_obj.get("ipv6"), dict):
                    ipv6_oper = str(if_obj["ipv6"].get("oper-state", "")).lower()
                ifs[(r_name, if_name)] = {
                    "oper": oper,
                    "ipv4_oper": ipv4_oper,
                    "ipv6_oper": ipv6_oper,
                }
        return ifs

    pre_ifs = _extract_interfaces(pre_json)
    post_ifs = _extract_interfaces(post_json)
    for (r_name, if_name), pre_info in pre_ifs.items():
        post_info = post_ifs.get((r_name, if_name))
        if pre_info["oper"] == "up":
            if post_info is None:
                regressions.append(
                    f"[{dev_label}] Interface router '{r_name}' interface '{if_name}' was removed (was 'up')"
                )
            elif post_info["oper"] != "up":
                regressions.append(
                    f"[{dev_label}] Interface router '{r_name}' interface '{if_name}' state dropped from 'up' to '{post_info['oper']}'"
                )
        if pre_info["ipv4_oper"] == "up":
            if post_info is None or post_info["ipv4_oper"] != "up":
                post_v4 = post_info["ipv4_oper"] if post_info else "removed"
                regressions.append(
                    f"[{dev_label}] Interface router '{r_name}' interface '{if_name}' IPv4 state dropped from 'up' to '{post_v4}'"
                )
        if pre_info["ipv6_oper"] == "up":
            if post_info is None or post_info["ipv6_oper"] != "up":
                post_v6 = post_info["ipv6_oper"] if post_info else "removed"
                regressions.append(
                    f"[{dev_label}] Interface router '{r_name}' interface '{if_name}' IPv6 state dropped from 'up' to '{post_v6}'"
                )

    # 2. Port Operational State Invariant
    def _extract_ports(data):
        ports = {}
        root = _unwrap_state_root(data)
        p_val = root.get("port", {})
        p_items = []
        if isinstance(p_val, dict):
            for p_id, p_obj in p_val.items():
                if isinstance(p_obj, dict):
                    p_items.append((p_id, p_obj))
        elif isinstance(p_val, list):
            for p_obj in p_val:
                if isinstance(p_obj, dict):
                    p_id = str(p_obj.get("port-id", ""))
                    if p_id:
                        p_items.append((p_id, p_obj))
        for p_id, p_obj in p_items:
            ports[p_id] = str(p_obj.get("oper-state", "")).lower()
        return ports

    pre_ports = _extract_ports(pre_json)
    post_ports = _extract_ports(post_json)
    for p_id, pre_oper in pre_ports.items():
        if pre_oper == "up":
            post_oper = post_ports.get(p_id)
            if post_oper is not None and post_oper != "up":
                regressions.append(
                    f"[{dev_label}] Port '{p_id}' operational state dropped from 'up' to '{post_oper}'"
                )

    # 3. BGP Peer State Invariant
    def _extract_bgp_peers(data):
        bgp_peers = {}
        for r_name, r_val in _get_router_entries(data):
            bgp_val = r_val.get("bgp", {})
            if isinstance(bgp_val, dict):
                n_val = bgp_val.get("neighbor", {})
                n_items = []
                if isinstance(n_val, dict):
                    for peer_ip, peer_obj in n_val.items():
                        if isinstance(peer_obj, dict):
                            n_items.append((peer_ip, peer_obj))
                elif isinstance(n_val, list):
                    for peer_obj in n_val:
                        if isinstance(peer_obj, dict):
                            peer_ip = str(
                                peer_obj.get("ip-address", peer_obj.get("peer-ip", ""))
                            )
                            if peer_ip:
                                n_items.append((peer_ip, peer_obj))
                for peer_ip, peer_obj in n_items:
                    state = str(
                        peer_obj.get(
                            "session-state",
                            peer_obj.get("oper-state", ""),
                        )
                    ).lower()
                    bgp_peers[(r_name, peer_ip)] = state
        return bgp_peers

    pre_bgp = _extract_bgp_peers(pre_json)
    post_bgp = _extract_bgp_peers(post_json)
    for (r_name, peer_ip), pre_state in pre_bgp.items():
        if pre_state in ("established", "up"):
            post_state = post_bgp.get((r_name, peer_ip))
            if post_state not in ("established", "up"):
                regressions.append(
                    f"[{dev_label}] BGP neighbor '{peer_ip}' session dropped from '{pre_state}' to '{post_state}'"
                )

    # 4. Route Table Stability Invariant
    def _count_routes(data):
        count = 0
        for r_name, r_val in _get_router_entries(data):
            rt = r_val.get("route-table", {})
            if isinstance(rt, dict):
                unicast = rt.get("unicast", {})
                if isinstance(unicast, dict):
                    for af in ["ipv4", "ipv6"]:
                        routes = unicast.get(af, {}).get("route", {})
                        if isinstance(routes, dict):
                            count += len(routes)
                        elif isinstance(routes, list):
                            count += len(routes)
        return count

    pre_routes = _count_routes(pre_json)
    post_routes = _count_routes(post_json)
    if pre_routes > 5 and post_routes < (pre_routes * 0.5):
        regressions.append(
            f"[{dev_label}] Unicast routing table collapsed: {pre_routes} routes in pre vs {post_routes} in post (>50% drop)"
        )

    # 5. DeepDiff-level Leaf Regression Invariant
    if diff and isinstance(diff, dict):
        values_changed = diff.get("values_changed", {})
        if isinstance(values_changed, dict):
            for path, change in values_changed.items():
                p_str = str(path).lower()
                old_v = str(change.get("old_value", "")).lower()
                new_v = str(change.get("new_value", "")).lower()
                if "oper-state" in p_str and old_v == "up" and new_v != "up":
                    readable = str(path).replace("root", "YANG")
                    reg_msg = f"[{dev_label}] Operational state regression at {readable}: dropped from '{old_v}' to '{new_v}'"
                    if reg_msg not in regressions:
                        regressions.append(reg_msg)
                elif (
                    "session-state" in p_str
                    and old_v in ("established", "up")
                    and new_v not in ("established", "up")
                ):
                    readable = str(path).replace("root", "YANG")
                    reg_msg = f"[{dev_label}] BGP session state regression at {readable}: dropped from '{old_v}' to '{new_v}'"
                    if reg_msg not in regressions:
                        regressions.append(reg_msg)

    return regressions


def _capture_single_device(dev_name, dev, mode, yang_paths, timeout=180):
    """
    Connects to a single SR OS device and captures model-driven YANG state snapshots.
    """
    filename = f"nokia_snapshot_{dev_name}_{mode}.json"
    target_path = os.path.join("snapshots", filename)

    host_ip = (
        str(getattr(getattr(dev, "connections", None), "cli", dev).ip)
        if hasattr(dev, "connections")
        else str(dev)
    )
    username = dev.credentials.default.username
    password = dev.credentials.default.password

    # Determine NETCONF port (custom topology or environment variable)
    netconf_port = 830
    if hasattr(dev, "connections") and hasattr(dev.connections, "netconf"):
        netconf_port = int(getattr(dev.connections.netconf, "port", 830))
    elif os.getenv("SROS_PORT"):
        try:
            netconf_port = int(os.getenv("SROS_PORT"))
        except ValueError:
            pass

    conn = None
    try:
        conn = sros_connect(
            host=host_ip,
            username=username,
            password=password,
            port=netconf_port,
            hostkey_verify=False,
            timeout=timeout,
        )

        if len(yang_paths) == 1 and yang_paths[0] == "/nokia-state:state":
            raw_state = conn.running.get("/nokia-state:state")
            clean_data = nokia_to_dict(raw_state)
        else:
            clean_data = {}
            for path in yang_paths:
                try:
                    sub_state = conn.running.get(path)
                    clean_data[path] = nokia_to_dict(sub_state)
                except Exception as path_err:
                    log.warning(
                        f"[{dev_name}] Could not retrieve path '{path}': {path_err}"
                    )
                    clean_data[path] = None

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, indent=2)

        log.info(f"Success! [{dev_name}] Structured JSON state saved to {target_path}")
        return dev_name, True, None
    except Exception as exc:
        log.error(f"Failed to capture state from [{dev_name}]: {exc}")
        return dev_name, False, str(exc)
    finally:
        if conn is not None and hasattr(conn, "disconnect"):
            try:
                conn.disconnect()
            except Exception as close_exc:
                log.debug(
                    f"[{dev_name}] NETCONF disconnect cleanup notice: {close_exc}"
                )


def _run_parallel_device_command(testbed, command, max_workers=10):
    """
    Executes a CLI command in parallel across all devices in testbed.devices
    to avoid serial O(N) blocking delays in scaled topologies.
    Returns a dict mapping dev_name -> (output, exc).
    """
    results = {}
    if not testbed or not getattr(testbed, "devices", None):
        return results

    worker_count = min(max_workers, max(1, len(testbed.devices)))

    def _exec(name, d):
        try:
            if not hasattr(d, "execute"):
                if getattr(d, "is_connected", lambda: False)() and hasattr(d, "cli"):
                    out = d.cli.execute(command)
                    return name, out, None
                return (
                    name,
                    None,
                    RuntimeError(f"Device '{name}' has no active CLI connection"),
                )
            out = d.execute(command)
            return name, out, None
        except Exception as exc:
            return name, None, exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_exec, name, dev) for name, dev in testbed.devices.items()
        ]
        for f in concurrent.futures.as_completed(futures):
            name, out, exc = f.result()
            results[name] = (out, exc)

    return results


# --- AETEST STRUCTURAL BLOCKS ---
class CommonSetup(aetest.CommonSetup):
    @aetest.subsection
    def connect_to_devices(self, testbed):
        max_workers = self.parent.parameters.get("max_workers", 10)
        worker_count = min(max_workers, max(1, len(testbed.devices)))
        log.info(
            f"Connecting to {len(testbed.devices)} device(s) concurrently (max_workers={worker_count})..."
        )
        try:
            testbed.connect(via="cli", max_workers=worker_count)
        except Exception as exc:
            log.warning(
                f"Bulk testbed connect reported: {exc}. Verifying individual connections..."
            )
            failed_devices = []
            for dev_name, dev in testbed.devices.items():
                if not getattr(dev, "is_connected", lambda: False)():
                    try:
                        dev.connect(via="cli")
                    except Exception as dev_exc:
                        log.error(f"[{dev_name}] Connection failed: {dev_exc}")
                        failed_devices.append(f"{dev_name} ({dev_exc})")
            if failed_devices:
                self.failed(
                    f"Failed to connect to testbed devices: {', '.join(failed_devices)}"
                )


class ConnectivityTest(aetest.Testcase):
    @aetest.test
    def check_version(self):
        testbed = self.parent.parameters["testbed"]
        max_workers = self.parent.parameters.get("max_workers", 10)
        results = _run_parallel_device_command(
            testbed, "show version", max_workers=max_workers
        )
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] Failed to execute 'show version': {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
            elif output and ("Nokia" in output or "TiMOS" in output):
                log.info(f"[{dev_name}] Nokia SR OS detected successfully")
            else:
                log.error(f"[{dev_name}] Nokia OS not detected in output")
                failed_devices.append(f"{dev_name} (unrecognized OS)")

        if failed_devices:
            self.failed(
                f"Connectivity check failed on devices: {', '.join(failed_devices)}"
            )
        else:
            self.passed("Nokia OS detected successfully on all testbed devices")


class InterfaceStateTest(aetest.Testcase):
    @aetest.test
    def verify_interfaces(self):
        testbed = self.parent.parameters["testbed"]
        max_workers = self.parent.parameters.get("max_workers", 10)
        results = _run_parallel_device_command(
            testbed, "show router interface", max_workers=max_workers
        )
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] Interface check exception: {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
                continue
            down_interfaces = []
            for line in output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith(
                    (
                        "=",
                        "-",
                        "Interface Table",
                        "Interfaces",
                        "IP-Address",
                        "Interface-Name",
                    )
                ):
                    continue
                parts = line_clean.split()
                if len(parts) >= 3:
                    name, adm, opr = parts[0], parts[1], parts[2]
                    if adm.lower() == "up":
                        opr_parts = opr.split("/")
                        if any(p.lower() != "up" for p in opr_parts):
                            down_interfaces.append(f"{name} (adm:{adm}, opr:{opr})")
            if down_interfaces:
                failed_devices.append(
                    f"{dev_name}: [down: {', '.join(down_interfaces)}]"
                )
            else:
                log.info(f"[{dev_name}] All admin Up interfaces are operationally Up")

        if failed_devices:
            self.failed(
                f"Interface operational check failed on devices: {'; '.join(failed_devices)}"
            )
        else:
            self.passed(
                "All administrative Up interfaces are operationally Up across all testbed devices"
            )


class ISISTest(aetest.Testcase):
    @aetest.test
    def verify_isis_interfaces(self):
        testbed = self.parent.parameters["testbed"]
        max_workers = self.parent.parameters.get("max_workers", 10)
        results = _run_parallel_device_command(
            testbed, "show router isis interface", max_workers=max_workers
        )
        configured_count = 0
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] ISIS check exception: {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
                continue
            output_lower = output.lower()
            not_configured = (
                "not configured" in output_lower
                or "disabled" in output_lower
                or "no matching" in output_lower
                or "not running" in output_lower
                or "no. of interfaces: 0" in output_lower
                or "total interfaces: 0" in output_lower
            )
            if not_configured:
                log.info(
                    f"[{dev_name}] ISIS is not configured or has 0 interfaces; skipping"
                )
                continue

            configured_count += 1
            down_isis = []
            up_count = 0
            for line in output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith(
                    ("=", "-", "Router (", "Interface", "Total", "No. of")
                ):
                    continue
                parts = line_clean.split()
                if len(parts) >= 4:
                    if "down" in line_clean.lower():
                        down_isis.append(parts[0])
                    elif "up" in line_clean.lower():
                        up_count += 1

            if down_isis:
                failed_devices.append(
                    f"{dev_name} (down ISIS interfaces: {', '.join(down_isis)})"
                )
            elif up_count > 0:
                log.info(
                    f"[{dev_name}] {up_count} ISIS interface(s) are operationally Up"
                )
            else:
                log.info(f"[{dev_name}] ISIS configured with 0 active interfaces")

        if failed_devices:
            self.failed(
                f"ISIS interface verification failed on devices: {'; '.join(failed_devices)}"
            )
        elif configured_count == 0:
            self.passed("ISIS is not configured on any testbed devices; check passed")
        else:
            self.passed(
                f"ISIS interfaces verified Up across {configured_count} configured device(s)"
            )


class OSPFTest(aetest.Testcase):
    @aetest.test
    def verify_ospf_neighbors(self):
        testbed = self.parent.parameters["testbed"]
        max_workers = self.parent.parameters.get("max_workers", 10)
        results = _run_parallel_device_command(
            testbed, "show router ospf neighbor", max_workers=max_workers
        )
        configured_count = 0
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] OSPF check exception: {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
                continue
            output_lower = output.lower()
            if (
                "not configured" in output_lower
                or "disabled" in output_lower
                or "no matching" in output_lower
                or "not running" in output_lower
            ):
                log.info(f"[{dev_name}] OSPF is not configured; skipping")
                continue

            configured_count += 1
            degraded_neighbors = []
            full_count = 0
            for line in output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith(
                    ("=", "-", "OSPFv", "Interface-Name", "No. of")
                ):
                    continue
                parts = line_clean.split()
                if len(parts) >= 3:
                    state = parts[2].lower()
                    if state in ("down", "init", "attempt"):
                        degraded_neighbors.append(
                            f"{parts[0]}:{parts[1]} (state: {parts[2]})"
                        )
                    elif state in ("full", "2-way"):
                        full_count += 1

            if degraded_neighbors:
                failed_devices.append(
                    f"{dev_name} (degraded OSPF neighbors: {', '.join(degraded_neighbors)})"
                )
            else:
                log.info(f"[{dev_name}] {full_count} OSPF neighbor(s) active")

        if failed_devices:
            self.failed(
                f"OSPF neighbor verification failed on devices: {'; '.join(failed_devices)}"
            )
        elif configured_count == 0:
            self.passed("OSPF is not configured on any testbed devices; check passed")
        else:
            self.passed(
                f"OSPF neighbors verified across {configured_count} configured device(s)"
            )


class BGPTest(aetest.Testcase):
    @aetest.test
    def verify_bgp_peers(self):
        testbed = self.parent.parameters["testbed"]
        max_workers = self.parent.parameters.get("max_workers", 10)
        results = _run_parallel_device_command(
            testbed, "show router bgp summary", max_workers=max_workers
        )
        configured_count = 0
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] BGP check exception: {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
                continue
            output_lower = output.lower()
            if (
                "not configured" in output_lower
                or "disabled" in output_lower
                or "no matching" in output_lower
                or "bgp is not" in output_lower
            ):
                log.info(f"[{dev_name}] BGP is not configured; skipping")
                continue

            configured_count += 1
            degraded_peers = []
            established_count = 0
            for line in output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith(
                    (
                        "=",
                        "-",
                        "BGP Router",
                        "BGP Admin",
                        "Total",
                        "Neighbor",
                        "Description",
                        "ServiceId",
                    )
                ):
                    continue
                parts = line_clean.split()
                if len(parts) >= 2:
                    last_token = parts[-1].lower()
                    if last_token in (
                        "idle",
                        "active",
                        "connect",
                        "opensent",
                        "openconfirm",
                    ):
                        degraded_peers.append(f"{parts[0]} (state: {parts[-1]})")
                    elif "/" in last_token or last_token == "established":
                        established_count += 1

            if degraded_peers:
                failed_devices.append(
                    f"{dev_name} (degraded BGP peers: {', '.join(degraded_peers)})"
                )
            else:
                log.info(f"[{dev_name}] {established_count} BGP peer(s) established")

        if failed_devices:
            self.failed(
                f"BGP peer verification failed on devices: {'; '.join(failed_devices)}"
            )
        elif configured_count == 0:
            self.passed("BGP is not configured on any testbed devices; check passed")
        else:
            self.passed(
                f"BGP sessions verified across {configured_count} configured device(s)"
            )


class SnapshotState(aetest.Testcase):
    @aetest.test
    def capture_with_pysros(self, testbed):
        mode = self.parent.parameters.get("mode", "pre")
        yang_paths = self.parent.parameters.get("yang_paths", ["/nokia-state:state"])
        max_workers = self.parent.parameters.get("max_workers", 10)
        timeout = self.parent.parameters.get("timeout", 180)
        os.makedirs("snapshots", exist_ok=True)

        worker_count = min(max_workers, max(1, len(testbed.devices)))
        log.info(
            f"Starting parallel snapshot capture across {len(testbed.devices)} device(s) with {worker_count} workers (timeout={timeout}s)..."
        )

        failures = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count
        ) as executor:
            future_to_dev = {
                executor.submit(
                    _capture_single_device, dev_name, dev, mode, yang_paths, timeout
                ): dev_name
                for dev_name, dev in testbed.devices.items()
            }
            for future in concurrent.futures.as_completed(future_to_dev):
                dev_name, success, error_msg = future.result()
                if not success:
                    failures[dev_name] = error_msg

        # Maintain default snapshot file for single-device verification workflows
        if testbed.devices:
            first_name = next(iter(testbed.devices.keys()))
            legacy_source = os.path.join(
                "snapshots", f"nokia_snapshot_{first_name}_{mode}.json"
            )
            legacy_target = os.path.join("snapshots", f"nokia_snapshot_{mode}.json")
            if os.path.exists(legacy_source):
                shutil.copyfile(legacy_source, legacy_target)

        if failures:
            self.failed(f"Snapshot capture failed on devices: {failures}")
        else:
            self.passed("Data captured and saved to snapshots/")


class VerifyChanges(aetest.Testcase):
    @aetest.test
    def compare_snapshots(self):
        testbed = self.parent.parameters.get("testbed")
        html_path = "snapshots/network_drift_report.html"

        exclude_paths = [
            # --- System, Time & Clock Volatiles ---
            r"root.*?\['statistics'\]",  # All packet/byte counters
            r"root.*?\['drop-reason'\]",  # Packet drop reason counters
            r"root.*?\['last-oper-change'\]",  # Timestamp for status changes
            r"root.*?\['oper-state-last-changed'\]",  # Port oper state change timestamp
            r"root.*?\['last-state-change'\]",  # Operational state timestamp
            r"root.*?\['last-mgmt-change'\]",  # Management transaction timestamp
            r"root.*?\['oper-time'\]",  # Operational uptime counter
            r"root.*?\['timer'\]",  # Protocol hold/dead/hello timers
            r"root.*?\['uptime'\]",  # System and process uptimes
            r"root.*?\['up-time'\]",  # System uptime ticks
            r"root.*?\['current-time'\]",  # Live system clock
            r"root.*?\['system-time'\]",  # Live system timestamp
            r"root.*?\['last-saved-time'\]",  # Datastore checkpoint save timestamp
            r"root.*?\['last-modified-time'\]",  # Configuration file modification timestamp
            r"root.*?\['time-last-modified'\]",  # Running datastore modification timestamp
            r"root.*?\['user-last-modified'\]",  # Running datastore user edit timestamp
            r"root.*?\['config-save'\]",  # Configuration save status and telemetry
            r"root.*?\['last-saved-file'\]",  # Last saved config filename
            r"root.*?\['time-saved'\]",  # Config save timestamp
            r"root.*?\['saved-time'\]",  # Datastore checkpoint timestamp
            r"root.*?\['boot-time'\]",  # CPM boot timestamp
            r"root.*?\['software-last-boot-time'\]",  # MDA/card boot timestamp
            r"root.*?\['high-water-mark-hit-time'\]",  # Dynamic policer watermark timestamp
            r"root.*?\['age'\]",  # Route/RIB table entry age in seconds
            # --- Hardware, Memory & Queuing Volatiles ---
            r"root.*?\['cpu'\]",  # Live CPU utilization percentages
            r"root.*?\['memory'\]",  # Live memory usage indexes
            r"root.*?\['memory-pools'\]",  # Dynamic memory pool buffers and allocations
            r"root.*?\['allocated-memory'\]",  # Dynamic RAM fluctuations
            r"root.*?\['temperature'\]",  # Environmental sensor readings
            r"root.*?\['hardware-queue'\]",  # FP ingress/egress queue buffer allocations & burst stats
            r"root.*?\['pool-usage'\]",  # FP ingress/egress buffer pool usage telemetry
            r"root.*?\['burst-control-group'\]",  # Forwarding plane burst calculation metrics
            r"root.*?\['resource-usage'\]",  # Dynamic ingress queue pool allocation counters
            r"root.*?\['free-space'\]",  # Compact flash / storage volume available bytes
            # --- Ephemeral Management & Session Volatiles ---
            r"root.*?\['connections'\]",  # Ephemeral TCP sockets (NETCONF/SSH ports)
            r"root.*?\['active-sessions'\]",  # Active management session table and idle counters
            r"root.*?\['attempted-logins'\]",  # User login attempt counter
            r"root.*?\['session'\]",  # Dynamic NETCONF/CLI sessions and idle times
            r"root.*?\['ssh'\]",  # Dynamic SSH server connection channels
            r"root.*?\['log'\]",  # System event logs and audit streams
            r"root.*?\['log-id'\]",  # Log instance and next-event sequence counters
            r"root.*?\['commit-history'\]",  # Checkpoint commit increment files and commit IDs
            r"root.*?\['neighbor-discovery'\]",  # Dynamic IPv6 ND cache / state transitions
            r"root.*?\['arp-table'\]",  # Dynamic ARP cache entries
            # --- Telemetry & Precision Timing Volatiles ---
            r"root.*?\['ptp'\]",  # Precision Time Protocol rolling historical records
            # --- Interface, Port & Protocol Operational Subtrees ---
            r"root.*?\['subscriber-management'\]",  # Broadband subscriber zero counters
            r"root.*?\['queue'\]",  # Queue hardware allocation/counters
            r"root.*?\['queue-group'\]",  # Queue group shaper/buffer trees
            r"root.*?\['pool'\]",  # Ingress/egress buffer pool trees
            r"root.*?\['dot1x'\]",  # 802.1x PAE state and zero counters
            r"root.*?\['efm-oam'\]",  # 802.3ah OAM telemetry zero counters
            r"root.*?\['elmi'\]",  # Ethernet LMI zero counters
            r"root.*?\['ssm'\]",  # SyncE SSM telemetry
            r"root.*?\['crc-monitor'\]",  # CRC alarm monitor state
            r"root.*?\['symbol-monitor'\]",  # Symbol error monitor state
            r"root.*?\['dampening'\]",  # Flap dampening penalty counters
            r"root.*?\['down-when-looped'\]",  # Loop detection state
            r"root.*?\['forwarding-engine'\]",  # Internal packet drop reason engines
            r"root.*?\['icmp'\]",  # ICMP packet counters
            r"root.*?\['icmp6'\]",  # ICMPv6 packet counters
            r"root.*?\['dhcp'\]",  # DHCP packet counters
            # --- Protocol Keepalive / Counter Volatiles ---
            r"root.*?\['sequence-number'\]",  # OSPF/ISIS LSA sequence numbers
            r"root.*?\['keepalive'\]",  # Protocol keepalive counters
            r"root.*?\['in-messages'\]",  # Inbound message packet counts
            r"root.*?\['out-messages'\]",  # Outbound message packet counts
            r"root.*?\['in-updates'\]",  # Inbound route updates
            r"root.*?\['out-updates'\]",  # Outbound route updates
        ]

        # Determine devices to compare
        devices_to_compare = []
        if testbed and testbed.devices:
            for dev_name in testbed.devices.keys():
                pre_p = f"snapshots/nokia_snapshot_{dev_name}_pre.json"
                post_p = f"snapshots/nokia_snapshot_{dev_name}_post.json"
                if os.path.exists(pre_p) and os.path.exists(post_p):
                    devices_to_compare.append((dev_name, pre_p, post_p))

        # Fallback to legacy single files if no device-specific pairs were matched
        if not devices_to_compare:
            pre_legacy = "snapshots/nokia_snapshot_pre.json"
            post_legacy = "snapshots/nokia_snapshot_post.json"
            if os.path.exists(pre_legacy) and os.path.exists(post_legacy):
                devices_to_compare.append(("global", pre_legacy, post_legacy))

        if not devices_to_compare:
            self.failed(
                "Missing snapshots for comparison. Ensure both pre and post states exist."
            )

        drift_detected = False
        all_diff_sections = []

        def _compute_diff(label, pre_p, post_p):
            try:
                with open(pre_p, "r", encoding="utf-8") as f:
                    pre_j = json.load(f)
                with open(post_p, "r", encoding="utf-8") as f:
                    post_j = json.load(f)

                d = DeepDiff(
                    pre_j, post_j, ignore_order=True, exclude_regex_paths=exclude_paths
                )
                return label, d, pre_j, post_j, None
            except Exception as e:
                return label, None, None, None, str(e)

        max_workers = self.parent.parameters.get("max_workers", 10)
        worker_count = min(max_workers, max(1, len(devices_to_compare)))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count
        ) as executor:
            futures = [
                executor.submit(_compute_diff, lbl, p_pre, p_post)
                for lbl, p_pre, p_post in devices_to_compare
            ]
            for f in concurrent.futures.as_completed(futures):
                dev_label, diff, pre_json, post_json, err = f.result()
                if err:
                    log.error(
                        f"[{dev_label}] State snapshot diff comparison failed: {err}"
                    )
                elif diff:
                    drift_detected = True
                    all_diff_sections.append((dev_label, diff, pre_json, post_json))

        # Sort sections by device label for deterministic reporting
        all_diff_sections.sort(key=lambda s: s[0])

        if not drift_detected:
            self.passed("✅ No operational changes detected. The network is stable!")
        else:
            log.error(
                "⚠️ Network drift detected! Rendering isolated change exceptions to HTML..."
            )

            html_content = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Network Drift Exception Report</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 30px; background: #f8f9fa; }
                    h2 { color: #212529; border-bottom: 2px solid #dee2e6; padding-bottom: 10px; }
                    h3 { color: #0d6efd; margin-top: 30px; border-bottom: 1px solid #ced4da; padding-bottom: 6px; }
                    .change-type { font-weight: bold; font-size: 1.1em; margin-top: 25px; margin-bottom: 10px; text-transform: uppercase; }
                    .added { color: #2b753e; } .removed { color: #b32424; } .changed { color: #b07d00; }
                    th, td { padding: 12px; text-align: left; border: 1px solid #dee2e6;
                             font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; font-size: 0.9em; }
                    th { background-color: #f1f3f5; color: #495057; font-family: inherit; font-size: 0.95em; }
                    pre { margin: 0; white-space: pre-wrap; max-height: 450px; overflow-y: auto; }
                </style>
            </head>
            <body>
                <h2>⚠️ Network Drift Exception Report (Isolated Delta Only)</h2>
            """

            sections = [
                ("dictionary_item_added", "Added Elements", "added"),
                ("dictionary_item_removed", "Removed Elements", "removed"),
                ("values_changed", "Modified Values", "changed"),
                ("iterable_item_added", "Added Array Items", "added"),
                ("iterable_item_removed", "Removed Array Items", "removed"),
            ]

            for dev_label, diff, pre_json, post_json in all_diff_sections:
                html_content += f"<h3>Device: {dev_label}</h3>"
                for diff_key, header_title, css_class in sections:
                    if diff_key in diff:
                        html_content += (
                            f'<div class="change-type {css_class}">{header_title}</div>'
                        )
                        html_content += (
                            '<table><tr><th style="width: 35%;">YANG Data Path</th>'
                        )

                        if diff_key == "values_changed":
                            html_content += "<th>Pre-Change Value</th><th>Post-Change Value</th></tr>"
                        else:
                            html_content += "<th>Element Details Content</th></tr>"

                        diff_data = diff[diff_key]

                        if isinstance(diff_data, dict):
                            for path, details in diff_data.items():
                                readable_path = html.escape(
                                    str(path).replace("root", "YANG")
                                )
                                html_content += (
                                    f"<tr><td><strong>{readable_path}</strong></td>"
                                )

                                if diff_key == "values_changed":
                                    old_val = html.escape(
                                        str(details.get("old_value", ""))
                                    )
                                    new_val = html.escape(
                                        str(details.get("new_value", ""))
                                    )
                                    html_content += f'<td><pre class="removed">{old_val}</pre></td><td><pre class="added">{new_val}</pre></td></tr>'
                                elif diff_key == "dictionary_item_added":
                                    content = extract_path_value(post_json, path)
                                    val_to_dump = (
                                        content if content is not None else details
                                    )
                                    val_to_dump = prune_excluded(
                                        val_to_dump, path, exclude_paths
                                    )
                                    html_content += f'<td><pre class="added">{html.escape(json.dumps(val_to_dump, indent=2))}</pre></td></tr>'
                                elif diff_key == "dictionary_item_removed":
                                    content = extract_path_value(pre_json, path)
                                    val_to_dump = (
                                        content if content is not None else details
                                    )
                                    val_to_dump = prune_excluded(
                                        val_to_dump, path, exclude_paths
                                    )
                                    html_content += f'<td><pre class="removed">{html.escape(json.dumps(val_to_dump, indent=2))}</pre></td></tr>'
                                else:
                                    pruned_details = prune_excluded(
                                        details, path, exclude_paths
                                    )
                                    html_content += f"<td><pre>{html.escape(json.dumps(pruned_details, indent=2))}</pre></td></tr>"
                        else:
                            for path in diff_data:
                                readable_path = html.escape(
                                    str(path).replace("root", "YANG")
                                )
                                html_content += (
                                    f"<tr><td><strong>{readable_path}</strong></td>"
                                )
                                target_json = (
                                    post_json if "added" in diff_key else pre_json
                                )
                                css_render = (
                                    "added" if "added" in diff_key else "removed"
                                )
                                content = extract_path_value(target_json, path)
                                if content is not None:
                                    content = prune_excluded(
                                        content, path, exclude_paths
                                    )
                                    html_content += f'<td><pre class="{css_render}">{html.escape(json.dumps(content, indent=2))}</pre></td></tr>'
                                else:
                                    html_content += f'<td><pre class="{css_render}">Element structure updated at this layer path.</pre></td></tr>'

                        html_content += "</table>"

            html_content += """
            </body>
            </html>
            """

            os.makedirs(os.path.dirname(html_path) or ".", exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            log.info(f"Visual delta report saved to {html_path}")

            enforce_invariants = self.parent.parameters.get(
                "enforce_invariants", False
            ) or os.getenv("SROS_ENFORCE_INVARIANTS", "false").lower() in (
                "true",
                "1",
                "yes",
            )

            if enforce_invariants:
                all_regressions = []
                for dev_label, diff, pre_json, post_json in all_diff_sections:
                    regs = check_operational_invariants(
                        pre_json, post_json, dev_label, diff=diff
                    )
                    all_regressions.extend(regs)

                if all_regressions:
                    reg_summary = "; ".join(all_regressions)
                    log.error(f"Invariant check failed: {reg_summary}")
                    self.failed(
                        f"Operational regression detected: {reg_summary}. Report: {html_path}"
                    )
                else:
                    log.info(
                        f"Invariant check passed. Delta report saved to {html_path}"
                    )
                    self.passed(
                        f"Operational invariants satisfied. Report saved to {html_path}"
                    )
            else:
                self.failed(
                    f"Network state drift detected. Report saved to {html_path}"
                )


def _resolve_template_var(val, env_key=None, default_val=None):
    if val is None:
        return os.getenv(env_key, default_val) if env_key else default_val
    val_str = str(val).strip()
    if val_str.startswith("{{") and val_str.endswith("}}"):
        if env_key and os.getenv(env_key):
            return os.getenv(env_key)
        m = re.search(r"default\(\s*['\"]([^'\"]+)['\"]", val_str)
        if m:
            return m.group(1)
        return os.getenv(env_key, default_val) if env_key else default_val
    return val


def load_testbed_from_ansible_inventory(inventory_file):
    """
    Dynamically converts an Ansible YAML inventory into a native pyATS Testbed schema.
    Enables single-source-of-truth network definitions without maintaining duplicate testbed files.
    """
    import yaml

    with open(inventory_file, "r", encoding="utf-8") as f:
        inv = yaml.safe_load(f) or {}

    global_vars = {}
    if isinstance(inv.get("all"), dict) and isinstance(inv["all"].get("vars"), dict):
        global_vars.update(inv["all"]["vars"])
    elif isinstance(inv.get("vars"), dict):
        global_vars.update(inv["vars"])

    # Also resolve variables from adjacent group_vars/all.yml or group_vars/all.yaml if present
    inv_dir = os.path.dirname(os.path.abspath(inventory_file))
    for gv_name in ["all.yml", "all.yaml"]:
        gv_path = os.path.join(inv_dir, "group_vars", gv_name)
        if os.path.exists(gv_path):
            try:
                with open(gv_path, "r", encoding="utf-8") as gvf:
                    gv_data = yaml.safe_load(gvf) or {}
                    if isinstance(gv_data, dict):
                        for k, v in gv_data.items():
                            if k not in global_vars:
                                global_vars[k] = v
            except Exception as e:
                log.debug(f"Could not load group_vars from {gv_path}: {e}")

    default_user = _resolve_template_var(
        global_vars.get("ansible_user"), "SROS_USER", "admin"
    )
    default_password = _resolve_template_var(
        global_vars.get("ansible_password"), "SROS_PASSWORD", "NokiaSros1!"
    )

    def _extract_hosts(group_dict):
        hosts = {}
        if not isinstance(group_dict, dict):
            return hosts
        if "hosts" in group_dict:
            if isinstance(group_dict["hosts"], dict):
                hosts.update(group_dict["hosts"])
            elif isinstance(group_dict["hosts"], list):
                for h in group_dict["hosts"]:
                    if isinstance(h, str):
                        hosts[h] = {}
                    elif isinstance(h, dict):
                        hosts.update(h)
        if "children" in group_dict and isinstance(group_dict["children"], dict):
            for child_val in group_dict["children"].values():
                hosts.update(_extract_hosts(child_val))
        return hosts

    all_hosts = _extract_hosts(inv.get("all", inv))
    devices = {}
    for dev_name, dev_vars in all_hosts.items():
        if not dev_vars or not isinstance(dev_vars, dict):
            dev_vars = {}
        dev_ip = str(dev_vars.get("ansible_host", dev_name))
        user = _resolve_template_var(
            dev_vars.get("ansible_user", default_user), "SROS_USER", "admin"
        )
        pwd = _resolve_template_var(
            dev_vars.get("ansible_password", default_password),
            "SROS_PASSWORD",
            "NokiaSros1!",
        )

        # Unicon CLI connections strictly use SSH (port 22 unless ansible_ssh_port is defined)
        ssh_port = dev_vars.get("ansible_ssh_port") or dev_vars.get("cli_port") or 22
        # NETCONF connection uses port 830 or SROS_PORT
        netconf_raw_port = (
            dev_vars.get("ansible_port")
            or global_vars.get("ansible_port")
            or os.getenv("SROS_PORT", "830")
        )
        netconf_port = _resolve_template_var(netconf_raw_port, "SROS_PORT", 830)

        cli_conn = {"protocol": "ssh", "ip": dev_ip}
        if ssh_port and int(ssh_port) != 22:
            cli_conn["port"] = int(ssh_port)

        devices[dev_name] = {
            "os": "sros",
            "type": "router",
            "connections": {
                "defaults": {"class": "unicon.Unicon", "via": "cli"},
                "cli": cli_conn,
                "netconf": {
                    "protocol": "netconf",
                    "ip": dev_ip,
                    "port": int(netconf_port),
                },
            },
            "credentials": {"default": {"username": user, "password": pwd}},
        }

    return {"testbed": {"name": "ansible-synthesized-testbed"}, "devices": devices}


class CommonCleanup(aetest.CommonCleanup):
    @aetest.subsection
    def disconnect(self, testbed):
        if testbed and getattr(testbed, "devices", None):
            for dev_name, dev in testbed.devices.items():
                try:
                    if getattr(dev, "is_connected", lambda: True)():
                        dev.disconnect()
                except Exception as exc:
                    log.debug(f"[{dev_name}] Disconnect cleanup notice: {exc}")


# --- RUNTIME CONTROLLER ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Nokia SROS multi-mode validation platform."
    )
    parser.add_argument(
        "--mode",
        choices=["pre", "post"],
        required=True,
        help="Specify 'pre' for baseline check or 'post' for change verification & diffing.",
    )
    parser.add_argument(
        "--testbed",
        default=None,
        help="Path to pyATS testbed YAML file (default: pyats_testbed.yml or testbed.yml).",
    )
    parser.add_argument(
        "--inventory",
        default=None,
        help="Path to Ansible inventory YAML file to dynamically synthesize pyATS testbed.",
    )
    parser.add_argument(
        "--limit",
        default=None,
        help="Comma-separated device names or patterns to limit verification scope.",
    )
    parser.add_argument(
        "--paths",
        default="/nokia-state:state",
        help="Comma-separated YANG state paths to query (default: '/nokia-state:state').",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Maximum concurrent worker threads for state collection (default: 10).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Device connection and command execution timeout in seconds (default: 180).",
    )
    parser.add_argument(
        "--enforce-invariants",
        action="store_true",
        help="Enforce operational invariant assertions (fail on regressions such as dropped peers or downed interfaces, permit valid configuration additions).",
    )

    args, unknown = parser.parse_known_args()

    testbed_data = None
    if args.inventory:
        log.info(
            f"Dynamically synthesizing pyATS testbed from Ansible inventory: {args.inventory}"
        )
        testbed_data = load_testbed_from_ansible_inventory(args.inventory)
    elif args.testbed:
        testbed_data = args.testbed
    elif os.path.exists("ansible/inventory.yml"):
        log.info(
            "Using ansible/inventory.yml as single source of truth for pyATS testbed"
        )
        testbed_data = load_testbed_from_ansible_inventory("ansible/inventory.yml")
    elif os.path.exists("pyats_testbed.yml"):
        testbed_data = "pyats_testbed.yml"
    elif os.path.exists("testbed.yml"):
        testbed_data = "testbed.yml"
    else:
        testbed_data = "pyats_testbed.yml"

    testbed = topology.loader.load(testbed_data)

    if args.limit:
        limit_patterns = [p.strip().lower() for p in args.limit.split(",") if p.strip()]
        filtered_devices = {
            d_name: dev
            for d_name, dev in testbed.devices.items()
            if any(
                pat in d_name.lower() or pat == d_name.lower() for pat in limit_patterns
            )
        }
        if not filtered_devices:
            log.error(
                f"No testbed devices matched --limit criteria '{args.limit}'. Available devices: {list(testbed.devices.keys())}"
            )
            sys.exit(1)
        log.info(
            f"Limiting pyATS verification to {len(filtered_devices)} device(s): {list(filtered_devices.keys())}"
        )
        testbed.devices = filtered_devices

    yang_paths = [p.strip() for p in args.paths.split(",") if p.strip()]

    base_tests = [
        ConnectivityTest,
        InterfaceStateTest,
        ISISTest,
        OSPFTest,
        BGPTest,
        SnapshotState,
    ]
    if args.mode == "pre":
        test_pipeline = base_tests
    elif args.mode == "post":
        test_pipeline = base_tests + [VerifyChanges]

    result = aetest.main(
        testbed=testbed,
        testcases=test_pipeline,
        mode=args.mode,
        enforce_invariants=args.enforce_invariants,
        yang_paths=yang_paths,
        max_workers=args.max_workers,
        timeout=args.timeout,
        argv=unknown,
    )

    if hasattr(aetest, "exit_cli_code"):
        aetest.exit_cli_code(result)

    if result is not None:
        passed = getattr(result, "is_passed", None)
        if callable(passed) and not passed():
            sys.exit(1)
        elif str(result).lower() not in ("passed", "pass", "ok"):
            sys.exit(1)
    sys.exit(0)
