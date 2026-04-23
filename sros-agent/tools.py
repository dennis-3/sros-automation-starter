import yaml
import os
from langchain.tools import tool
from scrapli import Scrapli

@tool
def get_lab_inventory(topology_file: str = "../topology.yml"):
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
        "auth_password": "admin",
        "platform": "nokia_sros"
    }
    try:
        with Scrapli(**device) as conn:
            response = conn.send_command(command)
            return response.result
    except Exception as e:
        return f"Connection failed: {str(e)}"