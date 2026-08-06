"""Contratto comune dei backend SSH.

Il backend è deliberatamente **stupido**: riceve un :class:`SshTarget` già
risolto e non legge mai la config. Tutto ciò che riguarda gli alias, la policy
di rete e i percorsi vive sopra, in ``ssh_transport``. Così l'unica differenza
fra device e test è *come* si parla col server, non *cosa* si è autorizzati a
fare — che è la parte che deve restare identica e testabile sul Mac.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SshError(Exception):
    """Radice degli errori SSH: permette un solo except a chi non deve distinguere."""


class SshUnavailableError(SshError):
    """Nessun backend SSH utilizzabile in questo ambiente."""


class SshHostKeyError(SshError):
    """Host key assente da ``known_hosts`` o diversa da quella registrata.

    Volutamente distinta dalle altre: è l'unico errore la cui risposta corretta
    non è "riprova" ma "un umano deve accettare l'impronta in Settings", e il
    messaggio che l'agente riceve deve dirlo. Una host key cambiata su un host
    già registrato è invece un potenziale MITM, non un intoppo.
    """


class SshAuthError(SshError):
    """Il server ha rifiutato la chiave (pubblica non installata, utente errato)."""


class SshTimeoutError(SshError):
    """Timeout di connessione o di comando."""


class SshTransportError(SshError):
    """Errore di rete o di I/O: host irraggiungibile, connessione caduta, SFTP fallito."""


def known_hosts_name(host: str, port: int) -> str:
    """Nome host come va scritto in ``known_hosts``.

    Una porta non standard cambia il formato in ``[host]:port``. Scrivere
    l'host nudo produce una riga che non verrà mai trovata, e l'effetto non è
    un errore leggibile ma un rifiuto per "host key sconosciuta" a ogni
    tentativo — con l'impronta già accettata dall'utente, che è il modo più
    rapido di far perdere fiducia nel pinning.

    Sta qui e non in un backend perché è il formato del file su disco, che i
    due backend condividono: jsch e asyncssh leggono lo stesso ``known_hosts``.
    """
    return host if port == 22 else f"[{host}]:{port}"


@dataclass(frozen=True, slots=True)
class SshTarget:
    """Tutto ciò che serve per aprire una connessione, già risolto.

    Non contiene l'alias per un motivo preciso: l'alias è la chiave di
    indicizzazione del pool e appartiene al livello sopra. Un backend che
    conoscesse gli alias sarebbe tentato di rileggersi la config.
    """

    host: str
    port: int
    username: str
    # Chiave privata e known_hosts vivono fuori dal workspace: vedi
    # ``jenny.config.paths.get_ssh_dir``.
    key_path: Path
    known_hosts_path: Path
    connect_timeout_s: float = 15.0
    # 0 = keepalive disattivato.
    keepalive_interval_s: int = 30
    # Dopo quanti secondi di inattività la connessione va chiusa. Non è una
    # micro-ottimizzazione: col keepalive attivo una sessione inutilizzata resta
    # a pingare il server per sempre dopo un singolo comando, e su un telefono
    # quello è batteria e traffico. 0 disattiva la chiusura per inattività.
    idle_close_s: int = 300

    @property
    def pool_key(self) -> str:
        """Impronta dei parametri di connessione.

        Il pool va invalidato quando cambiano, non solo quando cambia l'alias:
        se l'utente corregge la porta o l'utente in Settings, riusare la
        sessione aperta significherebbe parlare ancora col vecchio target.
        """
        return f"{self.username}@{self.host}:{self.port}#{self.key_path}"


@dataclass(frozen=True, slots=True)
class SshExecResult:
    """Esito di un comando remoto.

    ``truncated_chars`` è il numero di caratteri *scartati*, non la lunghezza
    finale: serve a dire all'agente quanto si sta perdendo, così può decidere se
    rilanciare il comando restringendo l'output invece di indovinare.
    """

    exit_code: int
    stdout: str
    stderr: str
    truncated_chars: int = 0


class SshBackend(Protocol):
    """Trasporto SSH: comandi, trasferimenti, chiavi.

    Nessun metodo interattivo (niente PTY, niente stdin): un comando che si
    blocca su un prompt deve scadere, non restare appeso. I comandi lunghi non
    passano da qui ma da ``ssh_job``, che li stacca dalla connessione.
    """

    async def exec(
        self,
        target: SshTarget,
        command: str,
        *,
        timeout_s: float,
        max_output_chars: int,
    ) -> SshExecResult:
        """Esegue ``command`` e ne raccoglie l'output, troncato a ``max_output_chars``."""
        ...

    async def put(self, target: SshTarget, local: Path, remote: str) -> int:
        """Carica ``local`` su ``remote`` via SFTP. Ritorna i byte trasferiti."""
        ...

    async def get(
        self, target: SshTarget, remote: str, local: Path, *, max_bytes: int
    ) -> int:
        """Scarica ``remote`` in ``local`` via SFTP. Ritorna i byte trasferiti.

        La dimensione va verificata **prima** di iniziare: un cap applicato
        mentre si scrive lascerebbe sul telefono un file troncato a metà.
        """
        ...

    async def generate_key_pair(self, key_path: Path) -> str:
        """Genera una coppia ed25519 in ``key_path`` (privata a 0600).

        Ritorna **solo la chiave pubblica** in formato OpenSSH, da incollare in
        ``authorized_keys``: la privata non lascia mai il telefono, e non passa
        né dai risultati dei tool né dalle API della WebUI.
        """
        ...

    async def probe_host_key(self, host: str, port: int) -> tuple[str, str]:
        """Legge la host key senza autenticarsi né eseguire nulla.

        Ritorna ``(riga_known_hosts, impronta_sha256)``. È il passo che precede
        l'accettazione manuale dell'impronta da parte dell'utente.
        """
        ...

    async def close_all(self) -> None:
        """Chiude ogni connessione aperta. Chiamata dallo shutdown del gateway."""
        ...
