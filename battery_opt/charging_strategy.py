import logging
from datetime import datetime
from core.edge_state import EDGE_STATE
from schemas.edge_state import EdgeBatteryConfig

logger = logging.getLogger(__name__)

class ChargingStrategy:
    def __init__(self, consus_id: str):
        self.consus_id = consus_id
        self.unit = EDGE_STATE.units[consus_id]
        self.cfg: EdgeBatteryConfig = EDGE_STATE.battery_configs.get(consus_id, EdgeBatteryConfig(consus_id=consus_id))
        self.settings = EDGE_STATE.settings

    def execute(self, mode: str, timestamp: datetime) -> int:
        if mode == "forced_charging":
            return self._forced_charge()
        elif mode == "charging":
            task = EDGE_STATE.tasks.get(self.consus_id, {})
            return self._scheduled_charge(task, timestamp)
        return 0

    def _forced_charge(self) -> int:
        soc = self.unit.current_soc
        max_soc = (self.cfg.max_soc or 100) / 100
        if soc >= max_soc:
            logger.info(f"[{self.consus_id}] Skipping forced charge — SoC at max")
            return 0

        charge_power = min(self.cfg.max_charge_w or 2000, 2000)
        logger.debug(f"[{self.consus_id}] Forced charging at {charge_power}W")
        return int(charge_power)

    def _scheduled_charge(self, task: dict, timestamp: datetime) -> int:
        try:
            soc = self.unit.current_soc
            capacity = self.cfg.capacity or 0
            max_soc = (self.cfg.max_soc or 100) / 100
            freq = self.settings.get("frequency", 1)

            if soc >= max_soc or capacity <= 0:
                return 0

            end_str = task.get("charge_window_end")
            if not end_str:
                logger.warning(f"[{self.consus_id}] Task missing charge_window_end")
                return 0

            now = timestamp.time()
            end = datetime.strptime(end_str, "%H:%M:%S").time()
            seconds_remaining = (
                datetime.combine(timestamp.date(), end) -
                datetime.combine(timestamp.date(), now)
            ).total_seconds()

            if seconds_remaining <= 0:
                return 0

            hours_remaining = seconds_remaining / 3600
            wh_needed = (max_soc - soc) * capacity * 1000
            required_w = wh_needed / hours_remaining

            dispatch = min(required_w, self.cfg.max_charge_w or required_w)
            logger.debug(f"[{self.consus_id}] Scheduled charge dispatch: {int(dispatch)}W")
            return int(dispatch)

        except Exception as e:
            logger.exception(f"[{self.consus_id}] Scheduled charging error: {e}")
            return 0
