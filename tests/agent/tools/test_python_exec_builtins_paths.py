"""I builtin del sandbox devono rispondere alla stessa domanda di ``open()``.

Tre difetti misurati sul layer ``python_exec_builtins`` dopo la riscrittura del
sandbox, tutti della stessa famiglia — *un pezzo del sandbox misura da una base,
un altro da un'altra*:

* **``path_resolve`` misurava dalla cwd del processo.** ``Path(path).resolve()``
  parte dalla cwd, e la cwd di ``python_exec`` è ``/``: ``path_resolve("out.txt")``
  rispondeva ``/out.txt`` mentre ``read_file("out.txt")`` e ``open("out.txt")``,
  nello stesso identico script, leggevano dal workspace (o, da B5, dal
  ``working_dir``). Il modello calcolava un percorso con un builtin e se lo
  vedeva rifiutare da un altro. In più ``Path.resolve()`` passa da ``os.lstat``
  su ogni prefisso: sotto guard quei prefissi sono fuori dal confine, quindi la
  semplice richiesta di un percorso produceva una raffica di WARNING "refused".
* **Nessun builtin diceva quale fosse la base.** All'epoca ``os.getcwd()`` non
  era patchata, quindi il modello che la interrogava otteneva ``/`` e tirava a
  indovinare; ``path_base()`` risponde con la base effettiva di QUESTA
  esecuzione, qualunque sia la modalità. Oggi ``os.getcwd()`` risponde la stessa
  cosa (vedi ``test_python_exec_getcwd.py``: la cwd riportata *è* la base di
  risoluzione) e ``path_base()`` resta il modo esplicito di chiederla — i due
  devono restare d'accordo, ed è un test di quel file a tenerlo fermo.
* **Il fallback ``exec`` di ``_load_wiki_script`` ereditava PEP 563.** Questo
  modulo apre con ``from __future__ import annotations`` e ``exec(code, ns)``
  eredita i flag ``__future__`` del frame chiamante: lo script della skill
  veniva compilato con le annotazioni-stringa e un ``@dataclass`` al suo interno
  moriva con un ``AttributeError`` incomprensibile dentro ``dataclasses``.
  Stesso bug che B12 ha chiuso in ``python_exec.execute()``, in un file che B12
  non aveva toccato.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from typing import Any

import pytest

from jenny.agent.tools.python_exec import PythonNamespace
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig

_REFUSED = "outside allowed directory"
_GUARD_LOGGER = "jenny.agent.tools.python_exec"


class _Recorder:
    """Namespace fittizio: raccoglie i builtin senza montare alcun guard."""

    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def register_function(self, name: str, func: Any) -> None:
        self.functions[name] = func


def _builtins(workspace, *, restrict: bool = True) -> dict[str, Any]:
    recorder = _Recorder()
    _register_builtin_functions(
        recorder,  # type: ignore[arg-type]
        workspace=str(workspace),
        restrict_to_workspace=restrict,
    )
    return recorder.functions


def _guarded_namespace(workspace, *, restrict: bool = True) -> PythonNamespace:
    """Namespace di produzione con i builtin registrati sopra."""
    cfg = PythonExecConfig()
    ns = PythonNamespace(
        working_dir=str(workspace),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict,
        workspace=str(workspace),
    )
    _register_builtin_functions(
        ns, workspace=str(workspace), restrict_to_workspace=restrict
    )
    return ns


@pytest.fixture
def workspace(tmp_path):
    """``(workspace, skill_dir)``, con un ``data.txt`` distinto in ognuna."""
    ws = tmp_path / "ws"
    skill = ws / "skills" / "foo"
    skill.mkdir(parents=True)
    (ws / "data.txt").write_text("from-root")
    (skill / "data.txt").write_text("from-skill")
    return ws, skill


# ---------------------------------------------------------------------------
# Gruppo 1 — path_resolve misura dalla base dei builtin, non dalla cwd
# ---------------------------------------------------------------------------


class TestPathResolveBase:
    def test_relative_path_resolves_against_the_workspace_root(self, workspace):
        ws, _ = workspace
        funcs = _builtins(ws)
        assert funcs["path_resolve"]("out.txt") == str(ws / "out.txt")

    def test_path_resolve_agrees_with_read_file(self, workspace):
        """Il difetto in una riga: il percorso calcolato deve essere leggibile."""
        ws, _ = workspace
        funcs = _builtins(ws)
        assert funcs["read_file"](funcs["path_resolve"]("data.txt")) == "from-root"

    def test_absolute_paths_are_returned_normalized(self, workspace):
        ws, _ = workspace
        funcs = _builtins(ws)
        assert funcs["path_resolve"](str(ws / "sub" / ".." / "x.txt")) == str(ws / "x.txt")

    def test_path_resolve_follows_working_dir(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _guarded_namespace(ws).execute(
            "print(path_resolve('data.txt'))", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == str(skill / "data.txt")

    def test_resolved_path_round_trips_into_open_under_a_working_dir(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _guarded_namespace(ws).execute(
            "print(open(path_resolve('data.txt')).read())", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "from-skill"

    def test_path_resolve_does_not_emit_spurious_refusals(self, workspace, caplog):
        """``Path.resolve()`` sotto guard faceva ``os.lstat`` su ogni prefisso e
        ogni prefisso fuori dal workspace veniva loggato come rifiuto."""
        ws, _ = workspace
        with caplog.at_level(logging.WARNING, logger=_GUARD_LOGGER):
            _, stderr, _ = _guarded_namespace(ws).execute("print(path_resolve('out.txt'))")
        assert stderr == ""
        assert "refused" not in caplog.text

    def test_unrestricted_path_resolve_matches_the_bare_open_base(self, workspace):
        """Senza confine ``open()`` è il builtin nudo e misura dalla cwd: i
        builtin devono dire la stessa cosa, non un'altra."""
        ws, _ = workspace
        funcs = _builtins(ws, restrict=False)
        assert funcs["path_resolve"]("out.txt") == os.path.abspath("out.txt")


# ---------------------------------------------------------------------------
# Gruppo 2 — path_base: la base smette di essere indovinabile
# ---------------------------------------------------------------------------


class TestPathBase:
    def test_reports_the_workspace_root_by_default(self, workspace):
        ws, _ = workspace
        assert _builtins(ws)["path_base"]() == str(ws)

    def test_reports_the_working_dir_when_one_is_active(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _guarded_namespace(ws).execute("print(path_base())", str(skill))
        assert stderr == ""
        assert stdout.strip() == str(skill)

    def test_reports_the_process_cwd_when_unrestricted(self, workspace):
        """Con il confine spento la verità è la cwd del processo, e va detta:
        è da lì che misurano sia ``open()`` sia ``read_file``."""
        ws, _ = workspace
        assert _builtins(ws, restrict=False)["path_base"]() == os.getcwd()

    def test_the_reported_base_is_where_a_relative_write_lands(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _guarded_namespace(ws).execute(
            "write_file('probe.txt', 'x')\nprint(path_base())", str(skill)
        )
        assert stderr == ""
        assert (skill / "probe.txt").exists()
        assert stdout.strip() == str(skill)


# ---------------------------------------------------------------------------
# Gruppo 3 — gli altri helper che riportano un percorso al modello
# ---------------------------------------------------------------------------


class TestReportedPathsAreUsable:
    """Ogni percorso restituito da un builtin deve essere ri-utilizzabile in un
    altro builtin senza rimaneggiamenti: è l'unico contratto che conta."""

    def test_list_dir_entries_can_be_read_back(self, workspace):
        ws, skill = workspace
        funcs = _builtins(ws)
        entries = [p for p in funcs["list_dir"]("skills/foo") if p.endswith("data.txt")]
        assert entries
        assert funcs["read_file"](entries[0]) == "from-skill"

    def test_find_files_results_can_be_read_back(self, workspace):
        ws, _ = workspace
        funcs = _builtins(ws)
        found = funcs["find_files"](".", "**/data.txt")
        assert found
        assert all(os.path.isabs(p) for p in found)
        assert {funcs["read_file"](p) for p in found} == {"from-root", "from-skill"}

    def test_grep_files_keys_can_be_read_back(self, workspace):
        ws, _ = workspace
        funcs = _builtins(ws)
        hits = funcs["grep_files"](".", "from-", "**/*.txt")
        assert hits
        for path in hits:
            assert funcs["read_file"](path)

    def test_list_dir_under_a_working_dir_stays_readable(self, workspace):
        ws, skill = workspace
        stdout, stderr, _ = _guarded_namespace(ws).execute(
            "entries = list_dir('.')\nprint(read_file(entries[0]))", str(skill)
        )
        assert stderr == ""
        assert stdout.strip() == "from-skill"


# ---------------------------------------------------------------------------
# Gruppo 4 — il confine non si muove
# ---------------------------------------------------------------------------


class TestBoundaryUnchanged:
    def test_path_resolve_does_not_grant_access_outside(self, workspace, tmp_path):
        """``path_resolve`` è aritmetica su stringhe: può nominare un percorso
        fuori, ma chi lo USA continua a rifiutarlo."""
        ws, _ = workspace
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")
        funcs = _builtins(ws)
        named = funcs["path_resolve"]("../secret.txt")
        assert named == str(tmp_path / "secret.txt")
        with pytest.raises(OSError, match=_REFUSED):
            funcs["read_file"](named)

    def test_relative_escape_is_still_refused(self, workspace):
        ws, _ = workspace
        funcs = _builtins(ws)
        with pytest.raises(OSError, match=_REFUSED):
            funcs["write_file"]("../../escaped.txt", "x")


# ---------------------------------------------------------------------------
# Gruppo 5 — il fallback exec degli script wiki non eredita PEP 563
# ---------------------------------------------------------------------------


_WIKI_SCRIPT = '''
import os
from dataclasses import dataclass


@dataclass
class Item:
    name: str


def lint(root):
    print("annotation:", type(Item.__annotations__["name"]).__name__)
    print("item:", Item(name="ok").name)
    print("file:", os.path.basename(__file__))
    print("root:", root)
'''


@pytest.fixture
def wiki_workspace(tmp_path, monkeypatch):
    """Workspace con uno script di skill ``lint_wiki.py`` pronto da caricare."""
    ws = tmp_path / "ws"
    scripts = ws / "skills" / "llm-wiki" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "lint_wiki.py").write_text(_WIKI_SCRIPT)
    monkeypatch.setattr(
        "jenny.agent.tools.python_exec_builtins.get_workspace_path", lambda: ws
    )
    return ws


@pytest.fixture
def importlib_broken(monkeypatch):
    """Forza il ramo di fallback: ``importlib`` non deve riuscire."""

    def _boom(*args, **kwargs):
        raise RuntimeError("importlib unavailable")

    monkeypatch.setattr(importlib.util, "spec_from_file_location", _boom)


class TestWikiScriptLoading:
    def test_importlib_route_still_works(self, wiki_workspace):
        out = _builtins(wiki_workspace)["wiki_lint"](str(wiki_workspace))
        assert "annotation: type" in out
        assert "item: ok" in out

    def test_exec_fallback_does_not_inherit_pep_563(self, wiki_workspace, importlib_broken):
        """Pre-fix: ``@dataclass`` esplodeva con
        "AttributeError: 'NoneType' object has no attribute '__dict__'"."""
        out = _builtins(wiki_workspace)["wiki_lint"](str(wiki_workspace))
        assert "annotation: type" in out
        assert "item: ok" in out

    def test_exec_fallback_gives_the_module_a_file(self, wiki_workspace, importlib_broken):
        """Gli script di skill ricavano da ``__file__`` la propria directory."""
        out = _builtins(wiki_workspace)["wiki_lint"](str(wiki_workspace))
        assert "file: lint_wiki.py" in out

    def test_a_relative_root_is_measured_from_the_same_base(self, wiki_workspace):
        """Lo script fa ``Path(root)`` da sé: se gli si passa il relativo nudo
        lo misura dalla cwd del processo, non dal workspace."""
        (wiki_workspace / "wikis" / "main").mkdir(parents=True)
        out = _builtins(wiki_workspace)["wiki_lint"]("wikis/main")
        assert f"root: {wiki_workspace / 'wikis' / 'main'}" in out

    def test_a_root_outside_the_workspace_is_refused(self, wiki_workspace, tmp_path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        with pytest.raises(OSError, match=_REFUSED):
            _builtins(wiki_workspace)["wiki_lint"](str(outside))
