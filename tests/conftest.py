"""
Test fixtures: a BFRTRuntimeClient backed by real bfrt_grpc table objects
(parsed from tests/data/bf-rt.json) whose read/write RPCs are replaced by an
in-memory store, so no switch is needed.
"""
import os
import pytest

from bfrt_manager import BFRTManager, BFRTRuntimeClient
from bfrt_manager.bfrt_grpc import client as gc

DATA = os.path.join(os.path.dirname(__file__), "data")


class _MergedData:
    """Result of a partial modify: the old data updated with the new fields."""

    def __init__(self, old, new):
        self._dict = dict(old.to_dict())
        self._dict.update({k: v for k, v in new.to_dict().items()
                           if k not in ("action_name", "is_default_entry") and v is not None})

    def to_dict(self):
        return dict(self._dict)


class FakeSwitchTable:
    """Replace the RPC methods of a gc._Table with an in-memory store."""

    def __init__(self, table):
        self.table = table
        self.entries = {}  # frozen key dict -> (key, data)
        self.default = None
        for name in ("entry_add", "entry_mod", "entry_add_or_mod", "entry_del", "entry_get",
                     "default_entry_set", "default_entry_reset", "default_entry_get"):
            setattr(table, name, getattr(self, name))

    @staticmethod
    def _id(key):
        return repr(sorted(key.to_dict().items()))

    def entry_add(self, target, key_list, data_list):
        for k, d in zip(key_list, data_list):
            if self._id(k) in self.entries:
                raise RuntimeError("ALREADY_EXISTS")
            self.entries[self._id(k)] = (k, d)

    def entry_mod(self, target, key_list, data_list):
        for k, d in zip(key_list, data_list):
            if self._id(k) not in self.entries:
                raise RuntimeError("NOT_FOUND")
            old = self.entries[self._id(k)][1]
            self.entries[self._id(k)] = (k, _MergedData(old, d))

    def entry_add_or_mod(self, target, key_list, data_list):
        for k, d in zip(key_list, data_list):
            self.entries[self._id(k)] = (k, d)

    def entry_del(self, target, key_list=None):
        if key_list is None:
            self.entries.clear()
            return
        for k in key_list:
            if self.entries.pop(self._id(k), None) is None:
                raise RuntimeError("NOT_FOUND")

    def default_entry_set(self, target, data):
        self.default = data

    def default_entry_reset(self, target):
        self.default = None

    def default_entry_get(self, target, flags=None):
        return [(self.default, None)] if self.default is not None else []

    def entry_get(self, target, key_list=None, flags=None, required_data=None):
        items = list(self.entries.values())
        if key_list:
            wanted = {self._id(k) for k in key_list}
            items = [i for i in items if self._id(i[0]) in wanted]
        return [(d, k) for k, d in items]


@pytest.fixture
def client():
    with open(os.path.join(DATA, "bf-rt.json"), "rb") as f:
        p4 = f.read()
    with open(os.path.join(DATA, "non_p4.json"), "rb") as f:
        nonp4 = f.read()
    info = gc._BfRtInfo("test_prog", p4, nonp4, None)

    cli = object.__new__(BFRTRuntimeClient)
    cli.device_id = 0
    cli.grpc_address = "fake:50052"
    cli.client_id = 0
    cli.profile = "pipe"
    cli.interface = None
    cli._target = gc.Target(0)
    cli.bfrt_info = info
    cli.p4_name = "test_prog"
    cli.all_tables = info.table_name_list_get()
    cli.tables = [t for t in cli.all_tables
                  if "EntryScope" in info.table_get(t).info.attributes_supported_get()]
    cli.fake = {t: FakeSwitchTable(info.table_get(t)) for t in cli.all_tables}
    return cli


@pytest.fixture
def manager(client):
    mgr = BFRTManager(0, "fake", 50052, client=client)
    mgr.all_tables = client.all_tables
    mgr.tables = client.tables
    return mgr
