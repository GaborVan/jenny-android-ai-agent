"""B5 — ``working_dir`` è la base di risoluzione, non un parametro decorativo.

Prima di questa suite ``working_dir`` era dichiarato nello schema, accettato,
assegnato a ``namespace.working_dir`` e **letto da nessuno**: nessun effetto sui
percorsi relativi, nessun effetto sugli import. Il modello ci credeva comunque e
pagava, in token, quattro chiamate in più per cicli interi.

Le due metà del contratto che ogni test qui sotto tiene ferme:

* la BASE DI RISOLUZIONE si sposta (percorsi relativi e ``import`` si misurano
  da ``working_dir``);
* il CONFINE no — resta la radice del workspace, e ``working_dir`` non può né
  uscirne né allargarlo.

Più l'invariante che rende il tutto innocuo per chi non passa il parametro:
senza ``working_dir`` nulla cambia, e ``sys.path`` torna sempre identico.
"""

from __future__ import annotations

import importlib
import sys
import threading

import pytest

from jenny.agent.tools.python_exec import PythonExecTool, PythonNamespace
from jenny.config.tool_schemas import PythonExecConfig

_REFUSED = "outside allowed directory"


@pytest.fixture
def workspace(tmp_path):
    """``(workspace, skill_dir)`` con un file omonimo in entrambi.

    ``data.txt`` esiste sia nella radice sia nella cartella della skill con
    contenuti diversi: è ciò che rende osservabile QUALE base è stata usata.
    """
    ws = tmp_path / "ws"
    skill = ws / "skills" / "foo"
    skill.mkdir(parents=True)
    (ws / "data.txt").write_text("from-root")
    (skill / "data.txt").write_text("from-skill")
    return ws, skill


def _namespace(workspace_dir, *, restrict: bool = True) -> PythonNamespace:
    """Namespace configurato come in produzione (allow/block list reali)."""
    cfg = PythonExecConfig()
    return PythonNamespace(
        working_dir=str(workspace_dir),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict,
        workspace=str(workspace_dir),
    )


@pytest.fixture
def clean_modules():
    """Toglie da ``sys.modules`` i moduli importati dal test."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Gruppo 1 — nessun cambiamento per chi non passa working_dir
# ---------------------------------------------------------------------------


class TestDefaultUnchanged:
    """Il test più importante della serie: il default non si muove."""

    def test_relative_open_still_resolves_to_the_workspace_root(self, workspace):
        ws, _ = workspace
        ns = _namespace(ws)
        stdout, stderr, _ = ns.execute("print(open('data.txt').read())")
        assert stderr == ""
        assert stdout.strip() == "from-root"

    def test_relative_write_still_lands_in_the_workspace_root(self, workspace):
        ws, _ = workspace
        ns = _namespace(ws)
        _, stderr, _ = ns.execute("open('written.txt', 'w').write('x')")
        assert stderr == ""
        assert (ws / "written.txt").read_text() == "x"

    def test_relative_os_listdir_still_lists_the_workspace_root(self, workspace):
        ws, _ = workspace
        ns = _namespace(ws)
        stdout, stderr, _ = ns.execute("import os\nprint(sorted(os.listdir('.')))")
        assert stderr == ""
        assert "data.txt" in stdout and "skills" in stdout

    def test_sys_path_is_untouched_without_working_dir(self, workspace):
        ws, _ = workspace
        before = list(sys.path)
        _namespace(ws).execute("import sys\nprint(len(sys.path))")
        assert sys.path == before


# ---------------------------------------------------------------------------
# Gruppo 2 — working_dir come base di risoluzione
# ---------------------------------------------------------------------------


class TestResolutionBase:
    def test_relative_open_resolves_against_working_dir(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _namespace(ws).execute(
            "print(open('data.txt').read())", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "from-skill"

    def test_relative_write_lands_in_working_dir(self, workspace):
        ws, skill = workspace
        _, stderr, _ = _namespace(ws).execute("open('out.txt', 'w').write('ok')", str(skill))
        assert stderr == ""
        assert (skill / "out.txt").read_text() == "ok"
        assert not (ws / "out.txt").exists()

    def test_pathlib_resolves_against_working_dir(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _namespace(ws).execute(
            "import pathlib\nprint(pathlib.Path('data.txt').read_text())", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "from-skill"

    def test_os_listdir_resolves_against_working_dir(self, workspace):
        ws, skill = workspace
        (skill / "marker.txt").write_text("m")
        stdout, stderr, _ = _namespace(ws).execute(
            "import os\nprint(sorted(os.listdir('.')))", str(skill)
        )
        assert stderr == ""
        assert "marker.txt" in stdout

    def test_absolute_paths_are_unaffected_by_working_dir(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _namespace(ws).execute(
            f"print(open({str(ws / 'data.txt')!r}).read())", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "from-root"


# ---------------------------------------------------------------------------
# Gruppo 3 — import: il motivo per cui B5 esiste (sblocca B6)
# ---------------------------------------------------------------------------


class TestImportFromWorkingDir:
    def test_import_finds_a_module_next_to_working_dir(self, workspace, clean_modules):
        ws, skill = workspace
        (skill / "b5_probe.py").write_text("VALUE = 7\n")
        stdout, stderr, _ = _namespace(ws).execute(
            "import b5_probe\nprint(b5_probe.VALUE)", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "7"

    def test_imported_module_can_import_a_sibling(self, workspace, clean_modules):
        """Il caso di cui B6 ha bisogno: la skill possiede più di un file."""
        ws, skill = workspace
        (skill / "b5_sibling.py").write_text("BASE = 40\n")
        (skill / "b5_main.py").write_text(
            "import b5_sibling\n\nVALUE = b5_sibling.BASE + 2\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(
            "import b5_main\nprint(b5_main.VALUE)", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "42"

    def test_bare_import_still_fails_without_working_dir(self, workspace, clean_modules):
        """Il comportamento storico (il bug osservato sul device) resta tale
        finché la skill non passa ``working_dir``: nessuna directory entra in
        ``sys.path`` di soppiatto."""
        ws, skill = workspace
        (skill / "b5_absent.py").write_text("VALUE = 1\n")
        _, stderr, _ = _namespace(ws).execute("import b5_absent")
        assert "b5_absent" in stderr

    def test_import_works_without_workspace_restriction(self, workspace, clean_modules):
        """``sys.path`` non dipende dal confine: l'import deve funzionare anche
        con ``restrict_to_workspace=False``."""
        ws, skill = workspace
        (skill / "b5_unrestricted.py").write_text("VALUE = 3\n")
        stdout, stderr, _ = _namespace(ws, restrict=False).execute(
            "import b5_unrestricted\nprint(b5_unrestricted.VALUE)", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "3"


# ---------------------------------------------------------------------------
# Gruppo 4 — il confine non si muove
# ---------------------------------------------------------------------------


class TestBoundaryStillHolds:
    def test_working_dir_outside_the_workspace_is_refused(self, workspace, tmp_path):
        ws, _ = workspace
        outside = tmp_path / "outside"
        outside.mkdir()
        _, stderr, _ = _namespace(ws).execute("print('never')", str(outside))
        assert _REFUSED in stderr

    def test_working_dir_escaping_via_dotdot_is_refused(self, workspace):
        ws, skill = workspace
        _, stderr, _ = _namespace(ws).execute("print('never')", str(skill / ".." / ".." / ".."))
        assert _REFUSED in stderr

    def test_relative_escape_from_working_dir_is_refused(self, workspace):
        """``working_dir`` + ``../../..``: la base si sposta, il confine no."""
        ws, skill = workspace
        _, stderr, _ = _namespace(ws).execute(
            "open('../../../escaped.txt', 'w').write('x')", str(skill)
        )
        assert _REFUSED in stderr
        assert not (ws.parent / "escaped.txt").exists()

    def test_absolute_escape_is_still_refused_with_a_working_dir(self, workspace, tmp_path):
        ws, skill = workspace
        outside = tmp_path / "escape_abs.txt"
        _, stderr, _ = _namespace(ws).execute(
            f"open({str(outside)!r}, 'w').write('x')", str(skill)
        )
        assert _REFUSED in stderr
        assert not outside.exists()

    def test_os_remove_outside_is_still_refused_with_a_working_dir(self, workspace, tmp_path):
        ws, skill = workspace
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        _, stderr, _ = _namespace(ws).execute(
            f"import os\nos.remove({str(victim)!r})", str(skill)
        )
        assert _REFUSED in stderr
        assert victim.read_text() == "keep"

    def test_working_dir_via_symlink_out_of_the_workspace_is_refused(self, workspace, tmp_path):
        """Un link dentro il workspace che punta fuori non è una scorciatoia:
        la validazione dereferenzia."""
        ws, _ = workspace
        outside = tmp_path / "outside_link_target"
        outside.mkdir()
        link = ws / "escape_link"
        link.symlink_to(outside, target_is_directory=True)
        _, stderr, _ = _namespace(ws).execute("print('never')", str(link))
        assert _REFUSED in stderr


# ---------------------------------------------------------------------------
# Gruppo 5 — sys.path torna esattamente com'era
# ---------------------------------------------------------------------------


class TestSysPathHygiene:
    def test_sys_path_is_restored_exactly(self, workspace):
        ws, skill = workspace
        before = list(sys.path)
        _namespace(ws).execute("print(1)", str(skill))
        assert sys.path == before

    def test_sys_path_is_restored_after_a_user_exception(self, workspace):
        ws, skill = workspace
        before = list(sys.path)
        _, stderr, _ = _namespace(ws).execute("raise ValueError('boom')", str(skill))
        assert "ValueError" in stderr
        assert sys.path == before

    def test_sys_path_is_restored_after_a_base_exception(self, workspace):
        """``SystemExit`` esce dal percorso normale: il ``finally`` deve
        ripulire lo stesso (vedi B1)."""
        ws, skill = workspace
        before = list(sys.path)
        _, stderr, _ = _namespace(ws).execute("raise SystemExit(2)", str(skill))
        assert "SystemExit" in stderr
        assert sys.path == before

    def test_sys_path_is_untouched_when_working_dir_is_refused(self, workspace, tmp_path):
        ws, _ = workspace
        outside = tmp_path / "outside_refused"
        outside.mkdir()
        before = list(sys.path)
        _, stderr, _ = _namespace(ws).execute("print(1)", str(outside))
        assert _REFUSED in stderr
        assert sys.path == before

    def test_working_dir_is_at_the_head_of_sys_path_during_the_exec(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _namespace(ws).execute("import sys\nprint(sys.path[0])", str(skill))
        assert stderr == ""
        assert stdout.strip() == str(skill)

    def test_out_of_order_exit_removes_the_right_entry(self, workspace, tmp_path):
        """Rimozione PER IDENTITÀ, non per indice.

        Da quando la cattura di stdout è per-thread nulla serializza più la
        finestra guardata: due ``execute()`` concorrenti sono dentro
        enter/exit nello stesso momento, e lo sono anche i chiamanti diretti di
        ``_enter_guard``/``_exit_guard`` (test, futuri chiamanti). Se l'uscita
        togliesse ``sys.path[0]``
        invece della propria voce, il thread che esce per primo porterebbe via
        quella dell'altro e ``sys.path`` resterebbe sporca per sempre.
        """
        ws, skill = workspace
        other = ws / "skills" / "bar"
        other.mkdir()
        before = list(sys.path)
        ns_a = _namespace(ws)
        ns_b = _namespace(ws)
        entered = threading.Event()
        may_exit = threading.Event()
        failure: list[BaseException] = []

        def _worker() -> None:
            try:
                ns_a._enter_guard(str(skill))
                entered.set()
                may_exit.wait(5)
            except BaseException as exc:  # pragma: no cover - diagnostica
                failure.append(exc)
                entered.set()
            finally:
                ns_a._exit_guard()

        thread = threading.Thread(target=_worker)
        thread.start()
        try:
            assert entered.wait(5)
            ns_b._enter_guard(str(other))
            # Uscita in ordine inverso rispetto all'ingresso.
            ns_b._exit_guard()
            assert str(skill) in sys.path
        finally:
            may_exit.set()
            thread.join(5)

        assert not failure
        assert sys.path == before


# ---------------------------------------------------------------------------
# Gruppo 6 — il tool
# ---------------------------------------------------------------------------


def _tool(workspace_dir) -> PythonExecTool:
    cfg = PythonExecConfig()
    return PythonExecTool(
        working_dir=str(workspace_dir),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(workspace_dir),
    )


class TestToolLayer:
    async def test_tool_uses_working_dir_as_the_resolution_base(self, workspace):
        ws, skill = workspace
        out = await _tool(ws).execute(code="print(open('data.txt').read())", working_dir=str(skill))
        assert "from-skill" in out

    async def test_tool_without_working_dir_uses_the_workspace_root(self, workspace):
        ws, _ = workspace
        out = await _tool(ws).execute(code="print(open('data.txt').read())")
        assert "from-root" in out

    async def test_working_dir_does_not_leak_into_the_next_call(self, workspace):
        """Una chiamata con ``working_dir`` non deve avvelenare la successiva
        che non lo passa: la base è per-chiamata."""
        ws, skill = workspace
        tool = _tool(ws)
        first = await tool.execute(code="print(open('data.txt').read())", working_dir=str(skill))
        second = await tool.execute(code="print(open('data.txt').read())")
        assert "from-skill" in first
        assert "from-root" in second

    async def test_tool_reports_a_refused_working_dir_as_a_clean_error(self, workspace, tmp_path):
        ws, _ = workspace
        outside = tmp_path / "outside_tool"
        outside.mkdir()
        out = await _tool(ws).execute(code="print(1)", working_dir=str(outside))
        assert out.startswith("Error: ")
        assert _REFUSED in out
        assert "Traceback" not in out

    async def test_tool_import_from_working_dir_end_to_end(self, workspace, clean_modules):
        ws, skill = workspace
        (skill / "b5_tool_probe.py").write_text("VALUE = 'imported'\n")
        out = await _tool(ws).execute(
            code="import b5_tool_probe\nprint(b5_tool_probe.VALUE)",
            working_dir=str(skill),
        )
        assert "imported" in out

    def test_schema_describes_what_working_dir_actually_does(self, workspace):
        """La descrizione è la parte del fix che il modello legge davvero: se
        torna a essere generica, il parametro ricomincia a mentire."""
        ws, _ = workspace
        text = _tool(ws).parameters["properties"]["working_dir"]["description"]
        assert "sys.path" in text
        assert "import" in text
        assert "workspace" in text


# ---------------------------------------------------------------------------
# Gruppo 7 — R10: `sys.modules` è globale quanto `sys.path`
# ---------------------------------------------------------------------------
#
# `sys.path` viene ripulito per identità all'uscita, ma il modulo che quella
# voce ha fatto caricare resta registrato in `sys.modules` per sempre. Se il
# suo nome è quello di un modulo di sistema NON ancora importato — `csv`,
# `types`, `token`, `copy`, `secrets`, `statistics`, `platform`, `queue` — da
# quel momento è la skill a rispondere a `import <nome>` per tutto il gateway.
# Riprodotto in tre righe prima del fix.


def _unimported_stdlib_name() -> str:
    """Un nome di modulo stdlib non ancora caricato in questo processo.

    Deve esserlo davvero: un nome GIÀ in ``sys.modules`` non è ombreggiabile
    (il finder non viene nemmeno interpellato), quindi userebbe il test come
    prova di qualcosa che non è mai stato in discussione. La lista è di moduli
    puri e senza effetti collaterali all'import.
    """
    for name in ("colorsys", "filecmp", "fileinput", "netrc", "wave", "statistics"):
        if name not in sys.modules and name in getattr(sys, "stdlib_module_names", ()):
            return name
    pytest.skip("no unimported stdlib module left to shadow in this process")


def _allowing(workspace_dir, name: str) -> PythonNamespace:
    """Namespace di produzione più *name* in allowlist.

    Serve ai casi che importano il modulo di sistema VERO: i nomi scelti da
    ``_unimported_stdlib_name`` non sono nell'allowlist di default, e senza
    questo il test misurerebbe il rifiuto d'import invece dello scarico.
    """
    cfg = PythonExecConfig()
    return PythonNamespace(
        working_dir=str(workspace_dir),
        allowed_modules=cfg.allowed_modules + [name],
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(workspace_dir),
    )


class TestModuleShadowing:
    """Dopo l'exec, un ``import`` dell'host deve tornare al modulo di sistema."""

    def test_a_working_dir_module_does_not_shadow_the_stdlib_afterwards(
        self, workspace, clean_modules
    ):
        """Il criterio di accettazione di R10, per intero."""
        ws, skill = workspace
        name = _unimported_stdlib_name()
        (skill / f"{name}.py").write_text("MARKER = 'shadow'\n")

        stdout, stderr, _ = _namespace(ws).execute(
            f"import {name}\nprint({name}.MARKER)", str(skill)
        )
        # Dentro l'exec la skill vince: è il senso di `working_dir`.
        assert stdout.strip() == "shadow"
        assert "Traceback" not in stderr

        # Fuori, l'host deve riavere il modulo vero.
        assert name not in sys.modules
        module = importlib.import_module(name)
        assert not hasattr(module, "MARKER")
        assert "site-packages" not in (module.__file__ or "")
        assert str(skill) not in (module.__file__ or "")

    def test_the_shadowing_is_reported_to_the_model(self, workspace, clean_modules):
        ws, skill = workspace
        name = _unimported_stdlib_name()
        (skill / f"{name}.py").write_text("MARKER = 'shadow'\n")
        _, stderr, _ = _namespace(ws).execute(f"import {name}", str(skill))
        assert "WORKING_DIR SHADOWING" in stderr
        assert name in stderr

    def test_a_plain_skill_module_is_unloaded_too_and_says_nothing(
        self, workspace, clean_modules
    ):
        """Nessuna collisione ⇒ nessun avviso, ma lo scarico avviene lo stesso.

        È il costo dichiarato della scelta: un modulo di skill viene
        ri-eseguito a ogni exec e non conserva stato a livello di modulo. Su
        tutti gli script reali di ``jenny/skills/*/scripts/`` quello stato è
        fatto di regex compilate e costanti, quindi il costo è teorico — ma va
        tenuto fermo da un test, non da una speranza.
        """
        ws, skill = workspace
        (skill / "b5_counter.py").write_text(
            "import itertools\nCOUNTER = next(itertools.count())\nLOADS = ['once']\n"
        )
        ns = _namespace(ws)
        _, stderr, _ = ns.execute("import b5_counter\nb5_counter.LOADS.append('again')", str(skill))
        assert "Traceback" not in stderr
        assert "WORKING_DIR SHADOWING" not in stderr
        assert "b5_counter" not in sys.modules

        stdout, stderr, _ = ns.execute(
            "import b5_counter\nprint(b5_counter.LOADS)", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "['once']"

    def test_a_module_that_was_already_imported_is_left_alone(self, workspace, clean_modules):
        """Lo scarico tocca solo ciò che questo exec ha caricato dalla base."""
        ws, skill = workspace
        (skill / "b5_preloaded.py").write_text("VALUE = 'from-skill'\n")
        sys.path.insert(0, str(skill))
        try:
            preloaded = importlib.import_module("b5_preloaded")
        finally:
            sys.path.remove(str(skill))

        _, stderr, _ = _namespace(ws).execute("import b5_preloaded", str(skill))
        assert "Traceback" not in stderr
        assert sys.modules.get("b5_preloaded") is preloaded

    def test_modules_loaded_from_outside_the_base_are_never_unloaded(
        self, workspace, clean_modules
    ):
        """Un import normale della stdlib dentro l'exec non deve sparire."""
        ws, skill = workspace
        name = _unimported_stdlib_name()
        _, stderr, _ = _allowing(ws, name).execute(f"import {name}", str(skill))
        assert "Traceback" not in stderr
        assert name in sys.modules
        assert str(skill) not in (sys.modules[name].__file__ or "")

    def test_nothing_is_unloaded_without_a_working_dir(self, workspace, clean_modules):
        """Senza base non c'è voce in ``sys.path``, quindi niente da scaricare."""
        ws, _ = workspace
        name = _unimported_stdlib_name()
        _, stderr, _ = _allowing(ws, name).execute(f"import {name}")
        assert "Traceback" not in stderr
        assert name in sys.modules
