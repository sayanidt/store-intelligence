import os
import sys
import json
import uuid
import time
import argparse
import urllib.request
import urllib.error
from datetime import datetime

# Mapping of raw events to standard FastAPI event types
EVENT_TYPE_MAPPING = {
    "entry": "ENTRY",
    "exit": "EXIT",
    "zone_entered": "ZONE_ENTER",
    "zone_exited": "ZONE_EXIT",
    "queue_completed": "BILLING_QUEUE_JOIN",
    "queue_abandoned": "BILLING_QUEUE_ABANDON"
}

def parse_iso_timestamp(ts_str):
    """
    Safely parse ISO 8601 string or standard datetime string to datetime object.
    """
    if not ts_str:
        return datetime.utcnow()
    
    # Standardize UTC indicator
    cleaned = ts_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        # Fallback formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
        # If all fail, return current time
        return datetime.utcnow()

def send_batch_to_api(batch, api_url):
    """
    Send a batch of events to the ingestion endpoint.
    """
    payload = {"events": batch}
    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=req_data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            error_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            error_body = e.reason
        print(f"\n[Error] HTTP {e.code} during ingestion: {error_body}")
        return None
    except Exception as e:
        print(f"\n[Error] Network exception during ingestion: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Event Replay Pipeline")
    parser.add_argument("--file", default="../data/sample_events.jsonl", help="Path to sample events JSONL file")
    parser.add_argument("--speed", type=float, default=0.0, help="Replay speed factor. 1.0 = real-time, 0.0 = as fast as possible")
    parser.add_argument("--batch", type=int, default=50, help="Batch size for event ingestion")
    parser.add_argument("--api-url", default="http://localhost:8000/events/ingest", help="Target API ingestion endpoint")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"Error: Event file not found at {args.file}")
        sys.exit(1)

    print(f"Reading events from {args.file}...")
    
    # 1. Read and parse raw events
    raw_events = []
    with open(args.file, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw_events.append((idx, json.loads(line)))
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping malformed JSON line {idx}: {e}")

    if not raw_events:
        print("No valid events found to replay.")
        sys.exit(0)

    # Pre-parse timestamps for sorting
    parsed_events = []
    for line_num, item in raw_events:
        raw_ts = item.get("event_timestamp") or item.get("event_time") or item.get("queue_join_ts")
        dt = parse_iso_timestamp(raw_ts)
        parsed_events.append((dt, line_num, item))

    # Sort events by timestamp to ensure correct chronological sequence
    parsed_events.sort(key=lambda x: x[0])
    total_events = len(parsed_events)
    print(f"Chronologically sorted {total_events} events. Processing schemas...")

def standardize_event(raw, dt, visitor_seqs, active_zones, exited_visitors):
    """
    Standardize a raw event dictionary into the target IngestEvent schema format.
    """
    # Standardize store_id
    raw_store = raw.get("store_code") or raw.get("store_id") or "unknown"
    store_id = str(raw_store).strip().upper()

    # Standardize visitor_id
    raw_visitor = raw.get("id_token") or raw.get("track_id") or "unknown"
    visitor_id = str(raw_visitor).strip()
    if not visitor_id.startswith("VIS_"):
        visitor_id = f"VIS_{visitor_id}"

    # Standardize event_type
    raw_type = raw.get("event_type", "entry")
    
    # If the visitor has already exited, maps subsequent entry detection to REENTRY
    if raw_type.lower() == "entry" and visitor_id in exited_visitors:
        event_type = "REENTRY"
    else:
        event_type = EVENT_TYPE_MAPPING.get(raw_type.lower(), raw_type.upper())

    if event_type == "EXIT":
        exited_visitors.add(visitor_id)

    camera_id = raw.get("camera_id") or "CAM_UNKNOWN"
    zone_id = raw.get("zone_id")

    # Compute dwell_ms from enter/exit events
    dwell_ms = 0
    if event_type == "ZONE_ENTER" and zone_id:
        active_zones[(visitor_id, zone_id)] = dt
    elif event_type == "ZONE_EXIT" and zone_id:
        entry_ts = active_zones.pop((visitor_id, zone_id), None)
        if entry_ts:
            dwell_ms = int((dt - entry_ts).total_seconds() * 1000)

    # Track session sequences
    visitor_seqs[visitor_id] = visitor_seqs.get(visitor_id, 0) + 1
    session_seq = visitor_seqs[visitor_id]

    # Extract metadata
    queue_depth = raw.get("queue_position_at_join")
    sku_zone = raw.get("zone_name")

    metadata = {"session_seq": session_seq}
    if queue_depth is not None:
        metadata["queue_depth"] = int(queue_depth)
    if sku_zone is not None:
        metadata["sku_zone"] = sku_zone

    is_staff = bool(raw.get("is_staff", False))

    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": dt.isoformat() + "Z",
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.85,  # Simulated replay constant
        "metadata": metadata
    }

def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Event Replay Pipeline")
    parser.add_argument("--file", default="../data/sample_events.jsonl", help="Path to sample events JSONL file")
    parser.add_argument("--speed", type=float, default=0.0, help="Replay speed factor. 1.0 = real-time, 0.0 = as fast as possible")
    parser.add_argument("--batch", type=int, default=50, help="Batch size for event ingestion")
    parser.add_argument("--api-url", default="http://localhost:8000/events/ingest", help="Target API ingestion endpoint")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"Error: Event file not found at {args.file}")
        sys.exit(1)

    print(f"Reading events from {args.file}...")
    
    # 1. Read and parse raw events
    raw_events = []
    with open(args.file, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw_events.append((idx, json.loads(line)))
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping malformed JSON line {idx}: {e}")

    if not raw_events:
        print("No valid events found to replay.")
        sys.exit(0)

    # Pre-parse timestamps for sorting
    parsed_events = []
    for line_num, item in raw_events:
        raw_ts = item.get("event_timestamp") or item.get("event_time") or item.get("queue_join_ts")
        dt = parse_iso_timestamp(raw_ts)
        parsed_events.append((dt, line_num, item))

    # Sort events by timestamp to ensure correct chronological sequence
    parsed_events.sort(key=lambda x: x[0])
    total_events = len(parsed_events)
    print(f"Chronologically sorted {total_events} events. Processing schemas...")

    # 2. Convert raw events to standardized IngestEvent schemas
    standardized_events = []
    
    # State trackers for sequential operations
    visitor_seqs = {}
    active_zones = {}  # maps (visitor_id, zone_id) -> entry_timestamp
    exited_visitors = set()

    for dt, line_num, raw in parsed_events:
        standard_event = standardize_event(raw, dt, visitor_seqs, active_zones, exited_visitors)
        standardized_events.append((dt, standard_event))

    # 3. Replay and POST events
    print(f"Replaying to ingestion endpoint: {args.api_url}")
    print(f"Speed factor: {args.speed} (0.0 = fast, 1.0 = real-time)")
    
    ingested_count = 0
    batch_buffer = []
    
    last_event_time = None
    
    for i, (dt, event) in enumerate(standardized_events, 1):
        # Handle speed delay
        if args.speed > 0.0 and last_event_time is not None:
            delta = (dt - last_event_time).total_seconds()
            sleep_time = delta / args.speed
            if sleep_time > 0.0:
                time.sleep(sleep_time)
        
        last_event_time = dt
        batch_buffer.append(event)

        # If batch size reached or last element, POST
        if len(batch_buffer) >= args.batch or i == total_events:
            send_batch_to_api(batch_buffer, args.api_url)
            
            # Print individual progress for all items in the batch
            for b_idx, item in enumerate(batch_buffer):
                n_index = i - len(batch_buffer) + b_idx + 1
                print(f"Ingested event {n_index}/{total_events} — type: {item['event_type']} store: {item['store_id']}")
                
            ingested_count += len(batch_buffer)
            batch_buffer = []

    print(f"\nReplay completed. Standardized and replayed {ingested_count} events.")

if __name__ == "__main__":
    main()
