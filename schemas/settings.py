from pydantic import BaseModel, Field, field_validator
from typing import Optional
from enum import Enum
from datetime import datetime


class EdgeSettingsStatus(str, Enum):
    active = "active"
    paused = "paused"
    inactive = "inactive"


class CheapWindow(BaseModel):
    """Legacy global cheap-window fallback: {'start':'HH:MM','end':'HH:MM'}.
    The edge still prefers task.charge_windows; this is only a fallback.
    """
    start: str = Field(..., description="Start time HH:MM (local)")
    end: str = Field(..., description="End time HH:MM (local)")

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        v = v.strip()
        # Accept HH:MM or HH:MM:SS and normalize to HH:MM
        try:
            datetime.strptime(v, "%H:%M")
            return v
        except ValueError:
            dt = datetime.strptime(v, "%H:%M:%S")
            return dt.strftime("%H:%M")


class AutoBiasTrim(BaseModel):
    """Optional automatic bias trimming to keep grid ≈ target_w."""
    enable: bool = Field(default=False)
    target_w: int = Field(default=0, description="Desired steady grid power (W)")
    deadband_w: int = Field(default=30, ge=0, description="Tolerance before adjusting (W)")
    step_w: int = Field(default=10, ge=1, description="Single adjustment step (W)")


class EdgeSettingsConfig(BaseModel):
    # Existing
    frequency: float = Field(default=1.0, gt=0)
    posting_interval_seconds: int = Field(default=10, gt=0)

    deadband1: Optional[float] = None
    deadband2: Optional[float] = None
    deadband3: Optional[float] = None

    edge_status: EdgeSettingsStatus = Field(default=EdgeSettingsStatus.inactive)
    group_id: Optional[str] = None

    # --- New EMS fields (match backend semantics) ---

    # Export / metering / bias
    export_cap_w: int = Field(default=0, ge=0, description="Export cap (W); 0 = zero-export")
    external_meter: bool = Field(default=True, description="Enable external CT/smart meter")
    meter_bias_w: int = Field(default=-50, ge=-500, le=500, description="Initial grid bias (W)")

    

    # Import-AC charging behaviour
    import_charge_power_w: int = Field(default=3400, ge=0, description="Target grid import during charge windows (W)")
    target_soc_percent: float = Field(default=100.0, ge=0, le=100, description="SOC (%) to stop Import-AC")

    # Optional auto bias trim
    auto_bias_trim: Optional[AutoBiasTrim] = Field(default=None)
