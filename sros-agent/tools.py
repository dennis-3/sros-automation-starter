import scrapli
import yaml
import os
import sys
from langchain.tools import tool
from scrapli import Scrapli

print(f"DEBUG: Using Python {sys.executable}")
print(f"DEBUG: Scrapli found at {scrapli.__file__}")

@tool
def get_lab_inventory(topology_file: str = "topology.yaml"):
    """Reads the Containerlab topology file..."""
    # Force absolute path check
    if not os.path.isabs(topology_file):
        # Assumes topology is in the same dir as the script or project root
        topology_file = os.path.join(os.getcwd(), topology_file)

    if not os.path.exists(topology_file):
        return {"error": f"Topology file not found at {topology_file}"} # Return dict
    
    with open(topology_file, 'r') as f:
        data = yaml.safe_load(f)
    
    nodes = data.get('topology', {}).get('nodes', {})
    return {name: node.get('mgmt-ipv4') for name, node in nodes.items()}

@tool
def check_sros_state(ip: str, command: str):
    """Logs into a Nokia SROS node via SSH to run a show command (e.g., 'show router interface')."""
    device = {
        "host": ip,
        "auth_username": "admin", # Default for clab SROS
        "auth_password": "NokiaSros1!",
        "platform": "nokia_sros",
        "auth_strict_key": False,
    }
    try:
        with Scrapli(**device) as conn:
            response = conn.send_command(command)
            return response.result
    except Exception as e:
        return f"Connection failed: {str(e)}"

@tool
def get_sros_interfaces(node_name: str, topology_file: str = "topology.yaml"):
    """Fetches the interface status for a given SROS node name (e.g., 'r1').
    It automatically looks up the management IP from the topology file.
    """
    inventory = get_lab_inventory(topology_file)
    
    # Check if inventory returned an error dict
    if "error" in inventory:
        return inventory["error"]
    
    ip = inventory.get(node_name)
    if not ip:
        return f"Error: Node {node_name} not found. Found: {list(inventory.keys())}"
    
    return check_sros_state(ip, "show router interface")