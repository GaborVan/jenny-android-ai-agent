"""Settings REST helpers for the WebUI HTTP surface.

The WebSocket channel owns transport/authentication. This module owns the
settings payload shape and the allowlisted config mutations exposed to WebUI.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

from jenny import __version__
from jenny.agent.token_usage import token_usage_payload
from jenny.config.loader import get_config_path, load_config, save_config
from jenny.security.workspace_access import workspace_sandbox_status
from jenny.security.workspace_policy import _safe_expanduser
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.utils.helpers import validate_timezone_name

QueryParams = dict[str, list[str]]


def _version_payload() -> dict[str, Any]:
    """Return version info for the settings payload."""
    return {
        "current": __version__,
    }


# Il tool android_web supporta solo Bing (android_web.py rifiuta ogni altro
# valore): il motore è quindi una costante, non un flag selezionabile.
_ANDROID_WEB_SEARCH_ENGINE = "bing"

WELCOME_TEMPLATES: dict[str, str] = {
    "it": "Ciao sono {bot_name} e da oggi vivo sul tuo smartphone, molto piacere!",
    "en": "Hi, I'm {bot_name} and from today I live on your smartphone. Nice to meet you!",
}

_CONTEXT_WINDOW_TOKEN_OPTIONS = {65_536, 262_144}
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Vocabolario accettato dal layer provider (``openai_compat_provider._build_kwargs``
# normalizza "minimum" in "minimal"). La select della WebUI ne espone un
# sottoinsieme; l'API accetta tutto ciò che il provider sa gestire.
_REASONING_EFFORT_VALUES = frozenset(
    {"none", "minimal", "minimum", "low", "medium", "high"}
)


class _Unset:
    """Sentinella per "campo assente dalla richiesta" dove ``None`` è un valore."""


_UNSET = _Unset()


class WebUISettingsError(ValueError):
    """User-facing settings validation failure."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status



def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _query_first_alias(query: QueryParams, snake: str, camel: str) -> str | None:
    value = _query_first(query, snake)
    return _query_first(query, camel) if value is None else value


def _resolve_env_placeholders(value: str | None) -> str | None:
    if not value:
        return None
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        env_value = os.environ.get(match.group(1))
        if env_value is None:
            missing = True
            return ""
        return env_value

    resolved = _ENV_REF_RE.sub(replace, value).strip()
    if missing and not resolved:
        return None
    return resolved or None


def _model_id_from_row(row: Any) -> str | None:
    if isinstance(row, str):
        return row.strip() or None
    if not isinstance(row, dict):
        return None
    for key in ("id", "name", "model"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _model_context_window(row: Any) -> int | None:
    if not isinstance(row, dict):
        return None
    for key in (
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "max_input_tokens",
    ):
        value = row.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value > 0:
            return int(value)
    return None


def _model_row_payload(row: Any) -> dict[str, Any] | None:
    model_id = _model_id_from_row(row)
    if not model_id:
        return None
    label: str | None = None
    owned_by: str | None = None
    if isinstance(row, dict):
        raw_label = row.get("display_name") or row.get("label") or row.get("name")
        if isinstance(raw_label, str) and raw_label.strip() and raw_label.strip() != model_id:
            label = raw_label.strip()
        raw_owner = row.get("owned_by") or row.get("owner") or row.get("organization")
        if isinstance(raw_owner, str) and raw_owner.strip():
            owned_by = raw_owner.strip()
    return {
        "id": model_id,
        "label": label,
        "owned_by": owned_by,
        "context_window": _model_context_window(row),
    }


def _extract_model_rows(body: Any) -> list[dict[str, Any]]:
    raw_rows = body.get("data") if isinstance(body, dict) else body
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_row in raw_rows:
        row = _model_row_payload(raw_row)
        if row is None or row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows


def provider_models_payload(query: QueryParams) -> dict[str, Any]:
    """Fetch an OpenAI-compatible provider's model list for Settings.

    The result is advisory only: users can always type a custom model id. This
    helper deliberately avoids mutating config so probing model lists never
    changes runtime behavior.
    """
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    config = load_config()
    provider_config = None
    for p in config.providers.providers:
        if p.name == provider_name:
            provider_config = p
            break
    if provider_config is None:
        api_key = (_query_first(query, "api_key") or "").strip()
        if not api_key:
            raise WebUISettingsError("unknown provider")
        provider_format = (_query_first(query, "format") or "openai_compat").strip()
        api_base = (_query_first(query, "api_base") or "").strip()
        from jenny.config.schema import ProviderConfig
        provider_config = ProviderConfig(
            name=provider_name,
            format=provider_format,
            api_key=api_key,
            api_base=api_base,
        )

    base_payload: dict[str, Any] = {
        "provider": provider_config.name,
        "label": provider_config.name,
        "models": [],
        "model_count": 0,
        "message": None,
        "fetched_at": time.time(),
    }

    api_base = _resolve_env_placeholders(provider_config.api_base)
    if provider_config.name == "openai" and not api_base:
        api_base = "https://api.openai.com/v1"
    if provider_config.format == "anthropic" and not api_base:
        api_base = "https://api.anthropic.com"
    if not api_base:
        return {
            **base_payload,
            "status": "missing_api_base",
            "message": "Configure an API base URL to load models.",
        }

    api_key = _resolve_env_placeholders(provider_config.api_key)
    override_key = (_query_first(query, "api_key") or "").strip()
    if override_key:
        api_key = override_key
    if not api_key:
        return {
            **base_payload,
            "status": "not_configured",
            "message": "Configure this provider before loading models.",
        }

    headers = {"Accept": "application/json"}
    if provider_config.format == "anthropic":
        # Messages API: auth via x-api-key e /v1/models (la base non include /v1).
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        base = api_base.rstrip("/")
        models_url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    else:
        headers["Authorization"] = f"Bearer {api_key}"
        models_url = f"{api_base.rstrip('/')}/models"

    try:
        response = httpx.get(
            models_url,
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        rows = _extract_model_rows(response.json())
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            return {
                **base_payload,
                "status": "not_configured",
                "message": "The provider rejected the configured credential.",
            }
        return {
            **base_payload,
            "status": "error",
            "message": f"Model list request failed with HTTP {status}.",
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            **base_payload,
            "status": "error",
            "message": f"Could not load models: {exc}",
        }

    return {
        **base_payload,
        "status": "available",
        "models": rows,
        "model_count": len(rows),
    }


def _parse_context_window_tokens(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise WebUISettingsError("context_window_tokens must be an integer") from None
    if parsed not in _CONTEXT_WINDOW_TOKEN_OPTIONS:
        raise WebUISettingsError("context_window_tokens must be 65536 or 262144")
    return parsed


def _parse_max_tokens(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        raise WebUISettingsError("max_tokens must be an integer") from None
    if parsed < 1:
        raise WebUISettingsError("max_tokens must be at least 1")
    return parsed


def _parse_temperature(value: str | None) -> float | None:
    if value is None:
        return None
    # Un ``input type=number`` su locale italiano può arrivare con la virgola
    # come separatore decimale; rifiutarlo trasformerebbe un campo legittimo in
    # un errore che l'utente non sa spiegare.
    normalized = value.strip().replace(",", ".")
    try:
        parsed = float(normalized)
    except ValueError:
        raise WebUISettingsError("temperature must be a number") from None
    if not 0.0 <= parsed <= 2.0:
        raise WebUISettingsError("temperature must be between 0 and 2")
    return parsed


def _parse_reasoning_effort(value: str | None) -> str | None | _Unset:
    """Valida l'effort, distinguendo "non inviato" da "azzerato".

    La select manda la stringa vuota per l'opzione "—", che significa "lascia
    decidere al provider" e va scritta come ``None``. ``_UNSET`` è il caso in
    cui il campo non è nella richiesta: senza questa distinzione un update di un
    altro campo azzererebbe l'effort come effetto collaterale.
    """
    if value is None:
        return _UNSET
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in _REASONING_EFFORT_VALUES:
        allowed = ", ".join(sorted(_REASONING_EFFORT_VALUES))
        raise WebUISettingsError(f"reasoning_effort must be one of: {allowed}")
    return normalized


def _is_first_run(config: Any = None) -> bool:
    """Return True if no provider has been configured yet."""
    config_path = get_config_path()
    if not config_path.exists():
        return True
    try:
        if config is None:
            config = load_config()
        return len(config.providers.providers) == 0
    except Exception:
        return True


def _mask_api_key(api_key: str | None) -> str:
    """Suggerimento offuscato mostrato dalla UI al posto della chiave vera.

    Stringa vuota se la chiave è assente o troppo corta perché la maschera
    non ne riveli una parte sproporzionata.
    """
    if api_key and len(api_key) > 8:
        return api_key[:4] + "..." + api_key[-4:]
    return ""


def _provider_list_simple(config: Any = None) -> list[dict[str, Any]]:
    """Return configured providers as a simple list for the UI."""
    if config is None:
        config = load_config()
    result: list[dict[str, Any]] = []
    for p in config.providers.providers:
        api_key_hint = _mask_api_key(p.api_key)
        result.append({
            "name": p.name,
            "format": p.format,
            "api_key_hint": api_key_hint,
            "api_base": p.api_base,
            "configured": bool(p.api_key or p.api_base),
            "api_type": p.api_type,
        })
    return result


async def save_onboarding(
    data: dict[str, Any],
    *,
    session_manager: Any | None = None,
    onboarding_event: Any | None = None,
) -> dict[str, Any]:
    """Save onboarding configuration."""
    provider_name = (data.get("provider_name") or "").strip()
    provider_format = (data.get("format") or "openai_compat").strip()
    api_key = (data.get("api_key") or "").strip()
    api_base = (data.get("api_base") or "").strip()
    model = (data.get("model") or "").strip()
    bot_name = (data.get("bot_name") or "Jenny").strip()
    bot_icon = (data.get("bot_icon") or "✿").strip()

    if not provider_name:
        raise WebUISettingsError("provider_name is required")
    if not model:
        raise WebUISettingsError("model is required")

    config = load_config()

    # Set up the provider
    from jenny.config.schema import ProviderConfig

    config.providers.providers = [
        ProviderConfig(
            name=provider_name,
            format=provider_format,
            api_key=api_key or None,
            api_base=api_base or None,
        )
    ]
    config.providers.default = provider_name

    # Set the model and persona
    config.agents.defaults.model = model
    config.agents.defaults.bot_name = bot_name
    config.agents.defaults.bot_icon = bot_icon

    # Persist the user's language preference for future backend-localized messages.
    locale = (data.get("locale") or "it").strip().lower()
    config.agents.defaults.language = locale if locale in WELCOME_TEMPLATES else "it"

    # Fase 6.7: valida il provider PRIMA di persistere/segnalare. Se la config
    # non produce un provider valido, l'errore torna subito alla WebUI di
    # onboarding invece di fallire in modo asincrono e silenzioso nel
    # GatewayContainer (che reloada la stessa config e resterebbe senza agent).
    # Non salviamo una config con provider inutilizzabile.
    from jenny.providers.factory import make_provider

    try:
        make_provider(config)
    except (ValueError, RuntimeError) as exc:
        raise WebUISettingsError(f"Provider configuration is invalid: {exc}") from exc

    save_config(config)

    # Il saluto di benvenuto finisce nell'unica sessione unificata,
    # la stessa che la chat rilegge all'attach.
    chat_id = "default"
    greeting_template = WELCOME_TEMPLATES.get(config.agents.defaults.language, WELCOME_TEMPLATES["it"])
    greeting = greeting_template.format(bot_name=bot_name)
    if session_manager:
        session = session_manager.get_or_create(UNIFIED_SESSION_KEY)
        session.add_message("assistant", greeting, chat_id=chat_id)
        session_manager.save(session)

    if onboarding_event:
        onboarding_event.set()

    return {
        "status": "ok",
        "chat_id": chat_id,
        "welcome_message": greeting,
    }


def settings_payload(
    *,
    requires_restart: bool = False,
) -> dict[str, Any]:
    config = load_config()
    defaults = config.agents.defaults
    effective_preset = config.agents.defaults

    android_web_search = config.tools.android_web.search
    android_web_fetch = config.tools.android_web.fetch

    exec_config = config.tools.python_exec
    sandbox_status = workspace_sandbox_status(
        restrict_to_workspace=config.security.restrict_to_workspace,
        workspace=config.workspace_path,
    )

    payload = {
        "first_run": _is_first_run(config),
        "providers": _provider_list_simple(config),
        "default_provider": config.providers.default,
        "agent": {
            "model": effective_preset.model,
            "max_tokens": effective_preset.max_tokens,
            "context_window_tokens": effective_preset.context_window_tokens,
            "temperature": effective_preset.temperature,
            "reasoning_effort": effective_preset.reasoning_effort,
            "timezone": defaults.timezone,
            "bot_name": defaults.bot_name,
            "bot_icon": defaults.bot_icon,
            "tool_hint_max_length": defaults.tool_hint_max_length,
        },
        "web_search": {
            "search_engine": android_web_search.search_engine,
            "max_results": android_web_search.max_results,
            "timeout": android_web_search.timeout,
            "fetch_max_chars": android_web_fetch.max_chars,
        },
        "location": {
            "enabled": config.tools.location.enable,
        },
        "runtime": {
            "config_path": str(_safe_expanduser(get_config_path())),
            "workspace_path": str(config.workspace_path),
            "gateway_host": config.gateway.host,
            "gateway_port": config.gateway.port,
            "heartbeat": {
                "enabled": config.gateway.heartbeat.enabled,
                "interval_s": config.gateway.heartbeat.interval_s,
                "keep_recent_messages": config.gateway.heartbeat.keep_recent_messages,
            },
            "dream": {
                "schedule": defaults.dream.describe_schedule(),
            },
        },
        "usage": token_usage_payload(timezone_name=defaults.timezone),
        "advanced": {
            "workspace_sandbox": sandbox_status.as_dict(),
            "ssrf_whitelist_count": len(config.security.ssrf_whitelist),
            "exec_enabled": exec_config.enable,
        },
        "requires_restart": requires_restart,
        "version": _version_payload(),
    }
    return payload


def update_agent_settings(query: QueryParams) -> dict[str, Any]:
    config = load_config()
    defaults = config.agents.defaults
    changed = False
    restart_required = False

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("model is required")
        if defaults.model != model:
            defaults.model = model
            changed = True

    default_provider = _query_first(query, "default_provider")
    if default_provider is not None:
        default_provider = default_provider.strip()
        if not default_provider:
            raise WebUISettingsError("default_provider is required")
        provider_exists = any(
            p.name == default_provider
            for p in config.providers.providers
        )
        if not provider_exists:
            raise WebUISettingsError("unknown provider")
        if config.providers.default != default_provider:
            config.providers.default = default_provider
            changed = True

    context_window_tokens = _parse_context_window_tokens(
        _query_first_alias(query, "context_window_tokens", "contextWindowTokens")
    )
    if (
        context_window_tokens is not None
        and defaults.context_window_tokens != context_window_tokens
    ):
        defaults.context_window_tokens = context_window_tokens
        changed = True

    # I tre parametri di generazione. La WebUI li agganciava già all'auto-save
    # ma qui non c'era nessun ramo a raccoglierli: la richiesta arrivava,
    # ``changed`` restava False, e il client mostrava "Saved!" perché la risposta
    # è 200 comunque. Erano campi decorativi.
    max_tokens = _parse_max_tokens(_query_first_alias(query, "max_tokens", "maxTokens"))
    if max_tokens is not None and defaults.max_tokens != max_tokens:
        defaults.max_tokens = max_tokens
        changed = True

    temperature = _parse_temperature(_query_first(query, "temperature"))
    if temperature is not None and defaults.temperature != temperature:
        defaults.temperature = temperature
        changed = True

    reasoning_effort = _parse_reasoning_effort(
        _query_first_alias(query, "reasoning_effort", "reasoningEffort")
    )
    if (
        not isinstance(reasoning_effort, _Unset)
        and defaults.reasoning_effort != reasoning_effort
    ):
        defaults.reasoning_effort = reasoning_effort
        changed = True

    timezone = _query_first(query, "timezone")
    if timezone is not None:
        timezone = timezone.strip()
        if not timezone:
            raise WebUISettingsError("timezone is required")
        # Rifiuta i nomi IANA sconosciuti (degrada ad accettare se tzdata manca).
        if err := validate_timezone_name(timezone):
            raise WebUISettingsError(err)
        if defaults.timezone != timezone:
            defaults.timezone = timezone
            changed = True
            restart_required = True

    bot_name = _query_first_alias(query, "bot_name", "botName")
    if bot_name is not None:
        bot_name = bot_name.strip()
        if not bot_name:
            raise WebUISettingsError("bot_name is required")
        if defaults.bot_name != bot_name:
            defaults.bot_name = bot_name
            changed = True
            restart_required = True

    bot_icon = _query_first_alias(query, "bot_icon", "botIcon")
    if bot_icon is not None:
        bot_icon = bot_icon.strip()
        if defaults.bot_icon != bot_icon:
            defaults.bot_icon = bot_icon
            changed = True
            restart_required = True

    tool_hint_max_length = _query_first_alias(
        query,
        "tool_hint_max_length",
        "toolHintMaxLength",
    )
    if tool_hint_max_length is not None:
        try:
            parsed = int(tool_hint_max_length)
        except ValueError:
            raise WebUISettingsError("tool_hint_max_length must be an integer") from None
        if parsed < 20 or parsed > 500:
            raise WebUISettingsError("tool_hint_max_length must be between 20 and 500")
        if defaults.tool_hint_max_length != parsed:
            defaults.tool_hint_max_length = parsed
            changed = True
            restart_required = True

    if changed:
        save_config(config)
    return settings_payload(requires_restart=restart_required)


async def update_provider(data: dict[str, Any]) -> dict[str, Any]:
    """Create or update a provider in the providers array."""
    config = load_config()
    name = (data.get("name") or "").strip()
    if not name:
        raise WebUISettingsError("name is required")

    fmt = data.get("format", "openai_compat")
    if fmt not in ("openai_compat", "anthropic"):
        raise WebUISettingsError(f"unknown format: {fmt}")

    api_key = (data.get("api_key") or "").strip() or None
    api_base = (data.get("api_base") or "").strip() or None

    providers = config.providers.providers

    for p in providers:
        if p.name == name:
            p.format = fmt
            # Chiave vuota = "tieni quella salvata". Rifiutiamo anche il
            # suggerimento offuscato (`sk-a...j8f9`): un client vecchio che
            # lo pre-compila nel campo lo rimanderebbe qui identico e
            # sovrascriverebbe la chiave vera con il segnaposto.
            if api_key and api_key != _mask_api_key(p.api_key):
                p.api_key = api_key
            p.api_base = api_base or p.api_base
            break
    else:
        from jenny.config.schema import ProviderConfig

        providers.append(ProviderConfig(
            name=name,
            format=fmt,
            api_key=api_key,
            api_base=api_base,
        ))

    if not config.providers.default:
        config.providers.default = name

    save_config(config)
    return settings_payload()


async def delete_provider(data: dict[str, Any]) -> dict[str, Any]:
    """Remove a provider from the array."""
    config = load_config()
    name = (data.get("name") or "").strip()

    config.providers.providers = [
        p for p in config.providers.providers if p.name != name
    ]

    if config.providers.default == name:
        config.providers.default = (
            config.providers.providers[0].name
            if config.providers.providers
            else None
        )

    save_config(config)
    return settings_payload()


def update_web_search_settings(query: QueryParams) -> dict[str, Any]:
    config = load_config()
    search_config = config.tools.android_web.search
    fetch_config = config.tools.android_web.fetch
    changed = False

    def set_search_value(attr: str, value: object) -> None:
        nonlocal changed
        if getattr(search_config, attr) != value:
            setattr(search_config, attr, value)
            changed = True

    def set_fetch_value(attr: str, value: object) -> None:
        nonlocal changed
        if getattr(fetch_config, attr) != value:
            setattr(fetch_config, attr, value)
            changed = True

    search_engine = _query_first(query, "search_engine")
    if search_engine is not None:
        search_engine = search_engine.strip().lower()
        if search_engine != _ANDROID_WEB_SEARCH_ENGINE:
            raise WebUISettingsError("unknown search engine")
        set_search_value("search_engine", search_engine)

    max_results = _query_first_alias(query, "max_results", "maxResults")
    if max_results is not None:
        try:
            parsed = int(max_results)
        except ValueError:
            raise WebUISettingsError("max_results must be an integer") from None
        if parsed < 1 or parsed > 10:
            raise WebUISettingsError("max_results must be between 1 and 10")
        set_search_value("max_results", parsed)

    timeout = _query_first(query, "timeout")
    if timeout is not None:
        try:
            parsed_timeout = int(timeout)
        except ValueError:
            raise WebUISettingsError("timeout must be an integer") from None
        if parsed_timeout < 1 or parsed_timeout > 120:
            raise WebUISettingsError("timeout must be between 1 and 120")
        set_search_value("timeout", parsed_timeout)

    fetch_max_chars = _query_first_alias(query, "fetch_max_chars", "fetchMaxChars")
    if fetch_max_chars is not None:
        try:
            parsed_fetch_max_chars = int(fetch_max_chars)
        except ValueError:
            raise WebUISettingsError("fetch_max_chars must be an integer") from None
        if parsed_fetch_max_chars < 1000 or parsed_fetch_max_chars > 200_000:
            raise WebUISettingsError("fetch_max_chars must be between 1000 and 200000")
        set_fetch_value("max_chars", parsed_fetch_max_chars)

    if changed:
        save_config(config)
    return settings_payload()


def update_location_settings(query: QueryParams) -> dict[str, Any]:
    """Aggiorna il toggle posizione (``tools.location.enable``).

    Solo il toggle è esposto in UI: gli altri campi (TTL Telegram, timeout fix
    fresco) restano config-only. Il gate reale resta comunque il permesso
    runtime Android; questo toggle spegne l'iniezione anche a permesso concesso.
    """
    config = load_config()
    loc = config.tools.location
    changed = False

    enabled = _query_first(query, "enabled")
    if enabled is not None:
        value = enabled.strip().lower() in ("1", "true", "yes", "on")
        if loc.enable != value:
            loc.enable = value
            changed = True

    if changed:
        save_config(config)
    return settings_payload()



