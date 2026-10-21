import pytest

from bfrt_manager import BFRTException
from bfrt_manager.bfrt_grpc import client as gc

LPM = "pipe.Ingress.ipv4_lpm"


def test_add_and_get_ports(manager):
    manager.add_port(0, speed="BF_SPEED_25G")
    manager.add_port(8, enable=False, AUTO_NEGOTIATION="PM_AN_FORCE_DISABLE")
    ports = manager.get_ports()
    assert set(ports) == {0, 8}
    assert ports[0]["$SPEED"] == "BF_SPEED_25G" and ports[0]["$PORT_ENABLE"] is True
    assert ports[8]["$AUTO_NEGOTIATION"] == "PM_AN_FORCE_DISABLE"


def test_enable_disable_delete_port(manager):
    manager.add_port(4)
    manager.set_port_enabled(4, False)
    assert manager.get_ports()[4]["$PORT_ENABLE"] is False
    assert manager.get_ports()[4]["$SPEED"] == "BF_SPEED_100G"
    manager.delete_port(4)
    assert manager.get_ports() == {}


def test_port_name_resolution(manager, client):
    t = manager.get_table("$PORT_STR_INFO")
    t.entry_add(client._target, [t.make_key([gc.KeyTuple("$PORT_NAME", "1/0")])],
                [t.make_data([gc.DataTuple("$DEV_PORT", 132)])])
    assert manager.get_dev_port("1/0") == 132
    assert manager.add_port("1/0") == 132
    assert 132 in manager.get_ports()
    with pytest.raises(BFRTException):
        manager.get_dev_port("9/9")


def test_port_stats(manager, client):
    manager.add_port(4)
    manager.add_port(8)
    t = manager.get_table("$PORT_STAT")
    for port, rx in ((4, 10), (8, 20)):
        t.entry_add(client._target, [t.make_key([gc.KeyTuple("$DEV_PORT", port)])],
                    [t.make_data([gc.DataTuple("$FramesReceivedOK", rx)])])
    stats = manager.get_port_stats()
    assert stats[4]["$FramesReceivedOK"] == 10 and stats[8]["$FramesReceivedOK"] == 20
    assert list(manager.get_port_stats([8])) == [8]


def test_multicast(manager):
    manager.add_multicast(mgid=1, ports=[16, 24])
    [node] = manager.get_table_entries("$pre.node")
    assert node["match-fields"] == {"$MULTICAST_NODE_ID": 1}
    assert node["action-params"]["$DEV_PORT"] == [16, 24]
    [grp] = manager.get_table_entries("$pre.mgid")
    assert grp["action-params"]["$MULTICAST_NODE_ID"] == [1]
    assert grp["action-params"]["$MULTICAST_NODE_L1_XID_VALID"] == [False]
    manager.delete_multicast(1)
    assert manager.get_table_entries("$pre.node") == []
    assert manager.get_table_entries("$pre.mgid") == []


def test_multicast_xids(manager):
    manager.add_multicast_node(1, [4])
    manager.add_multicast_node(2, [8], rid=5)
    manager.add_multicast_group(10, [1, 2], xids=[None, 7])
    [grp] = manager.get_table_entries("$pre.mgid")
    assert grp["action-params"]["$MULTICAST_NODE_L1_XID_VALID"] == [False, True]
    assert grp["action-params"]["$MULTICAST_NODE_L1_XID"] == [0, 7]
    with pytest.raises(BFRTException):
        manager.add_multicast_group(11, [1, 2], xids=[1])


def test_default_entry(manager):
    assert manager.get_default_entry(LPM) is None
    manager.set_match_fields_annotations(LPM, {})
    manager.set_action_annotations(LPM, "Ingress.forward", {"dst_mac": "mac"})
    manager.set_default_entry(LPM, "Ingress.forward", {"port": 3, "dst_mac": "00:00:00:00:00:03"})
    entry = manager.get_default_entry(LPM)
    assert entry["action-name"] == "Ingress.forward"
    assert entry["action-params"] == {"port": 3, "dst_mac": "00:00:00:00:00:03"}
    manager.reset_default_entry(LPM)
    assert manager.get_default_entry(LPM) is None


def test_load_program_missing_files(manager, tmp_path):
    with pytest.raises(FileNotFoundError):
        manager.load_program("p", str(tmp_path / "bf-rt.json"), "x", "y")
