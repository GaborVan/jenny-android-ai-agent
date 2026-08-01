"""Esecuzione dei tool per l'``AgentRunner`` (estratto da runner.py).

`ToolExecutionMixin` isola il fan-out dei tool-call di un turno
(`_execute_tools`) e l'esecuzione del singolo tool (`_run_tool`): tracking degli
eventi file-edit, progress, classificazione violazioni (via metodi del runner
risolti per MRO). Nessun import runtime verso ``runner`` → nessun ciclo.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from jenny.agent.progress_events import (
    invoke_file_edit_progress,
    on_progress_accepts_file_edit_events,
)
from jenny.agent.tool_error_policy import TOOL_ERROR_RETRY_HINT
from jenny.agent.tools.result import ToolResult
from jenny.config.runtime_env import tool_timeout_s
from jenny.providers.base import ToolCallRequest
from jenny.utils.file_edit_events import (
    build_file_edit_end_event,
    build_file_edit_error_event,
    build_file_edit_start_event,
    prepare_file_edit_trackers,
)
from jenny.utils.runtime import repeated_external_lookup_error

if TYPE_CHECKING:
    from jenny.agent.runner import AgentRunSpec


class ToolExecutionMixin:
    """Esecuzione tool-call del turno (mixin di AgentRunner)."""

    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
    ) -> tuple[list[Any], list[dict[str, str]], BaseException | None]:
        batches = self._partition_tool_batches(spec, tool_calls)
        tool_results: list[tuple[Any, dict[str, str], BaseException | None]] = []
        for batch in batches:
            if spec.concurrent_tools and len(batch) > 1:
                batch_results = await asyncio.gather(*(
                    self._run_tool(
                        spec, tool_call, external_lookup_counts, workspace_violation_counts,
                    )
                    for tool_call in batch
                ))
                tool_results.extend(batch_results)
            else:
                batch_results = []
                for tool_call in batch:
                    result = await self._run_tool(
                        spec, tool_call, external_lookup_counts, workspace_violation_counts,
                    )
                    tool_results.append(result)
                    batch_results.append(result)

        results: list[Any] = []
        events: list[dict[str, str]] = []
        fatal_error: BaseException | None = None
        for result, event, error in tool_results:
            results.append(result)
            events.append(event)
            if error is not None and fatal_error is None:
                fatal_error = error
        return results, events, fatal_error

    async def _run_tool(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
        external_lookup_counts: dict[str, int],
        workspace_violation_counts: dict[str, int],
    ) -> tuple[Any, dict[str, str], BaseException | None]:
        hint = TOOL_ERROR_RETRY_HINT
        lookup_error = repeated_external_lookup_error(
            tool_call.name,
            tool_call.arguments,
            external_lookup_counts,
        )
        if lookup_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated external lookup blocked",
            }
            if spec.fail_on_tool_error:
                return lookup_error + hint, event, RuntimeError(lookup_error)
            return lookup_error + hint, event, None
        prepare_call = getattr(spec.tools, "prepare_call", None)
        tool, params, prep_error = None, tool_call.arguments, None
        if callable(prepare_call):
            with suppress(Exception):
                prepared = prepare_call(tool_call.name, tool_call.arguments)
                if isinstance(prepared, tuple) and len(prepared) == 3:
                    tool, params, prep_error = prepared
        if prep_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": prep_error.split(": ", 1)[-1][:120],
            }
            handled = self._classify_violation(
                raw_text=prep_error,
                soft_payload=prep_error + hint,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return handled
            return prep_error + hint, event, (
                RuntimeError(prep_error) if spec.fail_on_tool_error else None
            )
        emit_file_edit_events = (
            spec.progress_callback is not None
            and on_progress_accepts_file_edit_events(spec.progress_callback)
        )
        progress_callback = spec.progress_callback if emit_file_edit_events else None
        file_edit_trackers = (
            prepare_file_edit_trackers(
                call_id=tool_call.id,
                tool_name=tool_call.name,
                tool=tool,
                workspace=spec.workspace,
                params=params if isinstance(params, dict) else None,
            )
            if progress_callback is not None
            else None
        )
        if file_edit_trackers and progress_callback is not None:
            await invoke_file_edit_progress(
                progress_callback,
                [build_file_edit_start_event(
                    file_edit_tracker,
                    params if isinstance(params, dict) else None,
                ) for file_edit_tracker in file_edit_trackers],
            )
        try:
            timeout_s = tool_timeout_s()
            if timeout_s is not None and timeout_s <= 0:
                timeout_s = None
            if tool is not None:
                exec_coro = tool.execute(**params)
            else:
                exec_coro = spec.tools.execute(tool_call.name, params)
            if timeout_s is None:
                result = await exec_coro
            else:
                result = await asyncio.wait_for(exec_coro, timeout=timeout_s)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            # Rete di sicurezza: un tool che non ritorna mai bloccherebbe l'intero
            # turno (UI ferma su "Agent running"). Lo trattiamo come soft error e
            # lo restituiamo al modello, così il turno prosegue e si chiude.
            if file_edit_trackers and progress_callback is not None:
                await invoke_file_edit_progress(
                    progress_callback,
                    [
                        build_file_edit_error_event(file_edit_tracker, "timed out")
                        for file_edit_tracker in file_edit_trackers
                    ],
                )
            timeout_msg = (
                f"tool '{tool_call.name}' timed out after {timeout_s:g}s"
                if timeout_s is not None
                else f"tool '{tool_call.name}' timed out"
            )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": timeout_msg,
            }
            payload = f"Error: {timeout_msg}{hint}"
            if spec.fail_on_tool_error:
                return payload, event, TimeoutError(timeout_msg)
            return payload, event, None
        except BaseException as exc:
            if file_edit_trackers and progress_callback is not None:
                await invoke_file_edit_progress(
                    progress_callback,
                    [
                        build_file_edit_error_event(file_edit_tracker, str(exc))
                        for file_edit_tracker in file_edit_trackers
                    ],
                )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": str(exc),
            }
            payload = f"Error: {type(exc).__name__}: {exc}"
            handled = self._classify_violation(
                raw_text=str(exc),
                # Preserve legacy exception payloads without the retry hint.
                soft_payload=payload,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return handled
            if spec.fail_on_tool_error:
                return payload, event, exc
            return payload, event, None

        # Tool migrati a ToolResult: srotola qui. Questo path chiama
        # tool.execute() direttamente e salta registry.execute() (dove l'esito
        # strutturato verrebbe reso): senza srotolamento il dataclass grezzo
        # finirebbe nella history e json.dumps fallirebbe.
        if isinstance(result, ToolResult):
            rendered = result.render()
            if not result.ok:
                msg = rendered if isinstance(rendered, str) else (
                    result.error.message if result.error else "tool failed"
                )
                result = msg if msg.startswith("Error") else f"Error: {msg}"
            else:
                result = rendered

        if isinstance(result, str) and result.startswith("Error"):
            if file_edit_trackers and progress_callback is not None:
                await invoke_file_edit_progress(
                    progress_callback,
                    [
                        build_file_edit_error_event(file_edit_tracker, result)
                        for file_edit_tracker in file_edit_trackers
                    ],
                )
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": result.replace("\n", " ").strip()[:120],
            }
            handled = self._classify_violation(
                raw_text=result,
                soft_payload=result + hint,
                event=event,
                tool_call=tool_call,
                workspace_violation_counts=workspace_violation_counts,
            )
            if handled is not None:
                return handled
            if spec.fail_on_tool_error:
                return result + hint, event, RuntimeError(result)
            return result + hint, event, None

        if file_edit_trackers and progress_callback is not None:
            await invoke_file_edit_progress(
                progress_callback,
                [build_file_edit_end_event(
                    file_edit_tracker,
                    params if isinstance(params, dict) else None,
                ) for file_edit_tracker in file_edit_trackers],
            )

        detail = "" if result is None else str(result)
        detail = detail.replace("\n", " ").strip()
        if not detail:
            detail = "(empty)"
        elif len(detail) > 120:
            detail = detail[:120] + "..."
        return result, {"name": tool_call.name, "status": "ok", "detail": detail}, None
