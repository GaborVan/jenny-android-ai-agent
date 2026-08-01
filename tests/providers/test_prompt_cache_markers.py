from __future__ import annotations

from typing import Any

from jenny.providers.anthropic_provider import AnthropicProvider
from jenny.providers.openai_compat_provider import OpenAICompatProvider


def _openai_tools(*names: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def _anthropic_tools(*names: str) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": f"{name} tool",
            "input_schema": {"type": "object", "properties": {}},
        }
        for name in names
    ]


def _marked_openai_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    if not tools:
        return []
    marked: list[str] = []
    for tool in tools:
        if "cache_control" in tool:
            marked.append((tool.get("function") or {}).get("name", ""))
    return marked


def _marked_anthropic_tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    if not tools:
        return []
    return [tool.get("name", "") for tool in tools if "cache_control" in tool]


def test_openai_compat_marks_builtin_boundary_and_tail_tool() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "assistant"},
        {"role": "user", "content": "user"},
    ]
    _, marked_tools = OpenAICompatProvider._apply_cache_control(
        messages,
        _openai_tools("read_file", "write_file", "mcp_fs_ls", "mcp_git_status"),
    )
    assert _marked_openai_tool_names(marked_tools) == ["write_file", "mcp_git_status"]


def test_anthropic_marks_builtin_boundary_and_tail_tool() -> None:
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    _, _, marked_tools = AnthropicProvider._apply_cache_control(
        "system",
        messages,
        _anthropic_tools("read_file", "write_file", "mcp_fs_ls", "mcp_git_status"),
    )
    assert _marked_anthropic_tool_names(marked_tools) == ["write_file", "mcp_git_status"]


def test_openai_compat_marks_only_tail_without_mcp() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "assistant"},
        {"role": "user", "content": "user"},
    ]
    _, marked_tools = OpenAICompatProvider._apply_cache_control(
        messages,
        _openai_tools("read_file", "write_file"),
    )
    assert _marked_openai_tool_names(marked_tools) == ["write_file"]


def _messages_have_cache_control(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    return True
    return False


def _build_kwargs_for(api_base: str, model: str) -> dict[str, Any]:
    provider = OpenAICompatProvider(api_key="k", api_base=api_base, default_model=model)
    return provider._build_kwargs(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "assistant"},
            {"role": "user", "content": "user"},
        ],
        tools=_openai_tools("read_file", "write_file"),
        model=model,
        max_tokens=256,
        temperature=0.5,
        reasoning_effort=None,
        tool_choice=None,
    )


def test_build_kwargs_emits_cache_markers_for_openrouter_anthropic() -> None:
    kwargs = _build_kwargs_for(
        "https://openrouter.ai/api/v1", "anthropic/claude-3.5-sonnet"
    )
    assert _messages_have_cache_control(kwargs["messages"])
    assert _marked_openai_tool_names(kwargs["tools"]) == ["write_file"]


def test_build_kwargs_omits_cache_markers_for_vanilla_openai() -> None:
    kwargs = _build_kwargs_for("https://api.openai.com/v1", "gpt-4o")
    assert not _messages_have_cache_control(kwargs["messages"])
    assert _marked_openai_tool_names(kwargs["tools"]) == []


def test_build_kwargs_omits_cache_markers_for_openrouter_non_anthropic() -> None:
    kwargs = _build_kwargs_for("https://openrouter.ai/api/v1", "openai/gpt-4o")
    assert not _messages_have_cache_control(kwargs["messages"])
    assert _marked_openai_tool_names(kwargs["tools"]) == []
