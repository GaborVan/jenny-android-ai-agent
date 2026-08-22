"""Le wiki che esistevano prima del passo 7, portate alla forma di oggi.

Passo **7.4** di ``roadmap/progetti-passi.md``. Due cose, entrambe idempotenti e
nessuna delle due distruttiva:

1. ``CLAUDE.md`` diventa ``AGENTS.md``. **Non e' un ritiro di template** —
   ``retire_withdrawn_templates`` serve alla parte ancora identica a un template
   spedito; qui il contenuto l'ha scritto l'utente, quindi e' un rinomino e il
   testo non si tocca. Se ``AGENTS.md`` esiste gia', non si tocca niente: quello
   e' lo stato che i lettori del passo 2.3 sanno disambiguare, e sceglierne uno
   qui vorrebbe dire buttare l'altro.
2. Ogni wiki prende un **id**, se non ce l'ha. Serve solo a ritrovare la propria
   chat dopo un rinomino della cartella (v. ``session/project_rename.py``): non
   e' un indirizzo, non finisce in nessun nome di file, e una wiki senza id
   continua a funzionare come prima.

Gira **a ogni avvio**, come l'estrazione dei template, e per la stessa ragione:
e' l'unico modo in cui arriva su un telefono installato da mesi. Il che vuol dire
che il costo a regime deve essere zero — e lo e': niente id da scrivere, niente
file da rinominare, nessuna scrittura.

Una wiki che non ha **nessun** file di istruzioni ne riceve uno **minimo**: la
frontmatter con l'id piu' un titolo. Non lo scaffold completo, che e' mestiere di
``/init`` o dello scaffolder — v. la decisione del 21/08, ``AGENTS.md`` di un
progetto nasce quasi vuoto.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import (
    LEGACY_WIKI_SCHEMA_FILENAME,
    WIKI_ID_KEY,
    WIKI_SCHEMA_FILENAME,
    discover_wikis,
    is_valid_wiki_id,
    new_wiki_id,
    strip_frontmatter,
    wiki_schema_file,
)

# I due nomi vengono da ``wiki_paths``: la migrazione e' l'unico posto che li
# conosce entrambi come nomi *da scrivere*, e non deve poterne inventare un terzo.
# Lo stesso segnaposto che scrive lo scaffolder, cosi' ``read_wiki_scope`` lo
# riconosce come "da riempire" invece di leggerlo come uno scope vero.
_SUMMARY_PLACEHOLDER = "<one-line scope — shown next to this wiki in wikis/_index.md>"

_AGENTS = WIKI_SCHEMA_FILENAME
_LEGACY = LEGACY_WIKI_SCHEMA_FILENAME


def _title_of(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()


def _rename_legacy(root: Path) -> bool:
    """``CLAUDE.md`` -> ``AGENTS.md``, solo se non c'e' già il secondo."""
    legacy, target = root / _LEGACY, root / _AGENTS
    if not legacy.is_file():
        return False
    if target.exists():
        logger.warning(
            "wiki {}: ha sia {} sia {}, non tocco niente — sceglierne uno butterebbe l'altro",
            root.name, _LEGACY, _AGENTS,
        )
        return False
    legacy.rename(target)
    logger.info("wiki {}: {} rinominato in {}", root.name, _LEGACY, _AGENTS)
    return True


def _ensure_id(root: Path) -> str | None:
    """Scrive un id nella frontmatter del file di istruzioni, se non c'e' già.

    Ritorna l'id **nuovo** quando l'ha scritto, ``None`` quando non c'era niente
    da fare — così il chiamante può dire quante wiki ha davvero toccato.
    """
    schema = wiki_schema_file(root)
    if schema is None:
        wiki_id_value = new_wiki_id()
        # ``summary`` resta il **segnaposto**, non il nome della cartella. Con il
        # nome, ``wikis/_index.md`` mostrerebbe «adhd — adhd», che sembra una
        # descrizione e non lo è: la voce di prima diceva «(no AGENTS.md)», cioè
        # la verità. ``read_wiki_scope`` riconosce i segnaposto fra ``<>`` e
        # continua a dire «(no scope set)» — che è un invito a riempirlo, non
        # una descrizione finta.
        atomic_write(
            root / _AGENTS,
            f"---\n{WIKI_ID_KEY}: {wiki_id_value}\nsummary: {_SUMMARY_PLACEHOLDER}\n---\n\n"
            f"# {_title_of(root.name)}\n",
        )
        logger.info("wiki {}: creato {} minimo con id", root.name, _AGENTS)
        return wiki_id_value

    text = schema.read_text(encoding="utf-8")
    frontmatter, _, _ = strip_frontmatter(text)
    if is_valid_wiki_id((frontmatter or {}).get(WIKI_ID_KEY)):
        return None

    wiki_id_value = new_wiki_id()
    line = f"{WIKI_ID_KEY}: {wiki_id_value}\n"
    if frontmatter is None:
        # Nessuna frontmatter: se ne apre una sopra il contenuto, che resta
        # intatto sotto.
        updated = f"---\n{line}---\n\n{text.lstrip()}"
    else:
        # C'e' una frontmatter: l'id entra come **prima** riga dentro di essa,
        # senza riserializzare il resto. Riscrivere il blocco con ``yaml.dump``
        # avrebbe riordinato le chiavi, normalizzato le virgolette e perso i
        # commenti di un file che l'utente ha scritto a mano.
        head, rest = text.split("---", 2)[0], text.split("---", 2)[1:]
        body_fm, body = rest[0], rest[1] if len(rest) > 1 else ""
        updated = f"{head}---\n{line}{body_fm.lstrip(chr(10))}---{body}"
    atomic_write(schema, updated)
    logger.info("wiki {}: id scritto in {}", root.name, schema.name)
    return wiki_id_value


def migrate_wikis(wikis_dir: Path) -> dict[str, list[str]]:
    """Porta tutte le wiki sotto *wikis_dir* alla forma del passo 7.

    Ritorna ``{"renamed": [...], "identified": [...]}`` — i nomi delle wiki
    toccate, così l'avvio può dire cosa ha fatto invece di farlo in silenzio.
    Non solleva: una wiki illeggibile viene saltata con un log, perché un avvio
    che muore su una cartella storta non migra nemmeno le altre.
    """
    renamed: list[str] = []
    identified: list[str] = []
    if not wikis_dir.is_dir():
        return {"renamed": renamed, "identified": identified}
    for index in discover_wikis(wikis_dir).values():
        root = index.parent
        try:
            if _rename_legacy(root):
                renamed.append(root.name)
            if _ensure_id(root) is not None:
                identified.append(root.name)
        except Exception:
            logger.opt(exception=True).error("Migrazione della wiki {} fallita", root.name)
    if renamed or identified:
        logger.info(
            "Migrazione wiki: {} rinominate ({}), {} identificate ({})",
            len(renamed), ", ".join(renamed) or "-",
            len(identified), ", ".join(identified) or "-",
        )
    return {"renamed": renamed, "identified": identified}
