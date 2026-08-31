"""
Detect likely causes of hanging or very slow Terraform / Genesys Cloud activity
from TF_LOG=json trace output.

Supports plan, export, and apply workflows with shared SDK/tail detection plus
workflow-specific stall patterns.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
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

logging.basicConfig(filename="parse_hang_errors.log", level=logging.ERROR)

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
    records = []
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logging.error("Failed to parse line at %s: %s", exc.lineno, exc)
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

    sdk_responses: Counter = field(default_factory=Counter)
    sdk_retries: Counter = field(default_factory=Counter)
    sdk_404: Counter = field(default_factory=Counter)
    sdk_status: Counter = field(default_factory=Counter)

    tail_dag_waits: Counter = field(default_factory=Counter)
    tail_sdk_retries: Counter = field(default_factory=Counter)
    tail_sdk_404: Counter = field(default_factory=Counter)
    tail_messages: Counter = field(default_factory=Counter)
    tail_export_starts: Counter = field(default_factory=Counter)
    tail_export_messages: Counter = field(default_factory=Counter)
    tail_apply_starts: Counter = field(default_factory=Counter)


def _record_sdk(counters: HangScanCounters, message: str) -> None:
    sdk = _sdk_payload(message)
    if not sdk or sdk.get("debug_type") != "SDK DEBUG RESPONSE":
        return

    method = sdk.get("invocation_method") or "?"
    url = sdk.get("invocation_url") or ""
    endpoint = _method_url(method, url)
    status = sdk.get("invocation_status_code")
    counters.sdk_responses[endpoint] += 1
    if status is not None:
        counters.sdk_status[status] += 1
    retry_after = sdk.get("invocation_retry_after")
    if retry_after is not None and int(retry_after) > 0:
        counters.sdk_retries[endpoint] += 1
    if status == 404:
        counters.sdk_404[endpoint] += 1


def _record_tail_sdk(counters: HangScanCounters, message: str) -> None:
    sdk = _sdk_payload(message)
    if not sdk or sdk.get("debug_type") != "SDK DEBUG RESPONSE":
        return

    method = sdk.get("invocation_method") or "?"
    url = sdk.get("invocation_url") or ""
    endpoint = _method_url(method, url)
    status = sdk.get("invocation_status_code")
    retry_after = sdk.get("invocation_retry_after")
    if retry_after is not None and int(retry_after) > 0:
        counters.tail_sdk_retries[endpoint] += 1
    if status == 404:
        counters.tail_sdk_404[endpoint] += 1


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

        _record_sdk(counters, message)

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

            _record_tail_sdk(counters, message)

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


@dataclass
class HangVerdict:
    category: str
    workflow: str
    summary: str
    detail: str
    score: int


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
                    summary="SDK is hitting 404 Not Found (missing Genesys Cloud object)",
                    detail=(
                        f"{row['method_url']}: {row['not_found_responses']:,} 404 responses "
                        f"({row['tail_not_found_responses']:,} in tail window)"
                    ),
                    score=int(row["tail_not_found_responses"] * 10 + row["not_found_responses"]) * 5,
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
                    summary="Refresh started but not completing for some resources",
                    detail=(
                        f"{row['resource']}: {row['starts']} starts vs "
                        f"{row['completes']} completes"
                    ),
                    score=int(row["start_minus_complete"]) * 10,
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
        "sdk_retry_endpoints": len(counters.sdk_retries),
        "sdk_404_endpoints": len(counters.sdk_404),
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
    return summary


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
