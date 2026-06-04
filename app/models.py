from pydantic import BaseModel, Field, validator, root_validator
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

# Allowed event types
VALID_EVENT_TYPES = {
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY"
}

class IngestEvent(BaseModel):
    event_id: str = Field(..., description="UUID representation of the event ID")
    store_id: str = Field(..., min_length=1)
    camera_id: str = Field(..., min_length=1)
    visitor_id: str = Field(..., min_length=1)
    event_type: str = Field(...)
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = Field(default=0)
    is_staff: bool = Field(default=False)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("event_id")
    def validate_uuid_format(cls, v):
        try:
            # Validate that it is a valid UUID
            uuid.UUID(v)
            return v
        except ValueError:
            raise ValueError("event_id must be a valid UUID string")

    @validator("event_type")
    def validate_event_type(cls, v):
        upper_v = v.upper()
        if upper_v not in VALID_EVENT_TYPES:
            raise ValueError(f"event_type must be one of {VALID_EVENT_TYPES}")
        return upper_v

class IngestBatch(BaseModel):
    events: List[IngestEvent]

    @validator("events")
    def validate_batch_size(cls, v):
        if len(v) == 0:
            raise ValueError("Batch must contain at least 1 event")
        if len(v) > 500:
            raise ValueError("Batch size exceeds limit of 500 events")
        return v

# --- Response Models ---

class MetricsResponse(BaseModel):
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: Dict[str, float]
    queue_depth: int
    abandonment_rate: float

class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float

class FunnelResponse(BaseModel):
    funnel: List[FunnelStage]

class ZoneHeatmap(BaseModel):
    zone_id: str
    zone_name: str
    visit_count: int
    avg_dwell_ms: float
    normalized_score: float
    data_confidence: str  # "LOW" or "NORMAL"

class HeatmapResponse(BaseModel):
    heatmap: List[ZoneHeatmap]

class Anomaly(BaseModel):
    anomaly_type: str
    severity: str  # INFO, WARN, CRITICAL
    description: str
    suggested_action: str
    detected_at: datetime

class AnomaliesResponse(BaseModel):
    anomalies: List[Anomaly]

class HealthResponse(BaseModel):
    status: str  # OK, DEGRADED
    last_event_timestamp: Dict[str, Optional[str]]
    db_status: str  # connected, unavailable
    warnings: List[str]
