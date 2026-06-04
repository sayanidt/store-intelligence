# Store Intelligence API

Store Intelligence is an edge-to-cloud analytical pipeline designed for Purplle beauty retail stores. It ingests computer vision tracking events from cameras, derives visitor sessions, calculates store-level analytics (funnels, queues, dwell times), and raises real-time operational alerts for floor staff.

---

## Architecture Overview
The system leverages edge AI to analyze IP camera streams, running object detection (YOLOv8) and tracking (ByteTrack) to capture visitor trajectory events. These events are processed by a Python client stream pipeline and posted to a centralized FastAPI service. The FastAPI application records telemetry in an SQLite database (configured in WAL mode for safe concurrency), performs real-time SQL aggregates for analytical queries, matches events with POS transaction logs, and runs rule-based checks to detect anomalies like billing spikes, dead zones, and feed delays.

*Note: CCTV video raw files are not included in the repository per challenge rules.*

---

## Quick Start Setup (Exactly 5 Commands)

Follow these five steps to clone, configure, build, run, and verify the Store Intelligence API:

```bash
# 1. Clone the project repository
git clone https://github.com/purplle/store-intelligence.git

# 2. Enter the project root directory
cd store-intelligence

# 3. Create the data directory and populate it with required CSV/JSONL records
mkdir -p data && cp /source/pos_transactions.csv /source/sample_events.jsonl ./data/

# 4. Start the application services using Docker Compose
docker compose up --build

# 5. Verify the health status of the API and SQLite connection
curl -f http://localhost:8000/health
```

---

## How to Run the Detection Pipeline

The pipeline client simulates edge video stream detections by reading chronological logs and replaying them to the API server. You can run the emitter in fast-forward mode (speed = 0) using:

```bash
python pipeline/emit.py --file data/sample_events.jsonl --speed 0
```
To run the replay at real-time speeds based on event timestamps, set `--speed 1.0`.

---

## API Endpoints Reference

All examples below utilize the target retail store ID `STORE_1076`.

### 1. Ingest Event Batch
* **Endpoint**: `POST /events/ingest`
* **Description**: Accepts a batch of up to 500 validated visitor events. Supports partial success.
* **Command**:
  ```bash
  curl -X POST http://localhost:8000/events/ingest \
    -H "Content-Type: application/json" \
    -d '{
      "events": [
        {
          "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
          "store_id": "STORE_1076",
          "camera_id": "cam1",
          "visitor_id": "VIS_shopper_8",
          "event_type": "ENTRY",
          "timestamp": "2026-06-04T12:00:00Z",
          "is_staff": false
        }
      ]
    }'
  ```

### 2. Retrieve Store Metrics
* **Endpoint**: `GET /stores/{store_id}/metrics`
* **Description**: Computes visitors, queue depths, zone dwells, and conversion rates.
* **Command**:
  ```bash
  curl http://localhost:8000/stores/STORE_1076/metrics
  ```

### 3. Retrieve Conversion Funnel
* **Endpoint**: `GET /stores/{store_id}/funnel`
* **Description**: Outputs visitor session counts and drop-off percentages for Entry → Zone Visit → Billing Queue → Purchase.
* **Command**:
  ```bash
  curl http://localhost:8000/stores/STORE_1076/funnel
  ```

### 4. Retrieve Store Heatmap
* **Endpoint**: `GET /stores/{store_id}/heatmap`
* **Description**: Generates visit counts, average dwells, and normalized traffic scores per zone.
* **Command**:
  ```bash
  curl http://localhost:8000/stores/STORE_1076/heatmap
  ```

### 5. Detect Active Store Anomalies
* **Endpoint**: `GET /stores/{store_id}/anomalies`
* **Description**: Runs real-time checks for queue spikes, conversion drops, dead zones, and stale feeds.
* **Command**:
  ```bash
  curl http://localhost:8000/stores/STORE_1076/anomalies
  ```

### 6. System Health Check
* **Endpoint**: `GET /health`
* **Description**: Validates database connections and reports stale feed lags across all stores.
* **Command**:
  ```bash
  curl http://localhost:8000/health
  ```

---

## Running the Automated Test Suite

We use `pytest` to execute all pipeline, metrics, and anomaly detection test cases. To run the tests and generate a coverage report, execute:

```bash
pytest tests/ -v --cov=app
```
*Note: The test suite uses isolated, temporary in-memory databases, keeping your local data files untouched.*
