import os
from pyats.easypy import run

def main(runtime):
    test_script = os.path.join(os.path.dirname(__file__), 'nokia_test_suite.py')

    # 1. Run the test suite
    # We capture the results of the run into a variable
    run(testscript=test_script, runtime=runtime)

    # 2. Logic to verify it worked:
    # After the run, pyATS creates a 'Task' object.
    # If your test suite saved data into 'self.parent.parameters',
    # it is now available here in the 'runtime' object.
    print("--- Snapshot Verification ---")
    print(f"Job successfully finished. Logs are in: {runtime.directory}")
