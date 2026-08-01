"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from jenny.utils.helpers import ensure_dir


def set_workspace_dir(path: str | Path) -> None:
    """Set the workspace root explicitly.

    The workspace is the writable directory that holds config, templates,
    skills, memory, and runtime subdirectories. On Android it is provided
    from Java/Kotlin (e.g. ``context.getFilesDir()/workspace``). Lo stato vive
    nel ``RuntimeContext`` (unica fonte di verità).
    """
    from jenny.runtime.context import get_runtime_context

    get_runtime_context().workspace_dir = Path(path) if path else None


def get_config_path() -> Path:
    """Get the configuration file path (lazy import to break circular dependency).

    Delegates to ``jenny.config.loader.get_config_path`` at call time so
    that importing this module never triggers a circular import during startup.
    """
    from jenny.config.loader import get_config_path as _loader_get_config_path
    return _loader_get_config_path()


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory (inside workspace)."""
    workspace = get_workspace_path()
    data_dir = workspace / ".jenny"
    # Migrate from earlier data-dir names in order (.minijenny is the more recent legacy).
    for legacy_name in (".minijenny", ".nanobot"):
        legacy_dir = workspace / legacy_name
        if not data_dir.exists() and legacy_dir.exists():
            legacy_dir.rename(data_dir)
            logger.info("Migrated runtime data directory %s -> .jenny", legacy_name)
    return ensure_dir(data_dir)


def get_workspace_path() -> Path:
    """Return the agent workspace path.

    The workspace must be set explicitly via ``set_workspace_dir`` before use.

    Raises:
        RuntimeError: if no workspace directory has been configured.
    """
    from jenny.runtime.context import get_runtime_context

    workspace_dir = get_runtime_context().workspace_dir
    if workspace_dir is not None:
        return ensure_dir(workspace_dir)
    raise RuntimeError(
        "Workspace directory is not configured. "
        "Call set_workspace_dir() before get_workspace_path()."
    )


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_uploads_dir() -> Path:
    """Cartella degli allegati caricati dall'utente dalla chat.

    Vive dentro il workspace (``workspace/uploads``) così da essere leggibile
    dai tool filesystem dell'agente (path sotto la radice del workspace) e
    visibile nel browser file della WebUI, a differenza della media dir
    runtime nascosta (``.jenny/media``). Gli allegati non-immagine vengono
    referenziati per path e letti on-demand dall'agente.
    """
    return ensure_dir(get_workspace_path() / "uploads")


def get_webui_dir() -> Path:
    """Return the directory for WebUI-only persisted display threads (JSON)."""
    return get_runtime_subdir("webui")


