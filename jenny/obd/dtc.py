"""Decodifica DTC e monitor di prontezza standard."""

from collections.abc import Iterable
from typing import Any


def decode_dtc(payloads: Iterable[bytes]) -> list[str]:
    codes = set()
    for payload in payloads:
        if not payload or payload[0] not in (0x43, 0x47, 0x4A):
            raise ValueError("Expected Mode 03, 07 or 0A response")
        data = payload[1:]
        # CAN può includere il conteggio DTC prima delle coppie di byte.
        if len(data) % 2:
            count, data = data[0], data[1:]
            if count * 2 > len(data):
                raise ValueError("Truncated DTC response")
            data = data[: count * 2]
        for offset in range(0, len(data), 2):
            a, b = data[offset : offset + 2]
            if a or b:
                codes.add(f"{'PCBU'[a >> 6]}{(a >> 4) & 3:X}{a & 15:X}{b:02X}")
    return sorted(codes)


def decode_readiness(payload: bytes) -> dict[str, Any]:
    if len(payload) < 6 or payload[:2] != b"\x41\x01":
        raise ValueError("Expected Mode 01 PID 01 with four data bytes")
    a, b, c, d = payload[2:6]
    monitors = {}
    for bit, name in enumerate(("misfire", "fuel_system", "comprehensive_components")):
        supported = bool(b & (1 << bit))
        monitors[name] = {
            "supported": supported,
            "complete": not bool(b & (1 << (bit + 4))) if supported else None,
        }
    compression = bool(b & 8)
    names = (
        {
            0: "nmhc_catalyst",
            1: "nox_scr",
            3: "boost_pressure",
            5: "exhaust_gas_sensor",
            6: "particulate_filter",
            7: "egr_vvt",
        }
        if compression
        else {
            0: "catalyst",
            1: "heated_catalyst",
            2: "evaporative_system",
            3: "secondary_air",
            4: "ac_refrigerant",
            5: "oxygen_sensor",
            6: "oxygen_sensor_heater",
            7: "egr_vvt",
        }
    )
    for bit, name in names.items():
        supported = bool(c & (1 << bit))
        monitors[name] = {
            "supported": supported,
            "complete": not bool(d & (1 << bit)) if supported else None,
        }
    return {
        "mil_on": bool(a & 0x80),
        "stored_dtc_count": a & 0x7F,
        "ignition": "compression" if compression else "spark",
        "monitors": monitors,
    }
