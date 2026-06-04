# PROMPT: Write tests/test_anomalies.py to test the /anomalies endpoint: DEAD_ZONE fires when no events in 30 min, BILLING_QUEUE_SPIKE fires when queue_depth > 5, STALE_FEED fires when last event > 10 min ago, each anomaly has severity field (INFO/WARN/CRITICAL), suggested_action is a non-empty string. Mock SQLite database with in-memory fixture.
# CHANGES MADE: Renamed imports (app_db and fastapi_app) to resolve python namespace collision. Held connection open in the mock_db fixture to preserve the shared memory database.

import os
import pytest
import sqlite3
import uuid
from datetime import datetime, timedelta

# Import database module and override path to avoid module cache collision
import app.database as app_db
app_db.DB_PATH = "file:memdb_anomalies?mode=memory&cache=shared"
os.environ["POS_CSV_PATH"] = ""

from fastapi.testclient import TestClient
from app.main import app as fastapi_app
from app.database import init_db

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

def test_dead_zone_anomaly():
    """
    Test DEAD_ZONE anomaly fires when an active zone records no events in the last 30 minutes.
    """
    store_id = "STORE_DEAD_ZONE"
    now = datetime.utcnow()
    
    # Zone A (dead): Event was 40 minutes ago
    ts_dead = (now - timedelta(minutes=40)).isoformat() + "Z"
    # Zone B (active): Event was 2 minutes ago (prevents STALE_FEED)
    ts_active = (now - timedelta(minutes=2)).isoformat() + "Z"
    
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_1",
            "visitor_id": "VIS_visitor_1",
            "event_type": "ZONE_ENTER",
            "timestamp": ts_dead,
            "zone_id": "makeup_shelf",
            "is_staff": False
        },
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_2",
            "visitor_id": "VIS_visitor_2",
            "event_type": "ZONE_ENTER",
            "timestamp": ts_active,
            "zone_id": "fragrance_section",
            "is_staff": False
        }
    ]
    client.post("/events/ingest", json={"events": events})

    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    
    # Assert DEAD_ZONE fires for makeup_shelf
    dead_zone_anomalies = [a for a in anomalies if a["anomaly_type"] == "DEAD_ZONE" and "makeup_shelf" in a["description"]]
    assert len(dead_zone_anomalies) == 1
    assert dead_zone_anomalies[0]["severity"] == "INFO"

def test_billing_queue_spike_anomaly():
    """
    Test BILLING_QUEUE_SPIKE fires when queue_depth > 5.
    """
    store_id = "STORE_QUEUE_SPIKE"
    now = datetime.utcnow()
    ts = (now - timedelta(minutes=2)).isoformat() + "Z"

    events = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_3",
            "visitor_id": "VIS_visitor_q",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": ts,
            "is_staff": False,
            "metadata": {"queue_depth": 6}
        }
    ]
    client.post("/events/ingest", json={"events": events})

    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    
    spike_anomalies = [a for a in anomalies if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spike_anomalies) == 1
    assert spike_anomalies[0]["severity"] == "WARN"

def test_stale_feed_anomaly():
    """
    Test STALE_FEED fires when last event > 10 min ago.
    """
    store_id = "STORE_STALE_FEED"
    now = datetime.utcnow()
    
    # Ingest event 15 minutes ago
    ts_stale = (now - timedelta(minutes=15)).isoformat() + "Z"
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_1",
            "visitor_id": "VIS_visitor_s",
            "event_type": "ENTRY",
            "timestamp": ts_stale,
            "is_staff": False
        }
    ]
    client.post("/events/ingest", json={"events": events})

    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    
    stale_anomalies = [a for a in anomalies if a["anomaly_type"] == "STALE_FEED"]
    assert len(stale_anomalies) == 1
    assert stale_anomalies[0]["severity"] == "WARN"

def test_severity_and_suggested_action_format():
    """
    Test that each anomaly has a severity field in (INFO/WARN/CRITICAL)
    and suggested_action is a non-empty string.
    """
    store_id = "STORE_FORMAT"
    now = datetime.utcnow()
    ts_stale = (now - timedelta(minutes=15)).isoformat() + "Z"
    
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "store_id": store_id,
            "camera_id": "cam_1",
            "visitor_id": "VIS_visitor_f",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": ts_stale,
            "is_staff": False,
            "metadata": {"queue_depth": 7}
        }
    ]
    client.post("/events/ingest", json={"events": events})

    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    anomalies = response.json()["anomalies"]
    
    assert len(anomalies) > 0
    for anomaly in anomalies:
        assert anomaly["severity"] in {"INFO", "WARN", "CRITICAL"}
        assert isinstance(anomaly["suggested_action"], str)
        assert len(anomaly["suggested_action"].strip()) > 0
