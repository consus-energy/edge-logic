from __future__ import annotations

from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple, Optional, Union
from datetime import datetime, date, time, timedelta
import logging

logger = logging.getLogger(__name__)
LONDON_TZ = ZoneInfo("Europe/London")


class EdgeState:

    """
    LANZone live state: battery configs, global settings, comms, and tasks.

    - Dynamic tasks are day-aware (keyed by service_day) and the edge keeps
      only *today* and *tomorrow* per battery.
    - Static tasks apply every day (service_day ignored).
    - Dynamic conflict resolution:
        * override=True beats non-override
        * else same idempotency_key → higher revision wins; tie → newer updated_at
        * else different idempotency_key → treat as replacement (new family)
    
    """

    def __init__(self, tz: ZoneInfo = LONDON_TZ, fallback_max_days: int = 2):
        self.tz = tz
        self.fallback_max_days = fallback_max_days

        self.battery_configs: Dict[str, dict] = {}
        self.settings: dict = {}
        self.comms_settings: dict = {}
        self.register_map: dict = {}

        # Dynamic, day-aware tasks: tasks_dynamic[consus_id][service_day] = entry
        self.tasks_dynamic: Dict[str, Dict[date, dict]] = {}
        # Static (fixed) task per battery: tasks_static[consus_id] = entry
        self.tasks_static: Dict[str, dict] = {}

    # ---------- battery / settings ----------
    def update_battery(self, consus_id: str, new_config: dict) -> None:
        self.battery_configs[consus_id] = new_config or {}
        logger.info("[EdgeState] Battery '%s' config updated", consus_id)

    def update_settings(self, new_settings: dict) -> None:
        self.settings = new_settings or {}
        logger.info("[EdgeState] Global settings updated")

    def update_comms_settings(self, new_comms_settings: dict) -> None:
        self.comms_settings = new_comms_settings or {}
        logger.info("[EdgeState] Comms settings updated")

    def set_register_map(self, reg_map: dict) -> None:
        self.register_map = reg_map or {}
        logger.info("[EdgeState] Register map loaded")

    def get_battery_config(self, consus_id: str) -> dict:
        return self.battery_configs.get(consus_id, {})

    def get_register_map(self) -> dict:
        return self.register_map

    def get_setting(self, key: str):
        return self.settings.get(key)
    
    def get_comms_setting(self, key: str):
        return self.comms_settings.get(key)

    # ---------- task API (MQTT entrypoint) ----------
    def update_task(self, consus_id: Union[str, List[str]], new_task: Optional[dict]) -> None:
        """
        MQTT entrypoint.
        - If task_type == 'dynamic': store under tasks_dynamic[consus_id][service_day].
        - If task_type == 'static' : store under tasks_static[consus_id] (service_day ignored).
        - If new_task is None     : for DYNAMIC, fallback copy-forward; for STATIC, keep existing.

        Expected dynamic fields:
          task_type="dynamic", service_day(ISO), charge_windows, max_import_limit,
          override(bool), idempotency_key(str), revision(int), updated_at(ISO).
        """
        if isinstance(consus_id, list):
            for cid in consus_id:
                self.update_task(cid, new_task)
            return

        # No new data: dynamic fallback only
        if new_task is None:
            self._fallback_dynamic_from_previous(consus_id)
            return

        task_type = (str(new_task.get("task_type") or "").lower())

        # ---------- STATIC ----------
        if task_type == "static":
            s_start = self._parse_time(new_task.get("charge_window_start"))
            s_end   = self._parse_time(new_task.get("charge_window_end"))

            # Allow static also via a single pair in charge_windows
            if (s_start is None or s_end is None) and (new_task.get("charge_windows") or []):
                try:
                    s, e = new_task["charge_windows"][0]
                    s_start, s_end = self._parse_time(s), self._parse_time(e)
                except Exception:
                    pass

            entry = {
                "task_code": new_task.get("task_code"),
                "task_type": "static",
                "charge_window_start": s_start,
                "charge_window_end": s_end,
                "max_import_limit_kw": new_task.get("max_import_limit"),
                "override": bool(new_task.get("override", False)),
                "updated_at": self._parse_dt(new_task.get("updated_at")) or datetime.now(self.tz),
                "idempotency_key": new_task.get("idempotency_key"),
                "revision": int(new_task.get("revision", 0)),
            }

            prev = self.tasks_static.get(consus_id)
            if prev and prev.get("override") and not entry["override"]:
                logger.info("[EdgeState] Ignored static non-override for '%s' (existing is override)", consus_id)
                return

            self.tasks_static[consus_id] = entry
            logger.info("[EdgeState] STATIC set for '%s': %s", consus_id, entry["task_code"])
            return

        # ---------- DYNAMIC ----------
        try:
            sd_str = new_task["service_day"]
            service_day = date.fromisoformat(sd_str)
        except Exception as e:
            logger.warning("[EdgeState] Dynamic task missing/invalid service_day: %s; rejecting.", e)
            return

        dyn_windows: List[Tuple[time, time]] = []
        for pair in (new_task.get("charge_windows") or []):
            try:
                s, e = pair
                dyn_windows.append((self._parse_time(s), self._parse_time(e)))
            except Exception:
                logger.warning("[EdgeState] Bad dynamic window %s; skipping.", pair)

        entry = {
            "task_code": new_task.get("task_code", f"task-{consus_id}-{service_day.isoformat()}"),
            "task_type": "dynamic",
            "charge_windows": dyn_windows,
            "max_import_limit_kw": new_task.get("max_import_limit"),
            "override": bool(new_task.get("override", False)),
            "updated_at": self._parse_dt(new_task.get("updated_at")) or datetime.now(self.tz),
            "idempotency_key": str(new_task.get("idempotency_key") or ""),  # required for dedupe
            "revision": int(new_task.get("revision", 0)),                   # monotonic
        }

        per_batt = self.tasks_dynamic.setdefault(consus_id, {})
        existing = per_batt.get(service_day)

        def take_new(reason: str):
            per_batt[service_day] = entry
            logger.info("[EdgeState] DYNAMIC set for '%s' on %s (%s): %s windows=%d",
                        consus_id, service_day, reason, entry["task_code"], len(dyn_windows))

        if existing:
            if entry["override"] and not existing.get("override", False):
                take_new("override supersedes")
            else:
                new_key = entry["idempotency_key"]
                old_key = existing.get("idempotency_key", "")
                if new_key and old_key and new_key == old_key:
                    # Same family: use revision, then updated_at
                    new_rev = entry["revision"]
                    old_rev = int(existing.get("revision", 0))
                    if new_rev > old_rev:
                        take_new("higher revision")
                    elif new_rev == old_rev and entry["updated_at"] > existing.get(
                        "updated_at", datetime.min.replace(tzinfo=self.tz)
                    ):
                        take_new("same revision, newer updated_at")
                    else:
                        logger.info("[EdgeState] Ignored older/duplicate dynamic for '%s' on %s",
                                    consus_id, service_day)
                else:
                    # Different family: accept as replacement (assume newest)
                    take_new("new idempotency_key")
        else:
            take_new("fresh")

        self._gc_dynamic_keep_today_tomorrow()

    # ---------- simple helpers ----------
    def get_task(self, consus_id: str, which_day: Optional[date] = None) -> Optional[dict]:
        if which_day is None:
            which_day = datetime.now(self.tz).date()
        return (self.tasks_dynamic.get(consus_id, {}) or {}).get(which_day) \
               or self.tasks_static.get(consus_id)

    def complete_task(self, consus_id: str, which_day: Optional[date] = None) -> None:
        """Call when today’s dynamic task is finished (or on DELETE)."""
        if which_day is None:
            which_day = datetime.now(self.tz).date()
        daymap = self.tasks_dynamic.get(consus_id)
        if daymap and which_day in daymap:
            daymap.pop(which_day, None)
            if not daymap:
                self.tasks_dynamic.pop(consus_id, None)
            logger.info("[EdgeState] Dynamic task completed for '%s' on %s", consus_id, which_day)
        self._gc_dynamic_keep_today_tomorrow()

    def get_task_type(self, consus_id: str, which_day: Optional[date] = None) -> Optional[str]:
        if which_day is None:
            which_day = datetime.now(self.tz).date()
        if which_day in (self.tasks_dynamic.get(consus_id) or {}):
            return "dynamic"
        if consus_id in self.tasks_static:
            return "static"
        return None

    def get_charge_windows(self, consus_id: str, which_day: Optional[date] = None) -> List[Tuple[time, time]]:
        """
        Resolve windows for 'which_day':
          - If a DYNAMIC task exists for that day → its windows.
          - Else, if a STATIC task exists → its single fixed window (if set).
          - Else → [].
        """
        if which_day is None:
            which_day = datetime.now(self.tz).date()

        dyn_day_map = self.tasks_dynamic.get(consus_id) or {}
        dyn_entry = dyn_day_map.get(which_day)
        if dyn_entry:
            return list(dyn_entry.get("charge_windows") or [])

        stat_entry = self.tasks_static.get(consus_id)
        if stat_entry:
            s = stat_entry.get("charge_window_start")
            e = stat_entry.get("charge_window_end")
            if s and e:
                return [(s, e)]
        return []

    # ---------- internals ----------
    def _fallback_dynamic_from_previous(self, consus_id: str) -> None:
        """
        For DYNAMIC tasks only: if no new payload arrives, copy the most recent
        day’s dynamic windows forward into today/tomorrow, if fresh enough.
        STATIC tasks are not affected by fallback.
        """
        per_batt = self.tasks_dynamic.get(consus_id, {})
        if not per_batt:
            return

        last_day = max(per_batt.keys())
        age_days = (datetime.now(self.tz).date() - last_day).days
        if age_days > self.fallback_max_days:
            logger.warning(
                "[EdgeState] Fallback refused for '%s': last task %s is %d days old (> %d).",
                consus_id, last_day, age_days, self.fallback_max_days
            )
            return

        last_task = per_batt[last_day]
        now = datetime.now(self.tz)
        today = now.date()
        tomorrow = today + timedelta(days=1)

        if today not in per_batt:
            per_batt[today] = {
                **last_task,
                "task_code": f"{last_task.get('task_code','task')}-copy-{today.isoformat()}",
                "updated_at": now,
            }
            logger.info("[EdgeState] Fallback copied %s → today for '%s'", last_day, consus_id)

        if tomorrow not in per_batt:
            per_batt[tomorrow] = {
                **last_task,
                "task_code": f"{last_task.get('task_code','task')}-copy-{tomorrow.isoformat()}",
                "updated_at": now,
            }
            logger.info("[EdgeState] Fallback copied %s → tomorrow for '%s'", last_day, consus_id)

        self._gc_dynamic_keep_today_tomorrow()

    def _gc_dynamic_keep_today_tomorrow(self) -> None:
        today = datetime.now(self.tz).date()
        tomorrow = (datetime.now(self.tz) + timedelta(days=1)).date()
        for cid, daymap in list(self.tasks_dynamic.items()):
            for d in list(daymap.keys()):
                if d != today and d != tomorrow:
                    daymap.pop(d, None)
            if not daymap:
                self.tasks_dynamic.pop(cid, None)

    @staticmethod
    def _parse_time(t: Optional[Union[str, time]]) -> Optional[time]:
        if t is None:
            return None
        if isinstance(t, time):
            return t
        t = str(t).strip().replace("Z", "")
        try:
            return time.fromisoformat(t)
        except Exception:
            # Fallback "HH:MM"
            return datetime.strptime(t, "%H:%M").time()

    @staticmethod
    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


# Singleton instance used by the MQTT listener
EDGE_STATE = EdgeState()
