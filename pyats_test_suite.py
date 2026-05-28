#!/usr/bin/env python3
import json
import logging
import argparse
import os
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

# --- AETEST STRUCTURAL BLOCKS ---
class CommonSetup(aetest.CommonSetup):
    @aetest.subsection
    def connect_to_devices(self, testbed):
        testbed.connect()

class ConnectivityTest(aetest.Testcase):
    @aetest.test
    def check_version(self):
        dev = self.parent.parameters['testbed'].devices['r2']
        output = dev.execute('show version')
        if 'Nokia' in output:
            self.passed("Nokia OS detected successfully")
        else:
            self.failed("Nokia OS not detected")

class ISISTest(aetest.Testcase):
    @aetest.test
    def verify_isis_interfaces(self):
        dev = self.parent.parameters['testbed'].devices['r2']
        output = dev.execute('show router isis interface')
        if 'Up' in output:
            self.passed("ISIS Interfaces are Up")
        else:
            self.failed("No Up ISIS interfaces found")

class SnapshotState(aetest.Testcase):
    @aetest.test
    def capture_with_pysros(self, testbed):
        dev = testbed.devices['r2']
        mode = self.parent.parameters.get('mode', 'pre')
        filename = f"nokia_snapshot_{mode}.json"

        conn = sros_connect(
            host=str(dev.connections.cli.ip),
            username=dev.credentials.default.username,
            password=dev.credentials.default.password,
            hostkey_verify=False,
            timeout=180,
        )

        # Pull clean model-driven structural interface arrays straight from YANG
        raw_state = conn.running.get('/nokia-state:state')
        clean_data = nokia_to_dict(raw_state)

        os.makedirs("snapshots", exist_ok=True)
        target_path = os.path.join("snapshots", filename)

        with open(target_path, "w") as f:
            json.dump(clean_data, f, indent=2)

        log.info(f"Success! Structured JSON state saved to {target_path}")
        self.passed(f"Data captured and saved to {target_path}")

class VerifyChanges(aetest.Testcase):
    @aetest.test
    def compare_snapshots(self):
        pre_path = "snapshots/nokia_snapshot_pre.json"
        post_path = "snapshots/nokia_snapshot_post.json"
        html_path = "snapshots/network_drift_report.html"

        if not os.path.exists(pre_path) or not os.path.exists(post_path):
            self.failed(f"Missing snapshots for comparison. Ensure both pre and post states exist.")

        with open(pre_path, "r") as f:
            pre_json = json.load(f)
        with open(post_path, "r") as f:
            post_json = json.load(f)

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

        # Calculate semantic configurations using deep semantic analysis
        diff = DeepDiff(
            pre_json,
            post_json,
            ignore_order=True,
            exclude_regex_paths=exclude_paths
        )

        if not diff:
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
                            
                            # Deep Lookup optimization for completely new keys added to state
                            elif diff_key == 'dictionary_item_added':
                                try:
                                    content = eval(str(path).replace("root", "post_json"))
                                    html_content += f'<td><pre class="added">{json.dumps(content, indent=2)}</pre></td></tr>'
                                except Exception:
                                    html_content += f'<td><pre class="added">{json.dumps(details, indent=2)}</pre></td></tr>'
                            
                            # Deep Lookup optimization for vanished keys dropped from state
                            elif diff_key == 'dictionary_item_removed':
                                try:
                                    content = eval(str(path).replace("root", "pre_json"))
                                    html_content += f'<td><pre class="removed">{json.dumps(content, indent=2)}</pre></td></tr>'
                                except Exception:
                                    html_content += f'<td><pre class="removed">{json.dumps(details, indent=2)}</pre></td></tr>'
                                    
                            else:
                                html_content += f'<td><pre>{json.dumps(details, indent=2)}</pre></td></tr>'
                    else:
                        # Falling back dynamically to Set processing structures if dealing with sequences
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
    
    args, unknown = parser.parse_known_args()
    testbed = topology.loader.load('testbed.yml')

    if args.mode == 'pre':
        test_pipeline = [ConnectivityTest, ISISTest, SnapshotState]
    elif args.mode == 'post':
        test_pipeline = [ConnectivityTest, ISISTest, SnapshotState, VerifyChanges]

    aetest.main(
        testbed=testbed,
        testcases=test_pipeline,
        mode=args.mode,
        argv=unknown
    )