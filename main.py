import time

import logging
import logging.config
from logging_config import LOGGING_CONFIG  

import os


# --- Container-friendly logging to STDOUT ---
if os.getenv("LOG_TO_STDOUT", "1") == "1":
    # Simple, robust console logging; no dictConfig used.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logger = logging.getLogger(__name__)
    logger.info("Logging configured for STDOUT (basicConfig).")
else:
    # Fall back to your existing dictConfig setup (file handlers etc.)
    import logging.config
    from logging_config import LOGGING_CONFIG
    logging.config.dictConfig(LOGGING_CONFIG)
    logger = logging.getLogger(__name__)
    logger.info("Logging configured via dictConfig.")



from utils.backend_utils import BackendPoster
from core.battery_unit import BatteryUnit
from core.controller import BatteryController
from core.edge_state import EDGE_STATE
from core.mqtt_listener import start_mqtt_listener
from core.thread_manager import ThreadManager
from battery_opt.safety_check import SafetyCheck

from bootstrap.edge_bootstrap import load_bootstrap_config
from bootstrap.edge_api import init_edge_state, check_config, verify_modbus_connectivity


def get_mqtt_topic(lanzone_id: str) -> str:
    return f"lanzone/{lanzone_id}/updates"


# === Single ThreadManager for dynamic batteries ===
thread_manager = ThreadManager()

def main():
    # Load static config
    bootstrap, reg = load_bootstrap_config()
    logger.info(f"[BOOTSTRAP] Loaded register map with {(reg)} entries")
    topic = get_mqtt_topic(bootstrap["group_id"])

    # SCHEMA VALIDATION
    EDGE_STATE.update_comms_settings({
        "api_base_url": bootstrap["api_base_url"],
        "MQTT_BROKER_HOST": bootstrap["MQTT_BROKER_HOST"],
        "MQTT_BROKER_PORT": bootstrap["MQTT_BROKER_PORT"],
        "MQTT_USER": bootstrap["MQTT_USER"],
        "MQTT_PASSWORD": bootstrap["MQTT_PASSWORD"],
        "group_id": bootstrap["group_id"],
        "KEEP_ALIVE": bootstrap["KEEP_ALIVE"],
        "MQTT_TOPIC": topic,
        "state_validation_endpoint": bootstrap["state_validation_endpoint"],
        "modbus_validation_endpoint": bootstrap["modbus_validation_endpoint"],
        "EDGE_PI_IP": bootstrap.get("EDGE_PI_IP", None),
        "ingest_endpoint": bootstrap["ingest_endpoint"],
        "API_KEY": bootstrap["API_KEY"]
    })

    EDGE_STATE.set_register_map(reg)
    logger.info("[BOOTSTRAP] EdgeState seeded with static config")

    # Pull dynamic config with fallback logic
    try:
        init_edge_state(EDGE_STATE)
        logger.info("[BOOTSTRAP] EdgeState pulled from /edge/init")
         
        #check config is crrect through post
        
         # TEMP removed
        #x = check_config(EDGE_STATE)
        #logger.info(f"[BOOTSTRAP] EdgeState validation result: {x}")
        #y = verify_modbus_connectivity(EDGE_STATE)
        #logger.info(f"[BOOTSTRAP] Modbus connectivity verified: {y}")

    
    
    except Exception:
        logger.warning("[BOOTSTRAP] /edge/init failed")

        

    start_mqtt_listener()
    logger.info("[BOOTSTRAP] MQTT listener started")

    poster = BackendPoster(interval_seconds=10)
    active_threads = {}

    try:
        while True:
            edge_status = EDGE_STATE.settings.get("edge_status")
            logger.debug(f"[LOOP] Current edge_status: {edge_status}")

            if edge_status == "active":
                
                if not poster.is_active():
                    poster.start()

                    logger.info("[LOOP] Started backend poster.")
                else:
                    logger.debug("[LOOP] Backend poster already active.")

                logger.info("[LOOP] Starting battery threads")
                for consus_id, battery_config in EDGE_STATE.battery_configs.items():
                    if consus_id in active_threads:
                        continue
                    unit = BatteryUnit(
                        consus_id=consus_id,
                        register_map=EDGE_STATE.get_register_map(),
                        config=battery_config
                    )
                    health = SafetyCheck(unit, consus_id, poll_hz=1.0)
                    health.start()
                    controller = BatteryController(unit=unit, consus_id=consus_id, health_monitor=health)
                    thread_manager.start_battery_thread(controller, poster)
                    active_threads[consus_id] = controller

            elif edge_status in ["paused", "inactive"]:
                
                if poster.is_active():
                    poster.stop()

                for consus_id in thread_manager.list_active():
                    thread_manager.stop_battery_thread(consus_id)


                logger.info(f"[LOOP] Edge status changed to {edge_status.upper()} — Stopped all threads.")

            time.sleep(5)  # Polling interval

    except KeyboardInterrupt:
        poster.stop()
        thread_manager.stop_all()
        logger.info("[LOOP] Graceful shutdown requested.")

if __name__ == "__main__":
    main()
