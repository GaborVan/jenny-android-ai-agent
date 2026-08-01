"""Adapter di route HTTP per Jenny Apps (estratto da ws_http).

Gestisce lo static server delle app (``/apps/{slug}/...``), la lista
(``/api/webui/apps``) e l'esecuzione azioni (``/api/apps/{slug}/actions/{action}``).
Stesso pattern router di ``SkillsRoutes``/``WikiRoutes``/``WorkspaceRoutes``.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.apps.manifest import SLUG_RE as APP_SLUG_RE
from jenny.channels.http_utils import (
    http_error,
    http_json_response,
    http_response,
    parse_query,
    query_first,
)

APP_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Request-line budget is 8192 bytes (websockets); leave headroom for path+token.
APP_PARAMS_MAX_CHARS = 6000
# App iframes run with an opaque origin (sandbox without allow-same-origin):
# every action fetch is cross-origin, so responses need ACAO on ALL statuses.
# Auth stays the explicit ?token= param (never cookies), so "*" is safe.
APP_CORS_HEADERS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Cache-Control", "no-store"),
]


class AppsRoutes:
    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_workspace_root: Callable[[], Path],
        log: Any,
    ) -> None:
        self._check_api_token = check_api_token
        self._get_workspace_root = get_workspace_root
        self._log = log

    def _check_apps_enabled(self) -> Response | None:
        from jenny.config.loader import load_config

        try:
            if not load_config().apps.enabled:
                return http_error(503, "apps are disabled")
        except Exception:
            pass
        return None

    def _apps_config_values(self) -> tuple[float, int]:
        from jenny.config.loader import load_config

        try:
            apps = load_config().apps
            return apps.http_timeout_s, apps.max_collection_bytes
        except Exception:
            return 20.0, 5_000_000

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path.startswith("/apps/"):
            return self._static(request, path)
        if path == "/api/webui/apps":
            return self._list(request)
        m = re.match(r"^/api/webui/apps/([^/]+)/delete$", path)
        if m:
            return self._delete(request, m.group(1))
        m = re.match(r"^/api/apps/([^/]+)/actions/([^/]+)$", path)
        if m:
            return await self._action(request, m.group(1), m.group(2))
        return None

    def _list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return disabled
        from jenny.webui.apps_api import list_apps_payload

        return http_json_response(list_apps_payload(self._get_workspace_root()))

    def _delete(self, request: WsRequest, raw_slug: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return disabled
        slug = unquote(raw_slug)
        if not slug or APP_SLUG_RE.match(slug) is None:
            return http_error(400, "invalid app slug")
        from jenny.webui.apps_api import delete_app

        try:
            return http_json_response(delete_app(self._get_workspace_root(), slug))
        except ValueError:
            return http_error(400, "invalid app slug")
        except FileNotFoundError:
            return http_error(404, "app not found")
        except Exception as e:
            self._log.warning("app delete {} failed: {}", slug, e)
            return http_error(500, "internal error")

    async def _action(self, request: WsRequest, raw_slug: str, raw_action: str) -> Response:
        def respond(payload: dict, status: int) -> Response:
            return http_json_response(payload, status=status, extra_headers=APP_CORS_HEADERS)

        if not self._check_api_token(request):
            return respond({"ok": False, "error": "Unauthorized"}, 401)
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return respond({"ok": False, "error": "apps are disabled"}, 503)

        slug = unquote(raw_slug)
        action = unquote(raw_action)
        if not slug or APP_SLUG_RE.match(slug) is None:
            return respond({"ok": False, "error": "invalid app slug"}, 400)
        if not action or APP_ACTION_RE.match(action) is None:
            return respond({"ok": False, "error": "invalid action name"}, 400)

        raw_params = query_first(parse_query(request.path), "params") or ""
        if len(raw_params) > APP_PARAMS_MAX_CHARS:
            return respond(
                {"ok": False, "error": f"params exceed {APP_PARAMS_MAX_CHARS} chars"}, 413
            )

        from jenny.webui.apps_api import execute_app_action_payload

        http_timeout_s, max_collection_bytes = self._apps_config_values()
        try:
            payload, status = await execute_app_action_payload(
                self._get_workspace_root(),
                slug,
                action,
                raw_params,
                http_timeout_s=http_timeout_s,
                max_collection_bytes=max_collection_bytes,
            )
        except Exception as e:
            self._log.warning("app action {}/{} failed: {}", slug, action, e)
            return respond({"ok": False, "error": "internal error"}, 500)
        return respond(payload, status)

    def _static(self, request: WsRequest, got: str) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        disabled = self._check_apps_enabled()
        if disabled is not None:
            return disabled

        parts = got[len("/apps/") :].split("/", 1)
        slug = unquote(parts[0])
        rel = unquote(parts[1]) if len(parts) > 1 and parts[1] else "index.html"
        if not slug or APP_SLUG_RE.match(slug) is None:
            return http_error(400, "invalid app slug")
        if ".." in rel.split("/") or rel.startswith("/"):
            return http_error(403, "Forbidden")

        # Only the app/ subfolder is web-reachable: manifest, AGENT.md and
        # data/ stay off the wire — data is only accessible through actions.
        apps_root = (self._get_workspace_root() / "apps").resolve()
        candidate = (apps_root / slug / "app" / rel).resolve()
        try:
            candidate.relative_to(apps_root / slug / "app")
        except ValueError:
            return http_error(403, "Forbidden")
        if not candidate.is_file():
            return http_error(404, "Not Found")
        try:
            body = candidate.read_bytes()
        except OSError as e:
            self._log.warning("app static: failed to read {}: {}", candidate, e)
            return http_error(500, "Internal Server Error")
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype = f"{ctype}; charset=utf-8"
        return http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", "no-store")],
        )
