"""Write and load structured notebook reports for run-to-run comparison."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def report_path(source_path: str, suffix: str = "-report.json") -> str:
    """e.g. plan-v185-hang.log -> plan-v185-hang-report.json"""
    source = Path(source_path)
    return str(source.with_name(f"{source.stem}{suffix}"))


def dataframe_records(df, head: int = 20) -> list[dict]:
    if df is None or getattr(df, "empty", True):
        return []
    out = df.head(head).copy()
    for col in out.columns:
        if str(out[col].dtype).startswith("datetime64"):
            out[col] = out[col].astype(str)
    return json.loads(out.to_json(orient="records", date_format="iso"))


def verdict_records(verdicts) -> list[dict]:
    rows = []
    for verdict in verdicts or []:
        category = verdict.category
        rows.append(
            {
                "category": category,
                "layer": verdict.layer or verdict_layer(category),
                "summary": verdict.summary,
                "detail": verdict.detail,
                "score": verdict.score,
            }
        )
    return rows


def summary_dict(summary: dict, *, primary_layer: str | None = None) -> dict:
    """Drop non-JSON verdict objects from hang summary."""
    out = dict(summary)
    out["verdicts"] = verdict_records(out.get("verdicts"))
    if primary_layer is not None:
        out["primary_layer"] = primary_layer
    elif out.get("primary_layer") is None and out.get("verdicts"):
        out["primary_layer"] = out["verdicts"][0].get("layer")
    return out


def issue_attribution_bundle(counters, summary: dict, workflow: str) -> dict:
    """Summary + issue_attribution for *-report.json export."""
    import commonlib.prep_hang_data as hang

    attribution = hang.build_issue_attribution(counters, summary, workflow)
    return {
        "issue_attribution": attribution,
        "summary": summary_dict(summary, primary_layer=attribution.get("primary_layer")),
    }


def sdk_report_sections(
    counters,
    duration_minutes: float | None = None,
    min_retry: int = 3,
    min_404: int = 2,
) -> dict:
    """Shared SDK tables for any *-report.json (hang or performance analysis)."""
    import commonlib.prep_hang_data as hang

    if duration_minutes is None:
        duration_minutes = hang._duration_minutes(counters)

    df_retries = hang.sdk_retry_dataframe(counters, min_count=min_retry)
    df_404 = hang.sdk_not_found_dataframe(counters, min_count=min_404)
    df_429_wait = hang.sdk_429_wait_dataframe(counters)
    df_429_wait_by_type = hang.sdk_429_wait_by_resource_type_dataframe(counters)
    df_sdk_rates = hang.sdk_call_rates_dataframe(counters, duration_minutes=duration_minutes)
    df_sdk_rates_by_type = hang.sdk_call_rates_by_resource_type_dataframe(
        counters, duration_minutes=duration_minutes
    )
    df_sdk_timeline = hang.sdk_timeline_dataframe(counters, bucket_minutes=1.0)
    sdk_timeline_summary = hang.sdk_timeline_summary(counters, bucket_minutes=1.0)
    return {
        "sdk_retries": dataframe_records(df_retries),
        "sdk_404": dataframe_records(df_404),
        "sdk_429_wait": dataframe_records(df_429_wait, head=30),
        "sdk_429_wait_by_resource_type": dataframe_records(df_429_wait_by_type, head=20),
        "sdk_call_rates": dataframe_records(df_sdk_rates, head=50),
        "sdk_call_rates_by_resource_type": dataframe_records(df_sdk_rates_by_type, head=30),
        "sdk_timeline": dataframe_records(df_sdk_timeline, head=500),
        "sdk_timeline_summary": sdk_timeline_summary,
    }


def write_run_report(source_path: str, workflow: str, sections: dict) -> str:
    """Write {capture-stem}-report.json next to the capture (hang or completed runs)."""
    path = report_path(source_path)
    report = {
        "source_log": source_path,
        "workflow": workflow,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **sections,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(f"Wrote report: {path}")
    return path


def write_hang_report(source_path: str, workflow: str, sections: dict) -> str:
    """Back-compat alias for write_run_report."""
    return write_run_report(source_path, workflow, sections)


def load_report(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
