"""Backend SSH nativo per Android: jsch, via bridge Chaquopy.

Questo modulo è deliberatamente sottile: tutta la parte che *può* andare storta
in modo interessante — pool di sessioni, troncamento dell'output, cap sui
download, lettura della host key — vive in Kotlin (``SshBridge.kt``), perché è
lì che stanno le API di jsch. Qui si fa una cosa sola, ma la si fa bene:
tradurre fra il mondo asincrono di Jenny e una chiamata bloccante che
attraversa il confine JNI, e riportare le categorie d'errore del bridge sulle
eccezioni del contratto (``ssh_backends/base.py``).

Confine, per convenzione con gli altri bridge (``AgenticSearchBridge``):
**JSON string in, JSON string out**. Nessun tipo complesso lo attraversa, e in
particolare nessun array di byte: SFTP riceve dei *path* e apre i file da sé.

Il comportamento osservabile deve coincidere con quello del backend dev
(``ssh_backends/dev.py``), che è coperto dai test contro un server SSH vero:
troncamento con conteggio dei caratteri scartati, exit code diverso da zero
come risultato e non come eccezione, cap sui download verificato prima di
scrivere. La parità di *quel* comportamento è il motivo per cui esistono due
implementazioni dietro un unico Protocol.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from jenny.agent.tools.ssh_backends.base import (
    SshAuthError,
    SshError,
    SshExecResult,
    SshHostKeyError,
    SshTarget,
    SshTimeoutError,
    SshTransportError,
)

_BRIDGE_CLASS = "com.flagdizero.jenny.SshBridge"

# Le categorie che il bridge sa produrre. Non è un dettaglio di comodità: una
# host key non pinnata richiede che un umano guardi l'impronta, un timeout no,
# e l'agente reagisce alle due cose in modo opposto. Una categoria sconosciuta
# (bridge più nuovo del Python) ricade sull'errore di trasporto, che è il più
# innocuo dei quattro.
_ERROR_BY_CATEGORY: dict[str, type[SshError]] = {
    "host_key": SshHostKeyError,
    "auth": SshAuthError,
    "timeout": SshTimeoutError,
    "io": SshTransportError,
}

# Margine fra il timeout applicato in Kotlin e il backstop lato asyncio. Il
# secondo esiste perché un bridge piantato non deve poter appendere per sempre
# la coroutine: deve scattare *dopo* quello nativo, altrimenti si perderebbe
# l'errore preciso che il bridge stava per restituire.
_BACKSTOP_MARGIN_S = 10.0

# I trasferimenti non hanno un timeout nel contratto (né lo ha il backend dev:
# si affida al TCP). Serve comunque un tetto, o una sessione SFTP morta
# resterebbe appesa a vita: è alto perché il cap sui file è di decine di MB e
# la rete di un telefono può essere lenta.
_TRANSFER_TIMEOUT_S = 600.0

# Generazione della chiave e probe della host key: operazioni brevi, ma la
# prima è CPU-bound e la seconda apre una connessione.
_KEYGEN_TIMEOUT_S = 60.0

_bridge: Any = None


def _get_bridge() -> Any:
    """Risolve (una volta) la classe Kotlin ``SshBridge`` via Chaquopy."""
    global _bridge
    if _bridge is None:
        from java import jclass  # importabile solo sotto il runtime Chaquopy

        _bridge = jclass(_BRIDGE_CLASS)
    return _bridge


def _target_payload(target: SshTarget) -> dict[str, Any]:
    """Parametri di connessione, passati a ogni chiamata.

    Il pool vive in Kotlin ma la sua *chiave* la calcola Python
    (``SshTarget.pool_key``): così basta cambiare un parametro in Settings
    perché la sessione aperta venga scartata invece che riusata verso il
    vecchio target.

    La password, quando c'è, viaggia qui dentro. Due conseguenze, entrambe
    rispettate: la chiave ``password`` si **omette** quando non serve invece di
    mandare ``null`` (``JSONObject.optString`` su un null di org.json restituisce
    la stringa ``"null"``, che jsch userebbe come password), e questo payload non
    va mai loggato — né qui né in Kotlin.
    """
    payload: dict[str, Any] = {
        "poolKey": target.pool_key,
        "host": target.host,
        "port": target.port,
        "username": target.username,
        "keyPath": str(target.key_path),
        "knownHostsPath": str(target.known_hosts_path),
        "connectTimeoutS": target.connect_timeout_s,
        "keepaliveIntervalS": target.keepalive_interval_s,
        # Il potatore delle sessioni inattive vive in Kotlin, dove vive il pool.
        # Senza, il keepalive terrebbe viva per sempre una sessione inutilizzata
        # dopo un singolo comando: su un telefono sono batteria e dati.
        "idleCloseS": target.idle_close_s,
    }
    if target.password is not None:
        payload["password"] = target.password
    return payload


async def _call(method: str, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
    """Chiama un metodo del bridge e ne decodifica la risposta JSON.

    La chiamata è bloccante e va su un thread; il ``wait_for`` è il backstop
    indipendente dai timeout nativi descritto sopra.
    """
    bridge = _get_bridge()
    request = json.dumps(payload)
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(getattr(bridge, method), request),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise SshTimeoutError(
            f"the native ssh bridge did not answer {method}() within {timeout_s:.0f}s"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - le eccezioni Java arrivano wrappate
        raise SshTransportError(f"ssh bridge {method}() failed: {exc}") from exc

    try:
        data = json.loads(str(raw))
    except ValueError as exc:
        raise SshTransportError(f"ssh bridge {method}() returned malformed JSON") from exc
    if not isinstance(data, dict):
        raise SshTransportError(f"ssh bridge {method}() returned {type(data).__name__}, not an object")

    error = data.get("error")
    if error is not None:
        category = str(data.get("category", "io"))
        raise _ERROR_BY_CATEGORY.get(category, SshTransportError)(str(error))
    return data


class AndroidSshBackend:
    """Implementazione di :class:`SshBackend` su jsch."""

    async def exec(
        self,
        target: SshTarget,
        command: str,
        *,
        timeout_s: float,
        max_output_chars: int,
    ) -> SshExecResult:
        payload = _target_payload(target)
        payload.update(
            command=command,
            timeoutS=timeout_s,
            # Il taglio avviene in Kotlin: una stringa da megabyte non deve
            # attraversare il confine JNI solo per essere buttata via qui.
            maxOutputChars=max_output_chars,
        )
        data = await _call(
            "exec",
            payload,
            timeout_s=timeout_s + target.connect_timeout_s + _BACKSTOP_MARGIN_S,
        )
        return SshExecResult(
            # -1 = ucciso da un segnale, come nel backend dev.
            exit_code=int(data.get("exitCode", -1)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            truncated_chars=int(data.get("truncatedChars", 0)),
        )

    async def put(self, target: SshTarget, local: Path, remote: str) -> int:
        payload = _target_payload(target)
        payload.update(localPath=str(local), remotePath=remote)
        data = await _call("put", payload, timeout_s=_TRANSFER_TIMEOUT_S)
        return int(data.get("bytes", 0))

    async def get(
        self, target: SshTarget, remote: str, local: Path, *, max_bytes: int
    ) -> int:
        payload = _target_payload(target)
        # Il cap viaggia con la richiesta perché il confronto con la dimensione
        # remota va fatto dove si apre il file, PRIMA di scrivere: applicarlo
        # qui, a trasferimento finito, lascerebbe sul telefono un file troncato
        # a metà e indistinguibile da uno buono.
        payload.update(remotePath=remote, localPath=str(local), maxBytes=max_bytes)
        data = await _call("get", payload, timeout_s=_TRANSFER_TIMEOUT_S)
        return int(data.get("bytes", 0))

    async def generate_key_pair(self, key_path: Path) -> str:
        data = await _call(
            "generateKeyPair",
            {"keyPath": str(key_path)},
            timeout_s=_KEYGEN_TIMEOUT_S,
        )
        public = str(data.get("publicKey", "")).strip()
        if not public:
            raise SshTransportError("the ssh bridge generated no public key")
        return public

    async def probe_host_key(self, host: str, port: int) -> tuple[str, str]:
        data = await _call(
            "probeHostKey",
            {"host": host, "port": port, "connectTimeoutS": 15.0},
            timeout_s=_KEYGEN_TIMEOUT_S,
        )
        line = str(data.get("line", "")).strip()
        fingerprint = str(data.get("fingerprint", "")).strip()
        if not line or not fingerprint:
            raise SshTransportError(f"{host}:{port} offered no usable host key")
        return line, fingerprint

    async def close_all(self) -> None:
        await _call("closeAll", {}, timeout_s=30.0)
