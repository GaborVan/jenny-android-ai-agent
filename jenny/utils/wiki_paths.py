"""Discovery, frontmatter e fingerprint delle wiki del workspace.

Layer neutro, senza dipendenze su ``webui/`` o ``agent/``: entrambi devono
sapere dove vivono le wiki e come leggerne l'intestazione, e farlo importare
all'uno dall'altro sarebbe un'inversione di layer. ``webui/wiki.py`` re-importa
questi nomi in cima al modulo, così la sua API pubblica (``discover_wikis``) e i
suoi helper privati restano dove i chiamanti li hanno sempre trovati.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?")

# Sottocartelle della radice di una wiki escluse dal fingerprint: ``log/`` e
# ``audit/`` sono giornale di bordo, non materiale da cui si compila una
# rubrica. Cambiano a ogni operazione sulla wiki — un lint, un audit risolto —
# e includerle vorrebbe dire far ripartire Atlas ogni volta senza che il
# contenuto rilevante sia cambiato di una riga.
_FINGERPRINT_SKIP_DIRS = frozenset({"log", "audit"})

_WIKI_INDEX_FILENAME = "_index.md"
# Il file di istruzioni di una wiki, **in ordine di precedenza**. Le wiki nuove
# nascono con ``AGENTS.md`` — che e' anche il nome che ``ContextBuilder`` cerca
# per i file di bootstrap — ma le sette che esistevano prima hanno un
# ``CLAUDE.md`` scritto a mano, e finche' il passo 7 non le rinomina vanno
# lette dov'e'. Tutti e due presenti: vince il primo, e chi legge lo dice.
_WIKI_SCHEMA_FILENAMES = ("AGENTS.md", "CLAUDE.md")


def wiki_schema_file(wiki_root: Path) -> Path | None:
    """Il file di istruzioni di una wiki, o ``None`` se non ne ha nessuno."""
    for name in _WIKI_SCHEMA_FILENAMES:
        candidate = wiki_root / name
        if candidate.is_file():
            return candidate
    return None


# ── Frontmatter ──────────────────────────────────────────────────────────────


def extract_title(text: str) -> str | None:
    """Titolo di una pagina: ``title:`` nel frontmatter, altrimenti il primo H1."""
    fm = _FRONTMATTER_RE.match(text)
    if fm:
        t = re.search(r"^title:\s*(.+)$", fm.group(1), re.M)
        if t:
            return t.group(1).strip().strip('"').strip("'")
    h1 = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return h1.group(1) if h1 else None


def split_frontmatter(text: str) -> tuple[str, str]:
    """``(testo grezzo del frontmatter, corpo)`` senza parsare lo YAML.

    Serve a chi deve solo *guardare* il frontmatter — l'indice full-text ne
    pesca i tag — e non può permettersi un ``yaml.safe_load`` per pagina su
    centinaia di file. Senza frontmatter il primo elemento è la stringa vuota.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end() :]


def strip_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return ``(frontmatter, body, title)`` for a page's raw markdown."""
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


def is_wiki_root(path: Path) -> bool:
    """Vero se ``path`` e' la radice di una wiki, cioe' contiene ``wiki/``.

    E' la definizione che il picker usa gia' via :func:`discover_wikis`, estratta
    perche' ora la chiede anche il prompt: ``agent/project.md`` si rende solo
    quando la cartella del turno e' una wiki, e la stessa domanda gliela fa il
    subagent (che ha la cartella ma non la chiave di sessione). Un secondo modo
    di dire "questa e' una wiki" sarebbe un secondo modo di sbagliarlo.
    """
    return (path / "wiki").is_dir()


def discover_wikis(wikis_dir: Path) -> dict[str, Path]:
    """Scan wikis_dir for subdirectories containing a wiki/ folder.

    Returns {name: wikis_dir/name/wiki} sorted alphabetically by name.
    """
    if not wikis_dir.exists():
        return {}
    result: dict[str, Path] = {}
    for child in sorted(wikis_dir.iterdir()):
        if child.is_dir() and is_wiki_root(child):
            result[child.name] = child / "wiki"
    return result


def discover_wiki_roots(wikis_dir: Path) -> dict[str, Path]:
    """Come :func:`discover_wikis`, ma restituisce la *radice* di ogni wiki.

    ``discover_wikis`` punta alla pages-dir ``wikis/<name>/wiki``; chi deve
    leggere ``CLAUDE.md`` o contare gli audit ha bisogno del livello sopra.
    """
    return {name: pages.parent for name, pages in discover_wikis(wikis_dir).items()}


def read_wiki_scope(wiki_root: Path) -> str:
    """Riga di scope di una wiki, nello stesso ordine di priorità del registry.

    1. ``summary:`` (o ``scope:``) nel frontmatter del file di istruzioni
       (``AGENTS.md``, o ``CLAUDE.md`` nelle wiki più vecchie).
    2. Primo bullet reale sotto "What this wiki covers" nella sezione ``## Scope``.
    3. Un fallback neutro, così l'output resta deterministico.

    I placeholder del template (``<...>``) sono ignorati a ogni livello. La
    logica è la stessa di ``skills/llm-wiki/scripts/reindex_wikis.py``, ma non la
    importiamo: quello script è un checkout della skill, destinato a essere
    copiato nel workspace e modificato dall'utente, non una libreria del package.
    """
    schema = wiki_schema_file(wiki_root)
    if schema is None:
        return "(no AGENTS.md)"
    try:
        text = schema.read_text(encoding="utf-8")
    except OSError:
        return f"(unreadable {schema.name})"

    explicit = _frontmatter_scalar(text, "summary", "scope")
    if explicit:
        return explicit

    in_scope = False
    in_covers = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_scope = stripped[3:].strip().lower() == "scope"
            in_covers = False
            continue
        if not in_scope:
            continue
        if stripped.lower().startswith("what this wiki covers"):
            in_covers = True
            continue
        if in_covers and stripped.startswith("- "):
            bullet = stripped[2:].strip()
            return "(no scope set)" if _is_placeholder(bullet) else bullet
        if in_covers and stripped.lower().startswith("what this wiki"):
            break  # sezione "excludes" raggiunta senza un bullet reale
    return "(no scope set)"


def _is_placeholder(value: str) -> bool:
    return "<" in value and ">" in value


def _frontmatter_scalar(text: str, *keys: str) -> str | None:
    """Primo scalare non-placeholder fra *keys* nel frontmatter YAML iniziale."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    for line in m.group(1).split("\n"):
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, val = line.partition(":")
        if key.strip() in keys:
            v = val.strip().strip('"').strip("'")
            if v and not _is_placeholder(v):
                return v
    return None


# ── Fingerprint ──────────────────────────────────────────────────────────────


def iter_wiki_sources(wikis_dir: Path) -> Iterator[Path]:
    """I ``.md`` che definiscono il contenuto delle wiki, in ordine stabile.

    Sono il registry ``_index.md``, il file di istruzioni di ogni wiki
    (``AGENTS.md`` o ``CLAUDE.md``) e tutto ciò che sta sotto la sua ``wiki/``. Fuori restano ``log/``, ``audit/`` e i
    file nascosti (vedi :data:`_FINGERPRINT_SKIP_DIRS`).
    """
    if not wikis_dir.is_dir():
        return
    index = wikis_dir / _WIKI_INDEX_FILENAME
    if index.is_file():
        yield index
    for name, root in discover_wiki_roots(wikis_dir).items():
        # Senza il ripiego, una wiki che tiene le istruzioni in ``AGENTS.md``
        # resta fuori dall'impronta: la modifichi e Atlas non se ne accorge mai.
        schema = wiki_schema_file(root)
        if schema is not None:
            yield schema
        pages = root / "wiki"
        for path in sorted(pages.rglob("*.md")):
            if path.name.startswith("."):
                continue
            rel_parts = path.relative_to(pages).parts
            if rel_parts and rel_parts[0] in _FINGERPRINT_SKIP_DIRS:
                continue
            yield path


def wiki_fingerprint(wikis_dir: Path, extra_paths: Iterable[Path] = ()) -> str:
    """Impronta del contenuto wiki: sha256 di ``(path, mtime_ns, size)``.

    Serve a rispondere a una sola domanda — "è cambiato qualcosa dall'ultimo
    run?" — senza leggere i file. *extra_paths* porta dentro input che stanno
    fuori da ``wikis/`` ma che cambiano il risultato, tipicamente il file di
    policy dell'utente: se cambiano i criteri, la rubrica va ricompilata anche
    a wiki ferma.

    Un file assente contribuisce comunque (con marcatore ``-``), così la sua
    comparsa o sparizione muove l'impronta.
    """
    digest = hashlib.sha256()
    for path in iter_wiki_sources(wikis_dir):
        digest.update(_stat_line(path, path.relative_to(wikis_dir).as_posix()))
    for path in sorted(set(extra_paths)):
        digest.update(_stat_line(path, str(path)))
    return digest.hexdigest()


def _stat_line(path: Path, label: str) -> bytes:
    try:
        st = path.stat()
        marker = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        marker = "-"
    return f"{label}\x00{marker}\n".encode()


def has_wikis(wikis_dir: Path) -> bool:
    """True se esiste almeno una wiki scansionabile."""
    return bool(discover_wikis(wikis_dir))
