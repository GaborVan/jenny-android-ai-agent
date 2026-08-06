"""Test per SshConfig/SshHostConfig e il loro aggancio a ToolsConfig."""

from __future__ import annotations

import pytest

from jenny.config.schema import Config, ToolsConfig
from jenny.config.tool_schemas import SshConfig, SshHostConfig


def test_defaults_are_closed():
    # Due gate distinti e volutamente entrambi necessari: questa e la sola
    # capacita che agisce su una macchina che non e il telefono.
    cfg = SshConfig()
    assert cfg.enable is False
    assert cfg.hosts == []


def test_operational_defaults():
    cfg = SshConfig()
    assert cfg.connect_timeout_s == 15.0
    # Basso di proposito: i comandi lunghi passano da ssh_job, non da ssh_exec.
    assert cfg.command_timeout_s == 60
    assert cfg.max_output_chars == 10_000
    assert cfg.keepalive_interval_s == 30
    assert cfg.idle_close_s == 300
    assert cfg.max_transfer_bytes == 50 * 1024 * 1024


def test_host_defaults():
    host = SshHostConfig(alias="prod", host="example.com", username="deploy")
    assert host.port == 22
    assert host.description == ""
    # L'enforcement e known_hosts: qui l'impronta e solo per la UI, e parte vuota.
    assert host.host_key_fingerprint is None
    assert host.job_log_dir == "/tmp/jenny-jobs"
    # La chiave resta il default: e il modo che non lascia un segreto
    # riutilizzabile dentro config.json.
    assert host.auth == "key"
    assert host.password is None


def test_password_host_round_trips():
    host = SshHostConfig(
        alias="nas", host="192.168.1.10", username="jenny", auth="password", password="hunter2"
    )
    assert host.auth == "password"
    # La password deve restare in model_dump: e cosi che finisce in config.json.
    assert host.model_dump()["password"] == "hunter2"


@pytest.mark.parametrize("auth", ["", "totp", "keyboard-interactive", "KEY"])
def test_unknown_auth_mode_is_rejected(auth: str):
    """Un modo sconosciuto deve fallire subito, non silenziosamente cadere su key."""
    with pytest.raises(Exception):
        SshHostConfig(alias="x", host="h", username="u", auth=auth)


def test_password_never_appears_in_the_repr():
    """``repr=False`` non e cosmetico: e come i segreti restano fuori dai log.

    Un ``repr`` di un oggetto di config finisce in una riga di log o in un
    messaggio d'errore senza che nessuno lo decida esplicitamente. Il resto dei
    campi deve invece restare visibile, o il repr non serve piu a diagnosticare.
    """
    host = SshHostConfig(
        alias="nas", host="h", username="u", auth="password", password="s3gr3t0"
    )
    text = repr(host)
    assert "s3gr3t0" not in text
    # Il campo intero sparisce, non solo il valore ("auth='password'" resta,
    # ed e giusto: e il modo, non il segreto).
    assert "password=" not in text
    assert "nas" in text and "auth='password'" in text


def test_password_never_appears_in_the_repr_of_the_whole_config():
    """Anche annidata: si logga ``config``, non ``config.tools.ssh.hosts[0]``."""
    config = Config()
    config.tools.ssh.hosts = [
        SshHostConfig(
            alias="nas", host="h", username="u", auth="password", password="s3gr3t0"
        )
    ]
    assert "s3gr3t0" not in repr(config)


def test_wired_into_tools_config():
    tools = ToolsConfig()
    assert isinstance(tools.ssh, SshConfig)
    assert Config().tools.ssh.enable is False


def test_camel_case_aliases_load():
    # Base genera alias camelCase: un config.json puo usare l'una o l'altra forma.
    cfg = SshConfig.model_validate(
        {
            "enable": True,
            "connectTimeoutS": 5.0,
            "commandTimeoutS": 30,
            "maxOutputChars": 2_000,
            "keepaliveIntervalS": 0,
            "idleCloseS": 60,
            "maxTransferBytes": 1024,
            "hosts": [
                {
                    "alias": "nas",
                    "host": "192.168.1.10",
                    "username": "jenny",
                    "hostKeyFingerprint": "SHA256:abc",
                    "jobLogDir": "/var/tmp/jobs",
                    "auth": "password",
                    "password": "hunter2",
                }
            ],
        }
    )
    assert cfg.enable is True
    assert cfg.connect_timeout_s == 5.0
    assert cfg.command_timeout_s == 30
    assert cfg.keepalive_interval_s == 0  # 0 = keepalive disattivato
    assert len(cfg.hosts) == 1
    host = cfg.hosts[0]
    assert isinstance(host, SshHostConfig)
    assert host.alias == "nas"
    assert host.host_key_fingerprint == "SHA256:abc"
    assert host.job_log_dir == "/var/tmp/jobs"
    assert host.auth == "password"
    assert host.password == "hunter2"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("connect_timeout_s", 0.5),      # < 1.0
        ("connect_timeout_s", 61.0),     # > 60.0
        ("command_timeout_s", 0),        # < 1
        ("command_timeout_s", 301),      # > 300
        ("max_output_chars", 999),       # < 1_000
        ("max_output_chars", 50_001),    # > 50_000
        ("keepalive_interval_s", -1),    # < 0
        ("keepalive_interval_s", 301),   # > 300
        ("idle_close_s", 29),            # < 30
        ("max_transfer_bytes", 1023),    # < 1024
    ],
)
def test_bounds_enforced(field: str, value: object):
    with pytest.raises(Exception):
        SshConfig(**{field: value})


@pytest.mark.parametrize("port", [0, 65536])
def test_host_port_bounds_enforced(port: int):
    with pytest.raises(Exception):
        SshHostConfig(alias="x", host="h", username="u", port=port)
