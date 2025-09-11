import logging
from datetime import datetime, time, timedelta
from core.edge_state import EDGE_STATE

logger = logging.getLogger(__name__)

AUTO_MODE = 0x0001
IMPORT_AC_MODE = 0x0004

class EMSManager:
    """Implements GoodWe EMS rule set for zero-export + off-peak charging.
    Responsibilities:
      * One-time commissioning writes (export cap, manufacturer code, etc.)
      * Decide EMS mode (Auto vs Import-AC) based on charge windows & SOC
      * Provide target power setpoint when in Import-AC
    """

    def __init__(self, consus_id: str):
        self.consus_id = consus_id
        self._commissioned = False
        self._last_setpoint_w = 0
        self._last_setpoint_ts = None
        self._hold_until = None 

    # ---------- commissioning ----------
    def commission_if_needed(self, unit):
        if self._commissioned:
            return
        cfg = EDGE_STATE.get_battery_config(self.consus_id)
        settings = EDGE_STATE.settings
        try:
            # Mandatory to accept EMS commands
            unit.safe_write("manufacturer_code", 2)

            # Export limit enable + cap (defaults)
            export_cap_w = settings.get("export_cap_w", 0)
            unit.safe_write("feed_power_enable", 1)
            unit.safe_write("export_power_cap", export_cap_w)

            # External meter enable if requested
            if settings.get("external_meter", True):
                unit.safe_write("external_meter_enable", 1)

            # Initial bias offset (e.g. -50 W)
            bias = settings.get("meter_bias_w", -50)
            if bias is not None:
                unit.safe_write("meter_target_power_offset", int(bias))

            self._commissioned = True
            logger.info(f"[{self.consus_id}] EMS commissioning complete (cap {export_cap_w} W)")
        except Exception:
            logger.exception(f"[{self.consus_id}] EMS commissioning failed; will retry next loop")

    # ---------- window helpers ----------
    def _parse_timeish(self, t: time | str) -> time:
        if isinstance(t, time):
            return t
        if isinstance(t, str):
            t = t.strip()
            # Accept HH:MM[:SS[.ffffff]] first, then fall back to HH:MM
            try:
                return time.fromisoformat(t)
            except ValueError:
                return datetime.strptime(t, "%H:%M").time()
        raise TypeError(f"Unsupported time value: {t!r}")

    
    def _normalize_windows(self, windows) -> list[tuple[time, time]]:
        """Accepts [start,end] pairs (list/tuple) or dicts {'start','end'}, with items as time or 'HH:MM'/'HH:MM:SS'."""
        norm: list[tuple[time, time]] = []
        for i, win in enumerate(windows or []):
            try:
                if not win:
                    continue
                if isinstance(win, dict):
                    s = self._parse_timeish(win.get("start"))
                    e = self._parse_timeish(win.get("end"))
                elif isinstance(win, (list, tuple)) and len(win) == 2:
                    s = self._parse_timeish(win[0])
                    e = self._parse_timeish(win[1])
                else:
                    continue
                norm.append((s, e))
            except Exception as ex:
                logger.warning("[%s] Skipping invalid window %r (idx=%s): %s", self.consus_id, win, i, ex)
        # Optional: dedupe identical windows while preserving order
        # norm = list(dict.fromkeys(norm))
        return norm

    
    def _now_in_any_window(self, now: time, windows: list[tuple[time, time]]) -> bool:
        # left-closed, right-open [start, end)
        # DEPR
        for s, e in windows:
            if s <= e:
                if s <= now < e:
                    return True
            else:
                # spans midnight (e.g., 23:30–04:30)
                if now >= s or now < e:
                    return True
        return False

    def in_charge_window(self) -> bool:
        now = datetime.now(EDGE_STATE.tz).time()   # use EdgeState tz
        windows = EDGE_STATE.get_charge_windows(self.consus_id)
        if not windows:
            return False
        for s, e in windows:
            if s <= e:
                if s <= now < e:
                    return True
            else:  # spans midnight
                if now >= s or now < e:
                    return True
        return False

    def _current_window_end_dt(self, now_dt: datetime) -> datetime | None:
        """Return the datetime (in EDGE_STATE.tz) at which the *current* window ends, or None if not in a window."""
        now_t = now_dt.timetz().replace(tzinfo=None)  # use naive time for comparisons
        windows = EDGE_STATE.get_charge_windows(self.consus_id)
        if not windows:
            return None
        for s, e in windows:
            # normalize to today/tomorrow with wrap handling
            if s <= e:
                if s <= now_t < e:
                    return now_dt.replace(hour=e.hour, minute=e.minute, second=getattr(e, "second", 0), microsecond=0)
            else:
                # window spans midnight: active if now>=s or now<e
                if now_t >= s:
                    # ends "tomorrow" at e
                    end_date = (now_dt + timedelta(days=1)).date()
                    return datetime.combine(end_date, e, tzinfo=EDGE_STATE.tz)
                if now_t < e:
                    # started "yesterday" at s, ends today at e
                    return now_dt.replace(hour=e.hour, minute=e.minute, second=getattr(e, "second", 0), microsecond=0)
        return None
    

    def in_balance_window(self) -> bool:
        return not self.in_charge_window()

  

    # ---------- decide/apply ----------
    def decide(self, unit, soc: float, pv_power: int | float = 0) -> tuple[int, int]:
        """
        Return (ems_mode, ems_power_set).

        Policy:
        - If in charge window and SOC < target -> Import-AC with positive setpoint (charging).
        - If in charge window and SOC >= target -> HOLD (no discharge) by staying in Import-AC with setpoint 0
          until the window ends.
        - Outside windows -> AUTO (balance to ~0 exchange).
        """
        settings = EDGE_STATE.settings
        now_dt = datetime.now(EDGE_STATE.tz)

        target_soc = settings.get("target_soc_percent", 100) / 100.0
        base_import_power_w = settings.get("import_charge_power_w", 0)
        min_import = settings.get("min_import_w", 0)

        # Optional dynamic cap from the active dynamic task
        dyn = EDGE_STATE.get_task(self.consus_id) or {}
        dyn_cap_kw = dyn.get("max_import_limit_kw")

        in_window = self.in_charge_window()

        if in_window:
            # If we just entered (or are in) a window and have reached target, latch hold until window end.
            if soc >= target_soc * 0.99:
                if self._hold_until is None or now_dt >= self._hold_until:
                    self._hold_until = self._current_window_end_dt(now_dt)
                    logger.info("[%s] Reached target SOC; HOLD until %s", self.consus_id, self._hold_until)
                # Stay in Import-AC with setpoint 0 → prevents discharge during cheap window
                return IMPORT_AC_MODE, 0

            # Charging path (below target)
            if base_import_power_w > 0:
                effective = base_import_power_w - pv_power
                if isinstance(min_import, (int, float)) and effective < min_import:
                    effective = min_import
            else:
                effective = 0

            # Apply dynamic cap if present
            if isinstance(dyn_cap_kw, (int, float)) and dyn_cap_kw > 0:
                effective = min(effective, int(dyn_cap_kw * 1000))

            return IMPORT_AC_MODE, int(max(0, effective))

        # Not in a charge window: clear hold and balance
        if self._hold_until is not None and now_dt >= self._hold_until:
            logger.info("[%s] Charge window ended; clearing HOLD", self.consus_id)
        self._hold_until = None
        return AUTO_MODE, 0

    def apply(self, unit, soc: float, meter_p: int | float = 0, pv_power: int | float = 0):
        self.commission_if_needed(unit)
        mode, xset = self.decide(unit, soc, pv_power)

        # Clamp & ramp only relevant for import (charging) setpoints
        cfg = EDGE_STATE.get_battery_config(self.consus_id) or {}
        max_charge_w = cfg.get("max_charge_w") or EDGE_STATE.settings.get("max_charge_w")
        # Ramp rate priority: battery config > global settings (either key form)
        ramp_rate = cfg.get("max_ramp_rate_w_per_s")
        if not ramp_rate:
            ramp_rate = EDGE_STATE.settings.get("max_ramp_rate_w_per_s") or EDGE_STATE.settings.get("ramprate")

        now_ts = datetime.now().timestamp()
        if mode == IMPORT_AC_MODE:
            # Sanity: no negative import request
            if xset < 0:
                xset = 0
            # Clamp to configured max charge
            if isinstance(max_charge_w, (int, float)) and max_charge_w > 0 and xset > max_charge_w:
                logger.debug(f"[{self.consus_id}] Clamp import setpoint {xset}W -> {max_charge_w}W (max_charge_w)")
                xset = int(max_charge_w)
            # Ramp limitation (symmetric up/down)
            if isinstance(ramp_rate, (int, float)) and ramp_rate > 0 and self._last_setpoint_ts is not None:
                dt = max(0.001, now_ts - self._last_setpoint_ts)
                max_delta = ramp_rate * dt
                delta = xset - self._last_setpoint_w
                if abs(delta) > max_delta:
                    direction = 1 if delta > 0 else -1
                    ramped = int(self._last_setpoint_w + direction * max_delta)
                    logger.debug(f"[{self.consus_id}] Ramp limit Δ{delta:.1f}W -> Δ{direction*max_delta:.1f}W ({self._last_setpoint_w}->{ramped})")
                    xset = ramped
        else:
            # Leaving import mode: reset ramp baseline
            self._last_setpoint_w = 0
            self._last_setpoint_ts = now_ts

        # Write mode (only if changed)
        try:
            current_mode = unit.safe_read("ems_power_mode")
        except Exception:
            current_mode = None
        if current_mode != mode:
            unit.safe_write("ems_power_mode", mode)
            logger.info(f"[{self.consus_id}] Set EMS mode {hex(mode)}")

        # Write setpoint
        if mode == IMPORT_AC_MODE:
            unit.safe_write("ems_power_set", int(xset))
            logger.debug(f"[{self.consus_id}] Import-AC set {xset} W")
            self._last_setpoint_w = int(xset)
            self._last_setpoint_ts = now_ts
        else:
            # ensure setpoint is zero to avoid stale values in Auto
            try:
                unit.safe_write("ems_power_set", 0)
            except Exception:
                pass

        # Optional trim if long-term residual drift persists (only in AUTO)
        bias_conf = EDGE_STATE.settings.get("auto_bias_trim")  # {"enable":bool,"target_w":0,"step_w":10,"deadband_w":30}
        if bias_conf and bias_conf.get("enable") and mode == AUTO_MODE:
            target_w = bias_conf.get("target_w", 0)
            residual = meter_p - target_w
            window = bias_conf.get("deadband_w", 30)
            if abs(residual) > window:
                step = bias_conf.get("step_w", 10)
                adj = step if residual > 0 else -step
                try:
                    current_bias = unit.safe_read("meter_target_power_offset")
                except Exception:
                    current_bias = 0
                new_bias = int(max(-500, min(500, current_bias + adj)))
                if new_bias != current_bias:
                    unit.safe_write("meter_target_power_offset", new_bias)
                    logger.info(f"[{self.consus_id}] Bias trim {current_bias}->{new_bias} (residual {residual} W)")
        return mode, xset
