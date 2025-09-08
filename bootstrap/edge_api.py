from datetime import datetime
import requests
import logging
from core.battery_unit import BatteryUnit
import time
import socket

from schemas.edge_state import EdgeStatePayload  
logger = logging.getLogger(__name__)

EAD_PROBES = {
    "meter_total_active_power": "36025",  # W (+/- import/export)
    "battery_soc": "37007",               # %
    "app_mode_display": "10405",          # enum-ish display (optional, but useful)
    "bms_alarm_bits": "39896",            # should be 0 in normal ops
    "bms_warning_bits": "39894",          # should be 0 in normal ops
    "ems_check_status": "40008",          # 1 (OK) on many firmwares
    "meter_path": "50091",                # internal/external path flag
}



def _auth_headers(edge_state) -> dict:
    token = edge_state.get_comms_setting("API_KEY")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}

def init_edge_state(edge_state):
    """
    Attempt /edge/init with fallback URLs if the primary API fails.
    Pull dynamic config from backend /edge/init and update EdgeState.
    """
    url = edge_state.get_comms_setting("API_URL")
    logger.info(f"[EdgeAPI] Trying to init EdgeState with URLs: {url}")

    if not url:
        logger.error("[EdgeAPI] No API URL configured in EdgeState")
        raise ValueError("No API URL configured in EdgeState")

    group_id = edge_state.get_comms_setting("group_id")

    try:
        logger.info(f"[EdgeAPI] Initializing EdgeState from {url}/edge/init?lanzone_id={group_id}")
        logger.info(f"[EdgeAPI] Using group_id: {group_id}")
        
        url = f"{url}/edge/init?lanzone_id={group_id}"

        logger.info(f"[EdgeAPI] Calling {url}")
        resp = requests.post(url, timeout=10, headers=_auth_headers(edge_state))
        logger.info(f"[EdgeAPI] Response: {resp.status_code} {resp.text}")

        if resp.status_code != 200:
            logger.error(f"[EdgeAPI] Failed: {resp.status_code} {resp.text}")
            raise RuntimeError("Edge init failed")

        payload = resp.json()

        # Overwrite EdgeState
        edge_state.update_settings(payload.get("settings", {}))

        edge_state.update_comms_settings(payload.get("lanzone", {}))

        for b in payload.get("batteries", []):
            edge_state.update_battery(b["consus_id"], b)

        for t in payload.get("tasks", []):
            edge_state.update_task(t["consus_id"], t)

        

        logger.info("[EdgeAPI] EdgeState initialized from API")
        logger.info(f"[EdgeAPI] Init succeeded with {url}")
        return
    
    except Exception as e:
        logger.warning(f"[EdgeAPI] Init failed for {url}: {e}")




def check_config(edge_state) -> bool:
    """
    Validates the current edge_state locally using Pydantic, 
    then POSTs it to the backend for external validation.
    """
    edge_dict = edge_state.to_dict()

    # Step 1: Local validation
    try:
        validated_payload = EdgeStatePayload.model_validate(edge_dict)
    
    except Exception as e:
        logger.error(f"[EdgeAPI] Local schema validation failed: {e}")
        return False

    # Step 2: Remote POST
    url = edge_state.get_comms_setting("API_URL") + edge_state.get_comms_setting("state_validation_endpoint")
    logger.info(f"[EdgeAPI] Validating EdgeState config at {url}")

    try:
        resp = requests.post(url, json=validated_payload.model_dump(mode="json"), timeout=10, headers=_auth_headers(edge_state))
        resp_data = resp.json() if resp.status_code == 200 else {}
        logger.info(f"[EdgeAPI] Validation response: {resp.status_code} {resp.text}")

        if resp_data.get('status') == "verified":
            logger.info("[EdgeAPI] EdgeState config is valid")
            return True
        else:
            logger.error(f"[EdgeAPI] Validation failed: {resp.status_code} {resp.text}")
            return False

    except Exception as e:
        logger.error(f"[EdgeAPI] Validation request failed: {e}")
        return False


# Safe, read-only, quick-to-read registers we already use elsewhere
READ_PROBES = {
    "meter_total_active_power": "36025",  # W (+/- import/export)
    "battery_soc": "37007",               # %
    "app_mode_display": "10405",          # enum-ish display (optional, but useful)
    "bms_alarm_bits": "39896",            # should be 0 in normal ops
    "bms_warning_bits": "39894",          # should be 0 in normal ops
    "ems_check_status": "40008",          # 1 (OK) on many firmwares
    "meter_path": "50091",                # internal/external path flag
}

def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> tuple[bool, float, str | None]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            dt = (time.perf_counter() - t0) * 1000.0
            return True, dt, None
    except Exception as e:
        return False, (time.perf_counter() - t0) * 1000.0, str(e)

def _plausible(sample: dict) -> tuple[bool, str | None]:
    """Minimal sanity checks so we can distinguish 'connected but garbage' from 'good'."""
    try:
        soc = sample.get("battery_soc")
        p = sample.get("meter_total_active_power")
        # battery_soc: 0..100
        if soc is None or not (0 <= float(soc) <= 100):
            return False, f"battery_soc out of range: {soc}"
        # meter power: allow large swings, but must be a number (avoid 0xFFFF/None)
        float(p)  # raises if NaN/None/str-junk
        # EMS/bms flags: if present and huge/None -> suspicious, but don't fail the whole test
        return True, None
    except Exception as e:
        return False, f"plausibility error: {e}"

def _read_snapshot(unit: BatteryUnit) -> dict:
    """Read a tiny, representative snapshot via your BatteryUnit helper."""
    # If you already have unit.read_telemetry() that includes the fields below, reuse it.
    # Here we call it and then subset; avoids multiple round-trips.
    t = unit.read_telemetry()
    return {
        "battery_soc": t.get("battery_soc"),
        "meter_total_active_power": t.get("meter_total_active_power"),
        "app_mode_display": t.get("app_mode_display"),
        "bms_alarm_bits": t.get("bms_alarm_bits"),
        "bms_warning_bits": t.get("bms_warning_bits"),
        "ems_check_status": t.get("ems_check_status"),
        "meter_path": t.get("meter_path"),
    }

def verify_modbus_connectivity(edge_state, consus_id: str | None = None) -> dict:
    """
    Verifies TCP + Modbus reachability and does a small read-only sanity check.
    Posts a compact result to the backend, unchanged from your current interface.
    """
    # Select batteries
    if consus_id:
        logger.info(f"[VERIFY] Testing Modbus for specific battery: {consus_id}")
        config = edge_state.get_battery_config(consus_id)
        batteries = {consus_id: config} if config else {}
    else:
        logger.info("[VERIFY] Testing Modbus for all batteries")
        batteries = edge_state.get_battery_configs()

    if not batteries:
        logger.warning("[VERIFY] No batteries found in EdgeState")
        return {"test_timestamp": datetime.utcnow().isoformat(), "results": {}}

    results = {}

    for cid, cfg in batteries.items():
        host = cfg.get("MODBUS_IP") or cfg.get("host")
        port = int(cfg.get("MODBUS_PORT") or cfg.get("port") or 502)
        unit_id = cfg.get("unit_id") or cfg.get("UNIT_ID") or 1

        logger.info(f"[VERIFY] {cid}: host={host} port={port} unit_id={unit_id}")

        status = {
            "reachable": False,
            "latency_ms": None,
            "modbus_ok": False,
            "values_ok": False,
            "error": None,
            "sample": None,
        }

        # 1) TCP probe (fast, clear error if port/firewall wrong)
        ok, rtt_ms, err = _tcp_probe(host, port, timeout=2.0)
        status["reachable"] = ok
        status["latency_ms"] = round(rtt_ms, 1)
        if not ok:
            status["error"] = f"tcp_error: {err}"
            results[cid] = status
            logger.warning(f"[VERIFY] {cid}: TCP failed: {err}")
            continue

        # 2) Modbus snapshot (single connect-read-disconnect)
        try:
            unit = BatteryUnit(consus_id=cid, register_map=edge_state.get_register_map(), config=cfg)
            unit.connect()
            # quick read
            snap1 = _read_snapshot(unit)
            # optional: second read to ensure we see movement in power (not strictly required)
            time.sleep(0.2)
            snap2 = _read_snapshot(unit)
            unit.disconnect()
            status["modbus_ok"] = True
            # choose the second if looks "more alive"
            status["sample"] = snap2 if snap2.get("meter_total_active_power") != snap1.get("meter_total_active_power") else snap1
            # 3) plausibility checks
            ok2, perr = _plausible(status["sample"])
            status["values_ok"] = ok2
            if not ok2:
                status["error"] = perr
        except Exception as e:
            status["error"] = f"modbus_error: {e}"
            logger.exception(f"[VERIFY] {cid}: Modbus failure: {e}")

        # Collapsed TRUE/FALSE result for your existing backend, but keep detail locally
        results[cid] = "TRUE" if (status["reachable"] and status["modbus_ok"] and status["values_ok"]) else "FALSE"
        # Also log a concise human message
        if results[cid] == "TRUE":
            logger.info(f"[VERIFY] {cid}: OK (rtt={status['latency_ms']}ms, soc={status['sample'].get('battery_soc')}, grid_w={status['sample'].get('meter_total_active_power')})")
        else:
            logger.warning(f"[VERIFY] {cid}: FAIL ({status['error']})")

    final_payload = {
        "test_timestamp": datetime.utcnow().isoformat(),
        "results": results
    }

    # Post to backend (unchanged)
    try:
        base_url = edge_state.get_comms_setting("API_URL")
        endpoint = edge_state.get_comms_setting("modbus_validation_endpoint")
        if not base_url or not endpoint:
            logger.error("[VERIFY] Missing API_URL or modbus_validation_endpoint in EdgeState")
            return final_payload

        url = base_url + endpoint
        logger.info(f"[VERIFY] Posting Modbus results to {url}")
        resp = requests.post(url, json=final_payload, timeout=10, headers=_auth_headers(edge_state))
        logger.info(f"[VERIFY] Backend response: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"[VERIFY] Failed to post Modbus results to backend: {e}")

    return final_payload


