"""Pair start/end normalized events for duration and open-stall analysis."""


def event_timing_dataframes(
    normalized_records,
    *,
    start_type,
    end_type,
    group_key="resource_id",
    start_extra_columns=None,
):
    """
    Match start/end event pairs for completed durations, and list open events
    (started but not completed) measured to the last log timestamp.

    Returns (df, df_merged, df_open).
    """
    import pandas as pd

    start_extra_columns = start_extra_columns or []
    df = pd.json_normalize(normalized_records)
    empty = pd.DataFrame()
    if df.empty or "type" not in df.columns:
        return df, empty, empty

    starts = (
        df[df["type"] == start_type]
        .copy()
        .sort_values([group_key, "timestamp"])
    )
    if starts.empty:
        return df, empty, empty

    start_cols = []
    for col in [group_key, "resource", "resource_type", *start_extra_columns]:
        if col in starts.columns and col not in start_cols:
            start_cols.append(col)

    starts["start_timestamp"] = pd.to_datetime(starts["timestamp"], utc=True, errors="coerce")
    starts["run"] = starts.groupby(group_key).cumcount() + 1
    starts = starts[start_cols + ["run", "start_timestamp"]]

    ends = (
        df[df["type"] == end_type]
        .copy()
        .sort_values([group_key, "timestamp"])
    )
    ends["end_timestamp"] = pd.to_datetime(ends["timestamp"], utc=True, errors="coerce")
    ends["run"] = ends.groupby(group_key).cumcount() + 1
    end_cols = [group_key, "run", "end_timestamp"]
    if "elapsed_seconds" in ends.columns:
        end_cols.append("elapsed_seconds")
    ends = ends[end_cols]

    df_merged = starts.merge(ends, on=[group_key, "run"], how="inner")
    df_merged["time_diff_minutes"] = (
        (df_merged["end_timestamp"] - df_merged["start_timestamp"]).dt.total_seconds() / 60
    )

    df_open = starts.merge(ends[[group_key, "run", "end_timestamp"]], on=[group_key, "run"], how="left")
    df_open = df_open[df_open["end_timestamp"].isna()].copy()
    if not df_open.empty:
        log_end = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").max()
        df_open["open_minutes"] = (
            (log_end - df_open["start_timestamp"]).dt.total_seconds() / 60
        )
        df_open = df_open.sort_values("open_minutes", ascending=False)

    return df, df_merged, df_open
