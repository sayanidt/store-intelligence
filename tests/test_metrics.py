# PROMPT: Write tests/test_metrics.py to test the /metrics endpoint using FastAPI TestClient: empty store returns 0s not nulls, all-staff clip returns 0 unique visitors, conversion_rate between 0 and 1, re-entries don't double-count unique visitors, zero-purchase store returns conversion_rate=0.0 not error. Mock SQLite database with in-memory fixture.
# CHANGES MADE: Renamed imports (app_db and fastapi_app) to resolve python namespace collision. Held connection open in the mock_db fixture to preserve the shared memory database.

import os
import pytest
import sqlite3
import uuid
from datetime import datetime

# Import database module and override path to avoid module cache collision
import app.database as app_db
app_db.DB_PATH = "file:memdb_metrics?mode=memory&cache=shared"
os.environ["POS_CSV_PATH"] = ""

from fastapi.testclient import TestClient
from app.main import app as fastapi_app
from app.database import init_db, get_db_connection

client = TestClient(fastapi_app)

@pytest.fixture(autouse=True)
def mock_db():
    """
    Fixture to initialize an in-memory SQLite database.
    We keep one connection open so that the shared cache memory database remains active.
    """
    conn = sqlite3.connect(app_db.DB_PATH, uri=True)
    init_db()
    yield conn
    conn.close()

def test_empty_store_metrics():
    """
    Test empty store returns 0s not nulls.
    """
    response = client.get("/stores/STORE_EMPTY/metrics")
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["unique_visitors"] == 0
    assert metrics["conversion_rate"] == 0.0
    assert metrics["queue_depth"] == 0
    assert metrics["abandonment_rate"] == 0.0
    assert metrics["avg_dwell_per_zone"] == {}

def test_all_staff_metrics():
    """
    Test all-staff clip returns 0 unique visitors.
    """
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_STAFF",
            "camera_id": "cam_1",
            "visitor_id": "VIS_staff_1",
            "event_type": "ENTRY",
            "timestamp": "2026-06-04T12:00:00Z",
            "is_staff": True
        },
        {
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_STAFF",
            "camera_id": "cam_1",
            "visitor_id": "VIS_staff_2",
            "event_type": "ENTRY",
            "timestamp": "2026-06-04T12:05:00Z",
            "is_staff": True
        }
    ]
    client.post("/events/ingest", json={"events": events})

    response = client.get("/stores/STORE_STAFF/metrics")
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["unique_visitors"] == 0

def test_conversion_rate_bounds():
    """
    Test conversion_rate is between 0 and 1.
    We inject 10 visitors, 5 billing joins, and 3 POS transactions.
    converted_visitors = min(5, 3) = 3.
    conversion_rate = 3 / 10 = 0.3.
    """
    store_id = "STORE_BOUNDS"
    
    # 1. Insert 3 POS transactions
    conn = get_db_connection()
    for i in range(3):
        conn.execute(
            "INSERT INTO pos_transactions VALUES (?, ?, ?, ?, ?)",
            (f"order_{i}", store_id, "2026-06-04", f"12:30:0{i}", 50.0)
        )
    conn.commit()
    conn.close()

    # 2. Ingest events
    events = []
    # 10 unique non-staff visitors
    for i in range(10):
        events.append({
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_1",
            "visitor_id": f"VIS_visitor_{i}",
            "event_type": "ENTRY",
            "timestamp": "2026-06-04T12:00:00Z",
            "is_staff": False
        })
    # 5 billing queue joins
    for i in range(5):
        events.append({
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_3",
            "visitor_id": f"VIS_visitor_{i}",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": "2026-06-04T12:15:00Z",
            "is_staff": False
        })
    client.post("/events/ingest", json={"events": events})

    response = client.get(f"/stores/{store_id}/metrics")
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["unique_visitors"] == 10
    assert 0.0 <= metrics["conversion_rate"] <= 1.0
    assert metrics["conversion_rate"] == 0.3

def test_reentries_deduplication():
    """
    Test that re-entries don't double-count unique visitors.
    We ingest an ENTRY, EXIT, and REENTRY for the same visitor.
    """
    store_id = "STORE_REENTRY"
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_1",
            "visitor_id": "VIS_visitor_reentry",
            "event_type": "ENTRY",
            "timestamp": "2026-06-04T12:00:00Z",
            "is_staff": False
        },
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_1",
            "visitor_id": "VIS_visitor_reentry",
            "event_type": "EXIT",
            "timestamp": "2026-06-04T12:10:00Z",
            "is_staff": False
        },
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_1",
            "visitor_id": "VIS_visitor_reentry",
            "event_type": "REENTRY",
            "timestamp": "2026-06-04T12:15:00Z",
            "is_staff": False
        }
    ]
    client.post("/events/ingest", json={"events": events})

    response = client.get(f"/stores/{store_id}/metrics")
    assert response.status_code == 200
    metrics = response.json()
    # Unique visitors should be 1, because count of ENTRY events for non-staff visitor is 1
    assert metrics["unique_visitors"] == 1

def test_zero_purchase_store():
    """
    Test zero-purchase store returns conversion_rate=0.0 not error.
    We ingest ENTRY and BILLING_QUEUE_JOIN, but no transactions are recorded.
    """
    store_id = "STORE_ZERO_PURCHASE"
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_1",
            "visitor_id": "VIS_cust",
            "event_type": "ENTRY",
            "timestamp": "2026-06-04T12:00:00Z",
            "is_staff": False
        },
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_3",
            "visitor_id": "VIS_cust",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": "2026-06-04T12:10:00Z",
            "is_staff": False
        }
    ]
    client.post("/events/ingest", json={"events": events})

    response = client.get(f"/stores/{store_id}/metrics")
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["unique_visitors"] == 1
    assert metrics["conversion_rate"] == 0.0
