# battery_opt/controller.py

import logging
from datetime import datetime, timezone
from core.edge_state import EDGE_STATE
from schemas.telemetry import TelemetryPayload
from battery_opt.task_evaluator import TaskEvaluator
from battery_opt.ems_manager import EMSManager
from battery_opt.safety_check import SafetyCheck, INTENT_FAULT_SAFE

logger = logging.getLogger(__name__)

class BatteryController:
    def __init__(self, unit, consus_id: str, health_monitor: SafetyCheck | None = None):
        self.unit = unit
        self.consus_id = consus_id
        self.task_evaluator = TaskEvaluator(consus_id)
        self.ems = EMSManager(consus_id)
        self.health_monitor = health_monitor
        self._fault_safe = False
        logger.info(f"BatteryController initialized for consus_id: {consus_id}")

    def get_live_config(self):
        cfg = EDGE_STATE.battery_configs.get(self.consus_id, {})
        settings = EDGE_STATE.settings
        task = EDGE_STATE.get_task(self.consus_id)
        return cfg, settings, task

    def _handle_mode(self, mode: str, timestamp: datetime, cfg, settings) -> TelemetryPayload:
        """Simplified: rely on inverter EMS (Auto vs Import-AC). Only dispatch 0 when idle."""
        try:
            telemetry = self.unit.read_telemetry()
            meter_p = telemetry.get("meter_total_active_power") or telemetry.get("grid_power") or 0
            pv_power = telemetry.get("pv_power_total_ac_included") or telemetry.get("pv_power_total") or 0

            if mode == "idle":
                # Ensure no stale manual dispatch remains (send 0 once)
                self.unit.dispatch(0)
            else:
                # Apply EMS logic (handles cheap window, SOC target, bias trim)
                self.ems.apply(self.unit, self.unit.current_soc, meter_p, pv_power)

            return TelemetryPayload(
                consus_id=self.consus_id,
                mode=mode,
                timestamp=timestamp,
                payload=telemetry
            )
        except Exception as e:
            logger.exception(f"{mode.title()} error for {self.consus_id}")
            return TelemetryPayload(
                consus_id=self.consus_id,
                mode=mode,
                timestamp=timestamp,
                payload=str(e)
            )

    def run_once(self) -> TelemetryPayload:
        try:
            cfg, settings, _ = self.get_live_config()


            
            # TODO: integrate watchdog to detect stale telemetry and fallback to AUTO_MODE
            timestamp = datetime.now(timezone.utc)
            # Drain health intents if monitor present
            if self.health_monitor:
                intent = self.health_monitor.poll_intent()
                
                while intent:
                    if intent.get("intent") == INTENT_FAULT_SAFE:
                        if not self._fault_safe:
                            logger.warning(f"[{self.consus_id}] Transition -> FAULT_SAFE due to health intent")
                        self._fault_safe = True
                    intent = self.health_monitor.poll_intent()
            
            mode = self.task_evaluator.determine_mode()
            if self._fault_safe:
                mode = "idle"  # force safe behavior
            return self._handle_mode(mode, timestamp, cfg, settings)
        
        except Exception as e:
            logger.exception(f"Unhandled error in controller for {self.consus_id}")
            return TelemetryPayload(
                consus_id=self.consus_id,
                mode="error",
                timestamp=datetime.now(timezone.utc),
                payload=str(e)
            )
