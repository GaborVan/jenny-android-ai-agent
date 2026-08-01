"""Composition helpers for the embedded WebUI gateway."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger as default_logger

from jenny.webui.media_gateway import WebUIMediaGateway
from jenny.webui.transcript import WebUITranscriptRecorder
from jenny.webui.workspaces import WebUIWorkspaceController
from jenny.webui.ws_http import GatewayHTTPHandler


@dataclass(frozen=True)
class GatewayServices:
    """Explicit dependencies shared by WebSocket transport and HTTP routes."""

    http: GatewayHTTPHandler
    media: WebUIMediaGateway
    transcripts: WebUITranscriptRecorder
    workspaces: WebUIWorkspaceController
    session_manager: Any | None


def build_gateway_services(
    *,
    config: Any,
    bus: Any,
    session_manager: Any | None,
    workspace_path: Path,
    default_restrict_to_workspace: bool,
    runtime_model_name: Any | None,
    disabled_skills: set[str] | None = None,
    snapshot_service: Any | None = None,
    logger: Any = default_logger,
    onboarding_event: Any | None = None,
    on_settings_changed: Callable[[], None] | None = None,
    on_telegram_changed: Callable[[], None] | None = None,
) -> GatewayServices:
    media = WebUIMediaGateway(
        workspace_path=workspace_path,
        logger=logger,
    )
    transcripts = WebUITranscriptRecorder(log=logger)
    workspaces = WebUIWorkspaceController(
        session_manager=session_manager,
        default_workspace=workspace_path,
        default_restrict_to_workspace=default_restrict_to_workspace,
    )
    http = GatewayHTTPHandler(
        config=config,
        session_manager=session_manager,
        runtime_model_name=runtime_model_name,
        bus=bus,
        media=media,
        workspaces=workspaces,
        skills_workspace_path=workspace_path,
        disabled_skills=disabled_skills,
        snapshot_service=snapshot_service,
        log=logger,
        onboarding_event=onboarding_event,
        on_settings_changed=on_settings_changed,
        on_telegram_changed=on_telegram_changed,
    )
    return GatewayServices(
        http=http,
        media=media,
        transcripts=transcripts,
        workspaces=workspaces,
        session_manager=session_manager,
    )
