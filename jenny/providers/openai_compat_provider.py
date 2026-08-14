"""OpenAI-compatible provider for all non-Anthropic LLM APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from typing import Any

import httpx
from loguru import logger

from jenny.providers.base import (
    LLMProvider,
    LLMResponse,
    StreamTimeout,
    resolve_first_output_timeout_s,
    resolve_stream_idle_timeout_s,
    tool_arguments_json_for_replay,
)
from jenny.providers.openai_compat_helpers import (
    _ALLOWABLE_MSG_KEYS,
    _DEFAULT_OPENROUTER_HEADERS,
    _KIMI_ALWAYS_THINKING_MODELS,
    _KIMI_THINKING_MODELS,
    _RESPONSES_FAILURE_THRESHOLD,
    _RESPONSES_PROBE_INTERVAL_S,
    _deep_merge,
    _is_direct_openai_base,
    _is_local_endpoint,
    _merge_responses_extra_body,
    _model_slug,
    _model_thinking_style,
    _openai_compat_timeout_s,
    _requires_max_completion_tokens,
    _responses_circuit_key,
    _short_tool_id,
    _supports_prompt_caching,
    _thinking_extra_body,
    _thinking_styles_for,
    _uses_openrouter_attribution,
    is_openai_reasoning_model,
)
from jenny.providers.openai_compat_parsing import ResponseParsingMixin
from jenny.providers.openai_responses import (
    consume_sse_with_reasoning,
    convert_messages,
    convert_tools,
    parse_response_output,
)
from jenny.providers.tool_ids import unique_tool_ids_in_history


class OpenAICompatProvider(ResponseParsingMixin, LLMProvider):
    """Unified provider for all OpenAI-compatible APIs."""

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base: str | None,
        default_model: str = "",
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        api_type: str = "auto",
        extra_query: dict[str, str] | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._extra_body = extra_body or {}
        self._api_type = api_type
        self._extra_query = extra_query or {}

        effective_base = api_base or None
        self._effective_base = effective_base
        self._default_headers = {"x-session-affinity": uuid.uuid4().hex}
        if _uses_openrouter_attribution(effective_base):
            self._default_headers.update(_DEFAULT_OPENROUTER_HEADERS)
        if extra_headers:
            self._default_headers.update(extra_headers)
        self._api_key_for_client = api_key or "no-key"
        self._is_local = _is_local_endpoint(effective_base)

        self._http_client: httpx.AsyncClient | None = None

        # Responses API circuit breaker: skip after repeated failures,
        # probe again after _RESPONSES_PROBE_INTERVAL_S seconds.
        self._responses_failures: dict[str, int] = {}
        self._responses_tripped_at: dict[str, float] = {}

    def _build_http_client(self) -> None:
        """Create a plain httpx client for the SDK-free path."""
        timeout_s = _openai_compat_timeout_s(local=self._is_local)
        if self._is_local:
            _local_limits = httpx.Limits(keepalive_expiry=0)
            self._http_client = httpx.AsyncClient(
                limits=_local_limits,
                timeout=timeout_s,
                transport=httpx.AsyncHTTPTransport(proxy=None, limits=_local_limits),
            )
        else:
            self._http_client = httpx.AsyncClient(timeout=timeout_s)

    async def _ensure_client(self) -> None:
        """Return the shared client, creating it on first call."""
        if self._http_client is None:
            self._build_http_client()

    def _api_url(self, path: str) -> str:
        """Return a fully-qualified API URL for the httpx fallback."""
        base = (self._effective_base or "https://api.openai.com/v1").rstrip("/")
        return f"{base}{path}"

    def _auth_headers(self) -> dict[str, str]:
        """Return request headers for the httpx fallback."""
        headers = dict(self._default_headers)
        headers.setdefault("Authorization", f"Bearer {self._api_key_for_client}")
        headers.setdefault("Content-Type", "application/json")
        return headers

    def _merge_extra_body(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Flatten SDK-style extra_body into a plain HTTP request body."""
        body = {k: v for k, v in kwargs.items() if k != "extra_body"}
        extra_body = kwargs.get("extra_body")
        if isinstance(extra_body, dict):
            body = _deep_merge(body, extra_body)
        return body

    async def _http_request(
        self,
        path: str,
        body: dict[str, Any],
        *,
        stream: bool = False,
    ) -> httpx.Response:
        """Send a POST request on the httpx client."""
        await self._ensure_client()
        if self._http_client is None:
            raise RuntimeError("HTTP client not initialized")
        url = self._api_url(path)
        headers = self._auth_headers()
        timeout = _openai_compat_timeout_s(local=self._is_local)
        request = self._http_client.build_request(
            "POST", url, headers=headers, json=body, timeout=timeout,
            params=self._extra_query or None,
        )
        response = await self._http_client.send(request, stream=stream)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            try:
                if stream and not e.response.is_closed:
                    await e.response.aread()
                text = e.response.text if e.response else ""
            except Exception:
                text = ""
            raise RuntimeError(
                f"HTTP {e.response.status_code}: {text[:500]}"
            ) from e
        return response

    @classmethod
    def _apply_cache_control(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Inject cache_control markers for prompt caching."""
        cache_marker = {"type": "ephemeral"}
        new_messages = list(messages)

        def _mark(msg: dict[str, Any]) -> dict[str, Any]:
            content = msg.get("content")
            if isinstance(content, str):
                return {**msg, "content": [
                    {"type": "text", "text": content, "cache_control": cache_marker},
                ]}
            if isinstance(content, list) and content:
                nc = list(content)
                nc[-1] = {**nc[-1], "cache_control": cache_marker}
                return {**msg, "content": nc}
            return msg

        if new_messages and new_messages[0].get("role") == "system":
            new_messages[0] = _mark(new_messages[0])
        if len(new_messages) >= 3:
            new_messages[-2] = _mark(new_messages[-2])

        new_tools = tools
        if tools:
            new_tools = list(tools)
            for idx in cls._tool_cache_marker_indices(new_tools):
                new_tools[idx] = {**new_tools[idx], "cache_control": cache_marker}
        return new_messages, new_tools

    @staticmethod
    def _normalize_tool_call_id(tool_call_id: Any) -> Any:
        """Normalize to a provider-safe 9-char alphanumeric form."""
        if not isinstance(tool_call_id, str):
            return tool_call_id
        if len(tool_call_id) == 9 and tool_call_id.isalnum():
            return tool_call_id
        return hashlib.sha1(tool_call_id.encode()).hexdigest()[:9]

    def _sanitize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip non-standard keys, disambiguate colliding tool_call IDs."""
        sanitized = LLMProvider._sanitize_request_messages(messages, _ALLOWABLE_MSG_KEYS)
        # Unicità/riaccoppiamento degli id: regola condivisa con l'Anthropic
        # provider, vedi ``providers/tool_ids.py``. Il resto del loop qui sotto è
        # wire-specifico e resta dove sta.
        sanitized = unique_tool_ids_in_history(
            sanitized,
            fresh_id=_short_tool_id,
            derive_id=lambda seed, idx, salt: self._normalize_tool_call_id(
                f"{seed}:{idx}:{salt}"
            ),
        )

        for clean in sanitized:
            if isinstance(clean.get("tool_calls"), list):
                normalized = []
                for tc in clean["tool_calls"]:
                    if not isinstance(tc, dict):
                        normalized.append(tc)
                        continue
                    tc_clean = dict(tc)
                    function = tc_clean.get("function")
                    if isinstance(function, dict):
                        function_clean = dict(function)
                        if "arguments" in function_clean:
                            function_clean["arguments"] = tool_arguments_json_for_replay(
                                function_clean.get("arguments")
                            )
                        else:
                            function_clean["arguments"] = "{}"
                        tc_clean["function"] = function_clean
                    normalized.append(tc_clean)
                clean["tool_calls"] = normalized
                if clean.get("role") == "assistant":
                    # Some OpenAI-compatible gateways reject assistant messages
                    # that mix non-empty content with tool_calls.
                    clean["content"] = None
        return self._enforce_role_alternation(sanitized)

    # ------------------------------------------------------------------
    # Build kwargs
    # ------------------------------------------------------------------

    def _request_model_name(self, model_name: str) -> str:
        return model_name

    @staticmethod
    def _supports_temperature(
        model_name: str,
        reasoning_effort: str | None = None,
    ) -> bool:
        """Return True when the model accepts a temperature parameter.

        GPT-5 family and reasoning models (o1/o3/o4) reject temperature
        when reasoning_effort is set to anything other than ``"none"``.
        """
        if reasoning_effort and reasoning_effort.lower() != "none":
            return False
        return not is_openai_reasoning_model(model_name)

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        model_name = model or self.default_model
        model_name = self._request_model_name(model_name)

        logger.debug(
            "[openai_compat] _build_kwargs: input_model={!r} "
            "default_model={!r} final_model_name={!r}",
            model,
            self.default_model,
            model_name,
        )

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
        }

        # GPT-5 and reasoning models (o1/o3/o4) reject temperature when
        # reasoning_effort is active.  Only include it when safe.
        if self._supports_temperature(model_name, reasoning_effort):
            kwargs["temperature"] = temperature

        if _requires_max_completion_tokens(model_name):
            kwargs["max_completion_tokens"] = max(1, max_tokens)
        else:
            kwargs["max_tokens"] = max(1, max_tokens)

        # Normalize reasoning_effort into a semantic form (OpenAI vocab)
        # used for internal decisions, and a wire form actually sent out.
        # "minimum" is accepted as a DashScope-native alias for "minimal".
        semantic_effort: str | None = None
        if isinstance(reasoning_effort, str):
            semantic_effort = reasoning_effort.lower()
            if semantic_effort == "minimum":
                semantic_effort = "minimal"

        wire_effort = reasoning_effort
        if wire_effort and semantic_effort != "none":
            kwargs["reasoning_effort"] = wire_effort

        # Only send thinking controls when reasoning_effort is explicit so
        # omitting the config preserves each provider's default.
        if reasoning_effort is not None:
            slug = _model_slug(model_name)
            thinking_enabled = semantic_effort not in ("none", "minimal")
            for thinking_style in _thinking_styles_for(model_name):
                if not thinking_enabled and slug in _KIMI_ALWAYS_THINKING_MODELS:
                    continue
                extra = _thinking_extra_body(thinking_style, thinking_enabled)
                if extra:
                    kwargs.setdefault("extra_body", {}).update(extra)

            # Moonshot rejects requests that carry both 'reasoning_effort'
            # and the native 'thinking' param.  We already expressed the
            # user's intent via the provider-native shape, so drop the
            # redundant wire-level kwarg.  Only kimi models need this —
            # Xiaomi's API accepts both params.
            if slug in _KIMI_THINKING_MODELS:
                kwargs.pop("reasoning_effort", None)

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        # Backfill reasoning_content="" on assistants missing it when
        # thinking is enabled (#3554, #3584); "" reads as "no thinking that turn".
        explicit_thinking = (
            reasoning_effort is not None
            and semantic_effort not in ("none", "minimal")
            and bool(_model_thinking_style(model_name))
        )
        if explicit_thinking:
            for msg in kwargs["messages"]:
                if msg.get("role") == "assistant" and "reasoning_content" not in msg:
                    msg["reasoning_content"] = ""

        # Merge user-configured extra_body last so it can override or
        # extend provider-specific defaults (e.g. chat_template_kwargs,
        # guided_json, repetition_penalty).  Uses recursive merge so
        # nested dicts like {"chat_template_kwargs": {"enable_thinking": false}}
        # do not clobber sibling keys already set by thinking-style logic.
        if self._extra_body:
            existing = kwargs.get("extra_body", {})
            kwargs["extra_body"] = _deep_merge(existing, self._extra_body)

        # Prompt caching esplicito: emette i marker cache_control solo dove il
        # gateway li accetta (OpenRouter + modelli Anthropic). Su endpoint
        # OpenAI vanilla il campo verrebbe rifiutato, quindi restano assenti.
        if _supports_prompt_caching(self._effective_base or self.api_base, model_name):
            cached_messages, cached_tools = self._apply_cache_control(
                kwargs["messages"], kwargs.get("tools"),
            )
            kwargs["messages"] = cached_messages
            if cached_tools is not None:
                kwargs["tools"] = cached_tools

        return kwargs

    def _should_use_responses_api(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> bool:
        """Use Responses API only for direct OpenAI requests that benefit from it."""
        if self._api_type == "chat_completions":
            return False
        if self._api_type == "responses":
            # Explicit configuration means Responses is mandatory; do not
            # consult the circuit breaker or fall back to Chat Completions.
            return True
        if not _is_direct_openai_base(self._effective_base):
                return False

        model_name = (model or self.default_model).lower()
        wants = False
        if reasoning_effort and reasoning_effort.lower() != "none":
            wants = True
        elif is_openai_reasoning_model(model_name):
            wants = True
        if not wants:
            return False

        return self._responses_circuit_allows_probe(model, reasoning_effort)

    def _responses_circuit_allows_probe(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> bool:
        """Return False when the Responses API circuit breaker is open."""
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        failures = self._responses_failures.get(key, 0)
        if failures >= _RESPONSES_FAILURE_THRESHOLD:
            tripped = self._responses_tripped_at.get(key, 0.0)
            if (time.monotonic() - tripped) < _RESPONSES_PROBE_INTERVAL_S:
                return False
            # Half-open: allow one probe attempt
        return True

    def _record_responses_failure(self, model: str | None, reasoning_effort: str | None) -> None:
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        count = self._responses_failures.get(key, 0) + 1
        self._responses_failures[key] = count
        if count >= _RESPONSES_FAILURE_THRESHOLD:
            self._responses_tripped_at[key] = time.monotonic()
            logger.warning(
                "Responses API circuit open for {} — falling back to Chat Completions",
                key,
            )

    def _record_responses_success(self, model: str | None, reasoning_effort: str | None) -> None:
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        self._responses_failures.pop(key, None)
        self._responses_tripped_at.pop(key, None)

    @staticmethod
    def _should_fallback_from_responses_error(e: Exception) -> bool:
        """Fallback only for likely Responses API compatibility errors."""
        response = getattr(e, "response", None)
        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        if status_code not in {400, 404, 422}:
            return False

        body = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        body_text = str(body).lower() if body is not None else ""
        compatibility_markers = (
            "responses",
            "response api",
            "max_output_tokens",
            "instructions",
            "previous_response",
            "unsupported",
            "not supported",
            "unknown parameter",
            "unrecognized request argument",
        )
        return any(marker in body_text for marker in compatibility_markers)

    def _build_responses_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a Responses API body for direct OpenAI requests."""
        model_name = model or self.default_model
        model_name = self._request_model_name(model_name)
        sanitized_messages = self._sanitize_messages(self._sanitize_empty_content(messages))
        instructions, input_items = convert_messages(sanitized_messages)

        body: dict[str, Any] = {
            "model": model_name,
            "instructions": instructions or None,
            "input": input_items,
            "max_output_tokens": max(1, max_tokens),
            "store": False,
            "stream": False,
        }

        if self._supports_temperature(model_name, reasoning_effort):
            body["temperature"] = temperature

        if reasoning_effort and reasoning_effort.lower() != "none":
            body["reasoning"] = {"effort": reasoning_effort}
            body["include"] = ["reasoning.encrypted_content"]

        if tools:
            body["tools"] = convert_tools(tools)
            body["tool_choice"] = tool_choice or "auto"

        extra_body = getattr(self, "_extra_body", {})
        if extra_body:
            body = _merge_responses_extra_body(body, extra_body)

        return body

    @classmethod
    def _extract_error_metadata(cls, e: Exception) -> dict[str, Any]:
        response = getattr(e, "response", None)
        headers = getattr(response, "headers", None)
        payload = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        if payload is None and response is not None:
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    payload = response_json()
                except Exception:
                    payload = None
        error_type, error_code = LLMProvider._extract_error_type_code(payload)

        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        should_retry: bool | None = None
        if headers is not None:
            raw = headers.get("x-should-retry")
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered == "true":
                    should_retry = True
                elif lowered == "false":
                    should_retry = False

        error_kind: str | None = None
        error_name = e.__class__.__name__.lower()
        if "timeout" in error_name:
            error_kind = "timeout"
        elif "connection" in error_name:
            error_kind = "connection"

        return {
            "error_status_code": int(status_code) if status_code is not None else None,
            "error_kind": error_kind,
            "error_type": error_type,
            "error_code": error_code,
            "error_retry_after_s": cls._extract_retry_after_from_headers(headers),
            "error_should_retry": should_retry,
        }

    @staticmethod
    def _handle_error(
        e: Exception,
        *,
        partial_content: str | None = None,
    ) -> LLMResponse:
        try:
            body = (
                getattr(e, "doc", None)
                or getattr(e, "body", None)
                or getattr(getattr(e, "response", None), "text", None)
            )
        except Exception:
            body = None
        body_text = body if isinstance(body, str) else str(body) if body is not None else ""
        msg = f"Error: {body_text.strip()[:500]}" if body_text.strip() else f"Error calling LLM: {e}"

        response = getattr(e, "response", None)
        retry_after = LLMProvider._extract_retry_after_from_headers(getattr(response, "headers", None))
        if retry_after is None:
            retry_after = LLMProvider._extract_retry_after(msg)
        return LLMResponse(
            content=msg,
            finish_reason="error",
            retry_after=retry_after,
            partial_content=partial_content or None,
            **OpenAICompatProvider._extract_error_metadata(e),
        )

    # ------------------------------------------------------------------
    # HTTP chat implementations
    # ------------------------------------------------------------------

    async def _http_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> LLMResponse:
        """Non-streaming chat via httpx."""
        kwargs = self._build_kwargs(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
        )
        body = self._merge_extra_body(kwargs)
        response = await self._http_request("/chat/completions", body)
        return self._parse(response.json())

    @staticmethod
    async def _iter_chat_completion_sse(
        response: httpx.Response,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield parsed Chat Completions SSE events as dicts."""
        buffer: list[str] = []

        def _flush() -> dict[str, Any] | None:
            data_lines = [line[5:].strip() for line in buffer if line.startswith("data:")]
            buffer.clear()
            if not data_lines:
                return None
            data = "\n".join(data_lines).strip()
            if not data or data == "[DONE]":
                return None
            try:
                return json.loads(data)
            except Exception:
                logger.warning(
                    "Failed to parse chat completion SSE JSON: {}", data[:200]
                )
                return None

        async for line in response.aiter_lines():
            if line == "":
                event = _flush()
                if event is not None:
                    yield event
                continue
            buffer.append(line)

        if buffer:
            event = _flush()
            if event is not None:
                yield event

    async def _http_chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Streaming chat via httpx."""
        kwargs = self._build_kwargs(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
        )
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        body = self._merge_extra_body(kwargs)

        idle_timeout_s = resolve_stream_idle_timeout_s()
        first_output_timeout_s = max(
            resolve_first_output_timeout_s(local=self._is_local), idle_timeout_s
        )
        response = await self._http_request("/chat/completions", body, stream=True)
        chunks: list[Any] = []
        # Finché il modello non ha emesso nulla vale il budget lungo: il
        # silenzio è prompt processing, non uno stallo. Dopo il primo output
        # torna in vigore l'idle inter-chunk, più stretto.
        saw_output = False
        sse_iter = self._iter_chat_completion_sse(response).__aiter__()
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        sse_iter.__anext__(),
                        timeout=idle_timeout_s if saw_output else first_output_timeout_s,
                    )
                except StopAsyncIteration:
                    break
                chunks.append(chunk)
                if not isinstance(chunk, dict):
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                raw_delta_content = delta.get("content")
                if not saw_output and (
                    raw_delta_content
                    or delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("tool_calls")
                    or delta.get("function_call")
                ):
                    saw_output = True
                if on_content_delta:
                    text = self._extract_text_content(raw_delta_content)
                    if text:
                        await on_content_delta(text)
                if on_thinking_delta:
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    r_text = self._extract_text_content(reasoning)
                    if not r_text:
                        r_text = self._extract_thinking_content(raw_delta_content)
                    if r_text:
                        await on_thinking_delta(r_text)
                if on_tool_call_delta:
                    for idx, tool_delta in enumerate(delta.get("tool_calls") or []):
                        fn = tool_delta.get("function") or {}
                        await on_tool_call_delta({
                            "index": tool_delta.get("index", idx),
                            "call_id": str(tool_delta.get("id") or ""),
                            "name": str(fn.get("name") or ""),
                            "arguments_delta": str(fn.get("arguments") or ""),
                        })
                    function_call = delta.get("function_call")
                    if function_call:
                        await on_tool_call_delta({
                            "index": 0,
                            "call_id": "",
                            "name": str(function_call.get("name") or ""),
                            "arguments_delta": str(function_call.get("arguments") or ""),
                        })
        except asyncio.TimeoutError as exc:
            # Quale dei due budget è scaduto lo sa solo questo frame: il
            # chiamante formatta il messaggio, quindi glielo passiamo.
            raise StreamTimeout(
                idle_timeout_s if saw_output else first_output_timeout_s,
                saw_output=saw_output,
            ) from exc
        except Exception as exc:
            # Preserve whatever text was already streamed to the user before
            # this exception aborted the stream, by attaching it to the raised
            # exception: `chunks` is local to this frame and would otherwise
            # be lost by the time chat_stream()'s outer except handles this
            # (#audit mid-stream-exception loss). Timeout is excluded: that
            # case is handled by the separate stall/retry mechanism in
            # base.py::chat_stream_with_retry and must not be touched here.
            if chunks:
                with suppress(Exception):
                    partial = self._parse_chunks(chunks).content
                    if partial:
                        exc.partial_content = partial
            raise
        return self._parse_chunks(chunks)

    async def _http_responses_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> LLMResponse:
        """Non-streaming Responses API call via httpx."""
        body = self._build_responses_body(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
        )
        response = await self._http_request("/responses", body)
        return parse_response_output(response.json())

    async def _http_responses_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Streaming Responses API call via httpx."""
        body = self._build_responses_body(
            messages, tools, model, max_tokens, temperature,
            reasoning_effort, tool_choice,
        )
        body["stream"] = True
        response = await self._http_request("/responses", body, stream=True)
        content, tool_calls, finish_reason, usage, reasoning_content = await consume_sse_with_reasoning(
            response,
            on_content_delta=on_content_delta,
            on_tool_call_delta=on_tool_call_delta,
        )
        return LLMResponse(
            content=content or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content=reasoning_content,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        await self._ensure_client()
        logger.info(
            "[openai_compat] chat: model={!r} default_model={!r} effective={!r}",
            model,
            self.default_model,
            model or self.default_model,
        )
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    result = await self._http_responses_chat(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    self._record_responses_success(model, reasoning_effort)
                    return result
                except Exception as responses_error:
                    if self._api_type == "responses":
                        raise
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise
                    self._record_responses_failure(model, reasoning_effort)

            return await self._http_chat(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
            )
        except Exception as e:
            return self._handle_error(e)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        await self._ensure_client()
        idle_timeout_s = resolve_stream_idle_timeout_s()
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    result = await self._http_responses_stream(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                        on_content_delta=on_content_delta,
                        on_tool_call_delta=on_tool_call_delta,
                    )
                    self._record_responses_success(model, reasoning_effort)
                    return result
                except Exception as responses_error:
                    if self._api_type == "responses":
                        raise
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise
                    self._record_responses_failure(model, reasoning_effort)

            return await self._http_chat_stream(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
                on_content_delta=on_content_delta,
                on_thinking_delta=on_thinking_delta,
                on_tool_call_delta=on_tool_call_delta,
            )
        except asyncio.TimeoutError as e:
            waited_s = e.waited_s if isinstance(e, StreamTimeout) else idle_timeout_s
            saw_output = e.saw_output if isinstance(e, StreamTimeout) else True
            return LLMResponse(
                content=(
                    f"Error calling LLM: stream stalled for more than "
                    f"{waited_s:g} seconds"
                    if saw_output
                    else f"Error calling LLM: no output from the model within "
                    f"{waited_s:g} seconds"
                ),
                finish_reason="error",
                error_kind="timeout",
            )
        except Exception as e:
            return self._handle_error(
                e, partial_content=getattr(e, "partial_content", None),
            )

    def get_default_model(self) -> str:
        return self.default_model
