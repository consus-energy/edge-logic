import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Required config keys (env var names must match these exactly)
REQUIRED_KEYS = [
    "api_base_url",
    "MQTT_BROKER_HOST",
    "MQTT_BROKER_PORT",
    "group_id",
    "KEEP_ALIVE",
    "ingest_endpoint",
    "state_validation_endpoint",
    "modbus_validation_endpoint",
    "MQTT_USER",
    "MQTT_PASSWORD",
    "API_KEY",
]

# Keys that may be intentionally blank (don’t fail if empty string)
ALLOW_EMPTY = {"MQTT_USER", "MQTT_PASSWORD", "EDGE_PI_IP"}

# Type casting for numeric fields
CAST = {
    "MQTT_BROKER_PORT": int,
    "KEEP_ALIVE": int,
}

def load_register_map():
    file_path = Path(__file__).resolve().parent / "register_map.json"
    try:
        with open(file_path, "r") as f:
            register_map = json.load(f)
        logger.info(f"[BOOTSTRAP] Loaded register map with {len(register_map)} entries")
        return register_map
    except Exception as e:
        raise RuntimeError(f"Failed to load register map: {e}") from e

def load_bootstrap_config(_: str = None):
    """
    ENV-only bootstrap:
      - Reads REQUIRED_KEYS from environment (names must match exactly)
      - Casts numeric fields
      - Allows some keys to be empty (ALLOW_EMPTY)
      - Raises if any required key is missing
    Returns: (config_dict, register_map)
    """
    cfg, missing = {}, []

    for key in REQUIRED_KEYS:
        val = os.getenv(key)
        if val is None or (val == "" and key not in ALLOW_EMPTY):
            missing.append(key)
            continue

        if key in CAST and val != "":
            try:
                val = CAST[key](val)
            except Exception as e:
                raise ValueError(f"[BOOTSTRAP] Invalid value for {key!r}: {val!r} ({e})") from e

        cfg[key] = val

    if missing:
        raise ValueError(f"[BOOTSTRAP] Missing required keys from ENV: {missing}")

    logger.info("[BOOTSTRAP] Loaded config from ENV (.env.edge)")
    reg = load_register_map()
    return cfg, reg
