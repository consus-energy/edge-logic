from pymodbus.client.sync import ModbusTcpClient  
import logging
from typing import Any, Dict, Union
from utils.write_guard import WriteGuard

logger = logging.getLogger(__name__)

class BatteryRegisterInterface:
    def __init__(self, battery: Dict, register_map: Dict):

        self.ip = battery["MODBUS_IP"]
        self.port = battery.get("MODBUS_PORT", 15002)
        self.unit_id = battery.get("unit_id", 1)
        self.register_map = register_map

        self.client = ModbusTcpClient(self.ip, port=self.port)
        self.registers = self._flatten_registers()

        logger.info(f"Initialized Modbus interface at {self.ip}:{self.port} (unit ID {self.unit_id})")

    def _flatten_registers(self) -> Dict[int, Dict[str, Any]]:
        flat = {}
        for reg in self.register_map.get("read_registers", []) + self.register_map.get("write_registers", []):
            flat[reg["address"]] = reg
        logger.debug(f"Flattened {len(flat)} Modbus registers.")
        return flat

    def connect(self) -> bool:
        logger.debug("Attempting Modbus TCP connection")
        success = self.client.connect()
        if success:
            logger.info(f"Connected to Modbus device at {self.ip}:{self.port}")
        else:
            logger.error(f"Failed to connect to Modbus device at {self.ip}:{self.port}")
        return success

    def close(self):
        logger.debug("Closing Modbus TCP connection")
        self.client.close()

    def read_register(self, address: int) -> Union[int, float]:
        reg = self.registers.get(address)
        if not reg:
            logger.error(f"Tried to read unknown register address: {address}")
            raise KeyError(f"Register {address} not defined in map.")

        try:
            result = self.client.read_holding_registers(address,1,unit=self.unit_id)
            if result.isError():
                raise IOError(f"Modbus read error at {address}")

            raw = result.registers[0]
            if reg["signed"] and raw > 32767:
                raw -= 65536

            logger.debug(f"Read from {reg['name']} (addr {address}): {raw} {reg.get('unit', '')}")
            return raw

        except Exception as e:
            logger.error(f"Failed to read register {address}: {e}", exc_info=True)
            raise

    def write_register(self, address: int, value: Union[int, float]):
        reg = self.registers.get(address)
        if not reg:
            logger.error(f"Tried to write to unknown register address: {address}")
            raise KeyError(f"Register {address} not defined in map.")
        if reg["type"] not in {"int16", "uint16"}:
            logger.error(f"Unsupported register type for write: {reg['type']} at address {address}")
            raise TypeError(f"Unsupported type for write: {reg['type']}")

        val_int = int(value)
        def _do_write():
            result = self.client.write_register(address, val_int, unit=self.unit_id)
            if result.isError():
                raise IOError(f"Modbus write error at {address}")
            logger.debug(f"Wrote {val_int} to {reg['name']} (addr {address})")
        if not WriteGuard.attempt(address, val_int, _do_write):
            return

    def _is_pv_register(self, name: str) -> bool:
        """
        Heuristic to identify PV-related registers by name so we can skip them
        when PV is disabled. Covers pv*, mppt_power_*, and AC-coupled PV CT2.
        """
        if not name:
            return False
        if name.startswith("pv"):
            return True
        if name.startswith("mppt_power_"):
            return True
        if name in {"ct2_active_power"}:
            return True
        return False

    def read_all(self, include_pv: bool = True) -> Dict[str, Union[int, float]]:
        """
        Read all configured holding registers.

        Parameters
        - include_pv: if False, skip PV-related registers to reduce Modbus I/O time
                      (useful when PV is disabled in site config).
        """
        values: Dict[str, Union[int, float]] = {}
        for reg in self.register_map.get("read_registers", []):
            name = reg.get("name")
            if not include_pv and self._is_pv_register(name):
                logger.debug(f"Skipping PV register '{name}' due to include_pv=False")
                continue
            try:
                values[name] = self.read_register(reg["address"])
            except Exception as e:
                values[name] = None
                logger.warning(f"Skipped {name} at {reg['address']}: {e}")
        logger.info("Completed full register read.")
        return values
