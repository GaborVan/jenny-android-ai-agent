"""Shared path helpers for workspace-scoped tools."""

from pathlib import Path

from jenny.config.paths import get_media_dir
from jenny.security.workspace_policy import UNRESTRICTED, resolve_allowed_path


def resolve_workspace_path(
    path: str,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    extra_allowed_dirs: list[Path] | None = None,
    extra_allowed_files: list[Path] | None = None,
    include_media_dir: bool = True,
) -> Path:
    """Resolve path against workspace and enforce allowed directory containment."""
    media_roots = [get_media_dir()] if include_media_dir else []
    extra_roots = [*media_roots, *(extra_allowed_dirs or [])] if allowed_dir else None
    # allowed_dir=None a QUESTO livello significa "restrizione disattivata"
    # (deciso dal chiamante tool via ToolWorkspace.allowed_root). Senza nemmeno
    # un'allowlist di file esatti è un opt-out deliberato → UNRESTRICTED, che
    # preserva il comportamento illimitato pre-esistente. Con extra_allowed_files
    # resta None, così resolve_allowed_path applica l'allowlist di file.
    if allowed_dir is None:
        root: object = UNRESTRICTED if not extra_allowed_files else None
    else:
        root = allowed_dir
    return resolve_allowed_path(
        path,
        workspace=workspace,
        allowed_root=root,
        extra_allowed_roots=extra_roots,
        extra_allowed_files=extra_allowed_files,
    )
