import sqlite3
import uuid
import json
import logging
from typing import Any
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse
from app.models import IngestEvent, IngestBatch
from app.database import get_db_connection

logger = logging.getLogger("store_intelligence")
router = APIRouter()

@router.post("/events/ingest")
async def ingest_events(request: Request, payload: Any = Body(...)):
    """
    Ingest a batch of up to 500 events.
    Supports partial success and deduplication by event_id.
    Derives and updates visitor sessions on ENTRY/EXIT/REENTRY events.
    """
    # 1. Parse and check structure of payload
    events_list = []
    if isinstance(payload, dict):
        events_list = payload.get("events", [])
    elif isinstance(payload, list):
        events_list = payload
    else:
        return JSONResponse(
            status_code=400,
            content={
                "ingested": 0,
                "failed": 1,
                "errors": ["Invalid payload format. Expected list of events or object with 'events' field."]
            }
        )

    # Validate batch size
    if not isinstance(events_list, list):
        return JSONResponse(
            status_code=400,
            content={
                "ingested": 0,
                "failed": 1,
                "errors": ["Events must be a list"]
            }
        )
        
    total_events = len(events_list)
    if total_events == 0:
        return JSONResponse(
            status_code=400,
            content={
                "ingested": 0,
                "failed": 0,
                "errors": ["Batch must contain at least 1 event"]
            }
        )
    if total_events > 500:
        return JSONResponse(
            status_code=400,
            content={
                "ingested": 0,
                "failed": 0,
                "errors": ["Batch size exceeds limit of 500 events"]
            }
        )

    # Extract store_id for routing/logging from first event
    store_id = "unknown"
    if total_events > 0 and isinstance(events_list[0], dict):
        store_id = events_list[0].get("store_id", "unknown")

    # Set state for middleware logging
    request.state.store_id = store_id
    request.state.event_count = total_events

    # 2. Iterate and validate each event
    ingested_count = 0
    failed_count = 0
    errors = []
    valid_events = []

    for index, item in enumerate(events_list):
        try:
            if not isinstance(item, dict):
                raise ValueError("Event must be a JSON object")
            # Parse & validate event
            event = IngestEvent(**item)
            valid_events.append(event)
        except Exception as e:
            failed_count += 1
            errors.append(f"Event at index {index} is invalid: {str(e)}")

    # 3. Persist valid events and derive sessions
    if valid_events:
        conn = get_db_connection()
        try:
            with conn:
                for event in valid_events:
                    meta_str = json.dumps(event.metadata) if event.metadata else None
                    
                    # Deduplicate using INSERT OR IGNORE
                    cursor = conn.execute("""
                        INSERT OR IGNORE INTO events (
                            event_id, store_id, camera_id, visitor_id, event_type,
                            timestamp, zone_id, dwell_ms, is_staff, confidence, metadata
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.event_id,
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.event_type,
                        event.timestamp.isoformat(),
                        event.zone_id,
                        event.dwell_ms,
                        1 if event.is_staff else 0,
                        event.confidence,
                        meta_str
                    ))

                    # If inserted (not ignored as duplicate), count it and derive session
                    if cursor.rowcount > 0:
                        ingested_count += 1
                        
                        # Derive visitor sessions
                        if event.event_type in ("ENTRY", "EXIT", "REENTRY"):
                            # Get the most recent session for this visitor and store
                            res = conn.execute("""
                                SELECT session_id, end_time FROM sessions
                                WHERE visitor_id = ? AND store_id = ?
                                ORDER BY start_time DESC LIMIT 1
                            """, (event.visitor_id, event.store_id)).fetchone()

                            event_time_str = event.timestamp.isoformat()

                            if event.event_type in ("ENTRY", "REENTRY"):
                                # If no session exists or the latest session has ended, start a new session.
                                # Reentry event can extend an active session or create a new one. Here we
                                # create a new session if the previous one is closed.
                                if not res or res["end_time"] is not None:
                                    session_id = str(uuid.uuid4())
                                    conn.execute("""
                                        INSERT INTO sessions (session_id, visitor_id, store_id, start_time, end_time)
                                        VALUES (?, ?, ?, ?, NULL)
                                    """, (session_id, event.visitor_id, event.store_id, event_time_str))
                            elif event.event_type == "EXIT":
                                if res and res["end_time"] is None:
                                    # Close the active session
                                    conn.execute("""
                                        UPDATE sessions SET end_time = ?
                                        WHERE session_id = ?
                                    """, (event_time_str, res["session_id"]))
                                else:
                                    # Create a zero-duration session if we receive an EXIT without an active ENTRY
                                    session_id = str(uuid.uuid4())
                                    conn.execute("""
                                        INSERT INTO sessions (session_id, visitor_id, store_id, start_time, end_time)
                                        VALUES (?, ?, ?, ?, ?)
                                    """, (session_id, event.visitor_id, event.store_id, event_time_str, event_time_str))
        except sqlite3.Error as e:
            logger.error(f"SQLite error during event ingestion: {e}")
            raise e
        finally:
            conn.close()

    # If some succeeded and some failed, it is partial success (return 200)
    # If all failed, return 200 with the ingestion status as requested
    return {
        "ingested": ingested_count,
        "failed": failed_count,
        "errors": errors
    }
