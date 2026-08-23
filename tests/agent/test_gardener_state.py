"""Il cursore del giardiniere: cosa ha letto, e cosa non deve saltare.

Passo **T4.1** di ``roadmap/taccuino-passi.md``. Qui non c'è nessun modello: c'è
la domanda «di questo diario, cosa non ho ancora letto?» e la risposta su disco.

Due test valgono più degli altri, e per la stessa ragione: **una riga di diario
che finisce sotto il cursore senza essere stata promossa è persa per sempre.** Il
diario è append-only, quindi nessuno la rileggerà mai; e non c'è nessun segnale
visibile, perché il file è intatto e il cursore è plausibile. Sono
``test_the_cap_does_not_swallow_the_first_unread_line`` (era un difetto vero,
trovato scrivendo il modulo) e ``test_a_pruned_state_keeps_the_days_that_exist``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jenny.agent.gardener_state import (
    GardenerState,
    gardener_state_file,
    read_journal_delta,
    read_state,
    write_state,
)


def _project(root: Path, name: str = "viaggio") -> Path:
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True)
    (project / "raw" / "journal").mkdir(parents=True)
    return project


def _journal(project: Path, day: str, *entries: str, heading: bool = True) -> Path:
    page = project / "raw" / "journal" / f"{day}.md"
    body = ""
    if heading:
        body += f"# {day[:4]}-{day[4:6]}-{day[6:]}\n\n"
    body += "".join(f"- 09:00 — {e}\n" for e in entries)
    page.write_text(body, encoding="utf-8")
    return page


# ── Il delta ─────────────────────────────────────────────────────────────────


def test_a_fresh_project_has_nothing_to_read(tmp_path) -> None:
    project = _project(tmp_path)
    assert read_journal_delta(project, GardenerState()).is_empty


def test_no_journal_folder_is_not_an_error(tmp_path) -> None:
    """Una wiki vecchia può non avere ancora il diario: nessun delta, nessuna
    eccezione. È il caso normale al primo giro dopo un aggiornamento."""
    plain = tmp_path / "wikis" / "vecchia"
    (plain / "wiki").mkdir(parents=True)
    assert read_journal_delta(plain, GardenerState()).is_empty


def test_it_reads_the_entries_and_skips_heading_and_blanks(tmp_path) -> None:
    project = _project(tmp_path)
    _journal(project, "20260822", "il furgone ha le gomme da cambiare", "si parte il 14")

    delta = read_journal_delta(project, GardenerState())

    assert delta.line_count == 2
    assert [f.path for f in delta.files] == ["raw/journal/20260822.md"]
    assert delta.files[0].lines == (
        "- 09:00 — il furgone ha le gomme da cambiare",
        "- 09:00 — si parte il 14",
    )


def test_the_days_arrive_in_order(tmp_path) -> None:
    """Cronologico, che qui è alfabetico: è la ragione per cui il nome del file è
    ``AAAAMMGG`` e non ``AAAA-MM-GG`` né ``GG-MM``. Il giardiniere promuove
    leggendo una storia, e una storia fuori ordine cambia le conclusioni."""
    project = _project(tmp_path)
    _journal(project, "20260901", "settembre")
    _journal(project, "20260822", "agosto")
    _journal(project, "20261015", "ottobre")

    delta = read_journal_delta(project, GardenerState())

    assert [f.path for f in delta.files] == [
        "raw/journal/20260822.md",
        "raw/journal/20260901.md",
        "raw/journal/20261015.md",
    ]


def test_a_hidden_file_is_not_a_journal_page(tmp_path) -> None:
    project = _project(tmp_path)
    (project / "raw" / "journal" / ".scratch.md").write_text("- 09:00 — x\n", encoding="utf-8")
    assert read_journal_delta(project, GardenerState()).is_empty


def test_an_unreadable_page_is_skipped_and_the_rest_is_read(tmp_path) -> None:
    """Un giorno illeggibile non deve far saltare la passata: il resto del diario
    è ancora materiale buono, e fermarsi qui vorrebbe dire che un file rotto
    congela il progetto per sempre."""
    project = _project(tmp_path)
    bad = project / "raw" / "journal" / "20260822.md"
    bad.write_bytes(b"\xff\xfe non utf-8")
    _journal(project, "20260823", "questo si legge")

    delta = read_journal_delta(project, GardenerState())

    assert [f.path for f in delta.files] == ["raw/journal/20260823.md"]


# ── Il cursore ───────────────────────────────────────────────────────────────


def test_consuming_a_delta_leaves_nothing_behind(tmp_path) -> None:
    """La proprietà per cui il cursore esiste: letto una volta, non si rilegge."""
    project = _project(tmp_path)
    _journal(project, "20260822", "primo", "secondo")

    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))

    assert read_journal_delta(project, state).is_empty


def test_only_the_new_lines_come_back(tmp_path) -> None:
    project = _project(tmp_path)
    page = _journal(project, "20260822", "primo")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))

    with page.open("a", encoding="utf-8") as fh:
        fh.write("- 10:00 — secondo\n")
    delta = read_journal_delta(project, state)

    assert delta.line_count == 1
    assert delta.files[0].lines == ("- 10:00 — secondo",)


def test_the_cursor_of_another_day_is_not_forgotten(tmp_path) -> None:
    """Il delta di oggi si **fonde** nel cursore, non lo sostituisce: se lo
    sostituisse, il primo giorno tranquillo farebbe ripartire da capo tutto il
    diario dei giorni prima."""
    project = _project(tmp_path)
    _journal(project, "20260822", "ieri")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))

    _journal(project, "20260823", "oggi")
    state = state.advanced(read_journal_delta(project, state))

    assert set(state.cursor) == {
        "raw/journal/20260822.md",
        "raw/journal/20260823.md",
    }
    assert read_journal_delta(project, state).is_empty


def test_a_lost_cursor_rereads_instead_of_skipping(tmp_path) -> None:
    """Perso il cursore si rilegge da capo, e va bene così: il costo è ripassare
    righe già viste, che l'idempotenza della passata rende innocuo. Il difetto
    da non avere è l'opposto — un cursore indovinato che salta righe in
    silenzio."""
    project = _project(tmp_path)
    _journal(project, "20260822", "primo", "secondo")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))
    write_state(project, state)
    gardener_state_file(project).unlink()

    assert read_journal_delta(project, read_state(project)).line_count == 2


def test_a_shrunken_journal_is_not_reread(tmp_path) -> None:
    """Un diario più corto del cursore vuol dire che qualcuno l'ha riscritto —
    cosa che l'append-only vieta. Rileggerlo da capo ripromuoverebbe fatti già
    promossi, quindi non si rilegge; il difetto lo trova il lint (T5), qui si
    tiene solo la testa fredda.

    Il prezzo, detto per intero: da quel momento le righe che quel giorno
    riceverà restano invisibili finché il file non risupera il cursore. È
    inerente al contare righe, e il rimedio non sta qui — sta nel non riscrivere
    un diario."""
    project = _project(tmp_path)
    _journal(project, "20260822", "primo", "secondo", "terzo")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))
    _journal(project, "20260822", "riscritto")

    assert read_journal_delta(project, state).is_empty


# ── Il tetto ─────────────────────────────────────────────────────────────────


def test_the_cap_takes_what_it_can_and_says_what_it_left(tmp_path) -> None:
    """Mai troncare zitti: il numero di voci rimaste è un dato che il chiamante
    deve poter mettere nel prompt. È la lezione già scritta in Atlas e nel tetto
    della mappa (T3)."""
    project = _project(tmp_path)
    _journal(project, "20260822", *[f"fatto {i}" for i in range(10)])

    delta = read_journal_delta(project, GardenerState(), max_lines=4)

    assert delta.line_count == 4
    assert delta.left_behind == 6


def test_the_rest_arrives_on_the_next_pass(tmp_path) -> None:
    project = _project(tmp_path)
    _journal(project, "20260822", *[f"fatto {i}" for i in range(10)])

    first = read_journal_delta(project, GardenerState(), max_lines=4)
    state = GardenerState().advanced(first)
    second = read_journal_delta(project, state, max_lines=4)

    assert second.files[0].lines[0] == "- 09:00 — fatto 4"
    assert second.left_behind == 2


def test_the_cap_does_not_swallow_the_first_unread_line(tmp_path) -> None:
    """**Il test che conta.** Il tetto deve fermare la lettura, non filtrarla.

    Era un difetto vero: superato il budget il ciclo continuava a scorrere, e una
    riga *vuota* dopo il punto di taglio faceva avanzare il cursore oltre la
    prima voce non letta. Quella voce restava nel file, sotto il cursore, e
    nessuno l'avrebbe più letta — perdita silenziosa, con il diario intatto e un
    cursore dall'aria plausibile.

    Da cui la forma di questo diario: una riga vuota **subito dopo** il taglio.
    """
    project = _project(tmp_path)
    page = project / "raw" / "journal" / "20260822.md"
    page.write_text(
        "# 2026-08-22\n\n"
        "- 09:00 — primo\n"
        "- 10:00 — secondo\n"
        "\n"
        "- 11:00 — terzo\n",
        encoding="utf-8",
    )

    first = read_journal_delta(project, GardenerState(), max_lines=1)
    assert first.files[0].lines == ("- 09:00 — primo",)

    state = GardenerState().advanced(first)
    second = read_journal_delta(project, state, max_lines=10)

    assert [line for f in second.files for line in f.lines] == [
        "- 10:00 — secondo",
        "- 11:00 — terzo",
    ], "una voce è finita sotto il cursore senza essere stata letta"


def test_a_zero_cap_reads_nothing_and_loses_nothing(tmp_path) -> None:
    project = _project(tmp_path)
    _journal(project, "20260822", "primo")

    delta = read_journal_delta(project, GardenerState(), max_lines=0)

    assert delta.is_empty and delta.left_behind == 1
    assert read_journal_delta(project, GardenerState()).line_count == 1


# ── Lo stato su disco ────────────────────────────────────────────────────────


def test_the_state_round_trips(tmp_path) -> None:
    project = _project(tmp_path)
    _journal(project, "20260822", "primo")
    state = GardenerState().advanced(
        read_journal_delta(project, GardenerState()), at=datetime(2026, 8, 22, 21, 30)
    )

    write_state(project, state)
    back = read_state(project)

    assert back.cursor == state.cursor
    assert back.last_run_at == "2026-08-22T21:30:00"


def test_the_state_lives_in_the_hidden_folder(tmp_path) -> None:
    """Sotto ``.jenny/``, cioè fuori dall'impronta di Atlas, fuori dalle viste e
    fuori dalla rubrica — senza che nessuno di quei tre debba imparare niente. Il
    quaderno è materiale umano, il cursore è macchinario."""
    project = _project(tmp_path)
    write_state(project, GardenerState())

    assert gardener_state_file(project) == project / ".jenny" / "gardener.json"
    assert gardener_state_file(project).is_file()
    assert not list((project / "wiki").rglob("*"))


def test_a_corrupt_state_reads_as_empty(tmp_path) -> None:
    project = _project(tmp_path)
    path = gardener_state_file(project)
    path.parent.mkdir(parents=True)
    path.write_text('{"version": 1, "cursor": {"a', encoding="utf-8")

    assert read_state(project) == GardenerState()


def test_a_state_from_another_version_is_not_guessed(tmp_path) -> None:
    project = _project(tmp_path)
    path = gardener_state_file(project)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"version": 99, "cursor": {"raw/journal/20260822.md": 4}}),
        encoding="utf-8",
    )

    assert read_state(project).cursor == {}


def test_a_nonsense_cursor_entry_is_dropped_not_trusted(tmp_path) -> None:
    """Un valore che non è un numero di righe non è un cursore. Scartarlo
    significa rileggere quel giorno; fidarsene significherebbe saltarlo."""
    project = _project(tmp_path)
    path = gardener_state_file(project)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "version": 1,
            "cursor": {
                "raw/journal/20260822.md": "quattro",
                "raw/journal/20260823.md": -1,
                "raw/journal/20260824.md": True,
                "raw/journal/20260825.md": 4,
            },
        }),
        encoding="utf-8",
    )

    assert read_state(project).cursor == {"raw/journal/20260825.md": 4}


def test_a_pruned_state_keeps_the_days_that_exist(tmp_path) -> None:
    """**Il secondo test che conta.** Si pota solo quel che non c'è più.

    Potare per età sembra ragionevole — una voce al giorno per sempre *sembra*
    una perdita — ed è la strada per rileggere un giorno che era già stato
    letto: costo per niente, e con il tetto in mezzo anche un ritardo. Mille
    giorni di cursore sono una quarantina di kilobyte.
    """
    project = _project(tmp_path)
    _journal(project, "20260822", "esiste")
    state = GardenerState(cursor={
        "raw/journal/20260822.md": 3,
        "raw/journal/20240101.md": 7,
    })

    write_state(project, state)

    assert read_state(project).cursor == {"raw/journal/20260822.md": 3}
