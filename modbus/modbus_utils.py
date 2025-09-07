import logging

logger = logging.getLogger(__name__)

def set_battery_power(modbus, power_w: int):
    """
    Safely sets battery power and mode on a GoodWe inverter.

    Args:
        modbus: An instance of BatteryRegisterInterface
        power_w: Desired power in watts (positive = discharge, negative = charge)
    """
    try:
        if power_w == 0:
            mode = 0  # Auto
            logger.info("Setting battery mode to AUTO (0 W)")
        elif power_w > 0:
            mode = 2  # Discharge
            logger.info(f"Setting battery to DISCHARGE mode: {power_w} W")
        else:
            mode = 1  # Charge
            logger.info(f"Setting battery to CHARGE mode: {abs(power_w)} W")
            power_w = abs(power_w)

        modbus.write_register(5001, mode)
        logger.debug(f"Register 5001 <- Mode: {mode}")

        modbus.write_register(5000, int(power_w))
        logger.debug(f"Register 5000 <- Power: {int(power_w)} W")

    except Exception as e:
        logger.error(f"Failed to set battery power ({power_w} W): {e}", exc_info=True)
