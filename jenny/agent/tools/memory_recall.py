"""Recupero dal tier freddo: come si ritrova un fatto che è stato degradato.

La fase 2 ha reso la cancellazione una degradazione, e ha mantenuto la promessa
sul disco — 16 voci spostate in un solo pomeriggio, zero perse. Ma "non è perso"
è un'affermazione sul filesystem, non su ciò che Jenny sa: finché l'unico modo
di rientrarci era un ``grep`` che il modello doveva ricordarsi di fare,
l'archivio era un debito che si accumulava senza che nessuno lo aprisse.

Questo modulo lo salda, e tre scelte lo distinguono dal ``grep`` che sostituisce.

**Nessuna corrispondenza per sottostringa, da nessuna parte.** Era il difetto
vero, non un dettaglio: questa memoria è bilingue, e un fatto scritto in
italiano è invisibile a una domanda in inglese sotto ricerca testuale. In più
``grep`` salta i file grandi in silenzio — un falso negativo che si legge
esattamente come "non l'ho mai saputo". Qui l'elenco torna intero e a scegliere
è il modello, che di lingue ne parla due.

**Nessun secondo modello, e niente ``spawn``.** Il piano prevedeva un subagent
sull'indice, e la macchina esiste; ma ``spawn`` è asincrono per contratto — il
risultato arriva da sé, non si aspetta — e un recupero che risponde tre turni
dopo non è un recupero. Soprattutto: chi deve giudicare la salienza è il modello
che ha in mano la conversazione, perché è la conversazione a dire quale dei 41
fatti c'entra. Un subagent guadagna il suo posto solo quando l'indice smette di
stare in un risultato di tool, che è la stessa soglia oltre la quale il piano
prevede gli embedding.

**Nessun indice su disco.** L'elenco si deriva dalla cartella a ogni chiamata.
Un file di indice sarebbe una seconda verità da tenere allineata, e la prima
volta che va fuori sincrono mente esattamente sul punto in cui questo tool deve
essere affidabile. L'archivio è fatto di file piccoli, uno per voce, proprio
perché rileggerli costi poco.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.memory_archive import ArchivedEntry, list_archived
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema

# Tetto dell'elenco, in caratteri. Non è il costo di un turno: è il costo di un
# turno *in cui il modello ha deciso di cercare*, che è raro, quindi può essere
# generoso. A ~90 caratteri per voce copre qualche centinaio di fatti, cioè molto
# oltre lo stato attuale (41).
_INDEX_MAX_CHARS = 24_000

# Sopra questa frazione del tetto si logga. È il segnale che dice *quando*
# costruire il passaggio LLM sull'indice (fase 7.2), invece di indovinarlo: la
# soglia del piano — "quando l'indice smette di stare in un contesto" — non è
# osservabile se nessuno la misura.
_INDEX_CROWDED_SHARE = 0.75


def _index_line(entry: ArchivedEntry) -> str:
    """Una riga per voce: l'id per richiamarla, e il fatto per riconoscerla.

    Il fatto viene troncato a riga, non a carattere: una voce multi-riga si
    presenta con la prima, che è quella che la introduce. Tagliare a metà frase
    darebbe da leggere un fatto diverso da quello archiviato.
    """
    first = entry.text.strip().splitlines()[0] if entry.text.strip() else ""
    when = f" [{entry.demoted}]" if entry.demoted else ""
    return f"- {entry.id}{when} {first}"


def _render_entry(entry: ArchivedEntry) -> str:
    """Una voce aperta, col suo indirizzo di provenienza.

    ``source``/``heading`` sono la ragione per cui l'archivio li conserva: un
    fatto riletto fra sei mesi senza sapere da quale file e da quale sezione
    veniva è una frase senza contesto, e rimetterlo al suo posto diventa un
    indovinello.
    """
    where = " › ".join(p for p in (entry.source, entry.heading) if p)
    head = f"{entry.id}"
    if where:
        head += f" — from {where}"
    if entry.demoted:
        head += f", demoted {entry.demoted}"
    return f"{head}\n{entry.text.strip()}"


@tool_parameters(
    tool_parameters_schema(
        ids=ArraySchema(
            StringSchema("An entry id from the list"),
            description=(
                "Ids to open in full. Omit to list everything in the archive "
                "first — the list is what you pick ids from."
            ),
        ),
    )
)
class MemoryRecallTool(Tool):
    """Elenca l'archivio, e apre le voci che il modello ha riconosciuto."""

    # Solo l'agente principale. È lì che succede "ti ricordi quando…", ed è un
    # tool di sola lettura: non può rompere niente, quindi non serve la cautela
    # che la fase 1.12 aveva usato al contrario (dare prima a Dream ciò che
    # scrive). A Dream non va: il suo prompt si è già dimostrato sensibile alla
    # superficie che gli si aggiunge, e non c'è ancora una misura che dica che
    # gli serve.
    _scopes = {"core", "orchestrator"}

    def __init__(self, workspace: str | Path):
        self._memory_dir = Path(workspace) / "memory"

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Search long-term memory for facts that were removed from the active "
            "files and moved to the archive. Call it with no arguments to see "
            "every archived fact, one line each, then call it again with the ids "
            "you want in full. "
            "Use it before telling the user you do not remember something, and "
            "whenever a question is about the past — what was decided, what "
            "something used to be, why a thing was set up that way. "
            "The list is not filtered by keyword, so a fact stored in one "
            "language is found by a question asked in another."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, ids: list[str] | None = None, **kwargs: Any) -> str:
        entries = list_archived(self._memory_dir)
        if not entries:
            return (
                "The memory archive is empty: no fact has been demoted yet. "
                "Nothing was lost — there is simply nothing here to recall."
            )
        if ids:
            return self._open(entries, ids)
        return self._list(entries)

    def _open(self, entries: list[ArchivedEntry], ids: list[str]) -> str:
        wanted = [str(i).strip() for i in ids if str(i).strip()]
        by_id = {e.id: e for e in entries}
        found = [_render_entry(by_id[i]) for i in wanted if i in by_id]
        missing = [i for i in wanted if i not in by_id]
        parts = found
        if missing:
            # Un id che non esiste si dice, non si ignora: silenziosamente
            # restituire "solo le altre" farebbe concludere al modello che quel
            # fatto non c'è, che è la bugia precisa da cui parte tutto questo.
            parts.append(
                f"No archived entry has id {', '.join(missing)}. "
                "Call recall with no arguments to see the current ids."
            )
        return "\n\n".join(parts)

    def _list(self, entries: list[ArchivedEntry]) -> str:
        lines: list[str] = []
        used = 0
        for entry in entries:
            line = _index_line(entry)
            if used + len(line) + 1 > _INDEX_MAX_CHARS:
                break
            lines.append(line)
            used += len(line) + 1

        header = (
            f"{len(entries)} facts have been moved out of the active memory files "
            "and kept here. Newest first. Call recall again with the ids you want "
            "in full."
        )
        body = "\n".join(lines)
        if len(lines) < len(entries):
            # Mai troncare in silenzio: un elenco tagliato senza dirlo è
            # indistinguibile da un archivio più piccolo, ed è il modo in cui
            # ``grep`` falliva.
            body += (
                f"\n\n{len(entries) - len(lines)} older entries are not listed here "
                "because the list hit its size limit. They are still in the archive; "
                "say so if the answer might be among them."
            )
        if used > _INDEX_MAX_CHARS * _INDEX_CROWDED_SHARE:
            logger.info(
                "Memory archive index at {}/{} chars over {} entries: past "
                "{:.0%} of the cap, the point where a selection pass over the "
                "index starts to earn its place (plan phase 7.2)",
                used, _INDEX_MAX_CHARS, len(entries), _INDEX_CROWDED_SHARE,
            )
        return f"{header}\n\n{body}"


TOOLS = [MemoryRecallTool]
