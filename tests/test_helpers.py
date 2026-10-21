import pytest

from bfrt_manager import BFRTException
from bfrt_manager.client import build_key_tuple, filter_key_value, split_annotations, _is_zero


@pytest.mark.parametrize("val, match_type", [
    (5, "Exact"),
    ({"value": 5}, "Exact"),
    ({"value": 5, "mask": 0xff}, "Ternary"),
    ({"value": 5, "prefix_len": 8}, "LPM"),
    ({"value": 5, "is_valid": True}, "Optional"),
    ({"low": 10, "high": 20}, "Range"),
])
def test_build_key_tuple(val, match_type):
    assert build_key_tuple("f", val).match_type == match_type


@pytest.mark.parametrize("val", [{}, {"mask": 1}, {"value": 1, "mask": 1, "prefix_len": 2}, {"low": 1}])
def test_build_key_tuple_invalid(val):
    with pytest.raises(BFRTException):
        build_key_tuple("f", val)


def test_filter_exact_zero_is_kept():
    assert filter_key_value("Exact", {"value": 0}) == 0


@pytest.mark.parametrize("mask", [0, "0.0.0.0", "00:00:00:00:00:00", "::", bytearray(4)])
def test_filter_ternary_wildcard_dropped(mask):
    assert filter_key_value("Ternary", {"value": 0, "mask": mask}) is None


def test_filter_ternary_kept():
    v = {"value": "10.0.0.0", "mask": "255.0.0.0"}
    assert filter_key_value("Ternary", v) == v


def test_filter_lpm():
    assert filter_key_value("LPM", {"value": 0, "prefix_len": 0}) is None
    v = {"value": "10.0.0.0", "prefix_len": 8}
    assert filter_key_value("LPM", v) == v


def test_split_annotations():
    assert split_annotations("ipv4, bytes") == ["ipv4", "bytes"]
    assert split_annotations(["mac"]) == ["mac"]
    assert split_annotations("") == []


def test_is_zero():
    assert _is_zero("0.0.0.0") and not _is_zero("10.0.0.0") and not _is_zero(1)
