"""Verifica del protocollo senza hardware."""

import pytest

from jenny.obd.dtc import decode_dtc, decode_readiness
from jenny.obd.elm327 import ELM327, ELM327Error
from jenny.obd.pids import decode_pid
from jenny.obd.transport import MockTransport


def test_init():
    transport = MockTransport(["ATZ\rELM327 v1.5\r>"] + ["OK>"] * 4)
    ELM327(transport).initialize()
    assert transport.writes == ["ATZ", "ATE0", "ATL0", "ATS0", "ATSP0"]


def test_echo_searching_multiline():
    elm = ELM327(MockTransport(["03\rSEARCHING...\r43 03 01 04 20\r43 C1 00 00 00\r>"]))
    assert decode_dtc(elm.query("03")) == ["P0301", "P0420", "U0100"]


@pytest.mark.parametrize("mode", [0x43, 0x47, 0x4A])
def test_codes_dedup_padding_and_families(mode):
    assert decode_dtc([bytes([mode]) + bytes.fromhex("03010420C1000000")] * 2) == [
        "P0301",
        "P0420",
        "U0100",
    ]
    assert decode_dtc([bytes([mode]) + bytes.fromhex("400180AF")]) == ["B00AF", "C0001"]
    assert decode_dtc([bytes([mode]) + bytes.fromhex("0203010420")]) == ["P0301", "P0420"]


def test_no_data():
    assert ELM327(MockTransport(["NO DATA\r>"])).query("03") == []


@pytest.mark.parametrize(
    "response",
    ["garbage>", "UNABLE TO CONNECT>", "?>", "410CZ1>", "410C0>", "420C0000>", "NO DATA\r430301>"],
)
def test_bad_responses(response):
    with pytest.raises(ELM327Error):
        ELM327(MockTransport([response])).query("010C")


def test_timeout():
    with pytest.raises(TimeoutError, match="prompt"):
        ELM327(MockTransport(["SEARCHING..."]), timeout=0.001).query("03")


def test_numbered_can_response():
    elm = ELM327(MockTransport(["014\r0:490201314434\r1:47503030523535\r2:42313233343536>"]))
    assert elm.query("0902") == [b"\x49\x02\x01" + b"1D4GP00R55B123456"]


@pytest.mark.parametrize("response", ["014\r0:490201314434>", "1:490201314434>"])
def test_truncated_numbered(response):
    with pytest.raises(ELM327Error):
        ELM327(MockTransport([response])).query("0902")


def test_readiness_spark():
    result = decode_readiness(bytes.fromhex("410182276521"))
    assert result["mil_on"] is True
    assert result["stored_dtc_count"] == 2
    assert result["ignition"] == "spark"
    assert result["monitors"]["misfire"] == {"supported": True, "complete": True}
    assert result["monitors"]["fuel_system"] == {"supported": True, "complete": False}
    assert result["monitors"]["catalyst"] == {"supported": True, "complete": False}
    assert result["monitors"]["heated_catalyst"] == {"supported": False, "complete": None}


def test_readiness_compression():
    result = decode_readiness(bytes.fromhex("4101000F8202"))
    assert result["mil_on"] is False
    assert result["ignition"] == "compression"
    assert result["monitors"]["nox_scr"]["complete"] is False
    assert result["monitors"]["egr_vvt"]["complete"] is True
    assert "oxygen_sensor" not in result["monitors"]


@pytest.mark.parametrize(
    "pid,raw,value,unit",
    [
        (0x0C, "1AF8", 1726, "rpm"),
        (0x0D, "64", 100, "km/h"),
        (0x05, "7B", 83, "°C"),
        (0x0F, "00", -40, "°C"),
        (0x10, "01F4", 5, "g/s"),
        (0x11, "80", 128 * 100 / 255, "%"),
        (0x2F, "FF", 100, "%"),
        (0x42, "36B0", 14, "V"),
        (0x1C, "06", "EOBD", None),
    ],
)
def test_pid_formula(pid, raw, value, unit):
    result = decode_pid(pid, bytes.fromhex(raw))
    assert result["value"] == value
    assert result["unit"] == unit


def test_unknown_pid_and_standard():
    assert decode_pid(0xFE, b"\x01\xff") == {"pid": "FE", "raw": [1, 255]}
    assert decode_pid(0x1C, b"\xff")["value"] == "OBD standard 0xFF"


@pytest.mark.parametrize("pid", [0x0C, 0x0D, 0x05, 0x0F, 0x10, 0x11, 0x2F, 0x42, 0x1C])
def test_truncated_pid(pid):
    with pytest.raises(ValueError, match="Truncated"):
        decode_pid(pid, b"")
