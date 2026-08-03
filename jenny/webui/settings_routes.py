"""HTTP route adapter for WebUI Settings APIs.

Keep WebUI Settings route handlers here, not in ``channels/websocket.py``.
The websocket channel owns transport concerns; this module owns WebUI Settings
request mapping and response shaping.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.bus.queue import MessageBus
from jenny.webui.settings_api import (
    WebUISettingsError,
    delete_provider,
    provider_models_payload,
    save_onboarding,
    settings_payload,
    update_agent_settings,
    update_location_settings,
    update_provider,
    update_web_search_settings,
)

QueryParams = dict[str, list[str]]


class WebUISettingsRouter:
    """Route WebUI Settings HTTP requests behind a transport-neutral boundary."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        logger: Any,
        check_api_token: Callable[[WsRequest], bool],
        parse_query: Callable[[str], QueryParams],
        json_response: Callable[[dict[str, Any]], Response],
        error_response: Callable[[int, str | None], Response],
        session_manager: Any | None = None,
        onboarding_event: Any | None = None,
        on_settings_changed: Callable[[], None] | None = None,
        on_telegram_changed: Callable[[], None] | None = None,
    ) -> None:
        self.bus = bus
        self.logger = logger
        self._check_api_token = check_api_token
        self._parse_query = parse_query
        self._json_response = json_response
        self._error_response = error_response
        self._session_manager = session_manager
        self._onboarding_event = onboarding_event
        self._on_settings_changed = on_settings_changed
        self._on_telegram_changed = on_telegram_changed

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path == "/api/settings":
            return self._handle_settings(request)
        if path == "/api/settings/update":
            return self._handle_settings_update(request)
        if path == "/api/settings/provider/update":
            return await self._handle_settings_provider_update(request)
        if path == "/api/settings/provider/delete":
            return await self._handle_settings_provider_delete(request)
        if path == "/api/settings/provider-models":
            return await self._handle_settings_provider_models(request)
        if path == "/api/settings/web-search/update":
            return self._handle_settings_web_search_update(request)
        if path == "/api/settings/location/update":
            return self._handle_settings_location_update(request)
        if path == "/api/onboarding/save":
            return await self._handle_onboarding_save(request)
        if path == "/api/telegram/status":
            return self._handle_telegram_status(request)
        if path == "/api/telegram/save":
            return await self._handle_telegram_save(request)
        if path == "/api/telegram/unpair":
            return self._handle_telegram_unpair(request)
        if path == "/api/telegram/disable":
            return self._handle_telegram_disable(request)
        return None

    def _query(self, request: WsRequest) -> QueryParams:
        return self._parse_query(request.path)

    def _authorized(self, request: WsRequest) -> bool:
        return self._check_api_token(request)

    def _unauthorized(self) -> Response:
        return self._error_response(401, "Unauthorized")

    def _fire_settings_changed(self) -> None:
        if self._on_settings_changed:
            try:
                self._on_settings_changed()
            except Exception:
                self.logger.exception("on_settings_changed callback failed")

    def _handle_settings(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(settings_payload())

    def _handle_settings_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            query = self._query(request)
            payload = update_agent_settings(query)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        # I parametri di generazione vivono in provider.generation, costruito una
        # volta in factory.make_provider: senza rebuild resterebbero scritti nel
        # config e inerti fino al riavvio, e la UI non mostra requires_restart.
        if any(
            key in query
            for key in (
                "model", "default_provider",
                "max_tokens", "maxTokens",
                "temperature",
                "reasoning_effort", "reasoningEffort",
            )
        ):
            self._fire_settings_changed()
        return self._json_response(payload)

    async def _handle_settings_provider_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        data: dict[str, str] = {"name": _query_param(query, "name")}
        if fmt := _query_param(query, "format"):
            data["format"] = fmt
        if api_key := _query_param(query, "api_key"):
            data["api_key"] = api_key
        if api_base := _query_param(query, "api_base"):
            data["api_base"] = api_base
        try:
            payload = await update_provider(data)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        if data.get("name") and payload.get("default_provider") == data["name"]:
            self._fire_settings_changed()
        return self._json_response(payload)

    async def _handle_settings_provider_delete(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        name = _query_param(query, "name")
        data = {"name": name}
        try:
            payload = await delete_provider(data)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        if name:
            self._fire_settings_changed()
        return self._json_response(payload)

    async def _handle_settings_provider_models(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(provider_models_payload, self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("failed to load provider model list")
            return self._error_response(500, "failed to load provider model list")
        # Diagnostica: il fetch modelli è advisory e non solleva su status
        # applicativi (not_configured/error/unsupported), quindi senza questo log
        # una lista vuota resterebbe invisibile nei log del gateway.
        self.logger.info(
            "[provider-models] provider={!r} status={!r} count={} message={!r}",
            payload.get("provider"),
            payload.get("status"),
            payload.get("model_count"),
            payload.get("message"),
        )
        return self._json_response(payload)

    def _handle_settings_web_search_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_web_search_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("web search settings update failed")
            return self._error_response(500, "failed to update web search settings")
        return self._json_response(payload)

    def _handle_settings_location_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_location_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("location settings update failed")
            return self._error_response(500, "failed to update location settings")
        return self._json_response(payload)

    async def _handle_onboarding_save(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        data = {
            "provider_name": _query_param(query, "provider_name"),
            "format": _query_param(query, "format"),
            "api_key": _query_param(query, "api_key"),
            "api_base": _query_param(query, "api_base"),
            "model": _query_param(query, "model"),
            "bot_name": _query_param(query, "bot_name"),
            "bot_icon": _query_param(query, "bot_icon"),
            "locale": _query_param(query, "locale"),
        }
        self.logger.info(
            "[onboarding-route] received: provider_name={!r} format={!r} model={!r} "
            "api_key_len={} bot_name={!r} query_keys={}",
            data["provider_name"],
            data["format"],
            data["model"],
            len(data["api_key"]),
            data["bot_name"],
            sorted(query.keys()),
        )
        try:
            payload = await save_onboarding(
                data,
                session_manager=self._session_manager,
                onboarding_event=self._onboarding_event,
            )
        except WebUISettingsError as e:
            self.logger.warning("[onboarding-route] settings error: {}", e.message)
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("onboarding save failed")
            return self._error_response(500, "failed to save onboarding configuration")
        self.logger.info(
            "[onboarding-route] success: chat_id={}",
            payload.get("chat_id"),
        )
        return self._json_response(payload)

    # -- Telegram ---------------------------------------------------------- #

    def _fire_telegram_changed(self) -> None:
        """Applica la config Telegram a caldo (ricrea/ferma il canale)."""
        if self._on_telegram_changed:
            try:
                self._on_telegram_changed()
            except Exception:
                self.logger.exception("on_telegram_changed callback failed")

    def _handle_telegram_status(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        from jenny.webui.telegram_api import telegram_status_payload

        try:
            return self._json_response(telegram_status_payload())
        except Exception:
            self.logger.exception("telegram status failed")
            return self._error_response(500, "failed to load telegram status")

    async def _handle_telegram_save(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        from jenny.webui.telegram_api import save_telegram_token

        token = _query_param(self._query(request), "token")
        try:
            payload = await save_telegram_token(token)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("telegram token save failed")
            return self._error_response(500, "failed to save telegram token")
        self._fire_telegram_changed()
        return self._json_response(payload)

    def _handle_telegram_unpair(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        from jenny.webui.telegram_api import unpair_telegram

        try:
            payload = unpair_telegram()
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("telegram unpair failed")
            return self._error_response(500, "failed to unpair telegram")
        self._fire_telegram_changed()
        return self._json_response(payload)

    def _handle_telegram_disable(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        from jenny.webui.telegram_api import disable_telegram

        try:
            payload = disable_telegram()
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("telegram disable failed")
            return self._error_response(500, "failed to disable telegram")
        self._fire_telegram_changed()
        return self._json_response(payload)


def _query_param(query: QueryParams, key: str) -> str:
    values = query.get(key)
    return values[0] if values else ""
