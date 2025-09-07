"""Health & Alerts Monitor (SafetyCheck)

Periodic scanner (1–2 Hz typical) that:
  * Polls critical health registers (EMS status, BMS alarms/warnings, ARC fault, meter comms flags, mode displays)
  * Maintains per-alert state machines (CLEAR -> ACTIVE -> RESOLVED)
  * Debounces activation & clearance, rate-limits duplicate sends
  * Batches WARNING/INFO while sending CRITICAL immediately
  * Emits intents (e.g., FAULT_SAFE) onto a local in-process queue consumed by controller

Placeholder notes:
  - Actual register names/addresses for EMS check, BMS alarms, ARC, meter comms must be added to register_map.json
  - Posting to backend currently logs JSON; replace _post_batch / _post_immediate with real HTTP implementation
"""

import logging
import time
import threading
from collections import deque
from datetime import datetime, timezone
import uuid

from core.edge_state import EDGE_STATE
from utils.backend_utils import post_health_alerts
from schemas.alerts import AlertEvent, AlertContext, RecentTelemetry

logger = logging.getLogger(__name__)

CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"

STATE_CLEAR = "CLEAR"
STATE_ACTIVE = "ACTIVE"
STATE_RESOLVED = "RESOLVED"

# Default debounce / timing parameters
DEBOUNCE_ACTIVATE_SEC = 5
DEBOUNCE_CLEAR_POLLS = 10   # consecutive clears to mark RESOLVED
WARNING_BATCH_SEC = 45
ACTIVE_REEMIT_SEC = 300     # periodic heartbeat for still ACTIVE alerts

# Intent queue (simple process-local). Controller should drain this.
INTENT_FAULT_SAFE = "FAULT_SAFE"

class AlertState:
    __slots__ = ("code","severity","state","first_seen","last_seen","activate_deadline","clear_count","event_id","count","context")
    def __init__(self, code: str, severity: str):
        self.code = code
        self.severity = severity
        self.state = STATE_CLEAR
        self.first_seen = None
        self.last_seen = None
        self.activate_deadline = None
        self.clear_count = 0
        self.event_id = None
        self.count = 0
        self.context = {}

class SafetyCheck:
    def __init__(self, unit, consus_id: str, poll_hz: float = 1.0):
        self.unit = unit
        self.consus_id = consus_id
        self.interval = max(0.2, 1.0 / poll_hz)  # seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        # Alert states keyed by code
        self.alerts: dict[str, AlertState] = {}
        # Batching queues
        self._batch: list[dict] = []
        self._last_batch_post = time.time()
        # Intent queue (simple deque)
        self.intent_queue = deque(maxlen=100)
        # Recent telemetry ring for context (timestamp -> minimal dict)
        self.telemetry_ring = deque(maxlen=50)

    # --- Public API ---
    def start(self):
        logger.info(f"[{self.consus_id}] HealthMonitor start interval={self.interval:.2f}s")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
        logger.info(f"[{self.consus_id}] HealthMonitor stopped")

    def poll_intent(self):
        try:
            return self.intent_queue.popleft()
        except IndexError:
            return None

    # --- Core Loop ---
    def _run(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._scan_once()
                self._flush_batches_if_needed()
            except Exception:
                logger.exception(f"[{self.consus_id}] Health scan error")
            dt = time.time() - t0
            sleep_for = max(0.0, self.interval - dt)
            time.sleep(sleep_for)

    # --- Scanning Logic ---
    def _read_reg(self, name: str):
        try:
            return self.unit.safe_read(name)
        except Exception:
            return None

    def _collect_raw(self):
        # Actual GoodWe register names (recently added to register_map.json)
        return {
            "ems_check_status": self._read_reg("ems_check_status"),
            "bms_warning_bits": self._read_reg("bms_warning_bits"),
            "bms_alarm_bits": self._read_reg("bms_alarm_bits"),
            "bms_soc": self._read_reg("bms_soc"),
            "bms_soh_percent": self._read_reg("bms_soh_percent"),
            "arc_fault": self._read_reg("arc_fault"),
            "parallel_comm_status": self._read_reg("parallel_comm_status"),
            "meter_internal_external": self._read_reg("meter_internal_external"),
            "int_meter_comm": self._read_reg("int_meter_comm"),
            "ext_meter_comm": self._read_reg("ext_meter_comm"),
            "remote_comm_loss_time": self._read_reg("remote_comm_loss_time"),
            "app_mode_display": self._read_reg("app_mode_display"),
            "meter_total_active_power": self._read_reg("meter_total_active_power"),
            "battery_soc": self._read_reg("battery_soc"),
            "pv_power_total": self._read_reg("pv_power_total"),
            "meter_target_power_offset": self._read_reg("meter_target_power_offset"),
        }

    def _scan_once(self):
        raw = self._collect_raw()
        now = time.time()
        # Cache telemetry context
        self.telemetry_ring.append({
            "ts": now,
            "soc": (raw.get("battery_soc") or 0) / 100.0,
            "grid_w": raw.get("meter_total_active_power"),
            "pv_w": raw.get("pv_power_total"),
            "mode": raw.get("app_mode_display"),
            "bias_w": raw.get("meter_target_power_offset"),
        })

    # Evaluate conditions
        self._eval_condition("EMS_FAULT", CRITICAL, raw.get("ems_check_status") not in (None, 1), raw)
        self._eval_condition("BMS_ALARM", CRITICAL, (raw.get("bms_alarm_bits") or 0) != 0, raw)
        self._eval_condition("ARC_FAULT", CRITICAL, (raw.get("arc_fault") or 0) != 0, raw)
        # Warning level
        self._eval_condition("BMS_WARNING", WARNING, (raw.get("bms_warning_bits") or 0) != 0, raw)
        # Meter comms (treat missing or 0? placeholder heuristic) -> WARNING
        comms_lost = (raw.get("ext_meter_comm") == 0 and raw.get("int_meter_comm") == 0)
        self._eval_condition("METER_COMMS_LOSS", WARNING, comms_lost, raw)

    # --- Alert State Machine ---
    def _eval_condition(self, code: str, severity: str, active: bool, raw: dict):
        st = self.alerts.get(code)
        if not st:
            st = AlertState(code, severity)
            self.alerts[code] = st
        now = time.time()
        if active:
            if st.state == STATE_CLEAR:
                # Start debounce window
                if st.activate_deadline is None:
                    st.activate_deadline = now + DEBOUNCE_ACTIVATE_SEC
                if now >= st.activate_deadline:
                    st.state = STATE_ACTIVE
                    st.first_seen = st.first_seen or now
                    st.last_seen = now
                    st.event_id = st.event_id or self._make_event_id(code, st.first_seen)
                    st.count += 1
                    st.context = self._make_context(raw)
                    self._emit_alert(st, STATE_ACTIVE)
                    if severity == CRITICAL:
                        self._enqueue_intent(INTENT_FAULT_SAFE)
            elif st.state == STATE_ACTIVE:
                st.last_seen = now
                # Periodic heartbeat for ACTIVE
                if (now - st.first_seen) > ACTIVE_REEMIT_SEC and (now - st.last_seen) < 2*self.interval:
                    self._emit_alert(st, STATE_ACTIVE, heartbeat=True)
            elif st.state == STATE_RESOLVED:
                # Re-activation
                st.state = STATE_ACTIVE
                st.last_seen = now
                st.count += 1
                st.context = self._make_context(raw)
                self._emit_alert(st, STATE_ACTIVE)
        else:
            # Not active
            if st.state == STATE_ACTIVE:
                st.clear_count += 1
                if st.clear_count >= DEBOUNCE_CLEAR_POLLS:
                    st.state = STATE_RESOLVED
                    st.last_seen = now
                    self._emit_alert(st, STATE_RESOLVED)
            else:
                # Remain CLEAR, reset activation deadline for stability
                st.activate_deadline = None
                st.clear_count = 0

    # --- Helpers ---
    def _make_event_id(self, code: str, first_seen: float):
        base = f"{self.consus_id}:{code}:{int(first_seen)}"
        return uuid.uuid5(uuid.NAMESPACE_OID, base).hex

    def _make_context(self, raw: dict):
        return {
            "mode": raw.get("ems_mode_display"),
            "soc": ((raw.get("battery_soc") or 0)/100.0),
            "grid_w": raw.get("meter_total_active_power"),
            "pv_w": raw.get("pv_power_total"),
            "bias_w": raw.get("meter_target_power_offset"),
        }

    def _enqueue_intent(self, intent: str):
        try:
            self.intent_queue.append({"intent": intent, "ts": datetime.now(timezone.utc).isoformat()})
        except Exception:
            logger.warning("Intent queue full; dropping intent")

    def _emit_alert(self, st: AlertState, state: str, heartbeat: bool = False):
        # Build standardized payload using schema
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        context = AlertContext(**{
            "mode": st.context.get("mode"),
            "soc": st.context.get("soc"),
            "grid_w": st.context.get("grid_w"),
            "pv_w": st.context.get("pv_w"),
            "bias_w": st.context.get("bias_w"),
        })
        recent = None
        if st.severity == CRITICAL:
            recent = [
                RecentTelemetry(**{
                    "ts": datetime.fromtimestamp(item["ts"], tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                    "soc": item.get("soc"),
                    "grid_w": item.get("grid_w"),
                    "pv_w": item.get("pv_w"),
                    "mode": item.get("mode"),
                    "bias_w": item.get("bias_w"),
                }) for item in list(self.telemetry_ring)[-20:]
            ]

        alert = AlertEvent(
            site_id=self.consus_id,
            ts=ts,
            severity=st.severity,  # type: ignore[arg-type]
            code=st.code,
            state=state,           # type: ignore[arg-type]
            event_id=st.event_id,
            count=st.count,
            heartbeat=heartbeat,
            context=context,
            recent_telemetry=recent,
        )

        payload = alert.model_dump(mode="json")
        if st.severity == CRITICAL:
            self._post_immediate(payload)
        elif st.severity in (WARNING, INFO):
            self._batch.append(payload)
        logger.info(f"[{self.consus_id}] Alert {st.code} {state} sev={st.severity} hb={heartbeat}")

    # --- Posting / Batching ---
    def _flush_batches_if_needed(self):
        now = time.time()
        if self._batch and (now - self._last_batch_post) >= WARNING_BATCH_SEC:
            batch = self._batch
            self._batch = []
            self._last_batch_post = now
            self._post_batch(batch)

    def _post_immediate(self, payload: dict):
        # Post critical alert immediately
        post_health_alerts([payload])
        logger.warning(f"[POST-CRITICAL] {payload}")

    def _post_batch(self, batch: list[dict]):
        post_health_alerts(batch)
        logger.info(f"[POST-BATCH] {len(batch)} alerts")
        for p in batch:
            logger.debug(f"[POST-BATCH-ITEM] {p}")

__all__ = ["SafetyCheck", "INTENT_FAULT_SAFE"]
