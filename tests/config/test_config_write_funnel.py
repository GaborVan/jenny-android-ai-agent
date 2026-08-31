"""Ogni scrittura di ``config.json`` passa da ``store.mutate()``. Verificato, non promesso.

È l'invariante su cui ``AGENTS.md`` avverte più forte — *«chiamare `save_config()`
direttamente reintroduce un bug di perdita dati silenziosa che nessun test
prenderà per te»* — ed era vero alla lettera: **nessun test lo prendeva.**
``tests/security/test_readonly_write_surfaces.py`` rileva `save_config(`, ma solo
dentro ``agent/tools/`` più sei file elencati a mano, quindi una chiamata
aggiunta in ``webui/``, ``command/`` o ``cron/`` non faceva fallire niente.

Perché la regola esiste: ``save_config`` riscrive il file **intero**. Chi legge
la config, la modifica e la salva fuori dal lock cancella qualunque cosa un
altro scrittore abbia scritto nel frattempo, e la perdita non lascia traccia —
nessuna eccezione, nessun log, solo una chiave che non c'è più.

Le due deroghe sono quelle che ``AGENTS.md`` nomina, e sono commentate sul
posto: ``config/bootstrap.py`` gira **prima dell'event loop** (non c'è un lock
asyncio da prendere) e ``config/loader.py`` promuove il ``.bak`` quando il file
vivo è illeggibile, riscrivendo contenuto che ha appena letto.
"""

from __future__ import annotations

import ast
from pathlib import Path

JENNY = Path(__file__).resolve().parents[2] / "jenny"

# L'unico chiamante legittimo, più il modulo che la definisce.
_FUNNEL_CALLERS = {"config/store.py", "config/loader.py"}

# Chi può scrivere ``config.json`` senza passare dal funnel. Non è una lista di
# comodo: ogni voce ha un commento sul posto che spiega perché, e allungarla
# dovrebbe costare la stessa fatica.
_DIRECT_WRITE_EXCEPTIONS = {
    "config/bootstrap.py",   # gira prima dell'event loop
    "config/loader.py",      # promozione del .bak su file illeggibile
}


def _modules() -> list[tuple[str, str]]:
    out = []
    for path in sorted(JENNY.rglob("*.py")):
        rel = path.relative_to(JENNY).as_posix()
        # ``skills/**/scripts`` sono script che l'agente esegue via python_exec,
        # non parte del gateway, e non importano il pacchetto.
        if rel.startswith("skills/"):
            continue
        out.append((rel, path.read_text("utf-8")))
    return out


def test_save_config_is_called_only_from_the_funnel() -> None:
    offenders = []
    for rel, src in _modules():
        if rel in _FUNNEL_CALLERS:
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "save_config"
            ):
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        f"save_config() chiamata fuori da config/store.py::mutate(): {offenders}. "
        "Riscrive il file intero, quindi cancella in silenzio quello che un altro "
        "scrittore ha appena scritto. Usare store.mutate()."
    )


def test_nothing_else_writes_the_config_file_directly() -> None:
    """Nessun modulo nomina ``config.json`` accanto a una primitiva di scrittura."""
    offenders = []
    for rel, src in _modules():
        if rel in _DIRECT_WRITE_EXCEPTIONS or rel.startswith("config/"):
            continue
        for lineno, line in enumerate(src.splitlines(), start=1):
            if "config.json" not in line:
                continue
            if any(p in line for p in ("atomic_write", ".write_text(", ".write_bytes(", "open(")):
                offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        f"scrittura diretta di config.json fuori dal funnel: {offenders}. "
        "Le due deroghe sono config/bootstrap.py e la promozione del .bak in "
        "config/loader.py, entrambe commentate sul posto."
    )


def test_the_documented_exceptions_still_explain_themselves() -> None:
    """Una deroga senza il suo commento è una deroga che nessuno ricorda.

    Se questo test cade, il commento è stato tolto o riscritto: rimetterlo, o
    togliere la voce da ``_DIRECT_WRITE_EXCEPTIONS`` perché la deroga non serve
    più. Quello che non va fatto è lasciare la lista senza la spiegazione.
    """
    bootstrap = (JENNY / "config" / "bootstrap.py").read_text("utf-8")
    # Si cerca il *funnel per nome*, non la parola "mutate": il commento cita
    # ``jenny.config.store``, che è il modulo, ed è il riferimento giusto.
    assert "config.store" in bootstrap and "funnel" in bootstrap, (
        "config/bootstrap.py non nomina più il funnel da cui è esente: "
        "il commento che spiega la deroga è stato perso."
    )

    loader = (JENNY / "config" / "loader.py").read_text("utf-8")
    assert "save_config" in loader, (
        "config/loader.py non definisce più save_config: se la funzione è "
        "sparita, questa deroga e il test sopra non hanno più soggetto."
    )
