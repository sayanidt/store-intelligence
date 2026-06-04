import time
import uuid
import re
import sys
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.database import init_db, load_pos_csv, load_sample_events
from app.ingestion import router as ingestion_router
from app.metrics import router as metrics_router
from app.funnel import router as funnel_router
from app.heatmap import router as heatmap_router
from app.anomalies import router as anomalies_router
from app.health import router as health_router

# Setup standard logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("store_intelligence")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan manager handling app startup and shutdown logic.
    On startup: Initializes SQLite schemas, imports the POS CSV transactions, and loads sample events.
    """
    logger.info("Starting Store Intelligence Application...")
    try:
        init_db()
        load_pos_csv()
        load_sample_events()
    except Exception as e:
        logger.critical(f"Failed during application startup sequence: {e}", exc_info=True)
    yield
    logger.info("Shutting down Store Intelligence Application...")

# Initialize FastAPI App
app = FastAPI(
    title="Store Intelligence API",
    description="Real-time physical retail store visitor tracking and metrics API",
    version="1.0.0",
    lifespan=lifespan
)

# --- Logging Middleware ---
class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = str(uuid.uuid4())
        # Inject trace ID into request state so it is accessible elsewhere
        request.state.trace_id = trace_id
        
        start_time = time.perf_counter()
        
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            # Let the exception propagate to exception handlers, which generate response.
            # But we record status_code as 500 for logging if it completely bypasses.
            status_code = 500
            raise e
        finally:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            
            # Retrieve store_id if set dynamically during ingestion, or extract from path
            store_id = getattr(request.state, "store_id", None)
            if not store_id:
                # Regex match for /stores/{store_id}/...
                match = re.search(r"/stores/([^/]+)", request.url.path)
                store_id = match.group(1) if match else "unknown"

            event_count = getattr(request.state, "event_count", None)
            
            # Build structured log payload
            log_payload = {
                "trace_id": trace_id,
                "store_id": store_id,
                "endpoint": request.url.path,
                "latency_ms": latency_ms,
                "status_code": status_code
            }
            if event_count is not None:
                log_payload["event_count"] = event_count

            # Output to stdout as structured JSON string
            print(json.dumps(log_payload), file=sys.stdout, flush=True)

        # Append trace ID header to responses
        response.headers["X-Trace-ID"] = trace_id
        return response

app.add_middleware(StructuredLoggingMiddleware)

# --- Exception Handlers ---
@app.exception_handler(sqlite3.Error)
async def sqlite_exception_handler(request: Request, exc: sqlite3.Error):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(f"Database error [trace_id: {trace_id}]: {exc}", exc_info=True)
    return JSONResponse(
        status_code=503,
        content={
            "error": "Database unavailable",
            "detail": "The database connection failed or an operations timeout occurred."
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(f"Internal system error [trace_id: {trace_id}]: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": f"An unexpected error occurred. Reference trace ID: {trace_id}."
        }
    )

# --- Register Routers ---
app.include_router(ingestion_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/docs")

