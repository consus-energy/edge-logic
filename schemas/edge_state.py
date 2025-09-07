
from pydantic import BaseModel
from typing import Dict, Any, List

from schemas.comms_settings import CommsSettings
from schemas.settings import EdgeSettingsConfig 
from schemas.task import EdgeTaskConfig
from schemas.battery_config import EdgeBatteryConfig


class RegisterMap(BaseModel):
    read_registers: List[dict]  
    write_registers: List[dict]
    
class EdgeStatePayload(BaseModel):
    batteries: Dict[str, EdgeBatteryConfig]
    tasks: Dict[str, EdgeTaskConfig]
    settings: EdgeSettingsConfig
    comms_settings: CommsSettings
    register_map: RegisterMap
    
