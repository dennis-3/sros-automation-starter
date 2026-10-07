#!/usr/bin/env python3
import json
import logging
import argparse
import os
import shutil
import concurrent.futures
from pyats import aetest
from pyats import topology
from pysros.management import connect as sros_connect
from deepdiff import DeepDiff

log = logging.getLogger(__name__)

# --- DATA CONVERSION HELPER ---
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

        with open(target_path, "w") as f:
            json.dump(clean_data, f, indent=2)

        log.info(f"Success! [{dev_name}] Structured JSON state saved to {target_path}")
        return dev_name, True, None
    except Exception as exc:
        log.error(f"Failed to capture state from [{dev_name}]: {exc}")
        return dev_name, False, str(exc)

# --- AETEST STRUCTURAL BLOCKS ---
class CommonSetup(aetest.CommonSetup):
    @aetest.subsection
    def connect_to_devices(self, testbed):
        testbed.connect()

class ConnectivityTest(aetest.Testcase):
    @aetest.test
    def check_version(self):
        testbed = self.parent.parameters['testbed']
        failed_devices = []
        for dev_name, dev in testbed.devices.items():
            try:
                output = dev.execute('show version')
                if 'Nokia' in output or 'TiMOS' in output:
                    log.info(f"[{dev_name}] Nokia SR OS detected successfully")
                else:
                    log.error(f"[{dev_name}] Nokia OS not detected in output")
                    failed_devices.append(f"{dev_name} (unrecognized OS)")
            except Exception as exc:
                log.error(f"[{dev_name}] Failed to execute 'show version': {exc}")
                failed_devices.append(f"{dev_name} ({exc})")

        if failed_devices:
            self.failed(f"Connectivity check failed on devices: {', '.join(failed_devices)}")
        else:
            self.passed("Nokia OS detected successfully on all testbed devices")

class ISISTest(aetest.Testcase):
    @aetest.test
    def verify_isis_interfaces(self):
        testbed = self.parent.parameters['testbed']
        for dev_name, dev in testbed.devices.items():
            try:
                output = dev.execute('show router isis interface')
                if 'Up' in output:
                    log.info(f"[{dev_name}] ISIS Interfaces are Up")
                else:
                    log.warning(f"[{dev_name}] No Up ISIS interfaces found")
            except Exception as exc:
                log.warning(f"[{dev_name}] Could not query ISIS interface state: {exc}")
        self.passed("ISIS Interface verification completed")

class SnapshotState(aetest.Testcase):
    @aetest.test
    def capture_with_pysros(self, testbed):
        mode = self.parent.parameters.get('mode', 'pre')
        yang_paths = self.parent.parameters.get('yang_paths', ['/nokia-state:state'])
        max_workers = self.parent.parameters.get('max_workers', 10)
        os.makedirs("snapshots", exist_ok=True)

        worker_count = min(max_workers, max(1, len(testbed.devices)))
        log.info(f"Starting parallel snapshot capture across {len(testbed.devices)} device(s) with {worker_count} workers...")

        failures = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_dev = {
                executor.submit(_capture_single_device, dev_name, dev, mode, yang_paths): dev_name
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

        for dev_label, pre_path, post_path in devices_to_compare:
            with open(pre_path, "r") as f:
                pre_json = json.load(f)
            with open(post_path, "r") as f:
                post_json = json.load(f)

            diff = DeepDiff(
                pre_json,
                post_json,
                ignore_order=True,
                exclude_regex_paths=exclude_paths
            )

            if diff:
                drift_detected = True
                all_diff_sections.append((dev_label, diff, pre_json, post_json))

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
                                    try:
                                        content = eval(str(path).replace("root", "post_json"))
                                        html_content += f'<td><pre class="added">{json.dumps(content, indent=2)}</pre></td></tr>'
                                    except Exception:
                                        html_content += f'<td><pre class="added">{json.dumps(details, indent=2)}</pre></td></tr>'
                                elif diff_key == 'dictionary_item_removed':
                                    try:
                                        content = eval(str(path).replace("root", "pre_json"))
                                        html_content += f'<td><pre class="removed">{json.dumps(content, indent=2)}</pre></td></tr>'
                                    except Exception:
                                        html_content += f'<td><pre class="removed">{json.dumps(details, indent=2)}</pre></td></tr>'
                                else:
                                    html_content += f'<td><pre>{json.dumps(details, indent=2)}</pre></td></tr>'
                        else:
                            for path in diff_data:
                                readable_path = str(path).replace("root", "YANG")
                                html_content += f'<tr><td><strong>{readable_path}</strong></td>'
                                target_json = post_json if 'added' in diff_key else pre_json
                                css_render = 'added' if 'added' in diff_key else 'removed'
                                try:
                                    content = eval(str(path).replace("root", "target_json"))
                                    html_content += f'<td><pre class="{css_render}">{json.dumps(content, indent=2)}</pre></td></tr>'
                                except Exception:
                                    html_content += f'<td><pre class="{css_render}">Element structure updated at this layer path.</pre></td></tr>'
                        
                        html_content += '</table>'

            html_content += """
            </body>
            </html>
            """

            with open(html_path, "w") as f:
                f.write(html_content)

            log.info(f"Visual delta report saved to {html_path}")
            self.failed(f"Network state has drifted! Isolated exception details here: {html_path}")

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
    parser.add_argument('--paths', default='/nokia-state:state',
                        help="Comma-separated YANG state paths to query (default: '/nokia-state:state').")
    parser.add_argument('--max-workers', type=int, default=10,
                        help="Maximum concurrent worker threads for state collection (default: 10).")

    args, unknown = parser.parse_known_args()

    testbed_file = args.testbed
    if not testbed_file:
        if os.path.exists('pyats_testbed.yml'):
            testbed_file = 'pyats_testbed.yml'
        elif os.path.exists('testbed.yml'):
            testbed_file = 'testbed.yml'
        else:
            testbed_file = 'pyats_testbed.yml'

    testbed = topology.loader.load(testbed_file)
    yang_paths = [p.strip() for p in args.paths.split(',') if p.strip()]

    if args.mode == 'pre':
        test_pipeline = [ConnectivityTest, ISISTest, SnapshotState]
    elif args.mode == 'post':
        test_pipeline = [ConnectivityTest, ISISTest, SnapshotState, VerifyChanges]

    aetest.main(
        testbed=testbed,
        testcases=test_pipeline,
        mode=args.mode,
        yang_paths=yang_paths,
        max_workers=args.max_workers,
        argv=unknown
    )