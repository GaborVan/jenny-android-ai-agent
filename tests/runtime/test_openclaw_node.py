"""Verifica il protocollo senza dipendere da Android o da un gateway remoto."""
import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from jenny.config.schema import Config, OpenClawNodeConfig
from jenny.runtime import openclaw_node as node


def setup_code(data):
    return "oc-pair://" + base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def test_payload_exact():
    payload = node.build_device_auth_payload_v3(
        device_id="device", client_id="openclaw-android", client_mode="node", role="node",
        scopes=[], signed_at_ms=123456, token="", nonce="nonce-é",
        platform=" Android ", device_family=" PHONE ",
    )
    assert payload.encode() == b"v3|device|openclaw-android|node|node||123456||nonce-\xc3\xa9|android|phone"


def test_device_id():
    assert node.device_id_from_public_key(bytes(32)) == "66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925"


def test_setup_code(monkeypatch):
    monkeypatch.setattr(node.time, "time", lambda: 100)
    data = {"url": "wss://example.test", "bootstrapToken": "fixture", "expiresAtMs": 100001}
    assert node.decode_pairing_setup_code(setup_code(data)) == data
    assert node.decode_pairing_setup_code(setup_code(data).removeprefix("oc-pair://")) == data
    data["expiresAtMs"] = 100000
    with pytest.raises(ValueError, match="^expired_setup_code$"):
        node.decode_pairing_setup_code(setup_code(data))


@pytest.mark.parametrize("raw", ["!garbage!", "", "oc-pair://e30", "oc-pair://bnVsbA"])
def test_invalid_setup_code(raw):
    with pytest.raises(ValueError, match="^invalid_setup_code$"):
        node.decode_pairing_setup_code(raw)


@pytest.mark.parametrize("data", [
    {"url": "https://example.test", "bootstrapToken": "fixture"},
    {"url": "wss://example.test", "bootstrapToken": ""},
    {"url": "wss://example.test", "bootstrapToken": "fixture", "expiresAtMs": True},
    {"url": "wss://example.test", "bootstrapToken": "fixture", "expiresAtMs": float("nan")},
])
def test_invalid_setup_fields(data):
    with pytest.raises(ValueError, match="^invalid_setup_code$"):
        node.decode_pairing_setup_code(setup_code(data))


def handlers(result=None):
    return {name: AsyncMock(return_value=result) for name in (
        "screen_dump", "clipboard_get", "clipboard_set", "speak",
    )}


@pytest.mark.parametrize("command,handler,params,args,kwargs", [
    ("ui.dump", "screen_dump", {}, (), {}),
    ("clipboard.get", "clipboard_get", {}, (), {}),
    ("clipboard.set", "clipboard_set", {"text": "hello"}, ("hello",), {}),
    ("voice.speak", "speak", {"text": "hello", "language": "it", "rate": 1.2},
     ("hello",), {"language": "it", "rate": 1.2}),
])
async def test_dispatch(command, handler, params, args, kwargs):
    result = {"ok": True, "nodes": [], "text": "hello"}
    mocks = handlers(result)
    assert await node.dispatch_command(command, params, **mocks) == {"ok": True, "payload": result}
    mocks[handler].assert_awaited_once_with(*args, **kwargs)
    assert sum(mock.await_count for mock in mocks.values()) == 1


async def test_dispatch_errors():
    assert await node.dispatch_command("unknown", {}, **handlers()) == {"ok": False, "error": "unsupported_command"}
    assert await node.dispatch_command("ui.dump", {}, **handlers()) == {"ok": False, "error": "bridge_unavailable"}
    assert await node.dispatch_command("ui.dump", {}, **handlers({"ok": False, "error": "access_denied"})) == {"ok": False, "error": "access_denied"}
    for command, params in [("clipboard.set", {}), ("voice.speak", {"text": 1}),
                            ("voice.speak", {"text": "x", "rate": "fast"}),
                            ("voice.speak", {"text": "x", "language": 2})]:
        mocks = handlers()
        assert (await node.dispatch_command(command, params, **mocks))["error"] == "invalid_params"
        assert not any(mock.await_count for mock in mocks.values())


async def test_dispatch_timeout():
    async def slow():
        await asyncio.Event().wait()
    mocks = handlers()
    mocks["screen_dump"] = slow
    assert await node.dispatch_command("ui.dump", {}, timeout=0.001, **mocks) == {"ok": False, "error": "timeout"}


def test_backoff():
    values = [node.next_backoff(i, jitter=lambda: 0) for i in range(100)]
    assert values == sorted(values)
    assert values[:3] == [1, 2, 4]
    assert values[-1] == 60
    assert node.next_backoff(100000, jitter=lambda: 1) == 60
    assert node.next_backoff(0, jitter=lambda: 1) == 1.25


def test_config_defaults_aliases():
    assert not Config().openclaw_node.enable
    config = Config.model_validate({"openclaw_node": {"enable": True, "device_private_key": "fixture"}})
    assert config.openclaw_node.enable
    loaded = Config.model_validate(config.model_dump(by_alias=True))
    assert loaded.openclaw_node.device_private_key == "fixture"


class Socket:
    def __init__(self, pairing=False):
        self.queue = asyncio.Queue()
        self.queue.put_nowait(json.dumps({"type": "event", "event": "connect.challenge", "payload": {"nonce": "challenge", "ts": 123}}))
        self.sent = []
        self.pairing = pairing

    async def recv(self):
        return await self.queue.get()

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        if frame["method"] == "connect":
            response = {"type": "res", "id": frame["id"], "ok": not self.pairing}
            if self.pairing:
                response["error"] = {"details": {"code": "PAIRING_REQUIRED", "requestId": "request-1", "recommendedNextStep": "wait_then_retry", "retryable": True}}
            else:
                response["payload"] = {"type": "hello-ok", "auth": {"deviceToken": "issued-fixture", "scopes": []}, "policy": {"maxPayload": 100000, "tickIntervalMs": 30000}}
            self.queue.put_nowait(json.dumps(response))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()


@pytest.fixture
def client(monkeypatch):
    instance = node.OpenClawNodeClient(OpenClawNodeConfig(enable=True, device_id="device"))
    class Crypto:
        def sign(self, payload, private_key):
            self.payload = payload
            return "c2lnbmF0dXJl"
    instance._crypto = Crypto()
    instance._public_key = "public"
    monkeypatch.setattr(instance, "_persist", AsyncMock())
    return instance


async def test_handshake_and_pairing(client):
    ws = Socket(pairing=True)
    assert not await client._handshake(ws, {"token": "fixture"})
    assert client.status()["state"] == "pairing_required"
    assert client.status()["request_id"] == "request-1"
    ws = Socket()
    assert await client._handshake(ws, {"bootstrapToken": "fixture"})
    frame = ws.sent[0]
    params = frame["params"]
    assert frame["method"] == "connect"
    assert params["commands"] == node.command_names()
    assert params["caps"] == ["screen", "voice"]
    assert params["role"] == "node" and params["scopes"] == []
    assert params["minProtocol"] == params["maxProtocol"] == 4
    assert params["device"]["signedAt"] == 123
    assert client._crypto.payload == "v3|device|openclaw-android|node|node||123|fixture|challenge|android|"
    assert client.settings.device_token == "issued-fixture"
    assert client.settings.credential == ""
    assert client.status()["state"] == "connected"
    client._persist.assert_awaited_once()


async def test_invoke_json_params_timeout_and_unavailable(client, monkeypatch):
    ws = Socket()
    mock = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(node, "clipboard_set", mock)
    await client._invoke(ws, {"id": "i1", "nodeId": "n1", "command": "clipboard.set", "paramsJSON": '{"text":"hello"}'})
    assert ws.sent[-1]["method"] == "node.invoke.result"
    assert ws.sent[-1]["params"] == {"id": "i1", "nodeId": "n1", "ok": True, "payload": {"ok": True}}
    mock.assert_awaited_once_with("hello")
    monkeypatch.setattr(node, "screen_dump", AsyncMock(return_value=None))
    await client._invoke(ws, {"id": "i2", "command": "ui.dump"})
    assert ws.sent[-1]["params"]["error"] == "bridge_unavailable"
    async def slow():
        await asyncio.Event().wait()
    monkeypatch.setattr(node, "screen_dump", slow)
    await client._invoke(ws, {"id": "i3", "command": "ui.dump", "timeoutMs": 1})
    assert ws.sent[-1]["params"]["error"] == "timeout"
    await client._invoke(ws, {"id": "i4", "command": "ui.dump", "paramsJSON": "{"})
    assert ws.sent[-1]["params"]["error"] == "invalid_params"


async def test_cancel_abandons_invoke(client, monkeypatch):
    ws = Socket()
    started = asyncio.Event()
    async def waiting():
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(node, "screen_dump", waiting)
    receiver = asyncio.create_task(client._receive(ws))
    ws.queue.put_nowait(json.dumps({"type": "event", "event": "node.invoke.request", "payload": {"id": "i1", "command": "ui.dump"}}))
    await asyncio.wait_for(started.wait(), 1)
    invoke = client._invokes["i1"]
    ws.queue.put_nowait(json.dumps({"type": "event", "event": "node.invoke.cancel", "payload": {"invokeId": "i1"}}))
    await asyncio.wait_for(asyncio.gather(invoke, return_exceptions=True), 1)
    assert not ws.sent
    assert not client._invokes
    receiver.cancel()
    await asyncio.gather(receiver, return_exceptions=True)


async def test_disabled_no_connection(monkeypatch):
    connector = AsyncMock()
    monkeypatch.setattr(node, "connect", connector)
    client = node.OpenClawNodeClient(OpenClawNodeConfig())
    await client.start()
    await client.run()
    await client.stop()
    connector.assert_not_called()
    assert client.status()["state"] == "disabled"


async def test_identity_persistence_and_restore(monkeypatch):
    public = bytes(range(32))
    public_encoded = base64.urlsafe_b64encode(public).decode().rstrip("=")
    device_id = hashlib.sha256(public).hexdigest()
    class Crypto:
        def generateIdentity(self):  # noqa: N802 - rispecchia l'API Kotlin del bridge
            return json.dumps({"ok": True, "deviceId": device_id, "publicKey": public_encoded, "privateKey": "fixture"})
        def publicKey(self, seed):  # noqa: N802 - rispecchia l'API Kotlin del bridge
            return public_encoded
    monkeypatch.setattr(node, "get_android_context", lambda: object())
    monkeypatch.setattr(node, "_resolve_crypto_bridge_class", lambda: lambda context: Crypto())
    config = Config()
    async def mutate(apply):
        apply(config)
        return config
    monkeypatch.setattr(node, "mutate", mutate)
    node.reset_openclaw_node_state()
    first = node.OpenClawNodeClient(OpenClawNodeConfig(enable=True))
    await first._identity()
    assert config.openclaw_node.device_id == device_id
    second = node.OpenClawNodeClient(config.openclaw_node)
    await second._identity()
    assert first._public_key == second._public_key
    node.reset_openclaw_node_state()


async def test_retries_pairing_then_uses_device_token(client, monkeypatch):
    sockets = []
    connected = asyncio.Event()
    class Connection:
        async def __aenter__(self):
            ws = Socket(pairing=len(sockets) == 0)
            sockets.append(ws)
            return ws
        async def __aexit__(self, *args):
            return False
    monkeypatch.setattr(node, "connect", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(node, "next_backoff", lambda attempt: 0)
    async def receive(ws):
        if len(sockets) >= 3:
            connected.set()
            await asyncio.Event().wait()
    monkeypatch.setattr(client, "_receive", receive)
    client.settings.credential = setup_code({"url": "ws://example.test", "bootstrapToken": "fixture"})
    client.settings.credential_kind = "setup_code"
    task = asyncio.create_task(client.start())
    await asyncio.wait_for(connected.wait(), 2)
    await client.stop()
    await asyncio.gather(task, return_exceptions=True)
    assert len(sockets) == 3
    assert "bootstrapToken" in sockets[0].sent[0]["params"]["auth"]
    assert sockets[0].sent[0]["params"]["auth"] == sockets[1].sent[0]["params"]["auth"]
    assert "token" in sockets[2].sent[0]["params"]["auth"]
    assert client.status()["state"] == "disabled"
