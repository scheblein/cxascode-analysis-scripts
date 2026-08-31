import json
import logging
import re
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('config.py'))))
import commonlib.config as cfg
import commonlib.event_timing as event_timing
import commonlib.normalized_cache as norm_cache

# Set up logging
logging.basicConfig(filename='parse_export_errors.log', level=logging.ERROR)

def read_json_from_file(file_path):
    """
    Reads and parses JSON records from a file, one record per line.
    
    Args:
        file_path (str): Path to the JSON file to read
        
    Returns:
        list: List of parsed JSON records as dictionaries
        
    Raises:
        JSONDecodeError: If a line contains invalid JSON. Error is logged but not raised.
        
    Each line in the file should contain a complete, valid JSON object. Invalid JSON
    lines are logged as errors and skipped.
    """
    records = []
    with open(file_path, 'r') as file:
        for line in file:
            try:
                # Attempt to parse each record into a dictionary
                record = json.loads(line)
                
                # Perform any additional processing on the parsed record here...
                # For example, you might extract specific fields or values from the record
                records.append(record)
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse line '{line.strip()}' at line {e.lineno}: {e}")
    return records

def _normalize_records_impl(records):
    normalized_records = []

    start_re = re.compile(
        r"Started processing for resource:\s(?P<type>[^.]+)\.(?P<label>\S+)\s\((?P<id>[^)]+)\)"
    )
    end_re = re.compile(
        r"Collected resource:\sType=(?P<type>[^,]+),\s*BlockLabel=(?P<label>[^,]+),\s*ID=(?P<id>.+)$"
    )

    for record in records:
        if "genesyscloud_resource_exporter" not in record.get("@caller", ""):
            continue

        message = record.get("@message", "")
        resourceType = resourceLabel = resourceId = exportEvent = None
        m = None

        m = start_re.search(message)
        if m:
            exportEvent = "export_start"
        else:
            m = end_re.search(message)
            if m:
                exportEvent = "export_end"

        if not m:
            continue

        resourceType = m.group("type").strip()
        resourceLabel = m.group("label").strip()
        resourceId = m.group("id").strip()

        ts = record.get("@timestamp") or record.get("timestamp")
        if not ts:
            continue

        normalized_records.append({
            'resource_id': resourceId,
            'timestamp': ts,
            'resource': resourceType + '.' + resourceLabel,
            'resource_type': resourceType,
            'resource_label': resourceLabel,
            'type': exportEvent
        })

    return normalized_records


def normalize_records(records, source_path=None):
    """
    Normalizes Terraform export log records into a standardized format.
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


def export_timing_dataframes(normalized_records):
    return event_timing.event_timing_dataframes(
        normalized_records,
        start_type="export_start",
        end_type="export_end",
        group_key="resource_id",
        start_extra_columns=["resource_label"],
    )