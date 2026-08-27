"""Una cartella legata che è sparita fa rifiutare la chat, a voce.

Passo **6** di ``roadmap/progetti-passi.md``.

Prima del passo 6 lo scope veniva costruito comunque e puntava al posto che
manca. La parte importante era già giusta — **niente fallback sulla radice
personale**, che avrebbe messo il lavoro di un progetto fra i file personali — ma
il risultato era che il turno partiva, il modello leggeva il contesto,
pianificava, e solo alla prima scrittura scopriva che non c'era niente sotto.

Qui quel comportamento diventa una frase, e la frase dice **come si recupera**:
fino al passo 7 l'indirizzo di un progetto è il nome della sua cartella, quindi
la causa quasi certa è un rinomino e la cura è rimettere il nome di prima.

Il test che conta è ``test_the_personal_chat_is_not_affected``: un controllo
scritto un filo troppo largo rifiuterebbe *ogni* turno la cui radice non è una
cartella di progetto, cioè tutti.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus


@pytest.fixture
def loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
    )


@pytest.fixture
def published(loop: AgentLoop) -> list[str]:
    sent: list[str] = []

    async def capture(message) -> None:
        sent.append(message.content)

    loop.bus.publish_outbound = capture  # type: ignore[assignment]
    return sent


def _msg() -> InboundMessage:
    return InboundMessage(
        channel="websocket", chat_id="default", sender_id="u", content="ciao"
    )


# ── Rifiuta, e dice come si recupera ─────────────────────────────────────


async def test_a_vanished_folder_stops_the_turn(loop: AgentLoop, published: list[str]) -> None:
    refused = await loop._refuse_missing_project(_msg(), "project:sparita")
    assert refused is True, "True vuol dire «già risposto»: nessun turno parte"
    assert published, "e qualcosa deve essere stato detto"


async def test_the_refusal_names_the_project_and_the_way_back(
    loop: AgentLoop, published: list[str]
) -> None:
    """Il nome due volte apposta: una per dire *quale*, una per dire *cosa rimettere*."""
    await loop._refuse_missing_project(_msg(), "project:patreon")
    text = published[0]
    assert "patreon" in text
    assert "renam" in text.lower(), (
        "fino al passo 7 l'indirizzo è il nome della cartella: il rinomino è la causa "
        "probabile ed è anche la cura, e il rifiuto è l'unico posto in cui dirlo"
    )
    assert "chip" in text.lower(), "e come si va altrove"


async def test_it_says_the_message_was_not_read(
    loop: AgentLoop, published: list[str]
) -> None:
    """Senza questo, l'utente non sa se deve riscriverlo."""
    await loop._refuse_missing_project(_msg(), "project:sparita")
    assert "not read your message" in published[0].lower()


# ── E non tocca nient'altro ──────────────────────────────────────────────


async def test_a_project_that_exists_passes_through(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    (tmp_path / "wikis" / "vera" / "wiki").mkdir(parents=True)
    assert await loop._refuse_missing_project(_msg(), "project:vera") is False
    assert published == []


@pytest.mark.parametrize(
    "key",
    ["unified:default", "websocket:default", "cron:update_check", "dream:default", ""],
    ids=["unificata", "websocket", "cron", "dream", "vuota"],
)
async def test_the_personal_chat_is_not_affected(
    loop: AgentLoop, published: list[str], key: str
) -> None:
    """Il controllo si accende su una chiave ``project:`` e su nient'altro.

    Scritto un filo più largo — «la radice del turno non è una cartella» —
    rifiuterebbe ogni turno personale e interno, cioè tutti: la radice
    dell'installazione non è una cartella di progetto per definizione.
    """
    assert await loop._refuse_missing_project(_msg(), key) is False
    assert published == []


# ── Sta prima di tutto il resto ──────────────────────────────────────────


def test_the_check_runs_before_the_init_expansion() -> None:
    """``/init`` su una cartella sparita deve dare *questo* rifiuto.

    Al contrario si finirebbe a renderizzare il prompt di ``/init`` con un
    percorso che non esiste, e a mandare il modello a scrivere un file dentro
    una cartella che non c'è.
    """
    src = Path("jenny/agent/loop.py").read_text(encoding="utf-8")
    check = src.index("_refuse_missing_project(msg, effective_key)")
    init = src.index("if raw == PROJECT_INIT_COMMAND")
    assert check < init, "il controllo della cartella deve precedere l'espansione di /init"
