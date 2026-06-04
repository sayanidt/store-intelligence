import sqlite3
import json
import logging
from typing import Optional, Dict
from datetime import datetime
from fastapi import APIRouter, Request, Query, HTTPException
from app.models import MetricsResponse
from app.database import get_db_connection

logger = logging.getLogger("store_intelligence")
router = APIRouter()

@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
async def get_store_metrics(
    store_id: str,
    start_time: Optional[str] = Query(None, description="ISO timestamp start filter"),
    end_time: Optional[str] = Query(None, description="ISO timestamp end filter")
):
    """
    Calculate and return real-time metrics for a specific store.
    Filters: start_time and end_time (optional, ISO 8601).
    All calculations exclude staff events (is_staff = 1).
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
        # Build SQL condition filters with datetime standardization
        time_filter_sql = ""
        params = {"store_id": store_id}
        
        if start_time:
            time_filter_sql += " AND datetime(timestamp) >= datetime(:start_time)"
            params["start_time"] = start_time
        if end_time:
            time_filter_sql += " AND datetime(timestamp) <= datetime(:end_time)"
            params["end_time"] = end_time

        # 1. Unique Visitors (count of ENTRY events where is_staff=0)
        query_visitors = f"""
            SELECT COUNT(DISTINCT visitor_id) as unique_visitors
            FROM events
            WHERE store_id = :store_id
              AND event_type = 'ENTRY'
              AND is_staff = 0
              {time_filter_sql}
        """
        res_visitors = conn.execute(query_visitors, params).fetchone()
        unique_visitors = res_visitors["unique_visitors"] if res_visitors else 0

        # 2. Conversion Rate calculation
        conversion_rate = 0.0
        if unique_visitors > 0:
            # Query visitors who had a billing zone event in the time window
            query_billing_visitors = f"""
                SELECT COUNT(DISTINCT visitor_id) as billing_visitors
                FROM events
                WHERE store_id = :store_id
                  AND (event_type = 'BILLING_QUEUE_JOIN' OR zone_id = 'billing')
                  AND is_staff = 0
                  {time_filter_sql}
            """
            res_billing = conn.execute(query_billing_visitors, params).fetchone()
            billing_visitors = res_billing["billing_visitors"] if res_billing else 0

            # Query POS transactions count in the time window
            pos_time_filter = ""
            pos_params = {"store_id": store_id}
            if start_time:
                pos_time_filter += " AND datetime(order_date || 'T' || order_time) >= datetime(:start_time)"
                pos_params["start_time"] = start_time
            if end_time:
                pos_time_filter += " AND datetime(order_date || 'T' || order_time) <= datetime(:end_time)"
                pos_params["end_time"] = end_time

            query_pos = f"""
                SELECT COUNT(*) as pos_count
                FROM pos_transactions
                WHERE store_id = :store_id
                  {pos_time_filter}
            """
            res_pos = conn.execute(query_pos, pos_params).fetchone()
            pos_count = res_pos["pos_count"] if res_pos else 0

            # Conversion Rate = min(billing_visitors, pos_count) / total_visitors
            converted_visitors = min(billing_visitors, pos_count)
            conversion_rate = float(converted_visitors) / float(unique_visitors)

        # 3. Average Dwell time per zone (excluding staff)
        query_dwell = f"""
            SELECT zone_id, AVG(dwell_ms) as avg_dwell
            FROM events
            WHERE store_id = :store_id
              AND zone_id IS NOT NULL
              AND is_staff = 0
              AND dwell_ms > 0
              {time_filter_sql}
            GROUP BY zone_id
        """
        rows_dwell = conn.execute(query_dwell, params).fetchall()
        avg_dwell_per_zone = {row["zone_id"]: float(row["avg_dwell"]) for row in rows_dwell}

        # 4. Current Queue Depth (from latest BILLING_QUEUE_JOIN event)
        # Note: Current queue depth represents the absolute latest status, ignoring time window filters.
        query_queue = """
            SELECT metadata
            FROM events
            WHERE store_id = :store_id
              AND event_type = 'BILLING_QUEUE_JOIN'
              AND is_staff = 0
            ORDER BY timestamp DESC LIMIT 1
        """
        res_queue = conn.execute(query_queue, {"store_id": store_id}).fetchone()
        queue_depth = 0
        if res_queue and res_queue["metadata"]:
            try:
                meta = json.loads(res_queue["metadata"])
                queue_depth = int(meta.get("queue_depth", 0))
            except (json.JSONDecodeError, ValueError, TypeError):
                queue_depth = 0

        # 5. Abandonment Rate (BILLING_QUEUE_ABANDON / total BILLING_QUEUE_JOIN)
        query_abandon = f"""
            SELECT event_type, COUNT(*) as cnt
            FROM events
            WHERE store_id = :store_id
              AND event_type IN ('BILLING_QUEUE_JOIN', 'BILLING_QUEUE_ABANDON')
              AND is_staff = 0
              {time_filter_sql}
            GROUP BY event_type
        """
        rows_abandon = conn.execute(query_abandon, params).fetchall()
        
        counts = {"BILLING_QUEUE_JOIN": 0, "BILLING_QUEUE_ABANDON": 0}
        for row in rows_abandon:
            counts[row["event_type"]] = row["cnt"]

        joins = counts["BILLING_QUEUE_JOIN"]
        abandons = counts["BILLING_QUEUE_ABANDON"]
        abandonment_rate = float(abandons) / float(joins) if joins > 0 else 0.0

        return {
            "unique_visitors": unique_visitors,
            "conversion_rate": conversion_rate,
            "avg_dwell_per_zone": avg_dwell_per_zone,
            "queue_depth": queue_depth,
            "abandonment_rate": abandonment_rate
        }
    except sqlite3.Error as e:
        logger.error(f"Database error in get_store_metrics: {e}")
        raise e
    finally:
        conn.close()
