#!/usr/bin/env python3
"""
lint_wiki.py — Health check for an LLM Wiki.

Usage:
    python3 lint_wiki.py <wiki-root>
    python3 lint_wiki.py --workspace <wikis-dir> [--fix]

Examples:
    python3 lint_wiki.py workspace/wikis/ai-research
    python3 lint_wiki.py --workspace workspace/wikis
    python3 lint_wiki.py --workspace workspace/wikis --fix   # repair registry drift

Per-wiki checks:
  1. Dead wikilinks — [[Target]] where Target.md doesn't exist. Wikis are
     isolated, so a link to another wiki's page is (correctly) a dead link.
  2. Orphan pages — wiki pages with no inbound links
  3. Missing index entries — wiki pages not listed in wiki/index.md
  4. Unlinked concepts — terms mentioned 3+ times but lacking their own page
  5. log/ shape — every file matches YYYYMMDD.md and has the right H1
  6. audit/ shape — every audit/*.md parses as a valid AuditEntry (incl. unique
     id and filename-timestamp match)
  7. Audit targets — every open audit's `target` file must exist
  8. Duplicate pages — pages whose titles normalize to the same key (case,
     punctuation, word order, stop-words) are flagged as likely duplicates
  9. Source integrity — every concept/entity page must have a non-empty
     `sources:` frontmatter (precondition, not just resolution), and every
     cited slug must resolve to a file under raw/
 10. Cross-link coverage — every concept/entity page must have at least one
     outbound wikilink, or an inbound wikilink from a page other than
     index.md — being listed in index.md alone is not cross-linking
 11. Summary completeness — every ingested text source (raw/articles,
     raw/papers, raw/notes) must have a matching wiki/summaries/<slug>.md

Checks 9-11 are the research pattern's, and they only fire where its folders
exist. Two layouts live in the world and no flag tells them apart: the structure
on disk is the declaration (see `is_research_layout`).

Every wiki, whatever its layout:
 12. Journal shape and integrity — raw/journal/YYYYMMDD.md, lines as
     `- HH:MM — text`, and **append-only verified against the previous lint**:
     a file that shrank, or whose already-written head changed, is reported. The
     journal is the only record of what was said, so a line that changes leaves
     behind a page nothing supports.
 15. Map size — wiki/index.md is injected into every turn of every conversation
     in that project, and past a ceiling the rest is not injected at all.

Notebook layout only (flat pages, no concepts/entities/summaries):
 13. Page state — every page declares `state:` from a closed vocabulary. A page
     is worth exactly what its state says.
 14. Cross-linking — a page with no link in or out is a note in a folder. Being
     listed in the map is not a link.

State: check 12 keeps one digest per journal file under <wiki>/.jenny/, which is
the only way to answer "did a line change since last time". It is machinery, not
the user's material — same place and same reason as the gardener's cursor.

Workspace checks (--workspace): lints every wiki under <wikis-dir>, then:
  8. wikis/_index.md exists and its wiki-registry block is in sync with the
     wikis on disk (via reindex_wikis.check_index). Add --fix to repair drift.

Exit codes:
  0 — no issues found
  1 — issues found (printed to stdout)
"""

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reindex_wikis  # noqa: E402  (sibling script, same scripts/ dir)

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
LOG_FILENAME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.md$")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
AUDIT_TS_RE = re.compile(r"^(\d{8}-\d{6})")  # YYYYMMDD-HHMMSS prefix

# Required audit frontmatter fields
AUDIT_REQUIRED_FIELDS = {
    "id", "target", "target_lines", "anchor_before", "anchor_text",
    "anchor_after", "severity", "author", "source", "created", "status",
}
VALID_SEVERITIES = {"info", "suggest", "warn", "error"}
VALID_STATUSES = {"open", "resolved"}

# Canonical op names for log/ entries (SKILL.md § log/ format).
VALID_LOG_OPS = {"compile", "ingest", "query", "lint", "audit", "promote", "split", "scaffold"}
LOG_ENTRY_RE = re.compile(r"^## \[\d{2}:\d{2}\] (\S+)")

# Stop-words dropped when comparing page titles for near-duplicates.
TITLE_STOPWORDS = {"a", "an", "the", "of", "and", "to", "for", "in", "on", "vs", "with"}

# ── Il taccuino: quel che il formato nuovo aggiunge ─────────────────────────
#
# Due layout esistono nel mondo e **nessun flag li distingue**: la struttura su
# disco è la dichiarazione. Una wiki di ricerca ha ``concepts/``/``entities/``/
# ``summaries/`` sotto ``wiki/``; un taccuino ha pagine piatte. I controlli del
# pattern di ricerca (passi 9-11) sono già ristretti a ``concepts``/``entities``
# e su un taccuino non scattano; questi sono il loro specchio.

RESEARCH_SUBDIRS = ("concepts", "entities", "summaries")

# Il vocabolario di ``state:``. Una pagina vale quanto il suo stato dice, ed è
# l'anticorpo alla deriva auto-confermante: senza stato, un'ipotesi appuntata di
# passaggio si rilegge fra un mese come un fatto stabilito.
PAGE_STATES = {"open", "hypothesis", "decided", "done"}

# Tetto della mappa, in caratteri. **Non è un numero scelto qui**: è la soglia
# oltre la quale il blocco di progetto smette di iniettare la mappa intera in
# ogni turno (``jenny/agent/context.py::_PROJECT_MAP_MAX_CHARS``). Oltre, il
# resto della mappa esiste ma l'agente non lo vede senza aprire il file — quindi
# è un avviso che vale per **tutti** i layout, perché la mappa la riceve ogni
# progetto.
MAP_MAX_CHARS = 2000

JOURNAL_FILENAME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.md$")
JOURNAL_ENTRY_RE = re.compile(r"^- \d{2}:\d{2} \u2014 \S")

# Lo stato del lint, dentro la cartella nascosta del progetto: macchinario, non
# materiale dell'utente — come il cursore del giardiniere, e per la stessa
# ragione sta fuori da ``wiki/`` (viste, grafo e impronta di Atlas non lo vedono).
LINT_STATE_REL = ".jenny/lint_journal.json"


def is_research_layout(wiki_path: Path) -> bool:
    """Se questa wiki è una biblioteca di ricerca, letto dalla struttura."""
    return any((wiki_path / name).is_dir() for name in RESEARCH_SUBDIRS)


def journal_files(root_path: Path) -> list[Path]:
    journal = root_path / "raw" / "journal"
    return sorted(journal.glob("*.md")) if journal.is_dir() else []


def head_digest(path: Path, size: int) -> str | None:
    """Digest dei primi *size* byte di *path*, o ``None`` se illeggibile.

    È il pezzo che rende esatto il controllo dell'append-only: la testa di oggi
    deve essere identica al file di ieri, byte per byte.
    """
    try:
        with path.open("rb") as fh:
            return hashlib.sha256(fh.read(size)).hexdigest()
    except OSError:
        return None


def read_lint_state(root_path: Path) -> dict:
    try:
        data = json.loads((root_path / LINT_STATE_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("digests", {}) if isinstance(data, dict) else {}


def write_lint_state(root_path: Path, digests: dict) -> None:
    path = root_path / LINT_STATE_REL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "digests": digests}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass  # il lint riporta, non deve fallire per non aver salvato lo stato


def dup_key(title: str) -> str:
    """A normalized identity for a page title: lowercased alphanumeric tokens,
    stop-words removed, sorted. Two titles with the same key differ only by case,
    punctuation, separators, word order, or stop-words — i.e. likely duplicates."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in TITLE_STOPWORDS]
    return " ".join(sorted(tokens))


def page_title(path: Path) -> str:
    """The page's title: frontmatter `title:`, else first H1, else filename stem."""
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm and str(fm.get("title", "")).strip():
        return str(fm["title"]).strip()
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def load_pages(wiki_dir: Path) -> dict[str, Path]:
    pages: dict[str, Path] = {}
    for p in wiki_dir.rglob("*.md"):
        pages[p.stem] = p
        rel = p.relative_to(wiki_dir)
        pages[str(rel.with_suffix(""))] = p
    return pages


def extract_wikilinks(text: str) -> list[str]:
    return WIKILINK_RE.findall(text)


def parse_frontmatter(text: str) -> dict | None:
    """Minimal YAML-ish frontmatter parser. Handles the flat key:value fields
    and one-level lists/arrays actually used by audit files. Does not handle
    arbitrary YAML — intentional, to avoid a pyyaml dependency."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    body = m.group(1)
    result: dict = {}
    # Track multi-line folded strings via simple heuristic: quoted scalars
    # can contain \n; unquoted values are single-line.
    i = 0
    lines = body.split("\n")
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        val = rest.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                parts = [p.strip() for p in inner.split(",")]
                parsed: list = []
                for p in parts:
                    if p.isdigit() or (p.startswith("-") and p[1:].isdigit()):
                        parsed.append(int(p))
                    else:
                        parsed.append(p.strip('"').strip("'"))
                result[key] = parsed
        elif val.startswith('"') and val.endswith('"'):
            result[key] = val[1:-1].replace("\\n", "\n").replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'"):
            result[key] = val[1:-1]
        elif val == "":
            # Possibly a block-style list:
            #   sources:
            #     - raw/articles/x.md
            #     - raw/articles/y.md
            # Consume the following indented `- item` lines. If none follow,
            # this stays an empty-string scalar (unchanged behaviour).
            block: list = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                stripped = nxt.strip()
                if not stripped:
                    j += 1
                    continue
                # A block item must be indented and start with a dash.
                if nxt[0] in (" ", "\t") and stripped.startswith("- "):
                    item = stripped[2:].strip().strip('"').strip("'")
                    block.append(item)
                    j += 1
                else:
                    break
            if block:
                result[key] = block
                i = j
                continue
            result[key] = val
        else:
            result[key] = val
        i += 1
    return result


def lint(root: str) -> int:
    root_path = Path(root)
    wiki_path = root_path / "wiki"
    log_path = root_path / "log"
    audit_path = root_path / "audit"

    if not wiki_path.exists():
        print(f"ERROR: wiki/ directory not found at {wiki_path}", file=sys.stderr)
        return 1

    pages = load_pages(wiki_path)
    all_wiki_files = list(wiki_path.rglob("*.md"))
    index_path = wiki_path / "index.md"

    issues = 0
    inbound: dict[str, list[str]] = defaultdict(list)

    # ── Pass 1: dead wikilinks ──────────────────────────────────────────────
    dead_links: list[tuple[str, str]] = []
    for md_file in all_wiki_files:
        text = md_file.read_text(encoding="utf-8")
        for link in extract_wikilinks(text):
            link = link.strip()
            if link not in pages and Path(link).stem not in pages:
                dead_links.append((str(md_file.relative_to(root_path)), link))
            else:
                target = pages.get(link) or pages.get(Path(link).stem)
                if target:
                    inbound[target.stem].append(md_file.stem)

    if dead_links:
        print(f"\n🔴 Dead wikilinks ({len(dead_links)}):")
        for source, link in dead_links:
            print(f"   {source} → [[{link}]]")
        print("   (wikis are isolated — a link to another wiki's page is dead;")
        print("    reference other wikis through wikis/_index.md instead)")
        issues += len(dead_links)
    else:
        print("✅ No dead wikilinks")

    # ── Pass 2: orphan pages ────────────────────────────────────────────────
    skip_orphan = {"index"}
    orphans = [
        p for p in all_wiki_files
        if p.stem not in inbound and p.stem not in skip_orphan
        and p.parent != wiki_path  # skip index.md at root
    ]
    if orphans:
        print(f"\n🟡 Orphan pages ({len(orphans)}) — no inbound wikilinks:")
        for p in orphans:
            print(f"   {p.relative_to(root_path)}")
        issues += len(orphans)
    else:
        print("✅ No orphan pages")

    # ── Pass 3: missing index entries ───────────────────────────────────────
    if index_path.exists():
        index_text = index_path.read_text(encoding="utf-8")
        not_in_index = [
            p for p in all_wiki_files
            if p != index_path
            and f"[[{p.stem}]]" not in index_text
            and str(p.relative_to(wiki_path).with_suffix("")) not in index_text
        ]
        if not_in_index:
            print(f"\n🟡 Pages missing from index.md ({len(not_in_index)}):")
            for p in not_in_index:
                print(f"   {p.relative_to(root_path)}")
            issues += len(not_in_index)
        else:
            print("✅ All pages in index.md")
    else:
        print("⚠️  wiki/index.md not found — skipping index check")

    # ── Pass 4: unlinked concepts ───────────────────────────────────────────
    all_text = " ".join(p.read_text(encoding="utf-8") for p in all_wiki_files)
    all_links = WIKILINK_RE.findall(all_text)
    link_counts: dict[str, int] = defaultdict(int)
    for link in all_links:
        link_counts[link.strip()] += 1

    missing_pages = [
        (link, count) for link, count in link_counts.items()
        if count >= 3 and link not in pages and Path(link).stem not in pages
    ]
    if missing_pages:
        print(f"\n🟡 Frequently linked but no page ({len(missing_pages)}):")
        for link, count in sorted(missing_pages, key=lambda x: -x[1]):
            print(f"   [[{link}]] — mentioned {count}x")
        issues += len(missing_pages)
    else:
        print("✅ No frequently-linked missing pages")

    # ── Pass 5: log/ shape ───────────────────────────────────────────────────
    if log_path.exists() and log_path.is_dir():
        log_issues: list[str] = []
        for p in sorted(log_path.iterdir()):
            if p.is_dir():
                continue
            if p.name == ".gitkeep":
                continue
            m = LOG_FILENAME_RE.match(p.name)
            if not m:
                log_issues.append(f"   {p.relative_to(root_path)} — filename doesn't match YYYYMMDD.md")
                continue
            y, mo, d = m.groups()
            iso = f"{y}-{mo}-{d}"
            lines = p.read_text(encoding="utf-8").splitlines()
            first_line = lines[:1]
            if not first_line or first_line[0].strip() != f"# {iso}":
                log_issues.append(f"   {p.relative_to(root_path)} — expected H1 '# {iso}'")
            for line in lines:
                entry_m = LOG_ENTRY_RE.match(line)
                if entry_m and entry_m.group(1) not in VALID_LOG_OPS:
                    log_issues.append(
                        f"   {p.relative_to(root_path)} — unknown op '{entry_m.group(1)}' "
                        f"(expected one of {sorted(VALID_LOG_OPS)})"
                    )
        if log_issues:
            print(f"\n🟡 log/ shape issues ({len(log_issues)}):")
            for s in log_issues:
                print(s)
            issues += len(log_issues)
        else:
            print("✅ log/ shape OK")
    else:
        print("⚠️  log/ directory not found — skipping log shape check")

    # ── Pass 6: audit/ shape ─────────────────────────────────────────────────
    audit_targets_to_check: list[tuple[str, str]] = []  # (audit_id, target)
    if audit_path.exists() and audit_path.is_dir():
        audit_files = [
            p for p in audit_path.rglob("*.md") if p.name != ".gitkeep"
        ]
        audit_issues: list[str] = []
        seen_ids: dict[str, str] = {}
        for p in audit_files:
            text = p.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            rel = p.relative_to(root_path)
            if fm is None:
                audit_issues.append(f"   {rel} — missing YAML frontmatter")
                continue
            missing = AUDIT_REQUIRED_FIELDS - set(fm.keys())
            if missing:
                audit_issues.append(
                    f"   {rel} — missing fields: {', '.join(sorted(missing))}"
                )
                continue
            if fm["severity"] not in VALID_SEVERITIES:
                audit_issues.append(
                    f"   {rel} — invalid severity '{fm['severity']}' (expected {sorted(VALID_SEVERITIES)})"
                )
            if not str(fm["source"]).strip():
                audit_issues.append(f"   {rel} — empty source field")
            # id must be unique and its timestamp prefix must match the filename.
            audit_id = str(fm["id"])
            if audit_id in seen_ids:
                audit_issues.append(
                    f"   {rel} — duplicate id '{audit_id}' (also in {seen_ids[audit_id]})"
                )
            else:
                seen_ids[audit_id] = str(rel)
            id_ts = AUDIT_TS_RE.match(audit_id)
            name_ts = AUDIT_TS_RE.match(p.name)
            if id_ts and name_ts and id_ts.group(1) != name_ts.group(1):
                audit_issues.append(
                    f"   {rel} — filename timestamp doesn't match id '{audit_id}'"
                )
            expected_status = "resolved" if "resolved" in p.parts else "open"
            if fm["status"] != expected_status:
                audit_issues.append(
                    f"   {rel} — status '{fm['status']}' doesn't match directory (expected '{expected_status}')"
                )
            if fm["status"] == "open":
                audit_targets_to_check.append((fm["id"], fm["target"]))

        if audit_issues:
            print(f"\n🔴 audit/ shape issues ({len(audit_issues)}):")
            for s in audit_issues:
                print(s)
            issues += len(audit_issues)
        else:
            print(f"✅ audit/ shape OK ({len(audit_files)} files)")
    else:
        print("⚠️  audit/ directory not found — skipping audit shape check")

    # ── Pass 7: audit targets exist ──────────────────────────────────────────
    missing_targets: list[tuple[str, str]] = []
    for audit_id, target in audit_targets_to_check:
        target_path = root_path / target
        # Audit target paths are relative to wiki-root but typically point
        # at files under wiki/. Check both locations.
        if not target_path.exists():
            alt = wiki_path / target
            if not alt.exists():
                missing_targets.append((audit_id, target))
    if missing_targets:
        print(f"\n🔴 Open audits with missing target files ({len(missing_targets)}):")
        for audit_id, target in missing_targets:
            print(f"   {audit_id} → {target}")
        issues += len(missing_targets)
    elif audit_targets_to_check:
        print("✅ All open-audit targets exist")

    # ── Pass 8: possible duplicate pages ─────────────────────────────────────
    dup_groups: dict[str, list[Path]] = defaultdict(list)
    for p in all_wiki_files:
        if p.stem == "index":  # section/root indexes legitimately repeat
            continue
        key = dup_key(page_title(p))
        if key:
            dup_groups[key].append(p)
    dups = {k: v for k, v in dup_groups.items() if len(v) > 1}
    if dups:
        total = sum(len(v) - 1 for v in dups.values())
        print(f"\n🟡 Possible duplicate pages ({total}) — same normalized title:")
        for key, files in sorted(dups.items()):
            print(f"   ~ '{key}':")
            for f in sorted(files):
                print(f"       {f.relative_to(root_path)}")
        issues += total
    else:
        print("✅ No duplicate-looking pages")

    # Concept/entity pages (excluding folder-split index.md hubs) are the pages
    # for which `sources:` is a documented precondition, per article-guide.md.
    sourced_pages = [
        p for p in all_wiki_files
        if p.stem != "index"
        and p.relative_to(wiki_path).parts
        and p.relative_to(wiki_path).parts[0] in ("concepts", "entities")
    ]

    # ── Pass 9: source integrity ─────────────────────────────────────────────
    raw_path = root_path / "raw"
    no_sources_field = [
        p for p in sourced_pages
        if not [
            s for s in (parse_frontmatter(p.read_text(encoding="utf-8")) or {}).get("sources", [])
            if str(s).strip()
        ]
    ]
    if no_sources_field:
        print(f"\n🔴 Concept/entity pages missing non-empty `sources:` frontmatter ({len(no_sources_field)}):")
        for p in no_sources_field:
            print(f"   {p.relative_to(root_path)}")
        print("   (a precondition of writing the page, not later cleanup — see SKILL.md § Definition of done)")
        issues += len(no_sources_field)
    else:
        print("✅ All concept/entity pages have non-empty sources: frontmatter")

    if raw_path.is_dir():
        raw_stems = {f.stem for f in raw_path.rglob("*") if f.is_file()}
        missing_sources: list[tuple[str, str]] = []
        for p in all_wiki_files:
            fm = parse_frontmatter(p.read_text(encoding="utf-8"))
            if not fm:
                continue
            srcs = fm.get("sources")
            if not isinstance(srcs, list):
                continue
            for s in srcs:
                s = str(s).strip()
                if not s or ("<" in s and ">" in s):
                    continue
                if Path(s).stem not in raw_stems:
                    missing_sources.append((str(p.relative_to(root_path)), s))
        if missing_sources:
            print(f"\n🟡 Pages citing sources not found in raw/ ({len(missing_sources)}):")
            for src_page, s in missing_sources:
                print(f"   {src_page} → sources: {s}")
            issues += len(missing_sources)
        else:
            print("✅ All cited sources resolve to raw/ files")
    else:
        print("⚠️  raw/ directory not found — skipping raw/ resolution check")

    # ── Pass 10: cross-link coverage ─────────────────────────────────────────
    outbound_count: dict[str, int] = {
        p.stem: len(extract_wikilinks(p.read_text(encoding="utf-8"))) for p in all_wiki_files
    }
    inbound_non_index: dict[str, list[str]] = defaultdict(list)
    for md_file in all_wiki_files:
        if md_file.stem == "index":
            continue
        for link in extract_wikilinks(md_file.read_text(encoding="utf-8")):
            link = link.strip()
            target = pages.get(link) or pages.get(Path(link).stem)
            if target:
                inbound_non_index[target.stem].append(md_file.stem)

    isolated_pages = [
        p for p in sourced_pages
        if outbound_count.get(p.stem, 0) == 0 and not inbound_non_index.get(p.stem)
    ]
    if isolated_pages:
        print(f"\n🟡 Concept/entity pages not cross-linked beyond index.md ({len(isolated_pages)}):")
        for p in isolated_pages:
            print(f"   {p.relative_to(root_path)}")
        print("   (add a [[...]] link to/from another concept, entity or summary page —")
        print("    being listed in index.md alone doesn't count; see SKILL.md § Definition of done)")
        issues += len(isolated_pages)
    else:
        print("✅ All concept/entity pages are cross-linked beyond index.md")

    # ── Pass 11: summary completeness ────────────────────────────────────────
    # Every ingested text source (raw/articles, raw/papers, raw/notes) must have
    # a wiki/summaries/<slug>.md. raw/refs/ are pointer files, not ingestable
    # text, so they are exempt.
    if raw_path.is_dir():
        summary_stems = {
            p.stem for p in (wiki_path / "summaries").rglob("*.md")
        } if (wiki_path / "summaries").is_dir() else set()
        ingestable_dirs = ("articles", "papers", "notes")
        raw_sources = [
            f for f in raw_path.rglob("*.md")
            if f.is_file()
            and f.relative_to(raw_path).parts
            and f.relative_to(raw_path).parts[0] in ingestable_dirs
        ]
        missing_summaries = [f for f in raw_sources if f.stem not in summary_stems]
        if missing_summaries:
            print(f"\n🔴 Raw sources without a wiki/summaries/ page ({len(missing_summaries)}):")
            for f in missing_summaries:
                print(f"   {f.relative_to(root_path)} → expected wiki/summaries/{f.stem}.md")
            print("   (step 3 of ingest — a summary per source is not optional; see SKILL.md § Definition of done)")
            issues += len(missing_summaries)
        elif raw_sources:
            print("✅ Every raw source has a summary page")

    # ── Pass 12: il diario (ogni wiki) ───────────────────────────────────────
    # Universale, perché il diario è universale: ogni wiki l'ha guadagnato, e la
    # cattura scrive lì indipendentemente dal layout delle pagine.
    journals = journal_files(root_path)
    if journals:
        bad_names = [j for j in journals if not JOURNAL_FILENAME_RE.match(j.name)]
        if bad_names:
            print(f"\n🟡 Journal files with an unexpected name ({len(bad_names)}):")
            for j in bad_names:
                print(f"   {j.relative_to(root_path)} — expected YYYYMMDD.md")
            issues += len(bad_names)

        malformed: list[tuple[str, int, str]] = []
        digests: dict[str, dict] = {}
        for j in journals:
            try:
                raw = j.read_bytes()
            except OSError:
                continue
            digests[j.relative_to(root_path).as_posix()] = {
                "sha": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                print(f"\n🔴 Journal file is not UTF-8: {j.relative_to(root_path)}")
                issues += 1
                continue
            for n, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if not JOURNAL_ENTRY_RE.match(stripped):
                    malformed.append((j.relative_to(root_path).as_posix(), n, stripped[:60]))
        if malformed:
            print(f"\n🟡 Journal lines not in `- HH:MM — text` form ({len(malformed)}):")
            for rel, n, sample in malformed[:20]:
                print(f"   {rel}:{n}  {sample}")
            print("   (one fact per line, and the timestamp is added by the tool —")
            print("    a line the gardener cannot read is a fact that never becomes a page)")
            issues += len(malformed)

        # L'append-only **verificato**, non predicato. Il diario è l'input del
        # giardiniere e la sola fonte di verità di quel che è stato detto: se una
        # riga già promossa cambia, la pagina che ne è nata resta a dire un'altra
        # cosa e nessuno se ne accorge — il file è intatto e il cursore è
        # plausibile. Confrontare col run precedente è l'unico modo di vederlo.
        #
        # Il confronto è **esatto** e costa due letture: se il file è più corto
        # di prima è stato troncato; se è più lungo o uguale, la sua *testa*
        # (i primi ``size`` byte di allora) deve avere lo stesso digest di
        # allora. Un digest sul file intero non basterebbe: non distingue
        # "cresciuto" — che è il caso normale — da "riscritto".
        previous = read_lint_state(root_path)
        violations: list[tuple[str, str]] = []
        for rel, now in sorted(digests.items()):
            before = previous.get(rel)
            if not isinstance(before, dict):
                continue
            old_size, old_sha = before.get("size"), before.get("sha")
            if not isinstance(old_size, int) or not isinstance(old_sha, str):
                continue
            if now["size"] < old_size:
                violations.append((rel, f"truncated ({old_size} → {now['size']} bytes)"))
            elif head_digest(root_path / rel, old_size) != old_sha:
                violations.append((rel, "an already-written line was changed"))
        if violations:
            print(f"\n🔴 Journal files that are no longer append-only ({len(violations)}):")
            for rel, why in violations:
                print(f"   {rel} — {why}")
            print("   (the journal is the gardener's input and the only record of what")
            print("    was said. A page promoted from a line that no longer exists now")
            print("    says something nothing supports.)")
            issues += len(violations)
        elif previous:
            print("✅ Journal is append-only since the last lint")
        else:
            print("ℹ️  Journal baseline recorded (append-only checked from the next lint)")
        write_lint_state(root_path, digests)

    # ── Pass 13-14: il formato taccuino ──────────────────────────────────────
    if not is_research_layout(wiki_path):
        flat_pages = [p for p in all_wiki_files if p != index_path]

        # ``state:`` è obbligatorio e chiuso a vocabolario. Una pagina vale
        # quanto il suo stato dice; senza, un'ipotesi appuntata di passaggio si
        # rilegge fra un mese come un fatto stabilito.
        bad_state: list[tuple[str, str]] = []
        for page in flat_pages:
            fm = parse_frontmatter(page.read_text(encoding="utf-8")) or {}
            value = str(fm.get("state", "")).strip()
            if value not in PAGE_STATES:
                bad_state.append((
                    page.relative_to(root_path).as_posix(), value or "(missing)"
                ))
        if bad_state:
            print(f"\n🔴 Pages with no valid `state:` ({len(bad_state)}):")
            for rel, value in bad_state:
                print(f"   {rel} — {value}")
            print(f"   (one of: {', '.join(sorted(PAGE_STATES))})")
            issues += len(bad_state)
        elif flat_pages:
            print("✅ Every page declares a valid state:")

        # Una pagina che non linka niente è una nota in una cartella, non una
        # voce di wiki: la stessa regola del passo 10, per le pagine piatte.
        unlinked = [
            page for page in flat_pages
            if not extract_wikilinks(page.read_text(encoding="utf-8"))
            and not inbound_non_index.get(page.stem)
        ]
        if unlinked:
            print(f"\n🟡 Pages with no link in or out ({len(unlinked)}):")
            for page in unlinked:
                print(f"   {page.relative_to(root_path)}")
            print("   (being listed in the map is not a link — that is what makes")
            print("    this a wiki instead of a folder)")
            issues += len(unlinked)

    # ── Pass 15: la mappa entra in ogni turno ────────────────────────────────
    # Vale per tutti i layout: il blocco di progetto inietta ``wiki/index.md`` in
    # ogni turno di ogni conversazione dentro quella cartella, quindi ogni riga
    # in più si paga a ogni messaggio — e oltre il tetto il resto non arriva.
    if index_path.exists():
        size = len(index_path.read_text(encoding="utf-8"))
        if size > MAP_MAX_CHARS:
            print(f"\n🟡 The map is {size} characters (over {MAP_MAX_CHARS}):")
            print(f"   {index_path.relative_to(root_path)}")
            print("   (it is injected into every turn, and past that ceiling the rest")
            print("    is not injected at all. What outgrew a few lines belongs on a page.)")
            issues += 1

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    if issues == 0:
        print("✅ Wiki is healthy — no issues found")
    else:
        print(f"⚠️  {issues} issue(s) found — review above and fix before next ingest")

    return 0 if issues == 0 else 1


def lint_workspace(wikis_dir_path: str, fix: bool = False) -> int:
    """Lint every wiki under <wikis-dir>, then check the workspace registry.

    With fix=True, registry drift is repaired in place (only the mechanical
    _index.md registry block — per-wiki issues always need human judgement)."""
    wikis_dir = reindex_wikis.resolve_wikis_dir(wikis_dir_path)
    if not wikis_dir.is_dir():
        print(f"ERROR: wikis dir not found at {wikis_dir}", file=sys.stderr)
        return 1

    wikis = reindex_wikis.discover_wikis(wikis_dir)
    if not wikis:
        print(f"ERROR: no wikis found under {wikis_dir}", file=sys.stderr)
        return 1

    failed = 0
    for name in wikis:
        print(f"\n{'='*50}\n📚 {name}\n{'='*50}")
        if lint(str(wikis_dir / name)) != 0:
            failed += 1

    # ── Workspace pass: _index.md registry sync ──────────────────────────────
    print(f"\n{'='*50}\n🗂  workspace registry (_index.md)\n{'='*50}")
    registry_problems = reindex_wikis.check_index(wikis_dir)
    if registry_problems and fix:
        print(f"🔧 registry out of sync — repairing ({len(registry_problems)}):")
        for p in registry_problems:
            print(f"   {p}")
        reindex_wikis.regenerate_index(wikis_dir)
        registry_problems = reindex_wikis.check_index(wikis_dir)
        if registry_problems:
            print("🔴 still out of sync after repair:")
            for p in registry_problems:
                print(f"   {p}")
        else:
            print("✅ registry repaired")
    elif registry_problems:
        print(f"🔴 wikis/_index.md out of sync ({len(registry_problems)}):")
        for p in registry_problems:
            print(f"   {p}")
        print("   (re-run with --fix, or: reindex_wikis.py <wikis-dir>)")
    else:
        print("✅ wikis/_index.md registry is in sync")

    print(f"\n{'─'*40}")
    total_bad = failed + (1 if registry_problems else 0)
    if total_bad == 0:
        print(f"✅ Workspace healthy — {len(wikis)} wiki(s), registry in sync")
        return 0
    print(f"⚠️  {failed} wiki(s) with issues" +
          (" + registry out of sync" if registry_problems else ""))
    return 1


if __name__ == "__main__":
    args = sys.argv[1:]
    fix = "--fix" in args
    args = [a for a in args if a != "--fix"]
    if args and args[0] == "--workspace":
        if len(args) < 2:
            print(__doc__)
            sys.exit(1)
        sys.exit(lint_workspace(args[1], fix=fix))
    if fix:
        print("--fix only applies to --workspace mode", file=sys.stderr)
        sys.exit(1)
    if not args:
        print(__doc__)
        sys.exit(1)
    sys.exit(lint(args[0]))

