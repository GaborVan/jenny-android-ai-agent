from __future__ import annotations

from pathlib import Path

import pytest

from jenny.security.workspace_policy import (
    _ROOT_RESOLVE_CACHE,
    _ROOT_RESOLVE_CACHE_MAX,
    WorkspaceBoundaryError,
    _is_path_within,
    _path_key,
    _resolved_root,
    invalidate_root_cache,
    resolve_allowed_path,
)


def _make_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")


def test_resolve_allowed_path_accepts_workspace_relative_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('ok')", encoding="utf-8")

    resolved = resolve_allowed_path("src/main.py", workspace=workspace, allowed_root=workspace)

    assert resolved == target.resolve()


def test_resolve_allowed_path_blocks_parent_traversal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(WorkspaceBoundaryError, match="outside allowed directory"):
        resolve_allowed_path("../secret.txt", workspace=workspace, allowed_root=workspace)


def test_resolve_allowed_path_blocks_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = workspace / "linked-secret.txt"
    try:
        link.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    assert not _is_path_within(link, workspace)
    with pytest.raises(WorkspaceBoundaryError):
        resolve_allowed_path("linked-secret.txt", workspace=workspace, allowed_root=workspace)


def test_resolve_allowed_path_allows_extra_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    media = tmp_path / "media"
    media.mkdir()
    image = media / "image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    resolved = resolve_allowed_path(
        image,
        workspace=workspace,
        allowed_root=workspace,
        extra_allowed_roots=[media],
    )

    assert resolved == image.resolve()


def test_resolve_allowed_path_allows_extra_file_only_exactly(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed = outside / "allowed.txt"

    resolved = resolve_allowed_path(
        allowed,
        workspace=workspace,
        allowed_root=workspace,
        extra_allowed_files=[allowed],
    )

    assert resolved == allowed.resolve()
    with pytest.raises(WorkspaceBoundaryError, match="outside allowed directory"):
        resolve_allowed_path(
            allowed / "child.txt",
            workspace=workspace,
            allowed_root=workspace,
            extra_allowed_files=[allowed],
        )


def test_resolve_allowed_path_extra_file_blocks_link_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / "MEMORY.md"
    outside_target.write_text("secret", encoding="utf-8")

    memory_link = workspace / "memory"
    _make_directory_link(memory_link, outside)
    logical_allowed = memory_link / "MEMORY.md"

    with pytest.raises(WorkspaceBoundaryError, match="outside allowed directory"):
        resolve_allowed_path(
            "memory/MEMORY.md",
            workspace=workspace,
            allowed_root=workspace / "skills",
            extra_allowed_files=[logical_allowed],
        )


# ---------------------------------------------------------------------------
# Cache della radice risolta
# ---------------------------------------------------------------------------
#
# `resolve_allowed_path` risolveva la radice a OGNI chiamata, e `Path.resolve()`
# passa da `realpath`, cioè una `lstat` per componente (~43 syscall per una
# radice tipica). Dentro un `python_exec` guardato ogni operazione su file passa
# di qui: la stessa radice veniva ricalcolata centinaia di volte per exec.


def test_resolved_root_is_memoised(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invalidate_root_cache()

    first = _resolved_root(workspace)
    assert _ROOT_RESOLVE_CACHE

    # Avvelenare la voce in cache è l'unico modo per osservare che la seconda
    # chiamata NON ha rifatto `realpath`.
    sentinel = Path("/sentinel")
    _ROOT_RESOLVE_CACHE[next(iter(_ROOT_RESOLVE_CACHE))] = sentinel
    assert _resolved_root(workspace) is sentinel
    assert first == workspace.resolve()


def test_invalidate_root_cache_forces_a_fresh_resolution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invalidate_root_cache()
    _resolved_root(workspace)
    _ROOT_RESOLVE_CACHE[next(iter(_ROOT_RESOLVE_CACHE))] = Path("/sentinel")

    invalidate_root_cache()

    assert _resolved_root(workspace) == workspace.resolve()


def test_the_path_itself_is_never_cached(tmp_path: Path) -> None:
    """In cache va solo la RADICE: il percorso è l'input non fidato."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    invalidate_root_cache()

    resolve_allowed_path("a.txt", workspace=workspace, allowed_root=workspace)

    assert set(_ROOT_RESOLVE_CACHE) == {_path_key(workspace)}


def test_entering_the_guard_invalidates_the_cache(tmp_path: Path) -> None:
    """L'invalidazione dichiarata: una voce stantia vive al massimo un exec."""
    from jenny.agent.tools.python_exec import PythonNamespace

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invalidate_root_cache()
    _ROOT_RESOLVE_CACHE["stale"] = Path("/sentinel")

    namespace = PythonNamespace(
        working_dir=str(workspace), restrict_to_workspace=True, workspace=str(workspace)
    )
    namespace.execute("1 + 1")

    assert "stale" not in _ROOT_RESOLVE_CACHE


def test_cache_is_bounded(tmp_path: Path) -> None:
    invalidate_root_cache()
    for index in range(_ROOT_RESOLVE_CACHE_MAX + 5):
        _resolved_root(tmp_path / f"root{index}")
    assert len(_ROOT_RESOLVE_CACHE) <= _ROOT_RESOLVE_CACHE_MAX
