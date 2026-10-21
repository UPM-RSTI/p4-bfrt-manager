#!/usr/bin/env python3
"""Insert, update or delete table entries described in a JSON file
(a single entry or a list of entries)."""
import json

from _common import parser, setup_logging
from bfrt_manager import BFRTManager, BFRTException, WriteOperation

p = parser(__doc__)
p.add_argument("operation", choices=[op.name for op in WriteOperation])
p.add_argument("json_file", help="JSON file with the entries")
args = p.parse_args()
setup_logging(args)

with open(args.json_file) as f:
    entries = json.load(f)

with BFRTManager(args.device_id, args.addr, args.port) as mgr:
    mgr.start(args.p4_path, args.p4_name)
    try:
        n = mgr.table_entry_operation_from_json(entries, WriteOperation[args.operation])
        print(f"{args.operation}: {n} entries applied")
    except BFRTException as e:
        raise SystemExit(str(e))
