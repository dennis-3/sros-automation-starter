#!/usr/bin/env python3
import json
import logging
import argparse
import sys
import os
import re
import shutil
import concurrent.futures
from pyats import aetest
from pyats import topology
from pysros.management import connect as sros_connect
from deepdiff import DeepDiff

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
            key = token[0] if token[0] != '' else token[1]

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

def nokia_to_dict(data):
    """
    Recursively unwraps Nokia YANG container and leaf structural metadata objects 
    into standard Python native dictionary and array data frameworks.
    """
    if hasattr(data, 'keys'):
        return {str(k): nokia_to_dict(data[k]) for k in data.keys()}
    elif isinstance(data, list):
        return [nokia_to_dict(item) for item in data]
    elif hasattr(data, 'value'):
        return nokia_to_dict(data.value)
    elif hasattr(data, '__str__') and not isinstance(data, (str, int, float, bool, type(None))):
        return str(data)
    else:
        return data

def _capture_single_device(dev_name, dev, mode, yang_paths, timeout=180):
    """
    Connects to a single SR OS device and captures model-driven YANG state snapshots.
    """
    filename = f"nokia_snapshot_{dev_name}_{mode}.json"
    target_path = os.path.join("snapshots", filename)

    host_ip = str(getattr(getattr(dev, 'connections', None), 'cli', dev).ip) if hasattr(dev, 'connections') else str(dev)
    username = dev.credentials.default.username
    password = dev.credentials.default.password

    conn = None
    try:
        conn = sros_connect(
            host=host_ip,
            username=username,
            password=password,
            hostkey_verify=False,
            timeout=timeout,
        )

        if len(yang_paths) == 1 and yang_paths[0] == '/nokia-state:state':
            raw_state = conn.running.get('/nokia-state:state')
            clean_data = nokia_to_dict(raw_state)
        else:
            clean_data = {}
            for path in yang_paths:
                try:
                    sub_state = conn.running.get(path)
                    clean_data[path] = nokia_to_dict(sub_state)
                except Exception as path_err:
                    log.warning(f"[{dev_name}] Could not retrieve path '{path}': {path_err}")
                    clean_data[path] = None

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, indent=2)

        log.info(f"Success! [{dev_name}] Structured JSON state saved to {target_path}")
        return dev_name, True, None
    except Exception as exc:
        log.error(f"Failed to capture state from [{dev_name}]: {exc}")
        return dev_name, False, str(exc)
    finally:
        if conn is not None and hasattr(conn, 'disconnect'):
            try:
                conn.disconnect()
            except Exception as close_exc:
                log.debug(f"[{dev_name}] NETCONF disconnect cleanup notice: {close_exc}")

def _run_parallel_device_command(testbed, command, max_workers=10):
    """
    Executes a CLI command in parallel across all devices in testbed.devices
    to avoid serial O(N) blocking delays in scaled topologies.
    Returns a dict mapping dev_name -> (output, exc).
    """
    results = {}
    if not testbed or not getattr(testbed, 'devices', None):
        return results

    worker_count = min(max_workers, max(1, len(testbed.devices)))

    def _exec(name, d):
        try:
            out = d.execute(command)
            return name, out, None
        except Exception as exc:
            return name, None, exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_exec, name, dev) for name, dev in testbed.devices.items()]
        for f in concurrent.futures.as_completed(futures):
            name, out, exc = f.result()
            results[name] = (out, exc)

    return results

# --- AETEST STRUCTURAL BLOCKS ---
class CommonSetup(aetest.CommonSetup):
    @aetest.subsection
    def connect_to_devices(self, testbed):
        max_workers = self.parent.parameters.get('max_workers', 10)
        worker_count = min(max_workers, max(1, len(testbed.devices)))
        log.info(f"Connecting to {len(testbed.devices)} device(s) concurrently (max_workers={worker_count})...")
        try:
            testbed.connect(max_workers=worker_count)
        except Exception as exc:
            log.warning(f"Bulk testbed connect reported: {exc}. Verifying individual connections...")
            for dev_name, dev in testbed.devices.items():
                if not getattr(dev, 'is_connected', lambda: False)():
                    try:
                        dev.connect()
                    except Exception as dev_exc:
                        log.error(f"[{dev_name}] Connection failed: {dev_exc}")


class ConnectivityTest(aetest.Testcase):
    @aetest.test
    def check_version(self):
        testbed = self.parent.parameters['testbed']
        max_workers = self.parent.parameters.get('max_workers', 10)
        results = _run_parallel_device_command(testbed, 'show version', max_workers=max_workers)
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] Failed to execute 'show version': {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
            elif output and ('Nokia' in output or 'TiMOS' in output):
                log.info(f"[{dev_name}] Nokia SR OS detected successfully")
            else:
                log.error(f"[{dev_name}] Nokia OS not detected in output")
                failed_devices.append(f"{dev_name} (unrecognized OS)")

        if failed_devices:
            self.failed(f"Connectivity check failed on devices: {', '.join(failed_devices)}")
        else:
            self.passed("Nokia OS detected successfully on all testbed devices")

class InterfaceStateTest(aetest.Testcase):
    @aetest.test
    def verify_interfaces(self):
        testbed = self.parent.parameters['testbed']
        max_workers = self.parent.parameters.get('max_workers', 10)
        results = _run_parallel_device_command(testbed, 'show router interface', max_workers=max_workers)
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] Interface check exception: {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
                continue
            down_interfaces = []
            for line in output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith(('=', '-', 'Interface Table', 'Interfaces', 'IP-Address', 'Interface-Name')):
                    continue
                parts = line_clean.split()
                if len(parts) >= 3:
                    name, adm, opr = parts[0], parts[1], parts[2]
                    if adm.lower() == 'up':
                        opr_parts = opr.split('/')
                        if opr_parts[0].lower() == 'down':
                            down_interfaces.append(f"{name} (adm:{adm}, opr:{opr})")
            if down_interfaces:
                failed_devices.append(f"{dev_name}: [down: {', '.join(down_interfaces)}]")
            else:
                log.info(f"[{dev_name}] All admin Up interfaces are operationally Up")

        if failed_devices:
            self.failed(f"Interface operational check failed on devices: {'; '.join(failed_devices)}")
        else:
            self.passed("All administrative Up interfaces are operationally Up across all testbed devices")

class ISISTest(aetest.Testcase):
    @aetest.test
    def verify_isis_interfaces(self):
        testbed = self.parent.parameters['testbed']
        max_workers = self.parent.parameters.get('max_workers', 10)
        results = _run_parallel_device_command(testbed, 'show router isis interface', max_workers=max_workers)
        configured_count = 0
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] ISIS check exception: {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
                continue
            output_lower = output.lower()
            if "not configured" in output_lower or "disabled" in output_lower or "no matching" in output_lower or "not running" in output_lower:
                log.info(f"[{dev_name}] ISIS is not configured; skipping")
                continue

            configured_count += 1
            down_isis = []
            up_count = 0
            for line in output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith(('=', '-', 'Router (', 'Interface', 'Total')):
                    continue
                parts = line_clean.split()
                if len(parts) >= 4:
                    if 'down' in line_clean.lower():
                        down_isis.append(parts[0])
                    elif 'up' in line_clean.lower():
                        up_count += 1

            if down_isis:
                failed_devices.append(f"{dev_name} (down ISIS interfaces: {', '.join(down_isis)})")
            elif up_count == 0:
                failed_devices.append(f"{dev_name} (ISIS enabled but 0 Up interfaces detected)")
            else:
                log.info(f"[{dev_name}] {up_count} ISIS interface(s) are operationally Up")

        if failed_devices:
            self.failed(f"ISIS interface verification failed on devices: {'; '.join(failed_devices)}")
        elif configured_count == 0:
            self.passed("ISIS is not configured on any testbed devices; check passed")
        else:
            self.passed(f"ISIS interfaces verified Up across {configured_count} configured device(s)")

class OSPFTest(aetest.Testcase):
    @aetest.test
    def verify_ospf_neighbors(self):
        testbed = self.parent.parameters['testbed']
        max_workers = self.parent.parameters.get('max_workers', 10)
        results = _run_parallel_device_command(testbed, 'show router ospf neighbor', max_workers=max_workers)
        configured_count = 0
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] OSPF check exception: {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
                continue
            output_lower = output.lower()
            if "not configured" in output_lower or "disabled" in output_lower or "no matching" in output_lower or "not running" in output_lower:
                log.info(f"[{dev_name}] OSPF is not configured; skipping")
                continue

            configured_count += 1
            degraded_neighbors = []
            full_count = 0
            for line in output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith(('=', '-', 'OSPFv', 'Interface-Name', 'No. of')):
                    continue
                parts = line_clean.split()
                if len(parts) >= 3:
                    state = parts[2].lower()
                    if state in ('down', 'init', 'attempt'):
                        degraded_neighbors.append(f"{parts[0]}:{parts[1]} (state: {parts[2]})")
                    elif state in ('full', '2-way'):
                        full_count += 1

            if degraded_neighbors:
                failed_devices.append(f"{dev_name} (degraded OSPF neighbors: {', '.join(degraded_neighbors)})")
            else:
                log.info(f"[{dev_name}] {full_count} OSPF neighbor(s) active")

        if failed_devices:
            self.failed(f"OSPF neighbor verification failed on devices: {'; '.join(failed_devices)}")
        elif configured_count == 0:
            self.passed("OSPF is not configured on any testbed devices; check passed")
        else:
            self.passed(f"OSPF neighbors verified across {configured_count} configured device(s)")

class BGPTest(aetest.Testcase):
    @aetest.test
    def verify_bgp_peers(self):
        testbed = self.parent.parameters['testbed']
        max_workers = self.parent.parameters.get('max_workers', 10)
        results = _run_parallel_device_command(testbed, 'show router bgp summary', max_workers=max_workers)
        configured_count = 0
        failed_devices = []
        for dev_name, (output, exc) in results.items():
            if exc:
                log.error(f"[{dev_name}] BGP check exception: {exc}")
                failed_devices.append(f"{dev_name} ({exc})")
                continue
            output_lower = output.lower()
            if "not configured" in output_lower or "disabled" in output_lower or "no matching" in output_lower or "bgp is not" in output_lower:
                log.info(f"[{dev_name}] BGP is not configured; skipping")
                continue

            configured_count += 1
            degraded_peers = []
            established_count = 0
            for line in output.splitlines():
                line_clean = line.strip()
                if not line_clean or line_clean.startswith(('=', '-', 'BGP Router', 'BGP Admin', 'Total', 'Neighbor', 'Description', 'ServiceId')):
                    continue
                parts = line_clean.split()
                if len(parts) >= 2:
                    last_token = parts[-1].lower()
                    if last_token in ('idle', 'active', 'connect', 'opensent', 'openconfirm'):
                        degraded_peers.append(f"{parts[0]} (state: {parts[-1]})")
                    elif '/' in last_token or last_token == 'established':
                        established_count += 1

            if degraded_peers:
                failed_devices.append(f"{dev_name} (degraded BGP peers: {', '.join(degraded_peers)})")
            else:
                log.info(f"[{dev_name}] {established_count} BGP peer(s) established")

        if failed_devices:
            self.failed(f"BGP peer verification failed on devices: {'; '.join(failed_devices)}")
        elif configured_count == 0:
            self.passed("BGP is not configured on any testbed devices; check passed")
        else:
            self.passed(f"BGP sessions verified across {configured_count} configured device(s)")


class SnapshotState(aetest.Testcase):
    @aetest.test
    def capture_with_pysros(self, testbed):
        mode = self.parent.parameters.get('mode', 'pre')
        yang_paths = self.parent.parameters.get('yang_paths', ['/nokia-state:state'])
        max_workers = self.parent.parameters.get('max_workers', 10)
        timeout = self.parent.parameters.get('timeout', 180)
        os.makedirs("snapshots", exist_ok=True)

        worker_count = min(max_workers, max(1, len(testbed.devices)))
        log.info(f"Starting parallel snapshot capture across {len(testbed.devices)} device(s) with {worker_count} workers (timeout={timeout}s)...")

        failures = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_dev = {
                executor.submit(_capture_single_device, dev_name, dev, mode, yang_paths, timeout): dev_name
                for dev_name, dev in testbed.devices.items()
            }
            for future in concurrent.futures.as_completed(future_to_dev):
                dev_name, success, error_msg = future.result()
                if not success:
                    failures[dev_name] = error_msg

        # Maintain default snapshot file for single-device verification workflows
        if testbed.devices:
            first_name = next(iter(testbed.devices.keys()))
            legacy_source = os.path.join("snapshots", f"nokia_snapshot_{first_name}_{mode}.json")
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
        testbed = self.parent.parameters.get('testbed')
        html_path = "snapshots/network_drift_report.html"

        exclude_paths = [
            # --- System & Time Volatiles ---
            r"root\[.*\]\['statistics'\]",          # All packet/byte counters
            r"root\[.*\]\['last-oper-change'\]",    # Time stamps for status changes
            r"root\[.*\]\['timer'\]",               # Protocol hold/dead/hello timers
            r"root\[.*\]\['uptime'\]",              # System and process uptimes
            r"root\[.*\]\['allocated-memory'\]",    # Dynamic RAM fluctuations
            
            # --- Hardware & Resource Volatiles ---
            r"root\['system'\]\['cpu'\]",            # Live CPU utilization percentages
            r"root\['system'\]\['memory'\]",         # Live memory usage indexes
            r"root\[.*\]\['temperature'\]",         # Environmental sensor readings
            
            # --- Protocol Keepalive / Serial Volatiles ---
            r"root\[.*\]\['sequence-number'\]",     # OSPF/ISIS LSA sequence numbers
            r"root\[.*\]\['bgp'\]\[.*\]\['keepalive'\]" # BGP message tracking counters
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
            self.failed("Missing snapshots for comparison. Ensure both pre and post states exist.")

        drift_detected = False
        all_diff_sections = []

        def _compute_diff(label, pre_p, post_p):
            try:
                with open(pre_p, "r", encoding="utf-8") as f:
                    pre_j = json.load(f)
                with open(post_p, "r", encoding="utf-8") as f:
                    post_j = json.load(f)

                d = DeepDiff(
                    pre_j,
                    post_j,
                    ignore_order=True,
                    exclude_regex_paths=exclude_paths
                )
                return label, d, pre_j, post_j, None
            except Exception as e:
                return label, None, None, None, str(e)

        max_workers = self.parent.parameters.get('max_workers', 10)
        worker_count = min(max_workers, max(1, len(devices_to_compare)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_compute_diff, lbl, p_pre, p_post) for lbl, p_pre, p_post in devices_to_compare]
            for f in concurrent.futures.as_completed(futures):
                dev_label, diff, pre_json, post_json, err = f.result()
                if err:
                    log.error(f"[{dev_label}] State snapshot diff comparison failed: {err}")
                elif diff:
                    drift_detected = True
                    all_diff_sections.append((dev_label, diff, pre_json, post_json))

        # Sort sections by device label for deterministic reporting
        all_diff_sections.sort(key=lambda s: s[0])

        if not drift_detected:
            self.passed("✅ No operational changes detected. The network is stable!")
        else:
            log.error("⚠️ Network drift detected! Rendering isolated change exceptions to HTML...")
            
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
                    table { width: 100%; border-collapse: collapse; margin-bottom: 20px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
                    th, td { padding: 12px; text-align: left; border: 1px solid #dee2e6; font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; font-size: 0.9em; }
                    th { background-color: #f1f3f5; color: #495057; font-family: inherit; font-size: 0.95em; }
                    pre { margin: 0; white-space: pre-wrap; }
                </style>
            </head>
            <body>
                <h2>⚠️ Network Drift Exception Report (Isolated Delta Only)</h2>
            """

            sections = [
                ('dictionary_item_added', 'Added Elements', 'added'),
                ('dictionary_item_removed', 'Removed Elements', 'removed'),
                ('values_changed', 'Modified Values', 'changed'),
                ('iterable_item_added', 'Added Array Items', 'added'),
                ('iterable_item_removed', 'Removed Array Items', 'removed')
            ]

            for dev_label, diff, pre_json, post_json in all_diff_sections:
                html_content += f'<h3>Device: {dev_label}</h3>'
                for diff_key, header_title, css_class in sections:
                    if diff_key in diff:
                        html_content += f'<div class="change-type {css_class}">{header_title}</div>'
                        html_content += '<table><tr><th style="width: 35%;">YANG Data Path</th>'
                        
                        if diff_key == 'values_changed':
                            html_content += '<th>Pre-Change Value</th><th>Post-Change Value</th></tr>'
                        else:
                            html_content += '<th>Element Details Content</th></tr>'

                        diff_data = diff[diff_key]

                        if isinstance(diff_data, dict):
                            for path, details in diff_data.items():
                                readable_path = str(path).replace("root", "YANG")
                                html_content += f'<tr><td><strong>{readable_path}</strong></td>'
                                
                                if diff_key == 'values_changed':
                                    old_val = details.get('old_value', '')
                                    new_val = details.get('new_value', '')
                                    html_content += f'<td><pre class="removed">{old_val}</pre></td><td><pre class="added">{new_val}</pre></td></tr>'
                                elif diff_key == 'dictionary_item_added':
                                    content = extract_path_value(post_json, path)
                                    val_to_dump = content if content is not None else details
                                    html_content += f'<td><pre class="added">{json.dumps(val_to_dump, indent=2)}</pre></td></tr>'
                                elif diff_key == 'dictionary_item_removed':
                                    content = extract_path_value(pre_json, path)
                                    val_to_dump = content if content is not None else details
                                    html_content += f'<td><pre class="removed">{json.dumps(val_to_dump, indent=2)}</pre></td></tr>'
                                else:
                                    html_content += f'<td><pre>{json.dumps(details, indent=2)}</pre></td></tr>'
                        else:
                            for path in diff_data:
                                readable_path = str(path).replace("root", "YANG")
                                html_content += f'<tr><td><strong>{readable_path}</strong></td>'
                                target_json = post_json if 'added' in diff_key else pre_json
                                css_render = 'added' if 'added' in diff_key else 'removed'
                                content = extract_path_value(target_json, path)
                                if content is not None:
                                    html_content += f'<td><pre class="{css_render}">{json.dumps(content, indent=2)}</pre></td></tr>'
                                else:
                                    html_content += f'<td><pre class="{css_render}">Element structure updated at this layer path.</pre></td></tr>'
                        
                        html_content += '</table>'

            html_content += """
            </body>
            </html>
            """

            os.makedirs(os.path.dirname(html_path) or ".", exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            log.info(f"Visual delta report saved to {html_path}")
            self.failed(f"Network state has drifted! Isolated exception details here: {html_path}")

def load_testbed_from_ansible_inventory(inventory_file):
    """
    Dynamically converts an Ansible YAML inventory into a native pyATS Testbed schema.
    Enables single-source-of-truth network definitions without maintaining duplicate testbed files.
    """
    import yaml
    with open(inventory_file, 'r', encoding='utf-8') as f:
        inv = yaml.safe_load(f) or {}

    global_vars = {}
    if isinstance(inv.get('all'), dict) and isinstance(inv['all'].get('vars'), dict):
        global_vars = inv['all']['vars']
    elif isinstance(inv.get('vars'), dict):
        global_vars = inv['vars']

    default_user = os.getenv('SROS_USER') or global_vars.get('ansible_user') or 'admin'
    default_password = os.getenv('SROS_PASSWORD') or global_vars.get('ansible_password') or 'NokiaSros1!'

    def _extract_hosts(group_dict):
        hosts = {}
        if not isinstance(group_dict, dict):
            return hosts
        if 'hosts' in group_dict and isinstance(group_dict['hosts'], dict):
            hosts.update(group_dict['hosts'])
        if 'children' in group_dict and isinstance(group_dict['children'], dict):
            for child_val in group_dict['children'].values():
                hosts.update(_extract_hosts(child_val))
        return hosts

    all_hosts = _extract_hosts(inv.get('all', inv))
    devices = {}
    for dev_name, dev_vars in all_hosts.items():
        if not dev_vars or not isinstance(dev_vars, dict):
            dev_vars = {}
        dev_ip = str(dev_vars.get('ansible_host', dev_name))
        user = dev_vars.get('ansible_user', default_user)
        pwd = dev_vars.get('ansible_password', default_password)
        port = dev_vars.get('ansible_port') or global_vars.get('ansible_port') or 22

        cli_conn = {
            'protocol': 'ssh',
            'ip': dev_ip
        }
        if port and int(port) != 22:
            cli_conn['port'] = int(port)

        devices[dev_name] = {
            'os': 'sros',
            'type': 'router',
            'connections': {
                'defaults': {
                    'class': 'unicon.Unicon'
                },
                'cli': cli_conn
            },
            'credentials': {
                'default': {
                    'username': user,
                    'password': pwd
                }
            }
        }

    return {
        'testbed': {
            'name': 'ansible-synthesized-testbed'
        },
        'devices': devices
    }

class CommonCleanup(aetest.CommonCleanup):
    @aetest.subsection
    def disconnect(self, testbed):
        testbed.disconnect()

# --- RUNTIME CONTROLLER ---
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Nokia SROS multi-mode validation platform.")
    parser.add_argument('--mode', choices=['pre', 'post'], required=True,
                        help="Specify 'pre' for baseline check or 'post' for change verification & diffing.")
    parser.add_argument('--testbed', default=None,
                        help="Path to pyATS testbed YAML file (default: pyats_testbed.yml or testbed.yml).")
    parser.add_argument('--inventory', default=None,
                        help="Path to Ansible inventory YAML file to dynamically synthesize pyATS testbed.")
    parser.add_argument('--limit', default=None,
                        help="Comma-separated device names or patterns to limit verification scope.")
    parser.add_argument('--paths', default='/nokia-state:state',
                        help="Comma-separated YANG state paths to query (default: '/nokia-state:state').")
    parser.add_argument('--max-workers', type=int, default=10,
                        help="Maximum concurrent worker threads for state collection (default: 10).")
    parser.add_argument('--timeout', type=int, default=180,
                        help="Device connection and command execution timeout in seconds (default: 180).")

    args, unknown = parser.parse_known_args()

    testbed_data = None
    if args.inventory:
        log.info(f"Dynamically synthesizing pyATS testbed from Ansible inventory: {args.inventory}")
        testbed_data = load_testbed_from_ansible_inventory(args.inventory)
    elif args.testbed:
        testbed_data = args.testbed
    elif os.path.exists('pyats_testbed.yml'):
        testbed_data = 'pyats_testbed.yml'
    elif os.path.exists('testbed.yml'):
        testbed_data = 'testbed.yml'
    elif os.path.exists('ansible/inventory.yml'):
        log.info("pyats_testbed.yml not specified; dynamically synthesizing testbed from ansible/inventory.yml")
        testbed_data = load_testbed_from_ansible_inventory('ansible/inventory.yml')
    else:
        testbed_data = 'pyats_testbed.yml'

    testbed = topology.loader.load(testbed_data)

    if args.limit:
        limit_patterns = [p.strip().lower() for p in args.limit.split(',') if p.strip()]
        filtered_devices = {
            d_name: dev for d_name, dev in testbed.devices.items()
            if any(pat in d_name.lower() or pat == d_name.lower() for pat in limit_patterns)
        }
        if not filtered_devices:
            log.error(f"No testbed devices matched --limit criteria '{args.limit}'. Available devices: {list(testbed.devices.keys())}")
            sys.exit(1)
        log.info(f"Limiting pyATS verification to {len(filtered_devices)} device(s): {list(filtered_devices.keys())}")
        testbed.devices = filtered_devices

    yang_paths = [p.strip() for p in args.paths.split(',') if p.strip()]

    base_tests = [ConnectivityTest, InterfaceStateTest, ISISTest, OSPFTest, BGPTest, SnapshotState]
    if args.mode == 'pre':
        test_pipeline = base_tests
    elif args.mode == 'post':
        test_pipeline = base_tests + [VerifyChanges]

    aetest.main(
        testbed=testbed,
        testcases=test_pipeline,
        mode=args.mode,
        yang_paths=yang_paths,
        max_workers=args.max_workers,
        timeout=args.timeout,
        argv=unknown
    )