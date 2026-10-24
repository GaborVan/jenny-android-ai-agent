"""Conversioni dei PID live; i PID sconosciuti conservano i byte originali."""

from typing import Any

OBD_STANDARDS = {
    1: "OBD-II (CARB)",
    2: "OBD (EPA)",
    3: "OBD and OBD-II",
    4: "OBD-I",
    5: "Not OBD compliant",
    6: "EOBD",
    7: "EOBD and OBD-II",
    8: "EOBD and OBD",
    9: "EOBD, OBD and OBD-II",
    10: "JOBD",
    11: "JOBD and OBD-II",
    12: "JOBD and EOBD",
    13: "JOBD, EOBD and OBD-II",
}


def decode_pid(pid: int, data: bytes) -> dict[str, Any]:
    names = {
        0x0C: ("engine_speed", "rpm", 2),
        0x0D: ("vehicle_speed", "km/h", 1),
        0x05: ("coolant_temperature", "°C", 1),
        0x0F: ("intake_air_temperature", "°C", 1),
        0x10: ("mass_air_flow", "g/s", 2),
        0x11: ("throttle", "%", 1),
        0x2F: ("fuel_level", "%", 1),
        0x42: ("control_module_voltage", "V", 2),
        0x1C: ("obd_standard", None, 1),
    }
    result: dict[str, Any] = {"pid": f"{pid:02X}", "raw": list(data)}
    if pid not in names:
        return result
    name, unit, size = names[pid]
    if len(data) < size:
        raise ValueError(f"Truncated data for PID {pid:02X}: need {size} bytes")
    a = data[0]
    ab = int.from_bytes(data[:2], "big")
    value: Any
    if pid == 0x0C:
        value = ab / 4
    elif pid == 0x10:
        value = ab / 100
    elif pid == 0x42:
        value = ab / 1000
    elif pid in (0x05, 0x0F):
        value = a - 40
    elif pid in (0x11, 0x2F):
        value = a * 100 / 255
    elif pid == 0x1C:
        value = OBD_STANDARDS.get(a, f"OBD standard 0x{a:02X}")
    else:
        value = a
    return {**result, "name": name, "value": value, "unit": unit}
