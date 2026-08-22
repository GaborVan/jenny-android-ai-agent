"""Sola lettura: cosa si chiude, e soprattutto cosa **non** si chiude.

Passo **4.2** di ``roadmap/progetti-passi.md``.

I cancelli sono otto, non tre. Tre sono quelli che il confine di scrittura del
passo 1.3 aveva già sdoppiato — i tool file, i builtin di ``python_exec``,
l'``open()`` grezza — e cinque sono nuovi: i mutatori di ``os``, ``os.open`` con
i soli flag di scrittura, ``symlink``, ``rmtree``, ``wiki_scaffold``. Più i tool
che si portano la destinazione da sé, che stanno in
``tests/security/test_readonly_write_surfaces.py``.

**La metà che conta di questo file è la seconda.** Chiudere si sbaglia in due
versi, e il verso pericoloso non è quello che sembra: una scrittura che passa è
un guasto, ma una *lettura* che si chiude rende l'interruttore inutilizzabile —
e un interruttore che nessuno accende non protegge niente. Le sonde
(``stat``, ``access``), gli enumeratori (``listdir``, ``walk``), ``read_file`` e
``open()`` in lettura devono continuare a funzionare identici.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jenny.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from jenny.agent.tools.python_exec import PythonNamespace
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig
from jenny.security.workspace_access import (
    build_workspace_scope,
    enter_workspace_scope,
)
from jenny.security.workspace_policy import ReadOnlyTurnError

_REFUSED = "read-only"


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def register_function(self, name: str, func: Any) -> None:
        self.functions[name] = func


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Un workspace con un file da leggere e uno da modificare."""
    (tmp_path / "leggibile.txt").write_text("ciao\n", encoding="utf-8")
    (tmp_path / "modificabile.txt").write_text("prima\n", encoding="utf-8")
    (tmp_path / "cartella").mkdir()
    return tmp_path


@pytest.fixture
def readonly(ws: Path):
    with enter_workspace_scope(build_workspace_scope(ws, "restricted").without_write_access()):
        yield ws


@pytest.fixture
def writable(ws: Path):
    with enter_workspace_scope(build_workspace_scope(ws, "restricted")):
        yield ws


def _builtins(ws: Path) -> dict[str, Any]:
    rec = _Recorder()
    _register_builtin_functions(rec, workspace=str(ws), restrict_to_workspace=True)  # type: ignore[arg-type]
    return rec.functions


def _namespace(ws: Path) -> PythonNamespace:
    cfg = PythonExecConfig()
    return PythonNamespace(
        working_dir=str(ws),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=str(ws),
    )


# ── Cancello 1: i tool file ──────────────────────────────────────────────


async def test_write_file_is_refused(readonly: Path) -> None:
    result = await WriteFileTool(str(readonly)).execute(path="nuovo.txt", content="x")
    assert _REFUSED in result
    assert not (readonly / "nuovo.txt").exists(), "rifiutato ma scritto è il caso peggiore"


async def test_edit_file_is_refused_and_leaves_the_file_alone(readonly: Path) -> None:
    result = await EditFileTool(str(readonly)).execute(
        path="modificabile.txt", old_text="prima", new_text="dopo"
    )
    assert _REFUSED in result
    assert (readonly / "modificabile.txt").read_text(encoding="utf-8") == "prima\n"


async def test_read_file_still_works(readonly: Path) -> None:
    """La metà che conta: leggere è l'unica cosa che questa modalità permette."""
    result = await ReadFileTool(str(readonly)).execute(path="leggibile.txt")
    assert "ciao" in result


async def test_with_writing_on_the_same_call_lands(writable: Path) -> None:
    """Il cancello deve essere il *flag*, non un effetto collaterale del fixture."""
    await WriteFileTool(str(writable)).execute(path="nuovo.txt", content="x")
    assert (writable / "nuovo.txt").read_text(encoding="utf-8") == "x"


# ── Cancello 2: i builtin di python_exec ─────────────────────────────────


@pytest.mark.parametrize(
    ("fn", "args"),
    [
        ("write_file", ("b.txt", "x")),
        ("append_file", ("b.txt", "x")),
        ("write_json", ("b.json", {"a": 1})),
    ],
)
def test_the_builtin_writers_are_refused(readonly: Path, fn: str, args: tuple) -> None:
    env = _builtins(readonly)
    with pytest.raises(ReadOnlyTurnError):
        env[fn](*args)
    assert not (readonly / args[0]).exists()


def test_the_builtin_readers_are_untouched(readonly: Path) -> None:
    env = _builtins(readonly)
    assert "ciao" in env["read_file"]("leggibile.txt")


def test_wiki_lint_is_not_collateral_damage(readonly: Path) -> None:
    """``wiki_lint`` e ``wiki_scaffold`` condividono un helper: uno legge, l'altro crea.

    Chiudere l'helper condiviso avrebbe spento due letture per fermare una
    scrittura, ed è la ragione per cui il rifiuto sta in ``_write_path`` e in
    ``wiki_scaffold``, non in ``_enforce_path``.
    """
    env = _builtins(readonly)
    with pytest.raises(ReadOnlyTurnError):
        env["wiki_scaffold"](str(readonly / "nuova"), "Titolo")
    # `wiki_lint` non deve fallire *per la sola lettura*: qualunque altro esito
    # (anche un errore sulla wiki inesistente) va bene.
    try:
        env["wiki_lint"](str(readonly))
    except ReadOnlyTurnError:  # pragma: no cover - è il difetto che cerchiamo
        pytest.fail("wiki_lint è una lettura e non deve cadere sulla sola lettura")
    except Exception:
        pass


# ── Cancello 3: open() grezza e i patch di io/os ─────────────────────────


def test_raw_open_for_writing_is_refused(readonly: Path) -> None:
    ns = _namespace(readonly)
    with pytest.raises(ReadOnlyTurnError):
        ns._resolve_workspace_write(str(readonly / "c.txt"), for_write=True)


def test_raw_open_for_reading_is_not(readonly: Path) -> None:
    ns = _namespace(readonly)
    assert ns._resolve_workspace_write(str(readonly / "leggibile.txt")) is not None


# ── I mutatori di os, e le sonde che restano ─────────────────────────────
#
# Da qui in giù si passa da ``ns.execute``, cioè dal codice vero: i patch di
# ``os`` si montano dentro la finestra guardata, e provarli chiamando ``os``
# direttamente dal test proverebbe l'``os`` del gateway, non quello del sandbox.


def _run(ws: Path, code: str) -> tuple[str, str]:
    stdout, stderr, _ = _namespace(ws).execute(code)
    return stdout, stderr


@pytest.mark.parametrize(
    "code",
    [
        "import os; os.mkdir('nuova')",
        "import os; os.remove('leggibile.txt')",
        "import os; os.rename('leggibile.txt', 'altro.txt')",
        "import os; os.chmod('leggibile.txt', 0o600)",
        "import os; os.utime('leggibile.txt', None)",
        "import shutil; shutil.rmtree('cartella')",
        "import os; os.symlink('leggibile.txt', 'link.txt')",
        "import os; os.open('nuovo.txt', os.O_WRONLY | os.O_CREAT)",
        "open('nuovo.txt', 'w').write('x')",
        "from pathlib import Path; Path('nuovo.txt').write_text('x')",
    ],
    ids=["mkdir", "remove", "rename", "chmod", "utime", "rmtree", "symlink",
         "os.open-w", "open-w", "pathlib-write"],
)
def test_every_mutating_route_is_refused(readonly: Path, code: str) -> None:
    """Dieci strade diverse per la stessa cosa, e nessuna deve arrivare."""
    stdout, stderr = _run(readonly, code)
    # Sul nome dell'eccezione e non sul testo: un traceback che contenesse
    # "read-only" per un'altra ragione farebbe passare questo test a vuoto.
    assert "ReadOnlyTurnError" in stdout + stderr, f"passata: {code!r}"
    assert not (readonly / "nuova").exists()
    assert not (readonly / "nuovo.txt").exists()
    assert not (readonly / "link.txt").exists()
    assert not (readonly / "altro.txt").exists()
    assert (readonly / "leggibile.txt").exists()
    assert (readonly / "cartella").is_dir()


@pytest.mark.parametrize(
    "code",
    [
        "import os; print(os.listdir('.'))",
        "import os; print(os.stat('leggibile.txt').st_size)",
        "import os; print(os.access('leggibile.txt', os.R_OK))",
        "import os; print(os.path.exists('leggibile.txt'))",
        "import os; print(sum(1 for _ in os.walk('.')))",
        "print(open('leggibile.txt').read())",
        "from pathlib import Path; print(Path('leggibile.txt').read_text())",
        # `O_RDONLY` è 0: senza la maschera sui flag si chiuderebbe anche questa.
        "import os; fd = os.open('leggibile.txt', os.O_RDONLY); print(os.read(fd, 4)); os.close(fd)",
    ],
    ids=["listdir", "stat", "access", "exists", "walk", "open-r", "pathlib-read", "os.open-r"],
)
def test_reading_is_untouched(readonly: Path, code: str) -> None:
    """La metà che conta.

    Chiudere si sbaglia in due versi, e quello pericoloso non è quello che
    sembra: una scrittura che passa è un guasto, ma una lettura che si chiude
    rende l'interruttore inutilizzabile — e un interruttore che nessuno accende
    non protegge niente.
    """
    stdout, stderr = _run(readonly, code)
    assert "ReadOnlyTurnError" not in stdout + stderr, f"chiusa per sbaglio: {code!r}"
    assert stdout.strip(), f"nessun output da {code!r}: {stderr}"


@pytest.mark.parametrize(
    "code",
    ["import os; os.mkdir('nuova')", "open('nuovo.txt', 'w').write('x')"],
    ids=["mkdir", "open-w"],
)
def test_with_writing_on_the_same_route_lands(writable: Path, code: str) -> None:
    """Controprova: i patch sono per processo, il flag è del *turno*."""
    stdout, stderr = _run(writable, code)
    assert "ReadOnlyTurnError" not in stdout + stderr
    assert (writable / "nuova").is_dir() or (writable / "nuovo.txt").exists()
