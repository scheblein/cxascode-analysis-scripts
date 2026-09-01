import json
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath("config.py"))))
import commonlib.config as cfg
import commonlib.normalized_cache as norm_cache
import commonlib.prep_tf_log_trace as trace

def read_json_from_file(file_path):
    logger = cfg.configure_capture_error_log(file_path, "parse-plan-output-errors")
    records = []
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.error(
                    "Failed to parse line '%s' at line %s: %s",
                    line,
                    exc.lineno,
                    exc,
                )
    return records


def _normalize_records_impl(records):
    return trace.normalize_ui_plan_records(records)


def normalize_records(records, source_path=None):
    """
    Normalizes terraform plan -json UI output (@module terraform.ui, hook/change).
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
