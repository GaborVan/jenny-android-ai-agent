"""La porta dei thread nudi ha DUE metà, e anche quella della sola lettura è aperta.

T4.16. ``tests/agent/tools/test_python_exec_threads.py::TestKnownRemainingDoors``
misura la metà «confine di percorso» di questa porta: un thread nudo raggiunto
per gli interni di un modulo consentito
(``asyncio.base_events.threading.Thread``) legge un file fuori dal workspace.
Questo file misura **l'altra metà**, che ha la stessa radice e nessuna prova:
``_guarded_exec_is_active()`` è thread-local e su un thread mai patchato torna
False, quindi ``_refuse_write_if_readonly`` diventa un no-op e un turno in
**sola lettura** scrive.

Perché pinnarla invece di chiuderla. Chiuderla vuol dire patchare
``threading.Thread`` a livello di processo, cioè mettersi in mezzo a ogni thread
del gateway: è un limite **accettato e documentato** (v. il commento TRUST
BOUNDARY in ``python_exec.py`` e la sezione «read-only turn» di
``.agent/security.md``). Ma da oggi quei documenti affermano un fatto
*misurato*, e un fatto misurato che nessuno riesegue diventa una leggenda: se
qualcuno chiude la porta, questi test falliscono ed è il momento di aggiornare i
due documenti — e di valutare se ``templates/agent/readonly.md`` possa tornare a
promettere più di quel che promette oggi.

Il salto via ``asyncio`` è invece coperto in entrambe le metà (T4.13): la prova
sta nel file citato sopra, non qui, per non avere due copie della stessa
asserzione.
"""

from __future__ import annotations

import dataclasses

import pytest

from jenny.config.tool_schemas import PythonExecConfig

_UPDATE_THE_DOCS = (
    "la porta dei thread nudi sembra chiusa: se è voluto, aggiorna il commento "
    "TRUST BOUNDARY in python_exec.py, la sezione read-only di .agent/security.md "
    "e TestKnownRemainingDoors"
)


def _tool(workspace, *, restrict: bool):
    """Il tool vero, come lo costruisce ``AgentLoop``. Serve il percorso async."""
    from jenny.agent.tools.python_exec import PythonExecTool
    from jenny.agent.tools.python_exec_builtins import _register_builtin_functions

    cfg = PythonExecConfig()
    tool = PythonExecTool(
        working_dir=str(workspace),
        timeout=30,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict,
        workspace=str(workspace),
    )
    _register_builtin_functions(
        tool.namespace, workspace=str(workspace), restrict_to_workspace=restrict
    )
    return tool


def _raw_thread_code(target) -> str:
    """Codice del modello che scrive DOPO il salto su un thread nudo.

    Il confronto dei due ``ident`` non è decorazione: un test che (per una
    qualunque ragione) eseguisse la scrittura sul thread di partenza passerebbe
    raccontando di aver provato un salto. ``T.get_ident`` invece di
    ``import threading``, che è rifiutato — è la stessa strada di
    ``TestKnownRemainingDoors``.
    """
    return (
        "import asyncio\n"
        "T = asyncio.base_events.threading\n"
        "outer = T.get_ident()\n"
        "box = []\n"
        "def probe():\n"
        "    try:\n"
        f"        open({str(target)!r}, 'w').write('BUCATO')\n"
        "        box.append((T.get_ident(), 'WROTE'))\n"
        "    except BaseException as exc:\n"
        "        box.append((T.get_ident(), type(exc).__name__))\n"
        "t = T.Thread(target=probe)\n"
        "t.start(); t.join()\n"
        "inner, verdict = box[0]\n"
        "print('SAME-THREAD' if inner == outer else 'HOPPED', verdict)\n"
    )


@pytest.mark.parametrize("restrict", [True, False], ids=["restricted", "unrestricted"])
async def test_a_raw_thread_still_writes_during_a_read_only_turn(tmp_path, restrict: bool):
    """Misurato il 23/08/2026, con ``restrict_to_workspace`` acceso e spento.

    Parametrizzato su ``restrict_to_workspace`` per la ragione di T4.3: la sola
    lettura è una proprietà del **turno**, quindi non deve dipendere dal confine
    di percorso — e qui infatti cade in entrambe le modalità, che è precisamente
    il punto da non lasciare implicito nei documenti.
    """
    from jenny.security.workspace_access import (
        build_workspace_scope,
        enter_workspace_scope,
    )

    target = tmp_path / "m.txt"
    target.write_text("prima\n", encoding="utf-8")
    scope = build_workspace_scope(tmp_path, "restricted").without_write_access()
    scope = dataclasses.replace(scope, restrict_to_workspace=restrict)

    with enter_workspace_scope(scope):
        out = await _tool(tmp_path, restrict=restrict).execute(
            code=_raw_thread_code(target)
        )

    assert "HOPPED" in out, f"il test non ha saltato niente: {out!r}"
    assert "WROTE" in out, f"{_UPDATE_THE_DOCS}: {out!r}"
    assert target.read_text(encoding="utf-8") == "BUCATO", _UPDATE_THE_DOCS
