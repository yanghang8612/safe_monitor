from safe_monitor.sources.x_search_polling import (
    _pack_handles_into_query_batches,
    _parse_created_at,
)


def test_pack_single_batch_when_under_budget():
    handles = ["a", "b", "c"]
    batches = _pack_handles_into_query_batches(handles, budget=500)
    assert batches == [["a", "b", "c"]]


def test_pack_splits_when_exceeding_budget():
    # Each "from:xxxx" + " OR " ≈ 13 chars. Budget 30 fits 2 per batch.
    handles = ["aaaa", "bbbb", "cccc", "dddd"]
    batches = _pack_handles_into_query_batches(handles, budget=30)
    assert all(sum(len(f"from:{h}") for h in b) + 4 * (len(b) - 1) <= 30 for b in batches)
    assert sum(len(b) for b in batches) == 4
    assert len(batches) >= 2


def test_pack_empty_input():
    assert _pack_handles_into_query_batches([], budget=500) == []


def test_pack_oversized_single_handle_still_emits_solo_batch():
    long_handle = "x" * 600
    batches = _pack_handles_into_query_batches([long_handle, "y"], budget=500)
    assert batches[0] == [long_handle]
    assert batches[1] == ["y"]


def test_parse_iso_with_z_suffix():
    assert _parse_created_at("2024-09-22T12:34:56Z") == 1727008496


def test_parse_iso_with_offset():
    assert _parse_created_at("2024-09-22T12:34:56+00:00") == 1727008496


def test_parse_legacy_twitter_format():
    assert _parse_created_at("Sun Sep 22 12:34:56 +0000 2024") == 1727008496


def test_parse_unix_int():
    assert _parse_created_at(1727008496) == 1727008496


def test_parse_invalid_returns_none():
    assert _parse_created_at("not a date") is None
    assert _parse_created_at(None) is None
    assert _parse_created_at({"weird": "shape"}) is None
