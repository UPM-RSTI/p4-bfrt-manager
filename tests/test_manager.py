import json
import pytest

from bfrt_manager import BFRTException, WriteOperation

LPM = "pipe.Ingress.ipv4_lpm"
ACL = "pipe.Ingress.acl"

ROUTE = {
    "table-name": LPM,
    "match-fields": {"hdr.ipv4.dst_addr": {"value": "10.0.0.0", "prefix_len": 8}},
    "match-annotations": {"hdr.ipv4.dst_addr": "ipv4"},
    "action-name": "Ingress.forward",
    "action-params": {"port": 0, "dst_mac": "00:10:94:00:00:01"},
    "action-annotations": {"dst_mac": "mac"},
}

PORT0 = {
    "table-name": "$PORT",
    "match-fields": {"$DEV_PORT": 0},
    "action-params": {"$SPEED": "BF_SPEED_100G", "$FEC": "BF_FEC_TYP_NONE", "$PORT_ENABLE": True},
}


def test_tables(manager):
    assert "$PORT" in manager.get_all_tables()
    assert "$PORT" not in manager.get_tables()
    assert set(manager.get_tables()) == {LPM, ACL}


def test_insert_and_read_back_roundtrip(manager):
    assert manager.table_entry_insert_from_json(ROUTE) == 1
    entries = manager.get_table_entries(LPM)
    assert entries == [ROUTE]


def test_port_zero_roundtrip(manager):
    manager.table_entry_insert_from_json(json.dumps([PORT0]))
    [entry] = manager.get_table_entries("$PORT")
    assert entry["match-fields"] == {"$DEV_PORT": 0}
    assert entry["action-params"]["$PORT_ENABLE"] is True


def test_ternary_wildcard_and_range(manager):
    entry = {
        "table-name": ACL,
        "match-fields": {
            "ig_port": 0,
            "src_addr": {"value": 0, "mask": 0},
            "l4_port": {"low": 80, "high": 90},
            "$MATCH_PRIORITY": 1,
        },
        "action-name": "Ingress.drop",
    }
    manager.table_entry_insert_from_json(entry)
    [read] = manager.get_table_entries(ACL)
    assert read["match-fields"] == {"ig_port": 0, "l4_port": {"low": 80, "high": 90},
                                    "$MATCH_PRIORITY": 1}


def test_annotations_not_duplicated(manager, client):
    manager.table_entry_insert_from_json(ROUTE)
    manager.table_entry_delete_from_json(ROUTE)
    manager.table_entry_insert_from_json(ROUTE)
    anns = client.bfrt_get_key_field_annotations(manager.get_table(LPM), "hdr.ipv4.dst_addr")
    assert anns == "ipv4"


def test_duplicate_insert_raises(manager):
    manager.table_entry_insert_from_json(ROUTE)
    with pytest.raises(BFRTException):
        manager.table_entry_insert_from_json(ROUTE)


def test_update_modifies(manager):
    manager.table_entry_insert_from_json(ROUTE)
    changed = dict(ROUTE, **{"action-params": {"port": 7, "dst_mac": "00:00:00:00:00:07"}})
    manager.table_entry_update_from_json(changed)
    [entry] = manager.get_table_entries(LPM)
    assert entry["action-params"]["port"] == 7


def test_update_missing_entry_raises(manager):
    with pytest.raises(BFRTException):
        manager.table_entry_operation_from_json(ROUTE, WriteOperation.update)


def test_bad_entry_does_not_hit_other_table(manager):
    bad = {"match-fields": {"hdr.ipv4.dst_addr": 1}}  # no table-name
    with pytest.raises(BFRTException) as exc:
        manager.table_entry_insert_from_json([ROUTE, bad, PORT0])
    assert "entry #1" in str(exc.value)
    # the valid entries were still applied, and nothing else
    assert len(manager.get_table_entries(LPM)) == 1
    assert len(manager.get_table_entries("$PORT")) == 1


def test_delete_and_counts(manager):
    manager.table_entry_insert_from_json(ROUTE)
    assert manager.count_table_entries(LPM) == 1
    assert manager.count_active_entries() == 1
    manager.table_entry_delete_from_json(ROUTE)
    assert manager.count_table_entries(LPM) == 0


def test_delete_missing_raises(manager):
    with pytest.raises(BFRTException):
        manager.table_entry_delete_from_json(ROUTE)


def test_delete_tables(manager):
    manager.table_entry_insert_from_json(ROUTE)
    manager.delete_tables()
    assert manager.count_active_entries() == 0


def test_resource_api(manager):
    assert manager.get_resource_keys() == ["table"]
    assert manager.insert_operation("/tables/table/x", ROUTE) is True
    assert manager.get_resource_entries("table") == [[ROUTE]]
    with pytest.raises(BFRTException):
        manager.get_resource_entries("counter")
    with pytest.raises(BFRTException):
        manager.apply_operation("counter", ROUTE, WriteOperation.insert)


def test_filtered_read(manager):
    manager.table_entry_insert_from_json(ROUTE)
    other = dict(ROUTE, **{"match-fields": {"hdr.ipv4.dst_addr": {"value": "192.168.0.0",
                                                                  "prefix_len": 16}}})
    manager.table_entry_insert_from_json(other)
    got = manager.get_table_entries(LPM, match_map=other["match-fields"])
    assert got == [other]


def test_print_spec(manager, capsys):
    manager.print_table_entries_spec("ipv4_lpm")
    out = capsys.readouterr().out
    assert f"table-name: '{LPM}'" in out
    assert "Ingress.forward" in out and "dst_mac" in out
    assert "bytes, " not in out  # choices column no longer repeats the type
    manager.print_table_entries_spec("$PORT")
    out = capsys.readouterr().out
    assert "$SPEED" in out and "BF_SPEED_25G" in out


def test_choices(manager, client):
    field = client.bfrt_get_action_data_field(manager.get_table("$PORT"), None, "$SPEED")
    assert field["choices"] == "BF_SPEED_10G, BF_SPEED_25G, BF_SPEED_100G"
    field = client.bfrt_get_action_data_field(manager.get_table(LPM), "Ingress.forward", "port")
    assert field["choices"] == ""


def test_unknown_table_suggests(manager):
    with pytest.raises(BFRTException) as exc:
        manager.get_table("ipv4_lmp")
    assert "not found" in str(exc.value) and "pipe.Ingress.ipv4_lpm" in str(exc.value)
