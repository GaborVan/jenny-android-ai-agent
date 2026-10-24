"""Trasporti sincroni con letture limitate e chiusura esplicita."""

from __future__ import annotations

import contextlib
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable
from typing import Any


class Transport(ABC):
    @abstractmethod
    def write_line(self, line: str) -> None: ...

    @abstractmethod
    def read_until(self, delimiter: str = ">", timeout: float = 10) -> str: ...

    @abstractmethod
    def close(self) -> None: ...


class MockTransport(Transport):
    """Una risposta per comando; eccezioni e prompt mancanti simulano guasti."""

    def __init__(self, responses: Iterable[str | Exception] = ()):
        self.responses = deque(responses)
        self.writes: list[str] = []
        self.closed = False

    def write_line(self, line: str) -> None:
        if self.closed:
            raise OSError("OBD transport is closed")
        self.writes.append(line)

    def read_until(self, delimiter: str = ">", timeout: float = 10) -> str:
        if self.closed:
            raise OSError("OBD transport is closed")
        response = self.responses.popleft() if self.responses else ""
        if isinstance(response, Exception):
            raise response
        if delimiter not in response:
            raise TimeoutError("Timed out waiting for ELM327 prompt")
        return response[: response.index(delimiter) + len(delimiter)]

    def close(self) -> None:
        self.closed = True


class SerialTransport(Transport):
    """Trasporto USB/seriale opzionale per diagnostica da desktop."""

    def __init__(self, device: str, timeout: float = 10, baudrate: int = 38400):
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError(
                "USB/serial OBD requires optional pyserial; install it on the desktop host."
            ) from exc
        self.serial = serial.Serial(device, baudrate, timeout=0.1, write_timeout=timeout)

    def write_line(self, line: str) -> None:
        self.serial.write((line + "\r").encode("ascii"))

    def read_until(self, delimiter: str = ">", timeout: float = 10) -> str:
        deadline = time.monotonic() + timeout
        data = bytearray()
        while time.monotonic() < deadline:
            data.extend(self.serial.read(1))
            if data.endswith(delimiter.encode("ascii")):
                return data.decode("ascii", errors="replace")
            if len(data) > 65536:
                raise OSError("ELM327 response exceeds 64 KiB")
        raise TimeoutError("Timed out waiting for ELM327 prompt")

    def close(self) -> None:
        self.serial.close()


class AndroidBluetoothSppTransport(Transport):
    """RFCOMM verso un dispositivo già associato nelle impostazioni Android."""

    UUID = "00001101-0000-1000-8000-00805F9B34FB"

    def __init__(self, device: str, timeout: float = 10):
        self.socket: Any = None
        self.timeout = timeout
        try:
            from java import jclass
        except ImportError as exc:
            raise RuntimeError("Bluetooth OBD requires Android running under Chaquopy") from exc
        try:
            adapter = jclass("android.bluetooth.BluetoothAdapter").getDefaultAdapter()
            if adapter is None or not adapter.isEnabled():
                raise RuntimeError("Bluetooth is unavailable or disabled")
            matches = [
                item
                for item in adapter.getBondedDevices().toArray()
                if str(item.getAddress()).lower() == device.lower() or str(item.getName()) == device
            ]
            if not matches:
                raise RuntimeError("OBD device is not paired; pair it in Android settings first")
            if len(matches) != 1:
                raise RuntimeError("Multiple paired devices have this name; use a MAC address")
            adapter.cancelDiscovery()
            self.socket = matches[0].createRfcommSocketToServiceRecord(
                jclass("java.util.UUID").fromString(self.UUID)
            )
            expired = threading.Event()

            def abort() -> None:
                expired.set()
                self.close()

            timer = threading.Timer(timeout, abort)
            timer.start()
            try:
                self.socket.connect()
            finally:
                timer.cancel()
                timer.join()
            if expired.is_set():
                raise TimeoutError("Bluetooth OBD connection timed out")
            self.input = self.socket.getInputStream()
            self.output = self.socket.getOutputStream()
        except Exception as exc:
            self.close()
            raise RuntimeError(
                f"Bluetooth OBD connection failed: {exc}. Check pairing and Android "
                "Nearby devices permissions (BLUETOOTH_CONNECT and BLUETOOTH_SCAN)."
            ) from exc

    def write_line(self, line: str) -> None:
        # close() interrompe anche una scrittura bloccata nel bridge Java.
        timer = threading.Timer(self.timeout, self.close)
        timer.start()
        try:
            for value in (line + "\r").encode("ascii"):
                self.output.write(value)
            self.output.flush()
        finally:
            timer.cancel()
            timer.join()

    def read_until(self, delimiter: str = ">", timeout: float = 10) -> str:
        deadline = time.monotonic() + timeout
        data = bytearray()
        while time.monotonic() < deadline:
            if self.socket is None or not self.socket.isConnected():
                raise OSError("Bluetooth OBD socket is closed")
            if not self.input.available():
                time.sleep(0.01)
                continue
            value = self.input.read()
            if value < 0:
                raise OSError("Bluetooth OBD connection closed by adapter")
            data.append(value)
            if data.endswith(delimiter.encode("ascii")):
                return data.decode("ascii", errors="replace")
            if len(data) > 65536:
                raise OSError("ELM327 response exceeds 64 KiB")
        raise TimeoutError("Timed out waiting for ELM327 prompt")

    def close(self) -> None:
        socket = self.socket
        if socket is not None:
            with contextlib.suppress(Exception):
                socket.close()
