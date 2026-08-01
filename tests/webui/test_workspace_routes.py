"""Test delle route ``/api/workspace/*`` (file-manager del workspace).

``jenny/webui/workspace_routes.py`` non aveva ancora test dedicati: qui si
copre auth 401, il gate ``workspace.enabled`` (503), il rispetto dei flag
``allow_write``/``allow_delete``, i path felici di ogni operazione e il
rifiuto del path traversal (delegato a ``workspace_files.validate_path``).
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.channels.http_utils import check_api_secret
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui.workspace_routes import WorkspaceRoutes

_SECRET = "s3cr3t-workspace"


def _request(path: str, token: str | None = _SECRET, data: dict | None = None) -> WsRequest:
    if token is not None and "token=" not in path:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}token={urllib.parse.quote(token)}"
    headers = Headers()
    if data is not None:
        headers["X-Jenny-Workspace-Data"] = json.dumps(data)
    return WsRequest(path=path, headers=headers)


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


def _set_workspace_config(config_path: Path, **overrides) -> None:
    config = load_config(config_path)
    for key, value in overrides.items():
        setattr(config.workspace, key, value)
    save_config(config, config_path)


@pytest.fixture()
def routes(workspace_root: Path) -> WorkspaceRoutes:
    return WorkspaceRoutes(
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        get_workspace_root=lambda: workspace_root,
    )


def _json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_returns_none_for_unrelated_path(routes: WorkspaceRoutes) -> None:
    assert await routes.dispatch(_request("/api/other"), "/api/other") is None


# ---------------------------------------------------------------------------
# /api/workspace/list
# ---------------------------------------------------------------------------


async def test_list_requires_auth(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/list", token=None), "/api/workspace/list"
    )
    assert response.status_code == 401


async def test_list_returns_503_when_workspace_disabled(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    _set_workspace_config(config_path, enabled=False)
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 503


async def test_list_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "note.txt").write_text("hello", encoding="utf-8")
    (workspace_root / "sub").mkdir()
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    names = {item["name"] for item in _json(response)["items"]}
    assert names == {"note.txt", "sub"}


async def test_list_rejects_path_traversal(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/list?path=../../etc"), "/api/workspace/list"
    )
    assert response.status_code == 400


async def test_list_missing_subdir_returns_404(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/list?path=missing-dir"), "/api/workspace/list"
    )
    assert response.status_code == 404


async def test_list_marks_dotfiles_internal_without_manifest(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "note.txt").write_text("hello", encoding="utf-8")
    (workspace_root / ".hidden").write_text("y", encoding="utf-8")
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name == {"note.txt": False, ".hidden": True}


async def test_list_marks_default_runtime_dirs_internal_without_manifest(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    # config.json/agent/cron/sessions/ui sono stato del runtime (segreti,
    # bundle rigenerati, storage dei motori interni), non contenuto
    # dell'utente: nascosti di default come i dotfile, senza bisogno di un
    # manifest esplicito.
    (workspace_root / "config.json").write_text("{}", encoding="utf-8")
    (workspace_root / "agent").mkdir()
    (workspace_root / "agent" / "identity.md").write_text("x", encoding="utf-8")
    (workspace_root / "cron").mkdir()
    (workspace_root / "sessions").mkdir()
    (workspace_root / "ui").mkdir()
    (workspace_root / "AGENTS.md").write_text("x", encoding="utf-8")

    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name == {
        "config.json": True,
        "agent": True,
        "cron": True,
        "sessions": True,
        "ui": True,
        "AGENTS.md": False,
    }


async def test_list_uses_internal_manifest_patterns(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / ".jenny").mkdir()
    (workspace_root / ".jenny" / "internal.json").write_text(
        json.dumps({"patterns": ["secret.txt"]}), encoding="utf-8"
    )
    (workspace_root / "secret.txt").write_text("shh", encoding="utf-8")
    (workspace_root / "normal.txt").write_text("hi", encoding="utf-8")
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name["secret.txt"] is True
    assert by_name["normal.txt"] is False


async def test_list_works_when_workspace_root_is_a_symlink(
    tmp_path: Path, config_path: Path
) -> None:
    # Su Android /data/data/... e /data/user/0/... sono alias simlink dello
    # stesso path: get_workspace_root() ritorna la forma non risolta, mentre
    # validate_path() risolve i symlink prima di iterare la directory. Un
    # mismatch testuale tra le due forme rompe item.relative_to(workspace_root)
    # (visto dal vivo, non solo teoricamente: bug trovato via verifica on-device).
    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "note.txt").write_text("hello", encoding="utf-8")
    (real_root / ".hidden").write_text("y", encoding="utf-8")
    symlinked_root = tmp_path / "link"
    symlinked_root.symlink_to(real_root, target_is_directory=True)

    routes = WorkspaceRoutes(
        check_api_token=lambda request: check_api_secret(request.headers, request.path, _SECRET),
        get_workspace_root=lambda: symlinked_root,
    )

    list_response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert list_response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(list_response)["items"]}
    assert by_name == {"note.txt": False, ".hidden": True}


async def test_list_falls_back_to_default_on_malformed_manifest(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / ".jenny").mkdir()
    (workspace_root / ".jenny" / "internal.json").write_text("{not json", encoding="utf-8")
    (workspace_root / "normal.txt").write_text("hi", encoding="utf-8")
    response = await routes.dispatch(_request("/api/workspace/list"), "/api/workspace/list")
    assert response.status_code == 200
    by_name = {item["name"]: item["internal"] for item in _json(response)["items"]}
    assert by_name["normal.txt"] is False


# ---------------------------------------------------------------------------
# /api/workspace/read
# ---------------------------------------------------------------------------


async def test_read_requires_auth(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/read?path=a.txt", token=None), "/api/workspace/read"
    )
    assert response.status_code == 401


async def test_read_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "note.txt").write_text("contenuto", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=note.txt"), "/api/workspace/read"
    )
    assert response.status_code == 200
    assert _json(response)["content"] == "contenuto"


async def test_read_missing_file_returns_404(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/read?path=missing.txt"), "/api/workspace/read"
    )
    assert response.status_code == 404


async def test_read_rejects_oversized_file(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    _set_workspace_config(config_path, max_file_size=4)
    (workspace_root / "big.txt").write_text("molto più lungo di 4 byte", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=big.txt"), "/api/workspace/read"
    )
    assert response.status_code == 400


async def test_read_binary_file_returns_415(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    """Un byte nullo nei primi 4 KB marca il file come binario → 415."""
    (workspace_root / "blob.bin").write_bytes(b"\x89PNG\x00\x1a\ndati")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=blob.bin"), "/api/workspace/read"
    )
    assert response.status_code == 415


async def test_read_text_without_extension(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    """La leggibilità dipende dal contenuto, mai dall'estensione."""
    (workspace_root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=Dockerfile"), "/api/workspace/read"
    )
    assert response.status_code == 200
    assert _json(response)["content"] == "FROM scratch\n"


async def test_read_jsonl_is_text(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "log.jsonl").write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=log.jsonl"), "/api/workspace/read"
    )
    assert response.status_code == 200
    assert _json(response)["content"] == '{"a": 1}\n{"a": 2}\n'


async def test_read_invalid_utf8_is_tolerated(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    """Byte non-UTF-8 senza null byte: si legge con caratteri sostitutivi."""
    (workspace_root / "latin.txt").write_bytes(b"caff\xe8 e brioche")
    response = await routes.dispatch(
        _request("/api/workspace/read?path=latin.txt"), "/api/workspace/read"
    )
    assert response.status_code == 200
    assert "caff" in _json(response)["content"]
    assert "�" in _json(response)["content"]


# ---------------------------------------------------------------------------
# /api/workspace/write
# ---------------------------------------------------------------------------


async def test_write_requires_auth(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/write", token=None, data={"path": "a.txt", "content": "x"}),
        "/api/workspace/write",
    )
    assert response.status_code == 401


async def test_write_requires_allow_write(routes: WorkspaceRoutes, config_path: Path) -> None:
    _set_workspace_config(config_path, allow_write=False)
    response = await routes.dispatch(
        _request("/api/workspace/write", data={"path": "a.txt", "content": "x"}),
        "/api/workspace/write",
    )
    assert response.status_code == 403


async def test_write_fails_closed_when_config_raises(
    routes: WorkspaceRoutes,
    workspace_root: Path,
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("jenny.config.loader.load_config", _boom)
    response = await routes.dispatch(
        _request("/api/workspace/write", data={"path": "a.txt", "content": "x"}),
        "/api/workspace/write",
    )
    assert response.status_code == 503
    assert not (workspace_root / "a.txt").exists()


async def test_delete_fails_closed_when_config_raises(
    routes: WorkspaceRoutes,
    workspace_root: Path,
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (workspace_root / "keep.txt").write_text("stay", encoding="utf-8")

    def _boom(*args, **kwargs):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("jenny.config.loader.load_config", _boom)
    response = await routes.dispatch(
        _request("/api/workspace/delete?path=keep.txt"), "/api/workspace/delete"
    )
    assert response.status_code == 503
    assert (workspace_root / "keep.txt").exists()


async def test_write_missing_header_returns_400(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(_request("/api/workspace/write"), "/api/workspace/write")
    assert response.status_code == 400


async def test_write_invalid_json_header_returns_400(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    request = _request("/api/workspace/write")
    request.headers["X-Jenny-Workspace-Data"] = "{not json"
    response = await routes.dispatch(request, "/api/workspace/write")
    assert response.status_code == 400


async def test_write_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/write", data={"path": "new/note.txt", "content": "ciao"}),
        "/api/workspace/write",
    )
    assert response.status_code == 200
    assert (workspace_root / "new" / "note.txt").read_text(encoding="utf-8") == "ciao"


async def test_write_rejects_path_traversal(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request(
            "/api/workspace/write", data={"path": "../outside.txt", "content": "x"}
        ),
        "/api/workspace/write",
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /api/workspace/mkdir
# ---------------------------------------------------------------------------


async def test_mkdir_requires_allow_write(routes: WorkspaceRoutes, config_path: Path) -> None:
    _set_workspace_config(config_path, allow_write=False)
    response = await routes.dispatch(
        _request("/api/workspace/mkdir?path=newdir"), "/api/workspace/mkdir"
    )
    assert response.status_code == 403


async def test_mkdir_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/mkdir?path=newdir"), "/api/workspace/mkdir"
    )
    assert response.status_code == 200
    assert (workspace_root / "newdir").is_dir()


# ---------------------------------------------------------------------------
# /api/workspace/rename
# ---------------------------------------------------------------------------


async def test_rename_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "old.txt").write_text("z", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/rename?oldPath=old.txt&newPath=new.txt"),
        "/api/workspace/rename",
    )
    assert response.status_code == 200
    assert not (workspace_root / "old.txt").exists()
    assert (workspace_root / "new.txt").read_text(encoding="utf-8") == "z"


async def test_rename_missing_source_returns_404(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/rename?oldPath=missing.txt&newPath=new.txt"),
        "/api/workspace/rename",
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /api/workspace/delete
# ---------------------------------------------------------------------------


async def test_delete_requires_allow_delete(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    _set_workspace_config(config_path, allow_delete=False)
    (workspace_root / "gone.txt").write_text("z", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/delete?path=gone.txt"), "/api/workspace/delete"
    )
    assert response.status_code == 403
    assert (workspace_root / "gone.txt").exists()


async def test_delete_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "gone.txt").write_text("z", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/delete?path=gone.txt"), "/api/workspace/delete"
    )
    assert response.status_code == 200
    assert not (workspace_root / "gone.txt").exists()


# ---------------------------------------------------------------------------
# /api/workspace/copy
# ---------------------------------------------------------------------------


async def test_copy_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "src.txt").write_text("dati", encoding="utf-8")
    response = await routes.dispatch(
        _request("/api/workspace/copy?path=src.txt&dest=dst.txt"), "/api/workspace/copy"
    )
    assert response.status_code == 200
    assert (workspace_root / "dst.txt").read_text(encoding="utf-8") == "dati"
    assert (workspace_root / "src.txt").exists()


# ---------------------------------------------------------------------------
# /api/workspace/download
# ---------------------------------------------------------------------------


async def test_download_requires_auth(routes: WorkspaceRoutes, config_path: Path) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/download?path=a.txt", token=None),
        "/api/workspace/download",
    )
    assert response.status_code == 401


async def test_download_missing_file_returns_404(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/download?path=missing.bin"), "/api/workspace/download"
    )
    assert response.status_code == 404


async def test_download_rejects_directory(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "adir").mkdir()
    response = await routes.dispatch(
        _request("/api/workspace/download?path=adir"), "/api/workspace/download"
    )
    assert response.status_code == 400


async def test_download_happy_path(
    routes: WorkspaceRoutes, workspace_root: Path, config_path: Path
) -> None:
    (workspace_root / "file.bin").write_bytes(b"\x00\x01binary")
    response = await routes.dispatch(
        _request("/api/workspace/download?path=file.bin"), "/api/workspace/download"
    )
    assert response.status_code == 200
    assert response.body == b"\x00\x01binary"
    assert 'filename="file.bin"' in response.headers["Content-Disposition"]


async def test_download_rejects_path_traversal(
    routes: WorkspaceRoutes, config_path: Path
) -> None:
    response = await routes.dispatch(
        _request("/api/workspace/download?path=../../etc/passwd"),
        "/api/workspace/download",
    )
    assert response.status_code == 400
