"""Client nodo OpenClaw: identità Android, pairing e nove comandi limitati.

Il gateway parla il protocollo WebSocket delle *nodi* OpenClaw (versione 4). Qui
vivono solo la macchina a stati del collegamento e i nove handler ammessi:
le capacità Android arrivano dai runtime esistenti (``ui_automation``,
``clipboard``, ``tts``, ``stt``), la firma Ed25519 dal bridge Kotlin ``NodeCryptoBridge``.

Fuori da Android tutto degrada: senza context non c'è bridge, quindi nessuna
identità e nessuna connessione. I test iniettano un finto crypto bridge.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import random
import ssl
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from jenny import __version__
from jenny.config.schema import Config, OpenClawNodeConfig
from jenny.config.store import mutate
from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.clipboard import clipboard_get, clipboard_set
from jenny.runtime.context import get_android_context
from jenny.runtime.secure_store import (
    OPENCLAW_NODE_TOKEN,
    get_secret,
    remove_secret,
    set_secret,
)
from jenny.runtime.stt import listen
from jenny.runtime.tts import speak
from jenny.runtime.ui_automation import press_global, screen_dump, swipe, tap_target, type_text_into

_BRIDGE = BridgeCache("com.flagdizero.jenny.NodeCryptoBridge")
Handler = Callable[..., Awaitable[dict[str, Any] | None]]

# Client vivo del processo: la WebUI legge qui lo stato e può riavviarlo senza
# ricostruire il container. Vive fuori dalla classe perché il router HTTP non ha
# un riferimento al container.
_ACTIVE: Any = None

# Limiti difensivi: un frame in uscita più grande del payload negoziato viene
# rifiutato prima di toccare il socket.
_MIN_PAYLOAD = 1024
_MAX_PAYLOAD = 16 * 1024 * 1024
_DEFAULT_PAYLOAD = 65536
_DEFAULT_TICK_SECONDS = 30.0
_MAX_PAIRING_ATTEMPTS = 120


def reset_openclaw_node_state() -> None:
    """Ricrea la cache del bridge a ogni avvio del gateway."""
    global _ACTIVE
    _BRIDGE.reset()
    _ACTIVE = None


def set_active_client(client: Any) -> None:
    global _ACTIVE
    _ACTIVE = client


def active_status() -> dict[str, Any]:
    client = _ACTIVE
    if client is None:
        return {"state": "disabled", "detail": "", "request_id": None, "last_error": None}
    return client.status()


async def stop_active_client() -> None:
    """Ferma il client prima di cancellare le credenziali persistenti."""
    global _ACTIVE
    if _ACTIVE is not None:
        await _ACTIVE.stop()
        _ACTIVE = None


async def restart_active_client() -> dict[str, Any]:
    """Ferma il client vivo (se c'è) e ne avvia uno nuovo dalla config corrente."""
    global _ACTIVE
    from jenny.config.loader import load_config

    if _ACTIVE is not None:
        await _ACTIVE.stop()
    client = OpenClawNodeClient(load_config().openclaw_node)
    _ACTIVE = client
    await client.start()
    return client.status()


def _resolve_crypto_bridge_class() -> Any:
    return _BRIDGE.resolve_class()


async def _get_crypto(context: Any) -> Any:
    if context is None:
        return None
    return await _BRIDGE.get(context, resolve=_resolve_crypto_bridge_class)


def normalize_metadata(value: str) -> str:
    """Normalizza ``platform``/``deviceFamily`` come fa il gateway: trim + lower."""
    return value.strip().lower()


def build_device_auth_payload_v3(
    *,
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    signed_at_ms: int,
    token: str,
    nonce: str,
    platform: str,
    device_family: str,
) -> str:
    """Payload di firma v3 (stringa con campi uniti da ``|``)."""
    return "|".join(
        (
            "v3",
            device_id,
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at_ms),
            token,
            nonce,
            normalize_metadata(platform),
            normalize_metadata(device_family),
        )
    )


def device_id_from_public_key(raw_public_key: bytes) -> str:
    """``deviceId`` = sha256 esadecimale della chiave pubblica Ed25519 grezza."""
    return hashlib.sha256(raw_public_key).hexdigest()


def _base64url_decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def decode_pairing_setup_code(raw: str) -> dict[str, Any]:
    """Decodifica un ``oc-pair://<code>`` (o il solo code) in ``{url, bootstrapToken, ...}``.

    Solleva ``ValueError("invalid_setup_code")`` o ``"expired_setup_code"``.
    """
    try:
        value = raw.strip().removeprefix("oc-pair://")
        if not value:
            raise ValueError
        data = json.loads(_base64url_decode(value))
        if not isinstance(data, dict):
            raise ValueError
        url_raw = data.get("url")
        token = data.get("bootstrapToken")
        if not isinstance(url_raw, str) or not isinstance(token, str) or not token.strip():
            raise ValueError
        parsed = urlsplit(url_raw)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        expiry = data.get("expiresAtMs")
        if expiry is not None:
            if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
                raise ValueError
            if expiry <= time.time() * 1000:
                raise ValueError("expired_setup_code")
        return data
    except (ValueError, TypeError, UnicodeError) as exc:
        if str(exc) == "expired_setup_code":
            raise ValueError("expired_setup_code") from None
        raise ValueError("invalid_setup_code") from None


def command_names() -> list[str]:
    return [
        "ui.dump", "ui.tap", "ui.swipe", "ui.type", "ui.press",
        "clipboard.get", "clipboard.set", "voice.speak", "voice.listen",
    ]


def command_error(code: str, message: str) -> dict[str, Any]:
    """Errore nel formato che il gateway pretende per ``node.invoke.result``.

    Lo schema impone che ``error`` sia un oggetto (``code``/``message``), mai
    una stringa: una stringa fa fallire la validazione con ``INVALID_REQUEST``
    e la risposta viene scartata, così l'invocazione resta appesa fino al
    timeout.
    """
    return {"ok": False, "error": {"code": code, "message": message}}


_PASSTHROUGH_ERRORS = {
    "service_not_enabled", "no_screen_dump", "element_not_found",
    "permission_denied", "permission_pending",
    "microphone_unavailable", "stt_unavailable", "stt_no_match", "stt_timeout",
}


async def dispatch_command(
    command: str,
    params: dict[str, Any],
    *,
    screen_dump: Handler,
    clipboard_get: Handler,
    clipboard_set: Handler,
    speak: Handler,
    tap: Handler,
    swipe: Handler,
    type_text: Handler,
    press_global: Handler,
    listen: Handler,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Esegue un comando ammesso e restituisce il payload di ``node.invoke.result``."""
    if command not in command_names():
        return command_error("unsupported_command", "unsupported_command")
    if not isinstance(params, dict):
        return command_error("invalid_params", "invalid_params")
    if command in {"clipboard.set", "voice.speak"} and not isinstance(params.get("text"), str):
        return command_error("invalid_params", "invalid_params")
    invalid = command_error("invalid_params", "invalid_params")
    target: dict[str, Any] = {}
    if command in {"ui.tap", "ui.type"}:
        if "index" in params:
            if type(params["index"]) is not int:
                return invalid
            target["index"] = params["index"]
        if "id" in params:
            if not isinstance(params["id"], str) or not params["id"]:
                return invalid
            target["node_id"] = params["id"]
        if len(target) > 1:
            return invalid
        if command == "ui.tap":
            if set(params) == {"x", "y"}:
                if any(type(params[key]) is not int for key in ("x", "y")):
                    return invalid
                target = {"x": params["x"], "y": params["y"]}
            elif set(params) not in ({"index"}, {"id"}):
                return invalid
        elif not isinstance(params.get("text"), str) or not params["text"]:
            return invalid
    if command == "ui.swipe":
        if any(type(params.get(key)) is not int for key in ("x1", "y1", "x2", "y2")):
            return invalid
        if type(params.get("durationMs", 300)) is not int:
            return invalid
    action = params.get("action", "")
    if command == "ui.press":
        if not isinstance(action, str) or action.lower() not in {"back", "home", "recents", "notifications"}:
            return invalid
        action = action.lower()
    seconds = params.get("seconds", 5)
    language = params.get("language", "")
    if command == "voice.listen":
        if (
            isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or (isinstance(seconds, float) and not math.isfinite(seconds))
            or not isinstance(language, str)
        ):
            return invalid
        seconds = max(1, min(15, seconds))
        timeout = min(60.0, max(timeout, seconds + 6.0))
    try:
        async with asyncio.timeout(timeout):
            if command == "ui.dump":
                result = await screen_dump()
            elif command == "ui.tap":
                result = await tap(**target)
            elif command == "ui.swipe":
                result = await swipe(params["x1"], params["y1"], params["x2"], params["y2"], params.get("durationMs", 300))
            elif command == "ui.type":
                result = await type_text(params["text"], **target)
            elif command == "ui.press":
                result = await press_global(action)
            elif command == "voice.listen":
                result = await listen(seconds, language=language)
            elif command == "clipboard.get":
                result = await clipboard_get()
            elif command == "clipboard.set":
                result = await clipboard_set(params["text"])
            else:
                language = params.get("language", "")
                rate = params.get("rate", 1.0)
                if (
                    not isinstance(language, str)
                    or isinstance(rate, bool)
                    or not isinstance(rate, (int, float))
                    or not math.isfinite(rate)
                ):
                    return command_error("invalid_params", "invalid_params")
                result = await speak(params["text"], language=language, rate=float(rate))
        if result is None:
            return command_error("bridge_unavailable", "bridge_unavailable")
        if result.get("ok") is False:
            error = result.get("error")
            if isinstance(error, str) and error in _PASSTHROUGH_ERRORS:
                return command_error(error, result.get("hint") or error)
            return command_error("command_failed", str(result.get("error") or "command_failed"))
        return {"ok": True, "payload": result}
    except TimeoutError:
        return command_error("timeout", "timeout")
    except Exception:  # noqa: BLE001 - un handler rotto non deve uccidere la connessione
        return command_error("command_failed", "command_failed")


def next_backoff(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 60.0,
    jitter: Callable[[], float] = random.random,
) -> float:
    """Backoff esponenziale con jitter (0..25%), limitato a *cap*."""
    exponent = min(30, max(0, attempt))
    return min(cap, base * 2**exponent * (1 + 0.25 * jitter()))


def next_pairing_backoff(
    attempt: int,
    *,
    base: float = 5.0,
    cap: float = 10.0,
    jitter: Callable[[], float] = random.random,
) -> float:
    """Attesa breve e limitata per intercettare l'approvazione del gateway."""
    return next_backoff(attempt, base=base, cap=cap, jitter=jitter)


class OpenClawNodeClient:
    """Una sola connessione alla volta, con task degli invoke limitati alla sua durata."""

    def __init__(
        self,
        settings: OpenClawNodeConfig,
        status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.settings = settings.model_copy()
        self._callback = status_callback
        self._status: dict[str, Any] = {"state": "disabled", "detail": "", "request_id": None, "last_error": None}
        self._task: asyncio.Task[None] | None = None
        self._invokes: dict[str, asyncio.Task[None]] = {}
        self._crypto: Any = None
        self._public_key = ""
        self._counter = 0
        self._token_stored = False
        self._max_payload = _DEFAULT_PAYLOAD
        self._tick_seconds = _DEFAULT_TICK_SECONDS

    def status(self) -> dict[str, Any]:
        return {
            **self._status,
            "paired": bool(self.settings.device_token),
            "token_stored": self._token_stored and bool(self.settings.device_token),
        }

    def _set_status(self, state: str, detail: str = "", request_id: str | None = None) -> None:
        self._status = {
            "state": state,
            "detail": detail,
            "request_id": request_id,
            "last_error": detail if state in {"error", "pairing_required", "token_rejected", "pairing_timeout"} else None,
        }
        if self._callback:
            try:
                self._callback(self.status())
            except Exception:  # noqa: BLE001 - un observer rotto non ferma il client
                pass

    async def start(self) -> None:
        if self.settings.enable and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._set_status("disabled")

    def _is_setup(self) -> bool:
        return not self.settings.device_token and (
            self.settings.credential_kind == "setup_code"
            or self.settings.credential.startswith("oc-pair://")
        )

    async def _persist(self, *, enrolled: bool = False) -> None:
        def apply(config: Config) -> None:
            target = config.openclaw_node
            target.url = self.settings.url
            target.credential = self.settings.credential
            target.credential_kind = self.settings.credential_kind
            target.device_id = self.settings.device_id
            target.device_private_key = self.settings.device_private_key
            target.device_token = "" if self._token_stored else self.settings.device_token
            target.device_scopes = list(self.settings.device_scopes)

        await mutate(apply)

    async def _resolve_token(self) -> None:
        """Migra il token legacy oppure recupera quello custodito dal Keystore."""
        if self.settings.device_token:
            self._token_stored = await set_secret(OPENCLAW_NODE_TOKEN, self.settings.device_token)
            if self._token_stored:
                await self._persist()
        else:
            self.settings.device_token = await get_secret(OPENCLAW_NODE_TOKEN) or ""
            self._token_stored = bool(self.settings.device_token)

    async def _identity(self) -> Any:
        if self._crypto is not None and self._public_key:
            return self._crypto
        if self._crypto is None:
            bridge = await _get_crypto(get_android_context())
            if bridge is None:
                raise ValueError("bridge_unavailable")
            self._crypto = bridge
        bridge = self._crypto
        if not self.settings.device_private_key:
            identity = json.loads(str(await asyncio.to_thread(bridge.generateIdentity)))
            if identity.get("ok") is not True:
                raise ValueError("identity_failed")
            self.settings.device_private_key = identity["privateKey"]
            self.settings.device_id = identity["deviceId"]
            await self._persist()
        self._public_key = str(await asyncio.to_thread(bridge.publicKey, self.settings.device_private_key))
        raw = _base64url_decode(self._public_key)
        if len(raw) != 32 or device_id_from_public_key(raw) != self.settings.device_id:
            raise ValueError("invalid_identity")
        return bridge

    async def run(self) -> None:
        attempt = 0
        pairing_attempt = 0
        if self.settings.enable:
            await self._resolve_token()
        while self.settings.enable:
            try:
                self._set_status("connecting")
                crypto = await self._identity()
                await self._connection(crypto)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                if self._status["state"] not in {"pairing_required", "token_rejected"}:
                    self._set_status("error", "connection_failed")
            finally:
                for task in self._invokes.values():
                    task.cancel()
                await asyncio.gather(*self._invokes.values(), return_exceptions=True)
                self._invokes.clear()
            if not self.settings.enable or self._status["state"] == "token_rejected":
                break
            if self._status["state"] == "pairing_required":
                pairing_attempt += 1
                if pairing_attempt >= _MAX_PAIRING_ATTEMPTS:
                    self._set_status("pairing_timeout", "pairing_timeout")
                    break
                await asyncio.sleep(next_pairing_backoff(pairing_attempt - 1))
                continue
            if self._status["state"] == "connected":
                pairing_attempt = 0
            attempt = 0 if self._status["state"] == "connected" else attempt + 1
            await asyncio.sleep(next_backoff(attempt))

    async def _connection(self, crypto: Any) -> None:
        url = self.settings.url
        if self._is_setup():
            setup = decode_pairing_setup_code(self.settings.credential)
            url = setup["url"]
            auth = {"bootstrapToken": setup["bootstrapToken"]}
            self.settings.url = url
        else:
            auth = {"token": self.settings.device_token or self.settings.credential}
        options: dict[str, Any] = {"max_size": _MAX_PAYLOAD, "open_timeout": 15, "close_timeout": 5}
        if urlsplit(url).scheme == "wss":
            try:
                import certifi

                options["ssl"] = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                options["ssl"] = ssl.create_default_context()
        async with connect(url, **options) as ws:
            if await self._handshake(ws, auth, crypto):
                await self._receive(ws)

    async def _handshake(self, ws: Any, auth: dict[str, str], crypto: Any = None) -> bool:
        """Challenge → connect firmato; ``True`` su ``hello-ok``, ``False`` su pairing."""
        if crypto is None:
            crypto = self._crypto
        async with asyncio.timeout(20):
            challenge = json.loads(await ws.recv())
            if challenge.get("event") != "connect.challenge":
                raise ValueError("invalid_challenge")
            payload = challenge.get("payload") or {}
            nonce, ts = payload.get("nonce"), payload.get("ts")
            if not isinstance(nonce, str) or type(ts) is not int:
                raise ValueError("invalid_challenge")
            token = next(iter(auth.values()), "")
            signature_payload = build_device_auth_payload_v3(
                device_id=self.settings.device_id,
                client_id="openclaw-android",
                client_mode="node",
                role="node",
                scopes=[],
                signed_at_ms=ts,
                token=token,
                nonce=nonce,
                platform="android",
                device_family="",
            )
            signature = str(await asyncio.to_thread(crypto.sign, signature_payload, self.settings.device_private_key))
            if signature.startswith("{"):
                raise ValueError("sign_failed")
            request_id = await self._send(
                ws,
                "connect",
                {
                    "minProtocol": 4,
                    "maxProtocol": 4,
                    "client": {"id": "openclaw-android", "version": __version__, "platform": "android", "mode": "node"},
                    "role": "node",
                    "scopes": [],
                    "caps": ["screen", "voice"],
                    "commands": command_names(),
                    "permissions": {},
                    "auth": dict(auth),
                    "locale": "en-US",
                    "userAgent": f"openclaw-android/jenny-{__version__}",
                    "device": {
                        "id": self.settings.device_id,
                        "publicKey": self._public_key,
                        "signature": signature,
                        "signedAt": ts,
                        "nonce": nonce,
                    },
                },
            )
            while True:
                response = json.loads(await ws.recv())
                if response.get("type") == "res" and response.get("id") == request_id:
                    break
        if not response.get("ok"):
            error = response.get("error") or {}
            details = error.get("details") or {}
            if details.get("code") == "PAIRING_REQUIRED" or error.get("code") == "PAIRING_REQUIRED":
                self._set_status("pairing_required", "PAIRING_REQUIRED", details.get("requestId"))
                return False
            if "token" in auth:
                await remove_secret(OPENCLAW_NODE_TOKEN)
                self.settings.device_token = ""
                self.settings.credential = ""
                self.settings.credential_kind = ""
                self._token_stored = False
                self._set_status("token_rejected", "token_rejected")
                await self._persist()
                return False
            raise ValueError("connect_rejected")
        hello = response.get("payload") or {}
        if hello.get("type") != "hello-ok":
            raise ValueError("invalid_hello")
        auth_info = hello.get("auth") or {}
        issued = auth_info.get("deviceToken")
        if isinstance(issued, str) and issued:
            self.settings.device_token = issued
            self._token_stored = await set_secret(OPENCLAW_NODE_TOKEN, issued)
            # La credenziale iniziale resta valida finché non arriva il token.
            self.settings.credential = ""
            self.settings.credential_kind = ""
        scopes = auth_info.get("scopes")
        if isinstance(scopes, list):
            self.settings.device_scopes = [str(scope) for scope in scopes]
        policy = hello.get("policy") or {}
        self._max_payload = max(_MIN_PAYLOAD, min(_MAX_PAYLOAD, int(policy.get("maxPayload", _DEFAULT_PAYLOAD))))
        self._tick_seconds = max(1.0, min(300.0, float(policy.get("tickIntervalMs", 30000)) / 1000))
        await self._persist(enrolled=bool(issued))
        self._set_status("connected")
        return True

    async def _receive(self, ws: Any) -> None:
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), self._tick_seconds * 3))
            event, data = frame.get("event"), frame.get("payload") or {}
            if event == "node.invoke.request":
                invoke_id = data.get("id")
                if isinstance(invoke_id, str) and invoke_id not in self._invokes:
                    task = asyncio.create_task(self._invoke(ws, data))
                    self._invokes[invoke_id] = task
                    task.add_done_callback(lambda _task, key=invoke_id: self._invokes.pop(key, None))
            elif event == "node.invoke.cancel":
                cancel_id = data.get("invokeId")
                if isinstance(cancel_id, str):
                    cancel_task = self._invokes.get(cancel_id)
                    if cancel_task:
                        cancel_task.cancel()

    async def _invoke(self, ws: Any, data: dict[str, Any]) -> None:
        try:
            try:
                params = json.loads(data["paramsJSON"]) if "paramsJSON" in data else data.get("params", {})
                timeout = max(0.001, min(60.0, float(data.get("timeoutMs", 15000)) / 1000))
                result = await dispatch_command(
                    data.get("command", ""),
                    params,
                    screen_dump=screen_dump,
                    clipboard_get=clipboard_get,
                    clipboard_set=clipboard_set,
                    speak=speak,
                    tap=tap_target,
                    swipe=swipe,
                    type_text=type_text_into,
                    press_global=press_global,
                    listen=listen,
                    timeout=timeout,
                )
            except (ValueError, TypeError):
                result = command_error("invalid_params", "invalid_params")
            await self._send(
                ws,
                "node.invoke.result",
                {"id": data["id"], "nodeId": data.get("nodeId", self.settings.device_id), **result},
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._set_status("error", "invoke_delivery_failed")

    async def _send(self, ws: Any, method: str, params: dict[str, Any]) -> str:
        self._counter += 1
        request_id = str(self._counter)
        frame = json.dumps({"type": "req", "id": request_id, "method": method, "params": params})
        if len(frame.encode()) > self._max_payload:
            raise ValueError("payload_too_large")
        await ws.send(frame)
        return request_id
