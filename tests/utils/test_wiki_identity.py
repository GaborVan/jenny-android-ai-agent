"""L'identità di una wiki: chi la scrive, chi la legge, e cosa fa se è ambigua.

Passo **7.1** e **7.4** di ``roadmap/progetti-passi.md``, strada **B**.

L'id serve a **una** cosa: ritrovare la chat di una wiki dopo che la cartella ha
cambiato nome. Non è l'indirizzo di niente — quello resta il nome della cartella,
come deciso il 21/08 — e non finisce in nessun nome di file. È la ragione per cui
può essere opaco: se un domani diventasse l'indirizzo, i file di sessione si
chiamerebbero ``project_3f9a2c1b7e04.jsonl`` e ispezionarli con adb, che è come
abbiamo trovato metà dei difetti di questa settimana, diventerebbe una ricerca.

Il test che conta più degli altri è
``test_two_wikis_with_the_same_id_resolve_to_nothing``: ci si arriva **copiando
una cartella**, che è una cosa che si fa senza pensarci, e indovinare quale delle
due sia "quella giusta" metterebbe la storia di una chat sotto la wiki sbagliata.
La lezione del passo 6: si rifiuta invece di scegliere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.utils.wiki_migration import migrate_wikis
from jenny.utils.wiki_paths import (
    WIKI_ID_KEY,
    find_wiki_by_id,
    is_valid_wiki_id,
    new_wiki_id,
    wiki_id,
)


def _wiki(root: Path, name: str, schema: str | None = None, body: str = "") -> Path:
    project = root / name
    (project / "wiki").mkdir(parents=True)
    (project / "wiki" / "index.md").write_text("# indice\n", encoding="utf-8")
    if schema:
        (project / schema).write_text(body, encoding="utf-8")
    return project


@pytest.fixture
def wikis(tmp_path: Path) -> Path:
    d = tmp_path / "wikis"
    d.mkdir()
    return d


# ── La forma ─────────────────────────────────────────────────────────────


def test_a_new_id_is_valid_and_not_derived_from_anything() -> None:
    """Un id derivato dal nome cambierebbe insieme al nome, cioè non servirebbe."""
    ids = {new_wiki_id() for _ in range(50)}
    assert len(ids) == 50, "due id uguali in cinquanta è già una collisione"
    assert all(is_valid_wiki_id(i) for i in ids)


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "ABCDEF123456", "3f9a2c1b7e0", "3f9a2c1b7e045", None, 12, "3f9a2c1b7e0g"],
    ids=["vuoto", "corto", "maiuscolo", "11", "13", "none", "int", "non-hex"],
)
def test_anything_else_is_not_an_id(raw) -> None:
    """Stretto apposta: un valore mezzo giusto letto come id troverebbe la wiki sbagliata."""
    assert is_valid_wiki_id(raw) is False


# ── La lettura ───────────────────────────────────────────────────────────


def test_the_id_is_read_from_the_instructions_file(wikis: Path) -> None:
    project = _wiki(wikis, "patreon", "AGENTS.md", "---\nid: 3f9a2c1b7e04\n---\n\n# P\n")
    assert wiki_id(project) == "3f9a2c1b7e04"


def test_it_is_read_from_claude_md_too_while_that_exists(wikis: Path) -> None:
    """Una wiki non ancora migrata non deve perdere la propria identità."""
    project = _wiki(wikis, "vecchia", "CLAUDE.md", "---\nid: 3f9a2c1b7e04\n---\n\n# V\n")
    assert wiki_id(project) == "3f9a2c1b7e04"


@pytest.mark.parametrize(
    ("schema", "body"),
    [
        (None, ""),
        ("AGENTS.md", "# senza frontmatter\n"),
        ("AGENTS.md", "---\nsummary: x\n---\n\n# senza id\n"),
        ("AGENTS.md", "---\nid: non-un-id\n---\n\n# id storto\n"),
    ],
    ids=["nessun-file", "nessuna-frontmatter", "nessun-id", "id-storto"],
)
def test_a_missing_id_is_not_an_error(wikis: Path, schema: str | None, body: str) -> None:
    """Una wiki senza id funziona come prima del passo 7: solo, un rinomino la perde."""
    project = _wiki(wikis, "senza", schema, body)
    assert wiki_id(project) is None


def test_the_id_survives_a_frontmatter_that_yaml_cannot_parse(wikis: Path) -> None:
    """Un difetto vero, visto sul telefono il 22/08.

    La riga di scope è testo libero dell'utente e finisce *dentro* la
    frontmatter. Un due punti in mezzo — «Prova del passo 7: la chat segue» — la
    rende non parsabile, e ``yaml.safe_load`` non perde quella riga: perde
    **tutte** le altre. L'id serve a riparare una chat orfana, quindi doveva
    essere leggibile proprio nei file un po' storti — cioè quelli in cui serve.
    Da qui la regex invece del parser: la sua forma è fissa.
    """
    import yaml

    rotta = "---\nid: 3f9a2c1b7e04\nsummary: Prova del passo 7: la chat segue\n---\n\n# P\n"
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(rotta.split("---")[1])

    project = _wiki(wikis, "storta", "AGENTS.md", rotta)
    assert wiki_id(project) == "3f9a2c1b7e04"


def test_a_seeded_scope_line_is_quoted_so_it_never_breaks_the_block(tmp_path: Path) -> None:
    """L'altra metà della correzione: chi *scrive* la riga la mette al sicuro.

    Leggere l'id con una regex salva l'id; non salva ``summary``, che è quello
    che ``read_wiki_scope`` cerca per comporre ``wikis/_index.md``. Quindi la
    riga si quota all'origine.
    """
    import yaml

    from jenny.webui.project_create import _yaml_scalar

    for seed in ("Prova del passo 7: la chat segue", 'con "virgolette"', "back\\slash", "# hash"):
        block = f"id: 3f9a2c1b7e04\nsummary: {_yaml_scalar(seed)}"
        assert yaml.safe_load(block)["summary"] == seed, f"seed non sopravvive: {seed!r}"


# ── La ricerca, e l'ambiguità ────────────────────────────────────────────


def test_a_wiki_is_found_by_its_id(wikis: Path) -> None:
    _wiki(wikis, "altra", "AGENTS.md", "---\nid: aaaaaaaaaaaa\n---\n")
    target = _wiki(wikis, "cercata", "AGENTS.md", "---\nid: bbbbbbbbbbbb\n---\n")
    assert find_wiki_by_id(wikis, "bbbbbbbbbbbb") == target


def test_an_unknown_id_finds_nothing(wikis: Path) -> None:
    _wiki(wikis, "altra", "AGENTS.md", "---\nid: aaaaaaaaaaaa\n---\n")
    assert find_wiki_by_id(wikis, "cccccccccccc") is None


def test_two_wikis_with_the_same_id_resolve_to_nothing(wikis: Path) -> None:
    """Ci si arriva copiando una cartella, e indovinare è il guasto peggiore.

    Con due candidate, sceglierne una metterebbe la storia di una conversazione
    sotto la wiki sbagliata — irrecuperabile, perché nessuno se ne accorge. Una
    chat che resta orfana e lo dice è un guaio molto più piccolo.
    """
    _wiki(wikis, "originale", "AGENTS.md", "---\nid: dddddddddddd\n---\n")
    _wiki(wikis, "copia", "AGENTS.md", "---\nid: dddddddddddd\n---\n")
    assert find_wiki_by_id(wikis, "dddddddddddd") is None


def test_a_malformed_target_is_not_looked_up(wikis: Path) -> None:
    _wiki(wikis, "una", "AGENTS.md", "---\nid: aaaaaaaaaaaa\n---\n")
    assert find_wiki_by_id(wikis, "") is None
    assert find_wiki_by_id(wikis, "aaaa") is None


def test_a_folder_that_is_not_a_wiki_is_not_a_candidate(wikis: Path) -> None:
    """La definizione di wiki è una: contiene ``wiki/``. Vale anche qui."""
    stray = wikis / "non-una-wiki"
    stray.mkdir()
    (stray / "AGENTS.md").write_text("---\nid: eeeeeeeeeeee\n---\n", encoding="utf-8")
    assert find_wiki_by_id(wikis, "eeeeeeeeeeee") is None


# ── La migrazione (7.4) ──────────────────────────────────────────────────


def test_claude_md_becomes_agents_md_with_the_content_intact(wikis: Path) -> None:
    """Non è un ritiro di template: quel testo l'ha scritto l'utente."""
    body = "---\nsummary: la mia wiki\ntags: [a, b]\n---\n\n# Titolo\n\nroba mia\n"
    project = _wiki(wikis, "vecchia", "CLAUDE.md", body)

    migrate_wikis(wikis)

    assert not (project / "CLAUDE.md").exists()
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "roba mia" in text
    assert "summary: la mia wiki" in text
    assert "tags: [a, b]" in text, (
        "la frontmatter non si riserializza: `yaml.dump` riordinerebbe le chiavi, "
        "normalizzerebbe le virgolette e perderebbe i commenti di un file scritto a mano"
    )
    assert is_valid_wiki_id(wiki_id(project))


def test_a_wiki_with_both_files_is_left_alone(wikis: Path) -> None:
    """Sceglierne uno butterebbe l'altro. È lo stato che i lettori sanno leggere."""
    project = _wiki(wikis, "doppia", "AGENTS.md", "---\nsummary: nuovo\n---\n")
    (project / "CLAUDE.md").write_text("---\nsummary: vecchio\n---\n", encoding="utf-8")

    migrate_wikis(wikis)

    assert (project / "CLAUDE.md").read_text(encoding="utf-8").strip().endswith("---")
    assert "nuovo" in (project / "AGENTS.md").read_text(encoding="utf-8")


def test_a_wiki_with_no_instructions_file_gets_a_minimal_one(wikis: Path) -> None:
    """Minimo e non lo scaffold completo: `AGENTS.md` nasce quasi vuoto (21/08)."""
    project = _wiki(wikis, "adhd")

    migrate_wikis(wikis)

    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert is_valid_wiki_id(wiki_id(project))
    assert "# Adhd" in text
    assert "What this wiki covers" not in text, "lo scaffold pieno è mestiere di /init"
    # `summary` resta un segnaposto, non il nome della cartella: con il nome,
    # `wikis/_index.md` direbbe «adhd — adhd», che *sembra* una descrizione. La
    # voce di prima diceva «(no AGENTS.md)», cioè la verità.
    from jenny.utils.wiki_paths import read_wiki_scope

    assert read_wiki_scope(project) == "(no scope set)"


def test_a_file_without_frontmatter_gains_one_above_its_content(wikis: Path) -> None:
    project = _wiki(wikis, "nuda", "CLAUDE.md", "# Solo un titolo\n\ncontenuto\n")

    migrate_wikis(wikis)

    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("---\n" + WIKI_ID_KEY)
    assert "# Solo un titolo" in text and "contenuto" in text


def test_the_migration_is_idempotent(wikis: Path) -> None:
    """Gira a **ogni** avvio, come l'estrazione dei template: a regime deve costare zero."""
    _wiki(wikis, "a", "CLAUDE.md", "---\nsummary: x\n---\n")
    _wiki(wikis, "b")

    first = migrate_wikis(wikis)
    before = {p.name: p.read_text(encoding="utf-8") for p in wikis.rglob("AGENTS.md")}
    second = migrate_wikis(wikis)

    assert sorted(first["identified"]) == ["a", "b"]
    assert second == {"renamed": [], "identified": []}
    assert {p.name: p.read_text(encoding="utf-8") for p in wikis.rglob("AGENTS.md")} == before


def test_a_broken_wiki_does_not_stop_the_others(wikis: Path) -> None:
    """Un avvio che muore su una cartella storta non migra nemmeno le altre."""
    _wiki(wikis, "sana")
    rotta = _wiki(wikis, "rotta")
    (rotta / "AGENTS.md").mkdir()  # una directory dove ci vuole un file

    result = migrate_wikis(wikis)

    assert "sana" in result["identified"]
    assert is_valid_wiki_id(wiki_id(wikis / "sana"))


def test_a_missing_wikis_dir_is_not_an_error(tmp_path: Path) -> None:
    assert migrate_wikis(tmp_path / "mai-esistita") == {"renamed": [], "identified": []}
