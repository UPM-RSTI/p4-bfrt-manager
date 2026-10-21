#!/usr/bin/env python3
"""Dump the entries of all tables (or the given ones) as JSON. The output can
be fed back to apply_entries.py."""
import json
import sys

from _common import parser, setup_logging
from bfrt_manager import BFRTManager

p = parser(__doc__)
p.add_argument("tables", nargs="*", help="tables to dump (default: all entry tables)")
p.add_argument("-o", "--output", help="output file (default: stdout)")
args = p.parse_args()
setup_logging(args)

with BFRTManager(args.device_id, args.addr, args.port) as mgr:
    mgr.start(args.p4_path, args.p4_name)
    entries = []
    for table in args.tables or mgr.get_tables():
        entries.extend(mgr.get_table_entries(table))

out = open(args.output, "w") if args.output else sys.stdout
json.dump(entries, out, indent=2)
out.write("\n")
