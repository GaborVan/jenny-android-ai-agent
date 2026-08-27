"""I quattro tool SSH, contro un server SSH vero che esegue davvero i comandi.

Un finto backend qui proverebbe poco: quasi tutto ciò che questi tool devono
garantire — troncamento riportato, timeout che indirizza a ``ssh_job``, cap sui
trasferimenti, job che sopravvive alla connessione — è comportamento del
trasporto, non formattazione. Il server è lo stesso di
``test_ssh_jobs.py``: asyncssh su loopback che passa i comandi a ``/bin/sh``.

Due dettagli di infrastruttura, entrambi già costati tempo altrove:

* niente fixture async generator (``@pytest.fixture`` su ``async def`` con
  ``yield``): con pytest 9 + pytest-asyncio 1.4 il test si **pianta** invece di
  fallire. Si usa un ``asynccontextmanager`` con ``async with`` nel test;
* la policy di rete blocca il loopback, correttamente. I tool accettano un
  ``validate`` iniettabile per la stessa ragione per cui lo accetta
  ``ssh_transport.resolve_target``: indebolire la policy vera per far girare
  quella finta sarebbe il baratto sbagliato.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from jenny.agent.tools import ssh_transport
from jenny.agent.tools.ssh import (
    SshExecTool,
    SshHostsTool,
    SshJobTool,
    SshTransferTool,
)
from jenny.agent.tools.ssh_jobs import reset_job_store
from jenny.config.schema import Config
from jenny.config.tool_schemas import SshHostConfig

# ``asyncssh`` non e una dipendenza runtime: su Android il client SSH e jsch
# via bridge nativo, e il pacchetto non entra mai nei requirements del device
# (stesso trattamento di ``cryptography``). Serve solo qui, per alzare un
# server SSH vero in-process. Senza importorskip questi moduli fallivano in
# *collection* su una macchina che non ce l'ha — come il runner della CI.
asyncssh = pytest.importorskip("asyncssh")

TEST_USER = "jenny"
ALIAS = "lab"


def _allow_everything(_host: str) -> tuple[bool, str]:
    return True, ""


async def _handle_shell(process: asyncssh.SSHServerProcess) -> None:
    proc = await asyncio.create_subprocess_shell(
        process.command or "",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, err = await proc.communicate()
    finally:
        # I due test sul timeout (`test_ssh_exec_timeout_points_at_ssh_job`,
        # `test_ssh_exec_cannot_raise_the_configured_timeout`) abbandonano la
        # richiesta, asyncssh cancella questa coroutine mentre `communicate()`
        # sta ancora aspettando un figlio VIVO, e il transport sopravvive al
        # loop del test. `BaseSubprocessTransport.close()` e' raggiunta solo da
        # `__del__`, quindi il GC piu' tardi chiama `loop.call_soon()` su un
        # loop chiuso: `RuntimeError: Event loop is closed`, sollevata dentro
        # `__del__` e quindi attribuita a UN TEST QUALUNQUE — quello che stava
        # girando quando e' scattato il GC. Chiuderlo qui uccide anche il figlio
        # orfano. Misurato il 23/08: transport trapelati 2 -> 0 (T8.9).
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()
    process.stdout.write(out.decode(errors="replace"))
    process.stderr.write(err.decode(errors="replace"))
    process.exit(proc.returncode or 0)


@dataclass(slots=True)
class _Env:
    config: Config
    workspace: Path
    port: int
    tmp: Path

    def host(self) -> SshHostConfig:
        return self.config.tools.ssh.hosts[0]


@asynccontextmanager
async def tool_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Server SSH vero + workspace isolato + config fresca intercettata."""
    from jenny.config import loader as loader_mod
    from jenny.config import paths as paths_mod
    from jenny.runtime.context import get_runtime_context

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key_path = tmp_path / "host_key"
    host_key.write_private_key(str(host_key_path))

    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized = tmp_path / "authorized_keys"
    authorized.write_bytes(client_key.export_public_key("openssh"))

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[str(host_key_path)],
        authorized_client_keys=str(authorized),
        process_factory=_handle_shell,
        sftp_factory=True,
    )
    port = server.sockets[0].getsockname()[1]

    previous_workspace = get_runtime_context().workspace_dir
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    paths_mod.set_workspace_dir(str(workspace))

    # La chiave privata vive dove ``ssh_transport`` la cerca: fuori dal
    # workspace, accanto ad esso.
    key_path = ssh_transport.ssh_key_path(ALIAS)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    client_key.write_private_key(str(key_path))
    os.chmod(key_path, 0o600)

    line = " ".join(host_key.export_public_key("openssh").decode().split()[:2])
    ssh_transport.record_host_key(f"[127.0.0.1]:{port} {line}")

    config = Config()
    config.tools.ssh.enable = True
    config.tools.ssh.hosts = [
        SshHostConfig(
            alias=ALIAS,
            host="127.0.0.1",
            port=port,
            username=TEST_USER,
            description="the lab box",
            job_log_dir=str(tmp_path / "jobs"),
        )
    ]
    # I tool rileggono la config a ogni chiamata (è il motivo per cui
    # ``ssh_hosts`` esiste): basta intercettare la funzione che usano.
    monkeypatch.setattr(loader_mod, "load_config", lambda *a, **k: config)

    ssh_transport.reset_ssh_backend()
    reset_job_store()
    try:
        yield _Env(config=config, workspace=workspace, port=port, tmp=tmp_path)
    finally:
        await ssh_transport.get_ssh_backend().close_all()
        ssh_transport.reset_ssh_backend()
        reset_job_store()
        paths_mod.set_workspace_dir(str(previous_workspace) if previous_workspace else "")
        server.close()
        await server.wait_closed()


def _exec_tool() -> SshExecTool:
    return SshExecTool(validate=_allow_everything)


def _job_tool() -> SshJobTool:
    return SshJobTool(validate=_allow_everything)


def _transfer_tool(env: _Env) -> SshTransferTool:
    return SshTransferTool(workspace=env.workspace, validate=_allow_everything)


# -- ssh_hosts ---------------------------------------------------------------


async def test_ssh_hosts_lists_alias_user_and_description(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        out = await SshHostsTool().execute()
    assert ALIAS in out
    assert f"{TEST_USER}@127.0.0.1:{env.port}" in out
    assert "the lab box" in out


async def test_ssh_hosts_sees_a_host_added_after_the_tool_was_built(tmp_path, monkeypatch):
    """La ragione d'essere del tool: i tool non si ricostruiscono al salvataggio."""
    async with tool_env(tmp_path, monkeypatch) as env:
        tool = SshHostsTool()
        assert "nas" not in await tool.execute()

        env.config.tools.ssh.hosts.append(
            SshHostConfig(alias="nas", host="10.0.0.5", username="root")
        )
        assert "nas" in await tool.execute()


async def test_ssh_hosts_says_what_to_do_when_nothing_is_configured(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        env.config.tools.ssh.hosts = []
        out = await SshHostsTool().execute()
    assert "Settings" in out


# -- ssh_exec ----------------------------------------------------------------


async def test_ssh_exec_returns_exit_code_and_stdout(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        out = await _exec_tool().execute(host=ALIAS, command="printf 'ciao\\n'")
    assert "exit code: 0" in out
    assert "ciao" in out


async def test_ssh_exec_reports_a_failed_command_without_erroring(tmp_path, monkeypatch):
    """Un comando fallito è un RISULTATO: il modello deve leggerlo e decidere."""
    async with tool_env(tmp_path, monkeypatch):
        out = await _exec_tool().execute(
            host=ALIAS, command="printf 'boom\\n' >&2; exit 3"
        )
    assert "exit code: 3" in out
    assert "boom" in out
    assert not out.startswith("Error:")


async def test_ssh_exec_reports_how_much_output_it_dropped(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        env.config.tools.ssh.max_output_chars = 1_000
        out = await _exec_tool().execute(
            host=ALIAS, command="printf 'x%.0s' $(seq 1 3000)"
        )
    assert "2000 characters were dropped" in out
    assert "grep" in out  # gli si dice cosa fare, non solo che ha perso roba


async def test_ssh_exec_timeout_points_at_ssh_job(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        out = await _exec_tool().execute(host=ALIAS, command="sleep 5", timeout_s=1)
    assert out.startswith("Error:")
    assert "ssh_job" in out


async def test_ssh_exec_cannot_raise_the_configured_timeout(tmp_path, monkeypatch):
    """Il tetto è la ragione per cui ssh_job esiste: un tool non deve poterlo alzare."""
    async with tool_env(tmp_path, monkeypatch) as env:
        env.config.tools.ssh.command_timeout_s = 1
        out = await _exec_tool().execute(host=ALIAS, command="sleep 5", timeout_s=300)
    assert out.startswith("Error:")
    assert "ssh_job" in out


async def test_unknown_alias_lists_the_valid_ones(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        out = await _exec_tool().execute(host="staging", command="true")
    assert out.startswith("Error:")
    assert ALIAS in out


async def test_unpinned_host_says_a_person_must_accept_the_fingerprint(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        ssh_transport.forget_host("127.0.0.1", env.port)
        out = await _exec_tool().execute(host=ALIAS, command="true")
    assert out.startswith("Error:")
    assert "Settings" in out
    assert "cannot be bypassed" in out


async def test_missing_private_key_is_reported_not_raised(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        ssh_transport.ssh_key_path(ALIAS).unlink()
        out = await _exec_tool().execute(host=ALIAS, command="true")
    assert out.startswith("Error:")
    assert "generate one" in out


async def test_blocked_host_error_says_it_is_not_retryable(tmp_path, monkeypatch):
    """Il loopback è bloccato dalla policy vera: qui si usa quella, non l'iniezione."""
    async with tool_env(tmp_path, monkeypatch):
        out = await SshExecTool().execute(host=ALIAS, command="true")
    assert out.startswith("Error:")
    assert "not retryable" in out


# -- ssh_job -----------------------------------------------------------------


async def test_ssh_job_start_poll_and_finish(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        tool = _job_tool()
        gate = tmp_path / "gate"
        started = await tool.execute(
            host=ALIAS,
            action="start",
            command=f"printf 'alpha\\n'; while [ ! -f {gate} ]; do sleep 0.05; done; "
            "printf 'omega\\n'",
        )
        assert "Started job" in started
        job_id = started.split("Started job ")[1].split()[0]

        first = ""
        for _ in range(100):
            first = await tool.execute(host=ALIAS, action="poll", job_id=job_id)
            if "alpha" in first:
                break
            await asyncio.sleep(0.05)
        assert "alpha" in first
        assert "running" in first

        gate.write_text("go")
        last = ""
        for _ in range(100):
            last = await tool.execute(host=ALIAS, action="poll", job_id=job_id)
            if "finished" in last:
                break
            await asyncio.sleep(0.05)
        assert "omega" in last
        assert "exit code 0" in last
        # Il delta non ripete cio che era gia stato consegnato.
        assert "alpha" not in last


async def test_ssh_job_start_requires_a_command(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        out = await _job_tool().execute(host=ALIAS, action="start")
    assert out.startswith("Error:")
    assert "command" in out


async def test_ssh_job_poll_requires_a_job_id(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        out = await _job_tool().execute(host=ALIAS, action="poll")
    assert out.startswith("Error:")
    assert "job_id" in out


async def test_ssh_job_unknown_id_lists_the_known_ones(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        tool = _job_tool()
        started = await tool.execute(host=ALIAS, action="start", command="true")
        job_id = started.split("Started job ")[1].split()[0]
        out = await tool.execute(host=ALIAS, action="poll", job_id="not-a-job")
    assert out.startswith("Error:")
    assert job_id in out


async def test_ssh_job_list_still_works_when_the_host_is_unreachable(tmp_path, monkeypatch):
    """È proprio quando l'host non risponde che serve sapere cosa era in sospeso."""
    async with tool_env(tmp_path, monkeypatch) as env:
        tool = _job_tool()
        started = await tool.execute(host=ALIAS, action="start", command="true")
        job_id = started.split("Started job ")[1].split()[0]

        ssh_transport.forget_host("127.0.0.1", env.port)  # host key non più accettata
        out = await tool.execute(host=ALIAS, action="list")

    assert job_id in out
    assert not out.startswith("Error:")


async def test_ssh_job_stop_marks_the_job_stopped(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        tool = _job_tool()
        started = await tool.execute(host=ALIAS, action="start", command="sleep 60")
        job_id = started.split("Started job ")[1].split()[0]

        stopped = await tool.execute(host=ALIAS, action="stop", job_id=job_id)
        assert "SIGTERM" in stopped

        listed = await tool.execute(host=ALIAS, action="list")
    assert "[stopped" in listed


async def test_ssh_job_rejects_an_unknown_action(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch):
        out = await _job_tool().execute(host=ALIAS, action="restart")
    assert out.startswith("Error:")


# -- ssh_transfer ------------------------------------------------------------


async def test_transfer_round_trip(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        source = env.workspace / "notes.txt"
        source.write_text("contenuto da caricare")
        remote = str(tmp_path / "remote_copy.txt")

        up = await _transfer_tool(env).execute(
            host=ALIAS, direction="up", local_path="notes.txt", remote_path=remote
        )
        assert "Uploaded" in up
        assert Path(remote).read_text() == "contenuto da caricare"

        down = await _transfer_tool(env).execute(
            host=ALIAS,
            direction="down",
            local_path="back/copy.txt",
            remote_path=remote,
        )
        assert "Downloaded" in down
        assert (env.workspace / "back" / "copy.txt").read_text() == "contenuto da caricare"


async def test_transfer_refuses_a_local_path_outside_the_workspace(tmp_path, monkeypatch):
    """La directory SSH (chiave privata, known_hosts) vive fuori dal workspace."""
    async with tool_env(tmp_path, monkeypatch) as env:
        out = await _transfer_tool(env).execute(
            host=ALIAS,
            direction="up",
            local_path=str(ssh_transport.ssh_key_path(ALIAS)),
            remote_path=str(tmp_path / "stolen_key"),
        )
    assert out.startswith("Error:")
    assert not (tmp_path / "stolen_key").exists()


async def test_transfer_refuses_a_traversal_out_of_the_workspace(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        out = await _transfer_tool(env).execute(
            host=ALIAS,
            direction="down",
            local_path="../../escaped.txt",
            remote_path="/etc/hostname",
        )
    assert out.startswith("Error:")
    assert not (tmp_path.parent / "escaped.txt").exists()


async def test_upload_over_the_cap_is_refused_before_connecting(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        env.config.tools.ssh.max_transfer_bytes = 1024
        (env.workspace / "big.bin").write_bytes(b"y" * 5000)
        remote = str(tmp_path / "should_not_exist.bin")

        out = await _transfer_tool(env).execute(
            host=ALIAS, direction="up", local_path="big.bin", remote_path=remote
        )
    assert out.startswith("Error:")
    assert "5000" in out and "1024" in out
    assert not Path(remote).exists()


async def test_download_over_the_cap_leaves_no_truncated_file(tmp_path, monkeypatch):
    """Un cap applicato mentre si scrive lascerebbe un file troncato indistinguibile."""
    async with tool_env(tmp_path, monkeypatch) as env:
        env.config.tools.ssh.max_transfer_bytes = 1024
        remote = tmp_path / "huge.bin"
        remote.write_bytes(b"y" * 5000)

        out = await _transfer_tool(env).execute(
            host=ALIAS, direction="down", local_path="huge.bin", remote_path=str(remote)
        )
    assert out.startswith("Error:")
    assert not (env.workspace / "huge.bin").exists()


async def test_transfer_reports_a_missing_local_file(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        out = await _transfer_tool(env).execute(
            host=ALIAS, direction="up", local_path="nope.txt", remote_path="/tmp/nope"
        )
    assert out.startswith("Error:")


async def test_transfer_rejects_an_unknown_direction(tmp_path, monkeypatch):
    async with tool_env(tmp_path, monkeypatch) as env:
        out = await _transfer_tool(env).execute(
            host=ALIAS, direction="sideways", local_path="a.txt", remote_path="/tmp/a"
        )
    assert out.startswith("Error:")


# -- registrazione e scope ---------------------------------------------------

_SSH_TOOLS = (SshHostsTool, SshExecTool, SshJobTool, SshTransferTool)


def test_every_ssh_tool_is_remote_scope_only():
    """``remote`` è uno scope nuovo apposta: ``operator`` è "tutto lo scope
    subagent", e ereditare SSH gli darebbe web + codice + shell remota insieme."""
    for tool_cls in _SSH_TOOLS:
        assert tool_cls._scopes == {"remote"}, tool_cls.__name__


def test_ssh_tools_are_absent_from_every_existing_scope():
    from unittest.mock import MagicMock

    from jenny.agent.tools.loader import ToolLoader
    from jenny.agent.tools.registry import ToolRegistry

    names = {"ssh_hosts", "ssh_exec", "ssh_job", "ssh_transfer"}
    for scope in ("core", "orchestrator", "subagent"):
        registered = ToolLoader().load(MagicMock(), ToolRegistry(), scope=scope)
        assert names.isdisjoint(registered), scope


def test_the_module_declares_its_tools_for_the_loader():
    from jenny.agent.tools import ssh as ssh_module
    from jenny.agent.tools.loader import _HARDCODED_TOOL_MODULES

    assert set(ssh_module.TOOLS) == set(_SSH_TOOLS)
    assert "ssh" in _HARDCODED_TOOL_MODULES


@pytest.mark.parametrize(
    ("enable", "hosts", "expected"),
    [(False, True, False), (True, False, False), (True, True, True)],
)
def test_enabled_needs_both_the_toggle_and_a_host(enable: bool, hosts: bool, expected: bool):
    from types import SimpleNamespace

    config = Config()
    config.tools.ssh.enable = enable
    if hosts:
        config.tools.ssh.hosts = [
            SshHostConfig(alias="x", host="example.com", username="u")
        ]
    ctx = SimpleNamespace(config=config.tools, workspace="/tmp", android_context=None)
    for tool_cls in _SSH_TOOLS:
        assert tool_cls.enabled(ctx) is expected, tool_cls.__name__


def test_enabled_does_not_require_android():
    """Gattare su ``android_context`` renderebbe i tool non testabili sul Mac."""
    from types import SimpleNamespace

    config = Config()
    config.tools.ssh.enable = True
    config.tools.ssh.hosts = [SshHostConfig(alias="x", host="example.com", username="u")]
    ctx = SimpleNamespace(config=config.tools, workspace="/tmp", android_context=None)
    assert SshExecTool.enabled(ctx) is True


# -- interruttore di emergenza -----------------------------------------------


async def test_disabling_ssh_takes_effect_without_a_restart(tmp_path, monkeypatch):
    """Spegnere SSH deve fermare subito anche un tool gia costruito.

    ``enabled()`` gira una volta sola allo startup, quindi RIACCENDERE SSH
    richiede un riavvio. Spegnerlo no: e l'interruttore di emergenza, e chi
    toglie la spunta mentre un subagent sta lavorando su un server si aspetta
    che smetta, non che finisca il turno.
    """
    async with tool_env(tmp_path, monkeypatch) as env:
        tool = _exec_tool()
        assert "exit code: 0" in await tool.execute(host=ALIAS, command="echo ciao")

        env.config.tools.ssh.enable = False
        result = await tool.execute(host=ALIAS, command="echo ciao")

    assert "switched off" in result
    assert "exit code" not in result


async def test_hosts_tool_distinguishes_switched_off_from_none_configured(
    tmp_path, monkeypatch
):
    """Dire "aggiungi un host" a chi ce li ha gia manda il modello fuori strada."""
    async with tool_env(tmp_path, monkeypatch) as env:
        env.config.tools.ssh.enable = False
        result = await SshHostsTool().execute()

    assert "switched off" in result
    assert "add one" not in result


def test_enabled_reads_the_shape_the_runtime_actually_passes():
    """``ctx.config`` e una ToolsConfig, NON la Config radice.

    Regressione: leggendo ``ctx.config.tools.ssh`` il gate era sempre None ->
    False, quindi i tool non si caricavano mai sul dispositivo. Il test
    precedente non lo vedeva perche costruiva il ctx con una Config intera,
    cioe con una forma che in produzione non esiste. Qui si passa esattamente
    quello che passano ``loop.py`` e ``subagent.py``.
    """
    from types import SimpleNamespace

    from jenny.config.schema import ToolsConfig

    tools_config = ToolsConfig()
    tools_config.ssh.enable = True
    tools_config.ssh.hosts = [SshHostConfig(alias="x", host="example.com", username="u")]

    ctx = SimpleNamespace(config=tools_config, workspace="/tmp", android_context=None)
    assert SshExecTool.enabled(ctx) is True


def test_subagent_tools_config_carries_ssh_through():
    """Senza questo il tipo ``sysadmin`` non vedrebbe mai i tool SSH.

    ``SubagentManager`` ricostruisce una ToolsConfig ridotta per i subagent: se
    ``ssh`` non viene propagato arrivano i default (spento, zero host) e
    l'allowlist del tipo non ha nulla da filtrare.
    """
    from jenny.config.schema import ToolsConfig

    source = ToolsConfig()
    source.ssh.enable = True
    source.ssh.hosts = [SshHostConfig(alias="x", host="example.com", username="u")]

    from types import SimpleNamespace

    from jenny.agent.subagent import SubagentManager

    # ``_live_tools_config`` stubbata sulla stessa sorgente: qui interessa solo
    # che la sezione ``ssh`` sopravviva alla riduzione, non da dove arrivi (la
    # freschezza ha i suoi test in ``test_subagent_config_freshness.py``).
    scoped = SubagentManager._subagent_tools_config(
        SimpleNamespace(tools_config=source, _live_tools_config=lambda: source)  # type: ignore[arg-type]
    )
    assert scoped.ssh.enable is True
    assert [h.alias for h in scoped.ssh.hosts] == ["x"]
