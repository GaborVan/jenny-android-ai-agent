"""Test di regressione per lo script `lint_wiki.py` della skill llm-wiki.

Copre le correzioni introdotte dopo aver osservato due run reali della skill in
cui il linter (a) non capiva le liste YAML a blocco in frontmatter e (b) non
verificava alcune precondizioni del "Definition of done":

- `parse_frontmatter`: sintassi lista inline (`[a, b]`) E a blocco (`- a`).
- Pass 9: `sources:` non-vuoto su concept/entity + risoluzione in `raw/`.
- Pass 10: cross-link oltre `index.md`.
- Pass 11: ogni fonte in `raw/{articles,papers,notes}` ha un summary.

Gli script della skill non fanno parte del package `jenny` importabile, quindi
la dir `scripts/` viene aggiunta a `sys.path`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "jenny"
    / "skills"
    / "llm-wiki"
    / "scripts"
)


@pytest.fixture(scope="module")
def lint_wiki():
    """Carica `lint_wiki.py` come modulo (la dir non è un package importabile)."""
    sys.path.insert(0, str(_SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "lint_wiki", _SCRIPTS_DIR / "lint_wiki.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── parse_frontmatter ────────────────────────────────────────────────────────


def test_parse_frontmatter_inline_list(lint_wiki):
    fm = lint_wiki.parse_frontmatter("---\ntitle: X\nsources: [a, b]\ntags: [t1]\n---\n")
    assert fm["sources"] == ["a", "b"]
    assert fm["tags"] == ["t1"]


def test_parse_frontmatter_block_list(lint_wiki):
    """La regressione principale: stile YAML a blocco per `sources:`."""
    text = (
        "---\n"
        'title: "Pagina"\n'
        "sources:\n"
        "  - raw/articles/x.md\n"
        "  - raw/articles/y.md\n"
        "tags: [t1]\n"
        "---\n"
    )
    fm = lint_wiki.parse_frontmatter(text)
    assert fm["sources"] == ["raw/articles/x.md", "raw/articles/y.md"]
    # la chiave dopo la lista a blocco deve continuare a essere letta
    assert fm["tags"] == ["t1"]


def test_parse_frontmatter_empty_key_stays_scalar(lint_wiki):
    """Una chiave vuota senza item a blocco resta stringa vuota (comportamento invariato)."""
    fm = lint_wiki.parse_frontmatter("---\ntitle: X\nsources:\ntags: [t1]\n---\n")
    assert fm["sources"] == ""
    assert fm["tags"] == ["t1"]


# ── helper per costruire una wiki minima ─────────────────────────────────────


def _make_wiki(root: Path, *, sources_style: str = "block", with_summary: bool = True,
               cross_link: bool = True) -> None:
    """Crea una wiki minima e ben formata sotto `root`.

    Parametrizzabile per innescare i singoli check negativi.
    """
    (root / "raw" / "articles").mkdir(parents=True)
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "wiki" / "summaries").mkdir(parents=True)
    (root / "log").mkdir()

    (root / "raw" / "articles" / "fonte.md").write_text("# Fonte\ntesto", encoding="utf-8")

    if sources_style == "block":
        src_fm = "sources:\n  - raw/articles/fonte.md\n"
    elif sources_style == "inline":
        src_fm = "sources: [raw/articles/fonte.md]\n"
    else:  # "missing"
        src_fm = ""

    concept_link = "\n\n## Correlati\n- [[entities/entita|Entità]]\n" if cross_link else ""
    entity_link = "\n\n[[concepts/concetto|Concetto]]" if cross_link else ""
    (root / "wiki" / "concepts" / "concetto.md").write_text(
        f"---\ntitle: Concetto\ntype: concept\n{src_fm}tags: [t]\n---\n\n# Concetto{concept_link}",
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "entita.md").write_text(
        f"---\ntitle: Entità\ntype: entity\n{src_fm}tags: [t]\n---\n\n# Entità{entity_link}",
        encoding="utf-8",
    )

    if with_summary:
        (root / "wiki" / "summaries" / "fonte.md").write_text(
            "---\ntitle: summaries/fonte\ntype: summary\nsource_url: http://x\n---\n\n# Fonte",
            encoding="utf-8",
        )

    # index.md elenca tutte le pagine (usando gli slug con path relativo)
    (root / "wiki" / "index.md").write_text(
        "# Index\n\n## Concepts\n- [[concepts/concetto|Concetto]]\n\n"
        "## Entities\n- [[entities/entita|Entità]]\n\n"
        "## Summaries\n- [[summaries/fonte|Fonte]]\n",
        encoding="utf-8",
    )
    (root / "log" / "20260101.md").write_text(
        "# 2026-01-01\n\n## [10:00] ingest | fonte — prova (touched 2 pages)\n",
        encoding="utf-8",
    )


# ── lint() end-to-end ────────────────────────────────────────────────────────


def test_lint_clean_wiki_block_sources(lint_wiki, tmp_path, capsys):
    """Una wiki ben formata con `sources:` a blocco non deve produrre falsi positivi."""
    _make_wiki(tmp_path, sources_style="block")
    rc = lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "no issues found" in out


def test_lint_clean_wiki_inline_sources(lint_wiki, tmp_path, capsys):
    _make_wiki(tmp_path, sources_style="inline")
    rc = lint_wiki.lint(str(tmp_path))
    assert rc == 0, capsys.readouterr().out


def test_lint_flags_missing_sources(lint_wiki, tmp_path, capsys):
    _make_wiki(tmp_path, sources_style="missing")
    rc = lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "non-empty `sources:` frontmatter" in out


def test_lint_flags_missing_summary(lint_wiki, tmp_path, capsys):
    _make_wiki(tmp_path, with_summary=False)
    rc = lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "without a wiki/summaries/ page" in out


def test_lint_flags_isolated_pages(lint_wiki, tmp_path, capsys):
    _make_wiki(tmp_path, cross_link=False)
    rc = lint_wiki.lint(str(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "not cross-linked beyond index.md" in out
