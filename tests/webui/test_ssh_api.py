"""Le impostazioni SSH lato WebUI: CRUD, chiave, accettazione dell'impronta.

Qui si misura la parte che l'utente tocca con le dita, e in particolare le
quattro cose che non devono cedere: la chiave privata non entra mai in un
payload, **né ci entra la password**, ogni scrittura passa dal funnel della
config, e una host key **cambiata** non si accetta senza una seconda decisione
esplicita.

NB sull'infrastruttura: l'ambiente si monta con fixture sincrone e con un
``asynccontextmanager`` dove serve attesa. Con pytest 9 + pytest-asyncio 1.4 una
fixture async generator si pianta invece di fallire, quindi non ce ne sono.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jenny.agent.tools.ssh_backends.base import SshTransportError, known_hosts_name
from jenny.agent.tools.ssh_transport import (
    is_host_pinned,
    known_hosts_path,
    pinned_host_key,
    record_host_key,
    ssh_key_path,
)
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui import ssh_api
from jenny.webui.settings_api import WebUISettingsError

# Chiavi finte ma base64 *valido*: l'impronta di una riga già registrata si
# ricalcola da qui (``_fingerprint_from_known_hosts_line``), e un blob non
# decodificabile la renderebbe silenziosamente ``None`` — cioè un test verde su
# un dialogo che non mostrerebbe niente.
KEY_LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFq2JV7TrJ7oC+dGKBRlEqiVJ7zzp3qNnuq0DbOizd+D"
OTHER_KEY_LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOju92BC8Fdfo6g9iywGm2HPHa2vx3h9X3sInwmxmP7u"

FINGERPRINT = "SHA256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_FINGERPRINT = "SHA256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
# Impronta di ``KEY_LINE`` nel formato di OpenSSH (e di ``probe_host_key``):
# deve venire fuori identica, altrimenti il confronto visivo fra vecchia e
# nuova impronta è fra due formati diversi.
PINNED_KEY_FINGERPRINT = "SHA256:cbw0ELDuFhVPhe1QDm8eQBkvvCwF8H+K77N3DxQ5px0"


# -- ambiente ----------------------------------------------------------------


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Workspace *e* config isolati per test.

    ``get_ssh_dir()`` deriva dal workspace, che la conftest di sessione condivide
    fra tutti i test: senza questo isolamento un test che scrive ``known_hosts``
    lo scriverebbe per tutti gli altri.
    """
    from jenny.config import paths as paths_mod

    previous = get_runtime_context().workspace_dir
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    paths_mod.set_workspace_dir(str(workspace))

    config_path = tmp_path / "config.json"
    save_config(Config(), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    # La policy di rete risolve davvero il DNS: qui non c'è niente da risolvere,
    # e i test che la vogliono negativa la risostituiscono da soli.
    monkeypatch.setattr(ssh_api, "validate_ssh_target", lambda host: (True, ""))
    ssh_api._PENDING_PROBES.clear()
    try:
        yield config_path
    finally:
        ssh_api._PENDING_PROBES.clear()
        paths_mod.set_workspace_dir(str(previous) if previous else "")


class FakeBackend:
    """Doppio del backend: probe senza rete, chiave senza crittografia."""

    def __init__(self, *, line: str = KEY_LINE, fingerprint: str = FINGERPRINT) -> None:
        self.line = line
        self.fingerprint = fingerprint
        self.fail: Exception | None = None
        self.generated: list[Path] = []
        self.probe_delay = 0.0

    async def generate_key_pair(self, key_path: Path) -> str:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text("PRIVATE-KEY-MATERIAL-DO-NOT-LEAK")
        self.generated.append(key_path)
        return "ssh-ed25519 AAAAPUBLIC jenny@phone"

    async def probe_host_key(self, host: str, port: int) -> tuple[str, str]:
        if self.probe_delay:
            await asyncio.sleep(self.probe_delay)
        if self.fail is not None:
            raise self.fail
        return f"{known_hosts_name(host, port)} {self.line}", self.fingerprint


def _backend(monkeypatch: pytest.MonkeyPatch, **kwargs) -> FakeBackend:
    backend = FakeBackend(**kwargs)
    monkeypatch.setattr(ssh_api, "get_ssh_backend", lambda: backend)
    return backend


def _q(**kwargs) -> dict[str, list[str]]:
    return {k: [str(v)] for k, v in kwargs.items()}


async def _add_host(alias: str = "prod", **overrides):
    params = {"alias": alias, "host": "example.com", "username": "root"}
    params.update(overrides)
    return await ssh_api.save_ssh_host(_q(**params))


# -- CRUD --------------------------------------------------------------------


async def test_save_host_creates_the_entry(env) -> None:
    payload = await _add_host(port=2222, description="il NAS di casa")

    assert payload["hosts"][0]["alias"] == "prod"
    assert payload["hosts"][0]["port"] == 2222
    assert payload["hosts"][0]["description"] == "il NAS di casa"
    # Un host appena aggiunto non è ancora usabile: né chiave né impronta.
    assert payload["hosts"][0]["has_key"] is False
    assert payload["hosts"][0]["pinned"] is False

    saved = load_config().tools.ssh.hosts
    assert [(h.alias, h.host, h.port, h.username) for h in saved] == [
        ("prod", "example.com", 2222, "root")
    ]


async def test_save_host_updates_the_existing_alias(env) -> None:
    await _add_host()
    payload = await _add_host(username="deploy", description="aggiornato")

    assert len(payload["hosts"]) == 1
    assert payload["hosts"][0]["username"] == "deploy"
    assert load_config().tools.ssh.hosts[0].username == "deploy"


async def test_save_host_requires_host_and_username(env) -> None:
    with pytest.raises(WebUISettingsError, match="host is required"):
        await ssh_api.save_ssh_host(_q(alias="prod", username="root"))
    with pytest.raises(WebUISettingsError, match="username is required"):
        await ssh_api.save_ssh_host(_q(alias="prod", host="example.com"))


async def test_alias_that_could_escape_the_key_path_is_refused(env) -> None:
    """Due alias non devono poter collassare sullo stesso file di chiave."""
    with pytest.raises(WebUISettingsError, match="alias must be"):
        await _add_host(alias="../../etc/passwd")
    assert load_config().tools.ssh.hosts == []


async def test_host_blocked_by_the_network_policy_is_refused(env, monkeypatch) -> None:
    monkeypatch.setattr(
        ssh_api,
        "validate_ssh_target",
        lambda host: (False, f"Blocked: {host} resolves to 127.0.0.1"),
    )
    with pytest.raises(WebUISettingsError, match="127.0.0.1"):
        await _add_host(host="localhost")
    # Niente host mezzo salvato: la validazione precede la scrittura.
    assert load_config().tools.ssh.hosts == []


async def test_moving_a_host_drops_the_pin_it_no_longer_covers(env) -> None:
    """Impronta accettata per un indirizzo, non per l'alias."""
    await _add_host()
    record_host_key(f"example.com {KEY_LINE}")

    def _stamp(config):
        config.tools.ssh.hosts[0].host_key_fingerprint = FINGERPRINT

    from jenny.config import store

    await store.mutate(_stamp)

    payload = await _add_host(host="other.example")

    assert payload["hosts"][0]["pinned"] is False
    assert payload["hosts"][0]["host_key_fingerprint"] is None
    assert not is_host_pinned("example.com", 22)


async def test_delete_removes_key_and_known_hosts_line(env, monkeypatch) -> None:
    backend = _backend(monkeypatch)
    await _add_host()
    await ssh_api.generate_ssh_key(_q(alias="prod"))
    record_host_key(f"example.com {KEY_LINE}")
    record_host_key(f"other.example {OTHER_KEY_LINE}")
    key_path = backend.generated[0]
    assert key_path.exists()

    payload = await ssh_api.delete_ssh_host(_q(alias="prod"))

    assert payload["hosts"] == []
    assert load_config().tools.ssh.hosts == []
    assert not key_path.exists()
    assert not ssh_api._public_key_path("prod").exists()
    # La riga se ne va con l'host, quella di un altro host resta.
    assert not is_host_pinned("example.com", 22)
    assert is_host_pinned("other.example", 22)


async def test_delete_of_an_unknown_alias_is_a_404(env) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        await ssh_api.delete_ssh_host(_q(alias="ghost"))
    assert excinfo.value.status == 404


async def test_enable_toggle_is_a_separate_gate(env) -> None:
    await _add_host()
    assert load_config().tools.ssh.enable is False

    payload = await ssh_api.update_ssh_settings(_q(enabled="1"))
    assert payload["enabled"] is True
    assert load_config().tools.ssh.enable is True

    # Host spenti restano visibili: sono ancora roba dell'utente da correggere.
    payload = await ssh_api.update_ssh_settings(_q(enabled="0"))
    assert payload["enabled"] is False
    assert len(payload["hosts"]) == 1


# -- chiave ------------------------------------------------------------------


async def test_generate_returns_only_the_public_key(env, monkeypatch) -> None:
    _backend(monkeypatch)
    await _add_host()

    payload = await ssh_api.generate_ssh_key(_q(alias="prod"))

    assert payload["public_key"].startswith("ssh-ed25519 ")
    assert payload["hosts"][0]["has_key"] is True
    assert "PRIVATE-KEY-MATERIAL-DO-NOT-LEAK" not in json.dumps(payload)


async def test_read_payload_never_carries_private_material(env, monkeypatch) -> None:
    """La regola del file: la privata non attraversa mai la WebView."""
    _backend(monkeypatch)
    await _add_host()
    await ssh_api.generate_ssh_key(_q(alias="prod"))

    dumped = json.dumps(ssh_api.ssh_settings_payload())

    assert "PRIVATE-KEY-MATERIAL-DO-NOT-LEAK" not in dumped
    assert "PRIVATE" not in dumped.upper()
    # Ciò che resta è un booleano e la pubblica, che è fatta per essere copiata.
    assert '"has_key": true' in dumped
    assert "ssh-ed25519 AAAAPUBLIC" in dumped


async def test_public_key_survives_closing_the_screen(env, monkeypatch) -> None:
    """Il backend la restituisce una volta sola: senza sidecar sarebbe persa."""
    _backend(monkeypatch)
    await _add_host()
    generated = (await ssh_api.generate_ssh_key(_q(alias="prod")))["public_key"]

    assert ssh_api.ssh_settings_payload()["hosts"][0]["public_key"] == generated


async def test_generated_private_key_is_0600(env, monkeypatch) -> None:
    """Contro il backend vero: i permessi sono la sua parte del contratto."""
    from jenny.agent.tools.ssh_backends.dev import DevSshBackend

    monkeypatch.setattr(ssh_api, "get_ssh_backend", lambda: DevSshBackend())
    await _add_host()

    payload = await ssh_api.generate_ssh_key(_q(alias="prod"))

    key_path = ssh_key_path("prod")
    assert oct(key_path.stat().st_mode)[-3:] == "600"
    assert oct(ssh_api._public_key_path("prod").stat().st_mode)[-3:] == "600"
    assert payload["public_key"].startswith("ssh-ed25519 ")
    assert "PRIVATE KEY" not in json.dumps(payload)


async def test_regenerating_a_key_requires_explicit_replace(env, monkeypatch) -> None:
    """Rigenerare revoca l'accesso già installato sul server."""
    _backend(monkeypatch)
    await _add_host()
    await ssh_api.generate_ssh_key(_q(alias="prod"))

    with pytest.raises(WebUISettingsError) as excinfo:
        await ssh_api.generate_ssh_key(_q(alias="prod"))
    assert excinfo.value.status == 409

    payload = await ssh_api.generate_ssh_key(_q(alias="prod", replace="1"))
    assert payload["public_key"]


async def test_generate_for_an_unknown_alias_is_a_404(env, monkeypatch) -> None:
    _backend(monkeypatch)
    with pytest.raises(WebUISettingsError) as excinfo:
        await ssh_api.generate_ssh_key(_q(alias="ghost"))
    assert excinfo.value.status == 404


# -- password ----------------------------------------------------------------

# Una password riconoscibile a occhio dentro un JSON: se sfugge in un payload,
# il test che la cerca lo dice senza ambiguità.
PASSWORD = "correct-horse-battery-staple"


async def test_save_host_with_password_auth(env) -> None:
    payload = await _add_host(auth="password", password=PASSWORD)

    assert payload["hosts"][0]["auth"] == "password"
    assert payload["hosts"][0]["has_password"] is True
    saved = load_config().tools.ssh.hosts[0]
    assert saved.auth == "password"
    assert saved.password == PASSWORD


async def test_default_auth_stays_key_for_hosts_saved_without_it(env) -> None:
    """Gli host già registrati non cambiano comportamento."""
    payload = await _add_host()

    assert payload["hosts"][0]["auth"] == "key"
    assert payload["hosts"][0]["has_password"] is False
    assert load_config().tools.ssh.hosts[0].password is None


async def test_password_never_travels_back_to_the_client(env) -> None:
    """La regola del file, per la password: al client va solo un booleano.

    Non esiste nemmeno la versione offuscata alla ``_mask_api_key``: quattro
    caratteri veri sarebbero quattro caratteri regalati.
    """
    await _add_host(auth="password", password=PASSWORD)

    dumped = json.dumps(ssh_api.ssh_settings_payload())

    assert PASSWORD not in dumped
    # Nemmeno un pezzo: un suggerimento parziale è comunque materiale vero.
    assert "correct-horse" not in dumped
    assert "battery-staple" not in dumped
    assert '"has_password": true' in dumped


async def test_the_save_response_does_not_echo_the_password_back(env) -> None:
    """Il payload di ritorno della scrittura è lo stesso di quello di lettura."""
    payload = await _add_host(auth="password", password=PASSWORD)

    assert PASSWORD not in json.dumps(payload)


async def test_a_password_host_without_a_password_is_refused(env) -> None:
    """Altrimenti l'host risulta configurato e fallisce solo al primo comando.

    Cioè dentro un turno dell'agente, dove l'errore lo legge il modello e non
    l'utente che potrebbe correggerlo.
    """
    with pytest.raises(WebUISettingsError, match="password is required"):
        await _add_host(auth="password")
    assert load_config().tools.ssh.hosts == []

    # Una password di soli spazi è un campo non compilato, non una password.
    with pytest.raises(WebUISettingsError, match="password is required"):
        await _add_host(auth="password", password="   ")
    assert load_config().tools.ssh.hosts == []


async def test_switching_an_existing_host_to_password_needs_one(env) -> None:
    await _add_host()

    with pytest.raises(WebUISettingsError, match="password is required"):
        await _add_host(auth="password")

    # L'host resta quello di prima: il rifiuto avviene dentro ``mutate``, che
    # non scrive niente se il callback solleva.
    saved = load_config().tools.ssh.hosts[0]
    assert saved.auth == "key"
    assert saved.password is None


async def test_editing_a_password_host_without_retyping_keeps_it(env) -> None:
    """Il campo vuoto vale "tieni quella salvata": la UI non l'ha mai ricevuta."""
    await _add_host(auth="password", password=PASSWORD)

    payload = await _add_host(auth="password", description="aggiornato")

    assert payload["hosts"][0]["description"] == "aggiornato"
    assert payload["hosts"][0]["has_password"] is True
    assert load_config().tools.ssh.hosts[0].password == PASSWORD


async def test_a_password_is_stored_exactly_as_typed(env) -> None:
    """Gli spazi ai bordi sono contenuto, non formattazione."""
    padded = f" {PASSWORD} "
    await _add_host(auth="password", password=padded)

    assert load_config().tools.ssh.hosts[0].password == padded


async def test_switching_between_password_and_key_leaves_a_coherent_config(env) -> None:
    """Andata e ritorno su un host esistente, senza residui in mezzo."""
    await _add_host(auth="password", password=PASSWORD)

    # Password → chiave: la credenziale che nessuno userà più se ne va con il
    # modo che la usava. Tenerla "per comodità" lascerebbe nel file, in chiaro,
    # una password che l'utente crede di aver rimosso passando alla chiave.
    payload = await _add_host(auth="key")
    assert payload["hosts"][0]["auth"] == "key"
    assert payload["hosts"][0]["has_password"] is False
    assert load_config().tools.ssh.hosts[0].password is None
    assert PASSWORD not in json.dumps(payload)

    # Chiave → password: siccome la vecchia è stata buttata, non basta
    # riselezionare il modo.
    with pytest.raises(WebUISettingsError, match="password is required"):
        await _add_host(auth="password")

    payload = await _add_host(auth="password", password="un'altra")
    assert payload["hosts"][0]["auth"] == "password"
    assert load_config().tools.ssh.hosts[0].password == "un'altra"

    # Un solo host per tutto il giro: l'alias è l'identità, non la modalità.
    assert len(load_config().tools.ssh.hosts) == 1


async def test_an_unknown_auth_mode_is_refused(env) -> None:
    with pytest.raises(WebUISettingsError, match="auth must be"):
        await _add_host(auth="kerberos")
    assert load_config().tools.ssh.hosts == []


async def test_a_password_host_still_has_to_pin_the_host_key(env, monkeypatch) -> None:
    """Il pinning non è un passo in meno con la password: è quello che decide
    a chi la password verrà consegnata."""
    _backend(monkeypatch)
    await _add_host(auth="password", password=PASSWORD)

    assert ssh_api.ssh_settings_payload()["hosts"][0]["pinned"] is False

    await ssh_api.probe_ssh_host_key(_q(alias="prod"))
    payload = await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=FINGERPRINT))

    assert payload["hosts"][0]["pinned"] is True
    # E l'impronta accettata non ha portato con sé la password.
    assert PASSWORD not in json.dumps(payload)


async def test_deleting_a_password_host_takes_the_password_with_it(env) -> None:
    """Eliminare deve revocare davvero, come già fa con la chiave."""
    await _add_host(auth="password", password=PASSWORD)
    assert PASSWORD in env.read_text("utf-8")

    payload = await ssh_api.delete_ssh_host(_q(alias="prod"))

    assert payload["hosts"] == []
    assert PASSWORD not in env.read_text("utf-8")


async def test_moving_a_password_host_keeps_the_password_but_drops_the_pin(env) -> None:
    """Cambiare indirizzo non ributta via la password, ma nemmeno la consegna.

    Il bersaglio si è spostato, quindi l'impronta accettata per il vecchio
    indirizzo se ne va (come per un host a chiave) — ed è quello il gate: finché
    un umano non verifica la macchina nuova, nessuna connessione parte e la
    password non arriva da nessuna parte.
    """
    await _add_host(auth="password", password=PASSWORD)
    record_host_key(f"example.com {KEY_LINE}")

    payload = await _add_host(auth="password", host="other.example")

    assert payload["hosts"][0]["has_password"] is True
    assert payload["hosts"][0]["pinned"] is False
    assert not is_host_pinned("example.com", 22)


# -- probe e accettazione dell'impronta --------------------------------------


async def test_probe_reports_the_fingerprint_without_accepting_it(env, monkeypatch) -> None:
    _backend(monkeypatch)
    await _add_host()

    probe = (await ssh_api.probe_ssh_host_key(_q(alias="prod")))["probe"]

    assert probe["fingerprint"] == FINGERPRINT
    assert probe["changed"] is False
    assert probe["already_accepted"] is False
    # Nessuna decisione presa: known_hosts è ancora intatto.
    assert not is_host_pinned("example.com", 22)


async def test_probe_of_an_unreachable_host_is_a_502(env, monkeypatch) -> None:
    backend = _backend(monkeypatch)
    backend.fail = SshTransportError("connection refused")
    await _add_host()

    with pytest.raises(WebUISettingsError) as excinfo:
        await ssh_api.probe_ssh_host_key(_q(alias="prod"))
    assert excinfo.value.status == 502


async def test_accept_writes_known_hosts_and_the_display_fingerprint(env, monkeypatch) -> None:
    _backend(monkeypatch)
    await _add_host()
    await ssh_api.probe_ssh_host_key(_q(alias="prod"))

    payload = await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=FINGERPRINT))

    assert payload["accepted"]["fingerprint"] == FINGERPRINT
    assert payload["hosts"][0]["pinned"] is True
    assert KEY_LINE in known_hosts_path().read_text()
    assert load_config().tools.ssh.hosts[0].host_key_fingerprint == FINGERPRINT


async def test_accept_without_a_probe_is_refused(env, monkeypatch) -> None:
    """Non si accetta un'impronta che non è stata letta da questa schermata."""
    _backend(monkeypatch)
    await _add_host()

    with pytest.raises(WebUISettingsError) as excinfo:
        await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=FINGERPRINT))
    assert excinfo.value.status == 409
    assert not is_host_pinned("example.com", 22)


async def test_accept_refuses_a_fingerprint_other_than_the_one_shown(env, monkeypatch) -> None:
    _backend(monkeypatch)
    await _add_host()
    await ssh_api.probe_ssh_host_key(_q(alias="prod"))

    with pytest.raises(WebUISettingsError, match="does not match"):
        await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=OTHER_FINGERPRINT))
    assert not is_host_pinned("example.com", 22)


async def test_a_stale_pending_probe_must_be_read_again(env, monkeypatch) -> None:
    _backend(monkeypatch)
    await _add_host()
    await ssh_api.probe_ssh_host_key(_q(alias="prod"))
    monkeypatch.setattr(ssh_api, "_PROBE_TTL_S", -1.0)

    with pytest.raises(WebUISettingsError, match="probe it again"):
        await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=FINGERPRINT))


async def test_changed_host_key_needs_a_second_explicit_decision(env, monkeypatch) -> None:
    """Il caso che questa UI esiste per gestire.

    Una host key diversa su un host già accettato è un potenziale MITM: il
    probe lo dichiara (``changed``) mostrando le due impronte affiancate, e
    l'accettazione fallisce finché non arriva ``replace=1``, cioè finché un
    umano non ha guardato e deciso.
    """
    _backend(monkeypatch, line=OTHER_KEY_LINE, fingerprint=OTHER_FINGERPRINT)
    await _add_host()
    record_host_key(f"example.com {KEY_LINE}")

    probe = (await ssh_api.probe_ssh_host_key(_q(alias="prod")))["probe"]
    assert probe["changed"] is True
    assert probe["fingerprint"] == OTHER_FINGERPRINT
    # L'impronta vecchia viaggia col probe: senza le due affiancate, "accetta"
    # e "annulla" sarebbero una scelta alla cieca.
    assert probe["pinned_fingerprint"] == PINNED_KEY_FINGERPRINT
    assert probe["pinned_fingerprint"] != probe["fingerprint"]

    with pytest.raises(WebUISettingsError) as excinfo:
        await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=OTHER_FINGERPRINT))
    assert excinfo.value.status == 409
    assert KEY_LINE in known_hosts_path().read_text()

    await ssh_api.accept_ssh_host_key(
        _q(alias="prod", fingerprint=OTHER_FINGERPRINT, replace="1")
    )
    text = known_hosts_path().read_text()
    assert OTHER_KEY_LINE in text
    assert KEY_LINE not in text


async def test_re_accepting_the_same_key_is_idempotent(env, monkeypatch) -> None:
    _backend(monkeypatch)
    await _add_host()
    await ssh_api.probe_ssh_host_key(_q(alias="prod"))
    await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=FINGERPRINT))

    probe = (await ssh_api.probe_ssh_host_key(_q(alias="prod")))["probe"]
    assert probe["already_accepted"] is True
    assert probe["changed"] is False

    await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=FINGERPRINT))
    assert known_hosts_path().read_text().count(KEY_LINE) == 1
    assert pinned_host_key("example.com", 22) is not None


# -- funnel della config -----------------------------------------------------


def test_module_never_calls_save_config_directly() -> None:
    """``save_config`` riscrive il file intero: chi lo chiama fuori da
    ``store.mutate`` cancella in silenzio ciò che un altro scrittore ha appena
    salvato. È il bug che nessun test cattura per te — questo lo cattura."""
    source = Path(ssh_api.__file__).read_text("utf-8")
    assert "save_config" not in source


async def test_every_write_goes_through_store_mutate(env, monkeypatch) -> None:
    from jenny.config import store

    calls: list[str] = []
    real_mutate = store.mutate

    async def counting_mutate(apply, **kwargs):
        calls.append("mutate")
        return await real_mutate(apply, **kwargs)

    monkeypatch.setattr(store, "mutate", counting_mutate)
    _backend(monkeypatch)

    await _add_host()
    await ssh_api.update_ssh_settings(_q(enabled="1"))
    await ssh_api.probe_ssh_host_key(_q(alias="prod"))
    await ssh_api.accept_ssh_host_key(_q(alias="prod", fingerprint=FINGERPRINT))
    await ssh_api.delete_ssh_host(_q(alias="prod"))

    # save host, enable, accept (impronta di display), delete.
    assert len(calls) == 4


async def test_route_layer_maps_auth_and_errors(env, monkeypatch) -> None:
    """Lo strato route: 401 senza token, status applicativi propagati, 500 muto."""
    import urllib.parse
    from unittest.mock import MagicMock

    from websockets.http11 import Headers
    from websockets.http11 import Request as WsRequest

    from jenny.channels.http_utils import (
        check_api_secret,
        http_error,
        http_json_response,
        parse_query,
    )
    from jenny.webui.settings_routes import WebUISettingsRouter

    secret = "s3cr3t-ssh"
    logger = MagicMock()
    router = WebUISettingsRouter(
        bus=MagicMock(),
        logger=logger,
        check_api_token=lambda r: check_api_secret(r.headers, r.path, secret),
        parse_query=parse_query,
        json_response=http_json_response,
        error_response=http_error,
    )

    def request(path: str, *, token: str | None = secret) -> WsRequest:
        if token is not None:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}token={urllib.parse.quote(token)}"
        return WsRequest(path=path, headers=Headers())

    _backend(monkeypatch)

    for path in (
        "/api/settings/ssh",
        "/api/settings/ssh/update",
        "/api/settings/ssh/host/save",
        "/api/settings/ssh/host/delete",
        "/api/settings/ssh/key/generate",
        "/api/settings/ssh/host-key/probe",
        "/api/settings/ssh/host-key/accept",
    ):
        response = await router.dispatch(request(path, token=None), path)
        assert response is not None and response.status_code == 401, path

    saved = await router.dispatch(
        request("/api/settings/ssh/host/save?alias=prod&host=example.com&username=root"),
        "/api/settings/ssh/host/save",
    )
    assert saved.status_code == 200
    assert json.loads(saved.body.decode())["hosts"][0]["alias"] == "prod"

    read = await router.dispatch(request("/api/settings/ssh"), "/api/settings/ssh")
    assert read.status_code == 200

    # Alias sconosciuto: 404 applicativo, non un 500.
    missing = await router.dispatch(
        request("/api/settings/ssh/key/generate?alias=ghost"),
        "/api/settings/ssh/key/generate",
    )
    assert missing.status_code == 404

    # Accettazione senza probe: 409, il conflitto che la UI deve saper leggere.
    conflict = await router.dispatch(
        request(f"/api/settings/ssh/host-key/accept?alias=prod&fingerprint={FINGERPRINT}"),
        "/api/settings/ssh/host-key/accept",
    )
    assert conflict.status_code == 409

    async def boom(query):
        raise RuntimeError("guasto inatteso")

    monkeypatch.setattr("jenny.webui.settings_routes.probe_ssh_host_key", boom)
    failed = await router.dispatch(
        request("/api/settings/ssh/host-key/probe?alias=prod"),
        "/api/settings/ssh/host-key/probe",
    )
    assert failed.status_code == 500
    assert b"guasto inatteso" not in failed.body
    logger.exception.assert_called_once()


async def test_a_setting_saved_during_a_slow_probe_is_not_lost(env, monkeypatch) -> None:
    """La regola dell'I/O lento fuori dal lock, misurata.

    Se la risoluzione DNS avvenisse *dentro* ``mutate``, il lock resterebbe
    preso per tutta la sua durata e ogni altra impostazione salvata nel
    frattempo aspetterebbe — o, con una config letta prima, sparirebbe.
    """
    from jenny.webui.settings_api import update_provider

    started = asyncio.Event()

    def slow_validate(host: str) -> tuple[bool, str]:
        import time

        started.set()
        time.sleep(0.05)
        return True, ""

    monkeypatch.setattr(ssh_api, "validate_ssh_target", slow_validate)

    saving = asyncio.create_task(_add_host())
    await started.wait()
    await update_provider({
        "name": "local-llama",
        "format": "openai_compat",
        "api_key": "EMPTY",
        "api_base": "http://127.0.0.1:8080/v1",
    })
    await saving

    config = load_config()
    assert [p.name for p in config.providers.providers] == ["local-llama"]
    assert [h.alias for h in config.tools.ssh.hosts] == ["prod"]
