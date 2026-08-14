"""Parsing delle risposte per l'``OpenAICompatProvider`` (estratto).

`ResponseParsingMixin` raccoglie i metodi che ripiegano le risposte (chat e
Responses API, complete e a chunk SSE) in ``LLMResponse``: estrazione testo/
thinking/usage e il fold dei tool-call. Mixato nel provider: ``self``/``cls``
risolvono via MRO ai metodi/attributi del provider. Nessun ciclo (importa solo
``base`` e gli helper leaf).
"""

from __future__ import annotations

from typing import Any

from jenny.providers.base import (
    LLMResponse,
    ToolCallRequest,
    parse_tool_arguments,
)
from jenny.providers.openai_compat_helpers import (
    _extract_tc_extras,
    _get,
    _short_tool_id,
)
from jenny.providers.tool_ids import dedupe_tool_ids


class ResponseParsingMixin:
    """Metodi di parsing risposta → LLMResponse (mixin del provider)."""

    @staticmethod
    def _maybe_mapping(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        return None

    @classmethod
    def _extract_text_content(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                item_map = cls._maybe_mapping(item)
                if item_map:
                    # Skip Mistral-style {"type":"thinking","thinking":[...]}
                    # blocks: their text belongs in reasoning_content.
                    if item_map.get("type") == "thinking":
                        continue
                    text = item_map.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
                    continue
                if isinstance(item, str):
                    parts.append(item)
            return "".join(parts) or None
        return str(value)

    @classmethod
    def _extract_thinking_content(cls, value: Any) -> str | None:
        """Extract reasoning text from Mistral-style thinking blocks.

        Mistral returns content as a list mixing
        ``{"type":"thinking","thinking":[{"type":"text","text":...}]}`` and
        ``{"type":"text","text":...}``. The thinking text belongs in
        ``reasoning_content`` so the agent can surface it as a reasoning
        trace rather than as the assistant's reply.
        """
        if not isinstance(value, list):
            return None
        parts: list[str] = []
        for item in value:
            item_map = cls._maybe_mapping(item)
            if not item_map:
                continue
            if item_map.get("type") != "thinking":
                continue
            inner = item_map.get("thinking")
            text = cls._extract_text_content(inner)
            if text:
                parts.append(text)
        return "".join(parts) or None

    @classmethod
    def _extract_usage(cls, response: Any) -> dict[str, int]:
        """Extract token usage from an OpenAI-compatible response (dict JSON).

        I campi ``cached_tokens`` provider-specifici sono normalizzati sotto
        un'unica chiave; vedi la catena di priorità qui sotto.
        """
        response_map = cls._maybe_mapping(response)
        usage_map = cls._maybe_mapping(response_map.get("usage")) if response_map else None
        if usage_map is None:
            return {}
        result = {
            "prompt_tokens": int(usage_map.get("prompt_tokens") or 0),
            "completion_tokens": int(usage_map.get("completion_tokens") or 0),
            "total_tokens": int(usage_map.get("total_tokens") or 0),
        }

        # --- cached_tokens (normalised across providers) ---
        # Priority order ensures the most specific field wins.
        for path in (
            ("prompt_tokens_details", "cached_tokens"),  # OpenAI/Zhipu/MiniMax/Qwen/Mistral/xAI
            ("cached_tokens",),                          # StepFun/Moonshot (top-level)
            ("prompt_cache_hit_tokens",),                # DeepSeek/SiliconFlow
        ):
            cached = cls._get_nested_int(usage_map, path)
            if cached:
                result["cached_tokens"] = cached
                break

        return result

    @staticmethod
    def _get_nested_int(obj: Any, path: tuple[str, ...]) -> int:
        """Scende in *obj* (dict) lungo *path* e restituisce un ``int``."""
        current = obj
        for segment in path:
            if not isinstance(current, dict):
                return 0
            current = current.get(segment)
        return int(current or 0) if current is not None else 0

    def _parse(self, response: Any) -> LLMResponse:
        response_map = self._maybe_mapping(response) or {}
        choices = response_map.get("choices") or []
        if not choices:
            content = self._extract_text_content(
                response_map.get("content") or response_map.get("output_text")
            )
            reasoning_content = self._extract_text_content(
                response_map.get("reasoning_content")
            )
            if content is not None:
                return LLMResponse(
                    content=content,
                    reasoning_content=reasoning_content,
                    finish_reason=str(response_map.get("finish_reason") or "stop"),
                    usage=self._extract_usage(response_map),
                )
            return LLMResponse(content="Error: API returned empty choices.", finish_reason="error")

        choice0 = self._maybe_mapping(choices[0]) or {}
        msg0 = self._maybe_mapping(choice0.get("message")) or {}
        content = self._extract_text_content(msg0.get("content"))
        finish_reason = str(choice0.get("finish_reason") or "stop")

        raw_tool_calls: list[Any] = []
        reasoning_content = msg0.get("reasoning_content")
        if reasoning_content is None and msg0.get("reasoning"):
            reasoning_content = self._extract_text_content(msg0.get("reasoning"))
        # Lift thinking blocks from content into reasoning_content when
        # the API returns them inline (e.g. Mistral).
        if reasoning_content is None:
            reasoning_content = self._extract_thinking_content(msg0.get("content"))
        for ch in choices:
            ch_map = self._maybe_mapping(ch) or {}
            m = self._maybe_mapping(ch_map.get("message")) or {}
            tool_calls = m.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                raw_tool_calls.extend(tool_calls)
                if ch_map.get("finish_reason") in ("tool_calls", "stop"):
                    finish_reason = str(ch_map["finish_reason"])
            if not content:
                content = self._extract_text_content(m.get("content"))
            if reasoning_content is None:
                reasoning_content = m.get("reasoning_content")

        parsed_tool_calls = []
        for tc in raw_tool_calls:
            tc_map = self._maybe_mapping(tc) or {}
            fn = self._maybe_mapping(tc_map.get("function")) or {}
            args = parse_tool_arguments(fn.get("arguments", {}))
            ec, prov, fn_prov = _extract_tc_extras(tc)
            parsed_tool_calls.append(ToolCallRequest(
                id=str(tc_map.get("id") or _short_tool_id()),
                name=str(fn.get("name") or ""),
                arguments=args,
                extra_content=ec,
                provider_specific_fields=prov,
                function_provider_specific_fields=fn_prov,
            ))

        return LLMResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            finish_reason=finish_reason,
            usage=self._extract_usage(response_map),
            reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
        )

    @classmethod
    def _parse_chunks(cls, chunks: list[Any]) -> LLMResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_bufs: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}

        def _accum_tc(tc: Any, idx_hint: int) -> None:
            """Accumulate one streaming tool-call delta into *tc_bufs*."""
            tc_index: int = _get(tc, "index") if _get(tc, "index") is not None else idx_hint
            buf = tc_bufs.setdefault(tc_index, {
                "id": "", "name": "", "arguments": "",
                "extra_content": None, "prov": None, "fn_prov": None,
            })
            tc_id = _get(tc, "id")
            if tc_id:
                buf["id"] = str(tc_id)
            fn = _get(tc, "function")
            if fn is not None:
                fn_name = _get(fn, "name")
                if fn_name:
                    buf["name"] = str(fn_name)
                fn_args = _get(fn, "arguments")
                if fn_args:
                    buf["arguments"] += str(fn_args)
            ec, prov, fn_prov = _extract_tc_extras(tc)
            if ec:
                buf["extra_content"] = ec
            if prov:
                buf["prov"] = prov
            if fn_prov:
                buf["fn_prov"] = fn_prov

        def _accum_legacy_function_call(function_call: Any) -> None:
            """Accumulate legacy ``delta.function_call`` streaming chunks."""
            if not function_call:
                return
            buf = tc_bufs.setdefault(0, {
                "id": "", "name": "", "arguments": "",
                "extra_content": None, "prov": None, "fn_prov": None,
            })
            fn_name = _get(function_call, "name")
            if fn_name:
                buf["name"] = str(fn_name)
            fn_args = _get(function_call, "arguments")
            if fn_args:
                buf["arguments"] += str(fn_args)

        for chunk in chunks:
            chunk_map = cls._maybe_mapping(chunk) or {}
            choices = chunk_map.get("choices") or []
            if not choices:
                usage = cls._extract_usage(chunk_map) or usage
                text = cls._extract_text_content(
                    chunk_map.get("content") or chunk_map.get("output_text")
                )
                if text:
                    content_parts.append(text)
                continue
            choice = cls._maybe_mapping(choices[0]) or {}
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
            delta = cls._maybe_mapping(choice.get("delta")) or {}
            raw_delta_content = delta.get("content")
            text = cls._extract_text_content(raw_delta_content)
            if text:
                content_parts.append(text)
            text = cls._extract_text_content(delta.get("reasoning_content"))
            if not text:
                text = cls._extract_text_content(delta.get("reasoning"))
            if not text:
                # Mistral streams thinking inside the content array as
                # {"type":"thinking", thinking:[{"type":"text", ...}]}.
                text = cls._extract_thinking_content(raw_delta_content)
            if text:
                reasoning_parts.append(text)
            for idx, tc in enumerate(delta.get("tool_calls") or []):
                _accum_tc(tc, idx)
            _accum_legacy_function_call(delta.get("function_call"))
            usage = cls._extract_usage(chunk_map) or usage

        # Some providers (e.g. Zhipu/GLM) reuse the same tool_call id for
        # parallel tool calls in streaming mode. Deduplicate before building
        # the response so downstream tool messages don't collide.
        bufs = list(tc_bufs.values())
        for buf, unique_id in zip(
            bufs,
            dedupe_tool_ids(
                [b["id"] for b in bufs],
                replacement=lambda _raw, _idx: _short_tool_id(),
            ),
        ):
            buf["id"] = unique_id

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=[
                ToolCallRequest(
                    id=b["id"] or _short_tool_id(),
                    name=b["name"],
                    arguments=parse_tool_arguments(b["arguments"]),
                    extra_content=b.get("extra_content"),
                    provider_specific_fields=b.get("prov"),
                    function_provider_specific_fields=b.get("fn_prov"),
                )
                for b in tc_bufs.values()
            ],
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
        )
