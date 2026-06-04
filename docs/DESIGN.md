# System Design: Purplle Store Intelligence System

This document outlines the system architecture, component design, AI-assisted design decisions, and engineering trade-offs of the Store Intelligence System built for Purplle retail stores.

---

## 1. System Architecture

The Store Intelligence System operates as a modular, edge-to-cloud distributed pipeline designed to capture, process, and analyze visitor behavior telemetry inside retail stores. The architecture is structured into a four-stage pipeline:

```mermaid
graph TD
    subgraph Edge Layer (Retail Store)
        Cam[IP Camera Streams] --> Det[Detection Layer: YOLOv8 + ByteTrack + OSNet]
        Det --> Stream[Event Stream: pipeline/emit.py]
    end
    
    subgraph Cloud/Server Layer
        Stream --> |HTTP POST Batches| API[Intelligence API: FastAPI]
        DB[(Storage: SQLite)] <--> API
        API --> Dash[Dashboard / Metrics Consuming Applications]
    end
```

### Component Breakdown:
1. **Detection Layer (Edge)**: Captures RTSP video feeds from physical retail cameras, extracts frames, detects customers and staff, tracks their coordinates, and maintains identity states (Re-ID).
2. **Event Stream (Edge)**: Buffer events on the edge nodes, structure them into standardized JSON formats, compute dwell times, and forward telemetry batches to the centralized API.
3. **Intelligence API (Cloud/Server)**: Receives, validates, and deduplicates event batches, handles visitor session state transitions, and serves analytical endpoints.
4. **Dashboard (Client)**: Queries the API metrics to visualize traffic funnels, heatmaps, queue performance, and active anomalies.

---

## 2. Component Design

### Detection Pipeline Choices:
- **YOLOv8 (Object Detection)**: Selected for its state-of-the-art inference speed and accuracy on edge computing devices. YOLOv8 is capable of performing multi-class detection (person, objects) at >30 FPS on embedded GPU systems like NVIDIA Jetson.
- **ByteTrack (Multi-Object Tracking)**: Unlike traditional trackers that discard low-score bounding boxes, ByteTrack associates almost every detection box (even low-confidence occluded boxes), drastically reducing track fragmentation in dense retail environments.
- **OSNet (Person Re-Identification)**: OSNet (Omni-Scale Network) was chosen because it extracts omni-scale features (both global body structures and local details like clothing patterns). It is lightweight and highly robust against lighting changes across different camera feeds.

### Event Schema Decisions:
- **UUID `event_id`**: Network partitions can cause the edge node to retry posting batches. Enforcing unique UUIDs as the primary key in the SQLite database allows the API to perform idempotent writes (`INSERT OR IGNORE`), ensuring duplicate delivery attempts have zero side-effects.
- **Boolean `is_staff` Flag**: Rather than filtering out store employees at the edge (which destroys valuable telemetry), employees are marked with `is_staff=True`. This design allows the store manager to compute staff-to-customer ratios and analyze staff placement optimization, while easily excluding them from customer conversion metrics.
- **`dwell_ms` in Milliseconds**: Storing duration in integer milliseconds prevents floating-point precision errors (common in floating-point seconds representations) and provides precise, atomic metrics for micro-dwell interactions at cosmetics shelves.

### API Layer & Real-Time Metrics:
- **FastAPI**: Selected for its asynchronous capabilities, fast execution times (built on Starlette and Uvicorn), and automatic Pydantic schema validation.
- **Real-time Aggregates**: Rather than relying on cron-based batch aggregations or cache layers (which lag behind live store behaviors), metrics are computed on-demand directly via SQLite query filters. Utilizing database indexes on `store_id`, `visitor_id`, and `timestamp` guarantees fast API query execution under typical retail volumes.

### Storage & Migration Path:
- **SQLite**: SQLite was chosen for local containerization simplicity. It is serverless, stores data in a single file, and avoids database daemon startup dependencies.
- **PostgreSQL Migration Path**:
  - The tables are designed using clean, standardized SQL structures.
  - To scale out to multiple stores, the application can transition to PostgreSQL by replacing the `sqlite3` connection manager in `app/database.py` with `psycopg2` or `asyncpg` and configuring a PostgreSQL connection pool.
  - The `JSON` serialized metadata field can be mapped directly to PostgreSQL’s native `JSONB` data type to support nested index searches.

---

## 3. AI-Assisted Decisions

During the architectural phase, LLM feedback helped refine the core logic:

1. **Session Sequence vs. Wall-Clock Ordering**:
   - *AI Suggestion*: The AI suggested implementing an auto-incrementing integer ordinal (`session_seq`) tracked per visitor rather than relying solely on database wall-clock timestamps for chronological sequence.
   - *Rationale*: Due to network latency, events from edge cameras covering different zones can arrive at the API out of order. An ordinal sequence computed sequentially by the emitter ensures that step-by-step visitor journeys are reconstructed accurately. I agreed and integrated this.
2. **Refined Queue Spike Threshold**:
   - *AI Suggestion*: The AI originally proposed triggering a `BILLING_QUEUE_SPIKE` warning at 3 people.
   - *Override*: After inspecting Purplle’s historical store feeds showing typical checkout line lengths frequently peaking at 3–4 during standard operating hours, I overrode the limit to 5 to prevent false-positive alarms from spamming staff.
3. **Re-ID via Spatio-Temporal Trajectory**:
   - *AI Suggestion*: The AI initially suggested a simple spatial bounding box distance threshold for local person Re-ID.
   - *Override*: Pure spatial overlap fails when multiple customers enter close together through the same door. I overrode the design to combine trajectory direction vectors with a maximum 5-second time-gap window to uniquely bind track IDs during cross-camera handovers.

---

## 4. Trade-offs and Limitations

- **Occlusion Handling**: In dense cosmetics aisles, shoppers frequently block camera views. This can split a single visitor's track into separate IDs, leading to minor over-counts of unique visitors.
- **SQLite Scalability**: Because SQLite locks the entire database file during writes, it struggles with concurrent write loads. If thousands of edge cameras stream events simultaneously, it will hit write timeouts, necessitating PostgreSQL.
- **Real-time API Overhead**: Computing metrics in real-time from SQLite raw event records scales linearly with data size. As history grows, raw queries will slow down, requiring partitioning or materialized rolling aggregate tables.
