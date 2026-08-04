"""Test del router HTTP di WebUISettingsRouter (dispatch, auth, mapping errori).

``tests/webui/test_settings_api.py`` copre già la logica pura in
``jenny/webui/settings_api.py``; qui si copre invece lo strato di route:
dispatch per path, 401 senza token, propagazione degli errori applicativi
(400/404) e mapping degli errori inattesi a 500.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from unittest.mock import MagicMock

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import check_api_secret, http_error, http_json_response, parse_query
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config, ProviderConfig
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_routes import WebUISettingsRouter

_SECRET = "s3cr3t-settings"


def _request(path: str, token: str | None = _SECRET) -> WsRequest:
    if token is not None and "token=" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}token={urllib.parse.quote(token)}"
    return WsRequest(path=path, headers=Headers())


def _router(**overrides) -> WebUISettingsRouter:
    kwargs: dict = dict(
        bus=MagicMock(),
        logger=MagicMock(),
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        parse_query=parse_query,
        json_response=http_json_response,
        error_response=http_error,
    )
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
# Dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_returns_none_for_unrelated_path() -> None:
    router = _router()
    result = await router.dispatch(_request("/api/unrelated"), "/api/unrelated")
    assert result is None


# ---------------------------------------------------------------------------
# /api/settings
# ---------------------------------------------------------------------------


async def test_settings_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings", token=None), "/api/settings"
    )
    assert response.status_code == 401


async def test_settings_returns_payload(config_path) -> None:
    router = _router()
    response = await router.dispatch(_request("/api/settings"), "/api/settings")
    assert response.status_code == 200
    body = _json(response)
    assert "providers" in body
    assert "agent" in body


# ---------------------------------------------------------------------------
# /api/settings/update
# ---------------------------------------------------------------------------


async def test_settings_update_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/update", token=None), "/api/settings/update"
    )
    assert response.status_code == 401


async def test_settings_update_invalid_value_maps_to_400(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/update?context_window_tokens=128000"),
        "/api/settings/update",
    )
    assert response.status_code == 400


async def test_settings_update_fires_on_settings_changed_for_model(config_path) -> None:
    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    response = await router.dispatch(
        _request("/api/settings/update?model=gpt-x"), "/api/settings/update"
    )
    assert response.status_code == 200
    on_changed.assert_called_once()


async def test_settings_update_does_not_fire_for_unrelated_field(config_path) -> None:
    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    response = await router.dispatch(
        _request("/api/settings/update?timezone=Asia/Tokyo"), "/api/settings/update"
    )
    assert response.status_code == 200
    on_changed.assert_not_called()


async def test_settings_update_swallows_on_settings_changed_exception(config_path) -> None:
    logger = MagicMock()
    boom = MagicMock(side_effect=RuntimeError("kaboom"))
    router = _router(logger=logger, on_settings_changed=boom)
    response = await router.dispatch(
        _request("/api/settings/update?model=gpt-y"), "/api/settings/update"
    )
    # Il callback fallisce ma la risposta resta 200: il fallimento è solo loggato.
    assert response.status_code == 200
    logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# /api/settings/provider/update
# ---------------------------------------------------------------------------


async def test_provider_update_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider/update", token=None),
        "/api/settings/provider/update",
    )
    assert response.status_code == 401


async def test_provider_update_requires_name(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider/update?format=openai_compat"),
        "/api/settings/provider/update",
    )
    assert response.status_code == 400


async def test_provider_update_success_fires_settings_changed_when_default(config_path) -> None:
    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    response = await router.dispatch(
        _request("/api/settings/provider/update?name=my-provider&api_key=sk-test"),
        "/api/settings/provider/update",
    )
    assert response.status_code == 200
    body = _json(response)
    providers = {p["name"]: p for p in body["providers"]}
    assert "my-provider" in providers
    on_changed.assert_called_once()


# ---------------------------------------------------------------------------
# /api/settings/provider/delete
# ---------------------------------------------------------------------------


async def test_provider_delete_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider/delete?name=x", token=None),
        "/api/settings/provider/delete",
    )
    assert response.status_code == 401


async def test_provider_delete_success_fires_settings_changed(config_path) -> None:
    config = load_config(config_path)
    config.providers.providers.append(
        ProviderConfig(name="to-delete", format="openai_compat", api_key="sk-1")
    )
    save_config(config, config_path)

    on_changed = MagicMock()
    router = _router(on_settings_changed=on_changed)
    response = await router.dispatch(
        _request("/api/settings/provider/delete?name=to-delete"),
        "/api/settings/provider/delete",
    )
    assert response.status_code == 200
    on_changed.assert_called_once()
    saved = load_config(config_path)
    assert all(p.name != "to-delete" for p in saved.providers.providers)


# ---------------------------------------------------------------------------
# /api/settings/provider-models
# ---------------------------------------------------------------------------


async def test_provider_models_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider-models", token=None),
        "/api/settings/provider-models",
    )
    assert response.status_code == 401


async def test_provider_models_settings_error_maps_to_status(config_path, monkeypatch) -> None:
    from jenny.webui.settings_routes import WebUISettingsError

    def boom(query):
        raise WebUISettingsError("provider sconosciuto", status=404)

    monkeypatch.setattr("jenny.webui.settings_routes.provider_models_payload", boom)
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider-models?provider=openai"),
        "/api/settings/provider-models",
    )
    assert response.status_code == 404


async def test_provider_models_unexpected_error_maps_to_500(config_path, monkeypatch) -> None:
    def boom(query):
        raise RuntimeError("guasto inatteso")

    monkeypatch.setattr("jenny.webui.settings_routes.provider_models_payload", boom)
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/provider-models?provider=openai"),
        "/api/settings/provider-models",
    )
    assert response.status_code == 500
    assert b"guasto inatteso" not in response.body


# ---------------------------------------------------------------------------
# /api/settings/web-search/update
# ---------------------------------------------------------------------------


async def test_web_search_update_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/web-search/update", token=None),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 401


async def test_web_search_update_noop_returns_payload(config_path) -> None:
    # Regressione: la route leggeva ``config.android_web`` (inesistente, il
    # blocco vive in ``config.tools.android_web``) e QUALSIASI chiamata
    # esplodeva con AttributeError non gestita.
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/web-search/update"),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 200
    assert "web_search" in _json(response)


async def test_web_search_update_persists_values(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request(
            "/api/settings/web-search/update"
            "?search_engine=bing&max_results=7&timeout=45&fetch_max_chars=20000"
        ),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 200
    body = _json(response)["web_search"]
    assert body["search_engine"] == "bing"
    assert body["max_results"] == 7
    assert body["timeout"] == 45
    assert body["fetch_max_chars"] == 20000

    saved = load_config(config_path)
    assert saved.tools.android_web.search.max_results == 7
    assert saved.tools.android_web.search.timeout == 45
    assert saved.tools.android_web.fetch.max_chars == 20000


async def test_web_search_update_invalid_engine_maps_to_400(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/web-search/update?search_engine=altavista"),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 400


async def test_web_search_update_unexpected_error_maps_to_500(config_path, monkeypatch) -> None:
    def boom(query):
        raise RuntimeError("guasto inatteso")

    monkeypatch.setattr("jenny.webui.settings_routes.update_web_search_settings", boom)
    router = _router()
    response = await router.dispatch(
        _request("/api/settings/web-search/update?timeout=45"),
        "/api/settings/web-search/update",
    )
    assert response.status_code == 500
    assert b"guasto inatteso" not in response.body


# ---------------------------------------------------------------------------
# /api/onboarding/save
# ---------------------------------------------------------------------------


async def test_onboarding_save_requires_auth(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request("/api/onboarding/save", token=None), "/api/onboarding/save"
    )
    assert response.status_code == 401


async def test_onboarding_save_settings_error_maps_to_400(config_path) -> None:
    router = _router()
    response = await router.dispatch(
        _request(
            "/api/onboarding/save?provider_name=openai&format=openai_compat&model=gpt-x"
        ),
        "/api/onboarding/save",
    )
    assert response.status_code == 400


async def test_onboarding_save_success(config_path) -> None:
    router = _router(onboarding_event=asyncio.Event())
    response = await router.dispatch(
        _request(
            "/api/onboarding/save"
            "?provider_name=openai&format=openai_compat&model=gpt-x&api_key=sk-test-123"
        ),
        "/api/onboarding/save",
    )
    assert response.status_code == 200
    body = _json(response)
    assert body["chat_id"] == "default"


async def test_onboarding_save_unexpected_error_maps_to_500(config_path, monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("jenny.webui.settings_routes.save_onboarding", boom)
    router = _router()
    response = await router.dispatch(
        _request(
            "/api/onboarding/save"
            "?provider_name=openai&format=openai_compat&model=gpt-x&api_key=sk-test-123"
        ),
        "/api/onboarding/save",
    )
    assert response.status_code == 500
    assert b"kaboom" not in response.body


async def test_settings_update_fires_on_settings_changed_for_generation_params(
    config_path,
) -> None:
    """I parametri di generazione vivono in ``provider.generation``, costruito una
    volta in ``factory.make_provider``: senza rebuild resterebbero scritti nel
    config e inerti fino al riavvio, e la UI non mostra ``requires_restart``."""
    for field in ("max_tokens=16384", "temperature=0.5", "reasoning_effort=medium"):
        on_changed = MagicMock()
        router = _router(on_settings_changed=on_changed)
        response = await router.dispatch(
            _request(f"/api/settings/update?{field}"), "/api/settings/update"
        )
        assert response.status_code == 200, field
        on_changed.assert_called_once()
