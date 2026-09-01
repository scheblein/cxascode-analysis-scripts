import json
import re
import sys
import os
from collections import Counter, defaultdict
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('config.py'))))
import commonlib.config as cfg
import commonlib.normalized_cache as norm_cache


def strip_and_replace_guid(uri):
    """
    Strips GUIDs from a URI and replaces them with {ID} placeholder.
    
    Args:
        uri (str): The URI string containing GUIDs to be replaced
        
    Returns:
        str: The URI with all GUIDs replaced with {ID}
        
    Example:
        >>> strip_and_replace_guid("http://api/123e4567-e89b-12d3-a456-426614174000/resource")
        "http://api/{ID}/resource"
    """
    guid_pattern = r'\w{8}-\w{4}-\w{4}-\w{4}-\w{12}'
    sanitized_uri=uri

    # Find all GUIDs in the string
    guids = re.findall(guid_pattern, uri)

    # Replace each GUID with {guid}
    for guid in guids:
      replacement = '{ID}'
      sanitized_uri = re.sub(guid, replacement, uri, flags=re.IGNORECASE)

    return sanitized_uri

def read_json_from_file(file_path):
    """
    Reads and parses JSON records from a file, one record per line.

    Args:
        file_path (str): Path to the JSON file to read

    Returns:
        list: List of dictionaries containing the parsed JSON records

    Raises:
        JSONDecodeError: If a line cannot be parsed as valid JSON (error will be logged)
        
    Example:
        >>> records = read_json_from_file("data.json")
        >>> print(records[0])  
        {'id': 1, 'name': 'test'}
    """
    logger = cfg.configure_capture_error_log(file_path, "parse-sdk-errors")
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
                logger.error(f"Failed to parse line '{line.strip()}' at line {e.lineno}: {e}")
    return records

def _normalize_records_impl(records):
    normalized_records = []

    for record in records:
        level = record.get("@level")
        msg = record.get("@message") or ""
        timestamp = record.get("@timestamp")
        sdk_debug = "SDK DEBUG" in msg

        if level == "info" and sdk_debug:
            raw_data = msg[20:]
            msg_json = json.loads(raw_data)
            msg_json["timestamp"] = timestamp
            msg_json["sanitized_url"] = strip_and_replace_guid(msg_json["invocation_url"])

            retry_after = msg_json.get("invocation_retry_after")
            if retry_after is None:
                msg_json["invocation_retry_after"] = 0
            else:
                msg_json["invocation_retry_after"] = int(msg_json["invocation_retry_after"])

            normalized_records.append(msg_json)

    return normalized_records


def normalize_records(records, source_path=None):
    """
    Normalizes a list of log records by extracting and transforming SDK debug messages.
    """
    c = cfg.Config()
    source_path = source_path or c.TERRAFORM_LOG_PATH
    cache_path = norm_cache.sdk_cache_path(source_path)
    return norm_cache.normalize_with_cache(
        source_path,
        cache_path,
        records,
        _normalize_records_impl,
    )


def load_normalized_records(source_path=None):
    c = cfg.Config()
    source_path = source_path or c.TERRAFORM_LOG_PATH
    cache_path = norm_cache.sdk_cache_path(source_path)
    return norm_cache.load_or_normalize(
        source_path,
        cache_path,
        read_json_from_file,
        _normalize_records_impl,
    )


def sdk_requests_timeline_dataframe(
    normalized_records,
    bucket_minutes: float = 1.0,
    top_endpoints: int = 5,
):
    """Request counts per minute from SDK norm records (for sdk-analysis.ipynb)."""
    import pandas as pd
    from datetime import datetime

    requests = [
        rec
        for rec in normalized_records
        if rec.get("debug_type") == "SDK DEBUG REQUEST"
    ]
    if not requests:
        return pd.DataFrame()

    timestamps = []
    for rec in requests:
        ts = rec.get("timestamp")
        if not ts:
            continue
        try:
            timestamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except ValueError:
            continue
    if not timestamps:
        return pd.DataFrame()

    start = min(timestamps)
    bucket_seconds = max(1, int(bucket_minutes * 60))
    endpoint_totals = Counter()
    bucket_counts: dict[int, Counter] = defaultdict(Counter)

    for rec in requests:
        ts = rec.get("timestamp")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        bucket = int((dt - start).total_seconds() // bucket_seconds)
        method = rec.get("invocation_method") or "?"
        url = rec.get("sanitized_url") or strip_and_replace_guid(rec.get("invocation_url") or "")
        endpoint = f"{method} {url}"
        bucket_counts[bucket][endpoint] += 1
        endpoint_totals[endpoint] += 1

    selected = {endpoint for endpoint, _ in endpoint_totals.most_common(top_endpoints)}
    for endpoint in endpoint_totals:
        if "/dnclists/" in endpoint and "/export" in endpoint:
            selected.add(endpoint)

    rows = []
    for bucket in sorted(bucket_counts):
        minute = round((bucket * bucket_seconds) / 60, 2)
        for endpoint, count in bucket_counts[bucket].items():
            if endpoint not in selected:
                continue
            rows.append(
                {
                    "minute_from_start": minute,
                    "method_url": endpoint,
                    "request_count": count,
                    "is_dnclist_export": "/dnclists/" in endpoint and "/export" in endpoint,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["minute_from_start", "request_count"], ascending=[True, False])
