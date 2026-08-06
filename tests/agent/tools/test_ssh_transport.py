"""Il livello che decide *se* si può parlare con un host.

Questi test coprono la parte autorizzativa — alias, policy di rete, pinning
della host key, presenza della chiave — che vive qui e non nei backend proprio
perché deve valere identica sul telefono e sul Mac.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.tools import ssh_transport
from jenny.agent.tools.ssh_backends.base import SshHostKeyError
from jenny.agent.tools.ssh_transport import (
    SshHostBlockedError,
    SshHostUnknownError,
    SshKeyMissingError,
    configured_hosts,
    forget_host,
    is_host_pinned,
    known_hosts_path,
    record_host_key,
    resolve_target,
    ssh_key_path,
)
from jenny.config.schema import Config
from jenny.config.tool_schemas import SshHostConfig

KEY_LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE"
OTHER_KEY_LINE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDIFFERENTDIFFERENTDIFFERENTXX"


@pytest.fixture
def ssh_home(tmp_path: Path):
    """Workspace isolato per test, così ``get_ssh_dir()`` non è condiviso."""
    from jenny.config import paths as paths_mod
    from jenny.runtime.context import get_runtime_context

    previous = get_runtime_context().workspace_dir
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    paths_mod.set_workspace_dir(str(workspace))
    try:
        yield tmp_path / "ssh"
    finally:
        paths_mod.set_workspace_dir(str(previous) if previous else "")


def _config(*hosts: SshHostConfig, enable: bool = True) -> Config:
    config = Config()
    config.tools.ssh.enable = enable
    config.tools.ssh.hosts = list(hosts)
    return config


def _host(alias: str = "prod", **kw) -> SshHostConfig:
    return SshHostConfig(
        alias=alias,
        host=kw.pop("host", "example.com"),
        port=kw.pop("port", 22),
        username=kw.pop("username", "root"),
        **kw,
    )


def _allow_everything(_host: str) -> tuple[bool, str]:
    return True, ""


# -- risoluzione degli alias -------------------------------------------------


def test_unknown_alias_error_lists_the_valid_ones(ssh_home):
    """Il chiamante tipico e un LLM che ha inventato il nome: va corretto subito."""
    config = _config(_host("prod"), _host("nas"))
    with pytest.raises(SshHostUnknownError) as excinfo:
        resolve_target("staging", config=config, validate=_allow_everything)
    message = str(excinfo.value)
    assert "prod" in message and "nas" in message


def test_blocked_host_is_refused(ssh_home):
    config = _config(_host())

    def blocked(_host: str) -> tuple[bool, str]:
        return False, "resolves to 127.0.0.1"

    with pytest.raises(SshHostBlockedError, match="127.0.0.1"):
        resolve_target("prod", config=config, validate=blocked)


def test_unpinned_host_is_refused_before_any_connection(ssh_home):
    """Nessun TOFU: senza impronta accettata non si tenta nemmeno di connettersi."""
    config = _config(_host())
    with pytest.raises(SshHostKeyError, match="not been accepted"):
        resolve_target("prod", config=config, validate=_allow_everything)


def test_missing_private_key_is_reported_as_such(ssh_home):
    config = _config(_host())
    record_host_key(f"example.com {KEY_LINE}")
    with pytest.raises(SshKeyMissingError, match="generate one"):
        resolve_target("prod", config=config, validate=_allow_everything)


def test_resolved_target_carries_config_values(ssh_home):
    config = _config(_host(port=2222, username="deploy"))
    config.tools.ssh.connect_timeout_s = 42.0
    config.tools.ssh.keepalive_interval_s = 7
    record_host_key(f"[example.com]:2222 {KEY_LINE}")
    ssh_key_path("prod").write_text("fake private key")

    host_cfg, target = resolve_target("prod", config=config, validate=_allow_everything)

    assert host_cfg.alias == "prod"
    assert (target.host, target.port, target.username) == ("example.com", 2222, "deploy")
    assert target.connect_timeout_s == 42.0
    assert target.keepalive_interval_s == 7
    assert target.known_hosts_path == known_hosts_path()


def test_hosts_are_hidden_when_ssh_is_disabled(ssh_home):
    assert configured_hosts(_config(_host(), enable=False)) == []
    assert len(configured_hosts(_config(_host()))) == 1


# -- known_hosts -------------------------------------------------------------


def test_record_host_key_is_idempotent(ssh_home):
    line = f"example.com {KEY_LINE}"
    record_host_key(line)
    record_host_key(line)
    assert known_hosts_path().read_text().count(KEY_LINE) == 1


def test_changed_host_key_is_refused_unless_replaced_explicitly(ssh_home):
    """Una host key cambiata e un potenziale MITM, non un aggiornamento."""
    record_host_key(f"example.com {KEY_LINE}")

    with pytest.raises(SshHostKeyError, match="already pinned"):
        record_host_key(f"example.com {OTHER_KEY_LINE}")

    record_host_key(f"example.com {OTHER_KEY_LINE}", replace=True)
    text = known_hosts_path().read_text()
    assert OTHER_KEY_LINE in text
    assert KEY_LINE not in text


def test_pinning_is_port_aware(ssh_home):
    """Porta non standard = nome host diverso in known_hosts."""
    record_host_key(f"[example.com]:2222 {KEY_LINE}")
    assert is_host_pinned("example.com", 2222)
    assert not is_host_pinned("example.com", 22)


def test_forget_host_removes_only_that_host(ssh_home):
    record_host_key(f"example.com {KEY_LINE}")
    record_host_key(f"other.example {OTHER_KEY_LINE}")

    forget_host("example.com", 22)

    assert not is_host_pinned("example.com", 22)
    assert is_host_pinned("other.example", 22)


def test_known_hosts_file_is_written_0600(ssh_home):
    record_host_key(f"example.com {KEY_LINE}")
    assert oct(known_hosts_path().stat().st_mode)[-3:] == "600"


def test_malformed_known_hosts_line_is_rejected(ssh_home):
    with pytest.raises(ValueError):
        record_host_key("example.com")


# -- percorsi ----------------------------------------------------------------


def test_key_path_is_derived_and_cannot_escape_the_ssh_dir(ssh_home):
    """L'alias arriva dalla config: non deve poter diventare un path."""
    from jenny.config.paths import get_ssh_dir

    nasty = ssh_key_path("../../etc/passwd")
    assert nasty.parent == get_ssh_dir()
    assert ".." not in nasty.name


def test_key_path_is_stable_per_alias(ssh_home):
    assert ssh_key_path("prod") == ssh_key_path("prod")
    assert ssh_key_path("prod") != ssh_key_path("nas")


# -- selezione del backend ---------------------------------------------------


def test_dev_backend_is_selected_off_device():
    from jenny.agent.tools.ssh_backends.dev import DevSshBackend

    ssh_transport.reset_ssh_backend()
    try:
        assert isinstance(ssh_transport.get_ssh_backend(), DevSshBackend)
    finally:
        ssh_transport.reset_ssh_backend()


def test_backend_is_a_singleton_so_the_pool_survives():
    """Ricostruirlo a ogni chiamata riaprirebbe una sessione SSH per comando."""
    ssh_transport.reset_ssh_backend()
    try:
        assert ssh_transport.get_ssh_backend() is ssh_transport.get_ssh_backend()
    finally:
        ssh_transport.reset_ssh_backend()


def test_reset_drops_the_cached_backend():
    ssh_transport.reset_ssh_backend()
    first = ssh_transport.get_ssh_backend()
    ssh_transport.reset_ssh_backend()
    assert ssh_transport.get_ssh_backend() is not first
    ssh_transport.reset_ssh_backend()
