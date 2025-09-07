import logging
import requests
import time
import threading
import queue
from core.edge_state import EDGE_STATE
from utils.serialize_datetimes import serialize_datetimes
from schemas.alerts import AlertEvent
from schemas.telemetry import TelemetryPayload  # adjust path as needed



  

import logging
logger = logging.getLogger(__name__)
    
def post_to_backend(data: list):
    try:
        if not data:
            logger.warning("[POSTER] No data to post to backend")
            return 0
        # Serialize datetimes in the data

        data = serialize_datetimes(data)
        url = EDGE_STATE.comms_settings["API_URL"] + EDGE_STATE.comms_settings["ingest_endpoint"]

        response = requests.post(url, json=data)
        if response.status_code != 200:
            logger.error(f"Failed to post data to backend: {response.status_code} - {response.text}")
            raise Exception(f"Backend returned error: {response.status_code} - {response.text}")
        logger.debug(f"Data posted successfully: {response.json()}")
        return 0

    except Exception as e:
        logger.exception(f"Exception during post_to_backend: {e}")
        raise



class BackendPoster:
    def __init__(self, interval_seconds=10):
        self.interval = interval_seconds
        self.data_queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._started = False  # <- NEW

    def start(self):
        if not self._started:
            self._thread.start()
            self._started = True
            logger.info(f"BackendPoster started with interval {self.interval}s")
        else:
            logger.debug("BackendPoster already started.")

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join()
        logger.info("BackendPoster stopped")
        self._started = False

    def is_active(self):
        return self._started #and not self._stop_event.is_set()
    
    def add_data(self, data: TelemetryPayload):
        if not isinstance(data, TelemetryPayload):
            raise TypeError("Only TelemetryPayload instances are accepted")
        self.data_queue.put(data)
        logger.debug("Data added to queue")

    def _worker(self):
        while not self._stop_event.is_set():
            time.sleep(self.interval)
            batch = []

            while not self.data_queue.empty():
                try:
                    item = self.data_queue.get_nowait()
                    batch.append(item.model_dump())  
                except queue.Empty:
                    break

            if batch:
                try:
                    post_to_backend(batch)
                    logger.info(f"Posted batch of {len(batch)} data points")
                except Exception as e:
                    logger.error(f"Failed to post batch: {e}")


# --- Health / Alerts Posting ---
def post_health_alerts(alerts: list[dict]):
    """POST health/alert events to /blob/health (or overridden comms setting).
    Accepts list of alert dicts (already lightweight). Adds basic serialization.
    """
    if not alerts:
        return 0
    try:
        # Validate and normalize schema using AlertEvent; drop invalid items
        validated: list[dict] = []
        invalid = 0
        for a in alerts:
            try:
                evt = AlertEvent.model_validate(a)
                validated.append(evt.model_dump(mode="json"))
            except Exception as ve:
                invalid += 1
                logger.warning(f"Dropping invalid alert payload: {ve}; data={a}")

        if not validated:
            logger.warning("No valid alerts to post after schema validation")
            return 0

        alerts = serialize_datetimes(validated)
        base = EDGE_STATE.comms_settings.get("API_URL")
        if not base:
            raise RuntimeError("API_URL not configured in EDGE_STATE.comms_settings")
        endpoint = EDGE_STATE.comms_settings.get("health_endpoint", "/blob/health")
        url = base + endpoint
        resp = requests.post(url, json=alerts, timeout=10)
        if resp.status_code // 100 != 2:
            logger.error(f"Health post failed {resp.status_code}: {resp.text}")
            return 1
        posted = len(alerts)
        if invalid:
            logger.info(f"Posted {posted} health alerts (dropped {invalid} invalid)")
        else:
            logger.debug(f"Posted {posted} health alerts -> {endpoint}")
        return 0
    except Exception as e:
        logger.exception(f"Exception during post_health_alerts: {e}")
        return 1
