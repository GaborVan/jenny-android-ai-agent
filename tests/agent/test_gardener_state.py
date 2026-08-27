"""Il cursore del giardiniere: cosa ha letto, e cosa non deve saltare.

Passo **T4.1** di ``roadmap/taccuino-passi.md``. Qui non c'è nessun modello: c'è
la domanda «di questo diario, cosa non ho ancora letto?» e la risposta su disco.

Due test valgono più degli altri, e per la stessa ragione: **una riga di diario
che finisce sotto il cursore senza essere stata promossa è persa per sempre.** Il
diario è append-only, quindi nessuno la rileggerà mai; e non c'è nessun segnale
visibile, perché il file è intatto e il cursore è plausibile. Sono
``test_the_cap_does_not_swallow_the_first_unread_line`` (era un difetto vero,
trovato scrivendo il modulo) e ``test_a_pruned_state_keeps_the_days_that_exist``.

Il terzo, arrivato dopo, è dello stesso ceppo:
``test_a_line_deleted_above_the_cursor_makes_the_day_be_reread``. Il conteggio di
righe da solo non vede un diario riscritto *sopra* il cursore, e l'append-only
è vero solo dal lato del giardiniere — un turno può scrivere ``raw/journal/*.md``
con ``write_file``. Da cui il testimone.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from loguru import logger

from jenny.agent.gardener_state import (
    GardenerState,
    gardener_state_file,
    read_journal_delta,
    read_state,
    write_state,
)


@contextmanager
def _warnings() -> Iterator[list[str]]:
    """I WARNING di loguru emessi dentro il blocco.

    Qui l'avviso è materia del test, non decorazione: un diario riscritto sopra
    il cursore si nota *solo* dal log — il file è intatto e il cursore ha
    l'aria giusta — quindi un avviso che non esce è il difetto che torna muto.
    """
    seen: list[str] = []
    handler = logger.add(lambda message: seen.append(str(message)), level="WARNING")
    try:
        yield seen
    finally:
        logger.remove(handler)


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


def test_a_shrunken_journal_is_still_reported(tmp_path) -> None:
    """Non si rilegge, ma non si tace: è l'unico segnale che quel giorno esiste."""
    project = _project(tmp_path)
    _journal(project, "20260822", "primo", "secondo", "terzo")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))
    _journal(project, "20260822", "riscritto")

    with _warnings() as said:
        assert read_journal_delta(project, state).is_empty

    assert any("20260822" in line and "append-only" in line for line in said), said


# ── Il testimone ─────────────────────────────────────────────────────────────


def test_a_line_deleted_above_the_cursor_makes_the_day_be_reread(tmp_path) -> None:
    """**Il terzo test che conta.** Un diario riscritto *sopra* il cursore.

    Il conteggio di righe da solo non lo vede: cancellata una riga già letta e
    aggiunta una nuova, il file resta più lungo del cursore, quindi nessun
    accorciamento, e le righe scorrono in su di una. La prima voce non letta
    finisce **sotto** il cursore — persa per sempre, perché il diario nessuno lo
    rilegge. È raggiungibile senza malizia: solo ``journal_append`` è append-only
    per costruzione, un turno può riscrivere ``raw/journal/*.md`` con
    ``write_file``.

    Da cui il testimone del prefisso consumato: se non torna si rilegge da riga
    zero. Costa una ripromozione, che la passata assorbe, invece di una riga
    perduta, che non torna.
    """
    project = _project(tmp_path)
    _journal(project, "20260822", "primo", "secondo", "terzo")
    state = GardenerState().advanced(
        read_journal_delta(project, GardenerState(), max_lines=2)
    )
    assert state.cursor == {"raw/journal/20260822.md": 4}
    # "primo" via, "quarto" in coda: il file resta più lungo del cursore.
    _journal(project, "20260822", "secondo", "terzo", "quarto")

    with _warnings() as said:
        delta = read_journal_delta(project, state, max_lines=10)

    assert [line for f in delta.files for line in f.lines] == [
        "- 09:00 — secondo",
        "- 09:00 — terzo",
        "- 09:00 — quarto",
    ], "una voce non letta è finita sotto il cursore"
    assert delta.line_count == 3
    assert any("20260822" in line for line in said), said

    # E il tetto conta sulla rilettura intera, non sulla coda: quel che resta
    # va detto anche qui.
    capped = read_journal_delta(project, state, max_lines=2)
    assert capped.line_count == 2 and capped.left_behind == 1


def test_a_plain_append_is_not_mistaken_for_a_rewrite(tmp_path) -> None:
    """Il controllo del test sopra: il caso normale — una voce in coda — non deve
    far scattare niente. Un testimone che grida a ogni append farebbe rileggere
    il diario a ogni passata, cioè ripromuovere tutto per sempre."""
    project = _project(tmp_path)
    page = _journal(project, "20260822", "primo")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))

    with page.open("a", encoding="utf-8") as fh:
        fh.write("- 10:00 — secondo\n")
    with _warnings() as said:
        delta = read_journal_delta(project, state)

    assert delta.files[0].lines == ("- 10:00 — secondo",)
    assert said == []


def test_a_cursor_without_a_witness_rereads_from_scratch(tmp_path) -> None:
    """Uno stato di prima del testimone — o con il testimone illeggibile — vale
    «non posso verificare», che è una rilettura: mai un cursore creduto sulla
    parola. Si paga una passata sola, poi il testimone c'è."""
    project = _project(tmp_path)
    _journal(project, "20260822", "primo", "secondo", "terzo")

    with _warnings() as said:
        delta = read_journal_delta(project, GardenerState(cursor={
            "raw/journal/20260822.md": 4,
        }))

    assert delta.line_count == 3
    assert any("20260822" in line for line in said), said


def test_a_state_file_from_before_the_witness_degrades_to_empty(tmp_path) -> None:
    """La forma dello stato è cambiata, quindi la versione è cambiata: il file di
    un telefono aggiornato si legge come stato vuoto — rilettura da capo, nessuna
    eccezione — che è lo stesso costo che il testimone assente avrebbe comunque
    imposto."""
    project = _project(tmp_path)
    path = gardener_state_file(project)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "version": 1,
            "cursor": {"raw/journal/20260822.md": 4},
            "last_run_at": "2026-08-22T21:30:00",
        }),
        encoding="utf-8",
    )

    assert read_state(project) == GardenerState()


def test_only_the_rewritten_day_is_reread(tmp_path) -> None:
    """Il testimone è per giorno, non per progetto: un giorno riscritto non deve
    trascinare nella rilettura tutto il diario che gli sta accanto."""
    project = _project(tmp_path)
    _journal(project, "20260822", "intatto uno", "intatto due")
    _journal(project, "20260823", "primo", "secondo", "terzo")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))
    # Il 23 riscritto sopra il cursore (via "primo", in coda due voci nuove, così
    # il file resta più lungo del cursore); il 22 solo allungato.
    _journal(project, "20260823", "secondo", "terzo", "quarto", "quinto")
    with (project / "raw" / "journal" / "20260822.md").open("a", encoding="utf-8") as fh:
        fh.write("- 10:00 — intatto tre\n")

    delta = read_journal_delta(project, state)

    assert {f.path: f.lines for f in delta.files} == {
        "raw/journal/20260822.md": ("- 10:00 — intatto tre",),
        "raw/journal/20260823.md": (
            "- 09:00 — secondo",
            "- 09:00 — terzo",
            "- 09:00 — quarto",
            "- 09:00 — quinto",
        ),
    }


def test_the_witness_survives_the_state_file(tmp_path) -> None:
    """Il testimone deve stare su disco accanto al conteggio: se non ci stesse,
    ogni passata ripartirebbe da zero su ogni giorno — la rilettura per sempre,
    invece della rilettura una volta."""
    project = _project(tmp_path)
    page = _journal(project, "20260822", "primo", "secondo")
    write_state(project, GardenerState().advanced(
        read_journal_delta(project, GardenerState())
    ))
    with page.open("a", encoding="utf-8") as fh:
        fh.write("- 10:00 — terzo\n")

    with _warnings() as said:
        delta = read_journal_delta(project, read_state(project))

    assert delta.files[0].lines == ("- 10:00 — terzo",)
    assert said == []


def test_a_last_line_completed_later_is_not_lost(tmp_path) -> None:
    """La perdita silenziosa che il conteggio da solo non vede.

    Un diario salvato **senza newline finale** — cosa che ``journal_append`` non
    fa mai, ma un ``write_file`` di un turno sì — ha l'ultima riga incompleta, e
    quella riga viene letta e consumata. Il primo ``journal_append`` successivo la
    *completa* incollandosi in coda, sulla stessa riga fisica: il numero di righe
    non si muove, quindi il vecchio ``seen >= len(physical)`` saltava il giorno e
    **il fatto appena catturato non veniva promosso mai**. Il file è intatto e il
    cursore ha l'aria giusta: nessun segnale, per sempre.

    Il rimedio è il testimone, che esisteva già ed era solo escluso dall'ultima
    riga. Il prezzo è la ripromozione di quel giorno, che la passata assorbe per
    costruzione.
    """
    project = _project(tmp_path)
    page = project / "raw" / "journal" / "20260822.md"
    page.write_text("# 2026-08-22\n\n- 09:00 — il furgone", encoding="utf-8")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))
    assert state.cursor == {"raw/journal/20260822.md": 3}

    with page.open("a", encoding="utf-8") as fh:
        fh.write(" ha le gomme da cambiare\n")
    with _warnings() as said:
        delta = read_journal_delta(project, state)

    assert delta.files[0].lines == ("- 09:00 — il furgone ha le gomme da cambiare",)
    assert any("20260822" in line for line in said), said


def test_a_finished_day_that_did_not_change_is_not_reread(tmp_path) -> None:
    """Il controllo del test sopra, e la ragione per cui il testimone si guarda
    anche a file finito senza che questo costi una ripromozione a ogni giro: se il
    prefisso torna, non c'è niente da fare e non si dice niente."""
    project = _project(tmp_path)
    _journal(project, "20260822", "primo", "secondo")
    state = GardenerState().advanced(read_journal_delta(project, GardenerState()))

    with _warnings() as said:
        delta = read_journal_delta(project, state)

    assert delta.is_empty
    assert said == []


def test_a_finished_day_with_no_witness_is_left_alone(tmp_path) -> None:
    """L'unico punto in cui «non verificabile» **non** vuol dire «rileggi».

    Sotto un cursore che sta alla fine del file non c'è niente da perdere — la
    stessa asimmetria del file accorciato — mentre trattare il testimone assente
    come un dubbio avrebbe fatto ripromuovere un diario intero a ogni cursore
    scritto da una versione che i testimoni non li teneva. Controllo dichiarato
    senza mutazione: fissa che il cancello non è stato allargato.
    """
    project = _project(tmp_path)
    _journal(project, "20260822", "primo", "secondo")

    with _warnings() as said:
        delta = read_journal_delta(
            project, GardenerState(cursor={"raw/journal/20260822.md": 4})
        )

    assert delta.is_empty
    assert said == []


# ── Le righe fisiche ─────────────────────────────────────────────────────────


def test_a_journal_line_is_what_wc_l_counts(tmp_path) -> None:
    """Il cursore è un numero che una persona verifica con ``wc -l``, e per farlo
    le righe vanno divise come le divide ``wc``.

    ``str.splitlines()`` divide anche su ``\\v``, ``\\f``, ``\\x85``, U+2028 e
    U+2029: una voce che ne contenga uno diventava **due**. E la correzione ovvia
    — ``text.split("\\n")`` — sarebbe stata peggio del difetto: su un file normale
    conta una riga in più (l'elemento vuoto dopo l'ultimo ``\\n``), cioè rompe la
    stessa proprietà in tutti i casi invece che in uno raro.
    """
    from jenny.agent.gardener_state import journal_lines

    assert journal_lines("uno\ndue\ntre\n") == ["uno", "due", "tre"]
    assert journal_lines("uno\ndue\ntre") == ["uno", "due", "tre"]
    assert journal_lines("") == []
    assert journal_lines("\n") == [""]
    # CRLF identico a ``splitlines()``: un diario già letto non deve rileggersi
    # per il solo aggiornamento del codice.
    assert journal_lines("uno\r\ndue\r\n") == ["uno", "due"]
    # I separatori esotici restano **dentro** la riga in cui il file li ha messi.
    for exotic in ("\v", "\f", "\x1c", "\x85", "\u2028", "\u2029"):
        assert journal_lines(f"uno{exotic}due\n") == [f"uno{exotic}due"]


def test_an_exotic_separator_does_not_invent_an_entry(tmp_path) -> None:
    """Lo stesso, visto dal cursore: una voce sola, e un cursore che concorda col
    numero di ``\\n`` del file. Raggiungibile solo da uno scrittore estraneo —
    ``journal_append`` normalizza con ``str.split()``, che quei caratteri li
    mangia — ma il diario è testo copiato da mezzo mondo."""
    project = _project(tmp_path)
    page = project / "raw" / "journal" / "20260822.md"
    page.write_text("# 2026-08-22\n\n- 09:00 — Nakasendo\u2028il vecchio\n", encoding="utf-8")

    delta = read_journal_delta(project, GardenerState())

    assert delta.line_count == 1
    assert delta.files[0].cursor_after == page.read_text(encoding="utf-8").count("\n")


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
            "version": 2,
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


# ── I due orologi: registrato e tentato ──────────────────────────────────────


def test_an_attempt_stamps_the_clock_without_moving_the_cursor(tmp_path) -> None:
    """Il gemello di ``advanced``, ed è tutto il punto della correzione.

    ``partial_write`` e ``commit_failed`` tengono il cursore fermo di proposito —
    ci sono righe non promosse che devono tornare — e la passata va segnata
    comunque, altrimenti «tenere il cursore» diventa «rifare la passata ogni
    mezz'ora per sempre».
    """
    state = GardenerState(
        cursor={"raw/journal/20260822.md": 4},
        last_run_at="2026-08-20T09:00:00",
        witness={"raw/journal/20260822.md": "0123456789abcdef"},
    )

    after = state.attempted(at=datetime(2026, 8, 23, 21, 0, 0))

    assert after.cursor == state.cursor and after.witness == state.witness
    assert after.last_run_at == "2026-08-20T09:00:00"
    assert after.last_attempt_at == "2026-08-23T21:00:00"
    assert after.failures == 1


def test_a_registered_pass_stamps_both_clocks_and_clears_the_streak(tmp_path) -> None:
    """Una passata riuscita è anche una passata tentata, e chiude la serie.

    Senza l'azzeramento il contatore salirebbe per sempre e l'allarme partirebbe
    su un progetto sano; senza il secondo timbro la registrazione di un successo
    resterebbe un tentativo vecchio."""
    project = _project(tmp_path)
    _journal(project, "20260822", "una voce")
    state = GardenerState(failures=5, last_attempt_at="2026-08-20T09:00:00")
    delta = read_journal_delta(project, state)

    after = state.advanced(delta, at=datetime(2026, 8, 23, 21, 0, 0))

    assert after.failures == 0
    assert after.last_run_at == after.last_attempt_at == "2026-08-23T21:00:00"


def test_the_two_clocks_survive_a_write(tmp_path) -> None:
    """``write_state`` ricostruisce lo stato per potarlo: i campi nuovi devono
    attraversare quella ricostruzione, o il timbro non arriva su disco."""
    project = _project(tmp_path)
    _journal(project, "20260822", "una voce")

    write_state(project, GardenerState(
        cursor={"raw/journal/20260822.md": 1},
        last_attempt_at="2026-08-23T21:00:00",
        failures=2,
    ))

    reread = read_state(project)
    assert reread.last_attempt_at == "2026-08-23T21:00:00" and reread.failures == 2


def test_a_state_written_before_the_two_clocks_is_read_not_thrown_away(tmp_path) -> None:
    """**Il test che paga il non aver bumpato ``_STATE_VERSION``.**

    Uno stato che il gate di versione rifiuta vale «stato vuoto», cioè rilettura
    del diario da capo: su un telefono con dei progetti sono duecento righe
    ripromosse e una passata LLM per niente. I due campi nuovi hanno un default
    sicuro — nessun tentativo, zero insuccessi — che è esattamente il
    comportamento di prima, quindi il file di ieri deve continuare a valere.
    """
    project = _project(tmp_path)
    _journal(project, "20260822", "una voce")
    path = gardener_state_file(project)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "version": 2,
            "cursor": {"raw/journal/20260822.md": 1},
            "last_run_at": "2026-08-22T10:00:00",
            "witness": {"raw/journal/20260822.md": "0123456789abcdef"},
        }),
        encoding="utf-8",
    )

    state = read_state(project)

    assert state.cursor == {"raw/journal/20260822.md": 1}
    assert state.last_attempt_at is None and state.failures == 0


def test_a_nonsense_failure_count_is_dropped_not_trusted(tmp_path) -> None:
    """Un contatore che non è un numero non è un contatore: azzerarlo ritarda un
    allarme, fidarsene lo farebbe partire su un progetto sano."""
    project = _project(tmp_path)
    path = gardener_state_file(project)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"version": 2, "cursor": {}, "failures": "tre"}), encoding="utf-8"
    )

    assert read_state(project).failures == 0


def test_recording_an_attempt_counts_the_series_on_disk(tmp_path) -> None:
    """Il contatore vive nel file, non in memoria: fra due tick non c'è nessun
    processo che si ricordi niente."""
    from jenny.agent.gardener_state import record_attempt

    project = _project(tmp_path)
    _journal(project, "20260822", "una voce")

    assert record_attempt(project) == 1
    assert record_attempt(project) == 2
    assert read_state(project).failures == 2


# ── La misura della mappa che l'ultima passata ha lasciato ───────────────────


def test_the_map_measure_survives_the_state_file(tmp_path) -> None:
    """Il freno del secondo innesco vive su disco, come i due orologi: fra due
    tick non c'è nessun processo che si ricordi niente, e un freno che si perde
    al riavvio è il livelock rimandato."""
    project = _project(tmp_path)
    write_state(project, GardenerState(map_left_at=4321))

    assert read_state(project).map_left_at == 4321


def test_a_state_written_before_the_map_measure_reads_as_armed(tmp_path) -> None:
    """È lo stato di tutti i progetti già sul telefono, e il default deve essere
    **innesco armato**: il costo di sbagliare da questo lato è una passata di
    troppo, dall'altro è la mappa tagliata per sempre. E nessun bump di
    ``_STATE_VERSION``, che costerebbe a ogni progetto una rilettura del diario da
    capo."""
    project = _project(tmp_path)
    path = gardener_state_file(project)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"version": 2, "cursor": {}, "last_run_at": "2026-08-20T10:00:00"}),
        encoding="utf-8",
    )

    state = read_state(project)

    assert state.map_left_at is None
    assert state.last_run_at == "2026-08-20T10:00:00"


def test_a_nonsense_map_measure_reads_as_armed(tmp_path) -> None:
    """Stessa asimmetria: un valore che non è un numero vale «nessuno l'ha ancora
    vista», non «lasciala stare»."""
    project = _project(tmp_path)
    path = gardener_state_file(project)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"version": 2, "cursor": {}, "map_left_at": "grossa"}), encoding="utf-8"
    )

    assert read_state(project).map_left_at is None


def test_a_pass_that_did_not_look_at_the_map_keeps_the_measure(tmp_path) -> None:
    """Il default di ``map_chars`` conserva, non azzera: un innesco che si riarma
    da sé a ogni passata di diario è esattamente il livelock che quel campo esiste
    per chiudere."""
    project = _project(tmp_path)
    _journal(project, "20260822", "una voce")
    write_state(project, GardenerState(map_left_at=9000))

    from jenny.agent.gardener_state import record_attempt

    record_attempt(project)
    assert read_state(project).map_left_at == 9000

    delta = read_journal_delta(project, read_state(project))
    write_state(project, read_state(project).advanced(delta))
    assert read_state(project).map_left_at == 9000
