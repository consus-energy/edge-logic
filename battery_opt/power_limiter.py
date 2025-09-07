# battery_opt/power_limiter.py

import logging
from core.edge_state import EDGE_STATE
from schemas.edge_state import EdgeBatteryConfig

logger = logging.getLogger(__name__)

class PowerLimiter:
    def __init__(self, consus_id: str):
        self.consus_id = consus_id
        self.last_dispatch = 0
        self._last_args = None
        self._last_output = 0

    def compute(self, demand_w: float, soc: float, cfg: EdgeBatteryConfig) -> int:
        try:
            timestep_sec = EDGE_STATE.settings.get("frequency", 1)
            timestep_hr = timestep_sec / 3600.0
            capacity = cfg.capacity or 0
            reserve_soc = (cfg.reserve_soc or 10) / 100
            max_soc = (cfg.max_soc or 100) / 100

            if capacity <= 0:
                logger.warning(f"[{self.consus_id}] Capacity is zero or undefined.")
                return 0

            args = (soc, demand_w)
            if self._last_args == args:
                return self._last_output

            if soc <= reserve_soc + 0.001 and demand_w > 0:
                return 0
            if soc >= max_soc - 0.001 and demand_w < 0:
                return 0

            if abs(demand_w - self.last_dispatch) < 1:
                return self.last_dispatch

            safe_power = 0

            if demand_w > 0 and soc > reserve_soc:
                avail_wh = (soc - reserve_soc) * capacity * 1000
                max_discharge = avail_wh / timestep_hr
                if cfg.max_discharge_w is not None:
                    max_discharge = min(max_discharge, cfg.max_discharge_w)
                safe_power = min(demand_w, max_discharge)

            elif demand_w < 0 and soc < max_soc:
                room_wh = (max_soc - soc) * capacity * 1000
                max_charge = room_wh / timestep_hr
                if cfg.max_charge_w is not None:
                    max_charge = min(max_charge, cfg.max_charge_w)
                safe_power = -min(abs(demand_w), max_charge)

            # Ramp rate limiting
            if cfg.max_ramp_rate_w_per_s is not None:
                max_delta = cfg.max_ramp_rate_w_per_s * timestep_sec
                delta = safe_power - self.last_dispatch
                if abs(delta) > max_delta:
                    safe_power = self.last_dispatch + max_delta * (1 if delta > 0 else -1)
                    logger.debug(f"[{self.consus_id}] Ramp-limited: Δ={delta}W capped to {safe_power}W")

            result = int(safe_power)
            self._last_args = args
            self._last_output = result
            self.last_dispatch = result
            return result

        except Exception as e:
            logger.exception(f"[{self.consus_id}] PowerLimiter error: {e}")
            return 0
