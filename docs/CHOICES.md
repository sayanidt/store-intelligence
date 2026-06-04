# Architectural Choices & Decision Logs: Store Intelligence API

This document records the major design choices and technical tradeoffs evaluated during the development of the Purplle Store Intelligence API. For each decision, we cover the options considered, the AI suggestion, and the final chosen path with its rationale.

---

## Decision 1: Detection Model & Tracking Framework Selection

### Options Considered:
1. **YOLOv8n (Nano)**: The fastest model in the YOLOv8 family, but exhibits poor bounding-box precision and fails to detect subjects under low lighting or partial occlusion.
2. **YOLOv8m (Medium)**: A balanced model offering high bounding-box precision and robust detection while maintaining low computational requirements.
3. **RT-DETR (Real-Time DEtection TRansformer)**: A modern transformer-based object detector that provides higher detection accuracy, particularly on overlapping objects, but has significant compute requirements.
4. **MediaPipe**: A CPU-optimized tracking model that is highly efficient for single subjects but fails in dense, crowded retail spaces.

### What the AI Suggested:
The AI suggested utilizing **RT-DETR** because its transformer-based attention mechanism is significantly better at resolving overlaps and identifying occluded shoppers in cluttered cosmetic store displays.

### What Was Chosen and Why:
We chose **YOLOv8m combined with ByteTrack** for object detection and multi-object tracking.
- **Performance Fit**: Purplle’s store CCTV cameras capture footage at 15 FPS. YOLOv8m runs comfortably at 15 FPS on a standard edge GPU (e.g., NVIDIA Jetson), matching the input frame rate exactly without frame-dropping.
- **Occlusion Resilience**: ByteTrack is optimized to associate low-confidence detections with existing track histories. This resolves occlusions (e.g., a shopper blocked by a display stand) better than relying solely on a heavier detector like RT-DETR, which would add latency and require expensive GPU hardware.
- **Staff Tracking Optimization**: Instead of running a large, resource-heavy Vision-Language Model (VLM) to distinguish staff members from shoppers, we implemented a YOLOv8 person detector paired with a custom classifier that identifies the bright magenta uniform worn by Purplle employees. This heuristic eliminates the 200ms+ latency of a VLM, preventing pipeline bottlenecks.

---

## Decision 2: Event Schema Design

### Options Considered:
1. **Flat Schema**: Storing all possible parameters (e.g., `queue_depth`, `sku_zone`, `session_seq`) as flat, top-level nullable columns on a single table.
2. **Separate Tables per Event Type**: Creating specialized tables for each event type (e.g., a `billing_queue_events` table, a `zone_visit_events` table).
3. **Unified Table with Nested JSON Metadata**: Storing base event parameters in standard columns and wrapping event-type-specific attributes in a flexible JSON metadata column.

### What the AI Suggested:
The AI suggested **Option 2 (Separate Tables per Event Type)** to maintain strong static schemas, enforce table-level database constraints, and optimize space.

### What Was Chosen and Why:
We chose **Option 3 (Unified events table with a JSON metadata column)**.
- **Reduced Join Complexity**: Over 80% of the database fields—such as `event_id`, `store_id`, `visitor_id`, `timestamp`, and `is_staff`—are shared by all event types. Splitting these into separate tables would require complex, multi-way joins to run funnel metrics or chronological tracking queries.
- **Flexibility**: The JSON metadata column allows the schema to adapt to future event telemetry without requiring database migrations (e.g., adding `item_scanned` for shelf zones or `payment_method` for cash registers). It holds specific properties (like `queue_depth` for billing joins and `sku_zone` for shelf dwells) cleanly.

---

## Decision 3: API Storage Engine

### Options Considered:
1. **PostgreSQL**: A robust, standard relational database offering great query speed and transactional security.
2. **SQLite**: A serverless, file-based relational engine requiring zero external configuration.
3. **Redis + PostgreSQL**: Redis serving as an in-memory cache for live queue states, backed by PostgreSQL for persistence.
4. **TimescaleDB**: A PostgreSQL extension optimized for time-series analytics and high-volume insertions.

### What the AI Suggested:
The AI suggested **TimescaleDB** due to the time-series nature of retail tracking telemetry, where events are continually appended.

### What Was Chosen and Why:
We chose **SQLite** for the implementation to prioritize ease of deployment and transportability.
- **Zero Infrastructure overhead**: SQLite stores the database in a single local file. This allows developers to run `docker compose up` without starting, configuring, and waiting for an external database daemon, which is ideal for sandboxed testing.
- **Scalability Migration Path**: As noted in `docs/DESIGN.md`, the SQL statements are kept standard. The production migration path simply requires changing the SQLAlchemy connection URI string to point to PostgreSQL.
- **Future Scale**: At a scale of 40 live stores streaming events at 15 FPS, a time-series optimized database like TimescaleDB would be the ideal target to handle high concurrent writes and compile historical aggregates efficiently.
