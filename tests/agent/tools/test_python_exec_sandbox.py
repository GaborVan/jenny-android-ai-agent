"""B4 — il confine di workspace vale anche per le operazioni di namespace di ``os``.

Prima di questa suite, con ``restrict_to_workspace=True`` il codice guardato non
poteva LEGGERE un file fuori dal workspace (``open``/``io.open``/``os.open``
passavano da ``resolve_allowed_path``) ma poteva cancellare, rinominare,
troncare ed ENUMERARE qualunque cosa raggiungibile dall'uid dell'app —
``workspace/config.json``, ``sessions/``, ``jenny_src/``, lo snapshot store.
Una sola chiamata, due confini diversi.

Ogni test qui sotto verifica una delle due metà del contratto:

* dentro il workspace la funzione continua a funzionare;
* fuori viene rifiutata con ``WorkspaceBoundaryError``;

più i casi che rendono il confine aggirabile se non coperti: ``dir_fd``,
descrittori interi, il secondo capo di un ``rename``, e — il più importante —
il fatto che il codice host su un altro thread non deve subire nulla (è la
regressione Chaquopy per cui i patch sono guard-gated).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading

import pytest

from jenny.agent.tools.python_exec import PythonNamespace
from jenny.config.tool_schemas import PythonExecConfig

_REFUSED = "outside allowed directory"


def _namespace(workspace, *, restrict: bool = True) -> PythonNamespace:
    """Namespace configurato come in produzione (allow/block list reali)."""
    cfg = PythonExecConfig()
    return PythonNamespace(
        working_dir=str(workspace),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict,
        workspace=str(workspace),
    )


@pytest.fixture
def sandbox(tmp_path):
    """(workspace, outside) con un file già presente in ciascuno."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "inside.txt").write_text("inside")
    (outside / "secret.txt").write_text("secret")
    return workspace, outside


def _run(ns: PythonNamespace, code: str) -> tuple[str, str]:
    stdout, stderr, _ = ns.execute(code)
    return stdout, stderr


# ---------------------------------------------------------------------------
# Gruppo 1 — mutatori a un percorso
# ---------------------------------------------------------------------------

_SINGLE_PATH_CASES = {
    "remove": "os.remove({p!r})",
    "unlink": "os.unlink({p!r})",
    "truncate": "os.truncate({p!r}, 0)",
    "chmod": "os.chmod({p!r}, 0o600)",
}

_SINGLE_DIR_CASES = {
    "rmdir": "os.rmdir({p!r})",
    "mkdir": "os.mkdir({p!r})",
    "makedirs": "os.makedirs({p!r})",
}


@pytest.mark.parametrize("fn", sorted(_SINGLE_PATH_CASES))
def test_single_path_mutators_inside_workspace_succeed(sandbox, fn) -> None:
    workspace, _ = sandbox
    target = workspace / f"{fn}.txt"
    target.write_text("data")
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _SINGLE_PATH_CASES[fn].format(p=str(target)))
    assert stderr == ""
    if fn in ("remove", "unlink"):
        assert not target.exists()
    elif fn == "truncate":
        assert target.read_text() == ""


@pytest.mark.parametrize("fn", sorted(_SINGLE_PATH_CASES))
def test_single_path_mutators_outside_workspace_refused(sandbox, fn) -> None:
    workspace, outside = sandbox
    target = outside / "secret.txt"
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _SINGLE_PATH_CASES[fn].format(p=str(target)))
    assert _REFUSED in stderr
    assert target.read_text() == "secret"


@pytest.mark.parametrize("fn", sorted(_SINGLE_DIR_CASES))
def test_single_path_dir_mutators_inside_workspace_succeed(sandbox, fn) -> None:
    workspace, _ = sandbox
    target = workspace / f"dir_{fn}"
    if fn == "rmdir":
        target.mkdir()
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _SINGLE_DIR_CASES[fn].format(p=str(target)))
    assert stderr == ""
    assert target.exists() is (fn != "rmdir")


@pytest.mark.parametrize("fn", sorted(_SINGLE_DIR_CASES))
def test_single_path_dir_mutators_outside_workspace_refused(sandbox, fn) -> None:
    workspace, outside = sandbox
    target = outside / "victim"
    if fn == "rmdir":
        target.mkdir()
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _SINGLE_DIR_CASES[fn].format(p=str(target)))
    assert _REFUSED in stderr
    if fn == "rmdir":
        assert target.is_dir()
    else:
        assert not target.exists()


# ---------------------------------------------------------------------------
# Gruppo 2 — mutatori a due percorsi (vanno risolti ENTRAMBI i capi)
# ---------------------------------------------------------------------------

_TWO_PATH_CASES = {
    "rename": "os.rename({src!r}, {dst!r})",
    "replace": "os.replace({src!r}, {dst!r})",
    "link": "os.link({src!r}, {dst!r})",
    "symlink": "os.symlink({src!r}, {dst!r})",
}


@pytest.mark.parametrize("fn", sorted(_TWO_PATH_CASES))
def test_two_path_mutators_inside_workspace_succeed(sandbox, fn) -> None:
    workspace, _ = sandbox
    src = workspace / f"{fn}_src.txt"
    src.write_text("payload")
    dst = workspace / f"{fn}_dst.txt"
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _TWO_PATH_CASES[fn].format(src=str(src), dst=str(dst)))
    assert stderr == ""
    assert dst.exists()


@pytest.mark.parametrize("fn", sorted(_TWO_PATH_CASES))
def test_two_path_mutators_with_destination_outside_refused(sandbox, fn) -> None:
    """Il caso del piano: sorgente dentro, destinazione fuori → rifiutato."""
    workspace, outside = sandbox
    src = workspace / f"{fn}_src.txt"
    src.write_text("payload")
    dst = outside / f"{fn}_escaped.txt"
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _TWO_PATH_CASES[fn].format(src=str(src), dst=str(dst)))
    assert _REFUSED in stderr
    assert not dst.exists()
    assert src.exists()


@pytest.mark.parametrize("fn", sorted(_TWO_PATH_CASES))
def test_two_path_mutators_with_source_outside_refused(sandbox, fn) -> None:
    workspace, outside = sandbox
    src = outside / "secret.txt"
    dst = workspace / f"{fn}_stolen.txt"
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _TWO_PATH_CASES[fn].format(src=str(src), dst=str(dst)))
    assert _REFUSED in stderr
    assert not dst.exists()
    assert src.read_text() == "secret"


def test_symlink_keeps_a_relative_target_relative(sandbox) -> None:
    """Il target relativo si legge rispetto alla directory del link.

    Validarlo contro il workspace root e riscriverlo in assoluto cambierebbe
    il file puntato: qui si verifica che resti quello previsto.
    """
    workspace, _ = sandbox
    (workspace / "sub").mkdir()
    (workspace / "sub" / "target.txt").write_text("relative-target")
    link = workspace / "sub" / "link.txt"
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import os; os.symlink('target.txt', {str(link)!r})")
    assert stderr == ""
    assert os.readlink(link) == "target.txt"
    assert link.read_text() == "relative-target"


def test_remove_deletes_the_symlink_not_its_target(sandbox) -> None:
    """La validazione dereferenzia i symlink, la CHIAMATA no.

    Se il wrapper passasse alla ``os.remove`` reale il percorso risolto invece
    di quello logico, cancellerebbe il bersaglio del link al posto del link.
    """
    workspace, _ = sandbox
    target = workspace / "target.txt"
    target.write_text("keep me")
    link = workspace / "link.txt"
    link.symlink_to(target)
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import os; os.remove({str(link)!r})")
    assert stderr == ""
    # `lexists`, non `Path.exists(follow_symlinks=False)`: quel parametro
    # esiste dalla 3.12, e il device gira sulla 3.11.
    assert not os.path.lexists(link)
    assert target.read_text() == "keep me"


# ---------------------------------------------------------------------------
# Gruppo 3 — enumeratori (elencare non è innocuo)
# ---------------------------------------------------------------------------

_ENUMERATOR_CASES = {
    "listdir": "os.listdir({p!r})",
    "scandir": "list(os.scandir({p!r}))",
    "walk": "list(os.walk({p!r}))",
    "stat": "os.stat({p!r})",
    "lstat": "os.lstat({p!r})",
}


@pytest.mark.parametrize("fn", sorted(_ENUMERATOR_CASES))
def test_enumerators_inside_workspace_succeed(sandbox, fn) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns, "import os; print(bool(" + _ENUMERATOR_CASES[fn].format(p=str(workspace)) + "))"
    )
    assert stderr == ""
    assert stdout.strip() == "True"


@pytest.mark.parametrize("fn", sorted(_ENUMERATOR_CASES))
def test_enumerators_outside_workspace_refused(sandbox, fn) -> None:
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _ENUMERATOR_CASES[fn].format(p=str(outside)))
    assert _REFUSED in stderr


def test_walk_outside_workspace_is_refused_not_silently_empty(sandbox) -> None:
    """``os.walk`` va patchata direttamente, non basta ``os.scandir``.

    ``walk`` è Python puro costruito su ``scandir``, e cattura l'``OSError``
    dello ``scandir`` iniziale passandolo a ``onerror`` (di default: ignora).
    ``WorkspaceBoundaryError`` è un ``OSError``, quindi con la sola ``scandir``
    patchata un ``walk`` fuori dal workspace restituirebbe silenziosamente un
    generatore vuoto invece di segnalare il confine.
    """
    workspace, outside = sandbox
    (outside / "nested").mkdir()
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import os; list(os.walk({str(outside)!r}))")
    assert _REFUSED in stderr


def test_walk_inside_workspace_sees_the_whole_tree(sandbox) -> None:
    workspace, _ = sandbox
    (workspace / "a" / "b").mkdir(parents=True)
    (workspace / "a" / "b" / "leaf.txt").write_text("leaf")
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns,
        f"import os; print(sorted(f for _, _, fs in os.walk({str(workspace)!r}) for f in fs))",
    )
    assert stderr == ""
    assert "leaf.txt" in stdout
    assert "inside.txt" in stdout


# ---------------------------------------------------------------------------
# dir_fd e descrittori interi
# ---------------------------------------------------------------------------

_DIR_FD_CASES = {
    "unlink": "os.unlink('inside.txt', dir_fd=fd)",
    "mkdir": "os.mkdir('sub', dir_fd=fd)",
    "chmod": "os.chmod('inside.txt', 0o600, dir_fd=fd)",
    "stat": "os.stat('inside.txt', dir_fd=fd)",
    "rename": "os.rename('inside.txt', 'other.txt', src_dir_fd=fd)",
}


@pytest.mark.parametrize("fn", sorted(_DIR_FD_CASES))
def test_dir_fd_argument_is_refused(sandbox, fn) -> None:
    """Un ``dir_fd`` rende il percorso relativo al descrittore: risolverlo
    contro il workspace non significherebbe più nulla (``../../etc`` da un fd
    legittimo esce dal confine)."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(
        ns,
        f"import os\nfd = os.open({str(workspace)!r}, os.O_RDONLY)\n"
        + _DIR_FD_CASES[fn]
        + "\n",
    )
    assert "is not allowed under workspace restriction" in stderr
    assert (workspace / "inside.txt").exists()


@pytest.mark.parametrize("fn", ["listdir", "scandir", "stat", "truncate"])
def test_integer_file_descriptor_argument_is_refused(sandbox, fn) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    extra = ", 0" if fn == "truncate" else ""
    _, stderr = _run(
        ns,
        f"import os\nfd = os.open({str(workspace / 'inside.txt')!r}, os.O_RDWR)\n"
        f"os.{fn}(fd{extra})\n",
    )
    assert "file descriptor is not allowed" in stderr


# ---------------------------------------------------------------------------
# Il confine non deve dipendere da un `import os` del codice guardato
# ---------------------------------------------------------------------------


def test_boundary_applies_without_importing_os(sandbox) -> None:
    """``import shutil`` raggiunge ``os`` attraverso il riferimento interno.

    Prima di B4 i patch venivano installati solo al primo ``import os`` del
    codice guardato: bastava non importarlo per avere un ``os`` intatto (e con
    esso anche l'``os.open`` non confinato).
    """
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import shutil; shutil.rmtree({str(outside)!r})")
    assert _REFUSED in stderr
    assert (outside / "secret.txt").read_text() == "secret"


def test_pathlib_iterdir_outside_workspace_refused(sandbox) -> None:
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import pathlib; list(pathlib.Path({str(outside)!r}).iterdir())")
    assert _REFUSED in stderr


def test_shutil_rmtree_inside_workspace_still_works(sandbox) -> None:
    """L'implementazione POSIX di ``rmtree`` lavora con ``dir_fd`` e fd interi.

    Senza un trattamento esplicito i wrapper la romperebbero anche DENTRO il
    workspace, togliendo all'agente una capability legittima (è come cancella
    una Jenny App).
    """
    workspace, _ = sandbox
    tree = workspace / "tree" / "sub"
    tree.mkdir(parents=True)
    (tree / "leaf.txt").write_text("leaf")
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import shutil; shutil.rmtree({str(workspace / 'tree')!r})")
    assert stderr == ""
    assert not (workspace / "tree").exists()


# ---------------------------------------------------------------------------
# Nessuna restrizione → nessun patch
# ---------------------------------------------------------------------------


def test_unrestricted_namespace_is_not_confined_by_a_previous_restricted_one(
    sandbox,
) -> None:
    """I wrapper stanno sul modulo ``os`` globale e sono condivisi.

    Se il confine vivesse nella closure del namespace che ha installato il
    patch, il primo namespace ristretto passato di lì imporrebbe il proprio
    workspace a tutti quelli successivi — compresi quelli non ristretti.
    """
    workspace, outside = sandbox
    restricted = _namespace(workspace)
    _, stderr = _run(restricted, f"import os; os.listdir({str(workspace)!r})")
    assert stderr == ""

    unrestricted = _namespace(workspace, restrict=False)
    stdout, stderr = _run(
        unrestricted, f"import os; print(sorted(os.listdir({str(outside)!r})))"
    )
    assert stderr == ""
    assert stdout.strip() == "['secret.txt']"


def test_each_namespace_enforces_its_own_workspace(tmp_path) -> None:
    """Due workspace diversi nello stesso processo non devono mescolarsi."""
    first = tmp_path / "ws_a"
    first.mkdir()
    second = tmp_path / "ws_b"
    second.mkdir()
    (second / "b.txt").write_text("b")

    ns_a = _namespace(first)
    _, stderr = _run(ns_a, f"import os; os.listdir({str(first)!r})")
    assert stderr == ""

    ns_b = _namespace(second)
    stdout, stderr = _run(ns_b, f"import os; print(sorted(os.listdir({str(second)!r})))")
    assert stderr == ""
    assert stdout.strip() == "['b.txt']"

    # …e il confine di B resta comunque un confine.
    _, stderr = _run(ns_b, f"import os; os.listdir({str(first)!r})")
    assert _REFUSED in stderr


def test_unrestricted_namespace_passes_everything_through(sandbox) -> None:
    workspace, outside = sandbox
    ns = _namespace(workspace, restrict=False)
    victim = outside / "victim.txt"
    victim.write_text("bye")
    code = (
        "import os\n"
        f"print(len(os.listdir({str(outside)!r})) > 0)\n"
        f"print(len(list(os.walk({str(outside)!r}))) > 0)\n"
        f"os.stat({str(victim)!r})\n"
        f"os.mkdir({str(outside / 'made')!r})\n"
        f"os.remove({str(victim)!r})\n"
    )
    stdout, stderr = _run(ns, code)
    assert stderr == ""
    assert stdout.split() == ["True", "True"]
    assert not victim.exists()
    assert (outside / "made").is_dir()


# ---------------------------------------------------------------------------
# Il gate: il codice host su un altro thread non deve subire nulla
# ---------------------------------------------------------------------------


def test_host_code_on_another_thread_is_unaffected(sandbox) -> None:
    """La regressione per cui il gate esiste (vedi ``_patch_os_open``).

    Un patch non gated sul modulo ``os`` GLOBALE romperebbe il gateway: la
    ``tempfile`` della stdlib usata da Chaquopy per estrarre le ``.so`` native
    lavora su file fuori dal workspace. Qui il thread host esegue tutta la
    superficie patchata fuori dal workspace mentre un exec guardato è appena
    stato eseguito, e deve riuscire.
    """
    workspace, outside = sandbox
    ns = _namespace(workspace)

    # Installa i patch globali (qualunque exec guardato li monta).
    _, stderr = _run(ns, "1 + 1")
    assert stderr == ""

    failures: list[BaseException] = []

    def _host_work() -> None:
        try:
            host_dir = outside / "host"
            os.mkdir(host_dir)
            os.makedirs(host_dir / "deep" / "nest")
            (host_dir / "f.txt").write_text("host")
            assert os.listdir(host_dir)
            assert list(os.scandir(host_dir))
            assert list(os.walk(host_dir))
            assert os.stat(host_dir / "f.txt").st_size == 4
            assert os.lstat(host_dir / "f.txt").st_size == 4
            os.chmod(host_dir / "f.txt", 0o600)
            os.truncate(host_dir / "f.txt", 0)
            os.rename(host_dir / "f.txt", host_dir / "g.txt")
            os.replace(host_dir / "g.txt", host_dir / "h.txt")
            os.link(host_dir / "h.txt", host_dir / "i.txt")
            os.symlink(host_dir / "h.txt", host_dir / "j.txt")
            os.remove(host_dir / "i.txt")
            os.unlink(host_dir / "j.txt")
            os.rmdir(host_dir / "deep" / "nest")
            shutil.rmtree(host_dir)
            assert not host_dir.exists()
        except BaseException as exc:  # noqa: BLE001 - riportata al thread di test
            failures.append(exc)

    thread = threading.Thread(target=_host_work)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert not failures, f"host code was affected by the sandbox patches: {failures[0]!r}"


def test_host_code_on_the_main_thread_is_unaffected(sandbox) -> None:
    """Stesso contratto sul thread che ha appena eseguito il codice guardato:
    il guard è thread-local ma viene disattivato in ``finally``."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import os; os.listdir({str(workspace)!r})")
    assert stderr == ""

    assert os.listdir(outside) == ["secret.txt"]
    os.remove(outside / "secret.txt")
    assert not (outside / "secret.txt").exists()


def test_guarded_thread_does_not_leak_the_boundary_to_a_concurrent_host_thread(
    sandbox,
) -> None:
    """Il confine vale per thread: mentre un exec guardato è IN CORSO, un altro
    thread deve poter lavorare fuori dal workspace."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    result: list[object] = []

    def _host_probe() -> None:
        try:
            result.append(sorted(os.listdir(outside)))
        except BaseException as exc:  # noqa: BLE001
            result.append(exc)

    ns.register_function("host_probe", lambda: _wait_for_host_thread(_host_probe))
    _, stderr, _ = ns.call_function("host_probe")
    assert stderr == ""
    assert result == [["secret.txt"]]


def _wait_for_host_thread(target) -> None:
    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive()


# ---------------------------------------------------------------------------
# Il bypass di rientranza deve coprire OGNI punto di risoluzione
# ---------------------------------------------------------------------------


def test_os_open_inside_workspace_does_not_log_a_wall_of_refusals(sandbox, caplog) -> None:
    """Una ``os.open`` legittima non deve produrre rifiuti nei log.

    ``resolve_allowed_path`` fa ``Path.resolve()`` → ``realpath`` → ``os.lstat``
    su OGNI antenato del percorso. Senza ``_path_guard_bypass`` quegli antenati
    (``/``, ``/tmp``, …) stanno fuori dal workspace, il wrapper di ``lstat`` li
    rifiuta a WARNING e ``realpath`` ingoia l'``OSError`` e tira dritto: la
    chiamata riesce, ma il modello legge decine di righe di rifiuto e conclude
    di aver fallito.
    """
    workspace, _ = sandbox
    ns = _namespace(workspace)
    with caplog.at_level(logging.WARNING, logger="jenny.agent.tools.python_exec"):
        stdout, stderr = _run(
            ns,
            "import os\n"
            "fd = os.open('inside.txt', os.O_RDONLY)\n"
            "os.close(fd)\n"
            "print('opened')\n",
        )
    assert stderr == ""
    assert "opened" in stdout
    refusals = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert not refusals, f"{len(refusals)} spurious refusals: {refusals[:3]}"


def test_os_open_still_refuses_a_path_outside_the_workspace(sandbox) -> None:
    """Il bypass non deve spegnere il confine: solo la RIENTRANZA."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(
        ns, f"import os; os.open({str(outside / 'secret.txt')!r}, os.O_RDONLY)"
    )
    assert _REFUSED in stderr


def test_os_open_relative_path_uses_the_exec_base(sandbox) -> None:
    """``_active_path_base()`` va letto FUORI dal bypass.

    Dentro il bypass ritorna ``None`` per costruzione, quindi wrappare la
    risoluzione senza spostare la lettura riporterebbe la base al confine e
    ``working_dir`` tornerebbe muto per ``os.open``.
    """
    workspace, _ = sandbox
    skill = workspace / "skills" / "foo"
    skill.mkdir(parents=True)
    (skill / "data.txt").write_text("from-skill")
    (workspace / "data.txt").write_text("from-root")
    ns = _namespace(workspace)
    stdout, stderr, _ = ns.execute(
        "import os\n"
        "fd = os.open('data.txt', os.O_RDONLY)\n"
        "print(os.read(fd, 100).decode())\n"
        "os.close(fd)\n",
        working_dir=str(skill),
    )
    assert stderr == ""
    assert "from-skill" in stdout


# ---------------------------------------------------------------------------
# `shutil.rmtree`: le callback girano DENTRO il bypass
# ---------------------------------------------------------------------------


def _rmtree_leak_code(outside, missing, call: str) -> str:
    """Snippet che prova a leggere fuori dal workspace da dentro una callback.

    Il canale è ``io.open`` e non l'``open`` del namespace: quest'ultimo è
    l'unico wrapper che NON è gated su ``_active_path_boundary()``
    (``_resolve_workspace_write`` ricade su ``self.workspace``), quindi regge
    anche dentro il bypass. ``io.open``, ``pathlib`` e tutta la superficie
    ``os`` no: lì il bypass spegne davvero il confine.
    """
    return (
        "import io, shutil\n"
        "leaked = []\n"
        "def cb(*a):\n"
        f"    leaked.append(io.open({str(outside / 'secret.txt')!r}).read())\n"
        f"{call.format(p=str(missing))}\n"
        "print('LEAKED:' + repr(leaked))\n"
    )


def test_shutil_rmtree_refuses_onerror_callback(sandbox) -> None:
    """``onerror=`` è invocata da ``rmtree`` DENTRO ``_path_guard_bypass``.

    Lì ``_active_path_boundary()`` è ``None``: ``builtins.open``, ``io.open``,
    ``io.FileIO`` e tutta la superficie ``os`` prendono il ramo di passthrough,
    e la callback legge ``config.json`` (chiavi API e bootstrap secret) senza
    confine. Basta un percorso inesistente per farla scattare.
    """
    workspace, outside = sandbox
    missing = workspace / "does-not-exist"
    stdout, stderr = _run(
        _namespace(workspace),
        _rmtree_leak_code(outside, missing, "shutil.rmtree({p!r}, onerror=cb)"),
    )
    assert "secret" not in stdout, "the callback read outside the workspace"
    assert "onerror" in stderr


def test_shutil_rmtree_refuses_positional_onerror_callback(sandbox) -> None:
    """Su 3.11 ``onerror`` è il TERZO parametro posizionale, non solo un kwarg."""
    workspace, outside = sandbox
    missing = workspace / "does-not-exist"
    stdout, stderr = _run(
        _namespace(workspace),
        _rmtree_leak_code(outside, missing, "shutil.rmtree({p!r}, False, cb)"),
    )
    assert "secret" not in stdout, "the callback read outside the workspace"
    assert "onerror" in stderr


@pytest.mark.skipif(sys.version_info < (3, 12), reason="onexc esiste da 3.12")
def test_shutil_rmtree_refuses_onexc_callback(sandbox) -> None:
    """``onexc=`` è il successore di ``onerror`` da 3.12: stesso buco."""
    workspace, outside = sandbox
    missing = workspace / "does-not-exist"
    stdout, stderr = _run(
        _namespace(workspace),
        _rmtree_leak_code(outside, missing, "shutil.rmtree({p!r}, onexc=cb)"),
    )
    assert "secret" not in stdout, "the callback read outside the workspace"
    assert "onexc" in stderr


def test_shutil_rmtree_still_accepts_ignore_errors(sandbox) -> None:
    """Il rifiuto riguarda le CALLBACK, non l'alternativa che suggeriamo."""
    workspace, _ = sandbox
    tree = workspace / "tree"
    tree.mkdir()
    (tree / "leaf.txt").write_text("leaf")
    _, stderr = _run(
        _namespace(workspace),
        f"import shutil; shutil.rmtree({str(tree)!r}, ignore_errors=True)",
    )
    assert stderr == ""
    assert not tree.exists()
