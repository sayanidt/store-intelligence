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
