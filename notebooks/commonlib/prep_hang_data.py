"""
Detect likely causes of hanging or very slow Terraform / Genesys Cloud activity
from TF_LOG=json trace output.

Supports plan, export, and apply workflows with shared SDK/tail detection plus
workflow-specific stall patterns.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from commonlib.classify_tf_log import classify_file, TfLogClassification
from commonlib.prep_sdk_data import strip_and_replace_guid
from commonlib.prep_tf_log_trace import (
    REFRESH_COMPLETE_RE,
    REFRESH_LINE_RE,
    REFRESH_START_RE,
    VERTEX_START_RE,
    is_graph_resource,
    parse_address,
)
import commonlib.config as cfg

DAG_WAIT_RE = re.compile(
    r'dag/walk: vertex "(?P<vertex>[^"]+)" is waiting for "(?P<waiting_for>[^"]+)"'
)
EXPORT_START_RE = re.compile(
    r"Started processing for resource:\s(?P<type>[^.]+)\.(?P<label>\S+)\s\((?P<id>[^)]+)\)"
)
EXPORT_END_RE = re.compile(
    r"Collected resource:\sType=(?P<type>[^,]+),\s*BlockLabel=(?P<label>[^,]+),\s*ID=(?P<id>.+)$"
)


def read_json_from_file(file_path: str) -> list[dict]:
    logger = cfg.configure_capture_error_log(file_path, "parse-hang-errors")
    records = []
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.error("Failed to parse line at %s: %s", exc.lineno, exc)
    return records


def classify_log(file_path: str) -> TfLogClassification:
    return classify_file(file_path)


def _parse_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sdk_payload(message: str) -> dict | None:
    if "SDK DEBUG" not in message:
        return None
    brace = message.find("{")
    if brace < 0:
        return None
    try:
        return json.loads(message[brace:])
    except json.JSONDecodeError:
        return None


def _method_url(method: str, url: str) -> str:
    return f"{method} {strip_and_replace_guid(url)}"


def _retry_after_seconds(sdk: dict) -> int:
    retry_after = sdk.get("invocation_retry_after")
    if retry_after is None:
        return 0
    try:
        return max(0, int(retry_after))
    except (TypeError, ValueError):
        return 0


def sdk_429_wait_total_seconds(counters: HangScanCounters) -> int:
    return int(sum(counters.sdk_429_wait_seconds.values()))


def _export_resource_key(resource_type: str, label: str) -> str:
    return f"{resource_type}.{label}"


@dataclass
class HangScanCounters:
    parsed_lines: int = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None

    workflows: set[str] = field(default_factory=set)

    dag_waits: Counter = field(default_factory=Counter)
    vertex_starts: Counter = field(default_factory=Counter)
    refresh_starts: Counter = field(default_factory=Counter)
    refresh_completes: Counter = field(default_factory=Counter)
    plan_rpc_requests: Counter = field(default_factory=Counter)

    export_starts: Counter = field(default_factory=Counter)
    export_completes: Counter = field(default_factory=Counter)
    export_start_by_id: Counter = field(default_factory=Counter)

    apply_starts: Counter = field(default_factory=Counter)
    apply_completes: Counter = field(default_factory=Counter)
    apply_rpc_open: Counter = field(default_factory=Counter)

    sdk_requests: Counter = field(default_factory=Counter)
    sdk_responses: Counter = field(default_factory=Counter)
    sdk_retries: Counter = field(default_factory=Counter)
    sdk_404: Counter = field(default_factory=Counter)
    sdk_429: Counter = field(default_factory=Counter)
    sdk_429_wait_seconds: Counter = field(default_factory=Counter)
    sdk_429_by_resource_type: Counter = field(default_factory=Counter)
    sdk_429_wait_by_resource_type: Counter = field(default_factory=Counter)
    sdk_status: Counter = field(default_factory=Counter)
    sdk_requests_by_resource_type: Counter = field(default_factory=Counter)
    sdk_responses_by_resource_type: Counter = field(default_factory=Counter)
    sdk_timeline_requests: dict[int, Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    sdk_timeline_404: dict[int, Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    sdk_timeline_dnclist_export_get: Counter = field(default_factory=Counter)
    sdk_timeline_dnclist_export_post: Counter = field(default_factory=Counter)

    tail_dag_waits: Counter = field(default_factory=Counter)
    tail_sdk_requests: Counter = field(default_factory=Counter)
    tail_sdk_retries: Counter = field(default_factory=Counter)
    tail_sdk_404: Counter = field(default_factory=Counter)
    tail_sdk_429: Counter = field(default_factory=Counter)
    tail_sdk_429_wait_seconds: Counter = field(default_factory=Counter)
    tail_messages: Counter = field(default_factory=Counter)
    tail_export_starts: Counter = field(default_factory=Counter)
    tail_export_messages: Counter = field(default_factory=Counter)
    tail_apply_starts: Counter = field(default_factory=Counter)


def _duration_minutes(counters: HangScanCounters) -> float | None:
    if counters.first_timestamp and counters.last_timestamp:
        return (counters.last_timestamp - counters.first_timestamp).total_seconds() / 60
    return None


def _timeline_bucket(
    counters: HangScanCounters,
    ts: datetime | None,
    bucket_seconds: int = 60,
) -> int | None:
    if ts is None or counters.first_timestamp is None:
        return None
    elapsed = (ts - counters.first_timestamp).total_seconds()
    if elapsed < 0:
        return 0
    return int(elapsed // bucket_seconds)


def _is_dnclist_export(method: str, url: str) -> bool:
    return "/dnclists/" in url and "/export" in url


def _record_sdk(
    counters: HangScanCounters,
    message: str,
    *,
    ts: datetime | None = None,
    tail: bool = False,
    bucket_seconds: int = 60,
) -> None:
    sdk = _sdk_payload(message)
    if not sdk:
        return

    debug_type = sdk.get("debug_type")
    method = sdk.get("invocation_method") or "?"
    url = sdk.get("invocation_url") or ""
    endpoint = _method_url(method, url)
    resource_type = sdk.get("resource_type") or "unknown"
    bucket = _timeline_bucket(counters, ts, bucket_seconds) if not tail else None

    if debug_type == "SDK DEBUG REQUEST":
        counters.sdk_requests[endpoint] += 1
        counters.sdk_requests_by_resource_type[resource_type] += 1
        if bucket is not None:
            counters.sdk_timeline_requests[bucket][endpoint] += 1
            if _is_dnclist_export(method, url):
                if method == "GET":
                    counters.sdk_timeline_dnclist_export_get[bucket] += 1
                elif method == "POST":
                    counters.sdk_timeline_dnclist_export_post[bucket] += 1
        if tail:
            counters.tail_sdk_requests[endpoint] += 1
        return

    if debug_type != "SDK DEBUG RESPONSE":
        return

    counters.sdk_responses[endpoint] += 1
    counters.sdk_responses_by_resource_type[resource_type] += 1
    status = sdk.get("invocation_status_code")
    if status is not None:
        counters.sdk_status[status] += 1
    retry_after = sdk.get("invocation_retry_after")
    if retry_after is not None and int(retry_after) > 0:
        counters.sdk_retries[endpoint] += 1
        if tail:
            counters.tail_sdk_retries[endpoint] += 1
    if status == 404:
        counters.sdk_404[endpoint] += 1
        if bucket is not None:
            counters.sdk_timeline_404[bucket][endpoint] += 1
        if tail:
            counters.tail_sdk_404[endpoint] += 1
    if status == 429:
        counters.sdk_429[endpoint] += 1
        wait_seconds = _retry_after_seconds(sdk)
        counters.sdk_429_wait_seconds[endpoint] += wait_seconds
        counters.sdk_429_by_resource_type[resource_type] += 1
        counters.sdk_429_wait_by_resource_type[resource_type] += wait_seconds
        if tail:
            counters.tail_sdk_429[endpoint] += 1
            counters.tail_sdk_429_wait_seconds[endpoint] += wait_seconds


def scan_hang_records(
    records: list[dict],
    tail_minutes: float = 5.0,
    classification: TfLogClassification | None = None,
) -> HangScanCounters:
    counters = HangScanCounters()
    timestamps: list[datetime] = []
    pending_apply_rpc: dict[str, str] = {}

    if classification:
        if classification.is_export:
            counters.workflows.add("export")
        if classification.is_plan:
            counters.workflows.add("plan")
        if classification.is_apply:
            counters.workflows.add("apply")

    for record in records:
        counters.parsed_lines += 1
        message = record.get("@message") or ""
        caller = record.get("@caller") or ""
        ts = _parse_timestamp(record.get("@timestamp"))
        if ts:
            timestamps.append(ts)
            if counters.first_timestamp is None:
                counters.first_timestamp = ts
            counters.last_timestamp = ts

        match = DAG_WAIT_RE.search(message)
        if match:
            counters.workflows.add("plan")
            key = (match.group("vertex"), match.group("waiting_for"))
            counters.dag_waits[key] += 1

        match = VERTEX_START_RE.search(message)
        if match:
            vertex = match.group("addr")
            if is_graph_resource(vertex):
                counters.vertex_starts[vertex] += 1

        match = REFRESH_START_RE.search(message) or REFRESH_LINE_RE.match(message)
        if match:
            counters.workflows.add("plan")
            counters.refresh_starts[match.group("addr")] += 1

        match = REFRESH_COMPLETE_RE.search(message)
        if match:
            counters.workflows.add("plan")
            counters.refresh_completes[match.group("addr")] += 1

        if record.get("tf_rpc") == "PlanResourceChange" and message == "Received request":
            counters.workflows.add("plan")
            resource_type = record.get("tf_resource_type") or "unknown"
            counters.plan_rpc_requests[resource_type] += 1

        if "genesyscloud_resource_exporter" in caller or EXPORT_START_RE.search(message) or EXPORT_END_RE.search(message):
            counters.workflows.add("export")

        match = EXPORT_START_RE.search(message)
        if match:
            key = _export_resource_key(match.group("type"), match.group("label"))
            counters.export_starts[key] += 1
            counters.export_start_by_id[match.group("id")] += 1

        match = EXPORT_END_RE.search(message)
        if match:
            key = _export_resource_key(match.group("type"), match.group("label"))
            counters.export_completes[key] += 1

        if record.get("tf_rpc") == "ApplyResourceChange":
            counters.workflows.add("apply")
            req_id = record.get("tf_req_id")
            resource_type = record.get("tf_resource_type") or "unknown"
            if message == "Received request" and req_id:
                pending_apply_rpc[req_id] = resource_type
                counters.apply_starts[resource_type] += 1
            elif message == "Received downstream response" and req_id:
                pending_apply_rpc.pop(req_id, None)
                counters.apply_completes[resource_type] += 1

        if record.get("@module") == "terraform.ui":
            record_type = record.get("type")
            if record_type == "apply_start":
                counters.workflows.add("apply")
                hook = record.get("hook") or {}
                resource_info = hook.get("resource") or {}
                addr = hook.get("id_value") or resource_info.get("addr") or "unknown"
                counters.apply_starts[addr] += 1
            elif record_type == "apply_complete":
                counters.workflows.add("apply")
                hook = record.get("hook") or {}
                resource_info = hook.get("resource") or {}
                addr = hook.get("id_value") or resource_info.get("addr") or "unknown"
                counters.apply_completes[addr] += 1

        _record_sdk(counters, message, ts=ts)

    for resource_type in pending_apply_rpc.values():
        counters.apply_rpc_open[resource_type] += 1

    if timestamps and tail_minutes > 0:
        cutoff = timestamps[-1].timestamp() - (tail_minutes * 60)
        for record in records:
            ts = _parse_timestamp(record.get("@timestamp"))
            if ts is None or ts.timestamp() < cutoff:
                continue

            message = record.get("@message") or ""
            caller = record.get("@caller") or ""

            match = DAG_WAIT_RE.search(message)
            if match:
                key = (match.group("vertex"), match.group("waiting_for"))
                counters.tail_dag_waits[key] += 1

            counters.tail_messages[message[:120]] += 1

            match = EXPORT_START_RE.search(message)
            if match:
                key = _export_resource_key(match.group("type"), match.group("label"))
                counters.tail_export_starts[key] += 1

            if "genesyscloud_resource_exporter" in caller or "Started processing" in message or "Collected resource" in message:
                counters.tail_export_messages[message[:120]] += 1

            if record.get("tf_rpc") == "ApplyResourceChange" and message == "Received request":
                resource_type = record.get("tf_resource_type") or "unknown"
                counters.tail_apply_starts[resource_type] += 1

            _record_sdk(counters, message, ts=ts, tail=True)

    if classification and not counters.workflows:
        counters.workflows.update(classification.categories)

    return counters


# Backwards-compatible alias
scan_plan_hang_records = scan_hang_records


def dag_wait_dataframe(counters: HangScanCounters, min_count: int = 3):
    import pandas as pd

    rows = []
    for (vertex, waiting_for), count in counters.dag_waits.most_common():
        if count < min_count:
            continue
        rows.append(
            {
                "vertex": vertex,
                "waiting_for": waiting_for,
                "count": count,
                "tail_count": counters.tail_dag_waits.get((vertex, waiting_for), 0),
            }
        )
    return pd.DataFrame(rows)


def blocked_resources_dataframe(counters: HangScanCounters, min_count: int = 3):
    import pandas as pd

    blocked = Counter()
    tail_blocked = Counter()
    for (vertex, waiting_for), count in counters.dag_waits.items():
        blocked[waiting_for] += count
        tail_blocked[waiting_for] += counters.tail_dag_waits.get((vertex, waiting_for), 0)

    rows = []
    for resource, count in blocked.most_common():
        if count < min_count:
            continue
        fields = parse_address(resource)
        tail = tail_blocked.get(resource, 0)
        rows.append(
            {
                "waiting_for": resource,
                "resource_type": fields["resource_type"],
                "resource_name": fields["resource_name"],
                "wait_messages": count,
                "tail_wait_messages": tail,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["tail_wait_messages", "wait_messages"], ascending=False)


def sdk_retry_dataframe(counters: HangScanCounters, min_count: int = 3):
    import pandas as pd

    rows = []
    for endpoint, count in counters.sdk_retries.most_common():
        if count < min_count:
            continue
        rows.append(
            {
                "method_url": endpoint,
                "retry_responses": count,
                "total_responses": counters.sdk_responses.get(endpoint, 0),
                "tail_retry_responses": counters.tail_sdk_retries.get(endpoint, 0),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["tail_retry_responses", "retry_responses"], ascending=False)


def sdk_429_wait_dataframe(counters: HangScanCounters, min_responses: int = 1):
    """429 responses and summed invocation_retry_after (seconds the SDK was told to wait)."""
    import pandas as pd

    rows = []
    for endpoint, response_count in counters.sdk_429.most_common():
        if response_count < min_responses:
            continue
        wait_seconds = counters.sdk_429_wait_seconds.get(endpoint, 0)
        rows.append(
            {
                "method_url": endpoint,
                "response_429": response_count,
                "wait_seconds": wait_seconds,
                "wait_minutes": round(wait_seconds / 60, 2),
                "avg_wait_seconds": round(wait_seconds / response_count, 1)
                if response_count
                else 0,
                "tail_response_429": counters.tail_sdk_429.get(endpoint, 0),
                "tail_wait_seconds": counters.tail_sdk_429_wait_seconds.get(endpoint, 0),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["wait_seconds", "response_429"], ascending=False)


def sdk_429_wait_by_resource_type_dataframe(counters: HangScanCounters, min_responses: int = 1):
    import pandas as pd

    rows = []
    for resource_type, response_count in counters.sdk_429_by_resource_type.most_common():
        if response_count < min_responses:
            continue
        wait_seconds = counters.sdk_429_wait_by_resource_type.get(resource_type, 0)
        rows.append(
            {
                "resource_type": resource_type,
                "response_429": response_count,
                "wait_seconds": wait_seconds,
                "wait_minutes": round(wait_seconds / 60, 2),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["wait_seconds", "response_429"], ascending=False)


def display_sdk_429_wait(
    counters: HangScanCounters,
    summary: dict,
    *,
    head: int = 20,
) -> None:
    """Notebook helper: print and chart summed invocation_retry_after on 429 responses."""
    import matplotlib.pyplot as plt

    try:
        from IPython.display import display
    except ImportError:
        display = print  # type: ignore[assignment]

    wait_seconds = summary.get("sdk_429_wait_seconds") or 0
    if not wait_seconds:
        print("No SDK 429 responses (or no invocation_retry_after on 429 lines).")
        return

    pct = summary.get("sdk_429_wait_pct_of_log")
    pct_text = f" ({pct:.1f}% of log span)" if pct is not None else ""
    print(
        f"429 wait time: {summary['sdk_429_wait_minutes']:.1f} min{pct_text} "
        f"across {summary['sdk_429_response_count']:,} rate-limit responses"
    )

    df_429_wait = sdk_429_wait_dataframe(counters)
    display(df_429_wait.head(head))

    df_429_wait_by_type = sdk_429_wait_by_resource_type_dataframe(counters)
    if not df_429_wait_by_type.empty:
        print("\n--- By Terraform resource_type ---")
        display(df_429_wait_by_type.head(15))

    plot_df = df_429_wait.head(15).sort_values("wait_seconds")
    if not plot_df.empty:
        plt.figure(figsize=(12, 6))
        plt.barh(plot_df["method_url"], plot_df["wait_minutes"], color="tab:orange")
        plt.xlabel("wait minutes (summed retry_after)")
        plt.title("SDK 429 rate-limit wait time by endpoint")
        plt.tight_layout()


def sdk_not_found_dataframe(counters: HangScanCounters, min_count: int = 2):
    import pandas as pd

    rows = []
    for endpoint, count in counters.sdk_404.most_common():
        if count < min_count:
            continue
        rows.append(
            {
                "method_url": endpoint,
                "not_found_responses": count,
                "total_responses": counters.sdk_responses.get(endpoint, 0),
                "tail_not_found_responses": counters.tail_sdk_404.get(endpoint, 0),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["tail_not_found_responses", "not_found_responses"], ascending=False)


def _per_minute(count: int, duration_minutes: float | None) -> float | None:
    if duration_minutes is None or duration_minutes <= 0:
        return None
    return count / duration_minutes


def sdk_call_rates_dataframe(
    counters: HangScanCounters,
    duration_minutes: float | None = None,
    min_events: int = 1,
):
    """SDK request/response rates by sanitized method+URL (API endpoint)."""
    import pandas as pd

    duration = duration_minutes if duration_minutes is not None else _duration_minutes(counters)
    endpoints = set(counters.sdk_requests) | set(counters.sdk_responses)
    rows = []
    for endpoint in endpoints:
        request_count = counters.sdk_requests.get(endpoint, 0)
        response_count = counters.sdk_responses.get(endpoint, 0)
        if request_count + response_count < min_events:
            continue
        rows.append(
            {
                "method_url": endpoint,
                "request_count": request_count,
                "response_count": response_count,
                "response_404": counters.sdk_404.get(endpoint, 0),
                "response_429": counters.sdk_429.get(endpoint, 0),
                "retry_responses": counters.sdk_retries.get(endpoint, 0),
                "requests_per_minute": _per_minute(request_count, duration),
                "responses_per_minute": _per_minute(response_count, duration),
                "response_404_per_minute": _per_minute(
                    counters.sdk_404.get(endpoint, 0), duration
                ),
                "response_429_per_minute": _per_minute(
                    counters.sdk_429.get(endpoint, 0), duration
                ),
                "wait_seconds_429": counters.sdk_429_wait_seconds.get(endpoint, 0),
                "wait_minutes_429": round(
                    counters.sdk_429_wait_seconds.get(endpoint, 0) / 60, 2
                ),
                "wait_seconds_429_per_log_minute": _per_minute(
                    counters.sdk_429_wait_seconds.get(endpoint, 0), duration
                ),
                "is_dnclist_export": "/dnclists/" in endpoint and "/export" in endpoint,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    sort_cols = [c for c in ("requests_per_minute", "request_count") if c in df.columns]
    return df.sort_values(sort_cols, ascending=False, na_position="last")


def sdk_call_rates_by_resource_type_dataframe(
    counters: HangScanCounters,
    duration_minutes: float | None = None,
    min_events: int = 1,
):
    """SDK request/response rates rolled up by Terraform resource_type from SDK DEBUG."""
    import pandas as pd

    duration = duration_minutes if duration_minutes is not None else _duration_minutes(counters)
    resource_types = set(counters.sdk_requests_by_resource_type) | set(
        counters.sdk_responses_by_resource_type
    )
    rows = []
    for resource_type in resource_types:
        request_count = counters.sdk_requests_by_resource_type.get(resource_type, 0)
        response_count = counters.sdk_responses_by_resource_type.get(resource_type, 0)
        if request_count + response_count < min_events:
            continue
        rows.append(
            {
                "resource_type": resource_type,
                "request_count": request_count,
                "response_count": response_count,
                "response_429": counters.sdk_429_by_resource_type.get(resource_type, 0),
                "wait_seconds_429": counters.sdk_429_wait_by_resource_type.get(resource_type, 0),
                "wait_minutes_429": round(
                    counters.sdk_429_wait_by_resource_type.get(resource_type, 0) / 60, 2
                ),
                "requests_per_minute": _per_minute(request_count, duration),
                "responses_per_minute": _per_minute(response_count, duration),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    sort_cols = [c for c in ("requests_per_minute", "request_count") if c in df.columns]
    return df.sort_values(sort_cols, ascending=False, na_position="last")


def sdk_timeline_dataframe(
    counters: HangScanCounters,
    bucket_minutes: float = 1.0,
    top_endpoints: int = 5,
    *,
    always_include_dnclist_export: bool = True,
):
    """SDK request counts per minute-from-log-start bucket (long format)."""
    import pandas as pd

    if not counters.sdk_timeline_requests:
        return pd.DataFrame()

    bucket_seconds = max(1, int(bucket_minutes * 60))
    endpoint_totals = Counter()
    for bucket_counts in counters.sdk_timeline_requests.values():
        endpoint_totals.update(bucket_counts)

    selected = {endpoint for endpoint, _ in endpoint_totals.most_common(top_endpoints)}
    if always_include_dnclist_export:
        for endpoint in endpoint_totals:
            if "/dnclists/" in endpoint and "/export" in endpoint:
                selected.add(endpoint)

    rows = []
    for bucket in sorted(counters.sdk_timeline_requests):
        minute = round((bucket * bucket_seconds) / 60, 2)
        for endpoint, request_count in counters.sdk_timeline_requests[bucket].items():
            if endpoint not in selected:
                continue
            rows.append(
                {
                    "minute_from_start": minute,
                    "bucket_index": bucket,
                    "method_url": endpoint,
                    "request_count": request_count,
                    "response_404": counters.sdk_timeline_404.get(bucket, Counter()).get(
                        endpoint, 0
                    ),
                    "is_dnclist_export": "/dnclists/" in endpoint and "/export" in endpoint,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["minute_from_start", "request_count"], ascending=[True, False])


def sdk_timeline_summary(counters: HangScanCounters, bucket_minutes: float = 1.0) -> dict:
    """Peak-minute stats for DNC list export polling (GET/POST)."""
    bucket_seconds = max(1, int(bucket_minutes * 60))

    def _peak(counter: Counter) -> dict | None:
        if not counter:
            return None
        bucket, count = counter.most_common(1)[0]
        return {
            "peak_requests": count,
            "minute_from_start": round((bucket * bucket_seconds) / 60, 2),
        }

    get_peak = _peak(counters.sdk_timeline_dnclist_export_get)
    post_peak = _peak(counters.sdk_timeline_dnclist_export_post)
    first_get = None
    if counters.sdk_timeline_dnclist_export_get:
        first_bucket = min(counters.sdk_timeline_dnclist_export_get)
        first_get = round((first_bucket * bucket_seconds) / 60, 2)

    return {
        "first_dnclist_export_get_minute": first_get,
        "dnclist_export_get_peak": get_peak,
        "dnclist_export_post_peak": post_peak,
    }


def display_sdk_call_rates(
    counters: HangScanCounters,
    summary: dict,
    *,
    table_head: int = 20,
):
    """Notebook helper: SDK endpoint rate tables (whole-log average per minute)."""
    duration = summary.get("duration_minutes")
    df_sdk_rates = sdk_call_rates_dataframe(counters, duration_minutes=duration)
    df_sdk_rates_by_type = sdk_call_rates_by_resource_type_dataframe(
        counters, duration_minutes=duration
    )

    print(f"Log span: {duration:.1f} min" if duration else "Log span: unknown")
    if not df_sdk_rates.empty:
        cols = [
            "method_url",
            "request_count",
            "requests_per_minute",
            "response_404_per_minute",
            "response_429_per_minute",
            "is_dnclist_export",
        ]
        display(df_sdk_rates[cols].head(table_head))
    else:
        print("No SDK DEBUG traffic found.")

    print("\n--- By Terraform resource_type ---")
    if not df_sdk_rates_by_type.empty:
        display(df_sdk_rates_by_type.head(15))
    else:
        print("(none)")

    return df_sdk_rates, df_sdk_rates_by_type


def display_sdk_timeline(
    counters: HangScanCounters,
    *,
    bucket_minutes: float = 1.0,
    top_endpoints: int = 5,
):
    """Notebook helper: line charts of SDK requests/minute over the log span."""
    import matplotlib.pyplot as plt

    import commonlib.gencharts as gencharts

    df_timeline = sdk_timeline_dataframe(
        counters,
        bucket_minutes=bucket_minutes,
        top_endpoints=top_endpoints,
    )
    timeline_summary = sdk_timeline_summary(counters, bucket_minutes=bucket_minutes)

    if timeline_summary.get("first_dnclist_export_get_minute") is not None:
        print(
            "First DNC list export GET activity: "
            f"minute {timeline_summary['first_dnclist_export_get_minute']:.1f} from log start"
        )
    get_peak = timeline_summary.get("dnclist_export_get_peak")
    if get_peak:
        print(
            "DNC list export GET peak: "
            f"{get_peak['peak_requests']} req/min at minute {get_peak['minute_from_start']:.1f}"
        )
    post_peak = timeline_summary.get("dnclist_export_post_peak")
    if post_peak:
        print(
            "DNC list export POST peak: "
            f"{post_peak['peak_requests']} req/min at minute {post_peak['minute_from_start']:.1f}"
        )

    if df_timeline.empty:
        print("No SDK DEBUG request timeline data.")
        return df_timeline, timeline_summary

    gencharts.plot_sdk_request_timeline(
        df_timeline,
        title="SDK requests per minute (from log start)",
    )
    plt.show()

    df_404 = df_timeline[df_timeline["response_404"] > 0]
    if not df_404.empty:
        gencharts.plot_sdk_request_timeline(
            df_404,
            value_column="response_404",
            title="SDK 404 responses per minute (from log start)",
        )
        plt.show()

    return df_timeline, timeline_summary


def display_sdk_pressure(
    counters: HangScanCounters,
    summary: dict,
    *,
    min_retry: int = 3,
    min_404: int = 2,
    bucket_minutes: float = 1.0,
    rate_table_head: int = 10,
) -> dict:
    """
    Hang notebooks: timeline first, compact whole-log rates, then retry/404/429
    detail only when counts are elevated.
    """
    import matplotlib.pyplot as plt

    print("When API traffic spiked (from log start):")
    df_timeline, timeline_summary = display_sdk_timeline(
        counters, bucket_minutes=bucket_minutes
    )

    duration = summary.get("duration_minutes")
    df_sdk_rates = sdk_call_rates_dataframe(counters, duration_minutes=duration)
    df_sdk_rates_by_type = sdk_call_rates_by_resource_type_dataframe(
        counters, duration_minutes=duration
    )

    print("\nTop endpoint rates (whole-log average — use timeline above for *when*):")
    if df_sdk_rates.empty:
        print("No SDK DEBUG traffic found.")
    else:
        cols = [
            "method_url",
            "request_count",
            "requests_per_minute",
            "response_404_per_minute",
            "is_dnclist_export",
        ]
        display(df_sdk_rates[cols].head(rate_table_head))

    df_retries = sdk_retry_dataframe(counters, min_count=min_retry)
    if not df_retries.empty:
        print(f"\nRetry storms (>={min_retry} responses with retry_after):")
        display(df_retries.head(15))
        plot_df = df_retries.head(15).sort_values("retry_responses")
        plt.figure(figsize=(12, 6))
        plt.barh(plot_df["method_url"], plot_df["retry_responses"], color="tab:red")
        plt.xlabel("responses with retry_after")
        plt.title("SDK retry storms")
        plt.tight_layout()
        plt.show()

    if summary.get("sdk_429_response_count", 0) > 0:
        print("\n429 rate-limit wait:")
        display_sdk_429_wait(counters, summary)

    df_404 = sdk_not_found_dataframe(counters, min_count=min_404)
    if not df_404.empty:
        print(f"\n404 not found (>={min_404} responses — common during export polling):")
        display(df_404.head(15))

    return {
        "df_timeline": df_timeline,
        "timeline_summary": timeline_summary,
        "df_sdk_rates": df_sdk_rates,
        "df_sdk_rates_by_type": df_sdk_rates_by_type,
        "df_retries": df_retries,
        "df_404": df_404,
    }


def display_provider_api_activity(
    counters: HangScanCounters,
    summary: dict,
    *,
    bucket_minutes: float = 1.0,
):
    """
    Performance notebooks: when API traffic spiked + key endpoint averages + 429
    if present. Retry/404 detail stays in the exported report only.
    """
    import pandas as pd

    print("Provider API timing from SDK DEBUG hooks in the log:")
    df_timeline, timeline_summary = display_sdk_timeline(
        counters, bucket_minutes=bucket_minutes
    )

    duration = summary.get("duration_minutes")
    df_sdk_rates = sdk_call_rates_dataframe(counters, duration_minutes=duration)
    if not df_sdk_rates.empty:
        export_rows = df_sdk_rates[df_sdk_rates["is_dnclist_export"]]
        other_rows = df_sdk_rates[~df_sdk_rates["is_dnclist_export"]].head(3)
        key_rows = pd.concat([export_rows, other_rows]).drop_duplicates(
            subset=["method_url"]
        )
        print("\nKey endpoints (whole-log average):")
        display(
            key_rows[
                [
                    "method_url",
                    "request_count",
                    "requests_per_minute",
                    "response_404_per_minute",
                    "is_dnclist_export",
                ]
            ]
        )

    if summary.get("sdk_429_response_count", 0) > 0:
        print("\n429 rate-limit wait (long completed runs):")
        display_sdk_429_wait(counters, summary)

    return df_timeline, timeline_summary, df_sdk_rates


def vertex_churn_dataframe(counters: HangScanCounters, min_count: int = 5):
    import pandas as pd

    rows = []
    for vertex, count in counters.vertex_starts.most_common():
        if count < min_count:
            continue
        fields = parse_address(vertex)
        rows.append(
            {
                "vertex": vertex,
                "resource_type": fields["resource_type"],
                "visit_starts": count,
            }
        )
    return pd.DataFrame(rows)


def refresh_imbalance_dataframe(counters: HangScanCounters, min_gap: int = 1):
    import pandas as pd

    rows = []
    addrs = set(counters.refresh_starts) | set(counters.refresh_completes)
    for addr in addrs:
        starts = counters.refresh_starts.get(addr, 0)
        completes = counters.refresh_completes.get(addr, 0)
        gap = starts - completes
        if abs(gap) < min_gap:
            continue
        fields = parse_address(addr)
        rows.append(
            {
                "resource": addr,
                "resource_type": fields["resource_type"],
                "starts": starts,
                "completes": completes,
                "start_minus_complete": gap,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("start_minus_complete", ascending=False)


def export_imbalance_dataframe(counters: HangScanCounters, min_gap: int = 1):
    import pandas as pd

    rows = []
    keys = set(counters.export_starts) | set(counters.export_completes)
    for key in keys:
        starts = counters.export_starts.get(key, 0)
        completes = counters.export_completes.get(key, 0)
        gap = starts - completes
        if abs(gap) < min_gap:
            continue
        resource_type, _, label = key.partition(".")
        rows.append(
            {
                "resource": key,
                "resource_type": resource_type,
                "resource_label": label,
                "export_starts": starts,
                "export_completes": completes,
                "start_minus_complete": gap,
                "tail_export_starts": counters.tail_export_starts.get(key, 0),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["tail_export_starts", "start_minus_complete"], ascending=False)


def apply_imbalance_dataframe(counters: HangScanCounters, min_gap: int = 1):
    import pandas as pd

    rows = []
    keys = set(counters.apply_starts) | set(counters.apply_completes)
    for key in keys:
        starts = counters.apply_starts.get(key, 0)
        completes = counters.apply_completes.get(key, 0)
        gap = starts - completes
        if abs(gap) < min_gap:
            continue
        rows.append(
            {
                "resource": key,
                "apply_starts": starts,
                "apply_completes": completes,
                "start_minus_complete": gap,
                "tail_apply_starts": counters.tail_apply_starts.get(key, 0),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["tail_apply_starts", "start_minus_complete"], ascending=False)


def apply_rpc_open_dataframe(counters: HangScanCounters, min_count: int = 1):
    import pandas as pd

    rows = [
        {"resource_type": resource_type, "open_rpc_requests": count}
        for resource_type, count in counters.apply_rpc_open.most_common()
        if count >= min_count
    ]
    return pd.DataFrame(rows)


def tail_messages_dataframe(counters: HangScanCounters, top_n: int = 20):
    import pandas as pd

    rows = [
        {"message_prefix": msg, "count": count}
        for msg, count in counters.tail_messages.most_common(top_n)
    ]
    return pd.DataFrame(rows)


def tail_export_messages_dataframe(counters: HangScanCounters, top_n: int = 15):
    import pandas as pd

    rows = [
        {"message_prefix": msg, "count": count}
        for msg, count in counters.tail_export_messages.most_common(top_n)
    ]
    return pd.DataFrame(rows)


LAYER_TERRAFORM = "terraform"
LAYER_PROVIDER = "provider"
LAYER_SDK = "sdk"

ISSUE_LAYERS = (LAYER_TERRAFORM, LAYER_PROVIDER, LAYER_SDK)

VERDICT_LAYER: dict[str, str] = {
    "graph_wait_loop": LAYER_TERRAFORM,
    "vertex_churn": LAYER_TERRAFORM,
    "stuck_refresh": LAYER_PROVIDER,
    "stuck_export": LAYER_PROVIDER,
    "stuck_apply": LAYER_PROVIDER,
    "stuck_apply_rpc": LAYER_PROVIDER,
    "sdk_retry_storm": LAYER_SDK,
    "sdk_not_found": LAYER_PROVIDER,
}

REPORT_SECTIONS_BY_CATEGORY: dict[str, list[str]] = {
    "graph_wait_loop": ["graph_wait_loops", "summary.dag_wait_pairs"],
    "vertex_churn": ["vertex_churn"],
    "stuck_refresh": ["refresh_imbalance", "open_refreshes", "completed_refreshes"],
    "stuck_export": ["export_imbalance", "open_exports", "completed_exports"],
    "stuck_apply": ["apply_imbalance", "open_applies", "completed_applies"],
    "stuck_apply_rpc": ["open_apply_rpcs"],
    "sdk_retry_storm": ["sdk_retries", "sdk_call_rates"],
    "sdk_not_found": ["sdk_404", "sdk_call_rates"],
    "dnclist_export_polling": [
        "sdk_timeline_summary",
        "sdk_timeline",
        "sdk_404",
        "sdk_call_rates",
    ],
    "sdk_rate_limit_wait": [
        "sdk_429_wait",
        "sdk_429_wait_by_resource_type",
        "summary.sdk_429_wait_seconds",
    ],
    "high_api_volume": ["sdk_call_rates_by_resource_type", "sdk_call_rates"],
}

TICKET_ROUTING: dict[str, str] = {
    LAYER_TERRAFORM: (
        "Terraform core / graph — dependency expansion, refresh scheduling, "
        "waiting on provider RPCs"
    ),
    LAYER_PROVIDER: (
        "genesyscloud provider — resource Read/Create/Update/Export logic, "
        "schema (Computed/Optional), when APIs are invoked"
    ),
    LAYER_SDK: (
        "Genesys Cloud SDK / HTTP client — retries, rate-limit backoff, "
        "connection behavior (observed via SDK DEBUG hooks)"
    ),
}


def verdict_layer(category: str) -> str:
    return VERDICT_LAYER.get(category, LAYER_PROVIDER)


@dataclass
class HangVerdict:
    category: str
    workflow: str
    summary: str
    detail: str
    score: int
    layer: str = ""


def build_hang_verdicts(counters: HangScanCounters, min_count: int = 3) -> list[HangVerdict]:
    verdicts: list[HangVerdict] = []

    if counters.dag_waits:
        top_blocked = blocked_resources_dataframe(counters, min_count=min_count)
        if not top_blocked.empty:
            row = top_blocked.iloc[0]
            workflow = "apply" if "apply" in counters.workflows and "plan" not in counters.workflows else "plan"
            verdicts.append(
                HangVerdict(
                    category="graph_wait_loop",
                    workflow=workflow,
                    summary="Terraform graph is waiting on dependencies (spinning)",
                    detail=(
                        f"Most repeated wait: {row['waiting_for']} "
                        f"({row['wait_messages']:,} dag/walk wait lines, "
                        f"{row['tail_wait_messages']:,} in tail window)"
                    ),
                    score=int(row["tail_wait_messages"] * 10 + row["wait_messages"]),
                    layer=verdict_layer("graph_wait_loop"),
                )
            )

    stuck_export = export_imbalance_dataframe(counters, min_gap=1)
    if not stuck_export.empty:
        row = stuck_export.iloc[0]
        if row["start_minus_complete"] > 0:
            verdicts.append(
                HangVerdict(
                    category="stuck_export",
                    workflow="export",
                    summary="Export started but not completing for some resources",
                    detail=(
                        f"{row['resource']}: {row['export_starts']} starts vs "
                        f"{row['export_completes']} completes "
                        f"({row['tail_export_starts']} starts in tail window)"
                    ),
                    score=int(row["tail_export_starts"] * 10 + row["start_minus_complete"] * 5),
                    layer=verdict_layer("stuck_export"),
                )
            )

    stuck_apply = apply_imbalance_dataframe(counters, min_gap=1)
    if not stuck_apply.empty:
        row = stuck_apply.iloc[0]
        if row["start_minus_complete"] > 0:
            verdicts.append(
                HangVerdict(
                    category="stuck_apply",
                    workflow="apply",
                    summary="Apply started but not completing for some resources",
                    detail=(
                        f"{row['resource']}: {row['apply_starts']} starts vs "
                        f"{row['apply_completes']} completes "
                        f"({row['tail_apply_starts']} starts in tail window)"
                    ),
                    score=int(row["tail_apply_starts"] * 10 + row["start_minus_complete"] * 5),
                    layer=verdict_layer("stuck_apply"),
                )
            )

    if counters.apply_rpc_open:
        row = apply_rpc_open_dataframe(counters).iloc[0]
        verdicts.append(
            HangVerdict(
                category="stuck_apply_rpc",
                workflow="apply",
                summary="ApplyResourceChange RPC requests never received a downstream response",
                detail=f"{row['resource_type']}: {row['open_rpc_requests']} open RPC requests at end of log",
                score=int(row["open_rpc_requests"]) * 20,
                layer=verdict_layer("stuck_apply_rpc"),
            )
        )

    if counters.sdk_retries:
        top_retry = sdk_retry_dataframe(counters, min_count=min_count)
        if not top_retry.empty:
            row = top_retry.iloc[0]
            workflow = next(iter(counters.workflows), "any")
            verdicts.append(
                HangVerdict(
                    category="sdk_retry_storm",
                    workflow=workflow,
                    summary="SDK is retrying API calls (rate limit or backoff)",
                    detail=(
                        f"{row['method_url']}: {row['retry_responses']:,} responses with "
                        f"invocation_retry_after ({row['tail_retry_responses']:,} in tail window)"
                    ),
                    score=int(row["tail_retry_responses"] * 10 + row["retry_responses"]),
                    layer=verdict_layer("sdk_retry_storm"),
                )
            )

    if counters.sdk_404:
        top_404 = sdk_not_found_dataframe(counters, min_count=2)
        if not top_404.empty:
            row = top_404.iloc[0]
            workflow = next(iter(counters.workflows), "any")
            verdicts.append(
                HangVerdict(
                    category="sdk_not_found",
                    workflow=workflow,
                    summary="Provider read path is polling with repeated 404s (often export-not-ready)",
                    detail=(
                        f"{row['method_url']}: {row['not_found_responses']:,} 404 responses "
                        f"({row['tail_not_found_responses']:,} in tail window)"
                    ),
                    score=int(row["tail_not_found_responses"] * 10 + row["not_found_responses"]) * 5,
                    layer=verdict_layer("sdk_not_found"),
                )
            )

    churn = vertex_churn_dataframe(counters, min_count=10)
    if not churn.empty:
        row = churn.iloc[0]
        verdicts.append(
            HangVerdict(
                category="vertex_churn",
                workflow="plan",
                summary="Graph vertices are being revisited repeatedly",
                detail=f"{row['vertex']}: {row['visit_starts']:,} starting visit lines",
                score=int(row["visit_starts"]),
                layer=verdict_layer("vertex_churn"),
            )
        )

    stuck_refresh = refresh_imbalance_dataframe(counters, min_gap=2)
    if not stuck_refresh.empty:
        row = stuck_refresh.iloc[0]
        if row["start_minus_complete"] > 0:
            verdicts.append(
                HangVerdict(
                    category="stuck_refresh",
                    workflow="plan",
                    summary="Provider refresh has not returned (Terraform still waiting)",
                    detail=(
                        f"{row['resource']}: {row['starts']} starts vs "
                        f"{row['completes']} completes"
                    ),
                    score=int(row["start_minus_complete"]) * 10,
                    layer=verdict_layer("stuck_refresh"),
                )
            )

    verdicts.sort(key=lambda v: v.score, reverse=True)
    return verdicts


WORKFLOW_VERDICT_CATEGORIES = {
    "export": {"stuck_export", "sdk_retry_storm", "sdk_not_found"},
    "plan": {"graph_wait_loop", "stuck_refresh", "vertex_churn", "sdk_retry_storm", "sdk_not_found"},
    "apply": {
        "stuck_apply",
        "stuck_apply_rpc",
        "graph_wait_loop",
        "sdk_retry_storm",
        "sdk_not_found",
    },
}


def filter_verdicts_for_workflow(
    verdicts: list[HangVerdict],
    workflow: str,
) -> list[HangVerdict]:
    allowed = WORKFLOW_VERDICT_CATEGORIES[workflow]
    filtered = [v for v in verdicts if v.category in allowed]
    filtered.sort(key=lambda v: v.score, reverse=True)
    return filtered


def hang_summary(
    counters: HangScanCounters,
    tail_minutes: float,
    min_count: int = 3,
    classification: TfLogClassification | None = None,
) -> dict:
    verdicts = build_hang_verdicts(counters, min_count=min_count)
    primary = verdicts[0] if verdicts else None

    duration_minutes = None
    if counters.first_timestamp and counters.last_timestamp:
        duration_minutes = (
            counters.last_timestamp - counters.first_timestamp
        ).total_seconds() / 60

    wait_total_seconds = sdk_429_wait_total_seconds(counters)
    wait_minutes = wait_total_seconds / 60.0
    wait_pct = None
    if duration_minutes and duration_minutes > 0:
        wait_pct = round((wait_minutes / duration_minutes) * 100, 1)

    workflows = sorted(counters.workflows)
    if classification and not workflows:
        workflows = classification.categories

    return {
        "workflows": workflows,
        "parsed_lines": counters.parsed_lines,
        "first_timestamp": counters.first_timestamp.isoformat() if counters.first_timestamp else None,
        "last_timestamp": counters.last_timestamp.isoformat() if counters.last_timestamp else None,
        "duration_minutes": duration_minutes,
        "tail_minutes": tail_minutes,
        "dag_wait_pairs": len(counters.dag_waits),
        "export_resources_started": len(counters.export_starts),
        "export_resources_completed": len(counters.export_completes),
        "apply_resources_started": len(counters.apply_starts),
        "apply_resources_completed": len(counters.apply_completes),
        "open_apply_rpc_types": len(counters.apply_rpc_open),
        "sdk_request_total": sum(counters.sdk_requests.values()),
        "sdk_response_total": sum(counters.sdk_responses.values()),
        "sdk_retry_endpoints": len(counters.sdk_retries),
        "sdk_404_endpoints": len(counters.sdk_404),
        "sdk_429_endpoints": len(counters.sdk_429),
        "sdk_429_response_count": sum(counters.sdk_429.values()),
        "sdk_429_wait_seconds": wait_total_seconds,
        "sdk_429_wait_minutes": round(wait_minutes, 2),
        "sdk_429_wait_pct_of_log": wait_pct,
        "sdk_status_codes": dict(counters.sdk_status),
        "primary_category": primary.category if primary else None,
        "primary_workflow": primary.workflow if primary else None,
        "primary_summary": primary.summary if primary else "No strong hang pattern detected",
        "primary_detail": primary.detail if primary else None,
        "verdicts": verdicts,
    }


def hang_summary_for_workflow(
    counters: HangScanCounters,
    tail_minutes: float,
    workflow: str,
    min_count: int = 3,
    classification: TfLogClassification | None = None,
) -> dict:
    summary = hang_summary(
        counters,
        tail_minutes=tail_minutes,
        min_count=min_count,
        classification=classification,
    )
    verdicts = filter_verdicts_for_workflow(summary["verdicts"], workflow)
    primary = verdicts[0] if verdicts else None
    summary["workflow"] = workflow
    summary["verdicts"] = verdicts
    summary["primary_category"] = primary.category if primary else None
    summary["primary_workflow"] = workflow
    summary["primary_summary"] = (
        primary.summary if primary else "No strong hang pattern detected"
    )
    summary["primary_detail"] = primary.detail if primary else None
    attribution = build_issue_attribution(counters, summary, workflow)
    summary["primary_layer"] = attribution.get("primary_layer")
    return summary


def _attribution_signal(
    *,
    category: str,
    layer: str,
    summary: str,
    detail: str,
    score: int,
) -> dict:
    return {
        "category": category,
        "layer": layer,
        "summary": summary,
        "detail": detail,
        "score": score,
        "report_sections": REPORT_SECTIONS_BY_CATEGORY.get(category, []),
    }


def _verdict_to_signal(verdict: HangVerdict | dict) -> dict:
    if isinstance(verdict, HangVerdict):
        category = verdict.category
        layer = verdict.layer or verdict_layer(category)
        return _attribution_signal(
            category=category,
            layer=layer,
            summary=verdict.summary,
            detail=verdict.detail,
            score=verdict.score,
        )
    category = verdict["category"]
    return _attribution_signal(
        category=category,
        layer=verdict.get("layer") or verdict_layer(category),
        summary=verdict["summary"],
        detail=verdict["detail"],
        score=int(verdict["score"]),
    )


def _provider_extra_signals(counters: HangScanCounters, summary: dict) -> list[dict]:
    signals: list[dict] = []
    timeline_summary = sdk_timeline_summary(counters)
    first_get = timeline_summary.get("first_dnclist_export_get_minute")
    if first_get is not None:
        get_peak = timeline_summary.get("dnclist_export_get_peak") or {}
        peak_requests = int(get_peak.get("peak_requests") or 0)
        signals.append(
            _attribution_signal(
                category="dnclist_export_polling",
                layer=LAYER_PROVIDER,
                summary="DNC list export-on-read polling detected",
                detail=(
                    f"First export GET at minute {first_get:.1f} from log start; "
                    f"peak {peak_requests} GET req/min"
                ),
                score=max(50, int(peak_requests * 15)),
            )
        )

    duration = summary.get("duration_minutes")
    df_by_type = sdk_call_rates_by_resource_type_dataframe(
        counters, duration_minutes=duration
    )
    if not df_by_type.empty:
        row = df_by_type.iloc[0]
        rpm = row.get("requests_per_minute")
        if rpm is not None and rpm >= 15:
            signals.append(
                _attribution_signal(
                    category="high_api_volume",
                    layer=LAYER_PROVIDER,
                    summary="High sustained API volume from a provider resource type",
                    detail=(
                        f"{row['resource_type']}: {row['request_count']:,} requests "
                        f"({rpm:.1f}/min over log span)"
                    ),
                    score=int(rpm * 10),
                )
            )
    return signals


def _sdk_extra_signals(counters: HangScanCounters, summary: dict) -> list[dict]:
    signals: list[dict] = []
    wait_seconds = summary.get("sdk_429_wait_seconds") or 0
    if wait_seconds >= 30:
        wait_minutes = summary.get("sdk_429_wait_minutes") or round(wait_seconds / 60, 2)
        pct = summary.get("sdk_429_wait_pct_of_log")
        pct_text = f" ({pct:.1f}% of log span)" if pct is not None else ""
        signals.append(
            _attribution_signal(
                category="sdk_rate_limit_wait",
                layer=LAYER_SDK,
                summary="SDK spent significant time waiting on rate-limit backoff",
                detail=(
                    f"{summary.get('sdk_429_response_count', 0):,} HTTP 429 responses; "
                    f"{wait_minutes:.1f} min summed invocation_retry_after{pct_text}"
                ),
                score=int(wait_seconds / 5),
            )
        )
    return signals


def build_issue_attribution(
    counters: HangScanCounters,
    summary: dict,
    workflow: str,
) -> dict:
    """Roll verdicts and SDK metrics into terraform / provider / sdk ownership."""
    layers: dict[str, list[dict]] = {layer: [] for layer in ISSUE_LAYERS}
    seen_categories: set[str] = set()

    for verdict in summary.get("verdicts") or []:
        signal = _verdict_to_signal(verdict)
        layers[signal["layer"]].append(signal)
        seen_categories.add(signal["category"])

    for signal in _provider_extra_signals(counters, summary):
        if signal["category"] not in seen_categories:
            layers[LAYER_PROVIDER].append(signal)
            seen_categories.add(signal["category"])

    for signal in _sdk_extra_signals(counters, summary):
        if signal["category"] not in seen_categories:
            layers[LAYER_SDK].append(signal)
            seen_categories.add(signal["category"])

    layer_scores = {
        layer: sum(item["score"] for item in signals) for layer, signals in layers.items()
    }
    active_layers = [layer for layer, score in layer_scores.items() if score > 0]
    primary_layer = max(active_layers, key=lambda layer: layer_scores[layer]) if active_layers else None

    layer_payload = {}
    for layer in ISSUE_LAYERS:
        signals = sorted(layers[layer], key=lambda item: item["score"], reverse=True)
        layer_payload[layer] = {
            "score": layer_scores[layer],
            "signal_count": len(signals),
            "signals": signals,
            "ticket_routing": TICKET_ROUTING[layer],
        }

    guidance = None
    if primary_layer:
        guidance = (
            f"Start with {primary_layer} ({TICKET_ROUTING[primary_layer]}). "
            "Correlate with other layers if multiple signals score highly."
        )

    return {
        "workflow": workflow,
        "primary_layer": primary_layer,
        "layer_scores": layer_scores,
        "layers": layer_payload,
        "guidance": guidance,
    }


def display_issue_attribution(attribution: dict) -> None:
    """Notebook helper: print terraform / provider / sdk routing from build_issue_attribution."""
    primary = attribution.get("primary_layer")
    if not primary:
        print("No strong layer attribution — check SDK sections and workflow tables.")
        return

    print(f"Issue owner (primary layer): {primary}")
    if attribution.get("guidance"):
        print(attribution["guidance"])
    print()

    for layer in ISSUE_LAYERS:
        block = attribution["layers"][layer]
        if block["signal_count"] == 0:
            continue
        print(f"--- {layer} (score {block['score']}) ---")
        print(block["ticket_routing"])
        for signal in block["signals"][:3]:
            print(f"  • {signal['summary']}")
            if signal.get("detail"):
                print(f"    {signal['detail']}")
        print()


def load_hang_scan(
    file_path: str,
    tail_minutes: float = 5.0,
) -> tuple[TfLogClassification, list[dict], HangScanCounters]:
    classification = classify_log(file_path)
    records = read_json_from_file(file_path)
    counters = scan_hang_records(
        records,
        tail_minutes=tail_minutes,
        classification=classification,
    )
    return classification, records, counters
