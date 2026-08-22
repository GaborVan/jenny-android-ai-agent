"""L'inventario delle scritture, e il guardiano che lo tiene onesto.

Passo **4.1** di ``roadmap/progetti-passi.md``.

«Sola lettura» vuol dire «non cambia niente sul telefono» (deciso il 22/08), e
quella promessa è mantenuta da una **lista** — chi scrive, e da quale cancello.
Una lista così invecchia in silenzio: il tool aggiunto fra sei mesi è uno
scrittore che nessuno ha classificato, e l'interruttore continua a promettere
quel che non fa più. Questo file è il solo motivo per cui quella promessa resta
vera senza che qualcuno se la ricordi.

Le tre domande, e sono diverse fra loro:

1. *Chi scrive passa da un cancello?* Se un modulo usa una primitiva di
   scrittura senza chiedere allo scope, o è nella lista di quelli che chiedono
   per conto proprio, o è un buco.
2. *I mutatori di ``os`` sono tutti classificati?* Le tabelle di
   ``python_exec`` mescolano mutatori, sonde ed enumeratori; l'insieme chiuso in
   sola lettura è dichiarato a parte. Un nome che non sta in nessuno dei due
   insiemi è un mutatore aggiunto e mai deciso.
3. *Le op delle mini-app sono partizionate?* Perché lì è ``query`` — la sola
   lettura — a dover restare aperta.

Non usa ``Tool.read_only``, e la ragione sta in
``WorkspaceScope.without_write_access``: quel flag significa "parallelizzabile",
il suo default è ``False`` per chiunque non l'abbia dichiarato, e riusarlo qui
sarebbe leggere una risposta scritta per un'altra domanda.
"""

from __future__ import annotations

import re
from pathlib import Path

from jenny.agent.tools.python_exec import PythonNamespace
from jenny.apps.manifest import STORAGE_OPS
from jenny.apps.storage import _MUTATING_OPS

ROOT = Path(__file__).resolve().parents[2] / "jenny"
TOOLS_DIR = ROOT / "agent" / "tools"

# Primitive che *scrivono su disco*. Non ci sono ``open(..., 'w')`` e simili
# apposta: quelle passano dai patch di ``python_exec`` o da ``_resolve_write``,
# cioè da un cancello. Qui si cercano le scritture **dirette**, quelle che si
# portano la destinazione da sole.
_WRITE_PRIMITIVES = re.compile(
    r"\b(atomic_write|\.write_text\(|\.write_bytes\(|\.mkdir\(|ensure_dir\()"
)

# Chi chiede allo scope per conto proprio. Ognuno con la ragione per cui la
# destinazione non passa da ``resolve_allowed_path``, che è anche la ragione per
# cui il cancello non lo vede.
_ASKS_FOR_ITSELF = {
    "download.py": "destinazione fissa: <installazione>/downloads/",
    "storage.py": "app_dir viene dalla radice dell'installazione, non dal turno",
    "cron.py": "scrive cron/jobs.json, che non è un file dell'utente",
    "app_update.py": "installa un APK: non è una scrittura, è una sostituzione",
    "journal.py": "appende al diario del progetto: non passa dai tool file, ha il suo gate",
}

# Chi scrive ma **non deve** chiedere, con la ragione. Non è una lista di
# perdoni: è la parte dell'inventario che dice *perché* la sola lettura non li
# riguarda, e va riletta quando uno di questi cambia mestiere.
_OUT_OF_SCOPE = {
    "filesystem.py": "è il cancello (`_resolve_write`)",
    "python_exec_builtins.py": "è il cancello (`_write_path`)",
    "apply_patch.py": "passa da `_resolve_write` dei tool file",
    "memory_entries.py": (
        "non è registrato fra i tool (nessun TOOLS): ci scrive solo Dream, "
        "che è una sessione interna e non ha un messaggio da cui leggere il flag"
    ),
    "ssh.py": "scrive su una macchina remota — altro asse, resta aperto",
    "ssh_jobs.py": "stato locale di un job ssh, non un file dell'utente",
    "ssh_transport.py": "stato locale del trasporto",
}


def _sources() -> dict[str, str]:
    files = {p.name: p.read_text(encoding="utf-8") for p in sorted(TOOLS_DIR.glob("*.py"))}
    storage = ROOT / "apps" / "storage.py"
    files[storage.name] = storage.read_text(encoding="utf-8")
    return files


# ── 1. Chi scrive, e da dove chiede ──────────────────────────────────────


def test_every_direct_writer_is_classified() -> None:
    """Un modulo che scrive e non compare in nessuno dei due elenchi è un buco.

    È il test che il passo 4 esiste per non dover rifare: senza, la promessa
    «non cambia niente sul telefono» resta vera solo finché qualcuno se la
    ricorda a mano.
    """
    known = set(_ASKS_FOR_ITSELF) | set(_OUT_OF_SCOPE)
    writers = {name for name, src in _sources().items() if _WRITE_PRIMITIVES.search(src)}
    unclassified = sorted(writers - known)
    assert not unclassified, (
        "questi moduli scrivono su disco e non sono classificati: "
        f"{unclassified}. Decidi se in sola lettura devono chiudersi (aggiungili a "
        "_ASKS_FOR_ITSELF e chiama current_turn_is_readonly) o no (_OUT_OF_SCOPE, con la ragione)"
    )


def test_the_ones_that_ask_really_ask() -> None:
    """Un nome nell'elenco non è una prova: la chiamata deve esserci."""
    sources = _sources()
    for name in sorted(_ASKS_FOR_ITSELF):
        src = sources[name]
        assert "current_turn_is_readonly" in src, f"{name} è nell'elenco ma non chiede allo scope"
        assert "READONLY_TOOL_REFUSAL" in src, (
            f"{name} deve usare il rifiuto condiviso: cinque parafrasi della stessa regola "
            "la fanno sembrare cinque regole"
        )


def test_no_stale_entries() -> None:
    """Una riga morta in un elenco è peggio di una mancante: sembra copertura."""
    sources = _sources()
    for name in sorted(set(_ASKS_FOR_ITSELF) | set(_OUT_OF_SCOPE)):
        assert name in sources, f"{name} è negli elenchi ma il file non esiste più"


# ── 2. I mutatori di os ──────────────────────────────────────────────────

# Le voci delle tabelle che NON mutano: sonde ed enumeratori. Insieme a
# ``_OS_MUTATING_FUNCTIONS`` devono coprire le tabelle per intero, così un nome
# nuovo cade fuori da entrambi e questo file si rompe.
_OS_NON_MUTATING = frozenset({
    "getxattr", "listxattr",
    "listdir", "scandir", "walk", "fwalk",
    "stat", "lstat", "access", "readlink", "statvfs", "pathconf",
})


def _os_table_names() -> set[str]:
    return (
        {n for n, _, _ in PythonNamespace._OS_SINGLE_PATH_FUNCTIONS}
        | {n for n, _, _ in PythonNamespace._OS_TWO_PATH_FUNCTIONS}
    )


def test_every_os_entry_is_either_a_mutator_or_a_probe() -> None:
    """Aggiungere un mutatore alla tabella senza deciderlo rompe qui."""
    undecided = sorted(
        _os_table_names() - PythonNamespace._OS_MUTATING_FUNCTIONS - _OS_NON_MUTATING
    )
    assert not undecided, (
        f"voci di os non classificate: {undecided}. Se cambiano qualcosa vanno in "
        "_OS_MUTATING_FUNCTIONS (e si chiudono in sola lettura), altrimenti in "
        "_OS_NON_MUTATING qui sopra"
    )


def test_no_mutator_is_declared_that_the_tables_do_not_wrap() -> None:
    """Un nome dichiarato mutatore ma non wrappato è una riga che non chiude niente."""
    orphans = sorted(PythonNamespace._OS_MUTATING_FUNCTIONS - _os_table_names())
    assert not orphans, f"dichiarati mutatori ma non presenti nelle tabelle: {orphans}"


def test_the_two_sets_do_not_overlap() -> None:
    both = sorted(PythonNamespace._OS_MUTATING_FUNCTIONS & _OS_NON_MUTATING)
    assert not both, f"classificati due volte: {both}"


# ── 3. Le op delle mini-app ──────────────────────────────────────────────


def test_storage_ops_are_partitioned_and_query_stays_open() -> None:
    """In sola lettura una mini-app deve poter ancora *mostrare* i suoi dati."""
    assert _MUTATING_OPS | {"query"} == STORAGE_OPS, (
        "ogni op deve essere o mutante o la lettura: un'op nuova non classificata "
        f"passerebbe in sola lettura (mutanti={sorted(_MUTATING_OPS)}, tutte={sorted(STORAGE_OPS)})"
    )
    assert "query" not in _MUTATING_OPS
