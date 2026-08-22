"""Inseguire una wiki che ha cambiato nome, portandole dietro la sua chat.

Passo **7** di ``roadmap/progetti-passi.md``, strada **B**.

L'indirizzo di una conversazione di progetto resta il **nome della cartella**, e
non diventa un id: la cartella continua a dedursi dalla chiave, cosi' come
deciso il 21/08, e i file di sessione continuano a chiamarsi
``project_patreon.jsonl`` invece di ``project_3f9a2c1b7e04.jsonl`` — che su
questo progetto non e' estetica, e' lo strumento con cui si guarda cosa e'
successo davvero.

L'id serve a una cosa sola: **ritrovare** una chat rimasta orfana. Ogni sessione
di progetto registra l'id della wiki a cui appartiene; quando la cartella non c'e'
piu' si cerca fra le wiki quella con quell'id, e se si trova le tracce della chat
prendono il nome nuovo. Cosi' la riparazione gira **solo quando qualcosa e' gia'
andato storto**, invece di mettersi sul percorso di ogni turno.

**Le tracce sono tre, non quattro.** La quarta —
``<progetto>/.jenny/tool-results/project_<nome>/`` — vive *dentro* la cartella
della wiki, quindi con il rinomino si e' già spostata da sé; il suo nome resta
quello vecchio, ma ``_cleanup_tool_result_buckets`` rimuove i bucket che non
appartengono alla sessione corrente, quindi si ripulisce da sola al primo turno.

**Se una destinazione e' occupata non si sposta niente.** Ci si arriva scambiando
due nomi (A→B, B→A): due sessioni vorrebbero lo stesso file. Meglio due chat da
rinominare a mano che due storie mescolate — la lezione del passo 6.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from jenny.session.manager import SessionManager

# Chiave nei metadati di sessione in cui vive l'id della wiki di appartenenza.
# Scritta una volta, al primo turno in cui la cartella c'e' — v.
# ``AgentLoop._remember_project_id``.
PROJECT_WIKI_ID_KEY = "project_wiki_id"


def project_trace_paths(workspace: Path, session_key: str) -> list[Path]:
    """I percorsi che portano il nome di *session_key*, esistenti o no.

    Sono le tracce che un rinomino deve portarsi dietro. L'elenco sta qui e non
    sparso fra i tre sottosistemi che le scrivono: quando ne nascera' una quarta,
    questo e' il posto in cui aggiungerla — e
    ``tests/session/test_project_session_files.py`` e' quello che se ne accorge.
    """
    # Import locali, e non e' igiene: ``agent/subagent_records`` tira dentro
    # ``agent/loop``, che importa questo modulo — un ciclo, e un modulo di
    # ``session/`` che importa ``agent/`` a livello di modulo e' anche
    # un'inversione di layer. Preso da ``tests/session/test_cold_imports.py``,
    # che prova ogni modulo come **primo** import di un interprete.
    from jenny.agent.subagent_records import _RECORDS_DIRNAME, SUBAGENTS_DIRNAME
    from jenny.config.paths import get_webui_dir

    stem = SessionManager.safe_key(session_key)
    webui_stem = SessionManager.safe_key(f"websocket:{session_key}")
    webui = get_webui_dir()
    return [
        workspace / "sessions" / f"{stem}.jsonl",
        webui / f"{webui_stem}.jsonl",
        webui / f"{webui_stem}.segments",
        webui / f"{webui_stem}.json",  # thread legacy, se questa installazione ne ha uno
        workspace / SUBAGENTS_DIRNAME / _RECORDS_DIRNAME / f"{stem}.jsonl",
    ]


def follow_renamed_project(
    workspace: Path, old_key: str, new_key: str
) -> tuple[bool, str | None]:
    """Rinomina le tracce di *old_key* in quelle di *new_key*.

    Ritorna ``(spostato, motivo)``: ``motivo`` e' valorizzato solo quando **non**
    si e' spostato niente, e dice perche' in una forma che si possa mostrare.

    Tutto o niente: si controllano prima tutte le destinazioni, poi si sposta.
    Uno spostamento a metà lascerebbe la sessione sotto un nome e la sua
    trascrizione sotto un altro, che e' esattamente lo stato che il passo 1 ha
    faticato a evitare (lo schermo di una chat e la memoria di un'altra).
    """
    if old_key == new_key:
        return False, None
    pairs = [
        (src, dst)
        for src, dst in zip(
            project_trace_paths(workspace, old_key),
            project_trace_paths(workspace, new_key),
        )
        if src.exists()
    ]
    if not pairs:
        return False, "there was nothing recorded under the old name"
    occupied = [dst.name for _, dst in pairs if dst.exists()]
    if occupied:
        # Il caso dello scambio di due nomi. Non si sceglie: si dice.
        return False, (
            "the new name already has a conversation of its own "
            f"({', '.join(sorted(occupied))})"
        )
    for src, dst in pairs:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        except OSError:
            # Uno spostamento a metà è peggio di nessuno, ma qui è già
            # avvenuto: si dice a voce alta invece di far finta.
            logger.opt(exception=True).error(
                "Rinomino parziale delle tracce di {} verso {}: {} non spostato",
                old_key, new_key, src.name,
            )
            return False, "moving the conversation's files failed halfway"
    logger.info("Chat di progetto seguita: {} -> {}", old_key, new_key)
    return True, None
