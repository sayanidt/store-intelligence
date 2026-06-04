import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from fastapi import APIRouter
from app.models import HealthResponse
from app.database import get_db_connection

logger = logging.getLogger("store_intelligence")
router = APIRouter()

@router.get("/health", response_model=HealthResponse)
async def get_health():
    """
    Checks the health of the application and its database connection.
    Returns the status, db_status, last event timestamp for each store,
    and any warnings about stale feeds (>10 minutes lag).
    """
    last_event_timestamps = {}
    warnings = []
    db_status = "connected"
    status = "OK"

    try:
        conn = get_db_connection()
        # Query the latest event timestamp for each store
        query = "SELECT store_id, MAX(timestamp) as last_ts FROM events GROUP BY store_id"
        rows = conn.execute(query).fetchall()
        conn.close()

        now = datetime.utcnow()
        for row in rows:
            store_id = row["store_id"]
            last_ts = row["last_ts"]
            last_event_timestamps[store_id] = last_ts

            if last_ts:
                # Sanitize the ISO string for parsing
                clean_ts = last_ts.split("+")[0].split("Z")[0]
                try:
                    latest_datetime = datetime.fromisoformat(clean_ts)
                    if latest_datetime < (now - timedelta(minutes=10)):
                        warnings.append(
                            f"STALE_FEED: Store {store_id} has not sent events in the last 10 minutes (last seen {last_ts})."
                        )
                        status = "DEGRADED"
                except ValueError:
                    status = "DEGRADED"
                    warnings.append(f"STALE_FEED: Store {store_id} has an invalid timestamp format ({last_ts}).")
            else:
                last_event_timestamps[store_id] = None
    except Exception as e:
        logger.error(f"Health check database query failed: {e}")
        db_status = "unavailable"
        status = "DEGRADED"
        warnings.append(f"Database connection error: {str(e)}")

    return {
        "status": status,
        "last_event_timestamp": last_event_timestamps,
        "db_status": db_status,
        "warnings": warnings
    }
