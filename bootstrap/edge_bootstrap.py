import yaml
import os
import json
from pathlib import Path



import logging
logger = logging.getLogger(__name__)



def load_register_map():
    file_path = Path(__file__).resolve().parent / "register_map.json"
    try:
        with open(file_path, "r") as f:
            register_map = json.load(f)

            logger.info(f"[BOOTSTRAP] Loaded register map with {(register_map)} entries")
           
            return register_map

    except Exception as e:
        print(f"Failed to load register map: {e}")

def load_bootstrap_config(filepath="bootstrap/edge_config.yaml"):
    """
    Loads static bootstrap config for the Edge device.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Bootstrap config not found at {filepath}")

    with open(filepath) as f:
        config = yaml.safe_load(f)

    required_keys = ["api_base_url", "MQTT_BROKER_HOST", "MQTT_BROKER_PORT", 
                     "group_id", "KEEP_ALIVE", "ingest_endpoint", "state_validation_endpoint",
                     "modbus_validation_endpoint", "MQTT_USER", "MQTT_PASSWORD", "API_KEY"]

    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required bootstrap key: {key}")

    reg = load_register_map()

    return config, reg

