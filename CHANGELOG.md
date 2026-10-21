# Changelog

## 0.1.2

### Added
- `set_annotations_from_json()`: apply the annotations of JSON entries without
  writing them, so a new session reads IPs/MACs as strings.
  `dump_entries.py -a FILE` uses it.
- Action scope support: inserting an entry with a `@defaultonly` action (or
  setting a `@tableonly` action as default) raises a clear `BFRTException`
  instead of the switch's generic `INVALID_ARGUMENT`;
  `print_table_entries_spec()` marks those actions.

### Fixed
- `examples/tna_counter_entries.json`: the second entry used
  `SwitchIngress.nop`, which `tna_counter` only allows as default action.

### Documentation
- Annotations are per client session; `@defaultonly` actions.

## 0.1.1

### Changed
- Unknown table names raise a clear `BFRTException` suggesting similar names
  (or how to list them) instead of a bare `KeyError` message.
- `print_table_entries_spec()` shows the full table name when given a short
  one, wraps long columns, and displays native annotations as `name=value`.
- Requires `tabulate>=0.9`.

### Fixed
- The `choices` column/field repeated the field type; it now lists the allowed
  values of string/enum fields such as `$SPEED` or `$FEC` (fix in the vendored
  `info_parse.py`).

### Documentation
- Installation in a virtual environment (needed on Ubuntu 24.04+ / Debian 12+).
- Example names are flagged as placeholders; new
  `examples/tna_counter_entries.json` for the SDE `tna_counter` program.

## 0.1.0

First standalone release. Changes are relative to the version used as the
BFRT backend of the TeraFlowSDN P4 driver (`bfrt_manager.py`, `bfrt_client.py`).

### Changed
- Packaged as `bfrt_manager` (`pip install .`); the Intel `bfrt_grpc` client is
  vendored under `bfrt_manager.bfrt_grpc`.
- Errors are raised as `BFRTException` instead of being logged and ignored.
  JSON batch operations still try every entry and raise once at the end with
  the list of failures.
- `WriteOperation.update` now modifies existing entries (`entry_mod`) instead
  of inserting.
- A connection failure raises `BFRTException` instead of calling `sys.exit()`.
- `start()` raises `FileNotFoundError` if `p4_name` is given and its files are
  missing, instead of silently falling back to the running program, and binds
  to the requested program.
- The library no longer configures logging (`basicConfig`, handlers, levels).
- `gc.Target` uses the configured `device_id`; `pipe_list` and `profile` are
  parameters of `start()`.
- `tofino2.bin` / `tofino3.bin` are found automatically.
- `BFRTManager` is a context manager and accepts an injected client.
- New: `modify_table_entry`, `insert_or_modify_table_entry`,
  `table_entry_update_from_json`, `find_p4_artifacts`.
- New scripting helpers: `load_program` (explicit paths), `bind`; default
  entries (`set_default_entry`, `reset_default_entry`, `get_default_entry`);
  ports (`add_port`, `set_port_enabled`, `delete_port`, `get_ports`,
  `get_port_stats`, `get_dev_port`); multicast (`add_multicast`,
  `add_multicast_node`, `add_multicast_group` and their `delete_*`).

### Fixed
- Exact match fields with value 0 (e.g. `$DEV_PORT: 0`) were dropped when
  reading entries.
- LPM wildcard check compared a list literal with 0; ternary wildcards with
  string masks (`"0.0.0.0"`) were not detected.
- Range match fields could not be built.
- A JSON entry without `table-name` was applied to the previous entry's table.
- Exported annotations mixed native bf-rt.json annotations with client ones,
  which broke `ipv4`/`mac` conversion on re-import; comma-separated
  annotations are now split.
- `print_table_entries_spec()` printed the wrong action's fields and crashed
  on actions without parameters.
- Entries were read twice from hardware on every `get_table_entries()`.
