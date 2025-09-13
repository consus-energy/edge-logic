# bootstrap/edge_bootstrap.py
import os
import yaml
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def load_register_map():
    file_path = Path(__file__).resolve().parent / "register_map.json"
    try:
        with open(file_path, "r") as f:
            register_map = json.load(f)
            logger.info(f"[BOOTSTRAP] Loaded register map with {len(register_map)} entries")
            return register_map
    except Exception as e:
        raise RuntimeError(f"Failed to load register map: {e}") from e

# Keys your app expects (same as before)
REQUIRED_KEYS = [
    "api_base_url", "MQTT_BROKER_HOST", "MQTT_BROKER_PORT",
    "group_id", "KEEP_ALIVE", "ingest_endpoint",
    "state_validation_endpoint", "modbus_validation_endpoint",
    "MQTT_USER", "MQTT_PASSWORD", "API_KEY"
]

# Map ENV VAR -> (config_key, caster)
ENV_TO_KEY = {
    "api_base_url": ("api_base_url", str),
    "MQTT_BROKER_HOST": ("MQTT_BROKER_HOST", str),
    "MQTT_BROKER_PORT": ("MQTT_BROKER_PORT", int),
    "GROUP_ID": ("group_id", str),
    "KEEP_ALIVE": ("KEEP_ALIVE", int),
    "INGEST_ENDPOINT": ("ingest_endpoint", str),
    "STATE_VALIDATION_ENDPOINT": ("state_validation_endpoint", str),
    "MODBUS_VALIDATION_ENDPOINT": ("modbus_validation_endpoint", str),
    "MQTT_USER": ("MQTT_USER", str),
    "MQTT_PASSWORD": ("MQTT_PASSWORD", str),
    "API_KEY": ("API_KEY", str),
    # Optional extras:
    "EDGE_PI_IP": ("EDGE_PI_IP", str),
}

def _load_from_env() -> dict:
    cfg = {}
    for env_name, (key, caster) in ENV_TO_KEY.items():
        val = os.getenv(env_name)
        if val is None or val == "":
            continue
        try:
            cfg[key] = caster(val)
        except Exception as e:
            raise ValueError(f"Env var {env_name} invalid for {key}: {e}") from e
    return cfg

def _load_from_yaml(filepath: str) -> dict:
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r") as f:
        data = yaml.safe_load(f) or {}
    # Cast ints if YAML provided strings
    if "MQTT_BROKER_PORT" in data:
        data["MQTT_BROKER_PORT"] = int(data["MQTT_BROKER_PORT"])
    if "KEEP_ALIVE" in data:
        data["KEEP_ALIVE"] = int(data["KEEP_ALIVE"])
    return data

def load_bootstrap_config(filepath: str = "bootstrap/edge_config.yaml"):
    """
    Loads bootstrap config preferring environment (.env.edge passed to the container).
    Falls back to YAML, and merges with env taking precedence.
    """
    env_cfg = _load_from_env()
    yaml_cfg = _load_from_yaml(filepath)

    # Merge with env taking precedence
    config = {**yaml_cfg, **env_cfg}

    missing = [k for k in REQUIRED_KEYS if k not in config or config[k] in (None, "")]
    if missing:
        # Be explicit so misconfigs are obvious at boot
        locations = []
        if env_cfg: locations.append("ENV")
        if yaml_cfg: locations.append(filepath)
        where = " + ".join(locations) if locations else "ENV"
        raise ValueError(f"[BOOTSTRAP] Missing required keys from {where}: {missing}")

    reg = load_register_map()

    if env_cfg and not yaml_cfg:
        logger.info("[BOOTSTRAP] Loaded config from ENV (.env.edge)")
    elif env_cfg and yaml_cfg:
        logger.info(f"[BOOTSTRAP] Loaded config from ENV overriding {filepath}")
    else:
        logger.info(f"[BOOTSTRAP] Loaded config from {filepath}")

    return config, reg
