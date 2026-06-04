# PROMPT: Write tests/test_pipeline.py to test the emit.py conversion logic: entry maps to ENTRY, queue_abandoned maps to BILLING_QUEUE_ABANDON, exit followed by entry produces REENTRY, is_staff=true is passed through, event_id is valid UUID4, batch size limit (500) validation. Mock SQLite with in-memory database.
# CHANGES MADE: Renamed imports (app_db and fastapi_app) to resolve python namespace collision. Held connection open in the mock_db fixture to preserve the shared memory database.

import os
import pytest
import sqlite3
import uuid
from datetime import datetime

# Import database module and override path to avoid module cache collision
import app.database as app_db
app_db.DB_PATH = "file:memdb_pipeline?mode=memory&cache=shared"
os.environ["POS_CSV_PATH"] = ""

from fastapi.testclient import TestClient
from app.main import app as fastapi_app
from app.database import init_db
from pipeline.emit import standardize_event

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

def test_entry_mapping():
    """
    Test that raw "entry" maps to "ENTRY" event_type.
    """
    raw = {"event_type": "entry", "store_code": "store_101", "track_id": "v1"}
    dt = datetime.utcnow()
    event = standardize_event(raw, dt, {}, {}, set())
    assert event["event_type"] == "ENTRY"

def test_queue_abandoned_mapping():
    """
    Test that raw "queue_abandoned" maps to "BILLING_QUEUE_ABANDON".
    """
    raw = {"event_type": "queue_abandoned", "store_code": "store_101", "track_id": "v1"}
    dt = datetime.utcnow()
    event = standardize_event(raw, dt, {}, {}, set())
    assert event["event_type"] == "BILLING_QUEUE_ABANDON"

def test_reentry_mapping():
    """
    Test that a visitor with a prior EXIT followed by another entry detection produces REENTRY.
    """
    visitor_seqs = {}
    active_zones = {}
    exited_visitors = set()
    dt = datetime.utcnow()

    # First event: ENTRY
    raw_1 = {"event_type": "entry", "store_code": "store_101", "track_id": "v1"}
    event_1 = standardize_event(raw_1, dt, visitor_seqs, active_zones, exited_visitors)
    assert event_1["event_type"] == "ENTRY"

    # Second event: EXIT
    raw_2 = {"event_type": "exit", "store_code": "store_101", "track_id": "v1"}
    event_2 = standardize_event(raw_2, dt, visitor_seqs, active_zones, exited_visitors)
    assert event_2["event_type"] == "EXIT"
    assert "VIS_v1" in exited_visitors

    # Third event: ENTRY again (should resolve to REENTRY)
    raw_3 = {"event_type": "entry", "store_code": "store_101", "track_id": "v1"}
    event_3 = standardize_event(raw_3, dt, visitor_seqs, active_zones, exited_visitors)
    assert event_3["event_type"] == "REENTRY"

def test_is_staff_pass_through():
    """
    Test that is_staff=true events are correctly passed through.
    """
    raw_staff = {"event_type": "entry", "is_staff": True, "track_id": "v1"}
    raw_cust = {"event_type": "entry", "is_staff": False, "track_id": "v2"}
    dt = datetime.utcnow()
    
    event_staff = standardize_event(raw_staff, dt, {}, {}, set())
    event_cust = standardize_event(raw_cust, dt, {}, {}, set())
    
    assert event_staff["is_staff"] is True
    assert event_cust["is_staff"] is False

def test_event_id_uuid4():
    """
    Test that event_id is a valid UUID4.
    """
    raw = {"event_type": "entry", "track_id": "v1"}
    dt = datetime.utcnow()
    event = standardize_event(raw, dt, {}, {}, set())
    
    event_id = event["event_id"]
    parsed_uuid = uuid.UUID(event_id)
    assert parsed_uuid.version == 4

def test_batch_size_validation():
    """
    Test batch size never exceeds 500 at the API routing level.
    """
    # 0 events -> Should fail validation (returns 400)
    response_empty = client.post("/events/ingest", json={"events": []})
    assert response_empty.status_code == 400

    # 501 events -> Exceeds maximum limit (returns 400)
    events_large = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_101",
            "camera_id": "cam_1",
            "visitor_id": f"VIS_{i}",
            "event_type": "ENTRY",
            "timestamp": "2026-06-04T12:00:00Z"
        }
        for i in range(501)
    ]
    response_large = client.post("/events/ingest", json={"events": events_large})
    assert response_large.status_code == 400
    assert "exceeds limit" in response_large.json()["errors"][0]
