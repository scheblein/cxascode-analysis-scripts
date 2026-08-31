import json
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath("config.py"))))
import commonlib.config as cfg
import commonlib.prep_tf_log_trace as trace

logging.basicConfig(filename="parse_plan_output_errors.log", level=logging.ERROR)


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


def normalize_records(records):
    """
    Normalizes terraform plan -json UI output (@module terraform.ui, hook/change).
    """
    normalized_records = trace.normalize_ui_plan_records(records)

    c = cfg.Config()
    if c.NORMALIZED_TERRAFORM_LOG_PATH:
        with open(c.NORMALIZED_TERRAFORM_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(normalized_records, f, indent=4)

    return normalized_records
