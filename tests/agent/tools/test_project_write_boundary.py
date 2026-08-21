"""Dentro un progetto: si legge dove serve, si scrive solo nella sua cartella.

La prigione di una sessione-progetto è **sulla scrittura**. In lettura non serve
e costava caro: fuori dalla directory privata dell'app non si arriva comunque
(il permesso di storage non ce l'abbiamo, il confine vero lo mette Android),
mentre restringere le letture toglieva a Jenny la possibilità di leggere la
propria skill dentro un progetto — `SkillsLoader` le passa il percorso di
`SKILL.md` perché se lo legga da sé, e sotto scope stretto quel percorso veniva
poi negato. Il caricamento progressivo moriva dentro ogni progetto.

La scrittura invece è l'isolazione che conta: è ciò che tiene un progetto
lontano dai file di un altro, da `USER.md` e da `SOUL.md`.

I cancelli sono due e vanno provati tutti e due, perché hanno forme diverse: i
tool file (dove lettura e scrittura erano già funzioni separate) e `python_exec`
(dove c'era un cancello solo, e dove `open()` grezza è una terza superficie che
non passa dai builtin).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.agent.tools.filesystem import ReadFileTool, WriteFileTool
from jenny.agent.tools.python_exec import PythonNamespace
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.workspace_access import (
    bind_workspace_scope,
    reset_workspace_scope,
    validate_workspace_scope_payload,
)

_REFUSED = "outside allowed directory"


@pytest.fixture
def scoped(tmp_path: Path):
    """Un workspace con una skill, due wiki, e lo scope legato alla prima."""
    ws = tmp_path / "workspace"
    project = ws / "wikis" / "patreon"
    other = ws / "wikis" / "etf"
    skill = ws / "skills" / "llm-wiki"
    for d in (project, other, skill):
        d.mkdir(parents=True)
    (skill / "SKILL.md").write_text("come si tiene una wiki", encoding="utf-8")
    (other / "CLAUDE.md").write_text("l'altra wiki", encoding="utf-8")
    (ws / "SOUL.md").write_text("chi sono", encoding="utf-8")

    scope = validate_workspace_scope_payload(
        {"project_path": str(project), "access_mode": "restricted"},
        default_workspace=ws,
        default_restrict_to_workspace=True,
    )
    token = bind_workspace_scope(scope)
    try:
        yield ws, project, other, skill
    finally:
        reset_workspace_scope(token)


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def register_function(self, name: str, func: Any) -> None:
        self.functions[name] = func


def _builtins(workspace: Path) -> dict[str, Any]:
    recorder = _Recorder()
    _register_builtin_functions(
        recorder,  # type: ignore[arg-type]
        workspace=str(workspace),
        restrict_to_workspace=True,
    )
    return recorder.functions


def _namespace(workspace: Path) -> PythonNamespace:
    cfg = PythonExecConfig()
    ns = PythonNamespace(
        working_dir=str(workspace),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(workspace),
    )
    _register_builtin_functions(
        ns, workspace=str(workspace), restrict_to_workspace=True
    )
    return ns


# ── i tool file ──────────────────────────────────────────────────────────────


class TestToolFile:
    async def test_legge_la_propria_skill(self, scoped):
        """La regressione concreta per cui la regola esiste."""
        ws, _project, _other, skill = scoped
        tool = ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        assert "come si tiene una wiki" in await tool.execute(path=str(skill / "SKILL.md"))

    async def test_legge_unaltra_wiki_se_gliela_si_chiede(self, scoped):
        ws, _project, other, _skill = scoped
        tool = ReadFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        assert "l'altra wiki" in await tool.execute(path=str(other / "CLAUDE.md"))

    async def test_scrive_nella_propria_cartella(self, scoped):
        ws, project, _other, _skill = scoped
        tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        result = await tool.execute(path=str(project / "wiki" / "nota.md"), content="ok")

        assert "Successfully wrote" in result
        assert (project / "wiki" / "nota.md").read_text(encoding="utf-8") == "ok"

    async def test_non_scrive_nella_wiki_di_un_altro_progetto(self, scoped):
        ws, _project, other, _skill = scoped
        tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        assert _REFUSED in await tool.execute(path=str(other / "rubato.md"), content="no")
        assert not (other / "rubato.md").exists()

    async def test_non_riscrive_chi_e_jenny(self, scoped):
        """`SOUL.md` e `USER.md` stanno fuori dalla cartella legata, ed è quello
        che li protegge: nessuna allowlist da mantenere."""
        ws, _project, _other, _skill = scoped
        tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        assert _REFUSED in await tool.execute(path=str(ws / "SOUL.md"), content="no")
        assert (ws / "SOUL.md").read_text(encoding="utf-8") == "chi sono"


# ── i builtin di python_exec ────────────────────────────────────────────────


class TestBuiltinPythonExec:
    def test_read_file_arriva_alla_skill(self, scoped):
        ws, _project, _other, skill = scoped

        assert "come si tiene una wiki" in _builtins(ws)["read_file"](str(skill / "SKILL.md"))

    def test_write_file_resta_nella_cartella_del_progetto(self, scoped):
        ws, project, _other, _skill = scoped

        _builtins(ws)["write_file"](str(project / "dentro.txt"), "ok")

        assert (project / "dentro.txt").read_text(encoding="utf-8") == "ok"

    @pytest.mark.parametrize("fn", ["write_file", "append_file"])
    def test_write_file_fuori_viene_rifiutato(self, scoped, fn):
        ws, _project, other, _skill = scoped

        with pytest.raises(Exception, match=_REFUSED):
            _builtins(ws)[fn](str(other / "rubato.txt"), "no")
        assert not (other / "rubato.txt").exists()

    def test_write_json_fuori_viene_rifiutato(self, scoped):
        ws, _project, other, _skill = scoped

        with pytest.raises(Exception, match=_REFUSED):
            _builtins(ws)["write_json"](str(other / "rubato.json"), {"a": 1})


# ── `open()` grezza dentro il sandbox ───────────────────────────────────────


class TestOpenGrezza:
    """La terza superficie: uno script che non usa i builtin.

    Se `open(..., 'w')` non fosse confinata, il confine sarebbe teatro — basta
    una riga di Python per aggirarlo.
    """

    def test_apre_in_lettura_fuori_dal_progetto(self, scoped):
        ws, _project, _other, skill = scoped

        stdout, stderr, _ = _namespace(ws).execute(
            f"print(open({str(skill / 'SKILL.md')!r}).read())", str(ws)
        )

        assert stderr == ""
        assert "come si tiene una wiki" in stdout

    def test_non_apre_in_scrittura_fuori_dal_progetto(self, scoped):
        ws, _project, other, _skill = scoped
        target = other / "rubato.txt"

        _stdout, stderr, _ = _namespace(ws).execute(
            f"open({str(target)!r}, 'w').write('no')", str(ws)
        )

        assert _REFUSED in stderr
        assert not target.exists()

    def test_apre_in_scrittura_dentro_il_progetto(self, scoped):
        ws, project, _other, _skill = scoped
        target = project / "dentro.txt"

        _stdout, stderr, _ = _namespace(ws).execute(
            f"open({str(target)!r}, 'w').write('ok')", str(ws)
        )

        assert stderr == ""
        assert target.read_text(encoding="utf-8") == "ok"


# ── senza scope non cambia niente ───────────────────────────────────────────


class TestSenzaProgetto:
    async def test_la_sessione_personale_scrive_in_tutto_il_workspace(self, tmp_path: Path):
        ws = tmp_path / "workspace"
        (ws / "wikis" / "patreon").mkdir(parents=True)
        tool = WriteFileTool(workspace=ws, allowed_dir=ws, restrict_to_workspace=True)

        result = await tool.execute(path=str(ws / "wikis" / "patreon" / "x.md"), content="ok")

        assert "Successfully wrote" in result

    def test_e_i_builtin_pure(self, tmp_path: Path):
        ws = tmp_path / "workspace"
        (ws / "sotto").mkdir(parents=True)

        _builtins(ws)["write_file"](str(ws / "sotto" / "x.txt"), "ok")

        assert (ws / "sotto" / "x.txt").read_text(encoding="utf-8") == "ok"
