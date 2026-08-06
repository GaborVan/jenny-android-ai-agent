"""Il backend dev contro un server SSH VERO, alzato in-process.

Non ci sono mock qui, ed è il punto: il contratto in ``ssh_backends/base.py``
descrive un comportamento (troncamento, timeout, rifiuto per host key
sconosciuta, cap sui trasferimenti) che deve valere identico anche per il
bridge jsch sul device. Misurarlo contro un finto trasporto proverebbe solo che
il finto trasporto fa quello che gli abbiamo detto di fare.

NB sull'infrastruttura: l'ambiente si monta con un ``asynccontextmanager`` e non
con una fixture async. Nessun altro test del repo usa fixture async generator, e
con pytest 9 + pytest-asyncio 1.4 quella combinazione si pianta invece di
fallire — un `async with` esplicito costa due righe e non ha quel problema.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import asyncssh
import pytest

from jenny.agent.tools.ssh_backends.base import (
    SshAuthError,
    SshHostKeyError,
    SshTarget,
    SshTimeoutError,
    SshTransportError,
)
from jenny.agent.tools.ssh_backends.dev import DevSshBackend, _known_hosts_entry

TEST_USER = "jenny"


async def _handle_process(process: asyncssh.SSHServerProcess) -> None:
    """Mini interprete: giusto i comportamenti che i test devono osservare."""
    command = process.command or ""
    if command.startswith("echo "):
        process.stdout.write(command[5:] + "\n")
        process.exit(0)
    elif command == "fail":
        process.stderr.write("something broke\n")
        process.exit(3)
    elif command.startswith("big "):
        process.stdout.write("x" * int(command[4:]))
        process.exit(0)
    elif command.startswith("sleep "):
        await asyncio.sleep(float(command[6:]))
        process.exit(0)
    else:
        process.stderr.write(f"unknown command: {command}\n")
        process.exit(127)


@dataclass(slots=True)
class _Env:
    backend: DevSshBackend
    target: SshTarget
    port: int
    key_path: Path
    known_hosts: Path
    host_key_line: str
    tmp: Path


@asynccontextmanager
async def ssh_env(tmp_path: Path):
    """Server SSH su loopback con chiave host e chiave client generate al volo."""
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    host_key_path = tmp_path / "host_key"
    host_key.write_private_key(str(host_key_path))

    client_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key_path = tmp_path / "id_ed25519"
    client_key.write_private_key(str(client_key_path))
    os.chmod(client_key_path, 0o600)

    authorized = tmp_path / "authorized_keys"
    authorized.write_bytes(client_key.export_public_key("openssh"))

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[str(host_key_path)],
        authorized_client_keys=str(authorized),
        process_factory=_handle_process,
        sftp_factory=True,
    )
    port = server.sockets[0].getsockname()[1]

    host_key_line = " ".join(host_key.export_public_key("openssh").decode().split()[:2])
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_key_line}\n")

    backend = DevSshBackend()
    target = SshTarget(
        host="127.0.0.1",
        port=port,
        username=TEST_USER,
        key_path=client_key_path,
        known_hosts_path=known_hosts,
        connect_timeout_s=5.0,
        keepalive_interval_s=0,
    )
    try:
        yield _Env(
            backend=backend,
            target=target,
            port=port,
            key_path=client_key_path,
            known_hosts=known_hosts,
            host_key_line=host_key_line,
            tmp=tmp_path,
        )
    finally:
        await backend.close_all()
        server.close()
        await server.wait_closed()


# -- esecuzione comandi ------------------------------------------------------

async def test_exec_returns_stdout_and_zero_exit(tmp_path):
    async with ssh_env(tmp_path) as env:
        result = await env.backend.exec(
            env.target, "echo ciao", timeout_s=5, max_output_chars=1000
        )
    assert result.exit_code == 0
    assert result.stdout.strip() == "ciao"
    assert result.truncated_chars == 0


async def test_exec_reports_nonzero_exit_without_raising(tmp_path):
    """Un comando fallito è un RISULTATO, non un'eccezione.

    L'agente deve poter leggere exit code e stderr e decidere; sollevare
    renderebbe indistinguibile "il comando è andato male" da "la connessione è
    caduta", che richiedono reazioni opposte.
    """
    async with ssh_env(tmp_path) as env:
        result = await env.backend.exec(
            env.target, "fail", timeout_s=5, max_output_chars=1000
        )
    assert result.exit_code == 3
    assert "something broke" in result.stderr


async def test_exec_truncates_and_counts_discarded_chars(tmp_path):
    async with ssh_env(tmp_path) as env:
        result = await env.backend.exec(
            env.target, "big 5000", timeout_s=5, max_output_chars=100
        )
    assert len(result.stdout) == 100
    assert result.truncated_chars == 4900


async def test_exec_times_out(tmp_path):
    async with ssh_env(tmp_path) as env:
        with pytest.raises(SshTimeoutError):
            await env.backend.exec(
                env.target, "sleep 5", timeout_s=0.5, max_output_chars=1000
            )


# -- sicurezza ---------------------------------------------------------------

async def test_unknown_host_key_is_refused(tmp_path):
    """Nessun TOFU: un host non presente in known_hosts non si contatta."""
    async with ssh_env(tmp_path) as env:
        empty = tmp_path / "empty_known_hosts"
        empty.write_text("")
        blind = SshTarget(
            host="127.0.0.1",
            port=env.port,
            username=TEST_USER,
            key_path=env.key_path,
            known_hosts_path=empty,
            connect_timeout_s=5.0,
        )
        with pytest.raises(SshHostKeyError):
            await env.backend.exec(blind, "echo ciao", timeout_s=5, max_output_chars=1000)


async def test_wrong_client_key_is_rejected(tmp_path):
    async with ssh_env(tmp_path) as env:
        stranger = asyncssh.generate_private_key("ssh-ed25519")
        stranger_path = tmp_path / "stranger"
        stranger.write_private_key(str(stranger_path))
        os.chmod(stranger_path, 0o600)

        bad = SshTarget(
            host="127.0.0.1",
            port=env.port,
            username=TEST_USER,
            key_path=stranger_path,
            known_hosts_path=env.known_hosts,
            connect_timeout_s=5.0,
        )
        with pytest.raises(SshAuthError):
            await env.backend.exec(bad, "echo ciao", timeout_s=5, max_output_chars=1000)


# -- trasferimenti -----------------------------------------------------------

async def test_sftp_put_then_get_roundtrip(tmp_path):
    async with ssh_env(tmp_path) as env:
        source = tmp_path / "up.txt"
        source.write_text("contenuto da caricare")
        remote = str(tmp_path / "remote_copy.txt")

        written = await env.backend.put(env.target, source, remote)
        assert written == source.stat().st_size
        assert Path(remote).read_text() == "contenuto da caricare"

        back = tmp_path / "down.txt"
        read = await env.backend.get(env.target, remote, back, max_bytes=1_000_000)
        assert back.read_text() == "contenuto da caricare"
        assert read == source.stat().st_size


async def test_get_refuses_file_over_cap_without_writing_it(tmp_path):
    """Il cap si applica PRIMA di scrivere: niente file troncato sul telefono."""
    async with ssh_env(tmp_path) as env:
        remote = tmp_path / "huge.bin"
        remote.write_bytes(b"y" * 5000)
        destination = tmp_path / "should_not_exist.bin"

        with pytest.raises(SshTransportError, match="over the"):
            await env.backend.get(
                env.target, str(remote), destination, max_bytes=1000
            )
        assert not destination.exists()


# -- chiavi e host key -------------------------------------------------------

async def test_generate_key_pair_writes_private_0600_and_returns_public(tmp_path):
    backend = DevSshBackend()
    key_path = tmp_path / "generated" / "id_ed25519"
    public = await backend.generate_key_pair(key_path)

    assert public.startswith("ssh-ed25519 ")
    assert key_path.exists()
    assert oct(key_path.stat().st_mode)[-3:] == "600"
    # La privata non deve comparire in cio che il metodo restituisce.
    assert "PRIVATE KEY" not in public


async def test_probe_host_key_returns_usable_known_hosts_line(tmp_path):
    async with ssh_env(tmp_path) as env:
        line, fingerprint = await env.backend.probe_host_key("127.0.0.1", env.port)

    assert line == f"[127.0.0.1]:{env.port} {env.host_key_line}"
    assert fingerprint.startswith("SHA256:")


def test_known_hosts_entry_brackets_only_nonstandard_ports():
    """Su porta 22 il nome host va nudo, altrimenti la riga non verra mai trovata."""
    assert _known_hosts_entry("example.com", 22, "ssh-ed25519 AAAA") == (
        "example.com ssh-ed25519 AAAA"
    )
    assert _known_hosts_entry("example.com", 2222, "ssh-ed25519 AAAA") == (
        "[example.com]:2222 ssh-ed25519 AAAA"
    )


# -- pool --------------------------------------------------------------------

async def test_connection_is_reused_across_commands(tmp_path):
    async with ssh_env(tmp_path) as env:
        await env.backend.exec(env.target, "echo uno", timeout_s=5, max_output_chars=1000)
        first = env.backend._connections[env.target.pool_key]
        await env.backend.exec(env.target, "echo due", timeout_s=5, max_output_chars=1000)
        assert env.backend._connections[env.target.pool_key] is first


async def test_close_all_drops_every_connection(tmp_path):
    async with ssh_env(tmp_path) as env:
        await env.backend.exec(env.target, "echo ciao", timeout_s=5, max_output_chars=1000)
        assert env.backend._connections

        await env.backend.close_all()
        assert not env.backend._connections


def test_pool_key_changes_with_connection_params(tmp_path):
    """Cambiare porta o utente deve invalidare la sessione, non riusarla."""
    base = SshTarget(
        host="example.com",
        port=22,
        username="jenny",
        key_path=tmp_path / "id",
        known_hosts_path=tmp_path / "known_hosts",
    )
    other_port = SshTarget(
        host=base.host,
        port=2222,
        username=base.username,
        key_path=base.key_path,
        known_hosts_path=base.known_hosts_path,
    )
    other_user = SshTarget(
        host=base.host,
        port=base.port,
        username="altro",
        key_path=base.key_path,
        known_hosts_path=base.known_hosts_path,
    )
    assert base.pool_key != other_port.pool_key
    assert base.pool_key != other_user.pool_key


# -- chiusura per inattivita -------------------------------------------------


async def test_idle_connection_is_closed_without_further_calls(tmp_path):
    """Il potatore deve girare anche quando NESSUNO chiama piu nulla.

    E' proprio quello il caso che conta: col keepalive attivo una sessione
    inutilizzata resterebbe a pingare il server all'infinito dopo un singolo
    comando, e su un telefono quello e batteria e traffico. Una potatura pigra
    (solo alla chiamata successiva) non lo risolverebbe affatto.
    """
    async with ssh_env(tmp_path) as env:
        target = SshTarget(
            host=env.target.host,
            port=env.target.port,
            username=env.target.username,
            key_path=env.target.key_path,
            known_hosts_path=env.target.known_hosts_path,
            connect_timeout_s=5.0,
            keepalive_interval_s=0,
            idle_close_s=1,
        )
        await env.backend.exec(target, "echo ciao", timeout_s=5, max_output_chars=1000)
        assert target.pool_key in env.backend._connections

        await asyncio.sleep(2.5)
        assert target.pool_key not in env.backend._connections


async def test_idle_reaping_can_be_switched_off(tmp_path):
    async with ssh_env(tmp_path) as env:
        target = SshTarget(
            host=env.target.host,
            port=env.target.port,
            username=env.target.username,
            key_path=env.target.key_path,
            known_hosts_path=env.target.known_hosts_path,
            connect_timeout_s=5.0,
            keepalive_interval_s=0,
            idle_close_s=0,
        )
        await env.backend.exec(target, "echo ciao", timeout_s=5, max_output_chars=1000)
        assert env.backend._reaper is None
        await asyncio.sleep(1.5)
        assert target.pool_key in env.backend._connections


async def test_close_all_stops_the_reaper_task(tmp_path):
    """Un task lasciato pendente fa gridare asyncio alla chiusura del loop."""
    async with ssh_env(tmp_path) as env:
        target = SshTarget(
            host=env.target.host,
            port=env.target.port,
            username=env.target.username,
            key_path=env.target.key_path,
            known_hosts_path=env.target.known_hosts_path,
            connect_timeout_s=5.0,
            keepalive_interval_s=0,
            idle_close_s=60,
        )
        await env.backend.exec(target, "echo ciao", timeout_s=5, max_output_chars=1000)
        reaper = env.backend._reaper
        assert reaper is not None

        await env.backend.close_all()
        assert reaper.cancelled() or reaper.done()
        assert env.backend._reaper is None
