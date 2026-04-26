import yaml
import os
from langchain.tools import tool
from scrapli import Scrapli

@tool
def get_lab_inventory(topology_file: str = "../topology.yaml"):
    """Reads the Containerlab topology file to find node names and management IPs."""
    if not os.path.exists(topology_file):
        return f"Error: {topology_file} not found at {os.path.abspath(topology_file)}"
    
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
        "platform": "nokia_sros"
    }
    try:
        with Scrapli(**device) as conn:
            response = conn.send_command(command)
            return response.result
    except Exception as e:
        return f"Connection failed: {str(e)}"

@tool
def get_sros_interfaces(node_name: str, topology_file: str = "../topology.yaml"):
    """Fetches the interface status for a given SROS node name (e.g., 'r1').
    It automatically looks up the management IP from the topology file.
    """
    inventory = get_lab_inventory(topology_file)
    if isinstance(inventory, str) and inventory.startswith("Error"):
        return inventory
    
    ip = inventory.get(node_name)
    if not ip:
        return f"Error: Node {node_name} not found in inventory. Available nodes: {list(inventory.keys())}"
    
    return check_sros_state(ip, "show router interface")