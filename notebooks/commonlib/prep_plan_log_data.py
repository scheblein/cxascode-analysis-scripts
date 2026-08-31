import json
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath("config.py"))))
import commonlib.config as cfg
import commonlib.event_timing as event_timing
import commonlib.normalized_cache as norm_cache
import commonlib.prep_tf_log_trace as trace

logging.basicConfig(filename="parse_plan_log_errors.log", level=logging.ERROR)


def read_json_from_file(file_path):
    records = []
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logging.error(
                    "Failed to parse line '%s' at line %s: %s",
                    line,
                    exc.lineno,
                    exc,
                )
    return records


def _normalize_records_impl(records):
    return trace.normalize_plan_trace_records(records)


def normalize_records(records, source_path=None):
    """
    Normalizes TF_LOG=json trace output from terraform plan (graph refresh,
    PlanResourceChange RPC, etc.).
    """
    c = cfg.Config()
    source_path = source_path or c.TERRAFORM_LOG_PATH
    cache_path = norm_cache.terraform_cache_path(source_path)
    return norm_cache.normalize_with_cache(
        source_path,
        cache_path,
        records,
        _normalize_records_impl,
    )


def load_normalized_records(source_path=None):
    c = cfg.Config()
    source_path = source_path or c.TERRAFORM_LOG_PATH
    cache_path = norm_cache.terraform_cache_path(source_path)
    return norm_cache.load_or_normalize(
        source_path,
        cache_path,
        read_json_from_file,
        _normalize_records_impl,
    )


def refresh_timing_dataframes(normalized_records):
    return event_timing.event_timing_dataframes(
        normalized_records,
        start_type="refresh_start",
        end_type="refresh_complete",
        group_key="resource_id",
        start_extra_columns=["resource_name"],
    )
