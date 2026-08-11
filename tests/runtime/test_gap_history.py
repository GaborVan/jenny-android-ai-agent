"""Lo storico dei buchi di attività: l'unica prova che il telefono l'ha uccisa.

Il modulo non impedisce il kill del gateway — nessun codice applicativo può —
ma è quello che lo rende osservabile a posteriori, quindi le sue regole vanno
tenute ferme. Sono tre, e nessuna fallirebbe rumorosamente:

* un buco si registra **una volta sola**: senza ``last_probe_at_ms`` che avanza
  anche quando non si registra niente, ogni riavvio del gateway rivaluterebbe
  la stessa fotografia e lo stesso downtime comparirebbe nel pannello una volta
  per riavvio;
* una durata non credibile non è un buco corto o lungo, è una misura non fatta:
  l'orologio di parete salta (NTP, fuso, ora messa a mano) e un salto
  all'indietro darebbe una durata negativa, uno in avanti un downtime di anni;
* niente qui solleva mai. È telemetria: perderla non è un motivo per far
  fallire l'avvio del gateway o l'apertura delle impostazioni.
"""

from __future__ import annotations

import json
from pathlib import Path

from jenny.runtime import gap_history

_MINUTE_MS = 60_000


def _probe(*, minutes: int, ended_ms: int = 10_000_000_000) -> tuple[int, int]:
    """Fotografia (battito osservato, istante dell'osservazione)."""
    return ended_ms - minutes * _MINUTE_MS, ended_ms


# -- registrazione -----------------------------------------------------------


def test_a_gap_over_the_threshold_is_recorded_and_persisted(tmp_path: Path) -> None:
    recorded = gap_history.record_probe(tmp_path, _probe(minutes=252), threshold_min=60)

    assert recorded is not None
    assert recorded["duration_ms"] == 252 * _MINUTE_MS
    assert gap_history.load_history(tmp_path)["gaps"] == [recorded]


def test_a_gap_under_the_threshold_is_not_recorded(tmp_path: Path) -> None:
    assert gap_history.record_probe(tmp_path, _probe(minutes=59), threshold_min=60) is None
    assert gap_history.load_history(tmp_path)["gaps"] == []


def test_the_same_probe_is_only_recorded_once(tmp_path: Path) -> None:
    """Il caso che il pannello mostrerebbe come quattro notti di downtime.

    La fotografia la scatta ``MainActivity`` e resta nelle preferenze finché
    non ne arriva un'altra: un gateway che riparte tre volte la rilegge tre
    volte, e senza il progresso di ``last_probe_at_ms`` la registrerebbe tre
    volte.
    """
    probe = _probe(minutes=120)

    first = gap_history.record_probe(tmp_path, probe, threshold_min=60)
    again = gap_history.record_probe(tmp_path, probe, threshold_min=60)

    assert first is not None
    assert again is None
    assert len(gap_history.load_history(tmp_path)["gaps"]) == 1


def test_a_probe_under_the_threshold_still_advances_the_watermark(tmp_path: Path) -> None:
    """Anche una fotografia scartata è stata valutata: non va rivalutata."""
    _, ended_ms = _probe(minutes=10)

    gap_history.record_probe(tmp_path, _probe(minutes=10), threshold_min=60)

    assert gap_history.load_history(tmp_path)["last_probe_at_ms"] == ended_ms


def test_no_probe_records_nothing_and_writes_no_file(tmp_path: Path) -> None:
    """Fuori da Android non c'è nessuna fotografia: niente file, niente storico."""
    assert gap_history.record_probe(tmp_path, None, threshold_min=60) is None
    assert not gap_history.history_path(tmp_path).exists()


# -- orologio di parete che salta --------------------------------------------


def test_a_backwards_clock_jump_is_not_a_gap(tmp_path: Path) -> None:
    """Il "prima" successivo al "dopo": sottrazione negativa, misura non fatta."""
    started_ms = 10_000_000_000
    recorded = gap_history.record_probe(
        tmp_path, (started_ms, started_ms - 5 * _MINUTE_MS), threshold_min=60
    )

    assert recorded is None
    assert gap_history.load_history(tmp_path)["gaps"] == []


def test_an_absurd_duration_is_not_a_gap(tmp_path: Path) -> None:
    """Oltre un mese non è downtime, è una data sbagliata al boot."""
    recorded = gap_history.record_probe(
        tmp_path, _probe(minutes=40 * 24 * 60), threshold_min=60
    )

    assert recorded is None
    assert gap_history.load_history(tmp_path)["gaps"] == []


# -- lettura -----------------------------------------------------------------


def test_recent_gaps_are_newest_first_and_capped(tmp_path: Path) -> None:
    """Il pannello mostra gli ultimi, e "ultimi" vuol dire in cima."""
    for index in range(1, 8):
        gap_history.record_probe(
            tmp_path,
            _probe(minutes=60 + index, ended_ms=10_000_000_000 + index * _MINUTE_MS),
            threshold_min=60,
        )

    recent = gap_history.recent_gaps(tmp_path, limit=5)

    assert len(recent) == 5
    durations = [gap["duration_ms"] for gap in recent]
    assert durations == sorted(durations, reverse=True)
    assert durations[0] == 67 * _MINUTE_MS


def test_the_history_never_grows_past_its_cap(tmp_path: Path) -> None:
    """Il file è letto a ogni apertura delle impostazioni: deve restare piccolo."""
    for index in range(1, gap_history.MAX_GAPS + 6):
        gap_history.record_probe(
            tmp_path,
            _probe(minutes=61, ended_ms=10_000_000_000 + index * _MINUTE_MS),
            threshold_min=60,
        )

    assert len(gap_history.load_history(tmp_path)["gaps"]) == gap_history.MAX_GAPS


def test_recent_gaps_on_a_workspace_without_history_is_empty(tmp_path: Path) -> None:
    assert gap_history.recent_gaps(tmp_path, limit=5) == []


# -- robustezza --------------------------------------------------------------


def test_a_corrupt_history_reads_as_an_empty_one(tmp_path: Path) -> None:
    path = gap_history.history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all", encoding="utf-8")

    assert gap_history.load_history(tmp_path) == {
        "version": 1,
        "gaps": [],
        "last_probe_at_ms": 0,
    }


def test_malformed_entries_are_dropped_instead_of_reaching_the_panel(tmp_path: Path) -> None:
    """Una voce senza durata diventerebbe "NaN" in mezzo alla lista."""
    path = gap_history.history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_probe_at_ms": 5,
                "gaps": [
                    "not a dict",
                    {"start_ms": 1, "end_ms": 2},
                    {"start_ms": 1, "end_ms": 0, "duration_ms": 0},
                    {"start_ms": 1, "end_ms": 3, "duration_ms": 2},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert gap_history.load_history(tmp_path)["gaps"] == [
        {"start_ms": 1, "end_ms": 3, "duration_ms": 2}
    ]


def test_a_history_that_cannot_be_written_does_not_break_the_startup(tmp_path: Path) -> None:
    """Lo storico vive sotto ``state/``: se là c'è un file, la scrittura fallisce."""
    (tmp_path / "state").write_text("I am a file, not a directory", encoding="utf-8")

    assert gap_history.record_probe(tmp_path, _probe(minutes=120), threshold_min=60) is not None
