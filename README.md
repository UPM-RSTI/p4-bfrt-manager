# p4-bfrt-manager

A Python library for writing scripts that control Intel Tofino switches
remotely through the **BF Runtime (BFRT) gRPC** API exposed by `bf_switchd`:
load P4 programs, bring ports up, set up multicast, install rules and read
tables and counters, from any machine that can reach the switch.

Table entries use a plain JSON representation, so switch state can be dumped to
a file, versioned and replayed. It works for P4 tables as well as for
fixed-function tables such as `$PORT` or the multicast engine (`$pre.*`).

Developed by the RSTI group at Universidad Politécnica de Madrid (UPM). The
library is also used as the BFRT backend of the
[TeraFlowSDN](https://tfs.etsi.org/) P4 driver and keeps that driver's resource
API (`get_resource_keys`, `get_resource_entries`, `apply_operation`...), but it
has no dependency on TeraFlowSDN and can be used on its own.

## Installation

Install it in a virtual environment (required on Ubuntu 24.04+ / Debian 12+,
where the system Python does not accept `pip install`):

```bash
python3 -m venv ~/venvs/bfrt
source ~/venvs/bfrt/bin/activate
pip install git+https://github.com/mgaflip/p4-bfrt-manager.git
```

To install a specific release, append the tag, e.g.
`pip install git+https://github.com/mgaflip/p4-bfrt-manager.git@v0.1.0`.
For development, clone the repository and install it in editable mode:

```bash
git clone https://github.com/mgaflip/p4-bfrt-manager.git
cd p4-bfrt-manager
pip install -e ".[test]"
```

Remember to activate the environment (`source ~/venvs/bfrt/bin/activate`) in
each new shell, or run scripts directly with `~/venvs/bfrt/bin/python`.

Requires Python ≥ 3.8. The BFRT gRPC client from Intel's
[open-p4studio](https://github.com/p4lang/open-p4studio) is bundled (see
[`src/bfrt_manager/bfrt_grpc`](src/bfrt_manager/bfrt_grpc/README.md)), so the SDE
does **not** need to be installed on the machine running the library, and it
does not conflict with the SDE's own `bfrt_grpc` when both are present.

## Quick start

```python
from bfrt_manager import BFRTManager

with BFRTManager(device_id=0, ip_address="10.0.2.68", port=50052) as mgr:
    # Load ./p4src/my_prog onto the switch (omit both arguments to use
    # the program that is already running)
    mgr.start("./p4src", "my_prog")

    mgr.print_table_entries_summary()

    mgr.table_entry_insert_from_json({
        "table-name": "pipe.Ingress.ipv4_lpm",
        "match-fields": {"hdr.ipv4.dst_addr": {"value": "10.10.10.0", "prefix_len": 24}},
        "match-annotations": {"hdr.ipv4.dst_addr": "ipv4"},
        "action-name": "Ingress.forward",
        "action-params": {"port": 12, "dst_mac": "00:10:94:00:00:01"},
        "action-annotations": {"dst_mac": "mac"},
    })

    print(mgr.get_table_entries("pipe.Ingress.ipv4_lpm"))
```

Exiting the `with` block closes the gRPC session (or call `mgr.stop()`).
See [`examples/setup_switch.py`](examples/setup_switch.py) for a complete script.

## Writing scripts

```python
with BFRTManager(0, "10.0.2.68", 50052) as sw:
    sw.start()                                   # use the program already running
    # sw.start("./p4src", "my_prog")             # ...or load one
    # sw.load_program("my_prog", "bf-rt.json", "context.json", "tofino2.bin")

    # Ports (device port or front-panel name)
    sw.add_port(4, speed="BF_SPEED_100G", fec="BF_FEC_TYP_NONE")
    sw.add_port("3/0", speed="BF_SPEED_25G")
    sw.set_port_enabled(4, False)
    print(sw.get_ports())
    print(sw.get_port_stats())

    # Multicast group 1 -> ports 16 and 24
    sw.add_multicast(mgid=1, ports=[16, 24])

    # Rules (short, unique table names are accepted)
    sw.set_match_fields_annotations("ipv4_lpm", {"hdr.ipv4.dst_addr": "ipv4"})
    sw.insert_table_entry("ipv4_lpm",
                          {"hdr.ipv4.dst_addr": {"value": "10.0.1.0", "prefix_len": 24}},
                          "Ingress.forward", {"port": 4})
    sw.set_default_entry("ipv4_lpm", "Ingress.drop")

    # Any table can be read, including registers and counters
    print(sw.get_table_entries("ipv4_lpm"))
```

Table names may be given in full (`pipe.Ingress.ipv4_lpm`) or by any unique
suffix (`Ingress.ipv4_lpm`, `ipv4_lpm`). Use `print_table_entries_spec(table)`
to see the key fields, actions and parameters of a table.

## Loading a P4 program

`start(p4_path, p4_name)` expects the `bf-p4c` output laid out as:

```
<p4_path>/<p4_name>/bf-rt.json
<p4_path>/<p4_name>/pipe/context.json
<p4_path>/<p4_name>/pipe/tofino.bin      # or tofino2.bin / tofino3.bin
```

The files are read locally and sent to the switch over gRPC. By default the
program is loaded on pipes 0–3 with profile `pipe`; use
`start(..., pipe_list=[0, 1], profile="pipe")` for other layouts.

## Table entry format

```json
{
  "table-name": "pipe.Ingress.acl",
  "match-fields": {
    "ig_port": 4,
    "src_addr": {"value": "10.0.0.0", "mask": "255.0.0.0"},
    "l4_port": {"low": 80, "high": 90},
    "$MATCH_PRIORITY": 10
  },
  "match-annotations": {"src_addr": "ipv4"},
  "action-name": "Ingress.drop",
  "action-params": {},
  "action-annotations": {}
}
```

| Match type | JSON value |
|------------|------------|
| Exact      | `v` or `{"value": v}` |
| Ternary    | `{"value": v, "mask": m}` |
| LPM        | `{"value": v, "prefix_len": n}` |
| Range      | `{"low": l, "high": h}` |
| Optional   | `{"value": v, "is_valid": true}` |

Values are integers by default. **Annotations** (`ipv4`, `ipv6`, `mac`,
`bytes`) let a field be given and returned as a string such as `"10.0.0.1"` or
`"00:10:94:00:00:01"`; several can be combined as `"ipv4,bytes"`.
Annotations belong to the client session, not to the switch: a new session
reading entries written by another one gets plain integers unless it applies
the same annotations first, e.g. with `set_annotations_from_json(entries)` or
`dump_entries.py -a entries.json`.

Actions declared `@defaultonly` in P4 can only be used with
`set_default_entry()`; `print_table_entries_spec()` marks them as
*(default action only)*.

`action-name` is omitted for fixed-function tables, whose data fields start
with `$`:

```json
{
  "table-name": "$PORT",
  "match-fields": {"$DEV_PORT": 4},
  "action-params": {"$SPEED": "BF_SPEED_100G", "$FEC": "BF_FEC_TYP_NONE", "$PORT_ENABLE": true}
}
```

When reading, wildcard fields (ternary with mask 0, LPM with prefix length 0)
are left out, so the output of `get_table_entries()` can be passed back
unchanged to `table_entry_insert_from_json()`.

## Main API

| Method | Description |
|--------|-------------|
| `start(p4_path=None, p4_name=None)` / `stop()` | Load/bind the program and discover tables / close the session |
| `get_tables()` / `get_all_tables()` | Tables that accept entries / all tables, including `$PORT`, `$pre.*`... |
| `get_table_entries(table, match_map=None)` | Read entries (optionally only those matching `match_map`) |
| `table_entry_insert_from_json(entries)` | Insert one entry, a list, or a JSON string |
| `table_entry_update_from_json(entries)` | Modify existing entries |
| `table_entry_delete_from_json(entries)` | Delete entries |
| `insert_table_entry(table, match, action, params)` | Single insert (also `modify_…`, `insert_or_modify_…`, `delete_table_entry`) |
| `delete_table_entries(table)` / `delete_tables()` | Clear one table / all entry tables |
| `count_table_entries(table)` / `count_active_entries()` | Entry counts |
| `set_default_entry(table, action, params)` / `reset_default_entry` / `get_default_entry` | Default (miss) action |
| `set_annotations_from_json(entries)` | Apply the annotations of JSON entries without writing them |
| `add_port(port, speed, fec, enable)` / `set_port_enabled` / `delete_port` | Port configuration (`$PORT`) |
| `get_ports()` / `get_port_stats(ports, counters)` / `get_dev_port("1/0")` | Port state, counters and name lookup |
| `add_multicast(mgid, ports)` / `delete_multicast(mgid)` | One-node multicast group |
| `add_multicast_node` / `add_multicast_group` (and `delete_…`) | Full PRE control: RIDs, several nodes, L1 XIDs |
| `load_program(name, bfrt_json, context_json, binary)` / `bind(name)` | Load from explicit paths / attach to a running program |
| `print_table_entries_spec(table)` | Print key and action fields of a table |
| `print_table_entries_summary()` | Print the number of entries per table |

Errors raise `BFRTException`. Batch operations try every entry and then raise a
single exception listing all failures. For lower-level access, `get_table()`
returns the underlying `bfrt_grpc` table object.

## Command-line examples

```bash
cd examples
python show_tables.py --addr 10.0.2.68 --table pipe.Ingress.ipv4_lpm
python dump_entries.py --addr 10.0.2.68 -o backup.json
python apply_entries.py --addr 10.0.2.68 insert entries.json
python apply_entries.py --addr 10.0.2.68 --p4-path ../p4src --p4-name my_prog insert entries.json
```

[`tna_counter_entries.json`](examples/tna_counter_entries.json) is written for
the `tna_counter` example program shipped with the Tofino SDE:

```bash
python apply_entries.py --addr <switch> insert tna_counter_entries.json
python dump_entries.py  --addr <switch> -a tna_counter_entries.json forward_exact
python apply_entries.py --addr <switch> delete tna_counter_entries.json
```

The dump includes the direct counters of each entry (`$COUNTER_SPEC_PKTS`,
`$COUNTER_SPEC_BYTES`); `-a` reuses the annotations of the JSON file so MAC
addresses are shown as strings.

Other table, action and field names in the examples (`pipe.Ingress.ipv4_lpm`,
`Ingress.forward`, `my_prog`...) and in `examples/entries.json` are
placeholders: run `show_tables.py` first to see the tables of the program
loaded on your switch, then `show_tables.py --table <name>` for their fields.
[`setup_switch.py`](examples/setup_switch.py) is a template for a full
configuration script.

## Logging

The library logs through the standard `logging` module (loggers under
`bfrt_manager`) and does not configure handlers. Enable output with, for example,
`logging.basicConfig(level=logging.INFO)`.

## Tests

```bash
pip install -e ".[test]"   # inside the virtual environment
pytest
```

The tests use real `bfrt_grpc` table objects built from a small `bf-rt.json`
fixture with the read/write RPCs replaced by an in-memory store, so no switch is
needed.

## License

Copyright 2025-2026 RSTI - Universidad Politécnica de Madrid (UPM).

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). The bundled
`bfrt_grpc` client is Copyright (C) 2024 Intel Corporation, also under Apache 2.0.
