"""Backend SSH per desktop/test basato su ``asyncssh``.

``asyncssh`` NON è una dipendenza runtime: su Android il client SSH è jsch, via
bridge nativo. Qui serve per far girare la suite sul Mac contro un server SSH
vero in-process, e come riferimento di comportamento per il bridge. L'import è
lazy così questo modulo si può importare ovunque senza errori — e un import a
livello modulo farebbe esplodere l'app al primo avvio sul device.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.tools.ssh_backends.base import (
    SshAuthError,
    SshExecResult,
    SshHostKeyError,
    SshTarget,
    SshTimeoutError,
    SshTransportError,
    SshUnavailableError,
    known_hosts_name,
)


def _load_asyncssh() -> Any:
    try:
        import asyncssh
    except ImportError as exc:  # pragma: no cover - dipende dall'ambiente
        raise SshUnavailableError(
            "the 'asyncssh' package is required for SSH outside Android "
            "(pip install asyncssh) — on device the native jsch bridge is used"
        ) from exc
    return asyncssh


def _truncate(text: str, limit: int) -> tuple[str, int]:
    """Tronca a ``limit`` caratteri, ritornando anche quanti se ne sono persi."""
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit


def _known_hosts_entry(host: str, port: int, key_line: str) -> str:
    """Riga ``known_hosts`` completa per un host."""
    return f"{known_hosts_name(host, port)} {key_line}".strip()


class DevSshBackend:
    """Implementazione di :class:`SshBackend` su asyncssh."""

    def __init__(self) -> None:
        self._connections: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Ultimo uso per chiave di pool, e il task che pota le inattive.
        self._last_used: dict[str, float] = {}
        self._reaper: asyncio.Task[None] | None = None

    # -- connessioni ---------------------------------------------------------

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _connect(self, target: SshTarget) -> Any:
        asyncssh = _load_asyncssh()
        password = target.password
        try:
            return await asyncssh.connect(
                target.host,
                port=target.port,
                username=target.username,
                # Con una password ``client_keys=[]`` NON è ridondante: la lista
                # di default fa cercare a asyncssh ``~/.ssh/id_*`` e tentare
                # publickey per primo, e su un server con ``MaxAuthTries`` basso
                # quei tentativi possono bruciare i turni prima che si arrivi
                # alla password. La lista vuota disattiva del tutto publickey.
                client_keys=[] if password is not None else [str(target.key_path)],
                password=password,
                # ``known_hosts`` è passato in ENTRAMBI i modi, e con la password
                # conta di più: senza impronta verificata la password verrebbe
                # consegnata a chiunque risponda a quell'indirizzo. Una chiave
                # rubata da un MITM non è comunque riusabile, una password sì.
                known_hosts=str(target.known_hosts_path),
                connect_timeout=target.connect_timeout_s,
                keepalive_interval=target.keepalive_interval_s,
                keepalive_count_max=3,
                # I quattro ``None`` espliciti NON sono difensivismo: senza di
                # essi asyncssh legge ``~/.ssh/{config,ca-bundle.crt,crt}`` e
                # parla con l'agent. Su Android ``~`` non è affidabile, e in
                # ogni caso la configurazione di questo client deve venire solo
                # dalla config di Jenny, mai da file di sistema.
                config=None,
                agent_path=None,
                x509_trusted_certs=None,
                x509_trusted_cert_paths=None,
            )
        except asyncssh.HostKeyNotVerifiable as exc:
            raise SshHostKeyError(str(exc)) from exc
        except asyncssh.PermissionDenied as exc:
            raise SshAuthError(str(exc)) from exc
        except asyncio.TimeoutError as exc:
            raise SshTimeoutError(
                f"connection to {target.host}:{target.port} timed out "
                f"after {target.connect_timeout_s}s"
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise SshTransportError(str(exc)) from exc

    def _touch(self, key: str) -> None:
        self._last_used[key] = time.monotonic()

    def _ensure_reaper(self, target: SshTarget) -> None:
        """Avvia il potatore delle connessioni inattive, se serve.

        Un solo task per backend, non uno per connessione: deve girare anche
        quando nessuno chiama più nulla — è proprio quello il caso in cui il
        keepalive terrebbe viva una sessione all'infinito.
        """
        if target.idle_close_s <= 0 or self._reaper is not None:
            return
        self._reaper = asyncio.create_task(self._reap_idle(target.idle_close_s))

    async def _reap_idle(self, idle_close_s: int) -> None:
        # Il tick è al più un minuto: la precisione non serve, la reattività
        # allo shutdown sì (un task che dorme cinque minuti ritarda la chiusura).
        tick = max(1.0, min(float(idle_close_s), 60.0))
        while True:
            await asyncio.sleep(tick)
            cutoff = time.monotonic() - idle_close_s
            for key, last in list(self._last_used.items()):
                if last > cutoff:
                    continue
                conn = self._connections.pop(key, None)
                self._last_used.pop(key, None)
                if conn is not None:
                    logger.debug("ssh: closing idle connection {}", key)
                    conn.close()

    async def _connection(self, target: SshTarget) -> Any:
        key = target.pool_key
        self._ensure_reaper(target)
        self._touch(key)
        async with self._lock_for(key):
            conn = self._connections.get(key)
            # Una connessione caduta resta nel dizionario finché non la si
            # interroga: ``is_closed()`` è il solo modo di distinguerla da una
            # viva, e senza questo controllo il primo comando dopo un cambio di
            # rete fallirebbe sempre.
            if conn is not None and not conn.is_closed():
                return conn
            if conn is not None:
                self._connections.pop(key, None)
            conn = await self._connect(target)
            self._connections[key] = conn
            return conn

    # -- API del contratto ---------------------------------------------------

    async def exec(
        self,
        target: SshTarget,
        command: str,
        *,
        timeout_s: float,
        max_output_chars: int,
    ) -> SshExecResult:
        asyncssh = _load_asyncssh()
        conn = await self._connection(target)
        try:
            result = await asyncio.wait_for(
                conn.run(command, check=False),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise SshTimeoutError(
                f"command timed out after {timeout_s}s: {command[:80]}"
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise SshTransportError(str(exc)) from exc

        stdout, lost_out = _truncate(str(result.stdout or ""), max_output_chars)
        stderr, lost_err = _truncate(str(result.stderr or ""), max_output_chars)
        return SshExecResult(
            # exit_status è None quando il comando è stato ucciso da un segnale:
            # -1 lo distingue da un vero exit 0 senza inventare un codice
            # plausibile.
            exit_code=result.exit_status if result.exit_status is not None else -1,
            stdout=stdout,
            stderr=stderr,
            truncated_chars=lost_out + lost_err,
        )

    async def put(self, target: SshTarget, local: Path, remote: str) -> int:
        asyncssh = _load_asyncssh()
        conn = await self._connection(target)
        size = local.stat().st_size
        try:
            async with conn.start_sftp_client() as sftp:
                await sftp.put(str(local), remote)
        except (OSError, asyncssh.Error) as exc:
            raise SshTransportError(str(exc)) from exc
        return size

    async def get(
        self, target: SshTarget, remote: str, local: Path, *, max_bytes: int
    ) -> int:
        asyncssh = _load_asyncssh()
        conn = await self._connection(target)
        try:
            async with conn.start_sftp_client() as sftp:
                # La dimensione si controlla PRIMA di scrivere: un cap applicato
                # durante il trasferimento lascerebbe sul telefono un file
                # troncato a metà, indistinguibile da uno buono.
                attrs = await sftp.stat(remote)
                size = int(attrs.size or 0)
                if size > max_bytes:
                    raise SshTransportError(
                        f"{remote} is {size} bytes, over the {max_bytes} byte limit"
                    )
                await sftp.get(remote, str(local))
        except SshTransportError:
            raise
        except (OSError, asyncssh.Error) as exc:
            raise SshTransportError(str(exc)) from exc
        return size

    async def generate_key_pair(self, key_path: Path) -> str:
        asyncssh = _load_asyncssh()

        def _write() -> str:
            key = asyncssh.generate_private_key("ssh-ed25519")
            key_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = key_path.with_suffix(key_path.suffix + ".tmp")
            key.write_private_key(str(tmp))
            # 0600 PRIMA della rename: fra write e chmod il file esisterebbe
            # con i permessi di default, e su un path condiviso sarebbe una
            # finestra di lettura per chiunque. È lo stesso motivo per cui
            # ``utils.path.atomic_write`` ha un argomento ``chmod=``.
            #
            # Qui però l'helper non si può usare, e non è una dimenticanza:
            # ``write_private_key`` scrive il file **da sé**, quindi non esiste
            # un ``content`` da passargli — usarlo vorrebbe dire rileggere la
            # chiave privata in memoria per riscriverla, che è peggio di così.
            os.chmod(tmp, 0o600)
            os.replace(tmp, key_path)
            return key.export_public_key("openssh").decode().strip()

        return await asyncio.to_thread(_write)

    async def probe_host_key(self, host: str, port: int) -> tuple[str, str]:
        asyncssh = _load_asyncssh()
        try:
            key = await asyncssh.get_server_host_key(host, port=port)
        except (OSError, asyncssh.Error) as exc:
            raise SshTransportError(str(exc)) from exc
        if key is None:
            raise SshTransportError(f"{host}:{port} offered no host key")
        # export_public_key include un commento finale che in known_hosts non ha
        # senso: si tiene solo "tipo base64".
        parts = key.export_public_key("openssh").decode().split()
        line = " ".join(parts[:2])
        return _known_hosts_entry(host, port, line), key.get_fingerprint()

    async def close_all(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            # Attende davvero la cancellazione: un task lasciato pendente fa
            # emettere a asyncio "Task was destroyed but it is pending" alla
            # chiusura del loop, che nei test è rumore e in produzione è un
            # avviso che nasconde quelli veri.
            with suppress(asyncio.CancelledError):
                await self._reaper
            self._reaper = None
        self._last_used.clear()
        connections = list(self._connections.values())
        self._connections.clear()
        for conn in connections:
            conn.close()
        for conn in connections:
            with_wait = getattr(conn, "wait_closed", None)
            if with_wait is not None:
                await with_wait()
