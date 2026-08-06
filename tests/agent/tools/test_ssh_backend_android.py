"""Il backend Android con un bridge jsch finto (schema di ``test_crypto_backend_android.py``).

Il bridge Kotlin non è testabile fuori dal device, quindi qui si verifica
esattamente il pezzo che vive in Python: che i parametri arrivino al confine
nella forma attesa, e che le categorie d'errore del bridge diventino le
eccezioni giuste del contratto. Il resto — troncamento vero, cap vero — è
misurato contro un server SSH reale in ``test_ssh_backend_dev.py``, e il bridge
deve riprodurlo: qui si può solo controllare che Python gli passi i limiti e
non ri-tronchi per conto suo.

NB sull'infrastruttura: niente fixture async generator. Con pytest 9 +
pytest-asyncio 1.4 quella combinazione si pianta invece di fallire.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from support.android import fake_java_module

import jenny.agent.tools.ssh_backends.android as android_mod
from jenny.agent.tools.ssh_backends.android import AndroidSshBackend
from jenny.agent.tools.ssh_backends.base import (
    SshAuthError,
    SshHostKeyError,
    SshTarget,
    SshTimeoutError,
    SshTransportError,
)

_METHODS = ("exec", "put", "get", "generateKeyPair", "probeHostKey", "closeAll")


class FakeSshBridge:
    """Sosia di ``com.flagdizero.jenny.SshBridge``: JSON in, JSON out."""

    def __init__(self) -> None:
        #: (metodo, payload decodificato) di ogni chiamata, in ordine.
        self.calls: list[tuple[str, dict[str, Any]]] = []
        #: metodo -> risposta (dict) o callable(payload) -> dict.
        self.responses: dict[str, Any] = {}
        #: eccezione sollevata al posto della chiamata (errore Java non gestito).
        self.raises: Exception | None = None
        #: secondi di attesa prima di rispondere (per provare il backstop).
        self.delay_s: float = 0.0

    def _handle(self, method: str, request: str) -> str:
        payload = json.loads(request)
        self.calls.append((method, payload))
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.raises is not None:
            raise self.raises
        response = self.responses.get(method, {})
        if callable(response):
            response = response(payload)
        return json.dumps(response)

    def payload_for(self, method: str) -> dict[str, Any]:
        return next(payload for name, payload in self.calls if name == method)


def _install(monkeypatch: pytest.MonkeyPatch) -> FakeSshBridge:
    bridge = FakeSshBridge()
    for name in _METHODS:
        setattr(bridge, name, lambda request, _n=name: bridge._handle(_n, request))
    fake_java_module(monkeypatch, {"com.flagdizero.jenny.SshBridge": bridge})
    # La classe risolta è cachata in un globale di modulo: va azzerata fra un
    # test e l'altro o il secondo test parlerebbe col bridge del primo.
    monkeypatch.setattr(android_mod, "_bridge", None)
    return bridge


@pytest.fixture()
def bridge(monkeypatch: pytest.MonkeyPatch) -> FakeSshBridge:
    return _install(monkeypatch)


def _target(tmp_path: Path, **overrides: Any) -> SshTarget:
    params: dict[str, Any] = {
        "host": "nas.example.com",
        "port": 2222,
        "username": "jenny",
        "key_path": tmp_path / "nas_ed25519",
        "known_hosts_path": tmp_path / "known_hosts",
        "connect_timeout_s": 5.0,
        "keepalive_interval_s": 30,
    }
    params.update(overrides)
    return SshTarget(**params)


# -- parametri al confine ----------------------------------------------------

async def test_exec_passes_connection_and_command_params(bridge, tmp_path):
    """Il bridge riceve i parametri di connessione, il comando e i due limiti."""
    bridge.responses["exec"] = {"exitCode": 0, "stdout": "ok", "stderr": ""}
    target = _target(tmp_path)

    await AndroidSshBackend().exec(
        target, "uptime", timeout_s=30.0, max_output_chars=10_000
    )

    payload = bridge.payload_for("exec")
    assert payload == {
        "poolKey": target.pool_key,
        "host": "nas.example.com",
        "port": 2222,
        "username": "jenny",
        "keyPath": str(target.key_path),
        "knownHostsPath": str(target.known_hosts_path),
        "connectTimeoutS": 5.0,
        "keepaliveIntervalS": 30,
        # Il potatore delle sessioni inattive vive in Kotlin (il pool sta là):
        # senza questo campo il keepalive terrebbe viva per sempre una sessione
        # inutilizzata dopo un singolo comando.
        "idleCloseS": 300,
        "command": "uptime",
        "timeoutS": 30.0,
        "maxOutputChars": 10_000,
    }


async def test_pool_key_travels_so_kotlin_can_reconnect_on_param_change(bridge, tmp_path):
    """Cambiare porta cambia la chiave del pool: il pool vive in Kotlin, la chiave qui."""
    bridge.responses["exec"] = {"exitCode": 0, "stdout": "", "stderr": ""}
    backend = AndroidSshBackend()

    await backend.exec(_target(tmp_path), "id", timeout_s=5, max_output_chars=100)
    await backend.exec(_target(tmp_path, port=22), "id", timeout_s=5, max_output_chars=100)

    keys = [payload["poolKey"] for name, payload in bridge.calls if name == "exec"]
    assert keys[0] != keys[1]


# -- risultati ---------------------------------------------------------------

async def test_exec_maps_result_fields(bridge, tmp_path):
    bridge.responses["exec"] = {
        "exitCode": 0,
        "stdout": "ciao\n",
        "stderr": "",
        "truncatedChars": 0,
    }
    result = await AndroidSshBackend().exec(
        _target(tmp_path), "echo ciao", timeout_s=5, max_output_chars=1000
    )
    assert (result.exit_code, result.stdout, result.truncated_chars) == (0, "ciao\n", 0)


async def test_nonzero_exit_is_a_result_not_an_exception(bridge, tmp_path):
    """Un comando fallito deve restare distinguibile da una connessione caduta."""
    bridge.responses["exec"] = {
        "exitCode": 3,
        "stdout": "",
        "stderr": "something broke\n",
        "truncatedChars": 0,
    }
    result = await AndroidSshBackend().exec(
        _target(tmp_path), "fail", timeout_s=5, max_output_chars=1000
    )
    assert result.exit_code == 3
    assert "something broke" in result.stderr


async def test_truncation_is_reported_as_the_bridge_measured_it(bridge, tmp_path):
    """Python non ri-tronca: il taglio è in Kotlin, qui si passa solo il limite.

    ``truncated_chars`` conta i caratteri SCARTATI, non la lunghezza finale.
    """
    bridge.responses["exec"] = {
        "exitCode": 0,
        "stdout": "x" * 100,
        "stderr": "",
        "truncatedChars": 4900,
    }
    result = await AndroidSshBackend().exec(
        _target(tmp_path), "big 5000", timeout_s=5, max_output_chars=100
    )
    assert len(result.stdout) == 100
    assert result.truncated_chars == 4900
    assert bridge.payload_for("exec")["maxOutputChars"] == 100


async def test_missing_result_fields_do_not_crash(bridge, tmp_path):
    """Un bridge che omette un campo dà un risultato degradato, non un TypeError."""
    bridge.responses["exec"] = {"stdout": "solo output"}
    result = await AndroidSshBackend().exec(
        _target(tmp_path), "x", timeout_s=5, max_output_chars=10
    )
    assert result.exit_code == -1
    assert result.stderr == ""


# -- traduzione degli errori -------------------------------------------------

@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("host_key", SshHostKeyError),
        ("auth", SshAuthError),
        ("timeout", SshTimeoutError),
        ("io", SshTransportError),
        # Categoria che questo Python non conosce (bridge più nuovo): ricade
        # sull'errore di trasporto, il più innocuo dei quattro.
        ("qualcosa_di_nuovo", SshTransportError),
    ],
)
async def test_error_categories_become_the_right_exceptions(
    bridge, tmp_path, category, expected
):
    bridge.responses["exec"] = {"error": "boom", "category": category}
    with pytest.raises(expected, match="boom"):
        await AndroidSshBackend().exec(
            _target(tmp_path), "id", timeout_s=5, max_output_chars=100
        )


async def test_host_key_error_is_not_swallowed_as_transport(bridge, tmp_path):
    """La host key è l'unico errore che richiede un umano: non deve degradare."""
    bridge.responses["exec"] = {
        "error": "no known_hosts file at /data/ssh/known_hosts",
        "category": "host_key",
    }
    with pytest.raises(SshHostKeyError):
        await AndroidSshBackend().exec(
            _target(tmp_path), "id", timeout_s=5, max_output_chars=100
        )


async def test_unexpected_java_exception_becomes_transport_error(bridge, tmp_path):
    """Un'eccezione che scappa dal bridge non deve risalire come errore generico."""
    bridge.raises = RuntimeError("java.lang.IllegalStateException: detached")
    with pytest.raises(SshTransportError, match="exec"):
        await AndroidSshBackend().exec(
            _target(tmp_path), "id", timeout_s=5, max_output_chars=100
        )


async def test_malformed_json_becomes_transport_error(bridge, tmp_path):
    bridge.exec = lambda request: "non è json"  # noqa: E731
    with pytest.raises(SshTransportError, match="malformed JSON"):
        await AndroidSshBackend().exec(
            _target(tmp_path), "id", timeout_s=5, max_output_chars=100
        )


async def test_stuck_bridge_hits_the_asyncio_backstop(bridge, tmp_path, monkeypatch):
    """Un bridge piantato scade lato asyncio invece di appendere la coroutine.

    Il margine e il connect timeout sono ridotti apposta: il backstop reale è
    volutamente più lungo di quello nativo, e aspettarlo qui costerebbe secondi.
    """
    monkeypatch.setattr(android_mod, "_BACKSTOP_MARGIN_S", 0.0)
    bridge.delay_s = 0.3
    bridge.responses["exec"] = {"exitCode": 0, "stdout": "", "stderr": ""}

    with pytest.raises(SshTimeoutError, match="did not answer"):
        await AndroidSshBackend().exec(
            _target(tmp_path, connect_timeout_s=0.05),
            "sleep 100",
            timeout_s=0.05,
            max_output_chars=100,
        )


# -- trasferimenti -----------------------------------------------------------

async def test_put_passes_paths_and_returns_bytes(bridge, tmp_path):
    bridge.responses["put"] = {"bytes": 4096}
    local = tmp_path / "up.txt"

    written = await AndroidSshBackend().put(_target(tmp_path), local, "/srv/up.txt")

    assert written == 4096
    payload = bridge.payload_for("put")
    # Path, non byte: il file lo apre Kotlin.
    assert payload["localPath"] == str(local)
    assert payload["remotePath"] == "/srv/up.txt"


async def test_get_sends_the_cap_so_it_can_be_checked_before_writing(bridge, tmp_path):
    bridge.responses["get"] = {"bytes": 128}
    destination = tmp_path / "down.bin"

    read = await AndroidSshBackend().get(
        _target(tmp_path), "/srv/down.bin", destination, max_bytes=1_000
    )

    assert read == 128
    payload = bridge.payload_for("get")
    assert payload["maxBytes"] == 1_000
    assert payload["localPath"] == str(destination)


async def test_get_over_cap_raises_and_leaves_nothing_behind(bridge, tmp_path):
    """Il cap lo applica il bridge prima di scrivere: qui si verifica l'errore."""
    destination = tmp_path / "should_not_exist.bin"
    bridge.responses["get"] = {
        "error": "/srv/huge is 5000 bytes, over the 1000 byte limit",
        "category": "io",
    }
    with pytest.raises(SshTransportError, match="over the"):
        await AndroidSshBackend().get(
            _target(tmp_path), "/srv/huge", destination, max_bytes=1_000
        )
    assert not destination.exists()


# -- chiavi e host key -------------------------------------------------------

async def test_generate_key_pair_returns_only_the_public_key(bridge, tmp_path):
    key_path = tmp_path / "generated" / "id_ed25519"
    bridge.responses["generateKeyPair"] = {"publicKey": "ssh-ed25519 AAAAC3Nz jenny\n"}

    public = await AndroidSshBackend().generate_key_pair(key_path)

    assert public == "ssh-ed25519 AAAAC3Nz jenny"
    assert "PRIVATE" not in public
    assert bridge.payload_for("generateKeyPair") == {"keyPath": str(key_path)}


async def test_generate_key_pair_without_public_key_is_an_error(bridge, tmp_path):
    bridge.responses["generateKeyPair"] = {"publicKey": ""}
    with pytest.raises(SshTransportError):
        await AndroidSshBackend().generate_key_pair(tmp_path / "id_ed25519")


async def test_probe_host_key_returns_line_and_fingerprint(bridge, tmp_path):
    bridge.responses["probeHostKey"] = {
        "line": "[nas.example.com]:2222 ssh-ed25519 AAAAC3Nz",
        "fingerprint": "SHA256:abc123",
    }
    line, fingerprint = await AndroidSshBackend().probe_host_key("nas.example.com", 2222)

    assert line == "[nas.example.com]:2222 ssh-ed25519 AAAAC3Nz"
    assert fingerprint == "SHA256:abc123"
    payload = bridge.payload_for("probeHostKey")
    assert payload["host"] == "nas.example.com"
    assert payload["port"] == 2222


async def test_probe_host_key_without_a_key_is_an_error(bridge):
    bridge.responses["probeHostKey"] = {"line": "", "fingerprint": ""}
    with pytest.raises(SshTransportError):
        await AndroidSshBackend().probe_host_key("nas.example.com", 2222)


# -- shutdown ----------------------------------------------------------------

async def test_close_all_reaches_the_bridge(bridge):
    bridge.responses["closeAll"] = {"closed": 2}
    await AndroidSshBackend().close_all()
    assert [name for name, _ in bridge.calls] == ["closeAll"]


async def test_close_all_surfaces_a_failing_bridge(bridge):
    """Lo shutdown non deve fingere di aver chiuso: chi chiama decide se ignorare."""
    bridge.responses["closeAll"] = {"error": "detached", "category": "io"}
    with pytest.raises(SshTransportError):
        await AndroidSshBackend().close_all()


def test_bridge_class_name_matches_the_kotlin_object():
    """Il nome è l'unico contratto con Kotlin che nessun test può verificare a runtime."""
    assert android_mod._BRIDGE_CLASS == "com.flagdizero.jenny.SshBridge"


async def test_concurrent_calls_do_not_block_the_event_loop(bridge, tmp_path):
    """Le chiamate bloccanti vanno su thread: due comandi non si serializzano sul loop."""
    bridge.delay_s = 0.2
    bridge.responses["exec"] = {"exitCode": 0, "stdout": "", "stderr": ""}
    backend = AndroidSshBackend()
    target = _target(tmp_path)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.gather(
        backend.exec(target, "uno", timeout_s=5, max_output_chars=10),
        backend.exec(target, "due", timeout_s=5, max_output_chars=10),
    )
    # Serializzate costerebbero 0.4s: il margine è largo per non dipendere dal
    # carico della macchina, ma la differenza fra le due ipotesi resta netta.
    assert loop.time() - started < 0.35
