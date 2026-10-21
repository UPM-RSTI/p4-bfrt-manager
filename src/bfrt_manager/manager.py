# Copyright 2025-2026 RSTI - Universidad Politécnica de Madrid (UPM)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
BFRT manager: load P4 programs and manage table entries of a Tofino switch
using a simple JSON representation.

A table entry looks like::

    {
      "table-name": "pipe.Ingress.ipv4_lpm",
      "match-fields": {"hdr.ipv4.dst_addr": {"value": "10.0.0.0", "prefix_len": 8}},
      "match-annotations": {"hdr.ipv4.dst_addr": "ipv4"},
      "action-name": "Ingress.forward",
      "action-params": {"port": 12, "dst_mac": "00:10:94:00:00:01"},
      "action-annotations": {"dst_mac": "mac"}
    }
"""

import enum
import json
import logging
import os

from .client import BFRTRuntimeClient, BFRTException, DEF_PIPE_LIST

LOGGER = logging.getLogger(__name__)

# Active clients, indexed by "ip:port"
CLIENTS = {}

# Resource keys (kept compatible with the TeraFlowSDN P4 driver)
KEY_TABLE = "table"

# Fixed-function tables
PORT_TABLE = "$PORT"
PORT_STAT_TABLE = "$PORT_STAT"
PORT_STR_INFO_TABLE = "$PORT_STR_INFO"
PRE_NODE_TABLE = "$pre.node"
PRE_MGID_TABLE = "$pre.mgid"
DEF_PORT_COUNTERS = ("$FramesReceivedOK", "$FramesReceivedAll",
                     "$FramesTransmittedOK", "$FramesTransmittedAll")

# Binary names produced by bf-p4c, in lookup order
TOFINO_BINARIES = ("tofino.bin", "tofino2.bin", "tofino3.bin")


@enum.unique
class WriteOperation(enum.Enum):
    """Write operations."""
    insert = 1
    update = 2
    delete = 3


def find_p4_artifacts(p4_path, p4_name, profile="pipe"):
    """
    Locate the compiler outputs of a program laid out as::

        <p4_path>/<p4_name>/bf-rt.json
        <p4_path>/<p4_name>/<profile>/context.json
        <p4_path>/<p4_name>/<profile>/tofino.bin   (or tofino2.bin / tofino3.bin)

    :return: (tofino_bin, bfrt_json, context_json)
    :raises FileNotFoundError: if any of them is missing
    """
    base = os.path.join(p4_path, p4_name)
    bfrt_json = os.path.join(base, "bf-rt.json")
    context_json = os.path.join(base, profile, "context.json")
    candidates = [os.path.join(base, profile, b) for b in TOFINO_BINARIES]
    tofino_bin = next((c for c in candidates if os.path.exists(c)), None)

    missing = [p for p in (bfrt_json, context_json) if not os.path.exists(p)]
    if tofino_bin is None:
        missing.append(" | ".join(candidates))
    if missing:
        raise FileNotFoundError(f"P4 program files not found: {', '.join(missing)}")
    return tofino_bin, bfrt_json, context_json


class BFRTManager:
    """
    Manage the runtime entries of a P4 pipeline on a Tofino switch via BFRT.

    :param device_id: device ID on the switch (usually 0)
    :param ip_address: switch management IP address
    :param port: bf_switchd gRPC port (usually 50052)
    :param client: an already built BFRTRuntimeClient (mainly for testing)
    :param client_kwargs: extra arguments for BFRTRuntimeClient
        (client_id, num_tries, timeout)

    Can be used as a context manager::

        with BFRTManager(0, "10.0.0.1", 50052) as mgr:
            mgr.start("./p4src", "my_prog")
            ...
    """

    def __init__(self, device_id: int, ip_address: str, port: int,
                 client=None, **client_kwargs):
        self.__id = device_id
        self.__ip_address = ip_address
        self.__port = int(port)
        self.__grpc_endpoint = f"{self.__ip_address}:{self.__port}"
        self.key_id = self.__grpc_endpoint
        self.p4_path = None
        self.p4_name = None
        self.bfrt_info = None

        if client is None:
            client = BFRTRuntimeClient(self.__id, self.__grpc_endpoint, **client_kwargs)
        CLIENTS[self.key_id] = client
        self.local_client = client

        self.all_tables = []
        self.tables = []
        self.table_entries = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.local_client is not None:
            self.stop()

    ############################################################################
    # Life cycle
    ############################################################################

    def start(self, p4_path=None, p4_name=None, pipe_list=DEF_PIPE_LIST, profile="pipe"):
        """
        Start the manager:
        (i) optionally load a P4 program on the switch,
        (ii) retrieve its BfRt info and bind to it, and
        (iii) discover its tables.

        :param p4_path: directory containing the compiled P4 programs
        :param p4_name: program name (subdirectory of p4_path). If None, the
            program currently loaded on the switch is used.
        :param pipe_list: pipes to load the program on
        :param profile: profile name (subdirectory with context.json and the binary)
        :raises FileNotFoundError: if the program files are missing
        :raises BFRTException: on any switch error
        """
        if p4_name is None:
            LOGGER.info("P4 name not provided, using program currently loaded in device")
            self.bind()
            return
        self.p4_path = p4_path or "."
        tofino_bin, bfrt_json, context_json = find_p4_artifacts(self.p4_path, p4_name, profile)
        self.load_program(p4_name, bfrt_json, context_json, tofino_bin,
                          pipe_list=pipe_list, profile=profile)

    def load_program(self, p4_name, bfrt_json, context_json, binary,
                     pipe_list=DEF_PIPE_LIST, profile="pipe"):
        """
        Load a compiled P4 program from explicit file paths and bind to it.

        :param p4_name: program name
        :param bfrt_json: path to bf-rt.json
        :param context_json: path to context.json
        :param binary: path to tofino.bin / tofino2.bin / tofino3.bin
        :param pipe_list: pipes to load the program on
        :param profile: profile name
        """
        assert self.local_client, "Manager already stopped"
        for path in (bfrt_json, context_json, binary):
            if not os.path.exists(path):
                raise FileNotFoundError(f"P4 program file not found: {path}")
        try:
            LOGGER.info("Loading P4 program %s (%s)", p4_name, binary)
            self.local_client.set_fwd_pipe_config(
                p4_name, binary, bfrt_json, context_json,
                pipe_list=pipe_list, profile=profile)
        except Exception:
            self.local_client.tear_down()
            raise
        self.bind(p4_name)

    def bind(self, p4_name=None):
        """
        Bind to a program already running on the switch and discover its tables.

        :param p4_name: program name; the first program on the device if None
        """
        assert self.local_client, "Manager already stopped"
        try:
            self.bfrt_info = self.local_client.bfrt_get_info(p4_name)
        except Exception:
            self.local_client.tear_down()
            raise
        self.p4_name = self.local_client.p4_name
        self.__discover_tables()
        self.__init_tables()
        LOGGER.info("BFRT Manager bound to %s on %s", self.p4_name, self.__grpc_endpoint)

    def stop(self):
        """Tear down the gRPC session and clean up the manager."""
        assert self.local_client, "Manager already stopped"
        try:
            self.local_client.tear_down()
        finally:
            CLIENTS.pop(self.key_id, None)
            self.local_client = None
            self.__clear()
            LOGGER.info("BFRT Manager stopped")

    def __clear(self):
        self.__id = None
        self.__ip_address = None
        self.__port = None
        self.__grpc_endpoint = None
        self.table_entries.clear()

    def __init_tables(self):
        self.table_entries = {name: [] for name in self.tables}

    def __discover_tables(self):
        self.table_entries.clear()
        self.all_tables = self.local_client.bfrt_get_all_tables()
        self.tables = self.local_client.bfrt_get_tables()

    ############################################################################
    # Tables
    ############################################################################

    def get_table(self, table_name):
        """Return the BFRT table object for ``table_name``."""
        return self.local_client.bfrt_get_table(table_name)

    def get_all_tables(self):
        """Names of all tables, including fixed-function ones ($PORT, $pre.*...)."""
        return self.all_tables

    def get_tables(self):
        """Names of the tables that support entries."""
        return self.tables

    def get_table_names(self):
        """Same as get_tables()."""
        return self.tables

    @staticmethod
    def get_table_name(table):
        """Name of a BFRT table object."""
        return table.info.name_get()

    def _resolve_table(self, table):
        return self.get_table(table) if isinstance(table, str) else table

    def get_table_entries(self, table_name, match_map=None, action_name=None, data_map=None):
        """
        Read the entries of a table.

        :param table_name: table name
        :param match_map: optional match dict (or list of them) to read specific entries
        :param action_name: optional action name for the requested data
        :param data_map: optional data fields to read
        :return: list of JSON-like entries
        :raises BFRTException: on read errors
        """
        table = self.get_table(table_name)
        return self.local_client.bfrt_get_table_entries(table, match_map, action_name, data_map)

    def table_entries_to_json(self, table_name):
        """All entries of a table (list of dicts)."""
        return self.get_table_entries(table_name)

    def count_table_entries(self, table_name):
        """Number of entries of a table."""
        return self.local_client.bfrt_count_table_entries(self.get_table(table_name))

    def count_table_entries_all(self):
        """Number of entries across all entry tables."""
        return sum(self.count_table_entries(t) for t in self.get_table_names())

    def count_active_entries(self):
        """Number of active entries across all supported P4 entities."""
        return self.count_table_entries_all()

    ############################################################################
    # Writes
    ############################################################################

    def insert_table_entry(self, table, match_map, action_name=None, action_params=None):
        """
        Insert an entry into a table.

        :param table: table object or name
        :param match_map: match fields dict
        :param action_name: action name (None for fixed tables such as $PORT)
        :param action_params: action parameters dict
        :raises BFRTException: if the entry cannot be inserted
        """
        if not match_map:
            raise BFRTException("Table entry without match fields is not accepted")
        table = self._resolve_table(table)
        LOGGER.debug("Inserting entry in %s: match=%s action=%s params=%s",
                     table.info.name_get(), match_map, action_name, action_params)
        self.local_client.bfrt_add_table_entry(table, match_map, action_name or None,
                                               action_params or {})

    def modify_table_entry(self, table, match_map, action_name=None, action_params=None):
        """Modify an existing entry. Same parameters as insert_table_entry."""
        table = self._resolve_table(table)
        self.local_client.bfrt_mod_table_entry(table, match_map, action_name or None,
                                               action_params or {})

    def insert_or_modify_table_entry(self, table, match_map, action_name=None, action_params=None):
        """Insert an entry, or modify it if it already exists."""
        table = self._resolve_table(table)
        self.local_client.bfrt_add_or_mod_table_entry(table, match_map, action_name or None,
                                                      action_params or {})

    def delete_table_entry(self, table, match_map):
        """Delete one entry from a table."""
        table = self._resolve_table(table)
        LOGGER.debug("Deleting entry from %s: match=%s", table.info.name_get(), match_map)
        self.local_client.bfrt_del_table_entry(table, match_map)

    def delete_table_entries(self, table):
        """Delete all entries of a table."""
        table = self._resolve_table(table)
        self.local_client.bfrt_del_table_entries(table)

    def delete_tables(self):
        """Delete the entries of all entry tables."""
        for table_name in self.get_tables():
            self.delete_table_entries(table_name)

    def set_match_fields_annotations(self, table, annotations):
        """Add client annotations to key fields, e.g. {"dst_addr": "ipv4"}."""
        self.local_client.bfrt_set_key_list_annotations(self._resolve_table(table), annotations)

    def set_action_annotations(self, table, action_name, annotations):
        """Add client annotations to action parameters, e.g. {"dst_mac": "mac"}."""
        self.local_client.bfrt_set_data_list_annotations(
            self._resolve_table(table), action_name, annotations)

    ############################################################################
    # JSON operations
    ############################################################################

    def table_entry_operation_from_json(self, json_resource, operation: WriteOperation):
        """
        Apply a write operation to one or more JSON table entries.

        All entries are attempted; if any of them fails, a BFRTException
        listing every failure is raised at the end.

        :param json_resource: entry dict, list of entries, or JSON string
        :param operation: WriteOperation.insert / update / delete
        :return: number of entries successfully applied
        """
        json_obj = json.loads(json_resource) if isinstance(json_resource, str) else json_resource
        entries = json_obj if isinstance(json_obj, list) else [json_obj]

        errors = []
        applied = 0
        for idx, ent in enumerate(entries):
            try:
                table_name = ent["table-name"]
                match_map = ent["match-fields"]
            except (KeyError, TypeError) as e:
                errors.append(f"entry #{idx}: missing attribute {e}")
                continue
            action_name = ent.get("action-name")
            action_params = ent.get("action-params") or {}

            try:
                table = self.get_table(table_name)
                if ent.get("match-annotations"):
                    self.set_match_fields_annotations(table, ent["match-annotations"])
                if ent.get("action-annotations"):
                    self.set_action_annotations(table, action_name, ent["action-annotations"])

                if operation is WriteOperation.insert:
                    self.insert_table_entry(table, match_map, action_name, action_params)
                elif operation is WriteOperation.update:
                    self.modify_table_entry(table, match_map, action_name, action_params)
                elif operation is WriteOperation.delete:
                    self.delete_table_entry(table, match_map)
                applied += 1
            except Exception as e:  # pylint: disable=broad-except
                errors.append(f"entry #{idx} ({table_name}): {e}")

        if errors:
            raise BFRTException(
                f"{operation.name} failed for {len(errors)} of {len(entries)} entries: "
                + "; ".join(errors))
        return applied

    def table_entry_insert_from_json(self, json_resource):
        """Insert entries from JSON."""
        return self.table_entry_operation_from_json(json_resource, WriteOperation.insert)

    def table_entry_update_from_json(self, json_resource):
        """Modify existing entries from JSON."""
        return self.table_entry_operation_from_json(json_resource, WriteOperation.update)

    def table_entry_delete_from_json(self, json_resource):
        """Delete entries from JSON."""
        return self.table_entry_operation_from_json(json_resource, WriteOperation.delete)

    ############################################################################
    # Resource API (TeraFlowSDN driver compatible)
    ############################################################################

    def get_resource_keys(self):
        """Available resource keys."""
        return [KEY_TABLE]

    def get_resource_entries(self, resource_key):
        """
        Entries of a resource key: a list with one list of entries per
        non-empty table.
        """
        if resource_key != KEY_TABLE:
            raise BFRTException(f"Invalid resource key: {resource_key}")
        entries = []
        for table_name in self.tables:
            t_entries = self.table_entries_to_json(table_name)
            if t_entries:
                entries.append(t_entries)
        return entries

    def insert_operation(self, resource_key, resource_value):
        """Insert the entries of a resource."""
        return self.apply_operation(resource_key, resource_value, WriteOperation.insert)

    def delete_operation(self, resource_key, resource_value):
        """Delete the entries of a resource."""
        return self.apply_operation(resource_key, resource_value, WriteOperation.delete)

    def apply_operation(self, resource_key, resource_value, operation: WriteOperation):
        """
        Apply a write operation to a resource.

        :return: True on success
        :raises BFRTException: on invalid key or if any entry fails
        """
        if KEY_TABLE not in resource_key:
            raise BFRTException(f"{operation} on invalid key {resource_key}")
        self.table_entry_operation_from_json(resource_value, operation)
        return True

    ############################################################################
    # Default entries
    ############################################################################

    def set_default_entry(self, table, action_name, action_params=None):
        """Set the default action of a table (used on table miss)."""
        self.local_client.bfrt_set_default_entry(self._resolve_table(table), action_name,
                                                 action_params or {})

    def reset_default_entry(self, table):
        """Restore the default action declared in the P4 program."""
        self.local_client.bfrt_reset_default_entry(self._resolve_table(table))

    def get_default_entry(self, table):
        """Default entry of a table as a JSON-like dict."""
        return self.local_client.bfrt_get_default_entry(self._resolve_table(table))

    ############################################################################
    # Ports ($PORT, $PORT_STAT, $PORT_STR_INFO)
    ############################################################################

    def get_dev_port(self, port_name):
        """
        Device port of a front-panel port name such as "1/0" (cage/lane).
        """
        entries = self.get_table_entries(PORT_STR_INFO_TABLE,
                                         match_map={"$PORT_NAME": port_name})
        if not entries:
            raise BFRTException(f"Unknown port name {port_name}")
        return entries[0]["action-params"]["$DEV_PORT"]

    def _as_dev_port(self, port):
        return self.get_dev_port(port) if isinstance(port, str) else port

    def add_port(self, port, speed="BF_SPEED_100G", fec="BF_FEC_TYP_NONE",
                 enable=True, **extra):
        """
        Add (create) a port.

        :param port: device port (int) or front-panel name ("1/0")
        :param speed: e.g. BF_SPEED_10G, BF_SPEED_25G, BF_SPEED_100G, BF_SPEED_400G
        :param fec: BF_FEC_TYP_NONE, BF_FEC_TYP_FIRECODE, BF_FEC_TYP_REED_SOLOMON
        :param enable: bring the port administratively up
        :param extra: other $PORT fields without the "$", e.g.
            AUTO_NEGOTIATION="PM_AN_FORCE_DISABLE"
        :return: the device port
        """
        dev_port = self._as_dev_port(port)
        params = {"$SPEED": speed, "$FEC": fec, "$PORT_ENABLE": enable}
        params.update({f"${k}": v for k, v in extra.items()})
        self.insert_table_entry(PORT_TABLE, {"$DEV_PORT": dev_port}, None, params)
        return dev_port

    def set_port_enabled(self, port, enable=True):
        """Bring a port administratively up or down."""
        self.modify_table_entry(PORT_TABLE, {"$DEV_PORT": self._as_dev_port(port)},
                                None, {"$PORT_ENABLE": enable})

    def delete_port(self, port):
        """Delete a port."""
        self.delete_table_entry(PORT_TABLE, {"$DEV_PORT": self._as_dev_port(port)})

    def get_ports(self):
        """
        Configured ports, as {dev_port: {"$SPEED": ..., "$PORT_ENABLE": ..., ...}}.
        """
        return {e["match-fields"]["$DEV_PORT"]: e.get("action-params", {})
                for e in self.get_table_entries(PORT_TABLE)}

    def get_port_stats(self, ports=None, counters=DEF_PORT_COUNTERS):
        """
        Port counters.

        :param ports: device ports or names; all configured ports if None
        :param counters: $PORT_STAT fields to read (None reads all of them)
        :return: {dev_port: {counter: value}}
        """
        if ports is None:
            ports = list(self.get_ports())
        if not ports:
            return {}
        match = [{"$DEV_PORT": self._as_dev_port(p)} for p in ports]
        entries = self.get_table_entries(PORT_STAT_TABLE, match_map=match,
                                         data_map=list(counters) if counters else None)
        return {e["match-fields"]["$DEV_PORT"]: e.get("action-params", {}) for e in entries}

    ############################################################################
    # Multicast ($pre.node, $pre.mgid)
    ############################################################################

    def add_multicast_node(self, node_id, ports, rid=0, lags=None):
        """
        Create a multicast replication node.

        :param node_id: node ID
        :param ports: device ports (or names) the packet is replicated to
        :param rid: replication ID (seen in egress as eg_intr_md.egress_rid)
        :param lags: LAG IDs
        """
        self.insert_table_entry(PRE_NODE_TABLE, {"$MULTICAST_NODE_ID": node_id}, None, {
            "$MULTICAST_RID": rid,
            "$MULTICAST_LAG_ID": list(lags or []),
            "$DEV_PORT": [self._as_dev_port(p) for p in ports],
        })

    def delete_multicast_node(self, node_id):
        """Delete a multicast node."""
        self.delete_table_entry(PRE_NODE_TABLE, {"$MULTICAST_NODE_ID": node_id})

    def add_multicast_group(self, mgid, node_ids, xids=None):
        """
        Create a multicast group (the mcast_grp set in the P4 program).

        :param mgid: multicast group ID
        :param node_ids: IDs of nodes created with add_multicast_node()
        :param xids: optional L1 exclusion IDs, one per node (None = no XID)
        """
        node_ids = list(node_ids)
        xids = list(xids) if xids is not None else [None] * len(node_ids)
        if len(xids) != len(node_ids):
            raise BFRTException("xids must have one element per node")
        self.insert_table_entry(PRE_MGID_TABLE, {"$MGID": mgid}, None, {
            "$MULTICAST_NODE_ID": node_ids,
            "$MULTICAST_NODE_L1_XID_VALID": [x is not None for x in xids],
            "$MULTICAST_NODE_L1_XID": [x or 0 for x in xids],
        })

    def delete_multicast_group(self, mgid):
        """Delete a multicast group."""
        self.delete_table_entry(PRE_MGID_TABLE, {"$MGID": mgid})

    def add_multicast(self, mgid, ports, node_id=None, rid=0):
        """
        Shortcut: create one node replicating to ``ports`` and a group ``mgid``
        containing it. ``node_id`` defaults to ``mgid``.
        """
        node_id = mgid if node_id is None else node_id
        self.add_multicast_node(node_id, ports, rid=rid)
        self.add_multicast_group(mgid, [node_id])

    def delete_multicast(self, mgid, node_id=None):
        """Undo add_multicast()."""
        self.delete_multicast_group(mgid)
        self.delete_multicast_node(mgid if node_id is None else node_id)

    ############################################################################
    # Printing helpers
    ############################################################################

    def print_table_entries_spec(self, table_name, action_name=None):
        """Print the key fields and the action data fields of a table."""
        from tabulate import tabulate  # pylint: disable=import-outside-toplevel

        def print_rows(rows):
            if rows:
                print(tabulate([list(r.values()) for r in rows], headers=list(rows[0].keys()),
                               stralign="right", tablefmt="pretty"))
            else:
                print("  (none)")

        if not isinstance(table_name, str):
            table_name = self.get_table_name(table_name)
        table = self.get_table(table_name)

        print(f"table-name: '{table_name}'")
        print_rows([self.local_client.bfrt_get_key_field(table, kf)
                    for kf in table.info.key_field_name_list_get()])

        actions = [None] if table_name.startswith("$") else table.info.action_name_list_get()
        for act in actions:
            if action_name is not None and action_name != act:
                continue
            if act is not None:
                print(f"action-name: '{act}'")
            print_rows([self.local_client.bfrt_get_action_data_field(table, act, p)
                        for p in table.info.data_field_name_list_get(act)])

    def print_table_entries_summary(self):
        """Print the number of entries of each table."""
        from tabulate import tabulate  # pylint: disable=import-outside-toplevel
        rows = [[t, self.count_table_entries(t)] for t in self.get_tables()]
        print(tabulate(rows, headers=["table", "# of entries"], stralign="right",
                       tablefmt="pretty"))

    def print_table_entries(self, table_name):
        """Print the entries of a table as JSON."""
        if not isinstance(table_name, str):
            table_name = self.get_table_name(table_name)
        print(json.dumps(self.table_entries_to_json(table_name), indent=2))

    def print_tables(self):
        """Print the entries of all tables as JSON."""
        contents = [e for e in (self.table_entries_to_json(t) for t in self.get_tables()) if e]
        print(json.dumps(contents, indent=2))

    def print_objects(self):
        """Print all P4 objects of the installed pipeline."""
        self.print_tables()
