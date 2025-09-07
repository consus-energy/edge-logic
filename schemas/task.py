# schemas/task_config.py (in edge codebase)
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, time
from enum import Enum

class EdgeTaskStatus(str, Enum):
    initialised = "initialised"
    active = "active"
    paused = "paused"
    expired = "expired" 

class EdgeTaskType(str, Enum):
    static = "static"
    dynamic = "dynamic"

class EdgeTaskConfig(BaseModel):
    description: str
    task_code: str
    assignment_group_id: str
    task_type: Optional[EdgeTaskType] = None


    charge_window_start: Optional[time] = None
    charge_window_end: Optional[time] = None
    balance_window_start: Optional[time] = None
    balance_window_end: Optional[time] = None

    #dynamic
    service_day: date 
    charge_windows: Optional[list[list[time]]] = None
    balance_windows: Optional[list[list[time]]] = None
    revision: Optional[int] = None  

    max_export_limit: Optional[float] = None
    max_import_limit: Optional[float] = None
    num_cycles: Optional[int] = None
    override: Optional[bool] = False

    status: Optional[EdgeTaskStatus] = EdgeTaskStatus.initialised
