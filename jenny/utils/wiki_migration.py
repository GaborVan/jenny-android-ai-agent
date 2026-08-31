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
3. Ogni wiki prende il suo **diario** (``raw/journal/``), se non ce l'ha —
   passo T1 di ``roadmap/taccuino-passi.md``. E' la presa sulla conversazione, e
   la politica che ci scrive dentro e' **universale**: vale per un progetto
   creato oggi e per una wiki di ricerca di mesi fa, perche' ogni conversazione
   di progetto contiene fatti stabili. Legare il diario al formato nuovo
   vorrebbe dire lasciare le sette wiki vere senza la cosa che il taccuino
   esiste per dare. E' l'unico dei tre punti che **crea** qualcosa invece di
   riscriverlo: una cartella vuota, nessun file toccato.

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
    new_wiki_id,
    strip_frontmatter,
    wiki_id,
    wiki_journal_dir,
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
            "wiki {}: has both {} and {}, leaving both alone — picking one would discard the other",
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
        logger.info("wiki {}: created a minimal {} carrying the id", root.name, _AGENTS)
        return wiki_id_value

    # «Ce l'ha già, un id?» si chiede **al lettore che lo legge**. ``wiki_id`` usa
    # una regex di proposito: una riga di scope con un due punti dentro rende il
    # blocco non parsabile e ``yaml.safe_load`` non perde quella riga, perde
    # **tutte** le altre (difetto visto sul telefono il 22/08 — v. il docstring
    # di ``wiki_paths.wiki_id``). Chiederlo a YAML qui voleva dire non vedere
    # l'id che c'era e scriverne uno nuovo a *ogni* avvio: l'identità della wiki
    # cambiava sotto i piedi e la sua chat non era più ritrovabile, mentre il log
    # diceva ogni volta «1 identificate». Ora scrittore e lettore concordano per
    # costruzione.
    if wiki_id(root) is not None:
        return None

    text = schema.read_text(encoding="utf-8")
    # Di ``strip_frontmatter`` qui serve **solo** la domanda strutturale «c'è un
    # blocco?», che e' una regex e sopravvive allo YAML rotto: quando il parse
    # fallisce ritorna ``{}``, non ``None``.
    frontmatter, _, _ = strip_frontmatter(text)

    # Un id scritto a mano e non conforme (``id: tesi-2024``) **non si tocca**:
    # la riga resta dov'e' e quella nuova le va sopra. Riscriverla in loco
    # cancellerebbe testo dell'utente, che e' l'unica cosa che questa migrazione
    # promette di non fare mai; lasciar perdere la wiki cancellerebbe invece la
    # sola cosa per cui l'id esiste — ritrovare la chat dopo un rinomino — e in
    # silenzio, per sempre. Il lettore prende il **primo** match, cioe' il
    # nostro, quindi l'esito e' stabile dal secondo avvio in poi.
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
    logger.info("wiki {}: id written to {}", root.name, schema.name)
    return wiki_id_value


def _ensure_journal(root: Path) -> bool:
    """Crea ``raw/journal/`` se non c'e'. True se l'ha creata.

    Solo la cartella, e nessuna pagina: il diario e' append-only e la sua prima
    pagina la scrive la prima cattura. Un file vuoto creato qui sarebbe una
    pagina di un giorno in cui non e' stato detto niente.
    """
    journal = wiki_journal_dir(root)
    if journal.is_dir():
        return False
    journal.mkdir(parents=True, exist_ok=True)
    logger.info("wiki {}: journal created at {}", root.name, journal.name)
    return True


def migrate_wikis(wikis_dir: Path) -> dict[str, list[str]]:
    """Porta tutte le wiki sotto *wikis_dir* alla forma del passo 7.

    Ritorna ``{"renamed": [...], "identified": [...], "journals": [...]}`` — i
    nomi delle wiki toccate da ciascuno dei tre punti, così l'avvio può dire cosa
    ha fatto invece di farlo in silenzio. Non solleva: una wiki illeggibile viene
    saltata con un log, perché un avvio che muore su una cartella storta non
    migra nemmeno le altre.
    """
    renamed: list[str] = []
    identified: list[str] = []
    journals: list[str] = []
    result = {"renamed": renamed, "identified": identified, "journals": journals}
    if not wikis_dir.is_dir():
        return result
    for index in discover_wikis(wikis_dir).values():
        root = index.parent
        try:
            if _rename_legacy(root):
                renamed.append(root.name)
            if _ensure_id(root) is not None:
                identified.append(root.name)
            if _ensure_journal(root):
                journals.append(root.name)
        except Exception:
            logger.opt(exception=True).error("Migrazione della wiki {} fallita", root.name)
    if renamed or identified or journals:
        logger.info(
            # «identificate» diceva di piu' di quel che era vero: fino al 22/08 il
            # conto includeva le wiki che l'id ce l'avevano gia' e a cui la
            # migrazione ne stava scrivendo un altro. Il conto e' delle wiki a cui
            # e' stato **scritto** un id, e la riga adesso lo dice.
            "Wiki migration: {} renamed ({}), {} with id written ({}), {} with a new journal ({})",
            len(renamed), ", ".join(renamed) or "-",
            len(identified), ", ".join(identified) or "-",
            len(journals), ", ".join(journals) or "-",
        )
    return result
