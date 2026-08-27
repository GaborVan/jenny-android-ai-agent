"""Il confine asimmetrico visto da un subagent — passo **T4.5**.

L'allargamento delle letture dentro un progetto
(``_FsTool._read_allowed_root``) esisteva solo per l'agente principale: un
subagent riceve come ``workspace`` la cartella del progetto
(``SubagentManager._tool_context``), quindi la sua radice di lettura *era* il
progetto e la ragione dichiarata dell'allargamento — il caricamento progressivo
delle skill che muore dentro ogni progetto — restava intatta proprio dove pesa
di piu': sotto ``orchestratorMode`` i subagent sono **gli unici** attori con i
tool di scrittura dentro un progetto.

I test di questo file sono per meta' **assenze**, e sono quelle che contano:
allargare le letture non deve allargare di un millimetro le scritture. Le prove
passano dai tool VERI costruiti da ``SubagentManager`` dentro
``enter_workspace_scope``, non da un ``ReadFileTool`` istanziato a mano: e' la
differenza fra provare il codice e provare il cablaggio.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.tools.filesystem import ReadFileTool, WriteFileTool
from jenny.config.paths import get_media_dir, set_workspace_dir
from jenny.config.schema import AgentDefaults, ToolsConfig
from jenny.runtime.context import get_runtime_context
from jenny.security.workspace_access import build_workspace_scope
from jenny.utils.helpers import sync_workspace_templates

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars

_SKILL = "skills/llm-wiki/SKILL.md"
_REFUSED = "outside allowed directory"


@pytest.fixture
def install(tmp_path: Path) -> Any:
    """Un'installazione vera, con un progetto dentro, montata su ``RuntimeContext``.

    Il workspace di processo va spostato davvero: la radice dell'installazione
    non e' un parametro che i tool ricevono, e' quella che il runtime dichiara.
    """
    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    # Il prompt di sistema di un subagent si costruisce da un template che vive
    # nel workspace: senza la sync, ``_run_subagent`` muore prima delle sonde.
    sync_workspace_templates(root, silent=True)
    (root / "skills" / "llm-wiki").mkdir(parents=True, exist_ok=True)
    (root / _SKILL).write_text("# come si scrive una wiki\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# chi sono i miei agenti\n", encoding="utf-8")
    project = root / "wikis" / "patreon"
    (project / "wiki").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    previous = get_runtime_context().workspace_dir
    set_workspace_dir(str(root))
    try:
        yield SimpleNamespace(root=root, project=project, outside=outside)
    finally:
        set_workspace_dir(str(previous) if previous is not None else "")


async def _tools_inside(project: Path, *, writable: bool = True) -> dict[str, Any]:
    """I tool di un subagent legato a ``project``, eseguiti dentro il suo scope.

    Ritorna una mappa nome -> risultato delle sonde: i tool esistono solo
    dentro ``enter_workspace_scope``, quindi le letture e le scritture si fanno
    qui, dal fake ``runner.run``.
    """
    from jenny.agent.subagent import SubagentManager, SubagentSpec, SubagentStatus
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    scope = build_workspace_scope(project, "restricted")
    if not writable:
        scope = scope.without_write_access()
    mgr = SubagentManager(
        provider=provider,
        workspace=project.parents[1],
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        tools_config=ToolsConfig(restrict_to_workspace=True),
    )
    mgr._announce_result = AsyncMock()
    probes: dict[str, Any] = {}

    async def fake_run(spec: Any) -> Any:
        read = spec.tools.get("read_file")
        write = spec.tools.get("write_file")
        assert read is not None and write is not None
        root = project.parents[1]
        probes["skill"] = await read.execute(path=str(root / _SKILL))
        probes["agents"] = await read.execute(path=str(root / "AGENTS.md"))
        media = get_media_dir() / "allegato.txt"
        media.write_text("un allegato dell'utente", encoding="utf-8")
        probes["media"] = await read.execute(path=str(media))
        probes["inside"] = await write.execute(
            path=str(project / "wiki" / "nota.md"), content="dentro"
        )
        probes["write_workspace_root"] = await write.execute(
            path=str(root / "SOUL.md"), content="fuori"
        )
        probes["write_other_project"] = await write.execute(
            path=str(root / "wikis" / "etf" / "nota.md"), content="fuori"
        )
        probes["write_outside"] = await write.execute(
            path=str(project.parents[2] / "outside" / "nota.md"), content="fuori"
        )
        return SimpleNamespace(
            stop_reason="done", final_content="done", error=None, tool_events=[]
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    status = SubagentStatus(
        task_id="sub-1",
        label="label",
        task_description="task",
        started_at=time.monotonic(),
    )
    await mgr._run_subagent(
        "sub-1",
        SubagentSpec(
            task="task",
            label="label",
            origin_channel="test",
            origin_chat_id="c1",
            workspace_scope=scope,
        ),
        status,
    )
    assert probes, "il fake runner non e' stato eseguito: le sonde non hanno girato"
    return probes


# ── (a) le letture arrivano all'installazione ───────────────────────────────


async def test_subagent_inside_a_project_reads_the_installation(install: Any) -> None:
    """La ragione dichiarata dell'allargamento, misurata sul subagent.

    ``SKILL.md`` e' il caso concreto: ``SkillsLoader`` passa quel percorso
    perche' l'agente se lo legga, e sotto uno scope stretto veniva negato.
    """
    probes = await _tools_inside(install.project)

    assert "come si scrive una wiki" in probes["skill"], probes["skill"]
    assert "chi sono i miei agenti" in probes["agents"], probes["agents"]


async def test_the_media_dir_is_inside_what_the_widening_already_opens(install: Any) -> None:
    """``_resolve_read(include_media_dir=True)``: la domanda posta dal passo T4.5.

    Risposta misurata: la cartella dei media **e' dentro l'installazione**
    (``<workspace>/.jenny/media``, v. ``get_data_dir``), quindi per un tool
    costruito da ``create()`` quel flag e' ora ridondante — non e' una seconda
    radice, e' un pezzo della prima. Resta necessario per chi ha una restrizione
    esplicita del costruttore piu' stretta (Atlas, i tool di lettura del
    gardener), ed e' l'unica ragione per non toglierlo.

    E si', da dentro un progetto ci si deve arrivare: la' finiscono gli allegati
    che l'utente manda in chat, e un subagent a cui si chiede di guardare
    l'immagine appena inviata non puo' dipendere da quale progetto era aperto.
    """
    assert get_media_dir().is_relative_to(install.root), (
        "la cartella dei media e' uscita dal workspace: `include_media_dir` "
        "tornerebbe a essere una radice a se', e questa nota va riscritta"
    )
    probes = await _tools_inside(install.project)

    assert "un allegato dell'utente" in probes["media"], probes["media"]


# ── (b) le scritture NON si allargano ───────────────────────────────────────


@pytest.mark.parametrize(
    "probe",
    ["write_workspace_root", "write_other_project", "write_outside"],
)
async def test_subagent_write_outside_the_project_is_still_refused(
    install: Any, probe: str
) -> None:
    """Le tre forme in cui un confine di scrittura sbagliato si manifesta.

    Parametrizzato di proposito: un fallimento deve dire QUALE delle tre e'
    passata, e la radice del workspace (dove stanno ``SOUL.md`` e ``USER.md``)
    e' quella che l'allargamento delle letture rende raggiungibile in lettura —
    quindi e' la piu' facile da lasciar scivolare anche in scrittura.
    """
    probes = await _tools_inside(install.project)

    assert "Successfully wrote" in probes["inside"], probes["inside"]
    assert _REFUSED in probes[probe], probes[probe]


def test_the_write_boundary_cannot_see_the_read_only_extra_dirs() -> None:
    """La proprieta' strutturale che rende (b) vera per costruzione.

    ``_extra_read_allowed_dirs`` — dove entra la radice dell'installazione —
    non compare in nessun percorso di scrittura. Un test di comportamento
    prova i tre posti che ho pensato; questo prova che non ce n'e' un quarto.
    """
    import ast
    import inspect

    from jenny.agent.tools import filesystem as fs_mod

    tree = ast.parse(inspect.getsource(fs_mod))
    writers = {"_resolve_write", "_commit_write"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in writers:
            names = {
                child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)
            }
            assert "_extra_read_allowed_dirs" not in names, (
                f"{node.name} legge le directory di sola lettura: l'allargamento "
                "delle letture e' diventato un allargamento delle scritture"
            )


# ── (c) la sola lettura resta sola lettura ──────────────────────────────────


async def test_readonly_subagent_still_cannot_write_anywhere(install: Any) -> None:
    """Il turno in sola lettura non guadagna niente dall'allargamento.

    Anche *dentro* il progetto: e' l'unico posto dove un turno scrivibile
    riesce, quindi e' l'unico dove l'assenza si vede.
    """
    probes = await _tools_inside(install.project, writable=False)

    for name, out in probes.items():
        if name.startswith("write_") or name == "inside":
            assert "refused write to" in out, f"{name}: {out}"


# ── (d) l'agente principale non cambia ──────────────────────────────────────


async def test_main_agent_tools_are_unchanged(install: Any) -> None:
    """Il ctx dell'agente principale ha ``workspace`` = radice: nessun extra nuovo."""
    from jenny.agent.tools.context import ToolContext
    from jenny.agent.tools.file_state import FileStates

    ctx = ToolContext(
        config=ToolsConfig(restrict_to_workspace=True),
        workspace=str(install.root),
        file_states=FileStates(),
    )
    read = ReadFileTool.create(ctx)
    write = WriteFileTool.create(ctx)

    assert read._extra_read_allowed_dirs == [install.root / "skills"] or (
        # ``expose_package_source`` aggiunge la radice del pacchetto, se c'e'.
        read._extra_read_allowed_dirs[0] == install.root / "skills"
    )
    assert install.root not in read._extra_read_allowed_dirs
    assert "chi sono i miei agenti" in await read.execute(path=str(install.root / "AGENTS.md"))
    assert _REFUSED in await write.execute(
        path=str(install.outside / "nota.md"), content="fuori"
    )
