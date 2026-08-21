"""Workspace access scope and sandbox capability helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from jenny.security.workspace_policy import _safe_expanduser
from jenny.session.keys import PROJECT_SESSION_PREFIX, is_project_session_key

WorkspaceAccessMode = Literal["restricted", "full"]
WORKSPACE_SCOPE_METADATA_KEY = "workspace_scope"
_ACCESS_MODES = {"restricted", "full"}

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled", ""}
_PROVIDER_LABELS = {
    "none": "None",
    "unknown": "Unknown system sandbox",
    "android_app_sandbox": "Android App Sandbox",
    "macos_app_sandbox": "macOS App Sandbox",
}

_CURRENT_WORKSPACE_SCOPE: ContextVar["WorkspaceScope | None"] = ContextVar(
    "jenny_workspace_scope",
    default=None,
)


class WorkspaceScopeError(ValueError):
    """Raised when a requested WebUI workspace scope is invalid."""

    status = 400

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class WorkspaceSandboxStatus:
    """Resolved workspace sandbox state for runtime display and tooling."""

    restrict_to_workspace: bool
    workspace_root: str
    level: str
    enforced: bool
    provider: str
    provider_label: str
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "restrict_to_workspace": self.restrict_to_workspace,
            "workspace_root": self.workspace_root,
            "level": self.level,
            "enforced": self.enforced,
            "provider": self.provider,
            "provider_label": self.provider_label,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class WorkspaceScope:
    """Effective project root and access mode for one agent turn."""

    project_path: Path
    access_mode: WorkspaceAccessMode
    restrict_to_workspace: bool
    sandbox_status: WorkspaceSandboxStatus

    @property
    def project_name(self) -> str:
        return self.project_path.name or str(self.project_path)

    def metadata(self) -> dict[str, str]:
        return {
            "project_path": str(self.project_path),
            "access_mode": self.access_mode,
        }

    def payload(self) -> dict[str, Any]:
        return {
            **self.metadata(),
            "project_name": self.project_name,
            "restrict_to_workspace": self.restrict_to_workspace,
            "sandbox_status": self.sandbox_status.as_dict(),
        }


@dataclass(frozen=True)
class ToolWorkspace:
    """Workspace policy resolved for a tool call."""

    project_path: Path | None
    restrict_to_workspace: bool
    scope: WorkspaceScope | None = None

    @property
    def allowed_root(self) -> Path | None:
        if self.restrict_to_workspace and self.project_path is not None:
            return self.project_path
        return None


@dataclass(frozen=True)
class WorkspaceScopeResolver:
    """Resolve the effective workspace scope at an agent turn boundary."""

    default_workspace: str | Path
    default_restrict_to_workspace: bool
    scoped_channel: str = "websocket"
    # Sottocartella che ospita i progetti, relativa al workspace. Un progetto
    # *e'* una wiki (v. ``roadmap/progetti-passi.md``): non esiste una
    # ``projects/`` separata. Configurabile perche' lo e' ``config.wiki.wikis_dir``,
    # e chi costruisce il resolver la passa da li'.
    projects_subdir: str = "wikis"

    @property
    def sandbox_status(self) -> WorkspaceSandboxStatus:
        return self.default().sandbox_status

    def default(self) -> WorkspaceScope:
        return default_workspace_scope(
            self.default_workspace,
            self.default_restrict_to_workspace,
        )

    def for_message(
        self,
        msg: Any,
        session_metadata: Any,
    ) -> WorkspaceScope:
        return self.for_turn(
            channel=getattr(msg, "channel", None),
            message_metadata=getattr(msg, "metadata", None),
            session_metadata=session_metadata,
            session_key=getattr(msg, "session_key", None),
        )

    def for_turn(
        self,
        *,
        channel: str | None,
        message_metadata: Any,
        session_metadata: Any,
        session_key: str | None = None,
    ) -> WorkspaceScope:
        """Lo scope di questo turno.

        **Per una sessione-progetto la cartella si ricava dalla chiave**, e non
        dai metadati: ``project:patreon`` -> ``<workspace>/wikis/patreon``. Cosi'
        la sessione e la sua cartella non possono divergere — non c'e' un secondo
        dato da tenere allineato, e nessun client puo' chiedere una cartella
        diversa da quella che il suo nome dichiara. I metadati restano la strada
        per tutto il resto (uno scope scelto a mano, i test).
        """
        if channel != self.scoped_channel:
            return self.default()
        if session_key and is_project_session_key(session_key):
            return self.for_project(session_key)
        return resolve_effective_workspace_scope(
            message_metadata=message_metadata,
            session_metadata=session_metadata,
            default_workspace=self.default_workspace,
            default_restrict_to_workspace=self.default_restrict_to_workspace,
        )

    def for_project(self, session_key: str) -> WorkspaceScope:
        """Lo scope di una sessione-progetto, dedotto dalla sua chiave.

        Sempre ``restricted``: la scrittura di un progetto sta nella sua
        cartella, e non c'e' un modo di chiedere il contrario. (La lettura resta
        aperta sul workspace — il confine e' asimmetrico, v.
        ``FileSystemTools._read_allowed_root``.)

        **Una cartella che non esiste non fa ricadere sulla radice personale.**
        Lo scope viene costruito lo stesso e punta al posto che manca: le
        scritture falliscono tutte, il che e' scomodo ma onesto, mentre il
        fallback silenzioso metterebbe il lavoro di un progetto nel workspace
        personale. Trasformarlo in un rifiuto detto a voce e' il passo 6.
        """
        name = session_key[len(PROJECT_SESSION_PREFIX):]
        root = _safe_expanduser(self.default_workspace).resolve(strict=False)
        project = (root / self.projects_subdir / name).resolve(strict=False)
        # Il nome arriva da una session key, che a sua volta arriva da un client:
        # un ``..`` non deve poter far uscire lo scope dalla cartella dei progetti.
        projects_root = (root / self.projects_subdir).resolve(strict=False)
        if project == projects_root or projects_root not in project.parents:
            logger.warning(
                "project session {} resolves outside {}; falling back to the default scope",
                session_key,
                projects_root,
            )
            return self.default()
        if not project.is_dir():
            logger.warning(
                "project session {} points at a missing folder ({}); writes will fail",
                session_key,
                project,
            )
        return build_workspace_scope(project, "restricted")


def workspace_sandbox_status(
    *,
    restrict_to_workspace: bool,
    workspace: str | Path,
    environ: dict[str, str] | None = None,
) -> WorkspaceSandboxStatus:
    """Return how workspace restriction is enforced in the current host."""

    workspace_root = str(_safe_expanduser(workspace).resolve(strict=False))
    provider = _env_system_provider(environ)
    if not restrict_to_workspace:
        return WorkspaceSandboxStatus(
            restrict_to_workspace=False,
            workspace_root=workspace_root,
            level="off",
            enforced=False,
            provider="none",
            provider_label=_provider_label("none"),
            summary="Workspace restriction is disabled.",
        )

    if provider:
        label = _provider_label(provider)
        return WorkspaceSandboxStatus(
            restrict_to_workspace=True,
            workspace_root=workspace_root,
            level="system",
            enforced=True,
            provider=provider,
            provider_label=label,
            summary=f"Workspace restriction is system-enforced by {label}.",
        )

    return WorkspaceSandboxStatus(
        restrict_to_workspace=True,
        workspace_root=workspace_root,
        level="application",
        enforced=False,
        provider="none",
        provider_label=_provider_label("none"),
        summary="Workspace restriction uses jenny application-level guards.",
    )


def default_access_mode(restrict_to_workspace: bool) -> WorkspaceAccessMode:
    return "restricted" if restrict_to_workspace else "full"


def build_workspace_scope(
    project_path: str | Path,
    access_mode: str,
) -> WorkspaceScope:
    mode = _normalize_access_mode(access_mode)
    root = _safe_expanduser(project_path).resolve(strict=False)
    restrict = mode == "restricted"
    return WorkspaceScope(
        project_path=root,
        access_mode=mode,
        restrict_to_workspace=restrict,
        sandbox_status=workspace_sandbox_status(
            restrict_to_workspace=restrict,
            workspace=root,
        ),
    )


def default_workspace_scope(
    workspace: str | Path,
    restrict_to_workspace: bool,
) -> WorkspaceScope:
    return build_workspace_scope(
        workspace,
        default_access_mode(restrict_to_workspace),
    )


def validate_workspace_scope_payload(
    raw: Any,
    *,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
) -> WorkspaceScope:
    """Validate a client-requested workspace scope."""
    if raw is None:
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
        )
    if not isinstance(raw, dict):
        raise WorkspaceScopeError("workspace_scope must be an object")

    raw_path = raw.get("project_path") or raw.get("path")
    if raw_path is None or raw_path == "":
        raw_path = str(_safe_expanduser(default_workspace).resolve(strict=False))
    if not isinstance(raw_path, str):
        raise WorkspaceScopeError("project_path must be a string")
    if "\0" in raw_path:
        raise WorkspaceScopeError("project_path contains invalid characters")

    project = _safe_expanduser(raw_path)
    if not project.is_absolute():
        raise WorkspaceScopeError("project_path must be absolute")
    project = project.resolve(strict=False)
    if not project.is_dir():
        raise WorkspaceScopeError("project_path must be an existing directory")

    raw_mode = raw.get("access_mode")
    if raw_mode is None:
        raw_mode = default_access_mode(default_restrict_to_workspace)
    if not isinstance(raw_mode, str):
        raise WorkspaceScopeError("access_mode must be a string")
    return build_workspace_scope(project, raw_mode)


def workspace_scope_from_metadata(
    metadata: Any,
    *,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
) -> WorkspaceScope:
    """Resolve persisted metadata, falling back safely for old or stale sessions."""
    if not isinstance(metadata, dict):
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
        )
    try:
        return validate_workspace_scope_payload(
            metadata.get(WORKSPACE_SCOPE_METADATA_KEY),
            default_workspace=default_workspace,
            default_restrict_to_workspace=default_restrict_to_workspace,
        )
    except WorkspaceScopeError:
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
        )


def resolve_effective_workspace_scope(
    *,
    message_metadata: Any,
    session_metadata: Any,
    default_workspace: str | Path,
    default_restrict_to_workspace: bool,
) -> WorkspaceScope:
    if isinstance(message_metadata, dict) and WORKSPACE_SCOPE_METADATA_KEY in message_metadata:
        return workspace_scope_from_metadata(
            message_metadata,
            default_workspace=default_workspace,
            default_restrict_to_workspace=default_restrict_to_workspace,
        )
    return workspace_scope_from_metadata(
        session_metadata,
        default_workspace=default_workspace,
        default_restrict_to_workspace=default_restrict_to_workspace,
    )


def bind_workspace_scope(scope: WorkspaceScope) -> Token[WorkspaceScope | None]:
    return _CURRENT_WORKSPACE_SCOPE.set(scope)


def reset_workspace_scope(token: Token[WorkspaceScope | None]) -> None:
    _CURRENT_WORKSPACE_SCOPE.reset(token)


@contextmanager
def enter_workspace_scope(scope: "WorkspaceScope | None") -> "Iterator[None]":
    """Bind *scope* per la durata del blocco (no-op se ``None``).

    Sostituisce il dance manuale ``bind_workspace_scope``/``reset_workspace_scope``
    con un unico costrutto strutturale, così il reset non può essere dimenticato.
    Con il gate fail-closed (`resolve_allowed_path`), uno scope non impostato fa
    comunque fallire in sicurezza le operazioni su path (deny), non aprirle."""
    if scope is None:
        yield
        return
    token = bind_workspace_scope(scope)
    try:
        yield
    finally:
        reset_workspace_scope(token)


def current_workspace_scope() -> WorkspaceScope | None:
    return _CURRENT_WORKSPACE_SCOPE.get()


def current_tool_workspace(
    default_workspace: str | Path | None,
    *,
    restrict_to_workspace: bool = False,
) -> ToolWorkspace:
    """Return the workspace/access policy for the current tool call."""

    scope = current_workspace_scope()
    project_path = (
        scope.project_path
        if scope is not None
        else _safe_expanduser(default_workspace) if default_workspace is not None else None
    )
    restrict = (
        scope.restrict_to_workspace
        if scope is not None
        else bool(restrict_to_workspace)
    )
    return ToolWorkspace(
        project_path=project_path,
        restrict_to_workspace=restrict,
        scope=scope,
    )


def _env_system_provider(environ: dict[str, str] | None = None) -> str | None:
    env = environ if environ is not None else os.environ
    explicit_provider = env.get("JENNY_WORKSPACE_SANDBOX_PROVIDER")
    enforced = env.get("JENNY_WORKSPACE_SANDBOX_ENFORCED")
    compatibility = env.get("JENNY_SANDBOX_ENFORCED")

    marker = enforced if enforced is not None else compatibility
    if marker is None:
        return None

    normalized_marker = marker.strip().lower()
    if normalized_marker in _FALSE_VALUES:
        return None
    if normalized_marker in _TRUE_VALUES:
        return _normalize_provider(explicit_provider)
    return _normalize_provider(marker)


def _normalize_provider(value: str | None) -> str:
    if not value:
        return "unknown"
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized or "unknown"


def _provider_label(provider: str) -> str:
    if provider in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[provider]
    return provider.replace("_", " ").title()


def _normalize_access_mode(value: str) -> WorkspaceAccessMode:
    mode = value.strip().lower().replace("_", "-")
    if mode == "restrict":
        mode = "restricted"
    if mode == "full-access":
        mode = "full"
    if mode not in _ACCESS_MODES:
        raise WorkspaceScopeError("access_mode must be restricted or full")
    return mode  # type: ignore[return-value]
