import pandas as pd

from field_fill import merge_partial_field_rows


def test_ffill_omitted_fields_then_drop_incomplete_start():
    idx = pd.to_datetime(["2026-08-18T10:00:00Z", "2026-08-18T10:05:00Z", "2026-08-18T10:10:00Z"])
    wide = pd.DataFrame(
        {
            "temp": [22.5, 22.8, 23.0],
            "humidity": [50.1, None, None],
            "status": [1.0, None, 0.0],
        },
        index=idx,
    )
    out = merge_partial_field_rows(wide, use_bfill=False)
    assert list(out["humidity"]) == [50.1, 50.1, 50.1]
    assert list(out["status"]) == [1.0, 1.0, 0.0]
    assert list(out["temp"]) == [22.5, 22.8, 23.0]


def test_does_not_invent_values_before_first_sample():
    idx = pd.to_datetime(["2026-08-18T10:00:00Z", "2026-08-18T10:05:00Z"])
    wide = pd.DataFrame({"temp": [22.5, 22.8], "humidity": [None, 50.1]}, index=idx)
    out = merge_partial_field_rows(wide, use_bfill=False)
    assert len(out) == 1
    assert float(out["humidity"].iloc[0]) == 50.1


def test_stale_values_are_dropped():
    idx = pd.to_datetime(["2026-08-18T10:00:00Z", "2026-08-18T10:20:00Z"])
    wide = pd.DataFrame({"temp": [22.5, 22.8], "humidity": [50.1, None]}, index=idx)
    out = merge_partial_field_rows(wide, stale_seconds=60)
    assert len(out) == 1
    assert out.index[0] == idx[0]
