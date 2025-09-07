import threading
import logging
import time

logger = logging.getLogger(__name__)

class ThreadManager:
    """
    Manages BatteryController threads dynamically.
    """
    def __init__(self):
        self.threads = {}       # consus_id -> Thread
        self.controllers = {}   # consus_id -> BatteryController instance
        self.running_flags = {} # consus_id -> threading.Event for stop control

    def start_battery_thread(self, controller, poster, interval_s=1.0):
        """
        Start a BatteryController thread for given consus_id if not already running.
        """
        consus_id = controller.consus_id
        if consus_id in self.threads:
            logger.info(f"[ThreadManager] Thread already running for {consus_id}")
            return

        # Create a stop flag for this thread
        stop_event = threading.Event()
        self.running_flags[consus_id] = stop_event

        def battery_thread_runner():
            logger.info(f"[ThreadManager] Starting thread loop for {consus_id}")
            while not stop_event.is_set():
                start_time = time.time()
                
                result = controller.run_once()
                poster.add_data(result)

                elapsed = time.time() - start_time
                if elapsed < interval_s:
                    time.sleep(interval_s - elapsed)
                else:
                    logger.warning(f"[{consus_id}] Loop overran by {elapsed - interval_s:.3f}s")

            logger.info(f"[ThreadManager] Thread loop stopped for {consus_id}")

        t = threading.Thread(target=battery_thread_runner, daemon=True)
        t.start()

        self.threads[consus_id] = t
        self.controllers[consus_id] = controller
        logger.info(f"[ThreadManager] Battery thread started for {consus_id}")

    def stop_battery_thread(self, consus_id):
        """
        Signal a running BatteryController thread to stop.
        """
        if consus_id not in self.threads:
            logger.warning(f"[ThreadManager] No thread to stop for {consus_id}")
            return

        # Signal the thread to stop
        self.running_flags[consus_id].set()

        # Optional: Wait for thread to join (not strictly required for daemon)
        # self.threads[consus_id].join(timeout=5)

        # Clean up
        del self.threads[consus_id]
        del self.controllers[consus_id]
        del self.running_flags[consus_id]
        logger.info(f"[ThreadManager] Stopped thread for {consus_id}")

    def stop_all(self):
        """
        Stop all running BatteryController threads.
        """
        logger.info("[ThreadManager] Stopping all battery threads")
        for consus_id in list(self.threads.keys()):
            self.stop_battery_thread(consus_id)
        logger.info("[ThreadManager] All battery threads stopped")


    def list_active(self):
        """
        Return list of currently active battery consus_ids.
        """
        return list(self.threads.keys())
