"""Completezza del confine di ``python_exec``: R11, R12, R13, ``open_code``, fd.

Quattro buchi di forma diversa, tutti trovati leggendo il file per intero dopo
che cinque agenti l'avevano riscritto in una notte:

**R11 — il confine rompeva l'interprete che lo esegue.** Chaquopy estrae i
payload nativi (``.so``) al primo import, e lo fa con ``os.path.exists`` →
``os.stat``, ``os.makedirs``, ``NamedTemporaryFile``, ``os.replace``,
``os.utime`` e un ``open(<.so>, 'rb')`` per leggere l'ELF. Da quando quelle
funzioni sono guardate, un ``import hashlib`` a freddo dentro un exec ristretto
fallisce. La sequenza è letta dal bytecode dell'importer nell'APK
(``assets/chaquopy/bootstrap.imy`` → ``java/android/importer.pyc``,
``AssetZipFile.extract_if_changed`` a riga 839), non dedotta.

**R12 — un rifiuto che diventa una risposta sbagliata.**
``WorkspaceBoundaryError`` è una ``PermissionError``, che è esattamente ciò che
``glob._iterdir`` (``except OSError``) e ``pathlib._Selector._select_from``
(``except PermissionError``) ingoiano: ``glob.glob('/sdcard/Download/*')``
restituisce ``[]``. Il modello riferisce "la cartella è vuota".

**R13 — funzioni ``os`` in nessuna delle due tabelle.** ``utime`` era la più
grave: ``Path.touch()`` la chiama per prima e ritorna se il file esiste.

**``io.open_code`` e i descrittori interi.** La prima apriva per percorso senza
passare da nessun controllo; il secondo era un rifiuto che sembrava un controllo
di sicurezza senza esserlo.

CI non è Chaquopy: R11 è coperto simulando la FORMA del problema — un modulo il
cui import deve fare ``os.stat`` e ``open`` su un percorso fuori dal workspace.
"""

from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager

import pytest

from jenny.agent.tools.python_exec import (
    PythonNamespace,
    _discover_runtime_path_prefixes,
    _effective_runtime_prefixes,
    _reset_runtime_path_prefixes,
)
from jenny.config.tool_schemas import PythonExecConfig

_REFUSED = "outside allowed directory"


def _namespace(workspace, *, restrict: bool = True, extra_modules: list[str] | None = None):
    cfg = PythonExecConfig()
    return PythonNamespace(
        working_dir=str(workspace),
        allowed_modules=cfg.allowed_modules + (extra_modules or []),
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict,
        workspace=str(workspace),
    )


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


def _run(ns: PythonNamespace, code: str) -> tuple[str, str]:
    stdout, stderr, _ = ns.execute(code)
    return stdout, stderr


@contextmanager
def _runtime_prefix(root):
    """Registra *root* come radice di estrazione di un finder tipo Chaquopy.

    Riproduce ciò che ``_discover_runtime_path_prefixes`` legge sul device:
    l'attributo ``extract_root`` di un ``AssetFinder`` in
    ``sys.path_importer_cache``. Lo snapshot è memoizzato, quindi va scartato
    prima e dopo.
    """

    class _FakeAssetFinder:
        extract_root = str(root)

    key = f"__jenny_test_asset_finder__{root}"
    sys.path_importer_cache[key] = _FakeAssetFinder()
    _reset_runtime_path_prefixes()
    try:
        yield
    finally:
        sys.path_importer_cache.pop(key, None)
        _reset_runtime_path_prefixes()


# ---------------------------------------------------------------------------
# R11 — i percorsi dell'interprete
# ---------------------------------------------------------------------------


def test_runtime_prefixes_are_read_from_the_interpreter(tmp_path) -> None:
    """Non si scrivono a mano: si leggono dai finder registrati."""
    root = tmp_path / "chaquopy" / "AssetFinder"
    root.mkdir(parents=True)
    _reset_runtime_path_prefixes()
    assert str(root) not in _discover_runtime_path_prefixes()
    with _runtime_prefix(root):
        assert str(root) in _discover_runtime_path_prefixes()


def test_a_prefix_containing_the_workspace_is_dropped(sandbox) -> None:
    """L'invariante che rende l'esenzione sicura.

    Un prefisso che contiene il confine non lo aggirerebbe: lo SPEGNEREBBE.
    """
    workspace, _ = sandbox
    with _runtime_prefix(workspace.parent):
        assert _effective_runtime_prefixes(str(workspace)) == ()


def test_a_prefix_inside_the_workspace_is_dropped(sandbox) -> None:
    """E uno interno è ridondante: il confine lo consente già."""
    workspace, _ = sandbox
    inner = workspace / "sub"
    inner.mkdir()
    with _runtime_prefix(inner):
        assert _effective_runtime_prefixes(str(workspace)) == ()


def test_a_disjoint_prefix_is_kept(sandbox) -> None:
    workspace, outside = sandbox
    with _runtime_prefix(outside):
        assert _effective_runtime_prefixes(str(workspace)) == (str(outside),)


def test_no_prefixes_without_a_boundary(sandbox) -> None:
    """Senza confine non c'è nulla da esentare (exec non ristretto)."""
    _, outside = sandbox
    with _runtime_prefix(outside):
        assert _effective_runtime_prefixes(None) == ()


def _write_native_shaped_module(directory, name: str) -> None:
    """Modulo che, per importarsi, deve fare ``os.stat`` e ``open`` su di sé.

    È la forma dell'``AssetZipFile.extract_if_changed`` di Chaquopy: guarda se
    il payload estratto esiste ed è aggiornato (``os.path.exists`` → ``os.stat``)
    e poi lo legge (``get_needed`` → ``open(path, 'rb')`` per l'ELF). Entrambi
    su un percorso FUORI dal workspace, che è la sostanza di R11.
    """
    payload = directory / f"{name}.payload"
    payload.write_bytes(b"\x7fELF-not-really")
    (directory / f"{name}.py").write_text(
        "import os\n"
        f"_payload = {str(payload)!r}\n"
        "SIZE = os.stat(_payload).st_size\n"
        "with open(_payload, 'rb') as fh:\n"
        "    HEAD = fh.read(4)\n"
    )


@contextmanager
def _importable(directory):
    sys.path.insert(0, str(directory))
    importlib.invalidate_caches()
    try:
        yield
    finally:
        sys.path.remove(str(directory))


def test_cold_import_of_a_runtime_module_is_refused_without_the_exemption(
    sandbox,
) -> None:
    """Il bug R11, nella sua forma riproducibile su CI."""
    workspace, outside = sandbox
    _write_native_shaped_module(outside, "jenny_fake_native_a")
    sys.modules.pop("jenny_fake_native_a", None)
    ns = _namespace(workspace, extra_modules=["jenny_fake_native_a"])
    with _importable(outside):
        _, stderr = _run(ns, "import jenny_fake_native_a")
    sys.modules.pop("jenny_fake_native_a", None)
    assert _REFUSED in stderr


def test_cold_import_of_a_runtime_module_works_with_the_exemption(sandbox) -> None:
    """Il criterio di accettazione: ``import <modulo con .so>`` a freddo funziona.

    Sul device il modulo è ``hashlib``/``uuid``/``csv``/``unicodedata``/
    ``xml.etree.ElementTree`` e la directory è la radice di estrazione di
    Chaquopy; qui sono un finto modulo e una finta radice, ma le chiamate che
    devono passare il confine sono le stesse.
    """
    workspace, outside = sandbox
    _write_native_shaped_module(outside, "jenny_fake_native_b")
    sys.modules.pop("jenny_fake_native_b", None)
    ns = _namespace(workspace, extra_modules=["jenny_fake_native_b"])
    # `_importable` per primo: `importlib.invalidate_caches()` ripulisce
    # `sys.path_importer_cache`, cioè proprio dove `_runtime_prefix` registra
    # il finder finto.
    with _importable(outside), _runtime_prefix(outside):
        stdout, stderr = _run(
            ns, "import jenny_fake_native_b as m; print(m.SIZE, m.HEAD)"
        )
    sys.modules.pop("jenny_fake_native_b", None)
    assert stderr == ""
    assert "b'\\x7fELF'" in stdout


def test_the_whole_extraction_sequence_passes(sandbox, tmp_path) -> None:
    """Le sette chiamate di ``AssetZipFile.extract``/``extract_if_changed``.

    Nell'ordine letto dal bytecode: ``os.path.exists`` (→ ``os.stat``),
    ``os.makedirs``, l'apertura del file temporaneo (``os.open`` via
    ``NamedTemporaryFile``), ``copyfileobj``, ``os.replace``, ``os.utime`` e
    infine ``open(<.so>, 'rb')`` dell'ELF. ``makedirs`` è la più insidiosa:
    sonda ``dirname(target)``, che sta un livello SOPRA la radice di estrazione,
    e se la sonda fallisce ricorre fino a ``/``.
    """
    workspace, _ = sandbox
    extract_root = tmp_path / "chaquopy" / "AssetFinder" / "stdlib"
    extract_root.mkdir(parents=True)
    ns = _namespace(workspace)
    with _runtime_prefix(extract_root):
        stdout, stderr = _run(
            ns,
            "import os, shutil\n"
            f"root = {str(extract_root)!r}\n"
            "target = os.path.join(root, '_fake.cpython-311.so')\n"
            "print(os.path.exists(target))\n"
            "os.makedirs(root, exist_ok=True)\n"
            "tmp = target + '.tmp'\n"
            "with open(tmp, 'wb') as dst:\n"
            "    dst.write(b'\\x7fELF')\n"
            "os.replace(tmp, target)\n"
            "os.utime(target, (1000, 1000))\n"
            "print(os.stat(target).st_size)\n"
            "with open(target, 'rb') as fh:\n"
            "    print(fh.read())\n",
        )
    assert stderr == ""
    assert stdout.splitlines() == ["False", "4", "b'\\x7fELF'"]


def test_ancestors_of_a_prefix_answer_stat_but_nothing_else(sandbox, tmp_path) -> None:
    """L'esenzione sugli antenati è la più stretta che serve a ``makedirs``."""
    workspace, _ = sandbox
    extract_root = tmp_path / "chaquopy" / "AssetFinder"
    extract_root.mkdir(parents=True)
    ancestor = extract_root.parent
    ns = _namespace(workspace)
    with _runtime_prefix(extract_root):
        stdout, stderr = _run(ns, f"import os; print(os.stat({str(ancestor)!r}).st_mode > 0)")
        assert stderr == ""
        assert stdout.strip() == "True"
        # Enumerare o mutare un antenato resta rifiutato.
        _, stderr = _run(ns, f"import os; os.listdir({str(ancestor)!r})")
        assert _REFUSED in stderr
        _, stderr = _run(ns, f"import os; os.rmdir({str(ancestor)!r})")
        assert _REFUSED in stderr
        assert ancestor.is_dir()


def test_the_exemption_does_not_leak_to_other_outside_paths(sandbox) -> None:
    """Esentare la radice del runtime non apre il resto del filesystem."""
    workspace, outside = sandbox
    runtime = outside / "runtime"
    runtime.mkdir()
    elsewhere = outside / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.txt").write_text("secret")
    ns = _namespace(workspace)
    with _runtime_prefix(runtime):
        stdout, stderr = _run(
            ns, f"print(open({str(elsewhere / 'secret.txt')!r}).read())"
        )
    assert "secret" not in stdout
    assert _REFUSED in stderr


def test_the_exemption_is_off_outside_a_guarded_exec(sandbox) -> None:
    """Fuori dal guard i thread-local sono vuoti: nessun prefisso attivo."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    with _runtime_prefix(outside):
        _run(ns, "1 + 1")
    from jenny.agent.tools.python_exec import _is_runtime_path

    assert not _is_runtime_path(str(outside / "secret.txt"))


# ---------------------------------------------------------------------------
# R12 — il rifiuto ingoiato deve comunque raggiungere il modello
# ---------------------------------------------------------------------------

_SWALLOWED = {
    "glob": "import glob; print(glob.glob({p!r} + '/*'))",
    "pathlib": "import pathlib; print(list(pathlib.Path({p!r}).glob('*')))",
}


@pytest.mark.parametrize("api", sorted(_SWALLOWED))
def test_a_refused_enumeration_never_looks_like_an_empty_folder(sandbox, api) -> None:
    """L'invariante è che il modello non possa concludere "la cartella è vuota".

    *Come* ci si arriva dipende dalla versione, e la differenza è reale sul
    device: ``glob.glob`` ingoia il rifiuto ovunque, ma ``Path.glob`` lo ingoia
    solo dalla 3.12 in poi. Sulla 3.11 — quella che Chaquopy impacchetta,
    ``android/app/build.gradle.kts`` — ``is_dir()`` lascia passare l'errore e
    il modello vede il traceback. Entrambe le forme vanno bene; quello che non
    deve mai succedere è una lista vuota senza spiegazione. Fissare qui
    ``stdout == "[]"`` avrebbe legato il test alla versione che NON spediamo.
    """
    workspace, outside = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(ns, _SWALLOWED[api].format(p=str(outside)))
    assert stdout.strip() != "[]" or "WORKSPACE BOUNDARY" in stderr
    if stdout.strip() == "[]":
        assert "1 path operation(s) refused" in stderr
        assert "hard policy boundary" in stderr
    else:
        assert stdout.strip() == ""
        assert _REFUSED in stderr


def test_the_note_names_the_refused_operation(sandbox) -> None:
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import glob; glob.glob({str(outside)!r} + '/*')")
    assert "os.scandir" in stderr
    assert str(outside) in stderr


def test_no_note_when_nothing_was_refused(sandbox) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import glob; glob.glob({str(workspace)!r} + '/*')")
    assert stderr == ""


def test_the_note_does_not_survive_into_the_next_execution(sandbox) -> None:
    """Il registro è azzerato all'ingresso del guard, non all'uscita."""
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _run(ns, f"import glob; glob.glob({str(outside)!r} + '/*')")
    _, stderr = _run(ns, "1 + 1")
    assert stderr == ""


def test_the_note_is_capped(sandbox) -> None:
    workspace, outside = sandbox
    for index in range(9):
        (outside / f"d{index}").mkdir()
    ns = _namespace(workspace)
    _, stderr = _run(
        ns,
        "import os\n"
        "for i in range(9):\n"
        "    try:\n"
        f"        os.listdir({str(outside)!r} + '/d%s' % i)\n"
        "    except OSError:\n"
        "        pass\n",
    )
    assert "9 path operation(s) refused" in stderr
    assert "(4 more)" in stderr


# ---------------------------------------------------------------------------
# R13 — le funzioni `os` che stavano in nessuna tabella
# ---------------------------------------------------------------------------


def test_utime_outside_workspace_is_refused(sandbox) -> None:
    workspace, outside = sandbox
    victim = outside / "secret.txt"
    before = victim.stat().st_mtime
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import os; os.utime({str(victim)!r}, (0, 0))")
    assert _REFUSED in stderr
    assert victim.stat().st_mtime == before


def test_path_touch_on_an_existing_outside_file_is_refused(sandbox) -> None:
    """Il caso raggiungibile per sbaglio: ``Path.touch()`` chiama ``os.utime``
    PER PRIMA e ritorna se il file esiste, quindi non arrivava mai all'``os.open``
    patchato."""
    workspace, outside = sandbox
    victim = outside / "secret.txt"
    before = victim.stat().st_mtime
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import pathlib; pathlib.Path({str(victim)!r}).touch()")
    assert _REFUSED in stderr
    assert victim.stat().st_mtime == before


def test_utime_inside_workspace_works(sandbox) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    target = workspace / "inside.txt"
    _, stderr = _run(ns, f"import os; os.utime({str(target)!r}, (1000, 1000))")
    assert stderr == ""
    assert target.stat().st_mtime == 1000


_PROBE_CASES = {
    "access": "os.access({p!r}, os.R_OK)",
    "readlink": "os.readlink({p!r})",
    "statvfs": "os.statvfs({p!r})",
    "pathconf": "os.pathconf({p!r}, 'PC_NAME_MAX')",
    "getxattr": "os.getxattr({p!r}, 'user.x')",
    "listxattr": "os.listxattr({p!r})",
    "setxattr": "os.setxattr({p!r}, 'user.x', b'1')",
    "removexattr": "os.removexattr({p!r}, 'user.x')",
}


@pytest.mark.parametrize("fn", sorted(_PROBE_CASES))
def test_unpatched_path_functions_are_now_refused(sandbox, fn) -> None:
    """Sonde e mutatori a un percorso che non stavano in nessuna tabella.

    ``os.stat`` era guardata e ``os.access`` no: due funzioni della stessa
    classe, comportamento opposto. Ora rispondono allo stesso modo.
    """
    if not hasattr(os, fn):
        pytest.skip(f"os.{fn} is not available on this platform")
    workspace, outside = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; " + _PROBE_CASES[fn].format(p=str(outside / "secret.txt")))
    assert _REFUSED in stderr


def test_access_inside_workspace_still_answers(sandbox) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns, f"import os; print(os.access({str(workspace / 'inside.txt')!r}, os.R_OK))"
    )
    assert stderr == ""
    assert stdout.strip() == "True"


def test_chdir_is_blocked_with_an_actionable_message(sandbox) -> None:
    """La cwd è UNA per il processo e la condivide il gateway: non è una
    questione di confine, è una mutazione dell'host."""
    workspace, _ = sandbox
    before = os.getcwd()
    ns = _namespace(workspace)
    _, stderr = _run(ns, f"import os; os.chdir({str(workspace)!r})")
    assert "process-global" in stderr
    assert "working_dir=" in stderr
    assert os.getcwd() == before


def test_chdir_is_blocked_even_inside_the_workspace(sandbox) -> None:
    """Non è il percorso a essere rifiutato: è la mutazione."""
    workspace, _ = sandbox
    ns = _namespace(workspace, restrict=False)
    _, stderr = _run(ns, f"import os; os.chdir({str(workspace)!r})")
    assert "process-global" in stderr


def test_chdir_still_works_for_host_code(sandbox) -> None:
    """Lo stub è guard-gated come tutti gli altri: il gateway non lo vede."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    _run(ns, "1 + 1")
    before = os.getcwd()
    os.chdir(str(workspace))
    os.chdir(before)
    assert os.getcwd() == before


# ---------------------------------------------------------------------------
# io.open_code
# ---------------------------------------------------------------------------


def test_io_open_code_outside_workspace_is_refused(sandbox) -> None:
    """``io`` è nell'allowlist e ``io.open_code`` apre per percorso.

    Su CPython 3.11 (la versione Chaquopy del device) ``io.open_code`` è
    importata direttamente da ``_io``, quindi era una terza via, diretta e
    completa, verso qualunque file. Chiuderla è gratis: la macchina di import
    scrive ``_io.open_code(...)``, cioè l'attributo del modulo C, non di ``io``.
    """
    workspace, outside = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns, f"import io; print(io.open_code({str(outside / 'secret.txt')!r}).read())"
    )
    assert "secret" not in stdout
    assert _REFUSED in stderr


def test_io_open_code_inside_workspace_works(sandbox) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns, f"import io; print(io.open_code({str(workspace / 'inside.txt')!r}).read())"
    )
    assert stderr == ""
    assert "inside" in stdout


def test_importing_still_works_after_the_open_code_patch(sandbox) -> None:
    """Il patch non deve toccare la macchina di import (usa ``_io.open_code``)."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(ns, "import json, csv, uuid; print('ok')")
    assert stderr == ""
    assert stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# Descrittori interi — un fd non è un confine, e non può diventarlo
# ---------------------------------------------------------------------------


def test_fdopen_on_a_workspace_file_works(sandbox) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns,
        "import os\n"
        f"fd = os.open({str(workspace / 'inside.txt')!r}, os.O_RDONLY)\n"
        "print(os.fdopen(fd).read())\n",
    )
    assert stderr == ""
    assert stdout.strip() == "inside"


def test_fileio_on_a_descriptor_works(sandbox) -> None:
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns,
        "import io, os\n"
        f"fd = os.open({str(workspace / 'inside.txt')!r}, os.O_RDONLY)\n"
        "print(io.FileIO(fd).read())\n",
    )
    assert stderr == ""
    assert "inside" in stdout


def test_fdopen_on_a_pipe_works(sandbox) -> None:
    """Una pipe non tocca il filesystem: non c'è nessun percorso da validare."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    stdout, stderr = _run(
        ns,
        "import os\n"
        "r, w = os.pipe()\n"
        "os.write(w, b'hi'); os.close(w)\n"
        "print(os.fdopen(r, 'rb').read())\n",
    )
    assert stderr == ""
    assert "hi" in stdout


def test_a_descriptor_is_never_stringified_into_a_path(sandbox) -> None:
    """Il ramo non si può togliere e basta: ``str(5)`` aprirebbe un file "5"."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    _run(
        ns,
        "import os\n"
        f"fd = os.open({str(workspace / 'inside.txt')!r}, os.O_RDONLY)\n"
        "os.fdopen(fd).read()\n",
    )
    assert not any(child.name.isdigit() for child in workspace.iterdir())


def test_dir_fd_is_still_refused(sandbox) -> None:
    """Ciò che resta rifiutato, e per un motivo diverso: con ``dir_fd`` la
    risoluzione non passa MAI dalla policy."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(
        ns,
        "import os\n"
        f"fd = os.open({str(workspace)!r}, os.O_RDONLY)\n"
        "os.unlink('inside.txt', dir_fd=fd)\n",
    )
    assert "is not allowed under workspace restriction" in stderr
    assert (workspace / "inside.txt").exists()


def test_os_open_with_an_integer_path_is_still_refused(sandbox) -> None:
    """In ``os.open`` un intero SIGNIFICA semantica relativa a un fd."""
    workspace, _ = sandbox
    ns = _namespace(workspace)
    _, stderr = _run(ns, "import os; os.open(0, os.O_RDONLY)")
    assert "file descriptor is not allowed" in stderr
