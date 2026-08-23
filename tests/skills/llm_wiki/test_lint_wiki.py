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

# ── T5: le due strutture, e il diario che vale per entrambe ──────────────────
#
# Passo **T5** di ``roadmap/taccuino-passi.md``. Due layout esistono nel mondo e
# **nessun flag li distingue**: la struttura su disco è la dichiarazione, e il
# lint la legge come tutti gli altri consumatori.
#
# Il gruppo che conta è quello sull'append-only del diario, e la ragione è che
# quel controllo è l'unico che guarda **il passato**: il diario è la sola
# registrazione di quel che è stato detto, e una riga già promossa che cambia
# lascia in giro una pagina che non poggia più su niente — con il file intatto e
# il cursore del giardiniere perfettamente plausibile.


def _notebook(root: Path, *, state: str | None = "open", links: bool = True) -> Path:
    """Una wiki nel formato taccuino: pagine piatte, nessuna tassonomia."""
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text(
        "---\ntitle: Orto\n---\n\n# Orto\n\n## Pages\n\n- [[semine]]\n- [[terreno]]\n",
        encoding="utf-8",
    )
    fm = f"---\nstate: {state}\n---\n" if state is not None else "---\ntitle: Semine\n---\n"
    body = "\n# Semine\n\nPomodori a fine aprile." + (" Vedi [[terreno]]." if links else "")
    (root / "wiki" / "semine.md").write_text(fm + body + "\n", encoding="utf-8")
    (root / "wiki" / "terreno.md").write_text(
        "---\nstate: decided\n---\n\n# Terreno\n\nArgilloso. Vedi [[semine]].\n",
        encoding="utf-8",
    )
    return root


def _library(root: Path) -> Path:
    """Una wiki di ricerca: le cartelle del pattern document-first."""
    for sub in ("concepts", "entities", "summaries"):
        (root / "wiki" / sub).mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# Ricerca\n\n- [[Ada]]\n", encoding="utf-8")
    (root / "wiki" / "entities" / "Ada.md").write_text(
        "---\nsources: [nota]\n---\n\n# Ada\n\nVedi [[index]].\n", encoding="utf-8"
    )
    (root / "raw" / "nota.md").write_text("grezzo\n", encoding="utf-8")
    return root


def _journal(root: Path, *entries: str, day: str = "20260823") -> Path:
    page = root / "raw" / "journal" / f"{day}.md"
    page.write_text(
        "# 2026-08-23\n\n" + "".join(f"- 09:0{i} \u2014 {e}\n" for i, e in enumerate(entries)),
        encoding="utf-8",
    )
    return page


def _run(lint_wiki, root: Path, capsys) -> str:
    lint_wiki.lint(str(root))
    return capsys.readouterr().out


# ── Il discriminante ─────────────────────────────────────────────────────────


def test_the_layout_is_read_from_the_folder_not_from_a_flag(lint_wiki, tmp_path):
    assert lint_wiki.is_research_layout(_library(tmp_path / "b") / "wiki") is True
    assert lint_wiki.is_research_layout(_notebook(tmp_path / "t") / "wiki") is False


def test_a_library_does_not_get_the_notebook_checks(lint_wiki, tmp_path, capsys):
    """**Le vecchie wiki non diventano cittadini di serie B.** Nessuna di loro ha
    ``state:``, e chiederglielo trasformerebbe otto wiki sane in otto wiki piene
    di errori — che è il modo più rapido di far ignorare un lint."""
    root = _library(tmp_path)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "state:" not in out
    assert "no link in or out" not in out


def test_a_library_still_gets_the_journal_check(lint_wiki, tmp_path, capsys):
    """Il diario è universale — ogni wiki l'ha guadagnato e la cattura ci scrive
    indipendentemente dal layout — quindi il suo controllo non è per formato."""
    root = _library(tmp_path)
    _journal(root, "un fatto")

    assert "Journal baseline recorded" in _run(lint_wiki, root, capsys)


# ── Il formato taccuino ──────────────────────────────────────────────────────


def test_a_healthy_notebook_is_quiet(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Every page declares a valid state" in out
    assert "no valid `state:`" not in out


def test_a_page_without_a_state_is_an_error(lint_wiki, tmp_path, capsys):
    """Una pagina vale quanto il suo stato dice: senza, un'ipotesi appuntata di
    passaggio si rilegge fra un mese come un fatto stabilito."""
    root = _notebook(tmp_path, state=None)
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "no valid `state:`" in out and "semine.md" in out


def test_a_state_outside_the_vocabulary_is_an_error(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path, state="quasi-deciso")
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "quasi-deciso" in out


def test_a_page_nobody_links_is_flagged(lint_wiki, tmp_path, capsys):
    """Essere elencata nella mappa non è un link: è quel che rende questa cosa
    una wiki invece di una cartella (R5, misurato)."""
    root = _notebook(tmp_path)
    (root / "wiki" / "isolata.md").write_text(
        "---\nstate: open\n---\n\n# Isolata\n\nNiente link.\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    assert "no link in or out" in _run(lint_wiki, root, capsys)


def test_a_dead_link_in_the_map_is_already_caught(lint_wiki, tmp_path, capsys):
    """Non un controllo nuovo: il passo 1 lo copriva già, e T3 ha misurato quanto
    costa — **quattro `list_dir` a ogni domanda a freddo**, perché il prompt dice
    di leggere le pagine che la mappa indica. Il test c'è perché quel costo ora è
    noto e nessuno deve poter allentare il controllo per sbaglio."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[fantasma]]\n", encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "Dead wikilinks" in out and "fantasma" in out


# ── La mappa entra in ogni turno ─────────────────────────────────────────────


def test_a_map_over_the_ceiling_is_flagged(lint_wiki, tmp_path, capsys):
    """Il tetto non è scelto dal lint: è la soglia oltre la quale il blocco di
    progetto smette di iniettare la mappa intera (``_PROJECT_MAP_MAX_CHARS``).
    Oltre, il resto della mappa esiste e l'agente non lo vede."""
    root = _notebook(tmp_path)
    (root / "wiki" / "index.md").write_text(
        "# Orto\n\n- [[semine]]\n- [[terreno]]\n" + ("x" * 2100), encoding="utf-8"
    )
    _journal(root, "un fatto")

    out = _run(lint_wiki, root, capsys)

    assert "The map is" in out and f"over {lint_wiki.MAP_MAX_CHARS}" in out


def test_the_ceiling_matches_the_one_the_prompt_uses(lint_wiki):
    """Due numeri che devono restare uguali vivono in due file: qui il confronto,
    così il giorno che uno cambia il test lo dice invece di lasciare un lint che
    avvisa alla soglia sbagliata."""
    source = (
        Path(__file__).resolve().parents[3] / "jenny" / "agent" / "context.py"
    ).read_text(encoding="utf-8")

    assert f"_PROJECT_MAP_MAX_CHARS = {lint_wiki.MAP_MAX_CHARS}" in source


# ── Il diario ────────────────────────────────────────────────────────────────


def test_a_malformed_journal_line_is_flagged(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    page = _journal(root, "un fatto")
    page.write_text(page.read_text(encoding="utf-8") + "riga senza forma\n", encoding="utf-8")

    out = _run(lint_wiki, root, capsys)

    assert "not in `- HH:MM" in out and "riga senza forma" in out


def test_a_journal_filename_that_is_not_a_day_is_flagged(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    (root / "raw" / "journal" / "appunti.md").write_text("# x\n", encoding="utf-8")

    assert "unexpected name" in _run(lint_wiki, root, capsys)


def test_the_first_run_records_a_baseline_instead_of_claiming_anything(
    lint_wiki, tmp_path, capsys
):
    root = _notebook(tmp_path)
    _journal(root, "un fatto")

    assert "baseline recorded" in _run(lint_wiki, root, capsys)
    assert (root / ".jenny" / "lint_journal.json").is_file()


def test_the_state_lives_in_the_hidden_folder(lint_wiki, tmp_path, capsys):
    """Macchinario, non materiale dell'utente: come il cursore del giardiniere, e
    per la stessa ragione — sotto ``.jenny/`` viste, grafo e impronta di Atlas non
    lo vedono."""
    root = _notebook(tmp_path)
    _journal(root, "un fatto")
    _run(lint_wiki, root, capsys)

    assert not list((root / "wiki").glob("*.json"))
    assert (root / ".jenny" / "lint_journal.json").is_file()


def test_appending_a_line_is_not_a_violation(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)

    with page.open("a", encoding="utf-8") as fh:
        fh.write("- 10:00 \u2014 secondo\n")

    assert "append-only since the last lint" in _run(lint_wiki, root, capsys)


def test_changing_a_line_already_written_is_a_violation(lint_wiki, tmp_path, capsys):
    """Il caso che un digest sul file intero **non** distingue da una crescita, e
    che un controllo sulla sola dimensione manca del tutto: la riga cambia e il
    file resta della stessa lunghezza."""
    root = _notebook(tmp_path)
    page = _journal(root, "primo")
    _run(lint_wiki, root, capsys)

    page.write_text(
        page.read_text(encoding="utf-8").replace("primo", "PRIMO"), encoding="utf-8"
    )
    out = _run(lint_wiki, root, capsys)

    assert "no longer append-only" in out
    assert "already-written line was changed" in out


def test_truncating_the_journal_is_a_violation(lint_wiki, tmp_path, capsys):
    root = _notebook(tmp_path)
    page = _journal(root, "primo", "secondo")
    _run(lint_wiki, root, capsys)

    page.write_text("# 2026-08-23\n", encoding="utf-8")
    out = _run(lint_wiki, root, capsys)

    assert "no longer append-only" in out and "truncated" in out


def test_a_wiki_without_a_journal_says_nothing_about_one(lint_wiki, tmp_path, capsys):
    """Una wiki più vecchia della migrazione può non avere il diario: silenzio,
    non un errore."""
    root = _notebook(tmp_path)

    out = _run(lint_wiki, root, capsys)

    assert "Journal" not in out
    assert not (root / ".jenny").exists()
