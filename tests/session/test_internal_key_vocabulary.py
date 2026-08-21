"""Guardia: il vocabolario delle session key vive in un solo modulo.

Il confine "sessione interna vs conversazione utente" è stato ricopiato quattro
volte (``session/keys.py``, ``agent/memory.py``, ``agent/autocompact.py``,
``agent/token_usage.py``) senza che nessun test confrontasse mai due copie fra
loro. La quarta — ``token_usage`` — è sopravvissuta più a lungo delle altre
proprio perché non era una tupla di prefissi ma una catena di ``if/elif``, che
una ricerca per letterali di tupla non intercetta.

Questo test fa uno sweep ``ast`` su ``jenny/`` e ``tests/`` e conta i membri
**distinti** del vocabolario che compaiono in *posizione di classificazione*:

- elemento di un letterale tupla/set/lista, o chiave di un letterale dict;
- argomento di ``.startswith()`` / ``.endswith()``;
- operando di un ``Compare`` (``key == "heartbeat"``).

Perché proprio questa forma, e non "qualunque occorrenza": il vocabolario
compare legittimamente in una ventina di punti che non classificano niente —
``jenny/runtime/notifier.py`` costruisce ``f"cron:{label}"`` come *tag* di una
notifica, ``jenny/runtime/container.py`` usa ``"heartbeat"`` come id di un job
cron. Sono usi produttori o di etichettatura, non decisioni sul confine, e una
versione ingenua li segnalerebbe tutti. Filtrando sulla posizione sintattica
restano solo i punti che davvero *decidono*.

La soglia è 2 perché un modulo che nomina un solo membro sta trattando un caso
particolare, non replicando la partizione.

**Esteso alla terza categoria** quando ``project:`` è nato (2026-08-21): la
partizione da difendere non è più "interna sì/no" ma
interna/progetto/personale, e la forma sbagliata da intercettare è la stessa —
un modulo che si ricopia i prefissi invece di chiedere a
``jenny.session.keys.session_kind``. Il prefisso di progetto è quello che costa
di più a lasciar copiare: sbagliarlo non fa sparire una funzione, mette la
conversazione di un progetto dentro ``MEMORY.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# I membri del vocabolario, nella forma testuale in cui li si scrive nel codice.
# ``project:`` sta nello stesso insieme e non in uno a parte: chi classifica una
# session key deve chiedere a ``keys.py``, e la domanda è una sola quale che sia
# la categoria che gli interessa.
VOCABULARY = frozenset(
    {"subagent:", "cron:", "dream:", "atlas:", "internal:", "heartbeat", "project:"}
)

# Chi può nominarne quanti vuole: il modulo canonico (è la sua definizione) e
# questo file (l'insieme qui sopra è il dato del test, non una quinta copia).
EXEMPT = frozenset(
    {
        Path("jenny/session/keys.py"),
        Path("tests/session/test_internal_key_vocabulary.py"),
    }
)

MAX_VOCABULARY_MEMBERS = 1


def _string_constants(nodes) -> set[str]:
    return {
        node.value
        for node in nodes
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _classification_literals(tree: ast.AST) -> set[str]:
    """I letterali stringa che il modulo usa per classificare una session key."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.Set, ast.List)):
            found |= _string_constants(node.elts)
        elif isinstance(node, ast.Dict):
            found |= _string_constants(k for k in node.keys if k is not None)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"startswith", "endswith"}
        ):
            found |= _string_constants(node.args)
        elif isinstance(node, ast.Compare):
            found |= _string_constants([node.left, *node.comparators])
    return found


def _sources() -> list[Path]:
    paths: list[Path] = []
    for package in ("jenny", "tests"):
        paths.extend(sorted((REPO_ROOT / package).rglob("*.py")))
    return paths


def _offenders() -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in _sources():
        relative = path.relative_to(REPO_ROOT)
        if relative in EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        members = _classification_literals(tree) & VOCABULARY
        if len(members) > MAX_VOCABULARY_MEMBERS:
            offenders[relative.as_posix()] = sorted(members)
    return offenders


class TestInternalKeyVocabulary:
    def test_no_module_reimplements_the_partition(self):
        """Nessun modulo fuori da ``keys.py`` classifica su ≥2 membri propri."""
        offenders = _offenders()
        assert offenders == {}, (
            "questi moduli si sono ricopiati il vocabolario delle sessioni "
            "interne invece di importarlo da jenny.session.keys: " + repr(offenders)
        )

    def test_sweep_actually_sees_an_ifelif_chain(self):
        """Lo sweep intercetta anche la forma che era sfuggita: ``if/elif``.

        Senza questo, un domani qualcuno può restringere ``_classification_literals``
        ai soli letterali di tupla e il test resterebbe verde pur avendo smesso
        di guardare la forma che aveva lasciato passare la quarta copia.
        """
        tree = ast.parse(
            "def f(key):\n"
            "    if key.startswith('dream:'):\n"
            "        return 'dream'\n"
            "    if key == 'heartbeat':\n"
            "        return 'cron'\n"
            "    return 'user'\n"
        )
        assert _classification_literals(tree) & VOCABULARY == {"dream:", "heartbeat"}

    def test_the_project_prefix_is_in_the_swept_vocabulary(self):
        """La terza categoria è difesa come le altre, non solo documentata.

        Senza questo, aggiungere ``project:`` all'insieme resta un gesto che
        qualcuno può disfare per far passare un modulo, e il costo lo si scopre
        dal contenuto di ``MEMORY.md``.
        """
        assert "project:" in VOCABULARY
        tree = ast.parse(
            "def f(key):\n"
            "    if key.startswith('project:'):\n"
            "        return 'project'\n"
            "    if key.startswith('cron:'):\n"
            "        return 'internal'\n"
            "    return 'personal'\n"
        )
        assert _classification_literals(tree) & VOCABULARY == {"project:", "cron:"}

    def test_sweep_ignores_producers_and_labels(self):
        """Un f-string produttore o un'etichetta non sono classificazione."""
        tree = ast.parse(
            "def f(job_id, label):\n"
            "    key = f'cron:{job_id}'\n"
            "    tag = f'cron:{label}'\n"
            "    job = dict(id='heartbeat')\n"
            "    return key, tag, job\n"
        )
        assert _classification_literals(tree) & VOCABULARY == set()

    def test_sweep_covers_the_whole_tree(self):
        """Guardia sulla guardia: se lo sweep smette di trovare file, si accorge."""
        assert len(_sources()) > 300
