"""Rinominare una cartella non perde più la sua chat.

Passo **7.2** e **7.3** di ``roadmap/progetti-passi.md``, strada **B**.

Il piano diceva «id stabile, e le chat passano a ``project:<id>``». Quella strada
**inverte l'invariante del 21/08** — la cartella si deduce dalla chiave e non è
scritta da nessuna parte — rimettendo il secondo dato che quella decisione aveva
evitato, e trasformando i nomi dei file in ``project_3f9a2c1b7e04.jsonl``.

Qui l'indirizzo resta il nome della cartella, e l'id serve solo a **ritrovare**
una chat orfana. La riparazione vive dentro il rifiuto del passo 6, che era già
l'unico punto che scopre che una cartella legata è sparita: quindi gira solo
quando qualcosa è già andato storto, e non sul percorso di ogni turno.

Le due asserzioni che pesano più delle altre:

- ``test_nothing_moves_when_the_destination_is_taken``: ci si arriva scambiando
  due nomi, e spostare comunque mescolerebbe due storie;
- ``test_a_partial_move_is_impossible``: sessione sotto un nome e trascrizione
  sotto un altro è lo schermo di una chat con la memoria di un'altra — il difetto
  che il passo 1 ha faticato a evitare.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.session.project_rename import (
    PROJECT_WIKI_ID_KEY,
    follow_renamed_project,
    project_trace_paths,
)

WIKI_ID = "3f9a2c1b7e04"


@pytest.fixture
def loop(tmp_path: Path, monkeypatch) -> AgentLoop:
    monkeypatch.setattr(
        "jenny.config.paths.get_webui_dir", lambda: _ensure(tmp_path / ".jenny" / "webui")
    )
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model"
    )


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def published(loop: AgentLoop) -> list[str]:
    sent: list[str] = []

    async def capture(message) -> None:
        sent.append(message.content)

    loop.bus.publish_outbound = capture  # type: ignore[assignment]
    return sent


def _wiki(root: Path, name: str, wiki_id: str | None = WIKI_ID) -> Path:
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True)
    (project / "wiki" / "index.md").write_text("# indice\n", encoding="utf-8")
    head = f"---\nid: {wiki_id}\n---\n\n" if wiki_id else "---\nsummary: x\n---\n\n"
    (project / "AGENTS.md").write_text(head + f"# {name}\n", encoding="utf-8")
    return project


def _msg() -> InboundMessage:
    return InboundMessage(
        channel="websocket", chat_id="default", sender_id="u", content="ciao"
    )


def _traces(loop: AgentLoop, key: str) -> list[Path]:
    """Le tracce **esistenti**, create a mano per simulare una chat vissuta."""
    made = []
    for path in project_trace_paths(loop.workspace, key):
        if path.suffix == ".segments":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        made.append(path)
    return made


# ── 7.2 — la sessione si annota di chi è ─────────────────────────────────


def test_a_project_turn_records_its_wiki_id(loop: AgentLoop, tmp_path: Path) -> None:
    _wiki(tmp_path, "patreon")
    loop._remember_project_id("project:patreon")
    assert loop.sessions.get_or_create("project:patreon").metadata[PROJECT_WIKI_ID_KEY] == WIKI_ID


def test_it_is_written_once_and_not_re_read(loop: AgentLoop, tmp_path: Path) -> None:
    """Il costo è una lettura di frontmatter al primo turno, e zero dopo.

    Provato cambiando l'id **nel file** invece di cancellarlo: cancellarlo
    lasciava passare anche una versione che rilegge a ogni turno, perché la
    seconda lettura non trovava niente da riscrivere.
    """
    project = _wiki(tmp_path, "patreon")
    loop._remember_project_id("project:patreon")
    (project / "AGENTS.md").write_text("---\nid: ffffffffffff\n---\n", encoding="utf-8")

    loop._remember_project_id("project:patreon")

    metadata = loop.sessions.get_or_create("project:patreon").metadata
    assert metadata[PROJECT_WIKI_ID_KEY] == WIKI_ID, (
        "l'id si annota una volta: rileggerlo a ogni turno è I/O per niente, e su un "
        "file che l'utente può cambiare sotto i piedi"
    )


def test_a_wiki_without_an_id_records_nothing(loop: AgentLoop, tmp_path: Path) -> None:
    """Non è un errore: quella chat si comporta come prima del passo 7."""
    _wiki(tmp_path, "senza", wiki_id=None)
    loop._remember_project_id("project:senza")
    assert PROJECT_WIKI_ID_KEY not in loop.sessions.get_or_create("project:senza").metadata


@pytest.mark.parametrize("key", ["unified:default", "cron:update_check", ""])
def test_nothing_is_recorded_outside_a_project(loop: AgentLoop, key: str) -> None:
    loop._remember_project_id(key)
    if key:
        assert PROJECT_WIKI_ID_KEY not in loop.sessions.get_or_create(key).metadata


# ── 7.3 — il rifiuto che ripara ──────────────────────────────────────────


async def test_a_renamed_folder_takes_its_chat_with_it(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    _wiki(tmp_path, "vecchio")
    loop._remember_project_id("project:vecchio")
    made = _traces(loop, "project:vecchio")
    # Il rinomino, fatto fuori da Jenny: è il caso reale.
    (tmp_path / "wikis" / "vecchio").rename(tmp_path / "wikis" / "nuovo")

    refused = await loop._refuse_missing_project(_msg(), "project:vecchio")

    assert refused is True, "il turno non parte: il messaggio non è stato letto"
    assert "nuovo" in published[0] and "renamed" in published[0].lower()
    assert PROJECT_WIKI_ID_KEY not in loop.sessions.get_or_create("project:vecchio").metadata, (
        "la sessione vecchia era in cache e i suoi file hanno cambiato nome: tenerla "
        "vorrebbe dire riscriverla al nome vecchio al primo salvataggio, e ritrovarsi "
        "la storia in due posti"
    )
    old = [p for p in project_trace_paths(loop.workspace, "project:vecchio") if p.exists()]
    new = [p for p in project_trace_paths(loop.workspace, "project:nuovo") if p.exists()]
    assert old == [], f"tracce rimaste sotto il nome vecchio: {[p.name for p in old]}"
    # Quattro: sessione, trascrizione, thread legacy e record dei subagent. La
    # ``.segments`` non la crea questo test perché è una directory.
    assert len(new) == len(made), "tutte le tracce esistenti devono essere arrivate"


async def test_nothing_moves_when_the_destination_is_taken(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """Lo scambio di due nomi: meglio due chat da rinominare a mano che due storie mescolate."""
    _wiki(tmp_path, "a")
    loop._remember_project_id("project:a")
    _traces(loop, "project:a")
    _traces(loop, "project:b")           # "b" ha già una conversazione sua
    (tmp_path / "wikis" / "a").rename(tmp_path / "wikis" / "b")

    await loop._refuse_missing_project(_msg(), "project:a")

    assert [p for p in project_trace_paths(loop.workspace, "project:a") if p.exists()]
    assert "already has a conversation of its own" in published[0]


async def test_a_partial_move_is_impossible(loop: AgentLoop, tmp_path: Path) -> None:
    """Tutto o niente: si controllano tutte le destinazioni, poi si sposta.

    Una sessione sotto un nome e la sua trascrizione sotto un altro è lo schermo
    di una chat con la memoria di un'altra.
    """
    _wiki(tmp_path, "src")
    made = _traces(loop, "project:src")
    # Una sola destinazione occupata, e non la prima: se lo spostamento fosse
    # incrementale, le precedenti sarebbero già partite.
    project_trace_paths(loop.workspace, "project:dst")[-1].write_text("x\n", encoding="utf-8")

    moved, why = follow_renamed_project(loop.workspace, "project:src", "project:dst")

    assert moved is False and why
    assert all(p.exists() for p in made), "nessuna traccia deve essersi mossa"


async def test_a_chat_that_never_recorded_an_id_gets_the_plain_refusal(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    (tmp_path / "wikis").mkdir()
    refused = await loop._refuse_missing_project(_msg(), "project:mai-vista")
    assert refused is True
    assert "never recorded" in published[0]
    assert "renaming it back" in published[0]


async def test_a_folder_whose_id_is_nowhere_gets_the_plain_refusal(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """La wiki è stata cancellata, non rinominata: non c'è niente da inseguire."""
    _wiki(tmp_path, "sparita")
    loop._remember_project_id("project:sparita")
    import shutil

    shutil.rmtree(tmp_path / "wikis" / "sparita")

    await loop._refuse_missing_project(_msg(), "project:sparita")

    assert "no folder here claims" in published[0]


async def test_an_ambiguous_id_is_not_followed(
    loop: AgentLoop, published: list[str], tmp_path: Path
) -> None:
    """Due wiki con lo stesso id: si rifiuta invece di scegliere (passo 6)."""
    _wiki(tmp_path, "originale")
    loop._remember_project_id("project:originale")
    _traces(loop, "project:originale")
    _wiki(tmp_path, "copia")             # stesso id, arrivata copiando la cartella
    (tmp_path / "wikis" / "originale").rename(tmp_path / "wikis" / "rinominata")

    await loop._refuse_missing_project(_msg(), "project:originale")

    assert [p for p in project_trace_paths(loop.workspace, "project:originale") if p.exists()]
    assert "no folder here claims" in published[0]


# ── L'elenco delle tracce ────────────────────────────────────────────────


def test_the_trace_list_covers_the_three_that_move(loop: AgentLoop) -> None:
    """La quarta traccia vive **dentro** la cartella, quindi si è già spostata da sé.

    ``<progetto>/.jenny/tool-results/project_<nome>/`` viaggia col rinomino; il
    suo nome resta quello vecchio e ``_cleanup_tool_result_buckets`` lo rimuove
    al primo turno. Se un giorno nascesse una quinta traccia *fuori* dalla
    cartella, va aggiunta qui — ed è questo il test che se ne accorge.
    """
    paths = project_trace_paths(loop.workspace, "project:patreon")
    kinds = {p.parent.name for p in paths}
    assert "sessions" in kinds
    assert "webui" in kinds
    assert "records" in kinds
    assert all("project_patreon" in p.name for p in paths)
