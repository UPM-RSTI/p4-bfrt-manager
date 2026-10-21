#!/usr/bin/env python3
"""Show the tables of the running program, their entry counts and (optionally)
the specification of one table."""
from _common import parser, setup_logging
from bfrt_manager import BFRTManager

p = parser(__doc__)
p.add_argument("--table", help="print key/action specification of this table")
args = p.parse_args()
setup_logging(args)

with BFRTManager(args.device_id, args.addr, args.port) as mgr:
    mgr.start(args.p4_path, args.p4_name)
    mgr.print_table_entries_summary()
    if args.table:
        mgr.print_table_entries_spec(args.table)
