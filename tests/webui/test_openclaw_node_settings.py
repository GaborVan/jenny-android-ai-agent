"""Il nodo OpenClaw visto dalla WebUI: payload, classificazione e rotte.

Il filo conduttore è la **riservatezza**: setup code, device token e chiave
privata non devono mai tornare in un payload né finire in un errore. Il resto è
il contratto delle rotte — auth, 400 su URL non valido, ``connect`` che riavvia
il client con la config appena salvata.
"""

from __future__ import annotations

import json
import urllib.parse
from unittest.mock import MagicMock

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import check_api_secret, http_error, http_json_response, parse_query
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.runtime.openclaw_node import command_names
from jenny.webui.settings_api import (
    WebUISettingsError,
    _openclaw_node_payload,
    accessibility_status_payload,
    settings_payload,
    update_openclaw_node_settings,
)
from jenny.webui.settings_routes import WebUISettingsRouter

_SECRET = "s3cr3t-openclaw"


def _request(path: str, token: str | None = _SECRET) -> WsRequest:
    if token is not None and "token=" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}token={urllib.parse.quote(token)}"
    return WsRequest(path=path, headers=Headers())


def _router(**overrides) -> WebUISettingsRouter:
    kwargs: dict = {
        "bus": MagicMock(),
        "logger": MagicMock(),
        "check_api_token": lambda request: check_api_secret(request.headers, request.path, _SECRET),
        "parse_query": parse_query,
        "json_response": http_json_response,
        "error_response": http_error,
    }
    kwargs.update(overrides)
    return WebUISettingsRouter(**kwargs)


@pytest.fixture()
def config_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def test_payload_never_contains_secrets() -> None:
    config = Config()
    config.openclaw_node.enable = True
    config.openclaw_node.credential = "oc-pair://SUPERSECRET"
    config.openclaw_node.credential_kind = "setup_code"
    config.openclaw_node.device_token = "device-token-SECRET"
    config.openclaw_node.device_private_key = "private-key-SECRET"
    config.openclaw_node.device_id = "a" * 64

    payload = _openclaw_node_payload(config)
    blob = json.dumps(payload)
    assert payload["commands"] == command_names()

    assert payload["has_credential"] is True
    assert payload["paired"] is True
    assert payload["credential_kind"] == "setup_code"
    assert "SUPERSECRET" not in blob
    assert "device-token-SECRET" not in blob
    assert "private-key-SECRET" not in blob


def test_settings_payload_includes_the_section(config_path) -> None:
    payload = settings_payload()
    assert "openclaw_node" in payload
    assert "status" in payload["openclaw_node"]


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def test_update_classifies_a_setup_code(config_path) -> None:
    await update_openclaw_node_settings(
        {
            "enable": ["1"],
            "url": ["wss://gw.example.test:433"],
            "credential_token": ["oc-pair://payload"],
        }
    )
    node = load_config().openclaw_node
    assert node.enable is True
    assert node.url == "wss://gw.example.test:433"
    assert node.credential == "oc-pair://payload"
    assert node.credential_kind == "setup_code"


async def test_update_classifies_a_shared_token(config_path) -> None:
    await update_openclaw_node_settings({"credential_token": ["shared-token"]})
    node = load_config().openclaw_node
    assert node.credential == "shared-token"
    assert node.credential_kind == "token"


async def test_update_clears_the_credential(config_path) -> None:
    await update_openclaw_node_settings({"credential_token": ["token"]})
    await update_openclaw_node_settings({"credential_token": [""]})
    node = load_config().openclaw_node
    assert node.credential == ""
    assert node.credential_kind == ""


async def test_update_rejects_a_non_ws_url(config_path) -> None:
    with pytest.raises(WebUISettingsError):
        await update_openclaw_node_settings({"url": ["https://not-a-gateway"]})
    assert load_config().openclaw_node.url.startswith("wss://")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def test_status_requires_auth(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/openclaw-node/status", token=None),
        "/api/settings/openclaw-node/status",
    )
    assert response.status_code == 401


async def test_status_returns_the_section(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/openclaw-node/status"),
        "/api/settings/openclaw-node/status",
    )
    assert response.status_code == 200
    assert "status" in _json(response)


async def test_update_route_persists(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/openclaw-node/update?enable=1&url=wss://gw.test:433"),
        "/api/settings/openclaw-node/update",
    )
    assert response.status_code == 200
    node = load_config().openclaw_node
    assert node.enable is True
    assert node.url == "wss://gw.test:433"


async def test_update_route_maps_a_bad_url_to_400(config_path) -> None:
    response = await _router().dispatch(
        _request("/api/settings/openclaw-node/update?url=http://nope"),
        "/api/settings/openclaw-node/update",
    )
    assert response.status_code == 400


async def test_connect_route_forces_enable_and_restarts(config_path, monkeypatch) -> None:
    import jenny.runtime.openclaw_node as openclaw_node

    calls: list[str] = []

    async def fake_restart() -> dict:
        calls.append("restart")
        return {"state": "connecting", "detail": "", "request_id": None, "last_error": None}

    monkeypatch.setattr(openclaw_node, "restart_active_client", fake_restart)

    response = await _router().dispatch(
        _request("/api/settings/openclaw-node/connect?url=wss://gw.test:433"),
        "/api/settings/openclaw-node/connect",
    )

    assert response.status_code == 200
    assert calls == ["restart"]
    assert load_config().openclaw_node.enable is True


@pytest.mark.parametrize("enabled", [None, True, False])
async def test_accessibility_payload_is_android_gated(monkeypatch, enabled):
    async def fake_enabled():
        return enabled

    monkeypatch.setattr("jenny.runtime.ui_automation.accessibility_enabled", fake_enabled)
    assert await accessibility_status_payload() == {
        "android": enabled is not None,
        "enabled": bool(enabled),
        "clipboard": enabled is not None,
    }


@pytest.mark.parametrize("action", ["status", "open"])
async def test_accessibility_status_route_requires_auth(action):
    path = f"/api/settings/accessibility/{action}"
    response = await _router().dispatch(_request(path, token=None), path)
    assert response.status_code == 401


async def test_accessibility_status_route_returns_the_flags(monkeypatch):
    async def fake_enabled():
        return True

    monkeypatch.setattr("jenny.runtime.ui_automation.accessibility_enabled", fake_enabled)
    path = "/api/settings/accessibility/status"
    response = await _router().dispatch(_request(path), path)
    assert response.status_code == 200
    assert _json(response) == {"android": True, "enabled": True, "clipboard": True}


async def test_accessibility_open_route_calls_the_bridge(monkeypatch):
    calls = []

    async def fake_open():
        calls.append("open")
        return {"ok": True}

    monkeypatch.setattr("jenny.runtime.ui_automation.open_accessibility_settings", fake_open)
    path = "/api/settings/accessibility/open"
    response = await _router().dispatch(_request(path), path)
    assert response.status_code == 200
    assert _json(response) == {"ok": True}
    assert calls == ["open"]


@pytest.mark.parametrize("result, status", [(None, 503), ({"ok": False, "error": "failed"}, 500)])
async def test_accessibility_open_route_reports_failure(monkeypatch, result, status):
    async def fake_open():
        return result

    monkeypatch.setattr("jenny.runtime.ui_automation.open_accessibility_settings", fake_open)
    path = "/api/settings/accessibility/open"
    response = await _router().dispatch(_request(path), path)
    assert response.status_code == status
