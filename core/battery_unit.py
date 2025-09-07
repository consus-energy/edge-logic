from modbus.modbus_registry import BatteryRegisterInterface

import logging
logger = logging.getLogger(__name__)

class BatteryUnit:
    def __init__(self, consus_id: str, register_map: dict, config: dict):
        self.consus_id = consus_id
        self.register_map = register_map
        self.config = config
        self.modbus = BatteryRegisterInterface(config, register_map)

        self.connected = False
        self.current_soc = config.get("initial_soc_percent", 50) / 100

    def connect(self):
        if not self.connected:
            try:
                self.modbus.connect()
                self.connected = True
                logger.debug(f"[{self.consus_id}] Modbus connected")
            except Exception as e:
                logger.warning(f"[{self.consus_id}] Connection failed: {e}")

    def disconnect(self):
        if self.connected:
            self.modbus.close()
            self.connected = False

    def read_telemetry(self):
        self.connect()
        include_pv = bool(self.config.get("pv_enabled", False))
        data = self.modbus.read_all(include_pv=include_pv)
        if data and "battery_soc" in data:
            self.current_soc = max(0.0, min(1.0, data["battery_soc"] / 100))
        
        # Aggregate PV only if enabled
        if include_pv:  #ONLY IF TRUE
            pv_power = 0
            for key in ("pv1_power","pv2_power","pv3_power","pv4_power","mppt_power_1","mppt_power_2","mppt_power_3","mppt_power_4","mppt_power_5"):
                val = data.get(key)
                if isinstance(val, (int,float)):
                    pv_power += val
            if pv_power:
                data["pv_power_total"] = pv_power
            # AC coupled PV
            ct2 = data.get("ct2_active_power")
            if isinstance(ct2,(int,float)):
                data["pv_power_total_ac_included"] = pv_power + ct2
        return data

    def read_demand(self):
        self.connect()
        try:
            return float(self.modbus.read_register(37107, timeout=2))
        except Exception as e:
            logger.warning(f"[{self.consus_id}] Demand read failed: {e}")
            return 0.0

    def dispatch(self, power_w: int): # USED ONLY FOR IDLING
        mode = 0 if power_w == 0 else (2 if power_w > 0 else 1)
        try:
            self.modbus.write_register(5001, mode)
            self.modbus.write_register(5000, abs(power_w))
            logger.info(f"[{self.consus_id}] Dispatched {power_w}W mode {mode}")
        except Exception as e:
            logger.error(f"[{self.consus_id}] Dispatch failed: {e}")

    # --- EMS helper API using register names ---
    def _get_address_by_name(self, name: str):
        for section in ("read_registers", "write_registers"):
            for reg in self.register_map.get(section, []):
                if reg.get("name") == name:
                    return reg.get("address")
        raise KeyError(f"Register name '{name}' not found")

    def safe_write(self, name: str, value: int):
        try:
            addr = self._get_address_by_name(name)
            self.modbus.write_register(addr, value)
        except Exception as e:
            logger.debug(f"[{self.consus_id}] safe_write {name} failed: {e}")

    def safe_read(self, name: str):
        addr = self._get_address_by_name(name)
        return self.modbus.read_register(addr)
