import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import List
from fastapi import APIRouter, HTTPException
from app.models import AnomaliesResponse, Anomaly
from app.database import get_db_connection

logger = logging.getLogger("store_intelligence")
router = APIRouter()

def get_conversion_rate_helper(conn, store_id: str, start_time: str = None, end_time: str = None) -> float:
    """
    Helper to calculate conversion rate for a store within a given time range.
    """
    time_filter = ""
    params = {"store_id": store_id}
    if start_time:
        time_filter += " AND datetime(timestamp) >= datetime(:start_time)"
        params["start_time"] = start_time
    if end_time:
        time_filter += " AND datetime(timestamp) <= datetime(:end_time)"
        params["end_time"] = end_time

    # Total visitors
    query_visitors = f"""
        SELECT COUNT(DISTINCT visitor_id) as cnt FROM events
        WHERE store_id = :store_id AND event_type = 'ENTRY' AND is_staff = 0
        {time_filter}
    """
    res_visitors = conn.execute(query_visitors, params).fetchone()
    total_visitors = res_visitors["cnt"] if res_visitors else 0
    if total_visitors == 0:
        return 0.0

    # Billing visitors
    query_billing = f"""
        SELECT COUNT(DISTINCT visitor_id) as cnt FROM events
        WHERE store_id = :store_id
          AND (event_type = 'BILLING_QUEUE_JOIN' OR zone_id = 'billing')
          AND is_staff = 0
          {time_filter}
    """
    res_billing = conn.execute(query_billing, params).fetchone()
    billing_visitors = res_billing["cnt"] if res_billing else 0

    # POS transaction count
    pos_time_filter = ""
    pos_params = {"store_id": store_id}
    if start_time:
        pos_time_filter += " AND datetime(order_date || 'T' || order_time) >= datetime(:start_time)"
        pos_params["start_time"] = start_time
    if end_time:
        pos_time_filter += " AND datetime(order_date || 'T' || order_time) <= datetime(:end_time)"
        pos_params["end_time"] = end_time

    query_pos = f"""
        SELECT COUNT(*) as cnt FROM pos_transactions
        WHERE store_id = :store_id
        {pos_time_filter}
    """
    res_pos = conn.execute(query_pos, pos_params).fetchone()
    pos_count = res_pos["cnt"] if res_pos else 0

    converted = min(billing_visitors, pos_count)
    return float(converted) / float(total_visitors)

@router.get("/stores/{store_id}/anomalies", response_model=AnomaliesResponse)
async def get_store_anomalies(store_id: str):
    """
    Analyze store metrics and events to detect active anomalies:
    1. BILLING_QUEUE_SPIKE: Queue depth > 5
    2. CONVERSION_DROP: Today's conversion < 0.5 * historical average
    3. DEAD_ZONE: Any active zone with no events in the last 30 minutes
    4. STALE_FEED: No events in the store for the last 10 minutes
    """
    conn = get_db_connection()
    anomalies = []
    now = datetime.utcnow()
    detected_at = now

    try:
        # Check if the store has any events at all
        query_any_events = "SELECT COUNT(*) as cnt FROM events WHERE store_id = ?"
        res_any = conn.execute(query_any_events, (store_id,)).fetchone()
        has_events = res_any["cnt"] > 0 if res_any else False

        # --- 1. STALE_FEED anomaly check ---
        ten_minutes_ago = (now - timedelta(minutes=10)).isoformat()
        query_latest = "SELECT timestamp FROM events WHERE store_id = ? ORDER BY timestamp DESC LIMIT 1"
        res_latest = conn.execute(query_latest, (store_id,)).fetchone()
        
        is_stale = False
        if res_latest:
            latest_ts = res_latest["timestamp"]
            # Extract date if it has 'Z' or offset
            clean_ts = latest_ts.split("+")[0].split("Z")[0]
            try:
                latest_datetime = datetime.fromisoformat(clean_ts)
                if latest_datetime < (now - timedelta(minutes=10)):
                    is_stale = True
            except ValueError:
                is_stale = True
        else:
            is_stale = True

        if is_stale:
            anomalies.append(
                Anomaly(
                    anomaly_type="STALE_FEED",
                    severity="WARN",
                    description="No events received for this store in the last 10 minutes.",
                    suggested_action="Verify camera pipeline status, network connectivity, and edge daemon health.",
                    detected_at=detected_at
                )
            )

        # We can run other checks only if the store has ever produced events
        if has_events:
            # --- 2. BILLING_QUEUE_SPIKE anomaly check ---
            query_queue = """
                SELECT metadata FROM events
                WHERE store_id = ?
                  AND event_type = 'BILLING_QUEUE_JOIN'
                  AND is_staff = 0
                ORDER BY timestamp DESC LIMIT 1
            """
            res_queue = conn.execute(query_queue, (store_id,)).fetchone()
            queue_depth = 0
            if res_queue and res_queue["metadata"]:
                try:
                    meta = json.loads(res_queue["metadata"])
                    queue_depth = int(meta.get("queue_depth", 0))
                except (json.JSONDecodeError, ValueError, TypeError):
                    queue_depth = 0

            if queue_depth > 5:
                anomalies.append(
                    Anomaly(
                        anomaly_type="BILLING_QUEUE_SPIKE",
                        severity="WARN",
                        description=f"Billing queue depth is currently {queue_depth}, exceeding threshold of 5.",
                        suggested_action="Deploy additional billing staff to reduce customer waiting times.",
                        detected_at=detected_at
                    )
                )

            # --- 3. CONVERSION_DROP anomaly check ---
            today_str = now.date().isoformat()
            today_start = today_str + "T00:00:00"
            today_end = today_str + "T23:59:59"

            today_rate = get_conversion_rate_helper(conn, store_id, today_start, today_end)
            historical_rate = get_conversion_rate_helper(conn, store_id, end_time=today_start)

            if historical_rate > 0:
                if today_rate < (0.5 * historical_rate):
                    anomalies.append(
                        Anomaly(
                            anomaly_type="CONVERSION_DROP",
                            severity="CRITICAL",
                            description=f"Today's conversion rate ({round(today_rate * 100, 2)}%) is below 50% of the historical average ({round(historical_rate * 100, 2)}%).",
                            suggested_action="Check zone engagement, inventory levels, and checkout service latency.",
                            detected_at=detected_at
                        )
                    )

            # --- 4. DEAD_ZONE anomaly check ---
            query_zones = "SELECT DISTINCT zone_id FROM events WHERE store_id = ? AND zone_id IS NOT NULL"
            rows_zones = conn.execute(query_zones, (store_id,)).fetchall()
            zones = [r["zone_id"] for r in rows_zones if r["zone_id"] != "billing" and "billing" not in r["zone_id"].lower()]

            thirty_minutes_ago = (now - timedelta(minutes=30)).isoformat()
            for zone in zones:
                query_zone_events = """
                    SELECT COUNT(*) as cnt FROM events
                    WHERE store_id = ?
                      AND zone_id = ?
                      AND datetime(timestamp) >= datetime(?)
                      AND is_staff = 0
                """
                res_zone = conn.execute(query_zone_events, (store_id, zone, thirty_minutes_ago)).fetchone()
                zone_event_count = res_zone["cnt"] if res_zone else 0

                if zone_event_count == 0:
                    anomalies.append(
                        Anomaly(
                            anomaly_type="DEAD_ZONE",
                            severity="INFO",
                            description=f"Zone '{zone}' has not recorded any visitor activity in the last 30 minutes.",
                            suggested_action="Check camera feed alignment, sensor status, or restock the merchandise in this zone.",
                            detected_at=detected_at
                        )
                    )

        return AnomaliesResponse(anomalies=anomalies)
    except sqlite3.Error as e:
        logger.error(f"Database error in get_store_anomalies: {e}")
        raise e
    finally:
        conn.close()
