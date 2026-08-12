"""Test per ``looks_like_user_question`` — il segnale "sto aspettando una risposta".

È l'euristica che impedisce a un goal sostenuto di spronarsi da solo quando la
risposta finale è una domanda all'utente (v. ``AgentRunner._goal_continue_allowed``).
Il caso di riferimento è il messaggio reale che il 2026-08-12 ha innescato 9
continuation di fila: la domanda sta in mezzo al testo, non in chiusura.
"""

from __future__ import annotations

import pytest

from jenny.utils.runtime import looks_like_user_question

_INCIDENT_MESSAGE = (
    "ok papi, si parte 😏 prima domanda:\n\n"
    "**cosa dovrebbe fare questa app? cosa vuoi vedere quando la apri?**\n\n"
    "dammi un'idea anche vaga — es. \"una lista della spesa\", \"il tracker delle mie "
    "piante\" — e da lì costruiamo."
)


@pytest.mark.parametrize(
    "text",
    [
        _INCIDENT_MESSAGE,
        "cosa vuoi vedere quando la apri? 😏",
        "Which one do you want, A or B?",
        "domanda in mezzo? poi continuo a parlare senza chiedere altro",
        "全角？",
    ],
)
def test_detects_questions(text: str) -> None:
    assert looks_like_user_question(text) is True


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "fatto: ho scritto app.json e index.html, passo alla validazione.",
        "Ho annaffiato l'albinella. Prossimo step: il basilico.",
    ],
)
def test_ignores_statements(text: str | None) -> None:
    assert looks_like_user_question(text) is False
