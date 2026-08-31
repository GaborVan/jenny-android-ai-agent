"""L'output del replay, congelato scenario per scenario.

``replay_transcript_to_ui_messages`` è la funzione più lunga del repo (622
righe): quindici closure su dieci locali mutabili condivise, poi un ciclo con
otto rami. Non è un difetto in sé — è però il posto in cui una ristrutturazione
può cambiare comportamento senza che nessun test mirato se ne accorga, perché
ciò che conta non è un ramo alla volta ma **come i rami interagiscono sullo
stato condiviso**.

Questi non sono test di unità: sono un calco. Ogni scenario è una trascrizione
plausibile, e l'atteso è l'output che la funzione produce **oggi**, verificato a
mano scenario per scenario prima di essere congelato. Servono a una cosa sola:
se una riscrittura cambia qualcosa, qui si vede subito e si vede *cosa*.

Se un cambio è voluto, si aggiorna l'atteso **e si dice nel commit perché** —
un calco che si riscrive in silenzio non protegge niente.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jenny.webui.transcript_replay import replay_transcript_to_ui_messages

GOLDEN = Path(__file__).parent / "data" / "transcript_replay_golden.json"


def _turn(turn_id: str, **extra: Any) -> dict[str, Any]:
    return {"chat_id": "c1", "turn_id": turn_id, **extra}


# Ogni scenario nomina *cosa* mette sotto pressione, non solo cosa contiene.
SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "un turno semplice: domanda, streaming, fine": [
        {"event": "user", "text": "ciao", **_turn("t1")},
        {"event": "delta", "text": "ci", **_turn("t1")},
        {"event": "delta", "text": "ao!", **_turn("t1")},
        {"event": "stream_end", "text": "ciao!", **_turn("t1")},
        {"event": "turn_end", "latency_ms": 1200, **_turn("t1")},
    ],
    "il ragionamento precede la risposta e va chiuso": [
        {"event": "user", "text": "pensa", **_turn("t2")},
        {"event": "reasoning_delta", "text": "sto ", **_turn("t2")},
        {"event": "reasoning_delta", "text": "valutando", **_turn("t2")},
        {"event": "reasoning_end", **_turn("t2")},
        {"event": "delta", "text": "ecco", **_turn("t2")},
        {"event": "stream_end", "text": "ecco", **_turn("t2")},
        {"event": "turn_end", **_turn("t2")},
    ],
    "ragionamento senza risposta: il segnaposto va potato": [
        {"event": "user", "text": "?", **_turn("t3")},
        {"event": "reasoning_delta", "text": "mmm", **_turn("t3")},
        {"event": "reasoning_end", **_turn("t3")},
        {"event": "turn_end", **_turn("t3")},
    ],
    "modifiche a file: due sullo stesso path si fondono": [
        {"event": "user", "text": "modifica", **_turn("t4")},
        {"event": "file_edit", "edits": [
            {"path": "SOUL.md", "added": 2, "deleted": 0, "status": "editing"},
        ], **_turn("t4")},
        {"event": "file_edit", "edits": [
            {"path": "SOUL.md", "added": 3, "deleted": 1, "status": "done"},
        ], **_turn("t4")},
        {"event": "stream_end", "text": "fatto", **_turn("t4")},
        {"event": "turn_end", **_turn("t4")},
    ],
    "allegati: la media sopprime fino a turn_end": [
        {"event": "user", "text": "guarda", "media_paths": ["a.png"], **_turn("t5")},
        {"event": "message", "text": "immagine", "media_paths": ["b.png"], **_turn("t5")},
        {"event": "delta", "text": "ignorato", **_turn("t5")},
        {"event": "stream_end", "text": "ignorato", **_turn("t5")},
        {"event": "turn_end", **_turn("t5")},
    ],
    "turno interrotto: l'assistente a metà va retrocesso": [
        {"event": "user", "text": "primo", **_turn("t6")},
        {"event": "delta", "text": "sto rispond", **_turn("t6")},
        {"event": "user", "text": "secondo", **_turn("t7")},
        {"event": "stream_end", "text": "risposta al secondo", **_turn("t7")},
        {"event": "turn_end", **_turn("t7")},
    ],
    "due turni di fila, ognuno col suo id": [
        {"event": "user", "text": "uno", **_turn("t8")},
        {"event": "stream_end", "text": "risposta uno", **_turn("t8")},
        {"event": "turn_end", **_turn("t8")},
        {"event": "user", "text": "due", **_turn("t9")},
        {"event": "stream_end", "text": "risposta due", **_turn("t9")},
        {"event": "turn_end", **_turn("t9")},
    ],
    "eventi malformati in mezzo a un turno buono": [
        {"event": "user", "text": "ok", **_turn("t10")},
        {"event": "delta", "text": 42, **_turn("t10")},
        {"event": "sconosciuto", "text": "rumore"},
        {"chat_id": "c1", "text": "senza event"},
        {"event": "file_edit", "edits": "non-una-lista", **_turn("t10")},
        {"event": "stream_end", "text": "ok", **_turn("t10")},
        {"event": "turn_end", **_turn("t10")},
    ],
    "trascrizione vuota": [],
}


# ``id`` viene da ``uuid4`` e ``createdAt`` da ``time.time()``: confrontarli
# alla lettera renderebbe questo file rosso a ogni giro. Si canonicalizza invece
# di ignorarli, perché **non sono rumore**: l'identità di un id dice quale
# messaggio un segmento riferisce, e i ``createdAt`` dicono l'ordine. Ogni id
# distinto diventa un gettone stabile nell'ordine di prima apparizione, e ogni
# timestamp il suo scarto dal primo — così una relazione che cambia si vede, e
# una randomizzazione no.
_VOLATILE_IDS = ("id", "activitySegmentId", "fileEditSegmentId", "replyTo")


def _canonical(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tokens: dict[str, str] = {}

    def token(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        if value not in tokens:
            # Il prefisso resta: distingue un segnaposto di buffer da un
            # messaggio utente, ed è metà dell'informazione.
            prefix = value.split("-", 1)[0]
            tokens[value] = f"<{prefix}#{len(tokens)}>"
        return tokens[value]

    base: int | None = None
    out = []
    for msg in messages:
        row = dict(msg)
        for key in _VOLATILE_IDS:
            if key in row:
                row[key] = token(row[key])
        if isinstance(row.get("createdAt"), int):
            base = row["createdAt"] if base is None else base
            row["createdAt"] = row["createdAt"] - base
        out.append(row)
    return out


def _actual() -> dict[str, Any]:
    return {
        name: _canonical(replay_transcript_to_ui_messages(lines))
        for name, lines in SCENARIOS.items()
    }


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_replay_matches_the_frozen_output(scenario: str) -> None:
    assert GOLDEN.is_file(), (
        f"manca {GOLDEN.name}: rigenerarlo con "
        "`python -m tests.webui.test_transcript_replay_characterization` e "
        "**rileggerlo** prima di committarlo."
    )
    golden = json.loads(GOLDEN.read_text("utf-8"))
    assert scenario in golden, f"scenario nuovo senza atteso: {scenario}"

    produced = _canonical(replay_transcript_to_ui_messages(SCENARIOS[scenario]))
    assert produced == golden[scenario], (
        f"il replay è cambiato per «{scenario}». Se il cambio è voluto, "
        "aggiorna il file di riferimento e spiega nel commit cosa cambia per "
        "chi ricarica una conversazione."
    )


def test_every_scenario_has_a_frozen_expectation() -> None:
    """Uno scenario aggiunto senza atteso passerebbe senza misurare niente."""
    golden = json.loads(GOLDEN.read_text("utf-8"))
    assert set(golden) == set(SCENARIOS), (
        f"scenari senza atteso: {sorted(set(SCENARIOS) - set(golden))}; "
        f"attesi senza scenario: {sorted(set(golden) - set(SCENARIOS))}"
    )


if __name__ == "__main__":  # pragma: no cover - rigenerazione manuale
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(_actual(), ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"scritto {GOLDEN}")
