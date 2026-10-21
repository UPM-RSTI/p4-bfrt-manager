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
Thin, JSON-friendly wrapper around Intel's BF Runtime gRPC client.

It converts between plain Python dictionaries (match fields, action
parameters) and the KeyTuple/DataTuple objects used by ``bfrt_grpc``.
"""

import logging
from functools import wraps

from .bfrt_grpc import bfruntime_pb2
from .bfrt_grpc import client as gc

LOGGER = logging.getLogger(__name__)

DEF_CLIENT_ID = 0
DEF_PROFILE = "pipe"
DEF_PIPE_LIST = (0, 1, 2, 3)
CLIENT_ANNOTATION = "$client_annotation"


class BFRTException(Exception):
    """Error raised by the BFRT client and manager."""

    def __init__(self, value):
        super().__init__(value)
        self.value = value

    def __str__(self):
        return f"BFRT Exception {self.value}"


def parse_bfrt_error(func):
    """Wrap any exception raised by ``func`` into a BFRTException."""
    @wraps(func)
    def handle(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except BFRTException:
            raise
        except Exception as e:
            raise BFRTException(e) from e
    return handle


###############################################################################
# Pure helpers (no switch needed, unit-tested)
###############################################################################

def _is_zero(value):
    """True if a key value/mask is all zeros, whatever its presentation
    (int, bytes, "0.0.0.0", "00:00:00:00:00:00", "::", ...)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value == 0
    if isinstance(value, (bytes, bytearray)):
        return not any(value)
    if isinstance(value, str):
        stripped = value.lower().replace("0x", "")
        return all(c in "0.:" for c in stripped)
    return False


def filter_key_value(match_type, value):
    """Simplify a key field read from the switch for JSON export.

    Exact fields are reduced to their plain value. Wildcard fields
    (ternary with a zero mask, LPM with prefix length 0) return None so the
    caller can omit them. Other match types are returned unchanged.
    """
    if match_type == "Exact":
        return value["value"] if isinstance(value, dict) else value
    if match_type == "Ternary":
        return None if _is_zero(value.get("mask")) else value
    if match_type == "LPM":
        return None if value.get("prefix_len") in (0, None) else value
    return value


def build_key_tuple(key, val):
    """Build a gc.KeyTuple from a JSON match-field value.

    Accepted forms:
      * ``value``                         -> exact
      * ``{"value": v}``                  -> exact
      * ``{"value": v, "mask": m}``       -> ternary
      * ``{"value": v, "prefix_len": n}`` -> LPM
      * ``{"value": v, "is_valid": b}``   -> optional
      * ``{"low": l, "high": h}``         -> range
    """
    if not isinstance(val, dict):
        return gc.KeyTuple(key, val)
    if not val:
        raise BFRTException(f"Invalid match field '{key}': empty dictionary")
    if set(val) == {"low", "high"}:
        return gc.KeyTuple(key, low=val["low"], high=val["high"])
    if "value" not in val:
        raise BFRTException(
            f"Invalid match field '{key}': {val} has no 'value' (or 'low'/'high')")
    extra = set(val) - {"value"}
    if not extra:
        return gc.KeyTuple(key, val["value"])
    if extra == {"mask"}:
        return gc.KeyTuple(key, value=val["value"], mask=val["mask"])
    if extra == {"prefix_len"}:
        return gc.KeyTuple(key, value=val["value"], prefix_len=val["prefix_len"])
    if extra == {"is_valid"}:
        return gc.KeyTuple(key, value=val["value"], is_valid=val["is_valid"])
    raise BFRTException(
        f"Invalid match field '{key}': unsupported combination {sorted(val)}")


def split_annotations(ann):
    """Normalise an annotation spec ("ipv4", "ipv4,bytes" or a list)."""
    if not ann:
        return []
    if isinstance(ann, str):
        ann = ann.split(",")
    return [a.strip() for a in ann if a and a.strip()]


def _client_annotations(annotations):
    """Return the client annotations (ipv4, mac, ...) as a comma-separated
    string, ignoring the native annotations coming from bf-rt.json."""
    return ",".join(a.value for a in annotations if a.name == CLIENT_ANNOTATION)


def table_entry_to_json(table_name, match_map, action_name=None, action_params=None,
                        match_annotations=None, action_annotations=None):
    """Build the JSON (dict) representation of a table entry."""
    json_data = {"table-name": table_name}
    if match_annotations:
        json_data["match-annotations"] = match_annotations
    if match_map:
        json_data["match-fields"] = match_map
    if action_name:
        json_data["action-name"] = action_name
    if action_annotations:
        json_data["action-annotations"] = action_annotations
    if action_params:
        json_data["action-params"] = action_params
    return json_data


###############################################################################
# Client
###############################################################################

class BFRTRuntimeClient:
    """
    BFRT Runtime client.

    :param device_id: device ID on the switch (usually 0)
    :param grpc_address: "ip:port" of bf_switchd's gRPC server
    :param client_id: gRPC client ID
    :param num_tries: subscribe attempts before giving up
    :param timeout: seconds to wait for each subscribe attempt
    :raises BFRTException: if the connection cannot be established
    """

    def __init__(self, device_id, grpc_address, client_id=DEF_CLIENT_ID,
                 num_tries=5, timeout=1):
        self.device_id = device_id
        self.grpc_address = grpc_address
        self.client_id = client_id
        self.profile = DEF_PROFILE
        self.p4_name = None
        self.bfrt_info = None
        self.all_tables = []
        self.tables = []
        self.interface = None
        self._target = gc.Target(device_id)

        LOGGER.debug("Connecting to device %d at %s", device_id, grpc_address)
        try:
            self.interface = gc.ClientInterface(
                self.grpc_address, client_id=client_id, device_id=device_id,
                notifications=None, num_tries=num_tries, timeout=timeout)
        except Exception as e:
            msg = f"Failed to connect to {grpc_address}: {e}"
            LOGGER.error(msg)
            raise BFRTException(msg) from e
        LOGGER.info("Connected to %s", grpc_address)

    def bfrt_get_info(self, p4_name=None):
        """
        Retrieve the BfRt info of a P4 program and bind this client to it.

        :param p4_name: program name; the first program on the device if None
        :return: _BfRtInfo object
        """
        LOGGER.debug("Retrieving bfrt info from device %d at %s",
                     self.device_id, self.grpc_address)
        try:
            self.bfrt_info = self.interface.bfrt_info_get(p4_name)
        except Exception as e:
            raise BFRTException(f"Cannot retrieve bfrt info: {e}") from e

        self.p4_name = self.bfrt_info.p4_name_get()
        self.all_tables = self.bfrt_info.table_name_list_get()
        self.tables = []
        for table_name in self.all_tables:
            table = self.bfrt_info.table_get(table_name)
            if "EntryScope" in table.info.attributes_supported_get():
                self.tables.append(table_name)

        try:
            LOGGER.debug("Binding pipeline config for P4 program %s", self.p4_name)
            self.interface.bind_pipeline_config(self.p4_name)
        except Exception as e:
            raise BFRTException(f"Binding failure for P4 program {self.p4_name}: {e}") from e

        return self.bfrt_info

    @parse_bfrt_error
    def bfrt_get_table(self, table_name):
        """Return the BFRT table object for ``table_name``."""
        return self.bfrt_info.table_get(table_name)

    def bfrt_get_tables(self):
        """Names of the tables that support entries (EntryScope attribute)."""
        return self.tables

    def bfrt_get_all_tables(self):
        """Names of all tables, including fixed-function ($PORT, $pre.*...)."""
        return self.all_tables

    @parse_bfrt_error
    def bfrt_get_table_entries(self, table, filter_match_map=None,
                               filter_action_name=None, filter_data_map=None):
        """
        Read the entries of a table as a list of JSON-like dicts.

        :param table: BFRT table object
        :param filter_match_map: a match dict (or list of them) to read
            specific entries only
        :param filter_action_name: action name for the required data
        :param filter_data_map: data fields to read (values are ignored)
        :return: list of entries (see table_entry_to_json)
        """
        table_name = table.info.name_get()

        key_list = None
        if filter_match_map:
            matches = [filter_match_map] if isinstance(filter_match_map, dict) else filter_match_map
            key_list = [self.bfrt_build_key_list(table, m) for m in matches]

        required_data = None
        if filter_action_name or filter_data_map:
            required_data = self.bfrt_build_data_list(
                table, filter_action_name, filter_data_map or {}, get=True)

        entries = table.entry_get(self._target, key_list=key_list,
                                  flags={"from_hw": True}, required_data=required_data)
        table_entries = []
        for data, key in entries:
            key_dict = key.to_dict()
            data_dict = data.to_dict()
            action_name = data_dict.get("action_name")

            match_map, match_annotations = {}, {}
            for k, v in key_dict.items():
                val = filter_key_value(table.info.key_field_match_type_get(k), v)
                if val is None:
                    continue
                match_map[k] = val
                ann = self.bfrt_get_key_field_annotations(table, k)
                if ann:
                    match_annotations[k] = ann

            action_params, action_annotations = {}, {}
            for p, v in data_dict.items():
                if p in ("action_name", "is_default_entry"):
                    continue
                action_params[p] = v
                ann = self.bfrt_get_action_data_annotations(table, action_name, p)
                if ann:
                    action_annotations[p] = ann

            table_entries.append(table_entry_to_json(
                table_name, match_map, action_name, action_params,
                match_annotations, action_annotations))
        return table_entries

    @parse_bfrt_error
    def bfrt_count_table_entries(self, table):
        """Number of entries in a table (read from hardware)."""
        entries = table.entry_get(self._target, None, {"from_hw": True})
        return sum(1 for _ in entries)

    @parse_bfrt_error
    def bfrt_build_key_tuple(self, key, val):
        """See :func:`build_key_tuple`."""
        return build_key_tuple(key, val)

    @parse_bfrt_error
    def bfrt_get_key_field_annotations(self, table, key_field_name):
        """Client annotations (ipv4, mac...) of a key field, comma separated."""
        return _client_annotations(table.info.key_field_annotations_get(key_field_name))

    @parse_bfrt_error
    def bfrt_get_key_field(self, table, key_field_name):
        """Description of a key field (match type, type, size...)."""
        info = table.info
        return {
            "key_field_name": key_field_name,
            "match_type": info.key_field_match_type_get(key_field_name),
            "type": info.key_field_type_get(key_field_name),
            "size": info.key_field_size_get(key_field_name),
            "repeated": info.key_field_repeated_get(key_field_name),
            "mandatory": info.key_field_mandatory_get(key_field_name),
            "annotations": ",".join(
                a.value for a in info.key_field_annotations_get(key_field_name)),
        }

    @parse_bfrt_error
    def bfrt_build_key_list(self, table, key_dict):
        """Build a table key from a match dict."""
        return table.make_key([build_key_tuple(k, v) for k, v in key_dict.items()])

    @parse_bfrt_error
    def bfrt_set_key_list_annotations(self, table, key_ann_dict):
        """Add client annotations (e.g. {"ipv4_dst": "ipv4"}) to key fields."""
        for key, anns in key_ann_dict.items():
            current = table.info.key_field_annotations_get(key)
            for ann in split_annotations(anns):
                if f"{CLIENT_ANNOTATION}.{ann}" not in current:
                    table.info.key_field_annotation_add(key, ann)

    @parse_bfrt_error
    def bfrt_get_action_data_annotations(self, table, action_name, data_field_name):
        """Client annotations of an action data field, comma separated."""
        return _client_annotations(
            table.info.data_field_annotations_get(data_field_name, action_name))

    @parse_bfrt_error
    def bfrt_get_action_data_field(self, table, action_name, data_field_name):
        """Description of an action data field."""
        info = table.info
        return {
            "data_field_name": data_field_name,
            "type": info.data_field_type_get(data_field_name, action_name),
            "size": info.data_field_size_get(data_field_name, action_name),
            "repeated": info.data_field_repeated_get(data_field_name, action_name),
            "mandatory": info.data_field_mandatory_get(data_field_name, action_name),
            "read_only": info.data_field_read_only_get(data_field_name, action_name),
            "choices": info.data_field_allowed_choices_get(data_field_name, action_name),
            "annotations": ",".join(
                a.value for a in info.data_field_annotations_get(data_field_name, action_name)),
        }

    @parse_bfrt_error
    def bfrt_build_data_list(self, table, action_name, param_dict, get=False):
        """Build table data from an action-params dict.

        With ``get=True`` only the field names are used (to request them).
        """
        def build_data_tuple(par, val):
            if par.startswith("$") and val is not None:
                if isinstance(val, str):
                    return gc.DataTuple(par, str_val=val)
                if isinstance(val, bool):
                    return gc.DataTuple(par, bool_val=val)
                if isinstance(val, float):
                    return gc.DataTuple(par, float_val=val)
                if isinstance(val, list):
                    par_type = table.info.data_field_type_get(par, action_name)
                    if par_type == "bool":
                        return gc.DataTuple(par, bool_arr_val=val)
                    if par_type == "string":
                        return gc.DataTuple(par, str_arr_val=val)
                    return gc.DataTuple(par, int_arr_val=val)
            return gc.DataTuple(par, val)

        if get:
            tuples = [build_data_tuple(p, None) for p in param_dict]
            return table.make_data(tuples, action_name, get=True)
        tuples = [build_data_tuple(p, v) for p, v in param_dict.items()]
        return table.make_data(tuples, action_name)

    @parse_bfrt_error
    def bfrt_set_data_list_annotations(self, table, action_name, par_ann_dict):
        """Add client annotations (e.g. {"dstAddr": "mac"}) to action data fields."""
        for par, anns in par_ann_dict.items():
            current = table.info.data_field_annotations_get(par, action_name)
            for ann in split_annotations(anns):
                if f"{CLIENT_ANNOTATION}.{ann}" not in current:
                    table.info.data_field_annotation_add(par, action_name, ann)

    def _write(self, op_name, method, table, key_dict, action_name=None,
               param_dict=None, with_data=True):
        try:
            key_list = [self.bfrt_build_key_list(table, key_dict)]
            if with_data:
                data_list = [self.bfrt_build_data_list(table, action_name, param_dict or {})]
                method(self._target, key_list, data_list)
            else:
                method(self._target, key_list)
        except BFRTException:
            raise
        except Exception as e:
            raise BFRTException(
                f"Cannot {op_name} entry in table {table.info.name_get()}: {e}") from e

    def bfrt_add_table_entry(self, table, key_dict, action_name, param_dict):
        """Insert an entry. Fails if it already exists."""
        self._write("add", table.entry_add, table, key_dict, action_name, param_dict)

    def bfrt_mod_table_entry(self, table, key_dict, action_name, param_dict):
        """Modify an existing entry."""
        self._write("modify", table.entry_mod, table, key_dict, action_name, param_dict)

    def bfrt_add_or_mod_table_entry(self, table, key_dict, action_name, param_dict):
        """Insert an entry, or modify it if it already exists."""
        self._write("add/modify", table.entry_add_or_mod, table, key_dict,
                    action_name, param_dict)

    def bfrt_del_table_entry(self, table, key_dict):
        """Delete one entry."""
        self._write("delete", table.entry_del, table, key_dict, with_data=False)

    def bfrt_del_table_entries(self, table):
        """Delete all entries of a table."""
        try:
            table.entry_del(self._target)
        except Exception as e:
            raise BFRTException(
                f"Cannot delete entries of table {table.info.name_get()}: {e}") from e

    def bfrt_set_default_entry(self, table, action_name, param_dict):
        """Set the default action (and its parameters) of a table."""
        try:
            data = self.bfrt_build_data_list(table, action_name, param_dict or {})
            table.default_entry_set(self._target, data)
        except Exception as e:
            raise BFRTException(
                f"Cannot set default entry of table {table.info.name_get()}: {e}") from e

    def bfrt_reset_default_entry(self, table):
        """Restore the default action declared in the P4 program."""
        try:
            table.default_entry_reset(self._target)
        except Exception as e:
            raise BFRTException(
                f"Cannot reset default entry of table {table.info.name_get()}: {e}") from e

    @parse_bfrt_error
    def bfrt_get_default_entry(self, table):
        """Default entry of a table as {"action-name": ..., "action-params": {...}}."""
        for data, _ in table.default_entry_get(self._target, flags={"from_hw": True}):
            data_dict = data.to_dict()
            action_name = data_dict.pop("action_name", None)
            data_dict.pop("is_default_entry", None)
            entry = {"table-name": table.info.name_get()}
            if action_name:
                entry["action-name"] = action_name
            if data_dict:
                entry["action-params"] = data_dict
            return entry
        return None

    def set_fwd_pipe_config(self, p4_name, tofino_bin_path, bfrt_json_path,
                            context_json_path, pipe_list=DEF_PIPE_LIST, profile=None):
        """
        Load a compiled P4 program on the switch.

        :param p4_name: program name
        :param tofino_bin_path: path to tofino.bin / tofino2.bin / tofino3.bin
        :param bfrt_json_path: path to bf-rt.json
        :param context_json_path: path to context.json
        :param pipe_list: pipes the profile is loaded on
        :param profile: profile name (default "pipe")
        """
        self.p4_name = p4_name
        profile = profile or self.profile
        try:
            LOGGER.info("Setting forwarding pipeline config for %s", p4_name)
            prof = gc.ProfileInfo(profile, context_json_path, tofino_bin_path, list(pipe_list))
            cfg = [gc.ForwardingConfig(p4_name, bfrt_json_path, [prof])]
            action = bfruntime_pb2.SetForwardingPipelineConfigRequest.VERIFY_AND_WARM_INIT_BEGIN_AND_END
            success = self.interface.send_set_forwarding_pipeline_config_request(action, "", cfg)
        except Exception as e:
            raise BFRTException(f"Cannot set forwarding pipeline config {p4_name}: {e}") from e
        if not success:
            raise BFRTException(f"Failure setting forwarding pipeline config {p4_name}")
        LOGGER.info("Forwarding pipeline config set for %s", p4_name)

    def tear_down(self):
        """Close the gRPC stream with the switch."""
        if self.interface is None:
            return
        LOGGER.debug("Tearing down gRPC session with %s", self.grpc_address)
        try:
            self.interface.tear_down_stream()
        except Exception as e:
            raise BFRTException(f"Error tearing down session with {self.grpc_address}: {e}") from e
        finally:
            self.interface = None
