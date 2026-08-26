import json
import logging
import re
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('config.py'))))
import commonlib.config as cfg

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

#TODO: Make applicable to export (even if using same tflog!!)
def normalize_records(records):
    """
    Normalizes Terraform log records into a standardized format.
    
    Args:
        records (list): List of raw Terraform log records to normalize
        
    Returns:
        list: List of normalized records with consistent fields
        
    Each normalized record contains:
        - resource_id: ID of the resource (defaults to "None")
        - timestamp: Timestamp from original record
        - type: Record type (e.g. start, end)
        - resource_type: Type of Terraform resource
        - resource_label: Label of the resource
    
    The normalized records are also written to a JSON file at:
    """
    normalized_records = []

    # start_re = re.compile(r"Started processing for resource:\s(.*)\.(.*)\s\((.*)\)")
    # end_re = re.compile(r"Collected resource\:\sType\=(.*)\,\sBlockLabel\=(.*)\,\sID\=(.*)$")
    start_re = re.compile(
        r"Started processing for resource:\s(?P<type>[^.]+)\.(?P<label>\S+)\s\((?P<id>[^)]+)\)"
    )
    end_re = re.compile(
        r"Collected resource:\sType=(?P<type>[^,]+),\s*BlockLabel=(?P<label>[^,]+),\s*ID=(?P<id>.+)$"
    )

    for record in records:
        #only process lines with @caller
        if "genesyscloud_resource_exporter" not in record.get("@caller", ""):
            continue

        message = record.get("@message", "")
        
        #reset variables
        resourceType = resourceLabel = resourceId = exportEvent = None
        m = None

        #does it match start pattern?
        m = start_re.search(message)
        if m:
            exportEvent = "export_start"
        else:
            #does it match end pattern?
            m = end_re.search(message)
            if m:
                exportEvent = "export_end"

        #matches nothing so skip
        if not m:
            continue

        # resourceType = m.group(1).strip()
        # resourceLabel = m.group(2).strip()
        # resourceId = m.group(3).strip()
        resourceType = m.group("type").strip()
        resourceLabel = m.group("label").strip()
        resourceId = m.group("id").strip()

        ts = record.get("@timestamp") or record.get("timestamp")
        if not ts:
            # no timestamp available; skip or set a default
            continue

        normalized_records.append({
            'resource_id': resourceId,
            'timestamp': ts,
            'resource': resourceType + '.' + resourceLabel,
            'resource_type': resourceType,
            'resource_label': resourceLabel,
            'type': exportEvent
        })

    c = cfg.Config()
    with open(c.NORMALIZED_TERRAFORM_LOG_PATH,"w") as f:
        pretty_json=json.dumps(normalized_records, indent=4)
        f.write(pretty_json)

    return normalized_records