"""Il contratto della sezione ``compile`` per il layout **progetto**.

Perché esiste, e il caso di campo che l'ha chiesta. Il 26/08/2026, dentro
``wikis/salute``, l'utente ha detto «sistema un po' la wiki, se necessario spezza
i concetti» e il risultato è stato buono: una pagina sovraccarica spezzata, un
rapporto di ``raw/research/`` promosso, la mappa riallineata. Ma la passata si è
**inventata la forma** — la skill aveva già l'operazione (``compile``, che nomina
perfino «when the user says "clean up the wiki"») e ``agent/project.md``
*scoraggiava* di leggerla: diceva che il layout della skill «non è questo
progetto» e si fermava lì.

Il costo dell'improvvisazione, misurato sul telefono: la passata ha trasformato
la ``source:`` di ``riattivazione-fisica.md`` in una lista YAML a due voci, e i
due lettori che la interpretano hanno dato due risposte diverse
(``gardener._page_frontmatter`` → ``'- raw/journal/20260826.md#11:00'``, trattino
incluso e quindi irrisolvibile; ``lint_wiki.parse_frontmatter`` → la seconda
voce). La provenienza di quella pagina è illeggibile e niente l'ha detto.

Come va letto questo file: sono ``in`` su un file di testo. Dicono che la frase
c'è, non che il modello la applichi — quello lo dice solo una sessione vera. Ma
la sezione è **la sola leva** che questo repo ha su quella passata, e un nome qui
rende la riscrittura una modifica sola.
"""

import re
from pathlib import Path

import pytest

_SKILL = (
    Path(__file__).resolve().parents[3] / "jenny" / "skills" / "llm-wiki" / "SKILL.md"
)

_SECTION_TITLE = "#### `compile` in a **project** wiki (notebook layout)"


def _flat(text: str) -> str:
    """Il testo con gli spazi normalizzati.

    Serve perché queste frasi si asseriscono su prosa **incolonnata a mano**: una
    riga che va a capo fra due parole spezza un ``in`` su una frase di quattro
    parole, e il test cadrebbe alla prima riformattazione invece che alla prima
    regola rimossa. Quel che si sorveglia è la frase, non dove finisce la riga.
    """
    return re.sub(r"\s+", " ", text)


@pytest.fixture(scope="module")
def skill() -> str:
    return _flat(_SKILL.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def section(skill: str) -> str:
    """Solo la sezione nuova.

    Il taglio non è pedanteria: mezza wiki di questo repo parla di ``source:`` e
    di caratteri, quindi un ``in`` sul file intero passerebbe con la sezione
    cancellata — è la stessa mutazione che il 25/08 ha trovato due asserzioni
    senza peso in ``gardener.md``.
    """
    title = _flat(_SECTION_TITLE)
    assert title in skill, (
        "la sezione non c'è: `agent/project.md` manda a leggerla, e senza di lei "
        "quel puntatore manda a una pagina che non esiste"
    )
    # Il titolo resta dentro: nomina il layout, che è una delle cose asserite.
    body = title + skill.split(title, 1)[1]
    # Finisce dove ricomincia la numerazione delle cinque operazioni.
    return body.split("### 2. `ingest`", 1)[0]


# Ogni riga: (cosa afferma, perché è portante, la frase).
_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "dice a quale layout si applica",
        "la sezione vive dentro un manuale scritto per il layout research: senza "
        "nominare il proprio, si legge come una variante facoltativa",
        "notebook layout",
    ),
    (
        "nomina la richiesta dell'utente con le sue parole",
        "il 26/08 la frase era «sistema un po' la wiki, se necessario spezza i "
        "concetti»: se la sezione non si fa riconoscere da quella, non viene aperta",
        "split the concepts",
    ),
    (
        "il tetto è in caratteri ed è 6000",
        "il manuale research misura in parole (1200) e quel numero qui non vuol dire "
        "niente: 6000 è ``_PROJECT_PAGES_MAX_CHARS``, il budget di iniezione",
        "6000",
    ),
    (
        "una pagina che non entra viene saltata intera, non accorciata",
        "è la conseguenza che rende lo split urgente invece che estetico: la pagina "
        "esiste su disco e nessuno la legge più",
        "skipped whole",
    ),
    (
        "l'ordine di iniezione è quello della mappa",
        "spiega perché una pagina *grande* affama quelle elencate dopo, che è metà "
        "della ragione per spezzare",
        "the order the map names them",
    ),
    (
        "muovere verbatim, non fondere e non riscrivere",
        "è l'inversione dura del passo 3 di ``compile``, che nel layout research dice "
        "«propose a merge. Confirm, then rewrite»: qui il testo sono le parole "
        "dell'utente arrivate da un diario append-only",
        "Do not merge and do not re-word",
    ),
    (
        "``source:`` è un valore solo",
        "il difetto vero del 26/08, ed è silenzioso: nessuna guardia sta sulle "
        "scritture della conversazione, quindi la forma sbagliata passa e si scopre "
        "solo rileggendo la pagina con due parser",
        "Never a YAML list",
    ),
    (
        "dice cosa fare quando le fonti sono due",
        "vietare la lista senza dire l'alternativa è il rifiuto su cui non si può "
        "agire: si riprova identico",
        "keep the one that carries its `state:`",
    ),
    (
        "``state:`` non sale durante un compile",
        "ristrutturare muove testo, non certifica: è la regola che il giardiniere ha "
        "e una conversazione, che il guardiano non ce l'ha, deve darsi da sé",
        "never goes up during a compile",
    ),
    (
        "``open`` su una fonte documentale è il valore giusto, non un difetto",
        "senza questa frase la sezione invita a «riparare» cinque pagine su cinque di "
        "un progetto sano — lo stesso errore che il lint faceva fino al 26/08",
        "which is the right value, not a defect",
    ),
    (
        "una contraddizione va nella sezione open della mappa",
        "e la deroga va detta: durante un compile l'utente c'è, quindi *può* deciderla "
        "— ma allora il registro deve dire che è stato lui",
        "unless the user is present and settles it",
    ),
    (
        "l'elenco delle pagine esce intero",
        "una voce caduta dalla mappa è una pagina che ha smesso di esistere per ogni "
        "conversazione futura: la stessa regola che protegge la potatura",
        "comes out of a compile whole",
    ),
    (
        "il cancello del lint resta",
        "è il contratto di tutta la skill — output letterale incollato, non «mi pare "
        "a posto» — e un'operazione nuova che non lo eredita lo indebolisce",
        "lint_wiki.lint(",
    ),
)


@pytest.mark.parametrize(("rule", "why", "phrase"), _RULES, ids=[r[0] for r in _RULES])
def test_the_section_states_every_rule_it_has_to_state(
    section: str, rule: str, why: str, phrase: str
) -> None:
    assert phrase in section, f"{rule}: {why}"


def test_the_section_does_not_import_the_word_budget_of_the_research_layout(
    section: str,
) -> None:
    """Il contro-limite: 1200 parole qui non vuol dire niente.

    Senza questa asserzione la sezione potrebbe ripetere la soglia del layout
    research accanto a quella vera, e un modello a cui si danno due tetti applica
    quello che trova prima.
    """
    assert "1200" not in section, (
        "1200 parole è la soglia del layout research: in un progetto la pagina si "
        "misura in caratteri contro 6000, e due tetti nella stessa sezione sono un "
        "tetto che non si applica"
    )


def test_the_pointer_and_the_section_name_the_same_operation(skill: str) -> None:
    """Il puntatore in ``agent/project.md`` e questa sezione devono combaciare.

    Il puntatore dice «`compile` carries a section for this one». Se qualcuno
    rinomina l'operazione da una parte e non dall'altra, il blocco di progetto —
    che si paga a ogni turno — manda a cercare una sezione che non c'è, e il
    silenzio è identico a quello del 26/08.
    """
    template = (
        Path(__file__).resolve().parents[3]
        / "jenny" / "templates" / "agent" / "project.md"
    )
    pointer = _flat(template.read_text(encoding="utf-8"))

    assert "`compile` carries a section for this one" in pointer
    assert "is that operation" in pointer, (
        "il puntatore deve dire che una richiesta di sistemare la wiki *è* "
        "quell'operazione: fino al 26/08 diceva solo che il layout della skill non "
        "era questo progetto, e il modello ha improvvisato"
    )
    assert "### 1. `compile`" in skill, "l'operazione a cui il puntatore manda"
