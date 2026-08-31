import re

REFRESH_START_RE = re.compile(
    r"NodeAbstractResourceInstance\.refresh for (?P<addr>.+)$"
)
REFRESH_COMPLETE_RE = re.compile(
    r"NodeAbstractResourceInstance\.writeResourceInstanceState to refreshState for (?P<addr>.+)$"
)
REFRESH_LINE_RE = re.compile(r"^refresh: (?P<addr>.+)$")
VERTEX_START_RE = re.compile(r'^vertex "(?P<addr>[^"]+)": starting visit')
VERTEX_COMPLETE_RE = re.compile(r'^vertex "(?P<addr>[^"]+)": visit complete')
INVALID_PLAN_RE = re.compile(
    r'produced an invalid plan for (?P<addr>[^,]+),'
)


def parse_address(addr: str) -> dict:
    """Parse a Terraform resource address into notebook-friendly fields."""
    addr = addr.strip()
    parts = addr.split(".")

    if len(parts) >= 2 and parts[0] == "module":
        module = ".".join(parts[:2])
        resource_type = parts[2] if len(parts) > 3 else parts[-2]
        resource_name = parts[-1]
    elif len(parts) >= 3 and parts[0] in {"data", "resource"}:
        module = "None"
        resource_type = parts[1]
        resource_name = parts[2]
    elif len(parts) >= 2:
        module = "None"
        resource_type = parts[-2]
        resource_name = parts[-1]
    else:
        module = "None"
        resource_type = addr
        resource_name = "None"

    return {
        "resource_id": addr,
        "module": module,
        "resource": addr,
        "resource_type": resource_type,
        "resource_name": resource_name,
    }


def is_graph_resource(addr: str) -> bool:
    if addr in {"root"}:
        return False
    if addr.startswith("var."):
        return False
    if addr.startswith("provider"):
        return False
    if addr.startswith("meta."):
        return False
    if addr.endswith("(expand)"):
        return False
    if addr.endswith("(close)"):
        return False
    return True


def _base_record(record: dict, record_type: str, fields: dict, action: str = "None") -> dict:
    return {
        "resource_id": fields["resource_id"],
        "timestamp": record["@timestamp"],
        "type": record_type,
        "module": fields["module"],
        "resource": fields["resource"],
        "resource_type": fields["resource_type"],
        "resource_name": fields["resource_name"],
        "action": action,
    }


def normalize_plan_trace_records(records: list[dict]) -> list[dict]:
    """
    Extract plan activity from TF_LOG=json trace records.

    Emits refresh_start, refresh_complete, and planned_change events using
    Terraform graph and provider RPC log lines.
    """
    normalized_records = []
    seen_planned_change = set()

    for record in records:
        message = record.get("@message") or ""
        timestamp = record.get("@timestamp")
        if not timestamp:
            continue

        match = REFRESH_START_RE.search(message)
        if match:
            fields = parse_address(match.group("addr"))
            normalized_records.append(_base_record(record, "refresh_start", fields))
            continue

        match = REFRESH_COMPLETE_RE.search(message)
        if match:
            fields = parse_address(match.group("addr"))
            normalized_records.append(_base_record(record, "refresh_complete", fields))
            continue

        match = REFRESH_LINE_RE.match(message)
        if match:
            fields = parse_address(match.group("addr"))
            normalized_records.append(_base_record(record, "refresh_start", fields))
            continue

        match = INVALID_PLAN_RE.search(message)
        if match:
            fields = parse_address(match.group("addr"))
            if fields["resource_id"] not in seen_planned_change:
                seen_planned_change.add(fields["resource_id"])
                normalized_records.append(_base_record(record, "planned_change", fields))
            continue

        if (
            record.get("tf_rpc") == "PlanResourceChange"
            and message == "Received downstream response"
        ):
            resource_type = record.get("tf_resource_type") or "unknown"
            req_id = record.get("tf_req_id") or timestamp
            resource_id = f"{resource_type}:{req_id}"
            if resource_id in seen_planned_change:
                continue
            seen_planned_change.add(resource_id)
            fields = {
                "resource_id": resource_id,
                "module": "None",
                "resource": resource_id,
                "resource_type": resource_type,
                "resource_name": req_id,
            }
            normalized_records.append(_base_record(record, "planned_change", fields))

    return normalized_records


def normalize_apply_trace_records(records: list[dict]) -> list[dict]:
    """
    Extract apply activity from TF_LOG=json trace records.

    Prefer ApplyResourceChange RPC pairs when present; otherwise fall back to
    graph vertex visit start/complete lines.
    """
    rpc_records = _normalize_apply_rpc_records(records)
    if rpc_records:
        return rpc_records
    return _normalize_apply_vertex_records(records)


def _normalize_apply_rpc_records(records: list[dict]) -> list[dict]:
    normalized_records = []
    pending_requests: dict[str, dict] = {}

    for record in records:
        if record.get("tf_rpc") != "ApplyResourceChange":
            continue

        message = record.get("@message") or ""
        req_id = record.get("tf_req_id")
        if not req_id:
            continue

        resource_type = record.get("tf_resource_type") or "unknown"
        resource_id = f"{resource_type}:{req_id}"
        fields = {
            "resource_id": resource_id,
            "module": "None",
            "resource": resource_id,
            "resource_type": resource_type,
            "resource_name": req_id,
        }

        if message == "Received request":
            pending_requests[req_id] = record
            normalized_records.append(_base_record(record, "apply_start", fields))
        elif message == "Received downstream response" and req_id in pending_requests:
            pending_requests.pop(req_id)
            entry = _base_record(record, "apply_complete", fields)
            duration_ms = record.get("tf_req_duration_ms")
            entry["elapsed_seconds"] = (
                duration_ms / 1000 if duration_ms is not None else None
            )
            normalized_records.append(entry)

    return normalized_records


def _normalize_apply_vertex_records(records: list[dict]) -> list[dict]:
    normalized_records = []
    vertex_starts: dict[str, list[str]] = {}

    for record in records:
        message = record.get("@message") or ""
        timestamp = record.get("@timestamp")
        if not timestamp:
            continue

        match = VERTEX_START_RE.search(message)
        if match:
            addr = match.group("addr")
            if is_graph_resource(addr):
                vertex_starts.setdefault(addr, []).append(timestamp)
                fields = parse_address(addr)
                normalized_records.append(_base_record(record, "apply_start", fields))
            continue

        match = VERTEX_COMPLETE_RE.search(message)
        if match:
            addr = match.group("addr")
            if is_graph_resource(addr):
                fields = parse_address(addr)
                elapsed_seconds = None
                starts = vertex_starts.get(addr)
                if starts:
                    start_ts = starts.pop(0)
                    try:
                        from datetime import datetime

                        start_dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                        end_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        elapsed_seconds = (end_dt - start_dt).total_seconds()
                    except ValueError:
                        elapsed_seconds = None
                entry = _base_record(record, "apply_complete", fields)
                entry["elapsed_seconds"] = elapsed_seconds
                normalized_records.append(entry)

    return normalized_records


def normalize_ui_plan_records(records: list[dict]) -> list[dict]:
    normalized_records = []

    for record in records:
        if record.get("@module") != "terraform.ui":
            continue

        hook = record.get("hook")
        change = record.get("change")
        if hook is None and change is None:
            continue

        resource_id = "None"
        module = resource = resource_name = resource_type = action = "None"

        if hook is not None:
            resource_info = hook.get("resource") or {}
            module = resource_info.get("module", "None")
            resource = resource_info.get("resource", "None")
            resource_name = resource_info.get("resource_name", "None")
            resource_type = resource_info.get("resource_type", "None")
            resource_id = hook.get("id_value") or resource_info.get("addr", "None")
            action = hook.get("action", "None")

        if change is not None:
            resource_info = change.get("resource") or {}
            module = resource_info.get("module", "None")
            resource = resource_info.get("resource", "None")
            resource_name = resource_info.get("resource_name", "None")
            resource_type = resource_info.get("resource_type", "None")
            resource_id = resource_info.get("addr", resource_id)
            action = change.get("action", action)

        normalized_records.append(
            {
                "resource_id": resource_id,
                "timestamp": record["@timestamp"],
                "type": record["type"],
                "module": module,
                "resource": resource,
                "resource_type": resource_type,
                "resource_name": resource_name,
                "action": action,
            }
        )

    return normalized_records


def normalize_ui_apply_records(records: list[dict]) -> list[dict]:
    apply_types = {
        "apply_start",
        "apply_complete",
        "apply_progress",
        "apply_errored",
        "provision_start",
        "provision_complete",
        "provision_progress",
        "provision_errored",
    }
    normalized_records = []

    for record in records:
        if record.get("@module") != "terraform.ui":
            continue

        record_type = record.get("type")
        if record_type not in apply_types:
            continue

        hook = record.get("hook")
        change = record.get("change")
        if hook is None and change is None:
            continue

        fields = parse_address("None")
        action = "None"
        elapsed_seconds = None

        if hook is not None:
            resource_info = hook.get("resource") or {}
            addr = hook.get("id_value") or resource_info.get("addr") or resource_info.get("resource")
            if addr:
                fields = parse_address(addr)
            else:
                fields = {
                    "resource_id": resource_info.get("addr", "None"),
                    "module": resource_info.get("module", "None"),
                    "resource": resource_info.get("resource", "None"),
                    "resource_type": resource_info.get("resource_type", "None"),
                    "resource_name": resource_info.get("resource_name", "None"),
                }
            action = hook.get("action", "None")
            elapsed_seconds = hook.get("elapsed_seconds")

        if change is not None:
            resource_info = change.get("resource") or {}
            addr = resource_info.get("addr") or resource_info.get("resource")
            if addr:
                fields = parse_address(addr)
            action = change.get("action", action)

        normalized_records.append(
            {
                "resource_id": fields["resource_id"],
                "timestamp": record["@timestamp"],
                "type": record_type,
                "module": fields["module"],
                "resource": fields["resource"],
                "resource_type": fields["resource_type"],
                "resource_name": fields["resource_name"],
                "action": action,
                "elapsed_seconds": elapsed_seconds,
            }
        )

    return normalized_records
