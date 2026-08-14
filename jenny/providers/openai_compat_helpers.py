"""Helper puri per l'``OpenAICompatProvider`` (estratti da openai_compat_provider).

Costanti di capability dei modelli (thinking style, timeout, chiavi tool-call) e
funzioni pure di supporto (slug modello, merge di extra_body, rilevamento
endpoint locale/OpenAI/OpenRouter, circuit-key). Modulo leaf: nessun import verso
il provider → nessun ciclo. Il provider re-importa i nomi che usa.
"""

from __future__ import annotations

import json
import secrets
import string
from typing import Any

from jenny.providers.body_merge import deep_merge
from jenny.providers.endpoint_budget import (
    DEFAULT_REQUEST_TIMEOUT_S,
    LOCAL_REQUEST_TIMEOUT_S,
    is_local_endpoint,
    request_timeout_s,
)

_ALLOWABLE_MSG_KEYS = frozenset({
    "role", "content", "tool_calls", "tool_call_id", "name",
    "reasoning_content", "extra_content",
})
_ALNUM = string.ascii_letters + string.digits

_STANDARD_TC_KEYS = frozenset({"id", "type", "index", "function"})
_STANDARD_FN_KEYS = frozenset({"name", "arguments"})
_DEFAULT_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/flagdizero/jenny-android-ai-agent",
    "X-OpenRouter-Title": "Jenny",
    "X-OpenRouter-Categories": "android-agent,personal-agent",
}
_KIMI_THINKING_MODELS: frozenset[str] = frozenset({
    "kimi-k2.5",
    "kimi-k2.6",
    "kimi-k2.7",
    "kimi-k2.7-code",
    "kimi-k2.7-code-highspeed",
    "k2.6-code-preview",
})
_KIMI_ALWAYS_THINKING_MODELS: frozenset[str] = frozenset({
    "kimi-k2.7-code",
    "kimi-k2.7-code-highspeed",
})
# Thinking-capable MiMo models per Xiaomi docs (see
# tests/providers/test_xiaomi_mimo_thinking.py). mimo-v2-flash is omitted
# because it does not support thinking.
_MIMO_THINKING_MODELS: frozenset[str] = frozenset({
    "mimo-v2.5-pro",
    "mimo-v2.5",
    "mimo-v2-pro",
    "mimo-v2-omni",
})
# Budget HTTP e riconoscimento loopback vivono in ``providers/endpoint_budget.py``,
# condivisi con l'Anthropic provider. Gli alias preservano i call-site e i test
# che importano questi nomi da qui.
_OPENAI_COMPAT_REQUEST_TIMEOUT_S = DEFAULT_REQUEST_TIMEOUT_S
_LOCAL_REQUEST_TIMEOUT_S = LOCAL_REQUEST_TIMEOUT_S

# Maps thinking_style → extra_body builder.
# Each builder takes a bool (thinking_enabled) and returns the dict to
# merge into extra_body, keeping the style→wire-format mapping in one place.
_THINKING_STYLE_MAP: dict[str, Any] = {
    "thinking_type": lambda on: {"thinking": {"type": "enabled" if on else "disabled"}},
    "enable_thinking": lambda on: {"enable_thinking": on},
    "reasoning_split": lambda on: {"reasoning_split": on},
}
_MODEL_THINKING_STYLES: dict[str, str] = {
    **dict.fromkeys(_KIMI_THINKING_MODELS, "thinking_type"),
    **dict.fromkeys(_MIMO_THINKING_MODELS, "thinking_type"),
}


def _model_slug(model_name: str) -> str:
    return model_name.lower().rsplit("/", 1)[-1]


_OPENAI_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4")


def is_openai_reasoning_model(model_name: str) -> bool:
    """Riconosce le famiglie di modelli reasoning OpenAI (GPT-5, o1/o3/o4).

    Match preciso sullo slug: evita i falsi positivi del vecchio substring
    ``token in name`` (es. uno slug di terze parti che contiene ``o1`` nel
    nome non deve essere classificato come reasoning)."""
    slug = _model_slug(model_name)
    return "gpt-5" in slug or any(
        slug == p or slug.startswith((p + "-", p + "."))
        for p in _OPENAI_REASONING_MODEL_PREFIXES
    )


def _requires_max_completion_tokens(model_name: str) -> bool:
    """Return True for models that reject ``max_tokens`` (GPT-5 family, o-series)."""
    return is_openai_reasoning_model(model_name)


def _model_thinking_style(model_name: str) -> str:
    return _MODEL_THINKING_STYLES.get(_model_slug(model_name), "")


def _thinking_styles_for(model_name: str) -> list[str]:
    styles: list[str] = []
    model_style = _model_thinking_style(model_name)
    if model_style:
        styles.append(model_style)
    return styles


def _thinking_extra_body(style: str, thinking_enabled: bool) -> dict[str, Any] | None:
    builder = _THINKING_STYLE_MAP.get(style)
    return builder(thinking_enabled) if builder else None


def _openai_compat_timeout_s(*, local: bool = False) -> float:
    """Return the bounded request timeout used for OpenAI-compatible providers.

    Delegatore verso ``providers/endpoint_budget.py``, dove la regola è
    condivisa con l'Anthropic provider.
    """
    return request_timeout_s(local=local)


def _short_tool_id() -> str:
    """9-char alphanumeric ID compatible with all providers (incl. Mistral)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


def _get(obj: Any, key: str) -> Any:
    """Get a value from dict or object attribute, returning None if absent."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _coerce_dict(value: Any) -> dict[str, Any] | None:
    """Return *value* se è un dict non vuoto, altrimenti None."""
    if isinstance(value, dict):
        return value if value else None
    return None


def _extract_tc_extras(tc: Any) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Extract (extra_content, provider_specific_fields, fn_provider_specific_fields).

    Cattura ``extra_content`` (Gemini) verbatim e le chiavi non standard sul
    tool-call / function del payload dict.
    """
    extra_content = _coerce_dict(_get(tc, "extra_content"))

    tc_dict = _coerce_dict(tc)
    prov = None
    fn_prov = None
    if tc_dict is not None:
        leftover = {k: v for k, v in tc_dict.items()
                    if k not in _STANDARD_TC_KEYS and k != "extra_content" and v is not None}
        if leftover:
            prov = leftover
        fn = _coerce_dict(tc_dict.get("function"))
        if fn is not None:
            fn_leftover = {k: v for k, v in fn.items()
                          if k not in _STANDARD_FN_KEYS and v is not None}
            if fn_leftover:
                fn_prov = fn_leftover

    return extra_content, prov, fn_prov


def _uses_openrouter_attribution(api_base: str | None) -> bool:
    """Apply Jenny attribution headers to OpenRouter requests by default."""
    return bool(api_base and "openrouter" in api_base.lower())


def _supports_prompt_caching(api_base: str | None, model_name: str) -> bool:
    """Il prompt-caching esplicito via ``cache_control`` è sicuro solo su
    OpenRouter con modelli Anthropic/Claude.

    Gli endpoint OpenAI vanilla (e la maggior parte dei gateway compatibili)
    rifiuterebbero il campo ``cache_control`` nel body, quindi i marker vanno
    emessi soltanto quando il modello lo prevede."""
    if not _uses_openrouter_attribution(api_base):
        return False
    name = (model_name or "").lower()
    return "claude" in name or "anthropic/" in name


_RESPONSES_FAILURE_THRESHOLD = 3
_RESPONSES_PROBE_INTERVAL_S = 300  # 5 minutes


# Casa in ``providers/endpoint_budget.py``, condiviso con l'Anthropic provider.
_is_local_endpoint = is_local_endpoint


def _is_direct_openai_base(api_base: str | None) -> bool:
    """Return True for direct OpenAI endpoints, not generic OpenAI-compatible gateways."""
    if not api_base:
        return True
    normalized = api_base.strip().lower().rstrip("/")
    return "api.openai.com" in normalized and "openrouter" not in normalized


def _responses_circuit_key(
    model: str | None,
    default_model: str,
    reasoning_effort: str | None,
) -> str:
    model_name = (model or default_model).lower()
    effort = reasoning_effort.lower() if isinstance(reasoning_effort, str) else ""
    return f"{model_name}:{effort}"


# Casa in ``providers/body_merge.py``, condivisa con l'Anthropic provider.
# L'alias preserva i call-site e i test che importano ``_deep_merge`` da qui.
_deep_merge = deep_merge


def _merge_unique_list(base: Any, override: Any) -> Any:
    """Append list values while preserving order and removing duplicates."""
    if not isinstance(base, list) or not isinstance(override, list):
        return override
    result: list[Any] = []
    seen: set[str] = set()
    for value in [*base, *override]:
        try:
            key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        except Exception:
            key = repr(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _merge_responses_extra_body(
    body: dict[str, Any],
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    """Merge configured Responses API body fields without clobbering tools."""
    reserved = {"include", "tools"}
    regular_extra = {key: value for key, value in extra_body.items() if key not in reserved}
    merged = _deep_merge(body, regular_extra)

    if "include" in extra_body:
        merged["include"] = _merge_unique_list(body.get("include"), extra_body["include"])

    if "tools" in extra_body:
        current_tools = body.get("tools")
        configured_tools = extra_body["tools"]
        if isinstance(current_tools, list) and isinstance(configured_tools, list):
            merged["tools"] = [*current_tools, *configured_tools]
        else:
            merged["tools"] = configured_tools

    return merged

