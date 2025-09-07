from pydantic import BaseModel
from typing import Optional, Union
from enum import Enum

class BatteryMode(str, Enum):
    active = "active"
    idle = "idle"
    charging = "charging"
    forced_charging = "forced_charging"
    
class EdgeBatteryConfig(BaseModel):
    consus_id: str
    capacity: Optional[float] = None
    reserve_soc: Optional[float] = None
    max_soc: Optional[float] = None
    max_discharge_w: Optional[float] = None
    max_charge_w: Optional[float] = None
    max_ramp_rate_w_per_s: Optional[float] = None
    battery_mode: Optional[BatteryMode] = None
    MODBUS_IP: Optional[str] = None
    MODBUS_PORT: Optional[Union[int, str]] = 15002
    pv_enabled: Optional[bool] = False

    control_mode: Optional[str] = None
