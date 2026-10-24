"""Simulazioni del bridge Java e della dipendenza seriale opzionale."""

import sys
import threading
from types import SimpleNamespace

import pytest

from jenny.obd.transport import AndroidBluetoothSppTransport, SerialTransport


class Socket:
    def __init__(self, hang=False):
        self.closed = threading.Event()
        self.hang = hang
        self.data = list(b"OK>")
        self.written = []

    def connect(self):
        if self.hang:
            assert self.closed.wait(1), "connect was not aborted"
            raise OSError("closed")

    def close(self):
        self.closed.set()

    def isConnected(self):  # noqa: N802
        return not self.closed.is_set()

    def getInputStream(self):  # noqa: N802
        return self

    def getOutputStream(self):  # noqa: N802
        return self

    def available(self):
        return len(self.data)

    def read(self):
        return self.data.pop(0)

    def write(self, value):
        self.written.append(value)

    def flush(self):
        pass


def bridge(monkeypatch, socket):
    events = []

    def create(uuid):
        assert uuid == AndroidBluetoothSppTransport.UUID
        assert events == ["cancel"]
        return socket

    device = SimpleNamespace(
        getName=lambda: "OBD",
        getAddress=lambda: "AA:BB:CC:DD:EE:FF",
        createRfcommSocketToServiceRecord=create,
    )
    adapter = SimpleNamespace(
        isEnabled=lambda: True,
        getBondedDevices=lambda: SimpleNamespace(toArray=lambda: [device]),
        cancelDiscovery=lambda: events.append("cancel"),
    )
    classes = {
        "android.bluetooth.BluetoothAdapter": SimpleNamespace(getDefaultAdapter=lambda: adapter),
        "java.util.UUID": SimpleNamespace(fromString=lambda x: x),
    }
    monkeypatch.setitem(sys.modules, "java", SimpleNamespace(jclass=classes.__getitem__))


@pytest.mark.parametrize("device", ["OBD", "aa:bb:cc:dd:ee:ff"])
def test_bluetooth_bridge(monkeypatch, device):
    socket = Socket()
    bridge(monkeypatch, socket)
    transport = AndroidBluetoothSppTransport(device)
    transport.write_line("ATZ")
    assert bytes(socket.written) == b"ATZ\r"
    assert transport.read_until(timeout=0.1) == "OK>"
    transport.close()
    transport.close()
    assert socket.closed.is_set()


def test_connect_timeout_closes_and_joins(monkeypatch):
    socket = Socket(hang=True)
    bridge(monkeypatch, socket)
    before = set(threading.enumerate())
    with pytest.raises(RuntimeError, match="connection failed"):
        AndroidBluetoothSppTransport("OBD", timeout=0.01)
    assert socket.closed.is_set()
    assert set(threading.enumerate()) == before


def test_read_timeout(monkeypatch):
    socket = Socket()
    socket.data = []
    bridge(monkeypatch, socket)
    transport = AndroidBluetoothSppTransport("OBD")
    try:
        with pytest.raises(TimeoutError):
            transport.read_until(timeout=0.01)
    finally:
        transport.close()


def test_not_chaquopy(monkeypatch):
    monkeypatch.setitem(sys.modules, "java", None)
    with pytest.raises(RuntimeError, match="Chaquopy"):
        AndroidBluetoothSppTransport("OBD")


def test_optional_pyserial(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    with pytest.raises(RuntimeError, match="optional pyserial"):
        SerialTransport("/dev/ttyUSB0")


def test_serial_io_and_close(monkeypatch):
    calls = {}
    data = iter(b"OK>")

    def open_serial(device, baudrate, **kwargs):
        calls.update(device=device, baudrate=baudrate, **kwargs)
        return SimpleNamespace(
            write=lambda value: calls.update(written=value),
            read=lambda size: bytes([next(data)]),
            close=lambda: calls.update(closed=True),
        )

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=open_serial))
    transport = SerialTransport("/dev/ttyUSB0")
    transport.write_line("010C")
    assert transport.read_until(timeout=0.1) == "OK>"
    transport.close()
    assert calls == {
        "device": "/dev/ttyUSB0",
        "baudrate": 38400,
        "timeout": 0.1,
        "write_timeout": 10,
        "written": b"010C\r",
        "closed": True,
    }


def test_serial_timeout(monkeypatch):
    port = SimpleNamespace(read=lambda size: b"", close=lambda: None)
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=lambda *a, **k: port))
    transport = SerialTransport("/dev/ttyUSB0")
    try:
        with pytest.raises(TimeoutError, match="prompt"):
            transport.read_until(timeout=0.001)
    finally:
        transport.close()
