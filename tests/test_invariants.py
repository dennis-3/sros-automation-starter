#!/usr/bin/env python3
"""
Unit tests for operational invariant checking and interface operational parsing.
Verifies that regressions (e.g. system interface going down or dormant) are
strictly detected across different SR OS YANG payload schemas and CLI outputs.
"""

import unittest


def _unwrap_state_root(data):
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
    regressions = []
    if not isinstance(pre_json, dict) or not isinstance(post_json, dict):
        return regressions

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


def parse_cli_interface_states(cli_output):
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
