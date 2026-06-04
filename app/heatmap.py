import sqlite3
import logging
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Request, Query, HTTPException
from app.models import HeatmapResponse, ZoneHeatmap
from app.database import get_db_connection

logger = logging.getLogger("store_intelligence")
router = APIRouter()

@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_store_heatmap(
    store_id: str,
    start_time: Optional[str] = Query(None, description="ISO timestamp start filter"),
    end_time: Optional[str] = Query(None, description="ISO timestamp end filter")
):
    """
    Generate a visual heatmap of the store zones.
    Calculates visits, average dwell time, and a normalized traffic score (0-100).
    Sets confidence to 'LOW' if the store has fewer than 20 sessions.
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

        # 2. Get total sessions for confidence check
        query_total_sessions = f"""
            SELECT COUNT(DISTINCT s.session_id) as total_sessions
            FROM sessions s
            WHERE s.store_id = :store_id
              AND s.visitor_id NOT IN (SELECT visitor_id FROM events WHERE is_staff = 1)
              {time_filter_sql}
        """
        res_total = conn.execute(query_total_sessions, params).fetchone()
        total_sessions = res_total["total_sessions"] if res_total else 0
        data_confidence = "LOW" if total_sessions < 20 else "NORMAL"

        # 3. Calculate visit count and average dwell time per zone
        # A "visit" is counted as a session having an event in that zone
        query_zones = f"""
            SELECT
                e.zone_id,
                COUNT(DISTINCT s.session_id) as visit_count,
                AVG(CASE WHEN e.dwell_ms > 0 THEN e.dwell_ms ELSE NULL END) as avg_dwell
            FROM events e
            JOIN sessions s ON e.visitor_id = s.visitor_id AND e.store_id = s.store_id
            WHERE e.store_id = :store_id
              AND e.zone_id IS NOT NULL
              AND e.is_staff = 0
              AND datetime(e.timestamp) >= datetime(s.start_time)
              AND (s.end_time IS NULL OR datetime(e.timestamp) <= datetime(s.end_time))
              {time_filter_sql}
            GROUP BY e.zone_id
        """
        rows = conn.execute(query_zones, params).fetchall()

        zones_data = []
        max_visit_count = 0
        for row in rows:
            v_count = row["visit_count"]
            if v_count > max_visit_count:
                max_visit_count = v_count
            zones_data.append({
                "zone_id": row["zone_id"],
                "zone_name": row["zone_id"].replace("_", " ").title(),
                "visit_count": v_count,
                "avg_dwell_ms": float(row["avg_dwell"]) if row["avg_dwell"] else 0.0
            })

        # 4. Normalize scores (0-100 scale based on max visit count)
        heatmap_zones = []
        for zone in zones_data:
            visit_cnt = zone["visit_count"]
            normalized_score = 0.0
            if max_visit_count > 0:
                normalized_score = (float(visit_cnt) / float(max_visit_count)) * 100.0

            heatmap_zones.append(
                ZoneHeatmap(
                    zone_id=zone["zone_id"],
                    zone_name=zone["zone_name"],
                    visit_count=visit_cnt,
                    avg_dwell_ms=round(zone["avg_dwell_ms"], 2),
                    normalized_score=round(normalized_score, 2),
                    data_confidence=data_confidence
                )
            )

        return HeatmapResponse(heatmap=heatmap_zones)
    except sqlite3.Error as e:
        logger.error(f"Database error in get_store_heatmap: {e}")
        raise e
    finally:
        conn.close()
