"""Carry last MQTT field values forward before ML (sparse / delta payloads)."""

from __future__ import annotations

import pandas as pd


def merge_partial_field_rows(
    wide_df: pd.DataFrame,
    *,
    use_bfill: bool = False,
    stale_seconds: int = 0,
) -> pd.DataFrame:
    """
    MQTT report-by-exception leaves NaN for omitted fields at a timestamp.
    Carry the last received value forward, then drop rows still incomplete.
    Does not invent zeros. Optional stale_seconds stops using a dead sensor.
    """
    if wide_df.empty:
        return wide_df
    wide_df = wide_df.sort_index()
    observed = wide_df.notna()
    filled = wide_df.ffill()
    if use_bfill:
        filled = filled.bfill()
    if stale_seconds and stale_seconds > 0:
        idx = pd.to_datetime(wide_df.index, utc=True, errors="coerce")
        max_age = pd.Timedelta(seconds=stale_seconds)
        times = pd.Series(idx, index=wide_df.index)
        for col in wide_df.columns:
            last_obs = times.where(observed[col]).ffill()
            age = times - last_obs
            filled[col] = filled[col].where(age.le(max_age))
    return filled.dropna()
