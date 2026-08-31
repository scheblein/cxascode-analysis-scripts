import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from commonlib.prep_tf_log_trace import (
    INVALID_PLAN_RE,
    REFRESH_COMPLETE_RE,
    REFRESH_START_RE,
    REFRESH_LINE_RE,
    VERTEX_COMPLETE_RE,
    VERTEX_START_RE,
)

EXPORT_START_RE = re.compile(
    r"Started processing for resource:\s(?P<type>[^.]+)\.(?P<label>\S+)\s\((?P<id>[^)]+)\)"
)
EXPORT_END_RE = re.compile(
    r"Collected resource:\sType=(?P<type>[^,]+),\s*BlockLabel=(?P<label>[^,]+),\s*ID=(?P<id>.+)$"
)

PLAN_UI_TYPES = {
    "refresh_start",
    "refresh_complete",
    "planned_change",
    "resource_drift",
}

APPLY_UI_TYPES = {
    "apply_start",
    "apply_complete",
    "apply_progress",
    "apply_errored",
    "provision_start",
    "provision_complete",
    "provision_progress",
    "provision_errored",
}

UI_TYPES = PLAN_UI_TYPES | APPLY_UI_TYPES | {"change_summary", "version", "outputs", "diagnostic", "log"}

TF_RPC_PLAN_HINTS = {"PlanResourceChange", "PlanAction", "PlanEphemeralResource"}
TF_RPC_APPLY_HINTS = {"ApplyResourceChange", "ApplyAction", "ApplyEphemeralResource"}

logging.basicConfig(filename="classify_tf_log_errors.log", level=logging.ERROR)


@dataclass
class TfLogClassification:
    file_path: str
    total_lines: int
    parsed_lines: int
    parse_errors: int

    is_export: bool = False
    is_plan: bool = False
    is_apply: bool = False

    log_formats: Counter = field(default_factory=Counter)
    ui_types: Counter = field(default_factory=Counter)
    tf_rpc: Counter = field(default_factory=Counter)
    modules: Counter = field(default_factory=Counter)

    export_start_count: int = 0
    export_end_count: int = 0
    exporter_caller_count: int = 0

    plan_ui_count: int = 0
    apply_ui_count: int = 0
    plan_change_summary_count: int = 0
    apply_change_summary_count: int = 0

    plan_rpc_count: int = 0
    apply_rpc_count: int = 0

    plan_refresh_start_count: int = 0
    plan_refresh_complete_count: int = 0
    plan_planned_change_count: int = 0
    apply_start_trace_count: int = 0
    apply_complete_trace_count: int = 0

    first_timestamp: str | None = None
    last_timestamp: str | None = None
    terraform_version: str | None = None

    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def categories(self) -> list[str]:
        found = []
        if self.is_export:
            found.append("export")
        if self.is_plan:
            found.append("plan")
        if self.is_apply:
            found.append("apply")
        return found

    @property
    def verdict(self) -> str:
        categories = self.categories
        if not categories:
            return "none of the above"
        if len(categories) == 1:
            return categories[0]
        return "all of the above: " + ", ".join(categories)

    @property
    def primary_log_format(self) -> str:
        if self.log_formats.get("terraform.ui", 0) > 0:
            return "terraform plan/apply -json (machine-readable UI)"
        if self.log_formats.get("export", 0) > 0:
            return "genesyscloud export (TF_LOG=json provider logs)"
        if self.log_formats.get("tf_log_json", 0) > 0:
            return "TF_LOG=json trace (JSON-encoded provider/core diagnostics)"
        if self.parsed_lines == 0:
            return "empty or unreadable"
        return "unknown"


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


def _timestamp(record: dict) -> str | None:
    return record.get("@timestamp") or record.get("timestamp")


def classify_record(record: dict, result: TfLogClassification) -> None:
    module = record.get("@module", "")
    record_type = record.get("type")
    caller = record.get("@caller", "")
    message = record.get("@message", "")

    if module:
        result.modules[module] += 1

    ts = _timestamp(record)
    if ts:
        if result.first_timestamp is None:
            result.first_timestamp = ts
        result.last_timestamp = ts

    if module == "terraform.ui":
        result.log_formats["terraform.ui"] += 1
        if record_type:
            result.ui_types[record_type] += 1

        if record_type in PLAN_UI_TYPES:
            result.plan_ui_count += 1
        if record_type in APPLY_UI_TYPES:
            result.apply_ui_count += 1

        if record_type == "change_summary":
            operation = (record.get("changes") or {}).get("operation")
            if operation == "plan":
                result.plan_change_summary_count += 1
            elif operation == "apply":
                result.apply_change_summary_count += 1

        if record_type == "version" and not result.terraform_version:
            result.terraform_version = record.get("terraform")

    elif "genesyscloud_resource_exporter" in caller or EXPORT_START_RE.search(message) or EXPORT_END_RE.search(message):
        result.log_formats["export"] += 1
    else:
        result.log_formats["tf_log_json"] += 1

    if "genesyscloud_resource_exporter" in caller:
        result.exporter_caller_count += 1
    if EXPORT_START_RE.search(message):
        result.export_start_count += 1
    if EXPORT_END_RE.search(message):
        result.export_end_count += 1

    rpc = record.get("tf_rpc")
    if rpc:
        result.tf_rpc[rpc] += 1
        if rpc in TF_RPC_PLAN_HINTS:
            result.plan_rpc_count += 1
        if rpc in TF_RPC_APPLY_HINTS:
            result.apply_rpc_count += 1

    if message.startswith("Terraform version:") and not result.terraform_version:
        result.terraform_version = message.split(":", 1)[1].strip()

    if REFRESH_START_RE.search(message) or REFRESH_LINE_RE.match(message):
        result.plan_refresh_start_count += 1
    if REFRESH_COMPLETE_RE.search(message):
        result.plan_refresh_complete_count += 1
    if INVALID_PLAN_RE.search(message):
        result.plan_planned_change_count += 1
    if (
        record.get("tf_rpc") == "PlanResourceChange"
        and message == "Received downstream response"
    ):
        result.plan_planned_change_count += 1

    if record.get("tf_rpc") == "ApplyResourceChange" and message in {
        "Received request",
        "Received downstream response",
    }:
        if message == "Received request":
            result.apply_start_trace_count += 1
        else:
            result.apply_complete_trace_count += 1


def finalize_classification(result: TfLogClassification) -> TfLogClassification:
    result.is_export = (
        result.exporter_caller_count > 0
        or result.export_start_count > 0
        or result.export_end_count > 0
    )

    has_plan_trace = (
        result.plan_refresh_start_count > 0
        or result.plan_refresh_complete_count > 0
        or result.plan_planned_change_count > 0
        or (result.plan_rpc_count > 0 and result.apply_rpc_count == 0)
    )

    if result.is_export:
        result.is_plan = False
        result.is_apply = False
    elif result.apply_rpc_count > 0 or result.apply_ui_count > 0 or result.apply_change_summary_count > 0:
        result.is_apply = True
        result.is_plan = (
            result.plan_ui_count > 0 or result.plan_change_summary_count > 0
        )
    else:
        result.is_plan = (
            result.plan_ui_count > 0
            or result.plan_change_summary_count > 0
            or has_plan_trace
        )
        result.is_apply = (
            result.apply_ui_count > 0
            or result.apply_change_summary_count > 0
        )

    if result.is_export:
        result.evidence.append(
            f"Export: {result.export_start_count} start messages, "
            f"{result.export_end_count} end messages, "
            f"{result.exporter_caller_count} exporter log lines"
        )

    if result.is_plan:
        plan_bits = []
        if result.plan_ui_count:
            plan_bits.append(f"{result.plan_ui_count} terraform.ui plan events")
        if result.plan_change_summary_count:
            plan_bits.append(f"{result.plan_change_summary_count} change_summary(operation=plan)")
        if result.plan_refresh_start_count:
            plan_bits.append(f"{result.plan_refresh_start_count} TF_LOG refresh_start trace lines")
        if result.plan_refresh_complete_count:
            plan_bits.append(f"{result.plan_refresh_complete_count} TF_LOG refresh_complete trace lines")
        if result.plan_planned_change_count:
            plan_bits.append(f"{result.plan_planned_change_count} TF_LOG planned_change trace lines")
        if result.plan_rpc_count and not plan_bits:
            plan_bits.append(f"{result.plan_rpc_count} PlanResourceChange RPC lines")
        result.evidence.append("Plan: " + ", ".join(plan_bits))

    if result.is_apply:
        apply_bits = []
        if result.apply_ui_count:
            apply_bits.append(f"{result.apply_ui_count} terraform.ui apply events")
        if result.apply_change_summary_count:
            apply_bits.append(f"{result.apply_change_summary_count} change_summary(operation=apply)")
        if result.apply_rpc_count:
            apply_bits.append(f"{result.apply_rpc_count} ApplyResourceChange RPC lines")
        if result.apply_start_trace_count:
            apply_bits.append(f"{result.apply_start_trace_count} TF_LOG apply_start trace lines")
        if result.apply_complete_trace_count:
            apply_bits.append(f"{result.apply_complete_trace_count} TF_LOG apply_complete trace lines")
        result.evidence.append("Apply: " + ", ".join(apply_bits))

    ui_lines = result.log_formats.get("terraform.ui", 0)
    tf_log_lines = result.log_formats.get("tf_log_json", 0)
    export_lines = result.log_formats.get("export", 0)

    if ui_lines and (tf_log_lines or export_lines):
        result.warnings.append(
            "Mixed log formats detected. This file may combine multiple captures "
            f"({ui_lines} UI lines, {tf_log_lines} TF_LOG lines, {export_lines} export lines)."
        )

    if result.is_plan and result.is_apply and result.apply_rpc_count == 0:
        result.warnings.append(
            "Both plan and apply graph activity detected. "
            "Apply logs often include a plan phase; use apply/analysis.ipynb for apply timing."
        )

    return result


def classify_file(file_path: str) -> TfLogClassification:
    result = TfLogClassification(
        file_path=file_path,
        total_lines=0,
        parsed_lines=0,
        parse_errors=0,
    )

    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            result.total_lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                result.parse_errors += 1
                continue

            result.parsed_lines += 1
            classify_record(record, result)

    return finalize_classification(result)


def classification_summary(result: TfLogClassification) -> dict:
    return {
        "file": result.file_path,
        "verdict": result.verdict,
        "categories": result.categories,
        "primary_log_format": result.primary_log_format,
        "total_lines": result.total_lines,
        "parsed_lines": result.parsed_lines,
        "parse_errors": result.parse_errors,
        "terraform_version": result.terraform_version,
        "first_timestamp": result.first_timestamp,
        "last_timestamp": result.last_timestamp,
        "is_export": result.is_export,
        "is_plan": result.is_plan,
        "is_apply": result.is_apply,
        "evidence": result.evidence,
        "warnings": result.warnings,
        "log_formats": dict(result.log_formats),
        "top_ui_types": result.ui_types.most_common(10),
        "top_tf_rpc": result.tf_rpc.most_common(10),
        "top_modules": result.modules.most_common(10),
    }
