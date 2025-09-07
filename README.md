# Edge Controller (GoodWe EMS) Overview

This edge controller manages GoodWe inverter/battery systems using EMS registers for zero‑export by day and Import‑AC charging during cheap windows at night. It aggregates PV, enforces write discipline and ramp/clamp limits, monitors health, and posts telemetry and alerts to a backend.

## Data Flow

- Inbound
	- Bootstrap HTTP seeds state and the register map.
	- MQTT provides dynamic updates (settings, tasks, battery configs) into a shared EdgeState.
	- Modbus reads device telemetry continuously.
- Outbound
	- Telemetry batches to `/blob/ingest` (every ~10 s).
	- Health/alerts to `/blob/health` (CRITICAL immediate; WARNING/INFO batched ~45 s).

## MQTT (Inbound Config)
- Topic: `lanzone/{group_id}/updates` (from bootstrap).
- Updates merged into `core/edge_state.py` (EDGE_STATE):
	- settings: cheap_window, import_charge_power_w, min_import_w, target_soc_percent, export_cap_w, meter_bias_w (planned split: bias_day_w/bias_night_w), max_charge_w, max_ramp_rate_w_per_s|ramprate, pv_enabled, auto_bias_trim, optional write_guard and endpoints.
	- tasks: minimal structures per battery (future use).
	- battery_configs: per‑consus_id Modbus and limits.

## HTTP API
- Bootstrap
	- `/edge/init`: initial state (settings, tasks, battery_configs).
	- `/edge/validate-state` and `/edge/validate-modbus`: sanity checks.
- Posting
	- `/blob/ingest`: array of TelemetryPayload objects.
	- `/blob/health`: array of alert events (CRITICAL/Warning/Info) with event_id.

## Schemas
- `schemas/telemetry.py` TelemetryPayload
	- `consus_id: str`, `mode: str`, `timestamp: datetime|ISO`, `payload: dict`.
- `schemas/battery_config.py` EdgeBatteryConfig
	- `consus_id`, `MODBUS_IP`, `MODBUS_PORT`, `max_charge_w`, `max_ramp_rate_w_per_s`, `pv_enabled`, optional capacity/reserve/max_soc.
- Settings (dynamic)
	- `cheap_window {start,end}`, `target_soc_percent`, `import_charge_power_w`, `min_import_w`, `export_cap_w`, `meter_bias_w` (planned `bias_day_w`/`bias_night_w`), `max_charge_w`, `max_ramp_rate_w_per_s|ramprate`, `pv_enabled`, `auto_bias_trim {enable,target_w,deadband_w,step_w}`, `write_guard {per_reg_min_s,global_writes_per_s}` (planned), endpoints.

## Modbus Register Map
- Reads (polled 1–2 Hz)
	- Grid/meter: 36025 meter_total_active_power.
	- Battery: 37007 battery_soc (also 39898 bms_soc for health); 37002/03/04 voltage/current/power.
	- PV: 35103–35119 strings; 35337–35341 MPPT; 36045 ct2_active_power (AC‑coupled).
	- Mode: 10405 app_mode_display; 10456 ems_mode_display.
	- Health: 40008 ems_check_status, 39894 bms_warning_bits, 39896 bms_alarm_bits, 39899 bms_soh_percent, 36065 arc_fault, 36066 parallel_comm_status, 50091/92/94 meter path/comms, 42101 remote_comm_loss_time.
- Writes (control)
	- EMS: 47511 ems_power_mode (0x0001 Auto, 0x0004 Import‑AC), 47512 ems_power_set (W).
	- Export: 47509 feed_power_enable=1, 47510 export_power_cap (W).
	- Bias: 47120 meter_target_power_offset (W).
	- Meter: 47464 external_meter_enable (0/1).
	- Commissioning: 47505 manufacturer_code=2; (opt) 42101 remote_comm_loss_time.

## Runtime Components
- `main.py`
	- Loads bootstrap + register map, seeds EdgeState, runs MQTT listener.
	- Starts BackendPoster (telemetry → `/blob/ingest`) and per‑battery controller threads.
- `core/battery_unit.py`
	- Modbus abstraction; telemetry read/aggregation (PV totals), safe read/write by name.
- `modbus/modbus_registry.py`
	- Low‑level Modbus; write path protected by WriteGuard (dedupe, min interval, global rate cap).
- `battery_opt/ems_manager.py`
	- Commissioning writes; mode selection (Auto vs Import‑AC); PV‑aware import setpoint; clamps and ramp limiting; optional bias trim in Auto.
- `core/controller.py`
	- Orchestrates each loop; reads telemetry; applies EMS; consumes FAULT_SAFE intents from health monitor to force safe behavior.
- `battery_opt/safety_check.py`
	- Polls health registers 1–2 Hz; alert state machines (debounce); CRITICAL posts immediately to `/blob/health` with recent telemetry; WARNING/INFO batched; emits FAULT_SAFE intents.
- `utils/backend_utils.py`
	- Posting helpers for telemetry and health endpoints.

## Control Logic
- Day (outside cheap window or SOC ≥ target)
	- Auto mode (0x0001); export cap enforced; setpoint cleared; bias offset applied (single value today).
- Night (within cheap window & SOC < target)
	- Import‑AC mode (0x0004); setpoint = `import_charge_power_w − PV_total`, min‑floored, max‑clamped; ramped by `max_ramp_rate_w_per_s`.
- Bias trim (optional)
	- In Auto: if residual grid exceeds deadband, adjust bias by step within bounds.

## Safeguards
- WriteGuard
	- Per‑register ≥0.25 s; ≤5 writes/s global; dedupe; logs throttles/drops.
- Health & FAULT_SAFE
	- CRITICAL triggers: EMS≠1, BMS Alarm≠0, ARC fault≠0 → emit FAULT_SAFE intent; controller suppresses Import‑AC path (safe idle behavior).
- Ramp & Clamp
	- Setpoint clamped to `max_charge_w` and ramp‑limited by `max_ramp_rate_w_per_s`.

## Outbound Payloads
- Telemetry (→ `/blob/ingest`)
	- Array of TelemetryPayloads: `consus_id`, `mode`, `timestamp`, `payload` (telemetry dict incl. SOC, grid_w, pv totals, etc.).
- Health (→ `/blob/health`)
	- Array of events: `{site_id, ts, severity, code, state, event_id, count, context{mode,soc,grid_w,pv_w,bias_w}}`; CRITICAL also includes `recent_telemetry` (last ~10 s).

## Gaps & Next Steps (design targets)
- Config: add `bias_day_w/bias_night_w`, `auto_bias_trim.min/max`, `write_guard.*` settings.
- Commissioning: periodic verify & reassert drifted registers.
- Control: dwell/hysteresis at window edges; explicit exit sequence (47512=0 then 47511=Auto); day/night bias split.
- Watchdog: stale telemetry (>3 s) → explicit Auto + 0 setpoint + small bias and 10 s write suppression.
- Posting: retries/backoff and disk spool for offline tolerance; health CRITICAL debounce 2–3 s.
- Telemetry: add rolling p95 |grid|, writes/sec, comms_age, state.

---
For deeper details, see:
- `battery_opt/ems_manager.py` (EMS behavior)
- `battery_opt/safety_check.py` (health & alerts)
- `modbus/modbus_registry.py` + `bootstrap/register_map.json` (I/O definitions)
- `core/controller.py` (control loop)
