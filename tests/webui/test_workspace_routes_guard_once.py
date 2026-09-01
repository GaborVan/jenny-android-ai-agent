"""Auth, gate e scala d'errore stanno nel ``dispatch``, e devono restarci.

I sette handler di ``WorkspaceRoutes`` ripetevano identici il controllo del
token, il gate ``workspace.enabled`` e la stessa scala a quattro rami
(``ValueError`` → 400, ``FileNotFoundError`` → 404, ``PermissionError`` → 403,
``OSError`` → 400). Ripetere è il modo più facile per lasciarne uno che risponde
500 dove gli altri rispondono 404 — o, peggio, uno senza il controllo del token.

Ora vivono una volta sola in ``dispatch``. Questo test serve al caso che segue:
un ottavo handler aggiunto domani. Se lo si mette nella tabella, è protetto
gratis; se lo si aggancia altrove, o se qualcuno reintroduce la scala nel
proprio handler «per sicurezza», questo lo dice.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[2] / "jenny" / "webui" / "workspace_routes.py"

_LADDER_ARMS = {"ValueError", "FileNotFoundError", "PermissionError", "OSError"}


def _class_and_dispatch() -> tuple[ast.ClassDef, ast.AsyncFunctionDef]:
    tree = ast.parse(ROUTES.read_text("utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WorkspaceRoutes")
    dispatch = next(
        f for f in cls.body
        if isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef)) and f.name == "dispatch"
    )
    return cls, dispatch


def _routed_handlers(dispatch: ast.AST) -> set[str]:
    """I nomi che la **tabella dei path** referenzia come ``self._x``.

    Solo i valori del dizionario, non ogni ``self._x`` che il dispatch nomina:
    altrimenti finivano dentro anche ``_check_api_token`` e
    ``_check_workspace_enabled``, che non sono handler di rotta.
    """
    out: set[str] = set()
    for table in ast.walk(dispatch):
        if not isinstance(table, ast.Dict):
            continue
        for value in table.values:
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "self"
            ):
                out.add(value.attr)
    return out


def test_dispatch_holds_auth_the_gate_and_the_ladder() -> None:
    _, dispatch = _class_and_dispatch()
    body = ast.unparse(dispatch)

    assert "_check_api_token" in body, "il controllo del token non è più nel dispatch"
    assert "_check_workspace_enabled" in body, "il gate workspace.enabled non è più nel dispatch"

    arms = {
        ast.unparse(h.type)
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Try)
        for h in node.handlers
        if h.type
    }
    missing = sorted(_LADDER_ARMS - arms)
    assert not missing, f"rami della scala assenti dal dispatch: {missing}"


def test_no_handler_repeats_the_ladder() -> None:
    """Una scala in un handler è una scala che può divergere da quella condivisa."""
    cls, dispatch = _class_and_dispatch()
    routed = _routed_handlers(dispatch)

    offenders = []
    for fn in cls.body:
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if fn.name not in routed:
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            arms = {ast.unparse(h.type) for h in node.handlers if h.type}
            # ``except Exception`` locale è un'altra cosa: è un default su una
            # lettura che può mancare, non la traduzione di un errore di rotta.
            if arms & _LADDER_ARMS:
                offenders.append(f"{fn.name}:{node.lineno}")

    assert not offenders, (
        f"handler che ri-traducono gli errori del filesystem: {offenders}. "
        "La scala vive nel dispatch: una copia qui può divergere da quella."
    )


def test_every_handler_in_the_table_is_a_real_method() -> None:
    """La tabella e i metodi devono corrispondere, in entrambi i sensi.

    Un handler agganciato fuori dalla tabella non passerebbe dal dispatch, cioè
    girerebbe **senza token e senza gate** — che è la regressione peggiore
    possibile qui, e sarebbe invisibile perché la rotta funzionerebbe.
    """
    cls, dispatch = _class_and_dispatch()
    defined = {
        f.name for f in cls.body
        if isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef))
    }
    table = _routed_handlers(dispatch)
    assert table, "tabella dei path non trovata nel dispatch"
    phantom = sorted(table - defined)
    assert not phantom, f"la tabella nomina metodi che non esistono: {phantom}"
