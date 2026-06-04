import sqlite3
import logging
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Request, Query, HTTPException
from app.models import FunnelResponse, FunnelStage
from app.database import get_db_connection

logger = logging.getLogger("store_intelligence")
router = APIRouter()

@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
async def get_store_funnel(
    store_id: str,
    start_time: Optional[str] = Query(None, description="ISO timestamp start filter"),
    end_time: Optional[str] = Query(None, description="ISO timestamp end filter")
):
    """
    Calculate the conversion funnel for the store:
    Entry -> Zone Visit -> Billing Queue -> Purchase
    Uses session-based tracking and enforces the progressive funnel hierarchy.
    """
    # Validate datetime inputs if provided
    for ts in (start_time, end_time):
        if ts:
            try:
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid ISO timestamp format: {ts}")

    conn = get_db_connection()
    try:
        # 1. Base time filters with datetime standardization
        time_filter_sql = ""
        params = {"store_id": store_id}
        if start_time:
            time_filter_sql += " AND datetime(s.start_time) >= datetime(:start_time)"
            params["start_time"] = start_time
        if end_time:
            time_filter_sql += " AND datetime(s.start_time) <= datetime(:end_time)"
            params["end_time"] = end_time

        # Get all non-staff sessions in the time window (Stage 1: Entry)
        query_entry_sessions = f"""
            SELECT DISTINCT s.session_id, s.visitor_id, s.start_time, s.end_time
            FROM sessions s
            WHERE s.store_id = :store_id
              AND s.visitor_id NOT IN (SELECT visitor_id FROM events WHERE is_staff = 1)
              {time_filter_sql}
        """
        rows = conn.execute(query_entry_sessions, params).fetchall()
        entry_sessions = [dict(row) for row in rows]
        entry_count = len(entry_sessions)

        # Stage 2: Zone Visit (Entry sessions that visited a non-billing zone)
        zone_count = 0
        zone_session_ids = set()
        if entry_count > 0:
            # Query sessions that have a zone event
            query_zone_sessions = f"""
                SELECT DISTINCT s.session_id
                FROM sessions s
                JOIN events e ON s.visitor_id = e.visitor_id AND s.store_id = e.store_id
                WHERE s.store_id = :store_id
                  AND e.is_staff = 0
                  AND e.event_type IN ('ZONE_ENTER', 'ZONE_EXIT', 'ZONE_DWELL')
                  AND e.zone_id IS NOT NULL
                  AND e.zone_id != 'billing'
                  AND e.zone_id NOT LIKE '%billing%'
                  AND datetime(e.timestamp) >= datetime(s.start_time)
                  AND (s.end_time IS NULL OR datetime(e.timestamp) <= datetime(s.end_time))
                  {time_filter_sql}
            """
            rows_zone = conn.execute(query_zone_sessions, params).fetchall()
            zone_session_ids = {row["session_id"] for row in rows_zone}
            zone_count = len(zone_session_ids)

        # Stage 3: Billing Queue (Zone Visit sessions that joined the billing queue)
        billing_count = 0
        billing_session_ids = set()
        if zone_count > 0:
            # Use placeholders for the zone session IDs to enforce the hierarchy
            placeholders = ",".join("?" for _ in zone_session_ids)
            query_billing_sessions = f"""
                SELECT DISTINCT s.session_id
                FROM sessions s
                JOIN events e ON s.visitor_id = e.visitor_id AND s.store_id = e.store_id
                WHERE s.store_id = ?
                  AND e.is_staff = 0
                  AND e.event_type = 'BILLING_QUEUE_JOIN'
                  AND datetime(e.timestamp) >= datetime(s.start_time)
                  AND (s.end_time IS NULL OR datetime(e.timestamp) <= datetime(s.end_time))
                  AND s.session_id IN ({placeholders})
            """
            # Add store_id and placeholders to query parameters
            query_args = [store_id] + list(zone_session_ids)
            rows_billing = conn.execute(query_billing_sessions, query_args).fetchall()
            billing_session_ids = {row["session_id"] for row in rows_billing}
            billing_count = len(billing_session_ids)

        # Stage 4: Purchase (Billing Queue sessions with a matching POS transaction within 30 min of BILLING_QUEUE_JOIN)
        purchase_count = 0
        if billing_count > 0:
            placeholders = ",".join("?" for _ in billing_session_ids)
            query_purchase_sessions = f"""
                SELECT DISTINCT s.session_id
                FROM sessions s
                JOIN events e ON s.visitor_id = e.visitor_id AND s.store_id = e.store_id
                JOIN pos_transactions p ON s.store_id = p.store_id
                WHERE s.store_id = ?
                  AND e.is_staff = 0
                  AND e.event_type = 'BILLING_QUEUE_JOIN'
                  AND datetime(e.timestamp) >= datetime(s.start_time)
                  AND (s.end_time IS NULL OR datetime(e.timestamp) <= datetime(s.end_time))
                  AND s.session_id IN ({placeholders})
                  AND datetime(p.order_date || 'T' || p.order_time) >= datetime(e.timestamp)
                  AND datetime(p.order_date || 'T' || p.order_time) <= datetime(e.timestamp, '+30 minutes')
            """
            query_args = [store_id] + list(billing_session_ids)
            rows_purchase = conn.execute(query_purchase_sessions, query_args).fetchall()
            purchase_count = len(rows_purchase)

        # Calculate drop-off percentages
        drop_off_zone = ((entry_count - zone_count) / entry_count * 100.0) if entry_count > 0 else 0.0
        drop_off_billing = ((zone_count - billing_count) / zone_count * 100.0) if zone_count > 0 else 0.0
        drop_off_purchase = ((billing_count - purchase_count) / billing_count * 100.0) if billing_count > 0 else 0.0

        funnel_stages = [
            FunnelStage(stage="Entry", count=entry_count, drop_off_pct=0.0),
            FunnelStage(stage="Zone Visit", count=zone_count, drop_off_pct=round(drop_off_zone, 2)),
            FunnelStage(stage="Billing Queue", count=billing_count, drop_off_pct=round(drop_off_billing, 2)),
            FunnelStage(stage="Purchase", count=purchase_count, drop_off_pct=round(drop_off_purchase, 2))
        ]

        return FunnelResponse(funnel=funnel_stages)
    except sqlite3.Error as e:
        logger.error(f"Database error in get_store_funnel: {e}")
        raise e
    finally:
        conn.close()
