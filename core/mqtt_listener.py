import json
import logging
import ssl
import time
import paho.mqtt.client as mqtt

from core.edge_state import EDGE_STATE
from core.thread_manager import ThreadManager
from core.controller import BatteryController
from utils.backend_utils import BackendPoster
from core.battery_unit import BatteryUnit
from bootstrap.edge_api import verify_modbus_connectivity

logger = logging.getLogger(__name__)
thread_manager = ThreadManager()
poster = BackendPoster(interval_seconds=10)

def _default_ca_path():
    # macOS bundle, then Debian/Raspbian bundle
    for p in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        try:
            with open(p, "rb"):
                return p
        except Exception:
            continue
    return None

def _ensure_controller_running(consus_id: str, config: dict):
    # Start a controller thread if not already running
    if consus_id in thread_manager.list_active():
        return
    reg_map = getattr(EDGE_STATE, "register_map", {}) or {}
    unit = BatteryUnit(consus_id=consus_id, register_map=reg_map, config=config or {})
    controller = BatteryController(unit=unit, consus_id=consus_id)
    thread_manager.start_battery_thread(controller, poster)
    logger.info("[MQTT] Controller thread started for %s", consus_id)

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        topic = EDGE_STATE.get_comms_setting("MQTT_TOPIC") or "lanzone/LANZ-001/updates"
        client.subscribe(topic, qos=1)
        logger.info("[MQTT] Connected. Subscribed to %s", topic)
    else:
        logger.error("[MQTT] Connection failed rc=%s", rc)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception as e:
        logger.warning("[MQTT] Bad JSON on %s: %s", msg.topic, e)
        return

    msg_type = payload.get("type")
    data = payload.get("data") or {}
    consus_id = payload.get("consus_id")

    if msg_type == "settings":
        EDGE_STATE.update_settings(data)
        logger.info("[MQTT] Settings updated")

    elif msg_type == "battery_config" and consus_id:
        EDGE_STATE.update_battery(consus_id, data)
        logger.info("[MQTT] Battery config updated: %s", consus_id)
        _ensure_controller_running(consus_id, data)

    elif msg_type == "battery_add" and consus_id:
        EDGE_STATE.update_battery(consus_id, data)
        _ensure_controller_running(consus_id, data)
        logger.info("[MQTT] Battery added: %s", consus_id)

    elif msg_type == "battery_remove" and consus_id:
        thread_manager.stop_battery_thread(consus_id)
        EDGE_STATE.battery_configs.pop(consus_id, None)
        logger.info("[MQTT] Battery removed: %s", consus_id)

    elif msg_type == "task" and consus_id:
        EDGE_STATE.update_task(consus_id, data)
        logger.info("[MQTT] Task updated for %s", consus_id)

    elif msg_type == "ping":
        logger.info("[MQTT] Ping received → pong")
        client.publish(msg.topic.replace("ping", "pong"),
                       json.dumps({"type": "pong"}), qos=1, retain=False)

    elif msg_type == "test_modbus" and consus_id:
        try:
            verify_modbus_connectivity(EDGE_STATE, consus_id)
        except Exception as e:
            logger.error("[MQTT] Modbus test error for %s: %s", consus_id, e)

    else:
        logger.warning("[MQTT] Unknown type=%s; topic=%s", msg_type, msg.topic)

def start_mqtt_listener():
    broker_host = EDGE_STATE.get_comms_setting("MQTT_BROKER_HOST") or "ibce3b30.ala.eu-central-1.emqxsl.com"
    broker_port = int(EDGE_STATE.get_comms_setting("MQTT_BROKER_PORT") or 8883)
    mqtt_user   = EDGE_STATE.get_comms_setting("MQTT_USER")
    mqtt_pass   = EDGE_STATE.get_comms_setting("MQTT_PASSWORD")
    keepalive   = int(EDGE_STATE.get_comms_setting("KEEP_ALIVE") or 60)

    client = mqtt.Client(
        client_id=f"edge-{EDGE_STATE.get_comms_setting('group_id') or 'unknown'}-{int(time.time())}",
        protocol=mqtt.MQTTv311
    )

    if mqtt_user:
        client.username_pw_set(mqtt_user, mqtt_pass)

    if broker_port == 8883:
        ca = _default_ca_path()
        if ca:
            client.tls_set(ca_certs=ca, cert_reqs=ssl.CERT_REQUIRED)
        else:
            # As a last resort, still try TLS without explicit CA (system defaults)
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(broker_host, broker_port, keepalive)
    client.loop_start()

    logger.info("[MQTT] Listener running (host=%s port=%s tls=%s)",
                broker_host, broker_port, broker_port == 8883)
    return client
