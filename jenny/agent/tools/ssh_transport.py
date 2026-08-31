"""Ponte fra la config e i backend SSH.

Qui vive tutto ciò che i backend non devono sapere: gli alias, la policy di
rete, dove stanno chiave e ``known_hosts``, e il rifiuto di parlare con un host
la cui impronta non è stata accettata da un umano. Un backend riceve un
:class:`SshTarget` già risolto e si limita a trasportare.

La divisione non è estetica: è ciò che rende il comportamento *autorizzativo*
identico su device e su Mac, perché sta tutto in questo file, che gira in
entrambi i posti ed è coperto dai test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from jenny.agent.tools.ssh_backends.base import (
    SshBackend,
    SshError,
    SshHostKeyError,
    SshTarget,
    known_hosts_name,
)
from jenny.config.paths import get_ssh_dir
from jenny.config.tool_schemas import SshHostConfig
from jenny.security.network import validate_ssh_target
from jenny.utils.path import atomic_write


class SshHostUnknownError(SshError):
    """Alias non registrato.

    Il messaggio elenca gli alias validi perché il chiamante tipico è un LLM
    che se l'è inventato: un errore senza l'elenco lo fa solo indovinare di
    nuovo, bruciando un turno.
    """

    def __init__(self, alias: object, known: Iterable[str]) -> None:
        self.alias = alias
        self.known = list(known)
        listed = ", ".join(self.known) if self.known else "none configured"
        super().__init__(f"unknown ssh host {alias!r}: configured hosts are {listed}")


class SshHostBlockedError(SshError):
    """L'host risolve a un indirizzo che la policy di rete non permette."""


class SshKeyMissingError(SshError):
    """Manca la chiave privata per questo alias: va generata da Settings."""


class SshPasswordMissingError(SshKeyMissingError):
    """Host dichiarato ``auth="password"`` ma senza password in config.

    Sottoclasse di :class:`SshKeyMissingError` perché per chi la cattura è
    esattamente la stessa situazione: manca la credenziale locale, la mette un
    umano in Settings, e riprovare non serve a nulla. Ereditarne la categoria fa
    sì che ``ssh.py::_describe`` passi già il messaggio così com'è al modello,
    invece di prefissarlo con la diagnosi generica "SSH failed" — che manderebbe
    l'agente a indagare sulla rete. (Il nome giusto della coppia sarebbe
    ``SshCredentialMissingError``; rinominare tocca ``ssh.py``.)
    """


class SshDisabledError(SshError):
    """SSH è spento in Settings.

    Controllato a ogni chiamata, non solo allo startup, e l'asimmetria è voluta:
    ``Tool.enabled()`` decide una volta sola se questi tool esistono, quindi
    *riaccendere* SSH richiede un riavvio del gateway — ma *spegnerlo* è
    l'interruttore di emergenza e deve avere effetto immediato. Chi toglie la
    spunta mentre un subagent sta lavorando su un server si aspetta che smetta.
    """


# Il backend è un SINGLETON, a differenza di quello crypto che si costruisce a
# ogni chiamata: qui dentro ci vive il pool di connessioni, e ricostruirlo
# ogni volta significherebbe riaprire una sessione SSH per ogni comando.
_backend: SshBackend | None = None


def get_ssh_backend() -> SshBackend:
    """Backend SSH per l'ambiente corrente (jsch su Android, asyncssh altrove)."""
    global _backend
    if _backend is not None:
        return _backend

    from jenny.runtime.context import get_android_context

    if get_android_context() is not None:
        from jenny.agent.tools.ssh_backends.android import AndroidSshBackend

        _backend = AndroidSshBackend()
    else:
        from jenny.agent.tools.ssh_backends.dev import DevSshBackend

        _backend = DevSshBackend()
    return _backend


def reset_ssh_backend() -> None:
    """Scorda il backend cachato.

    Chiamata allo startup del gateway insieme al reset degli altri bridge: un
    loop asyncio morto lascerebbe nel pool connessioni legate a un event loop
    che non esiste più, e ogni comando successivo fallirebbe.
    """
    global _backend
    _backend = None


# -- percorsi ----------------------------------------------------------------


def known_hosts_path() -> Path:
    return get_ssh_dir() / "known_hosts"


def ssh_key_path(alias: str) -> Path:
    """Chiave privata di un alias. Il nome è derivato, mai preso dalla config.

    Un ``key_file`` configurabile sarebbe un path scelto dall'utente (o, peggio,
    riflesso da un alias) che il backend poi apre: derivarlo qui rende
    impossibile puntare la lettura fuori dalla directory SSH.
    """
    safe = "".join(c for c in alias if c.isalnum() or c in "-_") or "host"
    return get_ssh_dir() / f"{safe}_ed25519"


# -- known_hosts -------------------------------------------------------------


def _known_hosts_lines() -> list[str]:
    path = known_hosts_path()
    if not path.exists():
        return []
    return [line for line in path.read_text("utf-8").splitlines() if line.strip()]


def pinned_host_key(host: str, port: int) -> str | None:
    """Riga registrata per questo host, o ``None`` se non è mai stata accettata."""
    name = known_hosts_name(host, port)
    for line in _known_hosts_lines():
        if line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if parts and parts[0] == name:
            return line
    return None


def is_host_pinned(host: str, port: int) -> bool:
    return pinned_host_key(host, port) is not None


def record_host_key(line: str, *, replace: bool = False) -> None:
    """Registra una riga ``known_hosts``, atomicamente.

    Se esiste già una riga per lo stesso host con una chiave **diversa** questa
    solleva invece di sovrascrivere: una host key cambiata è un potenziale MITM,
    e la sola risposta accettabile è che un umano guardi l'impronta e decida.
    ``replace=True`` è quella decisione, presa esplicitamente da Settings.
    """
    parts = line.split()
    if len(parts) < 2:
        raise ValueError(f"malformed known_hosts line: {line!r}")
    name = parts[0]

    kept: list[str] = []
    for existing in _known_hosts_lines():
        fields = existing.split()
        if fields and fields[0] == name and not existing.lstrip().startswith("#"):
            if existing.strip() == line.strip():
                return  # già registrata, idempotente
            if not replace:
                raise SshHostKeyError(
                    f"host key for {name} is already pinned to a different key; "
                    "accept the new fingerprint explicitly to replace it"
                )
            continue  # replace=True: la vecchia riga cade
        kept.append(existing)
    kept.append(line.strip())
    _write_known_hosts(kept)


def forget_host(host: str, port: int) -> None:
    """Rimuove ogni riga registrata per questo host (usata alla cancellazione)."""
    name = known_hosts_name(host, port)
    kept = [
        line
        for line in _known_hosts_lines()
        if not (line.split() and line.split()[0] == name)
    ]
    _write_known_hosts(kept)


def _write_known_hosts(lines: list[str]) -> None:
    """Riscrive ``known_hosts``, atomicamente, 0600 e con fsync.

    Passa da ``atomic_write`` e non da un temp+replace a mano: quella copia non
    faceva fsync, quindi era atomica contro un processo ucciso ma non contro un
    calo di corrente — e qui una riga persa toglie in silenzio il pinning di una
    host key, che è il contrario di quello che questo file serve a fare.
    """
    atomic_write(
        known_hosts_path(),
        "".join(f"{line}\n" for line in lines),
        chmod=0o600,
    )


# -- risoluzione degli alias -------------------------------------------------


def configured_hosts(config: Any = None) -> list[SshHostConfig]:
    """Host registrati, letti da config **fresca**.

    I tool non vengono ricostruiti quando cambiano le impostazioni (il gateway
    ricarica solo provider e modello), quindi rileggere qui è l'unico modo per
    cui un host aggiunto dalla UI funziona senza riavviare.
    """
    if config is None:
        from jenny.config.loader import load_config

        config = load_config()
    ssh = getattr(getattr(config, "tools", None), "ssh", None)
    if ssh is None or not ssh.enable:
        return []
    return list(ssh.hosts)


def resolve_target(
    alias: str,
    *,
    config: Any = None,
    validate: Callable[[str], tuple[bool, str]] | None = None,
) -> tuple[SshHostConfig, SshTarget]:
    """Alias → ``(config dell'host, SshTarget)``, applicando tutti i gate.

    ``validate`` è iniettabile perché la policy di rete blocca il loopback —
    correttamente, in produzione — mentre i test parlano con un server su
    127.0.0.1. Iniettarlo è preferibile a un'eccezione per i test dentro la
    policy, che indebolirebbe la cosa vera per comodità di quella finta.
    """
    if config is None:
        from jenny.config.loader import load_config

        config = load_config()

    hosts = configured_hosts(config)
    host_cfg = next((h for h in hosts if h.alias == alias), None)
    if host_cfg is None:
        raise SshHostUnknownError(alias, [h.alias for h in hosts])

    check = validate if validate is not None else validate_ssh_target
    ok, error = check(host_cfg.host)
    if not ok:
        raise SshHostBlockedError(f"ssh to {alias!r} refused: {error}")

    # Il pinning si controlla PRIMA della credenziale e per OGNI modo di
    # autenticazione, senza rami che lo saltino. Con la password è il controllo
    # che conta di più, non di meno: una chiave privata non lascia il telefono
    # nemmeno parlando con l'host sbagliato, mentre la password verrebbe
    # consegnata in chiaro a chiunque risponda a quell'indirizzo — e da lì è
    # riusabile su sudo, sulla webmail, ovunque l'utente l'abbia riciclata.
    if not is_host_pinned(host_cfg.host, host_cfg.port):
        raise SshHostKeyError(
            f"the host key for {alias!r} has not been accepted yet — open "
            "Settings > SSH, check the fingerprint and accept it. This is not "
            "something you can work around from here."
        )

    # ``key_path`` è derivato dall'alias e viene valorizzato comunque (entra
    # nella chiave del pool); per un host a password il backend non lo apre.
    key_path = ssh_key_path(alias)
    password: str | None = None
    if host_cfg.auth == "password":
        # Una stringa vuota è "non impostata", non "password vuota": la seconda
        # non esiste come credenziale valida e lasciarla passare produrrebbe un
        # rifiuto del server invece di un'istruzione per l'utente.
        password = host_cfg.password or None
        if password is None:
            raise SshPasswordMissingError(
                f"no password is set for {alias!r} — open Settings > SSH and set it "
                "for that host. It cannot be set from here, and nobody will show it "
                "to you"
            )
    elif not key_path.exists():
        raise SshKeyMissingError(
            f"no private key for {alias!r} — generate one in Settings > SSH and "
            "install the public key on the server"
        )

    ssh_cfg = config.tools.ssh
    return host_cfg, SshTarget(
        host=host_cfg.host,
        port=host_cfg.port,
        username=host_cfg.username,
        key_path=key_path,
        password=password,
        known_hosts_path=known_hosts_path(),
        connect_timeout_s=ssh_cfg.connect_timeout_s,
        keepalive_interval_s=ssh_cfg.keepalive_interval_s,
        idle_close_s=ssh_cfg.idle_close_s,
    )
