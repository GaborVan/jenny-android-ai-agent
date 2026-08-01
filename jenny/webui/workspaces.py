"""Persisted WebUI project workspace state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenny.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScope,
    WorkspaceScopeError,
    default_workspace_scope,
    validate_workspace_scope_payload,
)


class WebUIWorkspaceController:
    """Own WebUI project scope persistence and validation."""

    def __init__(
        self,
        *,
        session_manager: Any | None,
        default_workspace: Path,
        default_restrict_to_workspace: bool,
    ) -> None:
        self._sessions = session_manager
        self._default_workspace = default_workspace
        self._default_restrict_to_workspace = default_restrict_to_workspace

    def default_scope(self) -> WorkspaceScope:
        return default_workspace_scope(
            self._default_workspace,
            self._default_restrict_to_workspace,
        )

    def scope_for_session_key(self, session_key: str) -> WorkspaceScope:
        if self._sessions is None:
            return self.default_scope()
        metadata_reader = getattr(self._sessions, "read_session_metadata", None)
        if callable(metadata_reader):
            data = metadata_reader(session_key)
        else:
            data = self._sessions.read_session_file(session_key)
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        if not isinstance(metadata, dict) or WORKSPACE_SCOPE_METADATA_KEY not in metadata:
            return self.default_scope()
        try:
            return validate_workspace_scope_payload(
                metadata.get(WORKSPACE_SCOPE_METADATA_KEY),
                default_workspace=self._default_workspace,
                default_restrict_to_workspace=self._default_restrict_to_workspace,
            )
        except WorkspaceScopeError:
            return self.default_scope()
