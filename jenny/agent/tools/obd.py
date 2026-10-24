"""Tool OBD-II con una sessione serializzata e scadenza per inattività."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Callable
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.config.tool_schemas import ObdConfig
from jenny.obd.dtc import decode_dtc, decode_readiness
from jenny.obd.elm327 import ELM327
from jenny.obd.pids import decode_pid
from jenny.obd.transport import AndroidBluetoothSppTransport, SerialTransport, Transport


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            enum=["status", "connect", "read_codes", "clear_codes", "live", "vin", "readiness"],
            description="OBD-II operation",
        ),
        device=StringSchema(description="Paired Bluetooth name/MAC, or desktop serial device path"),
        confirm=BooleanSchema(
            description="Explicit consent to erase codes AND readiness monitors", default=False
        ),
        pids=ArraySchema(
            items=StringSchema(),
            max_items=32,
            description="Two-digit hexadecimal Mode 01 PIDs, e.g. 0C, 0D",
        ),
        required=["action"],
    )
)
class ObdTool(Tool):
    """Espone solo la diagnostica OBD-II standard al core."""

    _scopes = {"core", "orchestrator"}

    @property
    def name(self) -> str:
        return "obd"

    @property
    def description(self) -> str:
        return (
            "Standard OBD-II car diagnostics via a paired ELM327 Bluetooth adapter. "
            "Connect first, then read codes, live PIDs, VIN or readiness. "
            "clear_codes requires explicit confirm=true and erases codes AND readiness monitors."
        )

    config_key = "obd"

    @classmethod
    def config_cls(cls):
        return ObdConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return bool(getattr(ctx.config, "obd", None) and ctx.config.obd.enable)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.obd, android=bool(ctx.android_context))

    def __init__(
        self,
        config: ObdConfig | None = None,
        *,
        android: bool = True,
        transport_factory: Callable[[str], Transport] | None = None,
    ):
        self.config = config or ObdConfig()
        self._factory = transport_factory or (
            AndroidBluetoothSppTransport if android else SerialTransport
        )
        self._session: ELM327 | None = None
        self._device: str | None = None
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None
        self._last_used = time.monotonic()

    def _close(self) -> None:
        session, self._session = self._session, None
        self._device = None
        if session:
            with contextlib.suppress(Exception):
                session.transport.close()

    async def close(self) -> None:
        if self._idle_task:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
            self._idle_task = None
        self._close()

    async def _expire(self) -> None:
        try:
            while self._session:
                await asyncio.sleep(
                    max(0, self.config.idle_timeout_s - (time.monotonic() - self._last_used))
                )
                async with self._lock:
                    if time.monotonic() - self._last_used >= self.config.idle_timeout_s:
                        return
        finally:
            self._close()

    def _run(self, action: str, device: str, confirm: bool, pids: list[str]) -> Any:
        if action == "connect":
            if not device.strip():
                raise ValueError("connect requires a Bluetooth device name/MAC or serial path")
            self._close()
            transport = self._factory(device)
            try:
                session = ELM327(transport)
                session.initialize()
            except BaseException:
                transport.close()
                raise
            self._session, self._device = session, device
            return {"connected": True, "device": device}
        session = self._session
        if session is None:
            raise ValueError("No OBD adapter connected; use connect with a device first")
        if action == "read_codes":
            return {
                key: decode_dtc(session.query(mode))
                for key, mode in (("stored", "03"), ("pending", "07"), ("permanent", "0A"))
            }
        if action == "clear_codes":
            if confirm is not True:
                raise ValueError(
                    "clear_codes requires explicit confirm=true: it erases "
                    "diagnostic codes AND resets readiness monitors"
                )
            if not session.query("04"):
                raise ValueError("NO DATA: clearing codes was not acknowledged")
            return {
                "cleared": True,
                "readiness_reset": True,
                "note": "Permanent DTCs may persist until the vehicle verifies the repair.",
            }
        if action == "live":
            result = {}
            for pid in pids:
                responses = session.query("01" + pid)
                if any(payload[:2] != bytes((0x41, int(pid, 16))) for payload in responses):
                    raise ValueError(f"Unexpected response PID for {pid}")
                result[pid] = [decode_pid(int(pid, 16), payload[2:]) for payload in responses]
            return result
        if action == "readiness":
            return {"readiness": [decode_readiness(p) for p in session.query("0101")]}
        if action == "vin":
            responses = session.query("0902")
            if not responses:
                return {"vin": None, "message": "NO DATA"}
            if any(len(p) < 4 or p[:2] != b"\x49\x02" for p in responses):
                raise ValueError("Invalid VIN response")
            if len(responses) > 1 and sorted(p[2] for p in responses) != list(
                range(1, len(responses) + 1)
            ):
                raise ValueError("Ambiguous or incomplete VIN response")
            raw = b"".join(p[3:] for p in sorted(responses, key=lambda p: p[2])).strip(b"\x00")
            vin = raw.decode("ascii")
            if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
                raise ValueError("Invalid or incomplete 17-character VIN")
            return {"vin": vin}
        raise ValueError(f"Unknown OBD action: {action}")

    async def execute(
        self,
        action: str,
        device: str = "",
        confirm: bool = False,
        pids: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        async with self._lock:
            if action == "status":
                return json.dumps(
                    {
                        "connected": self._session is not None,
                        "device": self._device,
                        "idle_timeout_s": self.config.idle_timeout_s,
                    }
                )
            try:
                selected = pids if pids is not None else ["0C", "0D", "05"]
                if action == "live":
                    if (
                        not isinstance(selected, list)
                        or len(selected) > 32
                        or any(
                            not isinstance(p, str) or not re.fullmatch(r"[0-9a-fA-F]{2}", p)
                            for p in selected
                        )
                    ):
                        raise ValueError("pids must contain at most 32 two-digit hexadecimal PIDs")
                    selected = [p.upper() for p in selected]
                worker = asyncio.create_task(
                    asyncio.to_thread(self._run, action, device, confirm, selected)
                )
                try:
                    result = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    self._close()
                    # Attende il lavoro limitato dal timeout prima di rilasciare il lock.
                    with contextlib.suppress(Exception):
                        await worker
                    self._close()
                    raise
                self._last_used = time.monotonic()
                if self._session and (self._idle_task is None or self._idle_task.done()):
                    self._idle_task = asyncio.create_task(self._expire())
                return json.dumps(result)
            except Exception as exc:
                if isinstance(exc, (OSError, RuntimeError)):
                    self._close()
                return json.dumps({"error": str(exc)})


TOOLS = [ObdTool]
