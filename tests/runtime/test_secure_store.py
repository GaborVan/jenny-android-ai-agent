"""Verifica il bridge dei segreti senza Android e con risposte JSON simulate."""

import json

import pytest

from jenny.runtime import secure_store


@pytest.fixture(autouse=True)
def reset():
    secure_store.reset_secure_store_state()
    yield
    secure_store.reset_secure_store_state()


async def test_no_android_context(monkeypatch):
    monkeypatch.setattr(secure_store, "get_android_context", lambda: None)
    assert await secure_store.get_secret("name") is None
    assert await secure_store.set_secret("name", "fixture") is False
    assert await secure_store.remove_secret("name") is False


async def test_bridge_json_and_reset(monkeypatch):
    values = {}
    instances = []

    class Bridge:
        def __init__(self, context):
            instances.append(self)

        def getSecret(self, name):  # noqa: N802
            return json.dumps({"ok": True, "value": values.get(name)})

        def putSecret(self, name, value):  # noqa: N802
            values[name] = value
            return '{"ok":true}'

        def removeSecret(self, name):  # noqa: N802
            values.pop(name, None)
            return '{"ok":true}'

    monkeypatch.setattr(secure_store, "get_android_context", object)
    monkeypatch.setattr(secure_store, "_resolve_bridge_class", lambda: Bridge)
    assert await secure_store.get_secret("name") is None
    assert await secure_store.set_secret("name", "fixture")
    assert await secure_store.get_secret("name") == "fixture"
    assert len(instances) == 1
    secure_store.reset_secure_store_state()
    assert await secure_store.get_secret("name") == "fixture"
    assert len(instances) == 2
    assert await secure_store.remove_secret("name")
    assert await secure_store.get_secret("name") is None


@pytest.mark.parametrize("response", ['[]', '{', 'null', '{"ok":false}', '{"ok":1}'])
async def test_invalid_bridge_response(monkeypatch, response):
    class Bridge:
        def __getattr__(self, name):
            return lambda *args: response

    monkeypatch.setattr(secure_store, "get_android_context", object)
    monkeypatch.setattr(secure_store, "_resolve_bridge_class", lambda: lambda context: Bridge())
    assert await secure_store.get_secret("name") is None
    assert not await secure_store.set_secret("name", "fixture")
    assert not await secure_store.remove_secret("name")


async def test_bridge_failure(monkeypatch):
    def broken():
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(secure_store, "get_android_context", object)
    monkeypatch.setattr(secure_store, "_resolve_bridge_class", broken)
    assert await secure_store.get_secret("name") is None
    assert not await secure_store.set_secret("name", "fixture")
    assert not await secure_store.remove_secret("name")
