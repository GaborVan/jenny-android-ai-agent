"""``os.getcwd()`` deve nominare la directory da cui i file si aprono davvero.

Il fallimento riprodotto qui è stato catturato sul device un'ora dopo aver reso
`working_dir` reale (B5). Il modello ha scritto da sé, dentro un ciclo di
heartbeat, questa chiamata::

    {"working_dir": "/data/data/com.flagdizero.jenny/files/workspace",
     "code": "import sys, os\\n"
             "sys.path.insert(0, os.path.join(os.getcwd(), 'skills', 'waterbot', 'scripts'))\\n"
             "import re, wb_probe\\n..."}

Passa `working_dir` e poi assume che `os.getcwd()` lo rifletta. Non lo
rifletteva: `getcwd` non era patchata e su quella piattaforma risponde ``/``,
quindi la `join` produceva ``/skills/waterbot/scripts`` — una directory che non
esiste — e l'`import` moriva. È la TERZA incarnazione dello stesso difetto sullo
stesso device: prima la skill prometteva una cwd che il parametro non spostava,
poi il parametro esisteva solo in prosa, ora il parametro funziona ma la
domanda "dove sono?" riceve ancora la risposta di prima.

Il principio che questi test tengono fermo: **la base di risoluzione e la
working directory riportata sono la stessa cosa.** Un sandbox che risolve
``open("x.txt")`` contro una directory e ne nomina un'altra con ``getcwd()`` non
è un guardrail, è una trappola — e ci si cade scrivendo codice del tutto
ragionevole.

Ne discende, gratis, la coerenza che il review di Round 2 aveva schedato a
parte: ``os.path.abspath`` e ``Path.resolve()`` passano entrambe da
``os.getcwd`` (verificato su CPython 3.11, la versione Chaquopy del device, e su
3.14), quindi allineare `getcwd` allinea anche loro all'``open``.
"""

from __future__ import annotations

import os
import sys
import threading

import pytest

from jenny.agent.tools.python_exec import PythonNamespace, _path_guard_bypass
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig


@pytest.fixture
def workspace(tmp_path):
    """``(workspace, scripts)``, con la stessa forma vista sul device.

    ``data.txt`` esiste in entrambe con contenuti diversi: è ciò che rende
    osservabile QUALE base è stata usata.
    """
    ws = tmp_path / "ws"
    scripts = ws / "skills" / "waterbot" / "scripts"
    scripts.mkdir(parents=True)
    (ws / "data.txt").write_text("from-root")
    (scripts / "data.txt").write_text("from-scripts")
    return ws, scripts


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
def clean_sys_path():
    """Ripristina ``sys.path``: qui il codice guardato lo modifica da sé.

    ``_pop_exec_sys_path`` toglie la PROPRIA voce per identità; una inserita
    dal codice dell'agente (che è esattamente ciò che il device faceva) non è
    sua e resterebbe nel processo di test.
    """
    before = list(sys.path)
    yield
    sys.path[:] = before


@pytest.fixture
def clean_modules():
    """Toglie da ``sys.modules`` i moduli importati dal test."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Gruppo 1 — il fallimento del device, riprodotto
# ---------------------------------------------------------------------------


class TestDeviceFailure:
    def test_join_getcwd_then_open_names_a_file_open_accepts(self, workspace):
        """Il caso minimo: si costruisce un percorso con ``getcwd`` e lo si apre."""
        ws, scripts = workspace
        code = (
            "import os\n"
            "p = os.path.join(os.getcwd(), 'data.txt')\n"
            "print(p)\n"
            "print(open(p).read())\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code, str(scripts))
        assert stderr == ""
        named, content = stdout.strip().splitlines()
        assert named == str(scripts / "data.txt")
        assert content == "from-scripts"

    def test_sys_path_insert_from_getcwd_then_bare_import(
        self, workspace, clean_sys_path, clean_modules
    ):
        """``sys.path.insert(0, join(getcwd(), 'scripts'))`` + ``import`` nudo.

        ``working_dir`` è la directory della skill e il modulo sta un livello
        sotto: la voce che il guard mette in ``sys.path`` da sola non basta, ci
        vuole quella che il codice calcola da ``getcwd``. È il caso in cui la
        cwd riportata è davvero l'unico ingrediente.
        """
        ws, scripts = workspace
        skill = scripts.parent
        (scripts / "wb_probe.py").write_text("VALUE = 'probe'\n")
        code = (
            "import sys, os\n"
            "sys.path.insert(0, os.path.join(os.getcwd(), 'scripts'))\n"
            "import wb_probe\n"
            "print(wb_probe.VALUE)\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code, str(skill))
        assert stderr == ""
        assert stdout.strip() == "probe"

    def test_the_exact_shape_the_model_wrote_on_the_device(
        self, workspace, clean_sys_path, clean_modules
    ):
        """``working_dir`` = radice del workspace, cwd + sottodirectory a mano.

        È la chiamata copiata dal log del device, con i soli nomi cambiati.
        """
        ws, scripts = workspace
        (scripts / "wb_probe.py").write_text("VALUE = 'probe'\n")
        code = (
            "import sys, os\n"
            "sys.path.insert(0, os.path.join(os.getcwd(), 'skills', 'waterbot', 'scripts'))\n"
            "import re, wb_probe\n"
            "print(re.sub('e$', '!', wb_probe.VALUE))\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code, str(ws))
        assert stderr == ""
        assert stdout.strip() == "prob!"


# ---------------------------------------------------------------------------
# Gruppo 2 — una sola risposta a "dove sono?"
# ---------------------------------------------------------------------------


class TestOneAnswer:
    def test_abspath_agrees_with_open(self, workspace):
        """``os.path.abspath`` passa da ``getcwd``: era ``/data.txt``."""
        ws, scripts = workspace
        code = (
            "import os\n"
            "print(os.path.abspath('data.txt'))\n"
            "print(open(os.path.abspath('data.txt')).read())\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code, str(scripts))
        assert stderr == ""
        named, content = stdout.strip().splitlines()
        assert named == str(scripts / "data.txt")
        assert content == "from-scripts"

    def test_pathlib_resolve_agrees_with_open(self, workspace):
        ws, scripts = workspace
        code = (
            "import pathlib\n"
            "p = pathlib.Path('data.txt').resolve()\n"
            "print(p)\n"
            "print(p.read_text())\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code, str(scripts))
        assert stderr == ""
        named, content = stdout.strip().splitlines()
        assert named == str(scripts / "data.txt")
        assert content == "from-scripts"

    def test_pathlib_cwd_agrees(self, workspace):
        ws, scripts = workspace
        stdout, stderr, _ = _namespace(ws).execute(
            "import pathlib\nprint(pathlib.Path.cwd())", str(scripts)
        )
        assert stderr == ""
        assert stdout.strip() == str(scripts)

    def test_getcwdb_agrees_with_getcwd(self, workspace):
        """``os.path.abspath(b'...')`` passa da ``getcwdb``, non da ``getcwd``."""
        ws, scripts = workspace
        code = (
            "import os\n"
            "print(os.getcwdb().decode())\n"
            "print(os.path.abspath(b'data.txt').decode())\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code, str(scripts))
        assert stderr == ""
        reported, joined = stdout.strip().splitlines()
        assert reported == str(scripts)
        assert joined == str(scripts / "data.txt")

    def test_path_base_builtin_agrees_with_getcwd(self, workspace):
        """I due modi di chiedere la base devono dare la stessa stringa."""
        ws, scripts = workspace
        ns = _namespace(ws)
        _register_builtin_functions(ns, workspace=str(ws), restrict_to_workspace=True)
        code = (
            "import os\n"
            "print(path_base())\n"
            "print(os.getcwd())\n"
            "print(path_resolve('data.txt'))\n"
        )
        stdout, stderr, _ = ns.execute(code, str(scripts))
        assert stderr == ""
        base, cwd, resolved = stdout.strip().splitlines()
        assert base == cwd == str(scripts)
        assert resolved == str(scripts / "data.txt")

    def test_without_working_dir_getcwd_reports_the_workspace_root(self, workspace):
        """Senza ``working_dir`` la base è la radice del workspace, ed è da lì
        che i percorsi relativi si aprono già oggi: la cwd riportata deve dire
        quello, non la cwd del processo."""
        ws, _ = workspace
        code = (
            "import os\n"
            "print(os.getcwd())\n"
            "print(open('data.txt').read())\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code)
        assert stderr == ""
        reported, content = stdout.strip().splitlines()
        assert reported == str(ws)
        assert content == "from-root"


# ---------------------------------------------------------------------------
# Gruppo 3 — il patch è inerte fuori dal codice guardato
# ---------------------------------------------------------------------------


class TestHostIsUntouched:
    def test_the_cwd_is_the_real_one_again_after_the_exec(self, workspace):
        ws, scripts = workspace
        before = os.getcwd()
        _namespace(ws).execute("import os\nprint(os.getcwd())", str(scripts))
        assert os.getcwd() == before

    def test_a_host_thread_sees_the_real_cwd_during_a_guarded_exec(self, workspace):
        """Il gate è thread-local: mentre un exec è dentro il guard su un
        thread, ogni altro thread continua a vedere la cwd del processo."""
        ws, scripts = workspace
        before = os.getcwd()
        ns = _namespace(ws)
        entered = threading.Event()
        may_exit = threading.Event()
        seen: list[str] = []

        def _worker() -> None:
            try:
                ns._enter_guard(str(scripts))
                seen.append(os.getcwd())
                entered.set()
                may_exit.wait(5)
            finally:
                ns._exit_guard()

        thread = threading.Thread(target=_worker)
        thread.start()
        try:
            assert entered.wait(5)
            assert os.getcwd() == before
        finally:
            may_exit.set()
            thread.join(5)

        assert seen == [str(scripts)]
        assert os.getcwd() == before

    def test_an_unrestricted_exec_still_reports_the_process_cwd(
        self, workspace, tmp_path, monkeypatch
    ):
        """Senza confine nulla viene deviato, quindi non c'è nulla da riportare
        di diverso: ``open('x')`` finisce nella cwd del processo e ``getcwd()``
        deve continuare a nominare quella. Riportare qui ``working_dir``
        rifarebbe la trappola al contrario."""
        ws, scripts = workspace
        landing = tmp_path / "landing"
        landing.mkdir()
        monkeypatch.chdir(landing)
        real = os.getcwd()
        code = (
            "import os\n"
            "print(os.getcwd())\n"
            "open('unrestricted.txt', 'w').write('x')\n"
        )
        stdout, stderr, _ = _namespace(ws, restrict=False).execute(code, str(scripts))
        assert stderr == ""
        assert stdout.strip() == real
        assert (landing / "unrestricted.txt").exists()
        assert not (scripts / "unrestricted.txt").exists()


# ---------------------------------------------------------------------------
# Gruppo 4 — la risposta non cambia identità a metà risoluzione
# ---------------------------------------------------------------------------


class TestBypassInteraction:
    def test_the_reported_cwd_survives_a_bypass_window(self, workspace):
        """``_path_guard_bypass()`` sospende il CONFINE, non l'identità.

        ``_active_path_boundary()``/``_active_path_base()`` tornano ``None``
        sotto bypass perché il macchinario della policy non deve rientrare nei
        wrapper. ``getcwd`` non prende percorsi e non può rientrare in nulla:
        se seguisse il bypass, una ``Path('x').resolve()`` fatta DENTRO la
        risoluzione risponderebbe con la cwd del processo mentre la stessa
        chiamata fuori risponde con la base — cioè la stessa incoerenza che
        questo fix toglie, spostata di un livello.
        """
        ws, scripts = workspace
        ns = _namespace(ws)
        ns._enter_guard(str(scripts))
        try:
            inside = os.getcwd()
            with _path_guard_bypass():
                during = os.getcwd()
        finally:
            ns._exit_guard()
        assert inside == during == str(scripts)


# ---------------------------------------------------------------------------
# Gruppo 5 — il salto di thread porta con sé anche la risposta
# ---------------------------------------------------------------------------


class TestThreadHop:
    def test_getcwd_after_a_to_thread_hop_still_reports_the_base(self, workspace):
        """Il confine attraversa ``asyncio.to_thread`` (R8): la cwd riportata
        deve attraversarlo con lui, altrimenti il worker apre nella base e la
        nomina ``/``."""
        ws, scripts = workspace
        code = (
            "import asyncio, os\n"
            "async def main():\n"
            "    return await asyncio.to_thread(os.getcwd)\n"
            "print(asyncio.run(main()))\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code, str(scripts))
        assert stderr == ""
        assert stdout.strip() == str(scripts)


# ---------------------------------------------------------------------------
# Gruppo 6 — `os.chdir` resta bloccata
# ---------------------------------------------------------------------------


class TestChdirStaysBlocked:
    def test_chdir_cannot_move_the_reported_cwd(self, workspace):
        """La decisione, tenuta ferma da un test invece che da un commento.

        Con ``getcwd`` onesta si potrebbe pensare di lasciare che ``chdir``
        sposti la base per il resto dell'exec. Resta bloccata: la cwd è UNA per
        il processo (la condividono gateway, cron e notifier), e una base
        mutabile creerebbe un secondo stato — con ``sys.path``, inserito una
        volta sola all'ingresso del guard, che smetterebbe di corrispondere alla
        cwd riportata. Un argomento per chiamata si legge meglio.
        """
        ws, scripts = workspace
        code = (
            "import os\n"
            "try:\n"
            "    os.chdir(os.path.dirname(os.getcwd()))\n"
            "except OSError as exc:\n"
            "    print('REFUSED', exc)\n"
            "print(os.getcwd())\n"
        )
        stdout, stderr, _ = _namespace(ws).execute(code, str(scripts))
        assert stderr == ""
        lines = stdout.strip().splitlines()
        assert lines[0].startswith("REFUSED")
        assert "process-global" in lines[0]
        assert "working_dir=" in lines[0]
        assert lines[1] == str(scripts)
