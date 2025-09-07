from typing import Optional, List, Union, Literal
from pydantic import BaseModel


Severity = Literal["CRITICAL", "WARNING", "INFO"]
State = Literal["ACTIVE", "RESOLVED"]


class RecentTelemetry(BaseModel):
    ts: str  # RFC3339 UTC (Z)
    soc: Optional[float] = None
    grid_w: Optional[int] = None
    pv_w: Optional[int] = None
    mode: Optional[Union[int, str]] = None
    bias_w: Optional[int] = None


class AlertContext(BaseModel):
    mode: Optional[Union[int, str]] = None
    soc: Optional[float] = None
    grid_w: Optional[int] = None
    pv_w: Optional[int] = None
    bias_w: Optional[int] = None


class AlertEvent(BaseModel):
    site_id: str
    ts: str  # RFC3339 UTC (Z)
    severity: Severity
    code: str
    state: State
    event_id: str
    count: int
    heartbeat: bool = False
    context: AlertContext
    recent_telemetry: Optional[List[RecentTelemetry]] = None
    source: Optional[str] = None

__all__ = ["AlertEvent", "AlertContext", "RecentTelemetry", "Severity", "State"]
