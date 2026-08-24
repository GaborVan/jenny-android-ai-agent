"""Adapter di route HTTP per il file-manager del workspace (estratto da ws_http).

Stesso pattern router di ``SkillsRoutes``/``WikiRoutes``. Le operazioni su file
passano tutte per ``webui.workspace_files.validate_path`` (che delega al gate
unico symlink-safe/fail-closed del core — Fase 2).

Qui vivono solo letture e operazioni con parametri corti. La **scrittura** non
c'è di proposito: il contenuto di un file non può viaggiare su questo trasporto
(l'hook di handshake di ``websockets`` non legge body; query string e header
stanno in 8192 byte per riga e in ISO-8859-1), quindi ``workspace.write`` è un
comando dell'RPC WebSocket — v. ``webui.commands`` e ``channels.ws_rpc``.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Callable
from pathlib import Path

from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.channels.http_utils import (
    http_error,
    http_json_response,
    parse_query,
    query_first,
)

QueryParams = dict[str, list[str]]



def _project_delete_refusal(workspace_root: Path, target: Path) -> str | None:
    """Il motivo per cui *target* non si cancella da qui, o ``None``.

    **La delete del file manager non deve poter cancellare un progetto**, e il
    perche' non e' che sia pericolosa: e' che e' *parziale*. Un progetto vive in
    due domini — l'albero sotto ``wikis/<nome>/`` e le quattro tracce della sua
    conversazione, che stanno altrove (v.
    ``session/project_rename.py::project_trace_paths``). Una ``rmtree`` raggiunge
    il primo e non sa del secondo, quindi libera il *nome* senza liberare la
    conversazione: il progetto successivo creato con quel nome se la riprende
    tutta. Riprodotto sul telefono il 24/08/2026.

    Il rifiuto **dice dove**, che e' la forma degli altri rifiuti di questo
    codice (``command/builtin.py::_gardener_no_target``, il rifiuto di
    ``journal_append`` fuori da un progetto): su un telefono un divieto che non
    indica la strada e' un vicolo cieco.

    Due bersagli, non uno. La radice del progetto e' quello ovvio; la sua
    ``wiki/`` e' lo stesso guasto per un'altra porta, perche' senza quella
    cartella ``is_wiki_root`` diventa falso e il progetto sparisce dal picker
    **con la chat ancora attaccata al nome** — cioe' di nuovo l'orfano.

    Solo i figli diretti di ``wikis_dir``: ``is_wiki_root`` da solo direbbe di si'
    a qualunque cartella che contenga una ``wiki/``, e bloccherebbe
    cancellazioni legittime altrove nel workspace.
    """
    from jenny.session.keys import is_valid_project_name
    from jenny.utils.wiki_paths import is_wiki_root

    try:
        from jenny.config.loader import load_config

        wikis_dir = workspace_root / (load_config().wiki.wikis_dir or "wikis")
    except Exception:  # noqa: BLE001 — senza config si usa il nome di default
        wikis_dir = workspace_root / "wikis"

    if target.parent == wikis_dir and is_wiki_root(target):
        name = target.name
    elif (
        target.name == "wiki"
        and target.parent.parent == wikis_dir
        and is_wiki_root(target.parent)
    ):
        name = target.parent.name
    else:
        return None
    if not is_valid_project_name(name):
        # Una cartella il cui nome non puo' essere il nome di una conversazione
        # (``wikis/Ricerca ETF``, v. ``_collect_projects``) non ha una chat da
        # orfanare, e ``project.delete`` la rifiuterebbe proprio per quel nome:
        # rifiutare qui la renderebbe incancellabile da qualunque porta.
        return None
    return (
        f"`{name}` is a project, not just a folder: its conversation lives outside "
        "this tree, and deleting the folder here would leave that behind under a name "
        "anything else could take. Deleting a project is its own operation and it "
        "removes both — the file browser uses it for you, so if you are seeing this "
        "the app is out of date or something else made the call."
    )


class WorkspaceRoutes:
    """Route ``/api/workspace/*`` (CRUD file del workspace)."""

    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_workspace_root: Callable[[], Path],
    ) -> None:
        self._check_api_token = check_api_token
        self._get_workspace_root = get_workspace_root

    def _require_workspace_flag(
        self, attr: str, status: int, message: str
    ) -> Response | None:
        """Verifica un flag booleano di ``config.workspace``, fail-closed.

        Ritorna una ``Response`` di errore se il flag è disattivato oppure se
        ``load_config()`` solleva: in quest'ultimo caso NON si prosegue mai
        verso l'operazione filesystem (503), così un errore di config non
        scavalca silenziosamente il gate di sicurezza.
        """
        from jenny.config.loader import load_config

        try:
            allowed = bool(getattr(load_config().workspace, attr))
        except Exception:
            return http_error(503, "workspace configuration unavailable")
        if not allowed:
            return http_error(status, message)
        return None

    def _check_workspace_enabled(self) -> Response | None:
        return self._require_workspace_flag("enabled", 503, "workspace is disabled")

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        handlers = {
            "/api/workspace/list": self._list,
            "/api/workspace/read": self._read,
            "/api/workspace/mkdir": self._mkdir,
            "/api/workspace/rename": self._rename,
            "/api/workspace/delete": self._delete,
            "/api/workspace/copy": self._copy,
            "/api/workspace/download": self._download,
        }
        handler = handlers.get(path)
        if handler is None:
            return None
        return await handler(request)

    async def _list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_workspace_enabled()
        if err:
            return err
        from jenny.webui.workspace_files import list_directory, validate_path

        query = parse_query(request.path)
        rel_path = query_first(query, "path") or ""
        workspace_root = self._get_workspace_root()
        try:
            full_path = validate_path(workspace_root, rel_path)
            items = list_directory(full_path, workspace_root=workspace_root)
            return http_json_response({"items": items, "path": rel_path})
        except ValueError as e:
            return http_error(400, str(e))
        except FileNotFoundError:
            return http_error(404, "path not found")
        except PermissionError:
            return http_error(403, "permission denied")
        except OSError as e:
            return http_error(400, str(e))

    async def _read(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_workspace_enabled()
        if err:
            return err
        from jenny.webui.workspace_files import (
            WorkspaceBinaryFileError,
            read_file,
            validate_path,
        )

        query = parse_query(request.path)
        rel_path = query_first(query, "path") or ""
        workspace_root = self._get_workspace_root()
        try:
            full_path = validate_path(workspace_root, rel_path)
            from jenny.config.loader import load_config

            try:
                max_size = load_config().workspace.max_file_size
            except Exception:
                max_size = 1_000_000
            content = read_file(full_path, max_size=max_size)
            return http_json_response({"content": content, "path": rel_path})
        except WorkspaceBinaryFileError:
            # 415: il client (viewer workspace) reagisce delegando
            # l'apertura all'app di sistema via bridge nativo.
            return http_error(415, "binary file")
        except ValueError as e:
            return http_error(400, str(e))
        except FileNotFoundError:
            return http_error(404, "path not found")
        except PermissionError:
            return http_error(403, "permission denied")
        except OSError as e:
            return http_error(400, str(e))

    async def _mkdir(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_workspace_enabled()
        if err:
            return err
        err = self._require_workspace_flag(
            "allow_write", 403, "workspace writes are disabled"
        )
        if err:
            return err
        from jenny.webui.workspace_files import create_directory, validate_path

        query = parse_query(request.path)
        rel_path = query_first(query, "path") or ""
        workspace_root = self._get_workspace_root()
        try:
            full_path = validate_path(workspace_root, rel_path)
            create_directory(full_path)
            return http_json_response({"success": True, "path": rel_path})
        except ValueError as e:
            return http_error(400, str(e))
        except FileNotFoundError:
            return http_error(404, "path not found")
        except PermissionError:
            return http_error(403, "permission denied")
        except OSError as e:
            return http_error(400, str(e))

    async def _rename(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_workspace_enabled()
        if err:
            return err
        from jenny.webui.workspace_files import rename_path, validate_path

        query = parse_query(request.path)
        old_rel = query_first(query, "oldPath") or ""
        new_rel = query_first(query, "newPath") or ""
        workspace_root = self._get_workspace_root()
        try:
            old_path = validate_path(workspace_root, old_rel)
            new_path = validate_path(workspace_root, new_rel)
            rename_path(old_path, new_path)
            return http_json_response({"success": True})
        except ValueError as e:
            return http_error(400, str(e))
        except FileNotFoundError:
            return http_error(404, "path not found")
        except PermissionError:
            return http_error(403, "permission denied")
        except OSError as e:
            return http_error(400, str(e))

    async def _delete(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_workspace_enabled()
        if err:
            return err
        err = self._require_workspace_flag(
            "allow_delete", 403, "workspace deletes are disabled"
        )
        if err:
            return err
        from jenny.webui.workspace_files import delete_path, validate_path

        query = parse_query(request.path)
        rel_path = query_first(query, "path") or ""
        workspace_root = self._get_workspace_root()
        try:
            full_path = validate_path(workspace_root, rel_path)
            refusal = _project_delete_refusal(workspace_root, full_path)
            if refusal:
                return http_error(403, refusal)
            delete_path(full_path)
            return http_json_response({"success": True, "path": rel_path})
        except ValueError as e:
            return http_error(400, str(e))
        except FileNotFoundError:
            return http_error(404, "path not found")
        except PermissionError:
            return http_error(403, "permission denied")
        except OSError as e:
            return http_error(400, str(e))

    async def _copy(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_workspace_enabled()
        if err:
            return err
        from jenny.webui.workspace_files import copy_path, validate_path

        query = parse_query(request.path)
        src_rel = query_first(query, "path") or ""
        dest_rel = query_first(query, "dest") or ""
        workspace_root = self._get_workspace_root()
        try:
            src_path = validate_path(workspace_root, src_rel)
            dest_path = validate_path(workspace_root, dest_rel)
            copy_path(src_path, dest_path)
            return http_json_response({"success": True})
        except ValueError as e:
            return http_error(400, str(e))
        except FileNotFoundError:
            return http_error(404, "path not found")
        except PermissionError:
            return http_error(403, "permission denied")
        except OSError as e:
            return http_error(400, str(e))

    async def _download(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_workspace_enabled()
        if err:
            return err
        from jenny.webui.workspace_files import validate_path

        query = parse_query(request.path)
        rel_path = query_first(query, "path") or ""
        workspace_root = self._get_workspace_root()
        try:
            full_path = validate_path(workspace_root, rel_path)
        except ValueError as e:
            return http_error(400, str(e))
        if not full_path.exists():
            return http_error(404, "path not found")
        if full_path.is_dir():
            return http_error(400, "cannot download a directory")
        try:
            data = full_path.read_bytes()
        except FileNotFoundError:
            return http_error(404, "path not found")
        except PermissionError:
            return http_error(403, "permission denied")
        except OSError as e:
            return http_error(400, str(e))

        content_type = mimetypes.guess_type(full_path.name)[0] or "application/octet-stream"
        headers = Headers(
            [
                ("Content-Type", content_type),
                ("Content-Disposition", f'attachment; filename="{full_path.name}"'),
            ]
        )
        return Response(200, "OK", headers, data)
