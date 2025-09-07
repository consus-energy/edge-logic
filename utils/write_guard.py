import time
import threading
import logging

logger = logging.getLogger(__name__)

class WriteGuard:
    """Global write discipline: dedupe + rate limiting per register and globally.
    Policies (defaults):
      * Min interval per register: 0.25 s
      * Max total writes per second: 5
      * Only write when value changes.
    """
    MIN_INTERVAL_PER_REGISTER = 0.25
    MAX_WRITES_PER_SEC = 5

    _lock = threading.Lock()
    _last_value: dict[int,int] = {}
    _last_write_ts: dict[int,float] = {}
    _window_start: float = 0.0
    _window_count: int = 0

    @classmethod
    def attempt(cls, address: int, value: int, write_callable) -> bool:
        now = time.time()
        with cls._lock:
            # Global window accounting (1-second rolling window)
            if now - cls._window_start >= 1.0:
                cls._window_start = now
                cls._window_count = 0

            last_val = cls._last_value.get(address)
            if last_val == value:
                logger.debug(f"[WriteGuard] Skip dedupe addr {address} val {value}")
                return False

            last_ts = cls._last_write_ts.get(address, 0)
            if now - last_ts < cls.MIN_INTERVAL_PER_REGISTER:
                logger.debug(f"[WriteGuard] Throttle interval addr {address}")
                return False

            if cls._window_count >= cls.MAX_WRITES_PER_SEC:
                logger.warning("[WriteGuard] Global write rate limit reached; dropping write")
                return False

            # Accept write
            try:
                write_callable()
                cls._last_value[address] = value
                cls._last_write_ts[address] = now
                cls._window_count += 1
                return True
            except Exception as e:
                logger.error(f"[WriteGuard] Write failed addr {address}: {e}")
                return False
