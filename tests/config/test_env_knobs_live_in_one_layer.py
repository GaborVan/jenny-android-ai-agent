"""I knob ``JENNY_*`` si leggono da un posto solo, e i loro nomi vivono lì.

``config/runtime_env.py`` è dichiarato «il layer unico per i knob operativi
``JENNY_*``» (``AGENTS.md``). Non lo era: ``providers/base.py`` ri-implementava
il parsing di due timeout di streaming — proprio i due che i gotcha citano per
nome — e ``security/workspace_access.py`` leggeva tre manopole di sandbox, una
delle quali con un alias storico.

Il danno non è la duplicazione del parsing: è che chi apre quel file per sapere
**quali knob esistono** non li vedeva. Un alias legacy di un knob di sicurezza è
il tipo di dettaglio che sopravvive solo se sta dove la gente lo cerca.

Nessun linter poteva vederlo — ``ruff`` con `E,F,I,N,W` non ha nulla da dire su
un ``os.environ.get`` — quindi la regola vive qui.
"""

from __future__ import annotations

import ast
from pathlib import Path

JENNY = Path(__file__).resolve().parents[2] / "jenny"
LAYER = "config/runtime_env.py"

# Chi può nominare un ``JENNY_*`` senza leggerlo dall'ambiente: il layer che lo
# definisce, più i moduli che ne importano il *nome* come costante. La lettura
# resta loro perché ha una semantica propria (v. ``_env_system_provider``); il
# nome no.
_MAY_NAME_KNOBS = {
    LAYER,
    "security/workspace_access.py",
}


def _sources() -> list[tuple[str, str]]:
    return [
        (path.relative_to(JENNY).as_posix(), path.read_text("utf-8"))
        for path in sorted(JENNY.rglob("*.py"))
        if not path.relative_to(JENNY).as_posix().startswith("skills/")
    ]


def _env_reads(src: str) -> list[tuple[int, str]]:
    """I letterali ``"JENNY_…"`` passati a una lettura d'ambiente."""
    out = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # os.environ.get(...) / os.getenv(...) / env.get(...)
        is_read = isinstance(func, ast.Attribute) and func.attr in {"get", "getenv"}
        if not is_read:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value.startswith("JENNY_"):
                    out.append((node.lineno, arg.value))
    return out


def test_no_jenny_knob_is_read_outside_the_layer() -> None:
    offenders = []
    for rel, src in _sources():
        if rel == LAYER:
            continue
        for lineno, knob in _env_reads(src):
            offenders.append(f"{rel}:{lineno} ({knob})")

    assert not offenders, (
        f"knob JENNY_* letti fuori da {LAYER}: {offenders}. "
        "Il layer è dove si viene a sapere quali knob esistono: una lettura "
        "altrove li rende invisibili a chi cerca lì."
    )


def test_knob_names_are_defined_in_the_layer() -> None:
    """Un knob nominato altrove deve venire dal layer come costante importata."""
    layer_src = (JENNY / LAYER).read_text("utf-8")
    offenders = []
    for rel, src in _sources():
        if rel in _MAY_NAME_KNOBS:
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("JENNY_")
                and node.value not in layer_src
            ):
                offenders.append(f"{rel}:{node.lineno} ({node.value})")

    assert not offenders, (
        f"nomi di knob JENNY_* che il layer non conosce: {offenders}. "
        f"Aggiungerli a {LAYER}, anche solo come costante, e importarli da lì."
    )
