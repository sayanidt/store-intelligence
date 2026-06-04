import os
import sqlite3
import csv
import logging
from datetime import datetime

logger = logging.getLogger("store_intelligence")

DB_PATH = os.getenv("DATABASE_PATH", "/data/store_intelligence.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_db_connection():
    """
    Establish a connection to the SQLite database.
    Enables WAL mode and dictionary-like row factory.
    """
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable Write-Ahead Logging (WAL) for better concurrent performance
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Failed to connect to SQLite database: {e}")
        raise e

def init_db():
    """
    Creates necessary SQLite tables if they do not exist.
    """
    conn = get_db_connection()
    try:
        with conn:
            # Events table (event_id is primary key for idempotency)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    visitor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    zone_id TEXT,
                    dwell_ms INTEGER DEFAULT 0,
                    is_staff INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 1.0,
                    metadata TEXT
                )
            """)
            
            # Sessions table derived from ENTRY/EXIT events
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    visitor_id TEXT NOT NULL,
                    store_id TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT
                )
            """)

            # POS transactions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pos_transactions (
                    order_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    order_date TEXT NOT NULL,
                    order_time TEXT NOT NULL,
                    total_amount REAL NOT NULL
                )
            """)
        
        # Indexes for optimization
        with conn:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_store_id ON events (store_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_visitor_id ON events (visitor_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_visitor_store ON sessions (visitor_id, store_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_store ON pos_transactions (store_id)")

        logger.info("Database tables initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize database: {e}")
        raise e
    finally:
        conn.close()

def load_pos_csv():
    """
    Loads POS transaction data from the CSV file specified by POS_CSV_PATH env variable.
    Inserts data idempotently using INSERT OR IGNORE.
    """
    csv_path = os.getenv("POS_CSV_PATH")
    if not csv_path:
        logger.warning("POS_CSV_PATH environment variable not set. Skipping POS transaction import.")
        return

    if not os.path.exists(csv_path):
        logger.error(f"POS CSV file not found at: {csv_path}")
        return

    logger.info(f"Loading POS transactions from {csv_path}...")
    conn = get_db_connection()
    try:
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            
            # Standardize headers (strip spaces and lowercase)
            headers = {h.strip().lower(): h for h in reader.fieldnames} if reader.fieldnames else {}
            
            # Identify columns
            order_id_col = headers.get("order_id") or headers.get("orderid")
            store_id_col = headers.get("store_id") or headers.get("storeid")
            order_date_col = headers.get("order_date") or headers.get("orderdate") or headers.get("date")
            order_time_col = headers.get("order_time") or headers.get("ordertime") or headers.get("time")
            total_amount_col = headers.get("total_amount") or headers.get("totalamount") or headers.get("amount")

            if not all([order_id_col, store_id_col, order_date_col, order_time_col, total_amount_col]):
                logger.error(f"POS CSV is missing required columns. Identified headers: {list(headers.keys())}")
                return

            inserted_count = 0
            with conn:
                for row in reader:
                    order_id = row[order_id_col]
                    store_id = row[store_id_col]
                    order_date = row[order_date_col]
                    order_time = row[order_time_col]
                    try:
                        total_amount = float(row[total_amount_col])
                    except (ValueError, TypeError):
                        total_amount = 0.0

                    conn.execute("""
                        INSERT OR IGNORE INTO pos_transactions (
                            order_id, store_id, order_date, order_time, total_amount
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (order_id, store_id, order_date, order_time, total_amount))
                    inserted_count += 1
            
            logger.info(f"Loaded {inserted_count} transaction rows from CSV (using INSERT OR IGNORE).")
    except Exception as e:
        logger.error(f"Error loading POS CSV: {e}")
        # Don't fail completely, log error
    finally:
        conn.close()


def load_sample_events():
    """
    Checks if the events table is empty. If empty, reads data/sample_events.jsonl,
    standardizes the events (similar to pipeline/emit.py), and inserts them.
    Also populates the sessions table.
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM events")
        count = cursor.fetchone()[0]
        if count > 0:
            logger.info("Events table already has data. Skipping sample events import.")
            return
    except sqlite3.Error as e:
        logger.error(f"Failed to check events table count: {e}")
        conn.close()
        return

    import json
    import uuid
    from datetime import datetime

    paths_to_try = [
        os.getenv("EVENTS_JSONL_PATH", "/data/sample_events.jsonl"),
        "data/sample_events.jsonl",
        "../data/sample_events.jsonl",
        "/workspace/data/sample_events.jsonl"
    ]
    
    jsonl_path = None
    for p in paths_to_try:
        if os.path.exists(p):
            jsonl_path = p
            break

    if not jsonl_path:
        logger.warning("sample_events.jsonl not found in expected paths. Skipping sample events import.")
        conn.close()
        return

    logger.info(f"Loading sample events from {jsonl_path}...")
    
    event_type_mapping = {
        "entry": "ENTRY",
        "exit": "EXIT",
        "zone_entered": "ZONE_ENTER",
        "zone_exited": "ZONE_EXIT",
        "queue_completed": "BILLING_QUEUE_JOIN",
        "queue_abandoned": "BILLING_QUEUE_ABANDON"
    }

    def parse_iso_timestamp(ts_str):
        if not ts_str:
            return datetime.utcnow()
        cleaned = ts_str.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                try:
                    return datetime.strptime(cleaned, fmt)
                except ValueError:
                    continue
            return datetime.utcnow()

    raw_events = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    raw_events.append(json.loads(line))
    except Exception as e:
        logger.error(f"Error reading sample events file: {e}")
        conn.close()
        return

    parsed_events = []
    for item in raw_events:
        raw_ts = item.get("event_timestamp") or item.get("event_time") or item.get("queue_join_ts")
        dt = parse_iso_timestamp(raw_ts)
        parsed_events.append((dt, item))
    
    parsed_events.sort(key=lambda x: x[0])

    visitor_seqs = {}
    active_zones = {}
    exited_visitors = set()
    
    events_inserted = 0
    try:
        with conn:
            for dt, raw in parsed_events:
                raw_store = raw.get("store_code") or raw.get("store_id") or "STORE_1076"
                store_id = str(raw_store).strip().upper()

                raw_visitor = raw.get("id_token") or raw.get("track_id") or "unknown"
                visitor_id = str(raw_visitor).strip()
                if not visitor_id.startswith("VIS_"):
                    visitor_id = f"VIS_{visitor_id}"

                raw_type = raw.get("event_type", "entry")
                if raw_type.lower() == "entry" and visitor_id in exited_visitors:
                    event_type = "REENTRY"
                else:
                    event_type = event_type_mapping.get(raw_type.lower(), raw_type.upper())

                if event_type == "EXIT":
                    exited_visitors.add(visitor_id)

                camera_id = raw.get("camera_id") or "CAM_UNKNOWN"
                zone_id = raw.get("zone_id")

                dwell_ms = 0
                if event_type == "ZONE_ENTER" and zone_id:
                    active_zones[(visitor_id, zone_id)] = dt
                elif event_type == "ZONE_EXIT" and zone_id:
                    entry_ts = active_zones.pop((visitor_id, zone_id), None)
                    if entry_ts:
                        dwell_ms = int((dt - entry_ts).total_seconds() * 1000)

                visitor_seqs[visitor_id] = visitor_seqs.get(visitor_id, 0) + 1
                session_seq = visitor_seqs[visitor_id]

                queue_depth = raw.get("queue_position_at_join")
                sku_zone = raw.get("zone_name")

                metadata = {"session_seq": session_seq}
                if queue_depth is not None:
                    metadata["queue_depth"] = int(queue_depth)
                if sku_zone is not None:
                    metadata["sku_zone"] = sku_zone

                is_staff = bool(raw.get("is_staff", False))
                event_id = str(uuid.uuid4())
                meta_str = json.dumps(metadata)
                dt_str = dt.isoformat() + "Z"

                conn.execute("""
                    INSERT OR IGNORE INTO events (
                        event_id, store_id, camera_id, visitor_id, event_type,
                        timestamp, zone_id, dwell_ms, is_staff, confidence, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event_id, store_id, camera_id, visitor_id, event_type,
                    dt_str, zone_id, dwell_ms, 1 if is_staff else 0, 0.85, meta_str
                ))
                events_inserted += 1

                if event_type in ("ENTRY", "EXIT", "REENTRY"):
                    res = conn.execute("""
                        SELECT session_id, end_time FROM sessions
                        WHERE visitor_id = ? AND store_id = ?
                        ORDER BY start_time DESC LIMIT 1
                    """, (visitor_id, store_id)).fetchone()

                    if event_type in ("ENTRY", "REENTRY"):
                        if not res or res["end_time"] is not None:
                            sess_id = str(uuid.uuid4())
                            conn.execute("""
                                INSERT INTO sessions (session_id, visitor_id, store_id, start_time, end_time)
                                VALUES (?, ?, ?, ?, NULL)
                            """, (sess_id, visitor_id, store_id, dt_str))
                    elif event_type == "EXIT":
                        if res and res["end_time"] is None:
                            conn.execute("""
                                UPDATE sessions SET end_time = ?
                                WHERE session_id = ?
                            """, (dt_str, res["session_id"]))
                        else:
                            sess_id = str(uuid.uuid4())
                            conn.execute("""
                                INSERT INTO sessions (session_id, visitor_id, store_id, start_time, end_time)
                                VALUES (?, ?, ?, ?, ?)
                            """, (sess_id, visitor_id, store_id, dt_str, dt_str))
        logger.info(f"Successfully loaded and derived {events_inserted} sample events.")
    except Exception as e:
        logger.error(f"Error inserting sample events: {e}")
    finally:
        conn.close()

