"""Wiki backend logic — renderer, tree, graph, audit.

Provides the Python-native replacement for the Node/Express wiki server.
Supports multi-wiki: each wiki lives under workspace/wikis/{name}/wiki/.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from typing import override  # Python 3.12+
except ImportError:
    from typing_extensions import override  # backport 3.11
from typing import Any, Callable
from urllib.parse import quote as url_quote

from markdown import Markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor

from jenny.utils.path import atomic_write
from jenny.webui.audit import (
    AuditEntry,
    compute_anchor,
    filename_for,
    from_markdown,
    make_id,
    to_markdown,
)

# ── Constants ────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?")
_WIKILINK_RE = re.compile(r"\[\[(.+?)\]\]")

# Cartelle di primo livello nascoste da grafo e albero file: i summaries sono il
# livello di *citazione* (digest delle fonti), non contenuto da navigare. Le
# pagine restano servibili da /api/page (i link "Sources" continuano a funzionare).
_HIDDEN_TOP_GROUPS = frozenset({"summaries"})


def _top_group(rel: str) -> str:
    """Gruppo di primo livello di un path relativo alla pages-dir ``wiki/``."""
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else "other"

# ── Public dataclasses ───────────────────────────────────────────────────────


@dataclass
class RenderedPage:
    html: str
    frontmatter: dict[str, Any] | None
    raw_markdown: str
    title: str | None


@dataclass
class TreeNode:
    name: str
    path: str
    kind: str  # "file" | "dir"
    children: list[TreeNode] | None = None


@dataclass
class GraphNode:
    id: str
    label: str
    path: str
    group: str
    degree: int
    title: str | None


@dataclass
class GraphEdge:
    source: str
    target: str


@dataclass
class GraphData:
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9.\-]", "", name)
    return name.strip("-")


def _normalize_page_path(path: str) -> str:
    parts = path.split("/")
    slugged = [_slugify(p) for p in parts]
    if not slugged[-1].endswith(".md"):
        slugged[-1] += ".md"
    return "/".join(slugged)


def _split_wikilink(raw: str) -> tuple[str, str | None]:
    # Markdown tables require escaping the pipe as \|; normalize it so that
    # [[target\|label]] is parsed the same as [[target|label]].
    normalized = raw.replace("\\|", "|")
    parts = normalized.split("|", 1)
    if len(parts) >= 2:
        return (parts[0].strip(), parts[1].strip())
    return (parts[0].strip(), None)


def _extract_title(text: str) -> str | None:
    fm = _FRONTMATTER_RE.match(text)
    if fm:
        t = re.search(r"^title:\s*(.+)$", fm.group(1), re.M)
        if t:
            return t.group(1).strip().strip('"').strip("'")
    h1 = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return h1.group(1) if h1 else None


def _strip_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    m = _FRONTMATTER_RE.match(text)
    frontmatter: dict[str, Any] | None = None
    body = text
    if m:
        import yaml

        try:
            parsed = yaml.safe_load(m.group(1))
            frontmatter = parsed if isinstance(parsed, dict) else {}
        except Exception:
            frontmatter = {}
        body = text[m.end() :]
    title = None
    if frontmatter and "title" in frontmatter:
        title = frontmatter["title"]
    else:
        h1 = re.search(r"^#\s+(.+?)\s*$", body, re.M)
        if h1:
            title = h1.group(1)
    return frontmatter, body, title


# ── Discovery ────────────────────────────────────────────────────────────────


def discover_wikis(wikis_dir: Path) -> dict[str, Path]:
    """Scan wikis_dir for subdirectories containing a wiki/ folder.

    Returns {name: wikis_dir/name/wiki} sorted alphabetically by name.
    """
    if not wikis_dir.exists():
        return {}
    result: dict[str, Path] = {}
    for child in sorted(wikis_dir.iterdir()):
        if child.is_dir():
            wiki_sub = child / "wiki"
            if wiki_sub.is_dir():
                result[child.name] = wiki_sub
    return result


# ── Tree ─────────────────────────────────────────────────────────────────────


def _walk(
    dir_path: Path,
    rel: str,
    top_name: str | None = None,
    skip_names: frozenset[str] | None = None,
) -> TreeNode:
    # ``skip_names`` si applica solo a questo livello (non alla ricorsione): serve
    # a nascondere cartelle di primo livello come summaries/ dall'albero.
    skip = skip_names or frozenset()
    entries = sorted(
        [e for e in dir_path.iterdir() if not e.name.startswith(".") and e.name not in skip],
        key=lambda e: (not e.is_dir(), e.name.lower()),
    )
    children: list[TreeNode] = []
    for e in entries:
        # Non anteporre lo slash quando ``rel`` è vuoto (radice della wiki): un
        # path come ``/concepts/x.md`` verrebbe scartato come assoluto da
        # ``safe_wiki_page_path`` (os.path.isabs) → 404/400 nel drawer file.
        node_rel = f"{rel}/{e.name}" if rel else e.name
        if e.is_dir():
            children.append(_walk(e, node_rel))
        elif e.name.endswith(".md"):
            children.append(
                TreeNode(
                    name=e.name.removesuffix(".md"),
                    path=node_rel,
                    kind="file",
                )
            )
    return TreeNode(name=top_name or dir_path.name, path=rel, kind="dir", children=children)


def build_tree(wiki_root: Path) -> TreeNode:
    """Build a tree for a single wiki.

    wiki_root is the wiki root directory (contains wiki/ and audit/).
    Pages are scanned from wiki_root / "wiki".
    """
    pages_dir = wiki_root / "wiki"
    if not pages_dir.exists():
        return TreeNode(name="wiki", path="wiki", kind="dir", children=[])
    return _walk(pages_dir, "", skip_names=_HIDDEN_TOP_GROUPS)


def build_home_tree(wikis_dir: Path) -> TreeNode:
    """Build a combined tree across all discovered wikis."""
    root = TreeNode(name="wikis", path="wikis", kind="dir", children=[])
    index_path = wikis_dir / "_index.md"
    if index_path.exists():
        root.children.append(
            TreeNode(name="Home", path="_index.md", kind="file")
        )
    wikis = discover_wikis(wikis_dir)
    for name in sorted(wikis):
        pages_dir = wikis[name]
        subtree = _walk(pages_dir, f"{name}/wiki", top_name=name, skip_names=_HIDDEN_TOP_GROUPS)
        root.children.append(subtree)
    return root


# ── Graph ────────────────────────────────────────────────────────────────────


def build_graph(wiki_root: Path) -> GraphData:
    """Build a graph of wiki pages and their wikilink connections for one wiki."""
    pages_dir = wiki_root / "wiki"
    if not pages_dir.exists():
        return GraphData(nodes=[], edges=[])

    # I summaries sono esclusi dal grafo (livello di citazione, non contenuto):
    # i loro nodi/archi non compaiono; le pagine restano comunque servibili.
    files = [
        f for f in pages_dir.rglob("*.md")
        if _top_group(f.relative_to(pages_dir).as_posix()) not in _HIDDEN_TOP_GROUPS
    ]
    by_key: dict[str, str] = {}
    nodes: dict[str, GraphNode] = {}
    contents: dict[str, str] = {}
    # Le chiavi rel-path sono univoche per file; le chiavi per stem possono
    # collidere (es. concepts/foo.md vs entities/foo.md). Raccogliamo i candidati
    # per stem e registriamo la chiave solo se punta a un unico nodo, così i
    # wikilink per stem ambiguo non risolvono in modo non deterministico.
    stem_candidates: dict[str, set[str]] = {}

    for f in files:
        rel = f.relative_to(pages_dir).as_posix()
        node_id = f"wiki/{rel}"
        stem = f.stem
        group = _top_group(rel)
        text = f.read_text("utf-8")
        contents[node_id] = text
        title = _extract_title(text) or stem

        nodes[node_id] = GraphNode(
            id=node_id,
            label=stem,
            path=rel,
            group=group,
            degree=0,
            title=title,
        )
        # Chiave rel-path: sempre univoca, ha precedenza.
        by_key[rel.removesuffix(".md")] = node_id
        stem_candidates.setdefault(stem, set()).add(node_id)
        if stem.lower() != stem:
            stem_candidates.setdefault(stem.lower(), set()).add(node_id)

    for key, ids in stem_candidates.items():
        if len(ids) == 1 and key not in by_key:
            by_key[key] = next(iter(ids))

    edges: list[GraphEdge] = []
    seen_pairs: set[frozenset[str]] = set()

    for f in files:
        rel = f.relative_to(pages_dir).as_posix()
        src_id = f"wiki/{rel}"
        for match in _WIKILINK_RE.finditer(contents[src_id]):
            inner = match.group(1).strip()
            target, _label = _split_wikilink(inner)
            if not target or target.startswith("#"):
                continue
            tgt_id = (
                by_key.get(target)
                or by_key.get(target.removesuffix(".md"))
                or by_key.get(target.lower())
            )
            if not tgt_id or tgt_id == src_id:
                continue
            # Dedup non orientato: A↔B è un unico arco e conta una sola volta
            # nel degree di ciascun estremo.
            pair = frozenset({src_id, tgt_id})
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append(GraphEdge(source=src_id, target=tgt_id))
            nodes[src_id].degree += 1
            nodes[tgt_id].degree += 1

    return GraphData(nodes=list(nodes.values()), edges=edges)


def build_home_graph(wikis_dir: Path) -> GraphData:
    """Star graph: central hub node + one node per wiki."""
    wikis = discover_wikis(wikis_dir)
    nodes: list[GraphNode] = [
        GraphNode(id="_home", label="Wikis", path="", group="home", degree=0, title=None),
    ]
    edges: list[GraphEdge] = []

    for name in sorted(wikis):
        pages_dir = wikis[name]
        page_count = len(list(pages_dir.rglob("*.md")))
        nodes.append(
            GraphNode(
                id=name,
                label=name,
                path=f"{name}/wiki",
                group="wiki",
                degree=page_count,
                title=None,
            )
        )
        edges.append(GraphEdge(source="_home", target=name))

    return GraphData(nodes=nodes, edges=edges)


# ── Wikilink resolution ─────────────────────────────────────────────────────


def _strip_wiki_prefix(page_ref: str) -> str:
    """Remove a leading wiki/ prefix used when pages are stored under wiki/."""
    if page_ref.startswith("wiki/"):
        return page_ref[5:]
    return page_ref


def resolve_wikilink(wiki_root: Path, target: str) -> Path | None:
    """Resolve a wikilink target to a file path.

    Tries: exact path, with .md, case-insensitive, slug, stem.
    wiki_root is the wiki root directory (contains wiki/ and audit/).
    """
    pages_dir = wiki_root / "wiki"
    target = _strip_wiki_prefix(target)
    # Exact path
    p = pages_dir / target
    if p.is_file():
        return p
    # Directory with index.md
    if p.is_dir():
        index = p / "index.md"
        if index.is_file():
            return index
    # With .md
    p = pages_dir / f"{target}.md"
    if p.is_file():
        return p
    # Case-insensitive on relative path
    target_norm = _normalize_page_path(target)
    for f in pages_dir.rglob("*.md"):
        rel = f.relative_to(pages_dir).as_posix()
        if rel.lower() == target_norm.lower():
            return f
        if rel.lower().removesuffix(".md") == target.lower():
            return f
    # Stem search
    return _find_by_stem(pages_dir, target)


def _find_by_stem(dir_path: Path, target: str) -> Path | None:
    for f in dir_path.rglob("*.md"):
        if f.stem == target or f.name == target:
            return f
        if f.stem.lower() == target.lower():
            return f
    return None


# ── Renderer ─────────────────────────────────────────────────────────────────


class _WikilinksExtension(Extension):
    def __init__(self, wiki_root: Path, current_wiki: str | None = None, wikis_map: dict[str, Path] | None = None):
        self.wiki_root = wiki_root
        self.current_wiki = current_wiki
        self.wikis_map = wikis_map
        super().__init__()

    @override
    def extendMarkdown(self, md: Markdown) -> None:
        md.preprocessors.register(
            _WikilinksPreprocessor(md, self.wiki_root, self.current_wiki, self.wikis_map),
            "wikilinks",
            29,
        )


class _WikilinksPreprocessor(Preprocessor):
    def __init__(self, md: Markdown, wiki_root: Path, current_wiki: str | None = None, wikis_map: dict[str, Path] | None = None):
        self.wiki_root = wiki_root
        self.current_wiki = current_wiki
        self.wikis_map = wikis_map or {}
        super().__init__(md)

    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)

        def replace(match: re.Match[str]) -> str:
            inner = match.group(1).strip()
            target, label = _split_wikilink(inner)
            href = self._resolve_target(target)
            display = label or target
            return f'<a class="wikilink" href="{href}">{display}</a>'

        text = _WIKILINK_RE.sub(replace, text)
        return text.split("\n")

    def _resolve_target(self, target: str) -> str:
        anchor = None
        if "#" in target:
            idx = target.index("#")
            anchor = target[idx + 1 :]
            target = target[:idx]

        if not target and anchor:
            return f"#{_slugify(anchor)}"

        # Cross-wiki colon syntax: wiki:page
        if ":" in target:
            wiki_name, _, page_ref = target.partition(":")
            if wiki_name in self.wikis_map:
                wiki_path = self.wikis_map[wiki_name]
                page_ref = _strip_wiki_prefix(page_ref)
                resolved = resolve_wikilink(wiki_path, page_ref)
                if resolved:
                    rel = resolved.relative_to(wiki_path / "wiki").as_posix()
                    href = f"/?wiki={wiki_name}&page={url_quote(rel, safe='/')}"
                else:
                    href = f"/?wiki={wiki_name}&page={url_quote(page_ref)}"
                return _append_anchor(href, anchor)

        # Cross-wiki path syntax: other/wiki/page
        parts = target.split("/")
        if len(parts) >= 2 and parts[0] in self.wikis_map:
            wiki_name = parts[0]
            wiki_path = self.wikis_map[wiki_name]
            page_ref = _strip_wiki_prefix("/".join(parts[1:]))
            resolved = resolve_wikilink(wiki_path, page_ref)
            if resolved:
                rel = resolved.relative_to(wiki_path / "wiki").as_posix()
                href = f"/?wiki={wiki_name}&page={url_quote(rel, safe='/')}"
            else:
                href = f"/?wiki={wiki_name}&page={url_quote(page_ref)}"
            return _append_anchor(href, anchor)

        # Same-wiki resolution
        if self.current_wiki and self.current_wiki in self.wikis_map:
            wiki_path = self.wikis_map[self.current_wiki]
            resolved = resolve_wikilink(wiki_path, _strip_wiki_prefix(target))
            if resolved:
                rel = resolved.relative_to(wiki_path / "wiki").as_posix()
                href = f"/?wiki={self.current_wiki}&page={url_quote(rel, safe='/')}"
                return _append_anchor(href, anchor)
            href = f"/?wiki={self.current_wiki}&page={url_quote(target)}"
            return _append_anchor(href, anchor)

        return _append_anchor("#", anchor)


def _append_anchor(href: str, anchor: str | None) -> str:
    if anchor:
        return f"{href}#{_slugify(anchor)}"
    return href


class _MermaidPreserveExtension(Extension):
    @override
    def extendMarkdown(self, md: Markdown) -> None:
        md.preprocessors.register(_MermaidPreservePreprocessor(md), "mermaid_preserve", 30)


class _MermaidPreservePreprocessor(Preprocessor):
    def run(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        in_mermaid = False
        buffer: list[str] = []
        for line in lines:
            if line.strip().startswith("```mermaid"):
                in_mermaid = True
                buffer = [line]
                continue
            if in_mermaid and line.strip() == "```":
                buffer.append(line)
                content = "\n".join(buffer[1:-1])
                escaped = (
                    content.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                out.append(f'<pre class="mermaid-block"><code class="language-mermaid">{escaped}</code></pre>')
                in_mermaid = False
                buffer = []
                continue
            if in_mermaid:
                buffer.append(line)
            else:
                out.append(line)
        if in_mermaid:
            out.extend(buffer)
        return out


def create_renderer(
    wiki_root: Path,
    current_wiki: str | None = None,
    wikis_map: dict[str, Path] | None = None,
    extensions: list[str] | None = None,
) -> Callable[[str], RenderedPage]:
    """Create a markdown renderer configured for wiki pages.

    wiki_root: the wiki root directory (parent of wiki/ and audit/).
    """
    if extensions is None:
        extensions = ["fenced_code", "tables", "toc", "wikilinks", "mermaid"]

    md_extensions: list[Extension | str] = []
    for ext in extensions:
        if ext == "wikilinks":
            md_extensions.append(_WikilinksExtension(wiki_root, current_wiki, wikis_map))
        elif ext == "mermaid":
            md_extensions.append(_MermaidPreserveExtension())
        else:
            md_extensions.append(ext)

    def render(raw_markdown: str) -> RenderedPage:
        frontmatter, body, title = _strip_frontmatter(raw_markdown)
        md = Markdown(extensions=md_extensions)
        html = md.convert(body)
        return RenderedPage(
            html=html,
            frontmatter=frontmatter,
            raw_markdown=raw_markdown,
            title=title,
        )

    return render


# ── Audit helpers ───────────────────────────────────────────────────────────


def load_audits(
    wiki_root: Path,
    target: str | None = None,
    mode: str = "open",
) -> list[AuditEntry]:
    """Load audit entries from audit/ and audit/resolved/ directories."""
    entries: list[AuditEntry] = []
    dirs: list[Path] = []
    if mode in ("open", "all"):
        dirs.append(wiki_root / "audit")
    if mode in ("resolved", "all"):
        dirs.append(wiki_root / "audit" / "resolved")

    for dir_path in dirs:
        if not dir_path.exists():
            continue
        for f in dir_path.iterdir():
            if not f.name.endswith(".md") or not f.is_file():
                continue
            try:
                text = f.read_text("utf-8")
                entry = from_markdown(text)
                if target and entry.target != target:
                    continue
                entries.append(entry)
            except Exception as err:
                import logging

                logging.getLogger("wiki").warning("skipping malformed audit %s: %s", f, err)

    entries.sort(key=lambda e: e.created)
    return entries


def create_audit(
    wiki_root: Path,
    target: str,
    raw_markdown: str,
    sel_start: int,
    sel_end: int,
    comment: str,
    severity: str,
    author: str,
) -> dict[str, Any]:
    """Create a new audit entry under wiki_root / audit/."""
    pages_dir = wiki_root / "wiki"
    target_full = (pages_dir / target).resolve()
    try:
        target_full.relative_to(pages_dir.resolve())
    except ValueError:
        raise FileNotFoundError(f"target file not found: {target}") from None
    if not target_full.is_file():
        raise FileNotFoundError(f"target file not found: {target}")

    anchor = compute_anchor(raw_markdown, sel_start, sel_end)
    audit_id = make_id()
    slug = " ".join(comment.strip().split()[:5])
    filename = filename_for(audit_id, slug)
    audit_dir = wiki_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    out_path = audit_dir / filename

    entry = AuditEntry(
        id=audit_id,
        target=target,
        target_lines=anchor.target_lines,
        anchor_before=anchor.anchor_before,
        anchor_text=anchor.anchor_text,
        anchor_after=anchor.anchor_after,
        severity=severity,
        author=author or "anonymous",
        source="web-viewer",
        created=datetime.now().isoformat(),
        status="open",
        body=f"# Comment\n\n{comment.strip()}\n\n# Resolution\n\n<!-- filled in when the audit is processed -->\n",
    )
    atomic_write(out_path, to_markdown(entry))
    return {
        "id": audit_id,
        "filename": filename,
        "path": out_path.relative_to(wiki_root).as_posix(),
    }


def resolve_audit(wiki_root: Path, audit_id: str, resolution: str | None = None) -> dict[str, Any]:
    """Resolve an audit by moving it to audit/resolved/."""
    if not re.match(r"^\d{8}-\d{6}-[0-9a-f]{4}$", audit_id):
        raise ValueError("invalid audit id")

    open_dir = wiki_root / "audit"
    resolved_dir = wiki_root / "audit" / "resolved"
    resolved_dir.mkdir(parents=True, exist_ok=True)

    candidate = None
    for f in open_dir.iterdir():
        if f.name.startswith(audit_id):
            candidate = f
            break
    if not candidate:
        raise FileNotFoundError("no open audit with that id")

    open_path = candidate
    text = open_path.read_text("utf-8")
    entry = from_markdown(text)

    today = datetime.now().isoformat()[:10]
    res_text = (resolution or "").strip() or "(no details)"
    new_body = _replace_resolution(
        entry.body,
        f"{today} · accepted.\n{res_text}\n",
    )
    entry.status = "resolved"
    entry.body = new_body

    resolved_path = resolved_dir / candidate.name
    # Prima la copia risolta, poi la cancellazione dell'aperta: se il processo
    # muore in mezzo resta un duplicato (recuperabile), non un audit perso.
    atomic_write(resolved_path, to_markdown(entry))
    open_path.unlink()
    # Path relativi a wiki_root: non esporre il layout assoluto dell'host al
    # client (coerente con create_audit).
    return {
        "id": audit_id,
        "from": open_path.relative_to(wiki_root).as_posix(),
        "to": resolved_path.relative_to(wiki_root).as_posix(),
    }


def _replace_resolution(body: str, new_block: str) -> str:
    if re.search(r"# Resolution[\s\S]*$", body):
        return re.sub(r"# Resolution[\s\S]*$", f"# Resolution\n\n{new_block}", body)
    return f"{body.rstrip()}\n\n# Resolution\n\n{new_block}"


# ── Public API for HTTP handlers (compatibility) ────────────────────────────


def list_audits(
    wiki_root: Path,
    *,
    target: str | None = None,
    mode: str = "all",
) -> list[dict[str, Any]]:
    """List audit entries, optionally filtered by target and mode.

    Returns list[dict] for HTTP JSON serialization compatibility.
    """
    entries = load_audits(wiki_root, target, mode)
    return [
        {
            "id": e.id,
            "target": e.target,
            "target_lines": list(e.target_lines),
            "anchor_before": e.anchor_before,
            "anchor_text": e.anchor_text,
            "anchor_after": e.anchor_after,
            "severity": e.severity,
            "author": e.author,
            "created": e.created,
            "status": e.status,
            "body": e.body,
        }
        for e in entries
    ]



