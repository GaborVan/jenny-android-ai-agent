"""Le due evasioni residue trovate implementando B4, più l'allineamento da B5.

B4 ha chiuso la superficie ``os.*``, ma ha lasciato due buchi che il piano non
prevedeva perché nessuno li aveva misurati:

**Evasione 1 — ``builtins.open`` era guardato solo dentro il namespace.**
L'``open`` confinato viveva in ``__builtins__['open']`` del namespace guardato e
sull'attributo ``io.open``. Un modulo della stdlib risolve però ``open`` come
nome globale, e i globali di un modulo ricadono su ``builtins``: con
``restrict_to_workspace=True``,
``shutil.copyfile('/fuori/secret.txt', '<ws>/stolen.txt')`` copiava dentro il
contenuto di fuori. Lo ``os.stat`` che ``_samefile`` fa prima veniva rifiutato,
ma ``copyfile`` lo ingoia in un ``except OSError`` e apre lo stesso.

**Evasione 2 — la blocklist di processi si installava troppo tardi.**
Gli stub di ``_OS_BLOCKED_FUNCTIONS`` li montava ``_guarded_import`` sull'
``import os`` ESPLICITO, quindi ``import shutil; shutil.os.system('true')``
girava. Stessa dinamica per cui B4 aveva già spostato i patch di path in
``_enter_guard``.

**Allineamento (consegna da B5).** I builtin registrati (``read_file``,
``write_file``, …) misuravano i percorsi relativi dalla radice del workspace
mentre ``open()`` li misurava dal ``working_dir``: sotto un ``working_dir``,
``open("x.txt")`` e ``read_file("x.txt")`` parlavano di due file diversi.

Il gruppo che conta più di tutti resta l'ultimo: ``builtins.open`` è attraversato
dall'intero interprete, quindi il codice host su altri thread deve restare
inalterato — è lo stesso gate per cui i patch di B4 sono guard-gated.
"""

from __future__ import annotations

import builtins
import logging
import shutil
import threading

import pytest

from jenny.agent.tools.python_exec import PythonNamespace, _real_builtins_open
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.config.tool_schemas import PythonExecConfig

_REFUSED = "outside allowed directory"
_BLOCKED = "not available on this platform"
_GUARD_LOGGER = "jenny.agent.tools.python_exec"


def _namespace(workspace, *, restrict: bool = True, builtins_too: bool = False) -> PythonNamespace:
    """Namespace configurato come in produzione (allow/block list reali)."""
    cfg = PythonExecConfig()
    ns = PythonNamespace(
        working_dir=str(workspace),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict,
        workspace=str(workspace),
    )
    if builtins_too:
        _register_builtin_functions(
            ns, workspace=str(workspace), restrict_to_workspace=restrict
        )
    return ns


@pytest.fixture
def sandbox(tmp_path):
    """``(workspace, outside)`` con un file già presente in ciascuno."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "inside.txt").write_text("inside")
    (outside / "secret.txt").write_text("secret")
    return workspace, outside


def _run(ns: PythonNamespace, code: str, working_dir: str | None = None) -> tuple[str, str]:
    stdout, stderr, _ = ns.execute(code, working_dir)
    return stdout, stderr


def _refusals(caplog) -> list[str]:
    """I WARNING di rifiuto emessi dal guard durante il test."""
    return [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]


# ---------------------------------------------------------------------------
# Evasione 1 — il `builtins.open` reale
# ---------------------------------------------------------------------------


def test_shutil_copyfile_from_outside_the_workspace_is_refused(sandbox) -> None:
    """L'evasione riprodotta, non teorizzata: leggeva fuori e scriveva dentro."""
    workspace, outside = sandbox
    stolen = workspace / "stolen.txt"
    ns = _namespace(workspace)
    _, stderr = _run(
        ns,
        f"import shutil; shutil.copyfile({str(outside / 'secret.txt')!r}, {str(stolen)!r})",
    )
    assert _REFUSED in stderr
    assert not stolen.exists()


def test_shutil_copyfile_to_outside_the_workspace_is_refused(sandbox) -> None:
    """L'altra metà: il contenuto di dentro non deve poter uscire."""
    workspace, outside = sandbox
    leaked = outside / "leaked.txt"
    ns = _namespace(workspace)
    _, stderr = _run(
        ns,
        f"import shutil; shutil.copyfile({str(workspace / 'inside.txt')!r}, {str(leaked)!r})",
    )
    assert _REFUSED in stderr
    assert not leaked.exists()


def test_shutil_copy_from_outside_the_workspace_is_refused(sandbox) -> None:
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(
        ns, f"import shutil; shutil.copy({str(outside / 'secret.txt')!r}, {str(workspace)!r})"
    )
    assert _REFUSED in stderr
    assert not (workspace / "secret.txt").exists()


def test_shutil_copyfile_inside_the_workspace_still_works(sandbox) -> None:
    """La metà che deve continuare a funzionare: confinare non è vietare."""
    workspace, _ = sandbox
    dst = workspace / "copy.txt"
    ns = _namespace(workspace)
    _, stderr = _run(
        ns, f"import shutil; shutil.copyfile({str(workspace / 'inside.txt')!r}, {str(dst)!r})"
    )
    assert stderr == ""
    assert dst.read_text() == "inside"


def test_the_real_builtins_open_is_confined_during_a_guarded_exec(sandbox) -> None:
    """Il cuore dell'evasione 1, senza intermediari.

    ``builtins.open`` risolto a runtime da codice che NON vive nel namespace
    guardato (qui una funzione registrata, in produzione qualunque modulo della
    stdlib) deve passare dal confine come tutto il resto.
    """
    workspace, outside = sandbox
    ns = _namespace(workspace)
    ns.register_function("peek", lambda p: builtins.open(p).read())

    _, stderr, _ = ns.call_function("peek", [str(outside / "secret.txt")])
    assert _REFUSED in stderr

    _, stderr, result = ns.call_function("peek", [str(workspace / "inside.txt")])
    assert stderr == ""
    assert result == "inside"


def test_a_workspace_module_cannot_open_outside_the_workspace(sandbox) -> None:
    """Il caso che B6 rende ordinario: una skill che possiede un suo ``.py``.

    Un modulo importato da disco riceve i ``__builtins__`` REALI, non quelli del
    namespace guardato: prima di questo fix il suo ``open`` era l'``open`` nudo.
    """
    workspace, outside = sandbox
    (workspace / "b4b_reader.py").write_text("def read(p):\n    return open(p).read()\n")
    ns = _namespace(workspace)
    _, stderr = _run(
        ns,
        f"import b4b_reader; print(b4b_reader.read({str(outside / 'secret.txt')!r}))",
        str(workspace),
    )
    assert _REFUSED in stderr


def test_relative_open_from_a_stdlib_path_follows_the_working_dir(sandbox) -> None:
    """Il patch globale deve usare la stessa base di risoluzione di B5."""
    workspace, _ = sandbox
    skill = workspace / "skills" / "foo"
    skill.mkdir(parents=True)
    (skill / "data.txt").write_text("from-skill")
    ns = _namespace(workspace)
    _, stderr = _run(
        ns, "import shutil; shutil.copyfile('data.txt', 'copied.txt')", str(skill)
    )
    assert stderr == ""
    assert (skill / "copied.txt").read_text() == "from-skill"
    assert not (workspace / "copied.txt").exists()


# ---------------------------------------------------------------------------
# Evasione 3 — `io.FileIO`, trovata verificando la 1
# ---------------------------------------------------------------------------
#
# `io` è nell'allowlist di produzione e `FileIO` è una CLASSE che apre per
# percorso: né `_patch_io_open` né il patch di `builtins.open` la toccavano, e
# il C che la costruisce non consulta nessuno dei due. Era quindi una terza via
# diretta oltre lo stesso confine, in lettura e in scrittura.


def test_io_fileio_cannot_read_outside_the_workspace(sandbox) -> None:
    workspace, outside = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns, f"import io; print(io.FileIO({str(outside / 'secret.txt')!r}).read())"
    )
    assert _REFUSED in stderr
    assert "secret" not in stdout


def test_io_fileio_cannot_write_outside_the_workspace(sandbox) -> None:
    workspace, outside = sandbox
    victim = outside / "fileio.txt"
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import io; io.FileIO({str(victim)!r}, 'w').write(b'X')")
    assert _REFUSED in stderr
    assert not victim.exists()


def test_io_fileio_inside_the_workspace_still_works(sandbox) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns,
        f"import io\n"
        f"io.FileIO({str(workspace / 'raw.txt')!r}, 'w').write(b'raw')\n"
        f"print(io.FileIO({str(workspace / 'raw.txt')!r}).read())\n",
    )
    assert stderr == ""
    assert "raw" in stdout
    assert (workspace / "raw.txt").read_bytes() == b"raw"


def test_io_fileio_wrapped_in_a_buffered_reader_is_refused(sandbox) -> None:
    """Avvolgerla non la sblocca: il confine sta sull'apertura."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns,
        "import io; print(io.BufferedReader(io.FileIO("
        f"{str(outside / 'secret.txt')!r})).read())",
    )
    assert _REFUSED in stderr
    assert "secret" not in stdout


def test_io_fileio_still_produces_real_file_objects(sandbox) -> None:
    """La sostituzione è una SOTTOCLASSE: le istanze restano dei veri ``FileIO``."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns,
        f"import io\nf = io.FileIO({str(workspace / 'inside.txt')!r})\n"
        "print(isinstance(f, io.RawIOBase), f.readable(), f.read())\n",
    )
    assert stderr == ""
    assert stdout.split()[:2] == ["True", "True"]


def test_io_fileio_is_untouched_for_host_code(sandbox) -> None:
    workspace, outside = sandbox
    import io as host_io

    ns = _namespace(workspace)
    _, stderr = _run(ns, "1 + 1")
    assert stderr == ""
    assert host_io.FileIO(outside / "secret.txt").read() == b"secret"
    assert not hasattr(
        getattr(host_io.FileIO, "_jenny_real_fileio"), "_jenny_real_fileio"
    )


def test_io_open_code_stays_out_of_reach(sandbox) -> None:
    """``io.open_code`` apre per percorso: ora è confinata come ``io.open``.

    La motivazione che stava qui ("il guard degli import la ferma prima,
    ``_io`` non è nell'allowlist") era vera solo sulla 3.14 della macchina di
    sviluppo, dove ``io`` importa ``_io`` pigramente. Sulla 3.11 del device
    ``io.open_code`` è legata direttamente da ``_io`` al momento dell'import di
    ``io``: nessun import a runtime, nessun rifiuto, e il file si apriva. Il
    test passava per il motivo sbagliato sull'unica versione che non conta.
    Vedi ``test_python_exec_boundary_completeness.py`` per il resto.
    """
    workspace, outside = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns, f"import io; print(io.open_code({str(outside / 'secret.txt')!r}).read())"
    )
    assert "secret" not in stdout
    assert stderr != ""


# ---------------------------------------------------------------------------
# Ridondanti contro B4 — tenuti come rete di regressione, non come nuova copertura
# ---------------------------------------------------------------------------


def test_shutil_copytree_from_outside_is_refused(sandbox) -> None:
    """GIÀ COPERTO da ``os.scandir`` di B4: ``copytree`` enumera prima di aprire,
    quindi si fermava al confine anche senza il patch di ``builtins.open``."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(
        ns, f"import shutil; shutil.copytree({str(outside)!r}, {str(workspace / 'tree')!r})"
    )
    assert _REFUSED in stderr
    assert not (workspace / "tree").exists()


def test_pathlib_read_text_outside_is_refused(sandbox) -> None:
    """GIÀ COPERTO da ``_patch_io_open``: tutta la I/O di ``pathlib`` passa da
    ``io.open``. Resta qui perché è una delle tre vie che il piano elencava."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(
        ns, f"import pathlib; print(pathlib.Path({str(outside / 'secret.txt')!r}).read_text())"
    )
    assert _REFUSED in stderr


def test_glob_outside_the_workspace_leaks_nothing(sandbox) -> None:
    """GIÀ COPERTO da ``os.scandir`` di B4, ma con una sfumatura da mettere agli
    atti: ``glob`` cattura l'``OSError`` del confine e restituisce una lista
    VUOTA invece di propagarlo. Nessun dato esce — che è ciò che conta — ma il
    rifiuto è silenzioso, esattamente come per ``os.fwalk``.
    """
    workspace, outside = sandbox
    ns = _namespace(workspace)
    stdout, _ = _run(ns, f"import glob; print(glob.glob({str(outside / '*')!r}))")
    assert stdout.strip() == "[]"
    stdout, _ = _run(ns, f"import glob; print(glob.glob({str(outside / 'secret.txt')!r}))")
    assert stdout.strip() == "[]"


# ---------------------------------------------------------------------------
# Evasione 2 — la blocklist di processi non deve dipendere da `import os`
# ---------------------------------------------------------------------------

_INDIRECT_ESCAPES = {
    "shutil.os.system": "import shutil; shutil.os.system('true')",
    "shutil.os.popen": "import shutil; shutil.os.popen('echo hi').read()",
    "glob.os.system": "import glob; glob.os.system('true')",
    "pathlib.os.execv": "import pathlib; pathlib.os.execv('/bin/true', ['true'])",
    "shutil.os.fork": "import shutil; shutil.os.fork()",
    "shutil.os.setuid": "import shutil; shutil.os.setuid(0)",
}


@pytest.mark.parametrize("case", sorted(_INDIRECT_ESCAPES))
def test_process_functions_are_blocked_without_importing_os(sandbox, case) -> None:
    """L'evasione 2: ogni modulo consentito tiene un riferimento interno a ``os``."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, _INDIRECT_ESCAPES[case])
    assert _BLOCKED in stderr


def test_process_functions_stay_blocked_after_an_explicit_import_os(sandbox) -> None:
    """Il percorso storico non deve regredire."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; os.system('true')")
    assert _BLOCKED in stderr


def test_process_functions_are_blocked_without_workspace_restriction(sandbox) -> None:
    """La blocklist riguarda i processi, non i path: vale anche senza confine."""
    workspace, _ = sandbox
    ns = _namespace(workspace, restrict=False)
    _, stderr = _run(ns, "import shutil; shutil.os.system('true')")
    assert _BLOCKED in stderr


def test_environment_probes_are_not_installed_at_guard_entry() -> None:
    """Perché ``register_at_fork`` & co. restano legate all'``import os``.

    Misurato su CPython 3.11 (la versione Chaquopy del device) instrumentando le
    tre funzioni: ``os.register_at_fork`` viene chiamata a TEMPO DI IMPORT da
    ``random``, ``threading`` e ``logging``. Stubbarla all'ingresso del guard
    farebbe fallire con un OSError incomprensibile il primo import che tira
    dentro uno di quei moduli dentro un exec guardato — e ``tempfile`` importa
    ``random``, quindi il raggio è largo. Nessuna delle tre concede una
    capability, quindi il costo di lasciarle sull'``import os`` è nullo.

    ``device_encoding`` è nello stesso insieme ma per compagnia, non per
    necessità: sulla 3.11 (e sulla 3.14 della macchina di sviluppo) NON è più
    sul percorso di costruzione di ``TextIOWrapper`` — il C di ``_io`` usa
    ``_Py_GetLocaleEncodingObject`` — quindi spostarla sarebbe stato innocuo.
    """
    escape = PythonNamespace._OS_BLOCKED_ESCAPE_FUNCTIONS
    probes = PythonNamespace._OS_BLOCKED_PROBE_FUNCTIONS
    host_state = PythonNamespace._OS_BLOCKED_HOST_STATE_FUNCTIONS
    assert probes == {"device_encoding", "get_terminal_size", "register_at_fork"}
    assert not (escape & probes)
    assert not (host_state & probes)
    # `chdir`/`fchdir` stanno con le evasioni, non con le sonde: mutano la cwd
    # del PROCESSO, che è condivisa col gateway, quindi vanno montate presto.
    assert PythonNamespace._OS_BLOCKED_FUNCTIONS == escape | host_state | probes
    # La superficie che DEVE essere montata presto.
    assert {"system", "popen", "execv", "fork", "setuid", "kill"} <= escape


def test_guard_entry_stubs_the_escape_surface_but_not_the_probes(sandbox) -> None:
    """Controparte comportamentale, su un modulo finto per non dipendere
    dall'ordine dei test.

    Sul modulo ``os`` VERO non si può asserire: il primo ``import os`` esplicito
    di un qualunque exec guardato monta anche le sonde, globalmente e per
    sempre, quindi qualunque test che gira dopo di quello le vedrebbe stubbate.
    È il comportamento pre-esistente, non una regressione — vedi il commento su
    ``_OS_BLOCKED_PROBE_FUNCTIONS`` — ma rende il modulo globale inutilizzabile
    come oggetto di prova. Qui si esercitano la vera ``_install_os_blocked`` e
    il vero gate su un modulo usa-e-getta.
    """
    import types

    workspace, _ = sandbox
    ns = _namespace(workspace)
    fake = types.ModuleType("b4b_fake_os")
    fake.system = lambda _cmd: "spawned"  # type: ignore[attr-defined]
    fake.register_at_fork = lambda **_kw: "registered"  # type: ignore[attr-defined]
    fake.get_terminal_size = lambda *_a: "80x24"  # type: ignore[attr-defined]
    ns._install_os_blocked(fake, ns._OS_BLOCKED_ESCAPE_FUNCTIONS)

    # Gate chiuso (nessun exec guardato su questo thread): tutto passa.
    assert fake.system("true") == "spawned"

    ns._enter_guard()
    try:
        with pytest.raises(OSError, match=_BLOCKED):
            fake.system("true")
        # Le sonde NON sono state montate: la stdlib le chiama a tempo di import.
        assert fake.register_at_fork(after_in_child=None) == "registered"
        assert fake.get_terminal_size() == "80x24"
    finally:
        ns._exit_guard()

    assert fake.system("true") == "spawned"


# ---------------------------------------------------------------------------
# Il gate — il gruppo che conta di più
# ---------------------------------------------------------------------------


def _host_open_surface(outside) -> None:
    """Tutta la superficie di ``open`` che il codice host deve conservare."""
    import io

    target = outside / "host_open.txt"
    with open(target, "w") as handle:
        handle.write("host")
    assert open(target).read() == "host"
    assert io.open(target).read() == "host"
    assert io.FileIO(target).read() == b"host"
    with open(target, "rb") as handle:
        assert handle.read() == b"host"
    shutil.copyfile(target, outside / "host_copy.txt")
    assert (outside / "host_copy.txt").read_text() == "host"
    # Un percorso di sistema, cioè il caso Chaquopy: la ``tempfile`` che estrae
    # le ``.so`` native lavora fuori dal workspace.
    with open("/etc/hosts") as handle:
        handle.readline()


def test_host_code_on_another_thread_can_still_open_files_outside(sandbox) -> None:
    """La regressione per cui il gate esiste, estesa a ``builtins.open``.

    Patchare il ``builtins.open`` globale è la cosa più invasiva che si possa
    fare a questo file: ci passa l'intero interprete. Se il gate non tenesse, il
    gateway morirebbe alla prima estrazione di una ``.so`` da parte di Chaquopy.
    """
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, "1 + 1")
    assert stderr == ""

    failures: list[BaseException] = []

    def _host_work() -> None:
        try:
            _host_open_surface(outside)
        except BaseException as exc:  # noqa: BLE001 - riportata al thread di test
            failures.append(exc)

    thread = threading.Thread(target=_host_work)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert not failures, f"host code was affected by the open patch: {failures[0]!r}"


def test_guarded_thread_does_not_leak_builtins_open_to_a_concurrent_host_thread(
    sandbox,
) -> None:
    """Il confine vale per thread anche MENTRE un exec guardato è in corso."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    result: list[object] = []

    def _host_probe() -> None:
        try:
            _host_open_surface(outside)
            result.append("OK")
        except BaseException as exc:  # noqa: BLE001
            result.append(exc)

    def _spawn() -> None:
        thread = threading.Thread(target=_host_probe)
        thread.start()
        thread.join(timeout=30)
        assert not thread.is_alive()

    ns.register_function("host_probe", _spawn)
    _, stderr, _ = ns.call_function("host_probe")
    assert stderr == ""
    assert result == ["OK"]


def test_host_code_on_the_main_thread_can_open_outside_after_a_guarded_exec(
    sandbox,
) -> None:
    """Il guard è thread-local ma viene spento nel ``finally``."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"print(open({str(workspace / 'inside.txt')!r}).read())")
    assert stderr == ""
    _host_open_surface(outside)


def test_an_unrestricted_exec_is_not_confined_by_a_previous_restricted_one(
    sandbox,
) -> None:
    """Il wrapper di ``builtins.open`` è unico e globale: il confine deve
    arrivargli dal thread-local, non dalla closure del namespace che lo ha
    montato (è il buco cross-namespace che B4 ha chiuso per ``os``)."""
    workspace, outside = sandbox
    _, stderr = _run(_namespace(workspace), "1 + 1")
    assert stderr == ""

    stdout, stderr = _run(
        _namespace(workspace, restrict=False),
        f"import shutil; shutil.copyfile({str(outside / 'secret.txt')!r}, "
        f"{str(outside / 'free.txt')!r}); print(open({str(outside / 'free.txt')!r}).read())",
    )
    assert stderr == ""
    assert stdout.strip() == "secret"


def test_the_builtins_open_patch_is_idempotent(sandbox) -> None:
    """Un ingresso nel guard per chiamata: i wrapper non devono impilarsi."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    for _ in range(4):
        _, stderr = _run(ns, "1 + 1")
        assert stderr == ""
    real = _real_builtins_open()
    assert not hasattr(real, "_jenny_real_open")
    assert builtins.open._jenny_real_open is real  # type: ignore[attr-defined]
    # E il vero ``open`` è ancora quello: il codice host lo usa senza saperlo.
    assert real(workspace / "inside.txt").read() == "inside"


# ---------------------------------------------------------------------------
# Nessuna ricorsione, nessuna raffica di rifiuti
# ---------------------------------------------------------------------------


def test_a_user_exception_renders_without_refusal_spam(sandbox, caplog) -> None:
    """``traceback`` e ``linecache`` aprono i sorgenti dei frame — stdlib e
    jenny, tutti fuori dal workspace. Con ``builtins.open`` patchato, un fix
    sbagliato qui trasforma ogni eccezione in una raffica di rifiuti."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    with caplog.at_level(logging.WARNING, logger=_GUARD_LOGGER):
        _, stderr = _run(ns, "def f():\n    raise ValueError('boom')\nf()")
    assert "ValueError: boom" in stderr
    assert _refusals(caplog) == []


def test_an_exception_in_a_workspace_module_still_shows_its_source_line(
    sandbox, caplog
) -> None:
    """La riga di sorgente arriva da ``linecache``: deve continuare ad arrivare."""
    workspace, _ = sandbox
    (workspace / "b4b_boomer.py").write_text(
        "def go():\n    raise ValueError('from a real file')\n"
    )
    ns = _namespace(workspace)
    with caplog.at_level(logging.WARNING, logger=_GUARD_LOGGER):
        _, stderr = _run(ns, "import b4b_boomer\nb4b_boomer.go()", str(workspace))
    assert "raise ValueError('from a real file')" in stderr
    assert _refusals(caplog) == []


def test_an_import_outside_the_allowlist_does_not_spam_refusals(sandbox, caplog) -> None:
    """``_resolves_within_workspace`` risolve il percorso del modulo, e
    ``Path.resolve()`` fa ``os.lstat`` su OGNI prefisso: ``/``, ``/var``, … sono
    tutti fuori dal workspace. Senza bypass ogni import fuori allowlist sputava
    una decina di rifiuti su percorsi che non c'entravano nulla."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    with caplog.at_level(logging.WARNING, logger=_GUARD_LOGGER):
        _, stderr = _run(ns, "import sqlite3")
    assert "sqlite3" in stderr
    assert _refusals(caplog) == []


def test_a_stdlib_exception_inside_guarded_code_renders_its_frames(sandbox, caplog) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    with caplog.at_level(logging.WARNING, logger=_GUARD_LOGGER):
        _, stderr = _run(ns, "import json\njson.loads('{bad}')")
    assert "JSONDecodeError" in stderr
    assert "json" in stderr
    assert _refusals(caplog) == []


def test_a_log_handler_writing_outside_the_workspace_still_works(sandbox, tmp_path) -> None:
    """Il caso più cattivo del logging: un handler su file FUORI dal workspace,
    aperto pigramente proprio mentre il guard riporta un rifiuto. Senza il
    bypass sulla risoluzione, il wrapper rientrerebbe in sé stesso."""
    workspace, outside = sandbox
    logfile = outside / "guard.log"
    handler = logging.FileHandler(logfile, delay=True)
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        ns = _namespace(workspace)
        _, stderr = _run(ns, f"import os; os.remove({str(outside / 'secret.txt')!r})")
    finally:
        root.setLevel(previous)
        root.removeHandler(handler)
        handler.close()
    assert _REFUSED in stderr
    assert logfile.exists()
    assert "refused" in logfile.read_text()
    assert (outside / "secret.txt").exists()


def test_many_guarded_opens_do_not_recurse(sandbox) -> None:
    """Prova grezza di non-ricorsione: se il wrapper rientrasse in sé stesso, un
    ciclo di aperture esaurirebbe lo stack invece di finire."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns,
        "total = 0\n"
        "for i in range(200):\n"
        "    open('loop.txt', 'w').write(str(i))\n"
        "    total += len(open('loop.txt').read())\n"
        "print(total)\n",
    )
    assert stderr == ""
    # 10 numeri a una cifra + 90 a due + 100 a tre.
    assert stdout.strip() == "490"


# ---------------------------------------------------------------------------
# Allineamento dei builtin registrati sulla base di B5
# ---------------------------------------------------------------------------


@pytest.fixture
def registered(tmp_path):
    """``(ns, workspace, skill)`` con ``data.txt`` in entrambe le directory."""
    workspace = tmp_path / "ws"
    skill = workspace / "skills" / "foo"
    skill.mkdir(parents=True)
    (workspace / "data.txt").write_text("from-root")
    (skill / "data.txt").write_text("from-skill")
    return _namespace(workspace, builtins_too=True), workspace, skill


class TestRegisteredBuiltinsFollowTheSameBase:
    def test_read_file_agrees_with_open_under_a_working_dir(self, registered) -> None:
        ns, _, skill = registered
        stdout, stderr = _run(ns, "print(open('data.txt').read(), read_file('data.txt'))", str(skill))
        assert stderr == ""
        assert stdout.split() == ["from-skill", "from-skill"]

    def test_read_file_agrees_with_open_without_a_working_dir(self, registered) -> None:
        """L'invariante che rende il fix innocuo: senza ``working_dir`` nulla si muove."""
        ns, _, _ = registered
        stdout, stderr = _run(ns, "print(open('data.txt').read(), read_file('data.txt'))")
        assert stderr == ""
        assert stdout.split() == ["from-root", "from-root"]

    def test_write_file_lands_where_open_would_write(self, registered) -> None:
        ns, workspace, skill = registered
        _, stderr = _run(ns, "write_file('out.txt', 'ok')", str(skill))
        assert stderr == ""
        assert (skill / "out.txt").read_text() == "ok"
        assert not (workspace / "out.txt").exists()

    def test_write_file_without_a_working_dir_lands_in_the_root(self, registered) -> None:
        ns, workspace, skill = registered
        _, stderr = _run(ns, "write_file('root_out.txt', 'ok')")
        assert stderr == ""
        assert (workspace / "root_out.txt").read_text() == "ok"
        assert not (skill / "root_out.txt").exists()

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("print(file_exists('data.txt'))", "True"),
            ("print(sorted(p.rsplit('/', 1)[-1] for p in list_dir('.')))", "['data.txt']"),
            ("print(read_json('j.json')['k'])", "v"),
        ],
    )
    def test_the_other_path_builtins_follow_the_working_dir(
        self, registered, code, expected
    ) -> None:
        ns, _, skill = registered
        (skill / "j.json").write_text('{"k": "v"}')
        if "list_dir" in code:
            (skill / "j.json").unlink()
        stdout, stderr = _run(ns, code, str(skill))
        assert stderr == ""
        assert stdout.strip() == expected

    def test_the_boundary_still_holds_for_the_registered_builtins(
        self, registered, tmp_path
    ) -> None:
        """La base si sposta, il confine no."""
        ns, _, skill = registered
        victim = tmp_path / "outside_builtin.txt"
        victim.write_text("keep")
        _, stderr = _run(ns, f"print(read_file({str(victim)!r}))", str(skill))
        assert _REFUSED in stderr
        _, stderr = _run(ns, "print(read_file('../../../../outside_builtin.txt'))", str(skill))
        assert _REFUSED in stderr
        assert victim.read_text() == "keep"

    def test_the_registered_builtins_do_not_spam_refusals(self, registered, caplog) -> None:
        ns, _, skill = registered
        with caplog.at_level(logging.WARNING, logger=_GUARD_LOGGER):
            _, stderr = _run(ns, "read_file('data.txt')", str(skill))
        assert stderr == ""
        assert _refusals(caplog) == []
