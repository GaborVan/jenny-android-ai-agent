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
        "tap", "swipe", "type_text", "press_global", "listen",
    )}


@pytest.mark.parametrize("command,handler,params,args,kwargs", [
    ("ui.dump", "screen_dump", {}, (), {}),
    ("ui.tap", "tap", {"x": 10, "y": 20}, (), {"x": 10, "y": 20}),
    ("ui.tap", "tap", {"index": 0}, (), {"index": 0}),
    ("ui.tap", "tap", {"id": "field"}, (), {"node_id": "field"}),
    ("ui.swipe", "swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}, (1, 2, 3, 4, 300), {}),
    ("ui.swipe", "swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "durationMs": 500}, (1, 2, 3, 4, 500), {}),
    ("ui.type", "type_text", {"text": "hi"}, ("hi",), {}),
    ("ui.type", "type_text", {"text": "hi", "index": 1}, ("hi",), {"index": 1}),
    ("ui.type", "type_text", {"text": "hi", "id": "field"}, ("hi",), {"node_id": "field"}),
    ("ui.press", "press_global", {"action": "BACK"}, ("back",), {}),
    ("voice.listen", "listen", {}, (5,), {"language": ""}),
    ("voice.listen", "listen", {"seconds": 8, "language": "uk"}, (8,), {"language": "uk"}),
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
    assert await node.dispatch_command("unknown", {}, **handlers()) == {
        "ok": False, "error": {"code": "unsupported_command", "message": "unsupported_command"}
    }
    assert await node.dispatch_command("ui.dump", {}, **handlers()) == {
        "ok": False, "error": {"code": "bridge_unavailable", "message": "bridge_unavailable"}
    }
    assert await node.dispatch_command(
        "ui.dump", {}, **handlers({"ok": False, "error": "access_denied"})
    ) == {"ok": False, "error": {"code": "command_failed", "message": "access_denied"}}
    for command, params in [("clipboard.set", {}), ("voice.speak", {"text": 1}),
                            ("voice.speak", {"text": "x", "rate": "fast"}),
                            ("voice.speak", {"text": "x", "language": 2})]:
        mocks = handlers()
        assert (await node.dispatch_command(command, params, **mocks))["error"]["code"] == "invalid_params"
        assert not any(mock.await_count for mock in mocks.values())


async def test_dispatch_timeout():
    async def slow():
        await asyncio.Event().wait()
    mocks = handlers()
    mocks["screen_dump"] = slow
    assert await node.dispatch_command("ui.dump", {}, timeout=0.001, **mocks) == {
        "ok": False, "error": {"code": "timeout", "message": "timeout"}
    }


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
    assert ws.sent[-1]["params"]["error"] == {"code": "bridge_unavailable", "message": "bridge_unavailable"}
    async def slow():
        await asyncio.Event().wait()
    monkeypatch.setattr(node, "screen_dump", slow)
    await client._invoke(ws, {"id": "i3", "command": "ui.dump", "timeoutMs": 1})
    assert ws.sent[-1]["params"]["error"]["code"] == "timeout"
    await client._invoke(ws, {"id": "i4", "command": "ui.dump", "paramsJSON": "{"})
    assert ws.sent[-1]["params"]["error"]["code"] == "invalid_params"


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
    monkeypatch.setattr(node, "next_pairing_backoff", lambda attempt: 0)
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


@pytest.fixture
def secure(monkeypatch):
    secrets = {}

    async def put(name, value):
        secrets[name] = value
        return True

    async def remove(name):
        secrets.pop(name, None)
        return True

    get = AsyncMock(side_effect=lambda name: secrets.get(name))
    put_mock = AsyncMock(side_effect=put)
    remove_mock = AsyncMock(side_effect=remove)
    monkeypatch.setattr(node, "get_secret", get)
    monkeypatch.setattr(node, "set_secret", put_mock)
    monkeypatch.setattr(node, "remove_secret", remove_mock)
    return secrets, get, put_mock, remove_mock


@pytest.fixture
def persisted(monkeypatch):
    config = Config()

    async def mutate(apply):
        apply(config)
        return config

    monkeypatch.setattr(node, "mutate", AsyncMock(side_effect=mutate))
    return config


async def test_secure_token_persistence_and_restart(client, secure, persisted, monkeypatch):
    monkeypatch.setattr(client, "_persist", node.OpenClawNodeClient._persist.__get__(client))
    client.settings.credential = setup_code({"url": "ws://example.test", "bootstrapToken": "fixture"})
    client.settings.credential_kind = "setup_code"
    client.settings.url = "ws://example.test"
    assert await client._handshake(Socket(), {"bootstrapToken": "fixture"})
    secure[2].assert_awaited_once_with(node.OPENCLAW_NODE_TOKEN, "issued-fixture")
    assert persisted.openclaw_node.device_token == ""
    assert persisted.openclaw_node.credential == ""
    assert persisted.openclaw_node.credential_kind == ""
    states = []
    second = node.OpenClawNodeClient(persisted.openclaw_node, lambda status: states.append(status["state"]))
    second.settings.enable = True
    second._crypto = client._crypto
    second._public_key = client._public_key
    ws = Socket()

    class Connection:
        async def __aenter__(self):
            return ws

        async def __aexit__(self, *args):
            return False

    async def receive(socket):
        second.settings.enable = False

    monkeypatch.setattr(node, "connect", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(second, "_receive", receive)
    await second.run()
    assert not second._is_setup()
    assert ws.sent[0]["params"]["auth"] == {"token": "issued-fixture"}
    assert states == ["connecting", "connected"]
    assert second.status()["token_stored"]
    assert persisted.openclaw_node.device_token == ""


@pytest.mark.parametrize("available", [True, False])
async def test_legacy_token_migration_and_fallback(available, secure, persisted):
    secure[2].side_effect = None
    secure[2].return_value = available
    instance = node.OpenClawNodeClient(OpenClawNodeConfig(device_token="fixture"))
    await instance._resolve_token()
    await instance._persist()
    assert instance.settings.device_token == "fixture"
    assert persisted.openclaw_node.device_token == ("" if available else "fixture")


async def test_issued_token_plaintext_fallback(client, persisted, monkeypatch):
    monkeypatch.setattr(node, "set_secret", AsyncMock(return_value=False))
    monkeypatch.setattr(client, "_persist", node.OpenClawNodeClient._persist.__get__(client))
    assert await client._handshake(Socket(), {"bootstrapToken": "fixture"})
    assert persisted.openclaw_node.device_token == "issued-fixture"


async def test_pairing_required_keeps_setup(client, secure):
    credential = setup_code({"url": "ws://example.test", "bootstrapToken": "fixture"})
    client.settings.credential = credential
    client.settings.credential_kind = "setup_code"
    assert not await client._handshake(Socket(pairing=True), {"bootstrapToken": "fixture"})
    assert client.settings.credential == credential
    assert client.settings.credential_kind == "setup_code"
    client._persist.assert_not_awaited()
    secure[2].assert_not_awaited()


def test_pairing_backoff():
    for jitter in (0, 0.5, 1):
        values = [node.next_pairing_backoff(i, jitter=lambda value=jitter: value) for i in range(100)]
        assert values == sorted(values)
        assert all(5 <= value <= 10 for value in values)
    assert node.next_pairing_backoff(100000) == 10


async def test_token_rejection_stops_loop(client, secure, persisted, monkeypatch):
    secure[0][node.OPENCLAW_NODE_TOKEN] = "fixture"
    monkeypatch.setattr(client, "_persist", node.OpenClawNodeClient._persist.__get__(client))
    ws = Socket()

    async def send(raw):
        frame = json.loads(raw)
        ws.sent.append(frame)
        ws.queue.put_nowait(json.dumps({"type": "res", "id": frame["id"], "ok": False,
                                      "error": {"code": "TOKEN_REJECTED"}}))

    ws.send = send

    async def connection(crypto):
        assert not await client._handshake(ws, {"token": client.settings.device_token})

    connect_mock = AsyncMock(side_effect=connection)
    monkeypatch.setattr(client, "_connection", connect_mock)
    sleep = AsyncMock()
    monkeypatch.setattr(node.asyncio, "sleep", sleep)
    await client.run()
    secure[3].assert_awaited_once_with(node.OPENCLAW_NODE_TOKEN)
    assert not secure[0]
    assert persisted.openclaw_node.device_token == ""
    assert client.settings.device_token == ""
    assert client.status()["state"] == "token_rejected"
    assert not client.status()["paired"]
    connect_mock.assert_awaited_once()
    sleep.assert_not_awaited()


async def test_pairing_timeout_stops_loop(client, secure, monkeypatch):
    monkeypatch.setattr(node, "_MAX_PAIRING_ATTEMPTS", 3)
    client.settings.credential = "fixture"
    client.settings.credential_kind = "setup_code"

    async def connection(crypto):
        await client._handshake(Socket(pairing=True), {"bootstrapToken": "fixture"})

    connect_mock = AsyncMock(side_effect=connection)
    monkeypatch.setattr(client, "_connection", connect_mock)
    sleep = AsyncMock()
    monkeypatch.setattr(node.asyncio, "sleep", sleep)
    await client.run()
    assert connect_mock.await_count == 3
    assert sleep.await_count == 2
    assert all(5 <= call.args[0] <= 10 for call in sleep.await_args_list)
    assert client.status()["state"] == "pairing_timeout"
    assert client.settings.credential == "fixture"
    client._persist.assert_not_awaited()


async def test_forget_token_clears_store_and_stops_client(client, persisted, monkeypatch):
    from jenny.runtime import secure_store
    from jenny.webui import settings_api

    persisted.openclaw_node.device_token = "fixture"
    persisted.openclaw_node.credential = "fixture"
    persisted.openclaw_node.credential_kind = "token"
    node.set_active_client(client)
    remove = AsyncMock(return_value=True)
    monkeypatch.setattr(secure_store, "remove_secret", remove)
    monkeypatch.setattr(settings_api.store, "mutate", node.mutate)
    monkeypatch.setattr(settings_api, "settings_payload", lambda: settings_api._openclaw_node_payload(persisted))
    result = await settings_api.update_openclaw_node_settings({"forget_token": ["1"]})
    remove.assert_awaited_once_with(node.OPENCLAW_NODE_TOKEN)
    assert not persisted.openclaw_node.device_token
    assert not persisted.openclaw_node.credential
    assert not persisted.openclaw_node.credential_kind
    assert not result["paired"]
    assert node.active_status()["state"] == "disabled"


async def test_payload_reports_secure_pairing(client, secure, persisted):
    from jenny.webui.settings_api import _openclaw_node_payload

    secure[0][node.OPENCLAW_NODE_TOKEN] = "fixture"
    await client._resolve_token()
    node.set_active_client(client)
    try:
        payload = _openclaw_node_payload(persisted)
        assert payload["paired"]
        assert payload["token_stored"]
        assert not payload["has_credential"]
    finally:
        node.set_active_client(None)


async def test_hello_without_token_keeps_setup(client, secure):
    client.settings.credential = "fixture"
    client.settings.credential_kind = "setup_code"
    ws = Socket()

    async def send(raw):
        frame = json.loads(raw)
        ws.queue.put_nowait(json.dumps({"type": "res", "id": frame["id"], "ok": True,
                                      "payload": {"type": "hello-ok"}}))

    ws.send = send
    assert await client._handshake(ws, {"bootstrapToken": "fixture"})
    assert client.settings.credential == "fixture"
    assert client.settings.credential_kind == "setup_code"
    secure[2].assert_not_awaited()


async def test_normal_disconnect_uses_existing_backoff(client, secure, monkeypatch):
    async def connection(crypto):
        raise OSError("fixture disconnect")

    async def sleep(delay):
        client.settings.enable = False

    normal = AsyncMock(side_effect=sleep)
    monkeypatch.setattr(client, "_connection", connection)
    monkeypatch.setattr(node.asyncio, "sleep", normal)
    monkeypatch.setattr(node, "next_backoff", lambda attempt: 42)
    await client.run()
    normal.assert_awaited_once_with(42)
    secure[3].assert_not_awaited()
    assert client.status()["state"] == "error"


def test_command_names():
    assert node.command_names() == [
        "ui.dump", "ui.tap", "ui.swipe", "ui.type", "ui.press",
        "clipboard.get", "clipboard.set", "voice.speak", "voice.listen",
    ]


@pytest.mark.parametrize("command,params", [
    ("ui.tap", {}), ("ui.tap", {"x": 1}), ("ui.tap", {"x": True, "y": 2}),
    ("ui.tap", {"index": False}), ("ui.tap", {"id": ""}),
    ("ui.tap", {"index": 0, "id": "a"}), ("ui.tap", {"index": 0, "x": 1, "y": 2}),
    ("ui.tap", {"x": float("inf"), "y": 2}), ("ui.tap", {"index": 1.0}),
    ("ui.swipe", {}), ("ui.swipe", {"x1": 0, "y1": 0, "x2": 1, "y2": True}),
    ("ui.swipe", {"x1": 0, "y1": 0, "x2": 1, "y2": 2, "durationMs": False}),
    ("ui.type", {}), ("ui.type", {"text": ""}), ("ui.type", {"text": 1}),
    ("ui.type", {"text": "hi", "index": True}), ("ui.type", {"text": "hi", "id": ""}),
    ("ui.type", {"text": "hi", "index": 0, "id": "a"}),
    ("ui.press", {}), ("ui.press", {"action": 1}), ("ui.press", {"action": "delete"}),
    ("voice.listen", {"seconds": "5"}), ("voice.listen", {"seconds": True}),
    ("voice.listen", {"seconds": float("nan")}), ("voice.listen", {"seconds": float("inf")}),
    ("voice.listen", {"language": 1}),
])
async def test_new_dispatch_invalid_params(command, params):
    mocks = handlers()
    assert await node.dispatch_command(command, params, **mocks) == node.command_error("invalid_params", "invalid_params")
    assert not any(mock.await_count for mock in mocks.values())


@pytest.mark.parametrize("code", sorted(node._PASSTHROUGH_ERRORS))
@pytest.mark.parametrize("hint", [None, "Enable the service"])
async def test_dispatch_passthrough(code, hint):
    result = await node.dispatch_command("ui.tap", {"index": 0}, **handlers({"ok": False, "error": code, "hint": hint}))
    assert result == node.command_error(code, hint or code)


@pytest.mark.parametrize("seconds,expected", [(0, 1), (99, 15), (2.5, 2.5)])
async def test_listen_clamp_and_timeout(monkeypatch, seconds, expected):
    from unittest.mock import Mock

    original = asyncio.timeout
    timeout = Mock(side_effect=original)
    monkeypatch.setattr(node.asyncio, "timeout", timeout)
    mocks = handlers({"ok": True})
    assert (await node.dispatch_command("voice.listen", {"seconds": seconds}, timeout=1, **mocks))["ok"]
    mocks["listen"].assert_awaited_once_with(expected, language="")
    timeout.assert_called_once_with(expected + 6.0)
