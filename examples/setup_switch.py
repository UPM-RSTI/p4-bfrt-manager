#!/usr/bin/env python3
"""
End-to-end example: load a P4 program, bring ports up, create a multicast
group, install forwarding rules and read back state and counters.

Table, action and field names below are placeholders: adapt them to your
P4 program (use show_tables.py --table <name> to see its fields).
"""
import json
import logging

from bfrt_manager import BFRTManager, BFRTException

SWITCH = "10.0.2.68"
GRPC_PORT = 50052

logging.basicConfig(level=logging.INFO)

with BFRTManager(0, SWITCH, GRPC_PORT) as sw:

    # 1. Load the program (./p4src/my_prog/{bf-rt.json, pipe/context.json, pipe/tofino.bin})
    #    Use sw.start() instead to work with the program already running.
    sw.start("./p4src", "my_prog")

    # 2. Ports: by device port or by front-panel name
    for port in (4, 8, "3/0"):
        sw.add_port(port, speed="BF_SPEED_100G", fec="BF_FEC_TYP_NONE")
    print("Ports:", json.dumps(sw.get_ports(), indent=2))

    # 3. Multicast group 1 replicating to ports 16 and 24
    sw.add_multicast(mgid=1, ports=[16, 24])

    # 4. Field annotations let IPs and MACs be written as strings
    sw.set_match_fields_annotations("ipv4_lpm", {"hdr.ipv4.dst_addr": "ipv4"})
    sw.set_action_annotations("ipv4_lpm", "Ingress.forward", {"dst_mac": "mac"})

    # 5. Rules, one by one...
    sw.insert_table_entry(
        "ipv4_lpm",
        match_map={"hdr.ipv4.dst_addr": {"value": "10.0.1.0", "prefix_len": 24}},
        action_name="Ingress.forward",
        action_params={"port": 4, "dst_mac": "00:10:94:00:00:01"},
    )
    sw.set_default_entry("ipv4_lpm", "Ingress.drop")

    # ...or in bulk from JSON (e.g. a file produced by dump_entries.py)
    try:
        with open("entries.json") as f:
            sw.table_entry_insert_from_json(json.load(f))
    except BFRTException as e:
        print("Some entries failed:", e)

    # 6. Read back
    for entry in sw.get_table_entries("ipv4_lpm"):
        print(json.dumps(entry))
    sw.print_table_entries_summary()

    # 7. Counters
    print(json.dumps(sw.get_port_stats(), indent=2))

    # Any other table (registers, counters, meters...) is read the same way:
    # sw.get_table_entries("Ingress.pkt_counter", match_map={"$COUNTER_INDEX": 0})
