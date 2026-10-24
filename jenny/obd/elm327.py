"""Protocollo ELM327, con intestazioni disattivate e formattazione CAN attiva."""

from __future__ import annotations

import re

from jenny.obd.transport import Transport


class ELM327Error(ValueError):
    """Risposta negativa o malformata dell'adattatore."""


class ELM327:
    def __init__(self, transport: Transport, timeout: float = 10):
        self.transport = transport
        self.timeout = timeout

    def command(self, command: str) -> list[str]:
        command = command.upper().replace(" ", "")
        if not re.fullmatch(r"(?:AT[A-Z0-9]+|[0-9A-F]+)", command):
            raise ValueError("Invalid ELM327 command")
        self.transport.write_line(command)
        raw = self.transport.read_until(">", self.timeout)
        if ">" not in raw:
            raise TimeoutError("Timed out waiting for ELM327 prompt")
        lines = []
        for line in re.split(r"[\r\n]+", raw.split(">", 1)[0].upper()):
            line = " ".join(line.split())
            if line.replace(" ", "") == command:
                continue
            line = line.replace("SEARCHING...", "").strip()
            if not line:
                continue
            if line in {
                "?",
                "UNABLE TO CONNECT",
                "STOPPED",
                "CAN ERROR",
                "BUS ERROR",
                "BUFFER FULL",
                "BUS INIT: ERROR",
                "ERROR",
            }:
                raise ELM327Error(f"ELM327: {line}")
            lines.append(line)
        if "NO DATA" in lines:
            if len(lines) != 1:
                raise ELM327Error("ELM327 mixed NO DATA with a payload")
            return []
        return lines

    def initialize(self) -> None:
        for command in ("ATZ", "ATE0", "ATL0", "ATS0", "ATSP0"):
            lines = self.command(command)
            if not lines or (command != "ATZ" and lines != ["OK"]):
                raise ELM327Error(f"ELM327 initialization failed at {command}: {lines}")

    def query(self, command: str) -> list[bytes]:
        lines = self.command(command)
        payloads: list[bytes] = []
        assembled = bytearray()
        length: int | None = None
        sequence = 0
        for line in lines:
            compact = line.replace(" ", "")
            if re.fullmatch(r"[0-9A-F]{3}", compact):
                if length is not None or assembled:
                    raise ELM327Error("Duplicate ELM327 multiline length")
                length = int(compact, 16)
                continue
            match = re.fullmatch(r"([0-9A-F]):([0-9A-F]+)", compact)
            if match:
                if int(match[1], 16) != sequence % 16:
                    raise ELM327Error("Out-of-order ELM327 multiline response")
                sequence += 1
                compact = match[2]
            elif assembled:
                raise ELM327Error("Mixed numbered and unnumbered ELM327 response")
            if not re.fullmatch(r"(?:[0-9A-F]{2})+", compact):
                raise ELM327Error(f"Invalid ELM327 payload: {line}")
            data = bytes.fromhex(compact)
            if match:
                assembled.extend(data)
            else:
                payloads.append(data)
        if length is not None and len(assembled) != length:
            raise ELM327Error("Truncated ELM327 multiline response")
        if assembled:
            payloads.append(bytes(assembled))
        expected = int(command[:2], 16) + 0x40
        if any(data[0] != expected for data in payloads):
            raise ELM327Error(f"Unexpected response to {command}")
        return payloads
