import json
import logging
import paho.mqtt.client as mqtt

from core.edge_state import EDGE_STATE
from core.thread_manager import ThreadManager
from core.controller import BatteryController
from utils.backend_utils import BackendPoster
from core.battery_unit import BatteryUnit
from bootstrap.edge_api import verify_modbus_connectivity

logger = logging.getLogger(__name__)
thread_manager = ThreadManager()  # Share this if you want singleton

poster = BackendPoster(interval_seconds=10)  # Could also share one instance

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        topic = EDGE_STATE.get_comms_setting("MQTT_TOPIC")
        client.subscribe(topic, qos=1)
        logger.info(f"[MQTT] Subscribed to {topic}")
    else:
        logger.error(f"[MQTT] Connection failed with code {rc}")

def on_message(client, userdata, msg):

    logger.debug(f"[MQTT] Received message on {msg.topic}: {msg.payload}")
    payload = json.loads(msg.payload)
    msg_type = payload.get("type")
    data = payload.get("data")
    consus_id = payload.get("consus_id")

    register_map = EDGE_STATE.get_register_map()


    if msg_type == "settings":
        EDGE_STATE.update_settings(data)
        logger.info("[MQTT] Updated settings via push")
    
    # comms settings are static
    
    elif msg_type == "battery_config" and consus_id:
        EDGE_STATE.update_battery(consus_id, data)
        logger.info(f"[MQTT] Updated battery config: {consus_id}")

    elif msg_type == "battery_add" and consus_id:
        EDGE_STATE.update_battery(consus_id, data)
        unit = BatteryUnit(
            consus_id=consus_id,
            register_map=register_map,
            config=data
        )

        controller = BatteryController(unit=unit, consus_id=consus_id)
        thread_manager.start_battery_thread(controller, poster)
        logger.info(f"[MQTT] Added new battery: {consus_id}")

    elif msg_type == "battery_remove" and consus_id:
        thread_manager.stop_battery_thread(consus_id)
        EDGE_STATE.battery_configs.pop(consus_id, None)
        logger.info(f"[MQTT] Removed battery: {consus_id}")

    elif msg_type == "task" and consus_id:
        EDGE_STATE.update_task(consus_id, data)
        logger.info(f"[MQTT] Updated task: {consus_id}")


        

    elif msg_type == "test_modbus" and consus_id :

        try:
            verify_modbus_connectivity(EDGE_STATE, consus_id)
            
        except Exception as e:
            logger.error(f"[MQTT] Error testing Modbus for {consus_id}: {e}")
            
           
    else:
        logger.warning(f"[MQTT] Unknown payload type: {msg_type}, no action taken")

def start_mqtt_listener():
    broker_host = EDGE_STATE.get_comms_setting("MQTT_BROKER_HOST")
    broker_port = EDGE_STATE.get_comms_setting("MQTT_BROKER_PORT")

    keepalive = EDGE_STATE.get_comms_setting("KEEP_ALIVE")

    client = mqtt.Client(client_id=f"edge_listener{EDGE_STATE.get_comms_setting('group_id')}")
   

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(broker_host, broker_port, keepalive)
    client.loop_start()

    logger.info("[MQTT] Listener running with retain=True enforced on backend")
    return client
