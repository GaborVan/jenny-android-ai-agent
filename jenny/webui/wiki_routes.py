"""Adapter di route HTTP per le API Wiki + Audit della WebUI (estratto da
ws_http). Stesso pattern di ``WebUISettingsRouter``/``SkillsRoutes``.

Solo letture e parametri corti. La chiusura di un audit non è qui: porta una
nota di testo libero, e questo trasporto non può trasportare contenuto (v. la
docstring di ``webui.commands``). È il comando ``audit.resolve`` dell'RPC
WebSocket.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

if TYPE_CHECKING:
    from jenny.webui.wiki_search import WikiSearchService

from jenny.channels.http_utils import (
    http_error,
    http_json_response,
    parse_query,
    query_first,
)

QueryParams = dict[str, list[str]]

# Chiavi frontmatter esposte al client in ``/api/page``. Tutto il resto (URL
# sorgente, provenance, slug/flag interni) resta lato server: il frontmatter
# integrale non è usato dalla UI e ne inoltrerebbe chiavi arbitrarie inserite
# dall'autore.
_FRONTMATTER_ALLOWLIST = frozenset(
    {"title", "type", "entity_type", "tags", "created", "updated"}
)


def _filter_frontmatter(fm: Any) -> dict[str, Any] | None:
    """Restringe il frontmatter alle sole chiavi presentazionali consentite."""
    if not isinstance(fm, dict):
        return fm
    return {k: v for k, v in fm.items() if k in _FRONTMATTER_ALLOWLIST}


def safe_wiki_page_path(input_path: str) -> str | None:
    """Normalizza e valida un path di pagina wiki relativo.

    Rifiuta path assoluti o che risalgono fuori dalla wiki (``..``). Ritorna il
    path normalizzato relativo, ``"index.md"`` se vuoto, o ``None`` se invalido.
    """
    if not input_path:
        return "index.md"
    if os.path.isabs(input_path):
        return None
    normalized = os.path.normpath(input_path).replace(os.sep, "/")
    if normalized.startswith(".."):
        return None
    return normalized


def _collect_projects(wikis_dir: Path) -> list[dict[str, Any]]:
    """``[{name, modified}]`` per ogni wiki, ordinate come le da' la discovery.

    ``modified`` e' l'mtime della radice della wiki, che oggi e' il solo segnale
    di attivita' disponibile: la conversazione di un progetto non esiste ancora
    (item 3). Quando esistera', l'ultima attivita' dovra' venire da lei, non dal
    filesystem — un `lint` non e' attivita' dell'utente.
    """
    from jenny.utils.wiki_paths import discover_wiki_roots

    projects: list[dict[str, Any]] = []
    for name, root in discover_wiki_roots(wikis_dir).items():
        try:
            modified = int(root.stat().st_mtime)
        except OSError:
            modified = 0
        projects.append({"name": name, "modified": modified})
    return projects


class WikiRoutes:
    """Route ``/api/{config,tree,graph,page}`` e ``/api/audit*``."""

    def __init__(
        self,
        *,
        check_api_token: Callable[[WsRequest], bool],
        get_workspace_root: Callable[[], Path],
        json_safe: Callable[[Any], Any],
        search_service: "WikiSearchService | None" = None,
    ) -> None:
        self._check_api_token = check_api_token
        self._get_workspace_root = get_workspace_root
        self._json_safe = json_safe
        # Collaboratore privato con stato: la cache grafo+indice vive quanto il
        # gateway. Iniettabile perché i test devono poterla azzerare o
        # sostituire senza passare dal composition root; costruito pigramente
        # perché ``wiki_search`` tira dentro ``wiki`` e quindi ``markdown``, e
        # questo ``__init__`` gira all'avvio del gateway — come tutti gli altri
        # import di questo modulo, sta fuori dal cammino di boot.
        self._search_service = search_service

    # -- helpers --

    def _get_search_service(self) -> "WikiSearchService":
        if self._search_service is None:
            from jenny.webui.wiki_search import WikiSearchService

            self._search_service = WikiSearchService()
        return self._search_service

    def _get_wikis_dir(self) -> Path:
        from jenny.config.loader import load_config

        try:
            wikis_subdir = load_config().wiki.wikis_dir
        except Exception:
            wikis_subdir = "wikis"
        return self._get_workspace_root() / wikis_subdir

    def _check_wiki_enabled(self) -> Response | None:
        from jenny.config.loader import load_config

        try:
            if not load_config().wiki.enabled:
                return http_error(503, "wiki is disabled")
        except Exception:
            pass
        return None

    # -- dispatch --

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        if path == "/api/config":
            return await self._wiki_config(request)
        if path == "/api/projects":
            return await self._projects_list(request)
        if path == "/api/tree":
            return await self._wiki_tree(request)
        if path == "/api/graph":
            return await self._wiki_graph(request)
        if path == "/api/page":
            return await self._wiki_page(request)
        if path == "/api/audit":
            return await self._audit_list(request)
        if path == "/api/audit/create":
            return await self._audit_create(request)
        return None

    # -- wiki handlers --

    async def _wiki_tree(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import build_home_tree, build_tree, discover_wikis

        query = parse_query(request.path)
        wiki_name = query_first(query, "wiki") or ""
        wikis_dir = self._get_wikis_dir()

        if wiki_name:
            wikis = discover_wikis(wikis_dir)
            if wiki_name not in wikis:
                return http_error(404, "wiki not found")
            wiki_root = wikis[wiki_name].parent
            loop = asyncio.get_running_loop()
            tree = await loop.run_in_executor(None, build_tree, wiki_root)
        else:
            loop = asyncio.get_running_loop()
            tree = await loop.run_in_executor(None, build_home_tree, wikis_dir)

        def tree_to_dict(node):
            result = {"name": node.name, "path": node.path, "kind": node.kind}
            if node.children:
                result["children"] = [tree_to_dict(c) for c in node.children]
            return result

        return http_json_response(tree_to_dict(tree))

    async def _wiki_graph(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import build_home_graph, discover_wikis

        query = parse_query(request.path)
        wiki_name = query_first(query, "wiki") or ""
        wikis_dir = self._get_wikis_dir()
        loop = asyncio.get_running_loop()

        # Grafo e indice full-text viaggiano nella *stessa* risposta perché le
        # postings dell'indice sono indici nell'array ``nodes`` qui sotto.
        # Servirli da due endpoint aprirebbe la finestra in cui la wiki cambia
        # fra le due chiamate: il client accenderebbe i nodi sbagliati.
        if wiki_name:
            wikis = discover_wikis(wikis_dir)
            if wiki_name not in wikis:
                return http_error(404, "wiki not found")
            bundle = await loop.run_in_executor(
                None, self._get_search_service().bundle, wikis[wiki_name]
            )
            graph = bundle.graph
            search = bundle.search
        else:
            # Vista home: nodi = wiki, non pagine. Non c'è testo da cercare.
            graph = await loop.run_in_executor(None, build_home_graph, wikis_dir)
            search = None

        return http_json_response(
            {
                "nodes": [
                    {
                        "id": n.id,
                        "label": n.label,
                        "path": n.path,
                        "group": n.group,
                        "degree": n.degree,
                        "title": n.title,
                    }
                    for n in graph.nodes
                ],
                "edges": [{"source": e.source, "target": e.target} for e in graph.edges],
                "search": search,
            }
        )

    async def _wiki_page(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import create_renderer, discover_wikis, resolve_wikilink

        query = parse_query(request.path)
        wiki_name = query_first(query, "wiki") or ""
        page_path = query_first(query, "page") or ""
        wikis_dir = self._get_wikis_dir()
        wikis = discover_wikis(wikis_dir)

        if not wiki_name:
            full = wikis_dir / "_index.md"
            current_wiki = None
            wiki_root_for_renderer = wikis_dir
            containment_root = wikis_dir
        else:
            if wiki_name not in wikis:
                return http_error(404, "wiki not found")
            wiki_dir = wikis[wiki_name]
            wiki_root = wiki_dir.parent
            rel = safe_wiki_page_path(page_path) if page_path else "index.md"
            if not rel:
                return http_error(400, "invalid page path")
            full = wiki_dir / rel
            if full.is_dir():
                full = full / "index.md"
            if full.suffix != ".md":
                full = full.with_suffix(".md")
            if not full.is_file():
                candidate = resolve_wikilink(wiki_root, rel)
                if candidate:
                    full = candidate
            current_wiki = wiki_name
            wiki_root_for_renderer = wiki_root
            # Contenimento nella pages-dir ``wiki/`` (non nell'intera wikis_dir):
            # impedisce di raggiungere i fratelli raw/ audit/ log/.
            containment_root = wiki_dir

        if not full.is_file():
            return http_error(404, "file not found")

        try:
            full.resolve().relative_to(containment_root.resolve())
        except ValueError:
            return http_error(403, "path escapes wiki root")

        raw = full.read_text("utf-8")
        loop = asyncio.get_running_loop()
        renderer = create_renderer(
            wiki_root_for_renderer, current_wiki=current_wiki, wikis_map=wikis
        )
        rendered = await loop.run_in_executor(None, lambda: renderer(raw))

        if current_wiki:
            rel_page = str(full.relative_to(wikis[wiki_name])).replace(os.sep, "/")
        else:
            rel_page = "_index.md"

        return http_json_response(
            {
                "wiki": current_wiki,
                "page": rel_page,
                "path": str(full.relative_to(wikis_dir)).replace(os.sep, "/"),
                "title": rendered.title,
                "frontmatter": self._json_safe(_filter_frontmatter(rendered.frontmatter)),
                "html": rendered.html,
                "raw": rendered.raw_markdown,
            }
        )

    async def _wiki_config(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import discover_wikis

        wikis_dir = self._get_wikis_dir()
        wikis = list(discover_wikis(wikis_dir).keys())
        default_wiki = "main" if "main" in wikis else (wikis[0] if wikis else None)
        return http_json_response(
            {
                "author": "me",
                "wikis": wikis,
                "defaultWiki": default_wiki,
                "homePath": "_index.md",
            }
        )

    async def _projects_list(self, request: WsRequest) -> Response:
        """Elenco dei progetti per lo scope chip.

        **Un progetto e' una wiki**, quindi l'elenco e' `discover_wiki_roots` e
        non il contenuto di una cartella `projects/`: quella non esiste, e il
        chip la leggeva (v. `roadmap/project-sessions.md`, item 10). `dir` viaggia
        col payload perche' il chip mostra lo scope come un percorso e il nome
        della cartella e' configurabile (`config.wiki.wikis_dir`).
        """
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err

        wikis_dir = self._get_wikis_dir()
        loop = asyncio.get_running_loop()
        projects = await loop.run_in_executor(None, _collect_projects, wikis_dir)
        return http_json_response({"dir": wikis_dir.name, "projects": projects})

    # -- audit handlers --

    async def _audit_list(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import discover_wikis, list_audits

        query = parse_query(request.path)
        wiki_name = query_first(query, "wiki") or ""
        target = query_first(query, "target") or None
        mode = query_first(query, "mode") or "open"
        if mode not in {"open", "resolved", "all"}:
            return http_error(400, "invalid mode")

        if not wiki_name:
            return http_json_response({"entries": []})

        wikis = discover_wikis(self._get_wikis_dir())
        if wiki_name not in wikis:
            return http_error(404, "wiki not found")
        pages_dir = wikis[wiki_name]
        wiki_root = pages_dir.parent

        audits = list_audits(wiki_root, target=target, mode=mode)
        return http_json_response({"entries": audits})

    async def _audit_create(self, request: WsRequest) -> Response:
        if not self._check_api_token(request):
            return http_error(401, "Unauthorized")
        err = self._check_wiki_enabled()
        if err:
            return err
        from jenny.webui.wiki import create_audit, discover_wikis

        query = parse_query(request.path)
        wikis = discover_wikis(self._get_wikis_dir())

        wiki_name = query_first(query, "wiki") or ""
        if not wiki_name or wiki_name not in wikis:
            return http_error(400, "wiki required")

        wiki_root = wikis[wiki_name].parent
        target = query_first(query, "target") or ""

        pages_dir = wikis[wiki_name]
        raw_path = (pages_dir / (target or "index.md")).resolve()
        try:
            raw_path.relative_to(pages_dir.resolve())
        except ValueError:
            return http_error(403, "Forbidden")
        raw_markdown = ""
        if raw_path.is_file():
            raw_markdown = raw_path.read_text("utf-8")

        try:
            sel_start = int(query_first(query, "selStart") or 0)
            sel_end = int(query_first(query, "selEnd") or 0)
        except (ValueError, TypeError):
            return http_error(400, "invalid selStart/selEnd")

        try:
            result = create_audit(
                wiki_root=wiki_root,
                target=target,
                raw_markdown=raw_markdown,
                sel_start=sel_start,
                sel_end=sel_end,
                comment=query_first(query, "comment") or "",
                severity=query_first(query, "severity") or "warn",
                author=query_first(query, "author") or "anonymous",
            )
            result.pop("entry", None)
            return http_json_response(result)
        except FileNotFoundError as exc:
            return http_error(404, str(exc))
        except ValueError as exc:
            return http_error(400, str(exc))
