from pyats import aetest
from genie.testbed import load
from pysros.management import connect as sros_connect
from deepdiff import DeepDiff

import json
import logging

log = logging.getLogger(__name__)

class CommonSetup(aetest.CommonSetup):
    @aetest.subsection
    def connect_to_devices(self, testbed):
        testbed.connect()

# class SnapshotState(aetest.Testcase):
#     @aetest.test
#     def capture_interface_state(self, testbed):
#         dev = testbed.devices['CE-1']

#         log.info("Attempting to capture interface data via Parser...")

#         try:
#             # Instead of .learn(), we use .parse() with a specific Nokia command
#             # This command is more likely to be supported by Genie's SROS library
#             parsed_output = dev.parse('show router interface')

#             log.info("Successfully parsed interface data!")
#             with open("nokia_snapshot.txt", "w") as f:
#                 import json
#                 f.write(json.dumps(parsed_output, indent=2))

#         except Exception as e:
#             log.error(f"Parser failed or command not supported. Error: {e}")
#             log.info("Final Fallback: Raw CLI Execution.")

#             # This always works regardless of Genie support
#             raw = dev.execute('show router interface')
#             with open("nokia_snapshot.txt", "w") as f:
#                 f.write(raw)

# --- DATA CONVERSION HELPER ---
# This function walks through the Nokia 'Container' objects
# and converts them into standard Python dicts/lists.
def nokia_to_dict(data):
    # Case 1: It's a Container or a KeyedList (behaves like a dict)
    if hasattr(data, 'keys'):
        return {str(k): nokia_to_dict(data[k]) for k in data.keys()}

    # Case 2: It's a LeafList or standard list
    elif isinstance(data, list):
        return [nokia_to_dict(item) for item in data]

    # Case 3: It's a Nokia Leaf object
    # We check for the '.value' attribute which contains the actual data
    elif hasattr(data, 'value'):
        return nokia_to_dict(data.value)

    # Case 4: It's a custom Nokia Type (like a Decimal64 or Enum)
    # Most of these can be cast to a string or float safely
    elif hasattr(data, '__str__') and not isinstance(data, (str, int, float, bool, type(None))):
        return str(data)

    # Case 5: It's already a standard Python type
    else:
        return data

class SnapshotState(aetest.Testcase):
    @aetest.test
    def capture_with_pysros(self, testbed):
        dev = testbed.devices['CE-1']

        # We use the credentials from your pyATS testbed!
        conn = sros_connect(
            host=str(dev.connections.cli.ip),
            username=dev.credentials.default.username,
            password=dev.credentials.default.password,
            # port=22,
            hostkey_verify=False,  # <--- Add this line to bypass host key verification for testing purposes
            timeout=180,
            # device_params={'name': 'sros'}
        )

        # Get REAL structured data from the Nokia YANG model
        # This path gets all router interfaces in the 'Base' instance
        raw_state = conn.running.get('/nokia-state:state/router[router-name="Base"]/interface')

        # Transform the Nokia objects into a standard Python Dictionary
        clean_data = nokia_to_dict(raw_state)

        # Save to JSON
        with open("nokia_snapshot_structured.json", "w") as f:
            json.dump(clean_data, f, indent=2)

        log.info("Success! Structured JSON saved to nokia_snapshot_structured.json")
        self.passed("Data captured and serialized successfully.")

class ConnectivityTest(aetest.Testcase):
    @aetest.test
    def check_version(self):
        # Using the dictionary access we learned to avoid SyntaxErrors
        dev = self.parent.parameters['testbed'].devices['CE-1']
        output = dev.execute('show version')
        if 'Nokia' in output:
            self.passed("Nokia OS detected successfully")
        else:
            self.failed("Nokia OS not detected")

class OSPFTest(aetest.Testcase):
    @aetest.test
    def verify_ospf_interfaces(self):
        dev = self.parent.parameters['testbed'].devices['CE-1']
        # Since SROS parsers are limited, we use execute + logic
        output = dev.execute('show router ospf interface')
        if 'Up' in output:
            self.passed("OSPF Interfaces are Up")
        else:
            self.failed("No Up OSPF interfaces found")

class VerifyChanges(aetest.Testcase):
    @aetest.test
    def compare_snapshots(self):
        # 1. Load your 'Golden' snapshot (from yesterday or before a change)
        with open("nokia_snapshot_golden.json", "r") as f:
            golden = json.load(f)

        # 2. Load the 'Current' snapshot (the one you just captured)
        with open("nokia_snapshot_structured.json", "r") as f:
            current = json.load(f)

        # 3. Calculate the difference
        # ignore_order=True is helpful for YANG lists where sequence doesn't matter
        # Define a list of regex patterns for fields that are allowed to change
        exclude_paths = [
            r"root\[.*\]\['statistics'\]",  # Ignores all traffic counters
            r"root\[.*\]\['last-oper-change'\]", # Ignores timing changes
            r"root\[.*\]\['timer'\]" # Ignores ARP/Neighbor timers
        ]

        diff = DeepDiff(
            golden,
            current,
            ignore_order=True,
            exclude_regex_paths=exclude_paths
        )

        if not diff:
            self.passed("No changes detected. The network is stable!")
        else:
            # Use .to_json() instead of json.dumps()
            log.error("Network drift detected!")
            print(diff.to_json(indent=2))
            self.failed("Network state has drifted from the golden snapshot!")

class CommonCleanup(aetest.CommonCleanup):
    @aetest.subsection
    def disconnect(self, testbed):
        testbed.disconnect()

if __name__ == '__main__':
    # This allows you to run the script directly from CLI
    import sys
    from pyats import topology
    testbed = topology.loader.load('testbed.yml')
    aetest.main(testbed=testbed)
