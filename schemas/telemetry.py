from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime

class TelemetryPayload(BaseModel):
    consus_id: str
    mode: str
    source_type: str = Field(default="modbus")
    timestamp: Optional[datetime] = None  # Will accept ISO 8601 strings or datetime
    payload: Any  # Can be another BaseModel or raw dict/float/etc
