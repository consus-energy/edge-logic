from typing import Optional
from pydantic import BaseModel


class CommsSettings(BaseModel):
    api_base_url: str = "http://localhost:8000"
    ingest_endpoint: str = "/blob/ingest"
    state_validation_endpoint: str = "/edge/validate-state"
    modbus_validation_endpoint: str = "/edge/validate-modbus"
    MQTT_BROKER_HOST: str = "localhost"
    MQTT_BROKER_PORT: int = 1883
    MQTT_TOPIC: str = "lanzone/lanzone-1/updates"
    group_id: str = "lanzone-1"
    KEEP_ALIVE: int = 60
    EDGE_PI_IP: Optional[str] = None
