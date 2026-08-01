"""Test per jenny.config.bootstrap.ensure_minimal_config.

Copre: creazione del config minimale quando assente, idempotenza quando già
presente, mancata sovrascrittura di un config esistente (incluso il caso in
cui l'operatore ha già scelto un secret/token esplicito), e che il contenuto
prodotto sia effettivamente valido e ricaricabile dal loader.
"""

from __future__ import annotations

import json
import stat

from jenny.config.bootstrap import ensure_minimal_config
from jenny.config.loader import load_config


def test_creates_minimal_config_when_absent(tmp_path):
    config_path = tmp_path / "config.json"
    assert not config_path.exists()

    ensure_minimal_config(tmp_path)

    assert config_path.exists()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["gateway"]["host"] == "127.0.0.1"
    assert data["websocket"]["enabled"] is True
    assert data["websocket"]["token_issue_secret"]


def test_creates_parent_directories(tmp_path):
    workspace = tmp_path / "nested" / "workspace"
    assert not workspace.exists()

    ensure_minimal_config(workspace)

    assert (workspace / "config.json").exists()


def test_created_secret_is_random_per_workspace(tmp_path):
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    ws1.mkdir()
    ws2.mkdir()

    ensure_minimal_config(ws1)
    ensure_minimal_config(ws2)

    secret1 = json.loads((ws1 / "config.json").read_text())["websocket"]["token_issue_secret"]
    secret2 = json.loads((ws2 / "config.json").read_text())["websocket"]["token_issue_secret"]
    assert secret1 != secret2


def test_restricts_permissions_on_created_config(tmp_path):
    config_path = tmp_path / "config.json"
    ensure_minimal_config(tmp_path)

    mode = stat.S_IMODE(config_path.stat().st_mode)
    assert mode == 0o600


def test_idempotent_when_config_already_minimal(tmp_path):
    ensure_minimal_config(tmp_path)
    config_path = tmp_path / "config.json"
    first_write = config_path.read_text(encoding="utf-8")

    ensure_minimal_config(tmp_path)

    assert config_path.read_text(encoding="utf-8") == first_write


def test_does_not_overwrite_existing_config_content(tmp_path):
    config_path = tmp_path / "config.json"
    existing = {"gateway": {"host": "0.0.0.0", "port": 9999}, "extractDocumentText": False}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    ensure_minimal_config(tmp_path)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["gateway"]["host"] == "0.0.0.0"
    assert data["gateway"]["port"] == 9999


def test_backfills_token_issue_secret_on_existing_config_without_one(tmp_path):
    config_path = tmp_path / "config.json"
    existing = {"gateway": {"host": "127.0.0.1"}}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    ensure_minimal_config(tmp_path)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["websocket"]["token_issue_secret"]


def test_does_not_overwrite_explicit_token_issue_secret(tmp_path):
    config_path = tmp_path / "config.json"
    existing = {"websocket": {"token_issue_secret": "operator-chosen-secret"}}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    ensure_minimal_config(tmp_path)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["websocket"]["token_issue_secret"] == "operator-chosen-secret"


def test_does_not_overwrite_explicit_token(tmp_path):
    """Un ``token`` esplicito (non ``token_issue_secret``) blocca comunque il backfill."""
    config_path = tmp_path / "config.json"
    existing = {"websocket": {"token": "static-operator-token"}}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    ensure_minimal_config(tmp_path)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "token_issue_secret" not in data["websocket"]
    assert data["websocket"]["token"] == "static-operator-token"


def test_camelcase_token_issue_secret_alias_blocks_backfill(tmp_path):
    """La chiave camelCase ``tokenIssueSecret`` conta come già impostata."""
    config_path = tmp_path / "config.json"
    existing = {"websocket": {"tokenIssueSecret": "camel-secret"}}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    ensure_minimal_config(tmp_path)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "token_issue_secret" not in data["websocket"]


def test_ignores_malformed_existing_config_without_raising(tmp_path):
    """Un config.json corrotto non deve far esplodere il bootstrap: no-op silenzioso."""
    config_path = tmp_path / "config.json"
    config_path.write_text("{not valid json", encoding="utf-8")

    ensure_minimal_config(tmp_path)

    # Non tocca il contenuto corrotto (nessuna sovrascrittura su parse error).
    assert config_path.read_text(encoding="utf-8") == "{not valid json"


def test_ignores_existing_config_that_is_a_json_list(tmp_path):
    """Un config.json il cui contenuto non è un dict viene ignorato (no-op)."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    ensure_minimal_config(tmp_path)

    assert json.loads(config_path.read_text(encoding="utf-8")) == [1, 2, 3]


def test_created_config_is_reloadable_by_loader(tmp_path):
    """Il config minimale prodotto deve essere un config valido per il loader reale."""
    ensure_minimal_config(tmp_path)
    config_path = tmp_path / "config.json"

    config = load_config(config_path)

    assert config.gateway.host == "127.0.0.1"
    assert config.websocket["enabled"] is True
    assert config.websocket["token_issue_secret"]


def test_backfilled_config_is_reloadable_by_loader(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"gateway": {"host": "127.0.0.1"}}), encoding="utf-8")

    ensure_minimal_config(tmp_path)
    config = load_config(config_path)

    assert config.websocket["token_issue_secret"]
