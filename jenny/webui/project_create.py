"""Creazione di un progetto dalla WebUI: una wiki nuova, completa e vuota.

**Un progetto e' una wiki** (v. `roadmap/project-sessions.md`): non esiste una
`projects/` separata, quindi "nuovo progetto" vuol dire scaffoldare
`wikis/<nome>/` e registrarlo in `wikis/_index.md`. Prima di questo modulo il
chip creava una cartella nuda con `/api/workspace/mkdir`: nessun albero, nessun
file di istruzioni, nessuna voce nel registro — una wiki rotta che sembrava un
progetto.

**Lo scaffolder e' `project_scaffold.py`, nel package** *(dal 22/08, passo T1 di
`roadmap/taccuino-passi.md`)*. Fino a quel giorno era `scaffold.py` della skill,
per non avere due scaffolder che divergono; ora sono due di proposito, perche'
costruiscono **due formati diversi**: la skill fa la biblioteca di ricerca
(`raw/papers`, `concepts|entities|summaries`, le cinque operazioni), e resta la
strada per crearne una chiedendola a Jenny; il picker della UI fa un progetto —
pagine piatte, un diario, la mappa. Il confine e' netto e nessun file e'
conteso.

**La riga dell'utente non e' un extra.** *"Quando crei il progetto devi scrivere
tu qualcosa, sennò la chat è ferma"*: senza una riga di scope il primo turno non
ha su cosa appoggiarsi, e uno scope indovinato dall'agente e' peggio di nessuno
scope, perche' tutto quel che viene archiviato dopo lo eredita. Finisce in due
posti — il `summary:` dell'`AGENTS.md`, da cui `reindex_wikis` costruisce la riga
del registro, e la mappa del progetto, che e' quel che l'agente legge per primo —
e ci entra **alla nascita**, non per sostituzione di un segnaposto dopo.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import (
    LEGACY_WIKI_SCHEMA_FILENAME,
    WIKI_INDEX_FILENAME,
    WIKI_SCHEMA_FILENAME,
    is_wiki_root,
)
from jenny.webui.project_scaffold import scaffold_project

_TITLE_SPLIT_RE = re.compile(r"[-_.]+")

# La frontmatter iniziale e la riga di scope dentro di essa. Regex e non YAML per
# la stessa ragione di ``wiki_paths.wiki_id``: un due punti nel testo libero rende
# il blocco non parsabile, e ``yaml.safe_load`` non perde *quella* riga, perde
# tutte le altre. Qui va toccata una riga sola e riscritto il resto byte per
# byte, che un round-trip di ``yaml.dump`` non sa fare (riordina le chiavi,
# normalizza le virgolette, perde i commenti).
_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---")
_SUMMARY_LINE_RE = re.compile(r"^summary:[ \t]*(.*)$", re.M)


class ProjectCreateError(Exception):
    """Creazione non riuscita, con un messaggio mostrabile all'utente."""


def project_title(name: str) -> str:
    """Titolo leggibile da un nome di cartella: ``patreon-creator`` -> ``Patreon Creator``.

    Il titolo finisce negli H1 del template e non e' un identificatore: l'utente
    lo puo' correggere nell'`AGENTS.md`, il nome della cartella no (e' l'indirizzo
    della wiki). Per questo lo deriviamo invece di chiederlo: un campo in piu' nel
    dialogo per un dato che si cambia dopo.
    """
    words = [w for w in _TITLE_SPLIT_RE.split(name) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or name


def _yaml_scalar(value: str) -> str:
    """*value* come scalare YAML su una riga, sempre valido.

    Perche' quotare: la riga di scope e' testo libero dell'utente e finisce
    *dentro* la frontmatter. Un due punti in mezzo — "Prova del passo 7: la chat
    segue" — rende il blocco YAML non parsabile, e allora si perde **tutta** la
    frontmatter, non solo quella riga. Visto sul telefono il 22/08 su una wiki
    appena creata: ``read_wiki_scope`` cadeva sul ripiego e l'id risultava assente.

    Non si usa ``yaml.dump``: quello aggiunge il documento e a volte manda a
    capo. Qui basta la regola delle virgolette doppie — raddoppiare i backslash,
    scappare le virgolette — che rende sicuro qualunque testo, due punti e
    cancelletti compresi.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _is_complete_project(root: Path) -> bool:
    """Vero se *root* e' un progetto **finito**, cioe' da non toccare piu'.

    Il marcatore e' la **mappa** (``wiki/index.md``), e non il file di
    istruzioni. La ragione e' la migrazione dell'avvio
    (``utils/wiki_migration.py``): a ogni boot ogni cartella che contiene
    ``wiki/`` si prende un ``AGENTS.md`` minimo se non ce l'ha. Con quello come
    marcatore, un albero rimasto a meta' — la ``wiki/`` c'e', il resto no —
    risulterebbe "completo" dal riavvio successivo, e ci resterebbe per sempre
    con la riga di scope a segnaposto: cioe' esattamente il progetto che il
    picker elenca e che non si puo' finire.

    ``wiki/index.md`` invece non lo scrive nessun passaggio automatico: lo
    scrive uno scaffolder, il nostro o quello della skill — i due formati
    differiscono in tutto tranne che nella mappa — quindi la sua presenza vuol
    dire che un progetto e' nato davvero. Ed e' anche il file a cui punta il
    wikilink del registro: senza, la voce in ``wikis/_index.md`` e' un link
    morto.
    """
    if (root / "wiki" / WIKI_INDEX_FILENAME).is_file():
        return True
    # Una wiki scritta a mano prima del passo 7 tiene le istruzioni in
    # ``CLAUDE.md``. Lo scaffolder non lo vede e le scriverebbe accanto un
    # ``AGENTS.md``: e' lo stato che la migrazione si rifiuta di risolvere
    # («ha sia CLAUDE.md sia AGENTS.md, non tocco niente»). Una cartella cosi'
    # e' roba dell'utente, non un albero a meta'.
    return (root / LEGACY_WIKI_SCHEMA_FILENAME).is_file()


def _seed_scope_if_placeholder(root: Path, quoted_seed: str) -> bool:
    """Scrive la riga di scope in un ``AGENTS.md`` che ha solo il segnaposto.

    Serve al ripasso su un albero rimasto a meta'. Se l'avvio ha gia' scritto il
    suo ``AGENTS.md`` minimo (``wiki_migration._ensure_id``, ``summary:`` a
    segnaposto), lo scaffolder lo lascia stare — e' la regola che rende sicuro
    rilanciarlo — e la riga che l'utente ha appena scritto si perderebbe in
    silenzio: il progetto sarebbe completo e senza scopo, che e' la cosa per cui
    la riga viene chiesta.

    Uno ``summary:`` **vero** non si tocca: completare un progetto non e'
    riscriverne lo scope. Ritorna True se ha scritto.
    """
    schema = root / WIKI_SCHEMA_FILENAME
    try:
        text = schema.read_text(encoding="utf-8")
    except OSError:
        return False
    block = _FRONTMATTER_RE.match(text)
    if block is None:
        return False
    raw = block.group(1)
    line = _SUMMARY_LINE_RE.search(raw)
    if line is None:
        # Frontmatter senza ``summary:``: la riga si aggiunge in fondo al blocco,
        # che e' l'unico posto in cui non spezza niente di quel che c'e'.
        updated = f"{raw.rstrip(chr(10))}\nsummary: {quoted_seed}"
    else:
        # Stessa regola di ``wiki_paths._is_placeholder``, piu' il caso della
        # chiave vuota: entrambi vogliono dire "scope da riempire".
        value = line.group(1).strip().strip('"').strip("'")
        if value and not ("<" in value and ">" in value):
            return False
        updated = f"{raw[: line.start()]}summary: {quoted_seed}{raw[line.end() :]}"
    atomic_write(schema, f"{text[: block.start(1)]}{updated}{text[block.end(1) :]}")
    logger.info("project {}: scope line written into {}", root.name, WIKI_SCHEMA_FILENAME)
    return True


def create_project(
    *,
    wikis_dir: Path,
    scripts_dir: Path,
    name: str,
    seed: str,
) -> dict[str, Any]:
    """Scaffolda `wikis_dir/name`, ci scrive la riga di scope, aggiorna il registro.

    Sincrona e con I/O: il chiamante la mette in un thread. Il nome arriva gia'
    validato — questo modulo non e' il gate.

    **Su una cartella che esiste gia' rifiuta in due modi diversi**, e la
    differenza e' quel che il client mostra all'utente. Un progetto completo e'
    un "ce l'hai gia'"; una cartella che non e' un progetto e' un "c'e' qualcosa
    di mezzo", che si risolve rinominando. In mezzo ai due c'e' il caso per cui
    lo scaffolder e' scritto a top-up: l'albero a meta' — la ``wiki/`` c'e' (la
    crea per prima di proposito, cosi' resta visibile al picker) e il resto no,
    perche' la creazione e' morta a meta'. Prima di questo, quella cartella era
    irrecuperabile: elencata dal picker, senza mappa, e su un telefono l'utente
    non ha modo di ripararla a mano.
    """
    root = wikis_dir / name
    if root.exists():
        if _is_complete_project(root):
            raise ProjectCreateError(f"project already exists: {name}")
        if not is_wiki_root(root):
            raise ProjectCreateError(
                f"a folder named {name} is in the way: it exists but is not a project"
            )
        logger.info("project {}: half-built tree, completing it instead of refusing", name)

    try:
        created = scaffold_project(root, project_title(name), seed, _yaml_scalar(seed))
    except Exception as exc:
        raise ProjectCreateError(f"scaffold failed: {exc}") from exc

    # Il seme: scritto dallo scaffolder quando l'``AGENTS.md`` nasce adesso,
    # applicato a mano quando il file c'era gia' col segnaposto dell'avvio.
    seeded = WIKI_SCHEMA_FILENAME in created or _seed_scope_if_placeholder(
        root, _yaml_scalar(seed)
    )

    # Il registro lo scrive il chiamante e non lo scaffolder: quello conosce una
    # cartella, questo conosce `wikis_dir`. `reindex_wikis` resta della skill —
    # e' il registro del *workspace*, comune ai due formati, e non c'e' niente da
    # duplicare.
    registry: str | None = None
    try:
        reindex = importlib.util.spec_from_file_location(
            "reindex_wikis", scripts_dir / "reindex_wikis.py"
        )
        if reindex is not None and reindex.loader is not None:
            module = importlib.util.module_from_spec(reindex)
            reindex.loader.exec_module(module)
            # **Niente ``redirect_stdout`` qui.** Questa funzione gira dentro un
            # ``asyncio.to_thread`` (v. ``webui/commands.py::project_create``), e
            # ``redirect_stdout`` muta ``sys.stdout`` **di processo**: per tutta
            # la finestra, l'output di *ogni altro* thread finisce nel buffer che
            # buttiamo via. Non è teorico: ``python_exec`` cattura quel che il
            # codice del modello stampa proprio via ``sys.stdout``, e ha
            # sostituito il proprio ``redirect_stdout`` con un proxy per-thread
            # esattamente per questo (v. il commento lungo in
            # ``agent/tools/python_exec.py``, «cattura di stdout PER THREAD»).
            # Il proxy però si consulta al momento della scrittura, e chi scrive
            # legge ``sys.stdout``: con la nostra ``StringIO`` al suo posto, un
            # ``print()`` del modello nel turno accanto sparisce in silenzio.
            #
            # E non c'era niente da nascondere: ``regenerate_index`` stampa una
            # riga sola, su **stderr** (il caso «_index.md senza marcatori»), che
            # un ``redirect_stdout`` non tocca nemmeno. Costo del silenzio: zero.
            # Rischio: l'output di un altro turno.
            registry = str(module.regenerate_index(wikis_dir))
    except Exception as exc:
        # La wiki esiste ed e' completa: un registro non aggiornato e' un
        # inconveniente che `lint --workspace` sa riparare, non un fallimento
        # della creazione.
        logger.warning("could not refresh {} after seeding: {}", wikis_dir, exc)

    return {
        "name": name,
        "created": list(created or []),
        # La riga di scope entra alla nascita, quindi su un progetto nuovo e'
        # sempre `True`. Su un albero completato puo' essere `False`: se
        # l'``AGENTS.md`` c'era gia' con uno scope **vero** — la creazione era
        # morta fra quel file e la mappa — quello resta, e la riga di stavolta
        # finisce solo nella mappa. Il campo dice quel che e' successo invece di
        # dichiarare sempre riuscita una cosa che non sempre lo e'.
        "seeded": seeded,
        "registry": registry,
    }
