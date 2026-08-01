"""Python execution tool — replaces shell exec."""

from __future__ import annotations

import asyncio
import builtins
import importlib
import importlib.util
import io
import logging
import sys
import threading
import traceback
import types
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.context import current_request_session_key
from jenny.agent.tools.exec_session import (
    DEFAULT_EXEC_SESSION_MANAGER,
    DEFAULT_YIELD_MS,
    MAX_OUTPUT_CHARS,
    MAX_YIELD_MS,
    clamp_session_int,
    format_session_poll,
)
from jenny.agent.tools.python_exec_builtins import _register_builtin_functions
from jenny.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.config.paths import get_workspace_path
from jenny.config.tool_schemas import PythonExecConfig  # re-export (def in config.tool_schemas)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import guardrail for python_exec  (NB: NOT a security sandbox)
# ---------------------------------------------------------------------------
#
# IMPORTANT / TRUST BOUNDARY: python_exec runs arbitrary Python IN-PROCESS on
# the single Chaquopy interpreter. With `os`/`sys`/`shutil`/`httpx` in
# `allowed_modules`, there is NO in-process sandbox that resists a motivated
# adversary. The REAL containment is:
#   * the Android app sandbox (the app's own uid/permissions),
#   * the workspace path policy (filesystem writes — see security.workspace_policy),
#   * the SSRF policy (outbound network — see security.network).
# The allow/block module lists below are a USABILITY GUARDRAIL (they stop the
# agent from accidentally reaching for e.g. `subprocess`), NOT a containment
# control. Deployments that don't trust the model should disable python_exec
# via config (`tools.python_exec.enable = false`).
#
# `_guarded_import` (installed as `__builtins__['__import__']` in the guarded
# code's own namespace) enforces the allow/block lists on the attacker's
# top-level `import` statements, and guarded code is handed a proxy `sys`
# whose `.modules` is filtered (see `_GUARDED_SYS`).
#
# We deliberately do NOT patch `builtins.__import__` / `importlib` PROCESS-WIDE
# anymore: that mutated global interpreter state for the entire gateway
# process, was an unwinnable arms-race (guarded code can reach modules via
# `sys.modules`/`os.sys` regardless), and provided no real containment given
# `os`/`sys` are allowed. It only added a global-state hazard. Removed.

_import_guard_state = threading.local()


# ---------------------------------------------------------------------------
# Process-wide stdout/stderr redirect lock
# ---------------------------------------------------------------------------
#
# `PythonNamespace.execute()`/`call_function()` below use
# `contextlib.redirect_stdout`/`redirect_stderr`, which mutate the
# process-global `sys.stdout`/`sys.stderr` attribute for the duration of the
# call, then restore it. Real execution happens on separate OS threads: a
# one-shot `python_exec` call runs in the default executor threadpool (see
# `run_python_async`), and each long-running exec session
# (`exec_session.py::_PythonSession`) runs in its own dedicated background
# thread that calls back into these same two methods. If two such executions
# are genuinely concurrent, one's real stdout/stderr capture window can
# transiently swallow the other's output into the wrong buffer (or restore
# the global back to the wrong prior value), misattributing or losing
# output between unrelated tool calls.
#
# This lock ensures at most one thread is ever inside a redirect window at a
# time, so concurrent executions' output can no longer cross-contaminate.
#
# Trade-off: `exec_session.py` is explicitly designed for long-running
# background workflows, and several sessions may be active at once (see
# `ExecSessionManager.max_sessions`). Holding this lock for an entire
# session's execution means a long-running session will now block any
# other `python_exec`/exec-session call from starting its own redirect
# window until it finishes (or is stopped/times out) — a real, accepted
# usability cost for this fix. A non-blocking fix would require routing
# `sys.stdout`/`sys.stderr` per-thread instead of mutating them globally,
# which is a materially larger change than warranted for this low-severity
# output-attribution bug.
_stdout_redirect_lock = threading.Lock()


def _active_guard_rules() -> tuple[frozenset[str], frozenset[str]] | None:
    """Return (allowed, blocked) module sets for the guard active on this
    thread, or None if no python_exec code is currently executing here."""
    return getattr(_import_guard_state, "rules", None)


class _GuardedSysModules:
    """Filtered, read-only view over the real `sys.modules` mapping.

    Only restricts anything while a PythonNamespace guard is active on the
    current thread; outside of that window it passes straight through to
    the real mapping so normal host code is unaffected.
    """

    def _permitted(self, name: str) -> bool:
        rules = _active_guard_rules()
        if rules is None:
            return True
        allowed, blocked = rules
        base = name.split(".", 1)[0]
        if base in blocked:
            return False
        if allowed and base not in allowed:
            return False
        return True

    def __getitem__(self, name: str):
        if not self._permitted(name):
            raise KeyError(f"Module '{name}' is not accessible in python_exec")
        if name.split(".", 1)[0] == "sys":
            return _GUARDED_SYS
        return sys.modules[name]

    def get(self, name: str, default: Any = None):
        try:
            return self[name]
        except KeyError:
            return default

    def __contains__(self, name: str) -> bool:
        return self._permitted(name) and name in sys.modules

    def __iter__(self):
        return iter(k for k in list(sys.modules) if self._permitted(k))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def keys(self):
        return list(self)

    def items(self):
        return [(k, self[k]) for k in self]

    def values(self):
        return [self[k] for k in self]


class _GuardedSysModule:
    """Proxy for the `sys` module handed to guarded python_exec code.

    Transparently delegates everything to the real `sys` module except
    `.modules`, which is replaced by `_GuardedSysModules` so guarded code
    cannot use a plain `sys.modules[...]` lookup to reach a module outside
    its allowlist (this requires no import call, so it isn't covered by the
    `importlib`/`__import__` patch above).
    """

    modules = _GuardedSysModules()

    def __getattr__(self, name: str) -> Any:
        return getattr(sys, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(sys, name, value)


_GUARDED_SYS = _GuardedSysModule()


# ---------------------------------------------------------------------------
# Isolated namespace
# ---------------------------------------------------------------------------

class PythonNamespace:
    """Controlled Python namespace for exec tool."""

    def __init__(
        self,
        working_dir: str | None = None,
        allowed_modules: list[str] | None = None,
        blocked_modules: list[str] | None = None,
        restrict_to_workspace: bool = False,
        workspace: str | None = None,
    ):
        self.working_dir = working_dir or str(get_workspace_path())
        self.allowed_modules = set(allowed_modules or [])
        self.blocked_modules = set(blocked_modules or [])
        self.restrict_to_workspace = restrict_to_workspace
        self.workspace = workspace or self.working_dir
        self._ns: dict[str, Any] = {
            "__builtins__": self._safe_builtins(),
            "__name__": "__python_exec__",
            "__file__": "<python_exec>",
        }

    def _safe_builtins(self) -> dict[str, Any]:
        """Return a restricted set of builtins."""
        safe = {}
        blocked = {"exec", "eval", "compile", "__import__", "breakpoint", "exit", "quit"}
        for name in dir(builtins):
            if name.startswith("_") or name in blocked:
                continue
            safe[name] = getattr(builtins, name)
        # Re-add exec and eval for controlled use
        safe["exec"] = builtins.exec
        safe["eval"] = builtins.eval
        safe["__import__"] = self._guarded_import
        if self.restrict_to_workspace:
            # `open()` is the most-used file-I/O channel and the raw builtin
            # bypasses the workspace policy entirely (the registered helpers in
            # python_exec_builtins go through resolve_allowed_path, but a plain
            # `open("/outside", "w")` did not). Under restriction we hand the
            # guarded namespace a wrapper that resolves the path against the
            # workspace boundary. `io.open` (== builtins.open) and pathlib are
            # covered separately in _patch_io_open.
            safe["open"] = self._workspace_builtin_open
        return safe

    def _resolve_workspace_write(self, file: Any) -> Any:
        """Resolve *file* against the workspace boundary; reject raw fds.

        Shared by the builtin ``open`` wrapper and the ``io.open`` patch.
        Raises ``WorkspaceBoundaryError`` (an ``OSError`` subclass) with a
        clear "outside allowed directory" message when the path escapes the
        workspace — never an obscure failure.
        """
        from jenny.security.workspace_policy import resolve_allowed_path

        if isinstance(file, int):
            raise OSError(
                "open() with an integer file descriptor is not allowed "
                "under workspace restriction"
            )
        return resolve_allowed_path(
            str(file),
            workspace=self.workspace,
            allowed_root=self.workspace,
        )

    def _workspace_builtin_open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        """Workspace-contained replacement for the builtin ``open``.

        Installed in the guarded namespace only when ``restrict_to_workspace``
        is True; otherwise the raw builtin is used (behavior unchanged).
        """
        resolved = self._resolve_workspace_write(file)
        return builtins.open(str(resolved), mode, *args, **kwargs)

    _OS_BLOCKED_FUNCTIONS = frozenset({
        "system", "popen", "popen2", "popen3", "popen4",
        "execv", "execve", "execvp", "execvpe",
        "execl", "execle", "execlp", "execlpe",
        "spawnl", "spawnle", "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "posix_spawn", "posix_spawnp",
        "fork", "forkpty",
        "kill", "killpg",
        "setuid", "setgid", "seteuid", "setegid", "setreuid", "setregid",
        "setsid", "setpgrp", "setpgid",
        "chroot", "chown", "lchown",
        "mkfifo", "mknod",
        "openpty", "login_tty", "device_encoding",
        "get_terminal_size", "register_at_fork",
    })

    def _resolves_within_workspace(self, name: str) -> bool:
        """True se *name* è importabile da un file dentro il workspace.

        Un modulo che si risolve in un file locale del workspace non concede
        alcuna capability che il codice inline non abbia già (è codice scritto
        dall'utente o dall'agente, allo stesso livello di fiducia di ciò che
        finirebbe direttamente in ``python_exec``). Importarlo è quindi sicuro
        quanto eseguirlo inline, e va consentito anche se il nome non compare
        nell'allowlist esplicita — il workspace è eseguibile per definizione.

        Il controllo è fatto sul modulo top-level (``base``) via ``find_spec``,
        che localizza senza eseguire: risolvere un sottomodulo dotted invece
        importerebbe il package padre come effetto collaterale.
        """
        base = name.split(".")[0]
        try:
            spec = importlib.util.find_spec(base)
        except (ImportError, AttributeError, ValueError, TypeError):
            return False
        origin = getattr(spec, "origin", None) if spec is not None else None
        if not origin or origin in ("built-in", "frozen", "namespace"):
            return False
        from jenny.security.workspace_policy import resolve_allowed_path
        try:
            resolve_allowed_path(
                origin,
                workspace=self.workspace,
                allowed_root=self.workspace,
            )
        except Exception:
            return False
        return True

    def _guarded_import(self, name: str, *args, **kwargs):
        """Import hook that blocks dangerous modules and patches os."""
        base = name.split(".")[0]
        # I moduli bloccati vincono sempre, anche se un file omonimo esiste nel
        # workspace: un `subprocess.py` locale non deve poter sbloccare l'import.
        if base in self.blocked_modules:
            raise ImportError(f"Module '{name}' is blocked in python_exec")
        if self.allowed_modules and base not in self.allowed_modules:
            # Fuori allowlist ma risolvibile in un file del workspace → concesso
            # (workspace eseguibile). Altrimenti resta negato.
            if not self._resolves_within_workspace(name):
                raise ImportError(f"Module '{name}' is not in the allowed modules list")
        try:
            mod = importlib.import_module(name)
        except ImportError:
            raise ImportError(f"Module '{name}' is not available on this platform (blocked or missing)")
        if base == "sys":
            # Never hand out the real `sys` module: it carries the
            # unfiltered `sys.modules` table, which would let guarded code
            # reach any module already loaded in this process (including
            # `importlib`/`builtins`) with a plain dict lookup — no import
            # call involved, so nothing else in this guard would catch it.
            return _GUARDED_SYS
        if base == "os":
            self._patch_os_module(mod)
        if base in ("io", "pathlib") and self.restrict_to_workspace:
            # Cover `import io; io.open(...)` and the whole pathlib surface
            # (`Path.open`/`read_text`/`write_text`), which all dispatch to
            # io.open — patched here as soon as either module is imported by
            # guarded code (pathlib always requires `import pathlib` first, so
            # the patch is in place before any Path I/O runs).
            self._patch_io_open()
        self._patch_sys_backreferences(mod)
        return mod

    def _patch_sys_backreferences(self, mod: Any, _seen: set[int] | None = None, _depth: int = 0) -> None:
        """Neutralize direct references to the real `sys` module held by an
        allowed module (e.g. `os.sys`, `pathlib.sys`, `collections._sys`) —
        an artifact of that module doing `import sys` internally.

        These are just as reachable as `sys` itself (`import os;
        os.sys.modules[...]`) and would otherwise bypass the `_GUARDED_SYS`
        substitution above. The replacement is a fully attribute-transparent
        proxy, so the host module's own internal use of its `sys` reference
        keeps working normally.
        """
        if _depth > 2:
            return
        if _seen is None:
            _seen = set()
        if id(mod) in _seen:
            return
        _seen.add(id(mod))
        try:
            attrs = list(vars(mod).items())
        except TypeError:
            return
        for attr, val in attrs:
            if val is sys:
                try:
                    setattr(mod, attr, _GUARDED_SYS)
                except Exception:
                    continue
            elif isinstance(val, types.ModuleType) and val is not mod:
                self._patch_sys_backreferences(val, _seen, _depth + 1)

    def _patch_os_module(self, mod: Any) -> None:
        """Sostituisce le funzioni os shell-capable con stub d'errore.

        Come per ``os.open``/``io.open``, ``mod`` è il modulo ``os`` GLOBALE
        (condiviso col gateway) e il patch non viene mai ripristinato: gli stub
        sono quindi GUARD-GATED — bloccano solo mentre un exec guardato è attivo
        sul thread corrente (``_active_guard_rules()`` non è None) e delegano
        alla funzione reale per il codice host. Idempotente: la funzione reale è
        memorizzata una volta su ``_jenny_real_fn``, così i ripetuti ``import
        os`` (ognuno rientra qui) non impilano wrapper.
        """
        for fn in self._OS_BLOCKED_FUNCTIONS:
            if not hasattr(mod, fn):
                continue
            current = getattr(mod, fn)
            real = getattr(current, "_jenny_real_fn", current)

            def _blocked(*_a: Any, _real: Any = real, **_kw: Any) -> Any:
                if _active_guard_rules() is None:
                    # Codice host (nessun exec guardato su questo thread): intatto.
                    return _real(*_a, **_kw)
                raise OSError("This function is not available on this platform")

            _blocked._jenny_real_fn = real  # type: ignore[attr-defined]
            setattr(mod, fn, _blocked)

        if self.restrict_to_workspace:
            self._patch_os_open(mod)

    def _patch_os_open(self, mod: Any) -> None:
        """Confina ``os.open`` dentro il workspace, ma solo per codice guardato.

        ``mod`` è il modulo ``os`` GLOBALE, condiviso col gateway. Come per
        ``io.open`` (vedi ``_patch_io_open``), il wrapper è GUARD-GATED: applica
        il confine SOLO mentre un exec guardato è attivo sul thread corrente
        (``_active_guard_rules()`` non è None) e altrimenti passa dritto al vero
        ``os.open``. Senza questo gate il patch — mai ripristinato — resterebbe
        sul modulo globale e romperebbe l'``os.open`` del gateway: p.es. la
        stdlib ``tempfile`` usata da Chaquopy per estrarre le ``.so`` native
        (``_elementtree`` al primo ``import markdown`` della tab Wiki) verrebbe
        rifiutata perché il file temporaneo è fuori dal workspace.
        Idempotente: il vero opener è memorizzato su ``_jenny_real_open``.
        """
        from jenny.security.workspace_policy import resolve_allowed_path

        real_open = getattr(mod.open, "_jenny_real_open", mod.open)

        def _workspace_open(path: str | bytes | int, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            if _active_guard_rules() is None:
                # Codice host (nessun exec guardato su questo thread): intatto.
                return real_open(path, flags, mode, dir_fd=dir_fd)
            if isinstance(path, int):
                raise OSError("os.open with file descriptor is not allowed")
            if dir_fd is not None:
                raise OSError("os.open with dir_fd is not allowed")
            resolved = resolve_allowed_path(
                str(path),
                workspace=self.workspace,
                allowed_root=self.workspace,
            )
            return real_open(str(resolved), flags, mode)

        _workspace_open._jenny_real_open = real_open  # type: ignore[attr-defined]
        mod.open = _workspace_open

    def _patch_io_open(self) -> None:
        """Route ``io.open`` through the workspace policy under restriction.

        OPTION A (chosen): on this Python build ``pathlib.Path.open`` /
        ``.read_text`` / ``.write_text`` all dispatch to ``io.open`` (which is
        the same object as ``builtins.open``), so patching ``io.open`` covers
        the entire pathlib file-I/O surface without touching the pathlib class
        — verified by the test suite (see test_pathlib_write_text_*).

        Unlike the per-namespace builtin ``open`` replacement (isolated to the
        guarded namespace), ``io.open`` is a process-global object also used by
        the gateway itself. The wrapper is therefore GUARD-GATED: it enforces
        the boundary ONLY while guarded python_exec code is running on the
        current thread (``_active_guard_rules()`` is set), and passes straight
        through to the real ``io.open`` for all host code. This is the reason
        it differs from ``_patch_os_open`` (os.open is niche and not on the
        pathlib/host hot path).
        """
        real_open = getattr(io.open, "_jenny_real_open", io.open)

        def _workspace_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
            if _active_guard_rules() is None:
                # Host code (no guarded exec active on this thread): untouched.
                return real_open(file, mode, *args, **kwargs)
            resolved = self._resolve_workspace_write(file)
            return real_open(str(resolved), mode, *args, **kwargs)

        _workspace_io_open._jenny_real_open = real_open  # type: ignore[attr-defined]
        io.open = _workspace_io_open

    def _enter_guard(self) -> None:
        """Activate the process-wide import guard for this thread."""
        _import_guard_state.rules = (frozenset(self.allowed_modules), frozenset(self.blocked_modules))

    def _exit_guard(self) -> None:
        """Deactivate the process-wide import guard for this thread."""
        _import_guard_state.rules = None

    def execute(self, code: str) -> tuple[str, str, Any]:
        """Execute code and return (stdout, stderr, result)."""
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        result = None

        self._enter_guard()
        try:
            with _stdout_redirect_lock, redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                # Try eval first (for expressions)
                try:
                    result = eval(code, self._ns)
                except SyntaxError:
                    # Fall back to exec (for statements)
                    exec(code, self._ns)
        except Exception:
            traceback.print_exc(file=stderr_buf)
        finally:
            self._exit_guard()

        return stdout_buf.getvalue(), stderr_buf.getvalue(), result

    def call_function(self, name: str, args: list | None = None, kwargs: dict | None = None) -> tuple[str, str, Any]:
        """Call a registered function by name."""
        func = self._ns.get(name)
        if func is None:
            return "", f"Function '{name}' not found in namespace", None
        if not callable(func):
            return "", f"'{name}' is not callable", None

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        result = None

        self._enter_guard()
        try:
            with _stdout_redirect_lock, redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                result = func(*(args or []), **(kwargs or {}))
        except Exception:
            traceback.print_exc(file=stderr_buf)
        finally:
            self._exit_guard()

        return stdout_buf.getvalue(), stderr_buf.getvalue(), result

    def register_function(self, name: str, func: Any) -> None:
        """Register a callable in the namespace."""
        self._ns[name] = func


# ---------------------------------------------------------------------------
# Async wrapper with timeout
# ---------------------------------------------------------------------------


class PythonExecInterrupted(BaseException):
    """Iniettata nel thread di esecuzione per interromperlo (timeout o /stop).

    BaseException di proposito: un ``except Exception`` nel codice utente non
    deve poterla ingoiare.
    """


def _interrupt_thread(ident: int | None) -> None:
    """Best-effort: alza :class:`PythonExecInterrupted` nel thread *ident*.

    Usa ``PyThreadState_SetAsyncExc`` (ctypes, supportato da Chaquopy):
    interrompe loop e codice Python puro, NON chiamate C bloccanti — quei
    thread restano zombie ma innocui, gli effetti del turno sono già scartati
    dall'epoch di turno (vedi jenny.agent.turn_epochs).
    """
    if ident is None:
        return
    try:
        import ctypes

        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(ident), ctypes.py_object(PythonExecInterrupted)
        )
        if res > 1:
            # Contratto CPython: >1 = stato corrotto, va annullato subito.
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), None)
    except Exception:
        logger.debug("Could not interrupt python_exec thread %s", ident, exc_info=True)


async def run_python_async(
    code: str | None,
    function: str | None,
    args: list | None,
    kwargs: dict | None,
    namespace: PythonNamespace,
    timeout: int | None,
    max_output_chars: int,
) -> str:
    """Execute Python code/function in a thread with timeout."""
    loop = asyncio.get_running_loop()
    ident_cell: list[int | None] = [None]
    done_cell = [False]

    def _run():
        ident_cell[0] = threading.get_ident()
        try:
            if function:
                return namespace.call_function(function, args, kwargs)
            elif code:
                return namespace.execute(code)
            else:
                return "", "Error: Provide 'code' or 'function'", None
        except PythonExecInterrupted:
            # Il chiamante ha già mollato l'await: esito consumato da nessuno,
            # ritorno pulito per non sporcare i log del future abbandonato.
            return "", "Error: execution interrupted", None
        finally:
            done_cell[0] = True

    def _interrupt_if_running() -> None:
        # Se il thread ha già finito, l'interrupt colpirebbe il worker del
        # pool sul lavoro successivo: fire solo se ancora dentro _run.
        if not done_cell[0]:
            _interrupt_thread(ident_cell[0])

    try:
        if timeout and timeout > 0:
            stdout, stderr, result = await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=timeout,
            )
        else:
            stdout, stderr, result = await loop.run_in_executor(None, _run)
    except asyncio.TimeoutError:
        _interrupt_if_running()
        return f"Error: Python execution timed out after {timeout} seconds"
    except asyncio.CancelledError:
        # /stop (o abbandono del turno): prova a uccidere anche il thread.
        _interrupt_if_running()
        raise

    # Build output
    parts = []
    if stdout:
        parts.append(stdout.strip())
    if stderr:
        parts.append(f"STDERR:\n{stderr.strip()}")
    if result is not None:
        parts.append(f"Result: {result!r}")

    output = "\n".join(parts) if parts else "(no output)"

    # Truncate
    if len(output) > max_output_chars:
        half = max_output_chars // 2
        output = (
            output[:half]
            + f"\n\n... ({len(output) - max_output_chars:,} chars truncated) ...\n\n"
            + output[-half:]
        )

    return output


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        code=StringSchema(
            "Python code to execute. Can be a single expression or multiple statements.",
            nullable=True,
        ),
        function=StringSchema(
            "Name of a registered Python function to call. "
            "Use this for common operations like file I/O, HTTP, etc.",
            nullable=True,
        ),
        args=ArraySchema(
            StringSchema("Positional argument for the function call."),
            description="Positional arguments for the function call.",
            nullable=True,
        ),
        kwargs=ObjectSchema(
            description="Keyword arguments for the function call.",
            nullable=True,
        ),
        working_dir=StringSchema("Optional working directory for the execution.", nullable=True),
        timeout=IntegerSchema(
            60,
            description="Timeout in seconds (default 60, max 600).",
            minimum=1,
            maximum=600,
        ),
        max_output_chars=IntegerSchema(
            10000,
            description="Maximum output characters to return (default 10000, max 50000).",
            minimum=1000,
            maximum=MAX_OUTPUT_CHARS,
            nullable=True,
        ),
        yield_time_ms=IntegerSchema(
            description=(
                "Optional milliseconds to wait before returning output. "
                "When set, a still-running execution returns a session_id that "
                "can be polled with write_stdin."
            ),
            minimum=0,
            maximum=MAX_YIELD_MS,
            nullable=True,
        ),
    )
)
class PythonExecTool(Tool):
    """Execute Python code or call registered functions."""

    _scopes = {"core", "subagent"}
    config_key = "python_exec"

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    @classmethod
    def config_cls(cls):
        return PythonExecConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        cfg = getattr(ctx.config, "python_exec", None)
        if cfg is None:
            return True
        return cfg.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        cfg = getattr(ctx.config, "python_exec", None)
        if cfg is None:
            cfg = PythonExecConfig()
        # Accesso diretto (come filesystem.py): un attributo mancante deve
        # essere un errore rumoroso, non un silenzioso "unrestricted" (il
        # vecchio fallback getattr(..., False) apriva in caso di config anomala).
        restrict = ctx.config.restrict_to_workspace
        tool = cls(
            working_dir=str(ctx.workspace),
            timeout=cfg.timeout,
            max_output_chars=cfg.max_output_chars,
            allowed_modules=cfg.allowed_modules,
            blocked_modules=cfg.blocked_modules,
            restrict_to_workspace=restrict,
            workspace=str(ctx.workspace),
        )
        _register_builtin_functions(
            tool.namespace,
            workspace=str(ctx.workspace),
            restrict_to_workspace=restrict,
        )
        return tool

    def __init__(
        self,
        working_dir: str | None = None,
        timeout: int = 60,
        max_output_chars: int = 10_000,
        allowed_modules: list[str] | None = None,
        blocked_modules: list[str] | None = None,
        restrict_to_workspace: bool = False,
        workspace: str | None = None,
        session_manager: Any | None = None,
    ):
        self.working_dir = working_dir or str(get_workspace_path())
        self.timeout = timeout
        self.max_output_chars = max_output_chars
        self.namespace = PythonNamespace(
            working_dir=self.working_dir,
            allowed_modules=allowed_modules,
            blocked_modules=blocked_modules,
            restrict_to_workspace=restrict_to_workspace,
            workspace=workspace,
        )
        self._session_manager = session_manager or DEFAULT_EXEC_SESSION_MANAGER

    @property
    def name(self) -> str:
        return "python_exec"

    @property
    def description(self) -> str:
        return (
            "Execute Python code or call a registered function. "
            "Use code='...' for inline Python (expressions or statements). "
            "Use function='name' with args/kwargs to call registered functions. "
            "Prefer dedicated tools (read_file, grep, apply_patch, web_search, web_fetch) for file/search/web tasks. "
            "Use python_exec for tests, builds, calculations, data processing, "
            "and other logic. Output is truncated at 10000 chars."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self,
        code: str | None = None,
        function: str | None = None,
        args: list | None = None,
        kwargs: dict | None = None,
        working_dir: str | None = None,
        timeout: int | None = None,
        max_output_chars: int | None = None,
        yield_time_ms: int | None = None,
        **kwargs_extra: Any,
    ) -> str:
        if not code and not function:
            return "Error: Provide 'code' or 'function' parameter."

        # Update working dir if provided
        if working_dir:
            self.namespace.working_dir = working_dir

        effective_timeout = self._resolve_timeout(timeout)
        effective_max = clamp_session_int(
            max_output_chars, self._MAX_OUTPUT, 1000, MAX_OUTPUT_CHARS,
        )

        if yield_time_ms is not None:
            return await self._execute_session(
                code=code,
                function=function,
                args=args,
                kwargs=kwargs,
                yield_time_ms=yield_time_ms,
                max_output_chars=effective_max,
                timeout=effective_timeout,
            )

        return await run_python_async(
            code=code,
            function=function,
            args=args,
            kwargs=kwargs,
            namespace=self.namespace,
            timeout=effective_timeout,
            max_output_chars=effective_max,
        )

    async def _execute_session(
        self,
        *,
        code: str | None,
        function: str | None,
        args: list | None,
        kwargs: dict | None,
        yield_time_ms: int,
        max_output_chars: int,
        timeout: int | None,
    ) -> str:
        try:
            session_id, poll = await self._session_manager.start_python(
                code=code,
                function=function,
                args=args,
                kwargs=kwargs,
                namespace=self.namespace,
                timeout=timeout,
                yield_time_ms=clamp_session_int(yield_time_ms, DEFAULT_YIELD_MS, 0, MAX_YIELD_MS),
                owner_session_key=current_request_session_key(),
                max_output_chars=max_output_chars,
            )
            return format_session_poll(session_id, poll)
        except Exception as exc:
            return f"Error executing Python: {exc}"

    def _resolve_timeout(self, timeout: int | None) -> int | None:
        if timeout:
            return min(timeout, self._MAX_TIMEOUT)
        if self.timeout and self.timeout > 0:
            return self.timeout
        return None


# ---------------------------------------------------------------------------
# Built-in functions registered in namespace
# ---------------------------------------------------------------------------

# Environment variables exposed to sandboxed code via get_env()/list_env().
# Keep this the single source of truth for both functions so they can never
# drift apart — anything not in this set must never reach the model.


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [PythonExecTool]
