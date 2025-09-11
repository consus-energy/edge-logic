# battery_opt/task_evaluator.py

import logging
from datetime import datetime
from core.edge_state import EDGE_STATE

logger = logging.getLogger(__name__)

class TaskEvaluator:
    def __init__(self, consus_id: str):
        self.consus_id = consus_id

    def determine_mode(self) -> str:
        """Return one of: idle, active, forced_charging.
        Legacy 'charging'/'balancing' collapsed into a single 'active' state;
        EMSManager decides Import-AC vs Auto internally.
        """
        try:
            edge_status = EDGE_STATE.settings.get("edge_status", "active").lower()
            if edge_status != "active":
                return "idle"

            battery_cfg = EDGE_STATE.battery_configs.get(self.consus_id, {})
            battery_mode = str(battery_cfg.get("battery_mode", "idle"))
            if battery_mode == "idle":
                return "idle"
            if battery_mode == "forced_charging":
                return "forced_charging"
            
            return "active"
            # TASK STATUS REMOVED ONLY BASED ON BATTERY/ EDGE STATE

            #task = EDGE_STATE.tasks.get(self.consus_id, {})
            #status = task.get("status")
            #if status in {"expired", "paused", "initialised", None}:
                # Treat as idle unless explicitly forced
                #return "idle"

            # Active task → active mode (EMS decides exact inverter behaviour)
            #if status == "active":
                #return "active"

            # Fallback
            
        except Exception as e:
            logger.exception(f"[{self.consus_id}] TaskEvaluator error: {e}")
            return "idle"
