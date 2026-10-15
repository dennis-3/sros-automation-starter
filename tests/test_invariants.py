#!/usr/bin/env python3
"""
Unit tests for model-driven operational state extraction and invariant assertions.
Verifies that:
1. Native pySROS / YANG state models are accurately parsed without fragile CLI regexes.
2. Operational regressions (interface drops, IPv4 sub-state drops, BGP drops) are detected.
3. System, interface, IS-IS, OSPF, and BGP model structures (lists and dicts) are verified.
"""

import os
import sys
import unittest

# Ensure repo root is available for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyats_test_suite import (
    _count_routes,
    _extract_bgp_peers,
    _extract_interfaces,
    _extract_isis_interfaces,
    _extract_ospf_neighbors,
    _extract_ports,
    _extract_system_info,
    _get_router_entries,
    _unwrap_state_root,
    check_operational_invariants,
)


def parse_cli_interface_states(cli_output):
    """Legacy CLI text parser retained for regression verification."""
    down_interfaces = []
    for line in cli_output.splitlines():
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
    return down_interfaces


class TestOperationalInvariants(unittest.TestCase):
    def test_extract_system_info(self):
        data = {
            "state": {
                "system": {
                    "oper-name": "r1-pe1",
                    "platform": "7750 SR-1",
                    "version": {
                        "version-number": "25.7.R1",
                        "version-string": "TiMOS-B-25.7.R1",
                    },
                }
            }
        }
        info = _extract_system_info(data)
        self.assertEqual(info["oper-name"], "r1-pe1")
        self.assertEqual(info["platform"], "7750 SR-1")
        self.assertEqual(info["version"], "TiMOS-B-25.7.R1")

    def test_extract_interfaces_dict_and_list(self):
        dict_data = {
            "/nokia-state:state": {
                "router": {
                    "Base": {
                        "interface": {
                            "system": {
                                "oper-state": "up",
                                "ipv4": {"oper-state": "up"},
                            },
                            "to-r2": {
                                "oper-state": "down",
                                "ipv4": {"oper-state": "down"},
                            },
                        }
                    }
                }
            }
        }
        ifs = _extract_interfaces(dict_data)
        self.assertEqual(ifs[("Base", "system")]["oper"], "up")
        self.assertEqual(ifs[("Base", "system")]["ipv4_oper"], "up")
        self.assertEqual(ifs[("Base", "to-r2")]["oper"], "down")

        list_data = {
            "router": [
                {
                    "router-name": "Base",
                    "interface": [
                        {
                            "interface-name": "system",
                            "oper-state": "up",
                            "ipv4": {"oper-state": "up"},
                        }
                    ],
                }
            ]
        }
        ifs_list = _extract_interfaces(list_data)
        self.assertEqual(ifs_list[("Base", "system")]["oper"], "up")

    def test_extract_isis_interfaces(self):
        data = {
            "router": {
                "Base": {
                    "isis": {
                        "0": {
                            "interface": {
                                "system": {"oper-state": "up"},
                                "to-r2": {"oper-state": "up"},
                            }
                        }
                    }
                }
            }
        }
        isis_ifs = _extract_isis_interfaces(data)
        self.assertEqual(len(isis_ifs), 2)
        self.assertEqual(isis_ifs[("Base", "0", "system")]["oper"], "up")
        self.assertEqual(isis_ifs[("Base", "0", "to-r2")]["oper"], "up")

    def test_extract_ospf_neighbors(self):
        data = {
            "router": {
                "Base": {
                    "ospf": {
                        "0": {
                            "area": {
                                "0.0.0.0": {
                                    "interface": {
                                        "to-r2": {
                                            "neighbor": {
                                                "192.168.1.2": {
                                                    "oper-state": "full",
                                                    "router-id": "10.0.0.2",
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        nbrs = _extract_ospf_neighbors(data)
        self.assertEqual(len(nbrs), 1)
        key = ("Base", "0", "0.0.0.0", "to-r2", "192.168.1.2")
        self.assertIn(key, nbrs)
        self.assertEqual(nbrs[key]["oper"], "full")
        self.assertEqual(nbrs[key]["router_id"], "10.0.0.2")

    def test_extract_bgp_peers(self):
        data = {
            "router": {
                "Base": {
                    "bgp": {
                        "neighbor": {
                            "10.0.0.2": {
                                "session-state": "established",
                                "oper-state": "up",
                            }
                        }
                    }
                }
            }
        }
        peers = _extract_bgp_peers(data)
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[("Base", "10.0.0.2")]["state"], "established")

    def test_extract_ports(self):
        data = {
            "state": {
                "port": {
                    "1/1/1": {"oper-state": "up"},
                    "1/1/2": {"oper-state": "down"},
                }
            }
        }
        ports = _extract_ports(data)
        self.assertEqual(ports["1/1/1"], "up")
        self.assertEqual(ports["1/1/2"], "down")

    def test_count_routes(self):
        data = {
            "router": {
                "Base": {
                    "route-table": {
                        "unicast": {
                            "ipv4": {
                                "route": {
                                    "10.0.0.1/32": {},
                                    "10.0.0.2/32": {},
                                }
                            }
                        }
                    }
                }
            }
        }
        self.assertEqual(_count_routes(data), 2)

    def test_unwrap_state_root(self):
        data = {"/nokia-state:state": {"router": {"Base": {}}}}
        root = _unwrap_state_root(data)
        self.assertIn("router", root)

    def test_get_router_entries(self):
        data = {"router": {"Base": {"interface": {}}}}
        entries = _get_router_entries(data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0], "Base")

    def test_system_interface_down_detected(self):
        pre = {
            "router": {
                "Base": {
                    "interface": {
                        "system": {
                            "oper-state": "up",
                            "ipv4": {"oper-state": "up"},
                        }
                    }
                }
            }
        }
        post = {
            "router": {
                "Base": {
                    "interface": {
                        "system": {
                            "oper-state": "down",
                            "ipv4": {"oper-state": "down"},
                        }
                    }
                }
            }
        }
        regressions = check_operational_invariants(pre, post, dev_label="r1")
        self.assertTrue(any("system" in r and "down" in r for r in regressions))

    def test_system_interface_dormant_detected(self):
        pre = {
            "state": {
                "router": {"Base": {"interface": {"system": {"oper-state": "up"}}}}
            }
        }
        post = {
            "state": {
                "router": {"Base": {"interface": {"system": {"oper-state": "dormant"}}}}
            }
        }
        regressions = check_operational_invariants(pre, post, dev_label="r1")
        self.assertTrue(any("dormant" in r for r in regressions))

    def test_system_interface_ipv4_substate_drop(self):
        pre = {
            "/nokia-state:state": {
                "router": {
                    "Base": {
                        "interface": {
                            "system": {
                                "oper-state": "up",
                                "ipv4": {"oper-state": "up"},
                            }
                        }
                    }
                }
            }
        }
        post = {
            "/nokia-state:state": {
                "router": {
                    "Base": {
                        "interface": {
                            "system": {
                                "oper-state": "up",
                                "ipv4": {"oper-state": "down"},
                            }
                        }
                    }
                }
            }
        }
        regressions = check_operational_invariants(pre, post, dev_label="r1")
        self.assertTrue(any("IPv4 state dropped" in r for r in regressions))

    def test_system_interface_removed(self):
        pre = {"router": {"Base": {"interface": {"system": {"oper-state": "up"}}}}}
        post = {"router": {"Base": {"interface": {}}}}
        regressions = check_operational_invariants(pre, post, dev_label="r1")
        self.assertTrue(any("was removed" in r for r in regressions))

    def test_deepdiff_values_changed_oper_state_detected(self):
        diff = {
            "values_changed": {
                "root['router']['Base']['interface']['system']['oper-state']": {
                    "old_value": "up",
                    "new_value": "down",
                }
            }
        }
        regressions = check_operational_invariants({}, {}, dev_label="r1", diff=diff)
        self.assertEqual(len(regressions), 1)
        self.assertIn("dropped from 'up' to 'down'", regressions[0])

    def test_cli_parsing_up_down_flagged(self):
        cli = """
        ===============================================================================
        Interface Table (Router: Base)
        ===============================================================================
        Interface-Name                   Adm       Opr(L/I)     Mode    Port/SapId
        IP-Address                                                   PfxState
        -------------------------------------------------------------------------------
        system                           Up        Up/Down      Network system
        10.0.0.1/32                                                  n/a
        to-r2                            Up        Up/Up        Network 1/1/1
        192.168.1.1/30                                               n/a
        ===============================================================================
        """
        down = parse_cli_interface_states(cli)
        self.assertEqual(len(down), 1)
        self.assertIn("system (adm:Up, opr:Up/Down)", down[0])

    def test_cli_parsing_clean_pass(self):
        cli = """
        ===============================================================================
        Interface Table (Router: Base)
        ===============================================================================
        Interface-Name                   Adm       Opr(L/I)     Mode    Port/SapId
        IP-Address                                                   PfxState
        -------------------------------------------------------------------------------
        system                           Up        Up/Up        Network system
        10.0.0.1/32                                                  n/a
        to-r2                            Up        Up/Up        Network 1/1/1
        192.168.1.1/30                                               n/a
        ===============================================================================
        """
        down = parse_cli_interface_states(cli)
        self.assertEqual(len(down), 0)


if __name__ == "__main__":
    unittest.main()
