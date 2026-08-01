"""Tests for python_exec tool defaults."""

from unittest.mock import Mock, patch

from jenny.agent.tools.python_exec import (
    PythonExecTool,
    PythonNamespace,
    _register_builtin_functions,
)
from jenny.config.paths import get_workspace_path
from jenny.config.tool_schemas import PythonExecConfig


def test_python_namespace_default_working_dir():
    ns = PythonNamespace()
    assert ns.working_dir == str(get_workspace_path())


def test_python_namespace_explicit_working_dir():
    ns = PythonNamespace(working_dir="/tmp/custom")
    assert ns.working_dir == "/tmp/custom"


def test_python_exec_tool_default_working_dir():
    tool = PythonExecTool()
    assert tool.working_dir == str(get_workspace_path())
    assert tool.namespace.working_dir == str(get_workspace_path())


def test_python_exec_tool_explicit_working_dir():
    tool = PythonExecTool(working_dir="/tmp/other")
    assert tool.working_dir == "/tmp/other"
    assert tool.namespace.working_dir == "/tmp/other"


# ---------------------------------------------------------------------------
# http_get — SSRF redirect-bounce protection
# ---------------------------------------------------------------------------


def _make_namespace() -> PythonNamespace:
    ns = PythonNamespace()
    _register_builtin_functions(ns, workspace=str(get_workspace_path()), restrict_to_workspace=False)
    return ns


def _fake_validate(allowed_ok: bool = True, blocked_substrings: tuple[str, ...] = ("127.0.0.1",)):
    def _validate(url: str, **kwargs):
        if any(sub in url for sub in blocked_substrings):
            return False, f"Blocked: {url} resolves to private/internal address"
        return (allowed_ok, "")

    return _validate


class TestHttpGet:
    def test_allowed_host_returns_body_normally(self):
        """A request to a non-blocked host still succeeds and returns the body."""
        ns = _make_namespace()
        fake_resp = Mock()
        fake_resp.has_redirect_location = False
        fake_resp.text = "hello world"
        fake_resp.raise_for_status = Mock()

        with patch("jenny.security.network.validate_url_target", side_effect=_fake_validate()):
            with patch("httpx.get", return_value=fake_resp) as mock_get:
                _, _, result = ns.call_function("http_get", args=["https://example.com/data"])

        assert result == "hello world"
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        assert kwargs["follow_redirects"] is False

    def test_blocks_ssrf_on_initial_url(self):
        """The initial URL is still validated and blocked as before."""
        ns = _make_namespace()
        with patch("jenny.security.network.validate_url_target", side_effect=_fake_validate()):
            _, _, result = ns.call_function("http_get", args=["http://127.0.0.1:8080/admin"])
        assert result.startswith("Error: SSRF blocked")

    def test_does_not_follow_redirect_into_blocked_address(self):
        """A remote server that 3xx-redirects into a blocked address (the
        SSRF-bounce bug) must not have that redirect silently followed.
        Previously httpx's own follow_redirects=True would resolve/connect to
        the Location target with no re-validation at all."""
        ns = _make_namespace()
        fake_resp = Mock()
        fake_resp.has_redirect_location = True
        fake_resp.status_code = 302
        fake_resp.headers = {"location": "http://127.0.0.1:8765/internal-admin"}

        with patch("jenny.security.network.validate_url_target", side_effect=_fake_validate()):
            with patch("httpx.get", return_value=fake_resp) as mock_get:
                _, _, result = ns.call_function("http_get", args=["https://example.com/redirector"])

        # Only the original URL was ever fetched — the redirect target was
        # never requested.
        mock_get.assert_called_once()
        assert "redirect" in result.lower()
        assert "127.0.0.1:8765" in result

    def test_non_redirect_response_still_raises_for_http_errors(self):
        """raise_for_status behavior for real 4xx/5xx errors is unchanged."""
        import httpx

        ns = _make_namespace()
        fake_resp = Mock()
        fake_resp.has_redirect_location = False
        fake_resp.raise_for_status = Mock(side_effect=httpx.HTTPStatusError(
            "500 error", request=Mock(), response=Mock(status_code=500)
        ))

        with patch("jenny.security.network.validate_url_target", side_effect=_fake_validate()):
            with patch("httpx.get", return_value=fake_resp):
                stdout, stderr, result = ns.call_function(
                    "http_get", args=["https://example.com/broken"]
                )

        assert result is None
        assert "500 error" in stderr


# ---------------------------------------------------------------------------
# get_env / list_env — must share the same allowlist, no secret leakage
# ---------------------------------------------------------------------------


class TestGetEnvListEnv:
    def test_get_env_returns_allowed_keys(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        ns = _make_namespace()
        _, _, result = ns.call_function("get_env", args=["PATH"])
        assert result == "/usr/bin"

    def test_get_env_blocks_non_allowlisted_key(self, monkeypatch):
        monkeypatch.setenv("SOME_SECRET_VAR", "top-secret-value")
        ns = _make_namespace()
        _, _, result = ns.call_function("get_env", args=["SOME_SECRET_VAR"])
        assert result is None
        assert result != "top-secret-value"

    def test_list_env_unchanged_behavior(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("PYTHONPATH", "/opt/x")
        monkeypatch.setenv("SOME_SECRET_VAR", "top-secret-value")
        ns = _make_namespace()
        _, _, result = ns.call_function("list_env")
        assert result == {
            "PATH": "/usr/bin",
            "LANG": "en_US.UTF-8",
            "PYTHONPATH": "/opt/x",
        }
        assert "SOME_SECRET_VAR" not in result


# ---------------------------------------------------------------------------
# Intervento 1 — workspace containment for open()/io.open/pathlib + no raw httpx
# ---------------------------------------------------------------------------


def _restricted_namespace(workspace: str) -> PythonNamespace:
    """A namespace configured exactly like production under restriction:
    default allow/block module lists + restrict_to_workspace=True + the
    registered builtin helpers."""
    cfg = PythonExecConfig()
    ns = PythonNamespace(
        working_dir=workspace,
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=True,
        workspace=workspace,
    )
    _register_builtin_functions(ns, workspace=workspace, restrict_to_workspace=True)
    return ns


class TestWorkspaceOpenContainment:
    def test_builtin_open_outside_workspace_is_blocked(self, tmp_path):
        outside = tmp_path.parent / "escape_open.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(f"open({str(outside)!r}, 'w').write('x')")
        assert "outside allowed directory" in stderr
        assert not outside.exists()

    def test_builtin_open_inside_workspace_works(self, tmp_path):
        inside = tmp_path / "inside.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(
            f"f = open({str(inside)!r}, 'w'); f.write('hello'); f.close()"
        )
        assert stderr == ""
        assert inside.read_text() == "hello"

    def test_builtin_open_rejects_integer_fd(self, tmp_path):
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute("open(0, 'r')")
        assert "file descriptor is not allowed" in stderr

    def test_io_open_outside_workspace_is_blocked(self, tmp_path):
        outside = tmp_path.parent / "escape_io.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(
            f"import io; io.open({str(outside)!r}, 'w').write('x')"
        )
        assert "outside allowed directory" in stderr
        assert not outside.exists()

    def test_pathlib_write_text_outside_workspace_is_blocked(self, tmp_path):
        """OPTION A: pathlib routes through io.open on this Python build, so
        Path.write_text is contained by the io.open patch. This test fixes
        that expected behavior; if a future runtime changes the pathlib I/O
        dispatch, this test is the tripwire."""
        outside = tmp_path.parent / "escape_pathlib.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(
            f"import pathlib; pathlib.Path({str(outside)!r}).write_text('x')"
        )
        assert "outside allowed directory" in stderr
        assert not outside.exists()

    def test_pathlib_write_text_inside_workspace_works(self, tmp_path):
        inside = tmp_path / "inside_pathlib.txt"
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute(
            f"import pathlib; pathlib.Path({str(inside)!r}).write_text('ok')"
        )
        assert stderr == ""
        assert inside.read_text() == "ok"

    def test_unrestricted_open_is_unchanged(self, tmp_path):
        """With restrict_to_workspace=False the raw builtin open is used, so a
        path outside the workspace is writable (behavior unchanged)."""
        outside = tmp_path.parent / "unrestricted_open.txt"
        ns = PythonNamespace(
            working_dir=str(tmp_path),
            restrict_to_workspace=False,
            workspace=str(tmp_path),
        )
        _, stderr, _ = ns.execute(
            f"f = open({str(outside)!r}, 'w'); f.write('x'); f.close()"
        )
        assert stderr == ""
        assert outside.read_text() == "x"
        outside.unlink()


class TestHttpxNotAllowlisted:
    def test_import_httpx_blocked_by_default_allowlist(self, tmp_path):
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute("import httpx")
        assert "httpx" in stderr
        assert "not in the allowed modules" in stderr

    def test_httpx_absent_from_default_config(self):
        assert "httpx" not in PythonExecConfig().allowed_modules

    def test_http_get_to_metadata_ip_is_blocked(self, tmp_path):
        """Non-regression: the SSRF guard still blocks the cloud metadata IP
        via the http_get helper (no httpx import needed by guarded code)."""
        ns = _restricted_namespace(str(tmp_path))
        _, _, result = ns.call_function(
            "http_get", args=["http://169.254.169.254/latest/meta-data/"]
        )
        assert result.startswith("Error: SSRF blocked")


class TestUrllibNotAllowlisted:
    """urllib is a raw URL client: `urlopen` bypasses validate_url_target
    (SSRF via http://, LFI via file://). It must not be in the default
    allowlist, same rationale as httpx."""

    def test_import_urllib_blocked_by_default_allowlist(self, tmp_path):
        ns = _restricted_namespace(str(tmp_path))
        _, stderr, _ = ns.execute("from urllib.request import urlopen")
        assert "urllib" in stderr
        assert "not in the allowed modules" in stderr

    def test_urllib_absent_from_default_config(self):
        assert "urllib" not in PythonExecConfig().allowed_modules


class TestWorkspaceExecutable:
    """Il workspace è eseguibile: un modulo che si risolve in un file dentro
    il workspace è importabile anche se non è nell'allowlist esplicita — è
    codice fidato quanto ciò che l'agente scriverebbe inline. Ma i moduli
    bloccati restano bloccati, e ciò che è fuori dal workspace resta soggetto
    all'allowlist."""

    def test_workspace_module_importable_outside_allowlist(self, tmp_path, monkeypatch):
        scripts = tmp_path / "skills" / "demo" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "mytool.py").write_text("VALUE = 42\n")
        monkeypatch.syspath_prepend(str(scripts))
        ns = _restricted_namespace(str(tmp_path))
        assert "mytool" not in ns.allowed_modules
        stdout, stderr, _ = ns.execute("import mytool; print(mytool.VALUE)")
        assert stderr == ""
        assert stdout.strip() == "42"

    def test_blocked_module_stays_blocked_even_if_shadowed(self, tmp_path, monkeypatch):
        """Un file `subprocess.py` nel workspace NON deve poter sbloccare il
        nome bloccato — la precedenza del blocklist viene prima."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "subprocess.py").write_text("HACKED = True\n")
        monkeypatch.syspath_prepend(str(scripts))
        ns = _restricted_namespace(str(tmp_path))
        assert "subprocess" in ns.blocked_modules
        _, stderr, _ = ns.execute("import subprocess")
        assert "blocked in python_exec" in stderr

    def test_non_workspace_module_still_needs_allowlist(self, tmp_path):
        """Un modulo fuori allowlist e non nel workspace resta negato."""
        ns = _restricted_namespace(str(tmp_path))
        assert "urllib" not in ns.allowed_modules
        _, stderr, _ = ns.execute("import urllib.request")
        assert "not in the allowed modules" in stderr
