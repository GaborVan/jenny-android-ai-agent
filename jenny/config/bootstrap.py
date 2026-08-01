import contextlib
import json
import secrets
from pathlib import Path

_TOKEN_ISSUE_SECRET_KEYS = ("token_issue_secret", "tokenIssueSecret")


def ensure_minimal_config(workspace_path: Path) -> None:
    """Create a minimal gateway config in the workspace if none exists.

    Also backfills a per-install ``websocket.token_issue_secret`` when none is
    configured. Without it, ``/webui/bootstrap`` falls back to a loopback-only
    check — but Android does not isolate loopback TCP sockets between apps,
    so any app on the device could mint a fully privileged API token. The
    secret is generated once and persisted into ``config.json`` (workspace
    storage, private to this app's Android UID), so it survives restarts and
    is never sent back out over the network by this function.
    """
    config_path = workspace_path / "config.json"

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        minimal = {
            "gateway": {"host": "127.0.0.1"},
            "websocket": {
                "enabled": True,
                "token_issue_secret": secrets.token_urlsafe(32),
            },
        }
        config_path.write_text(json.dumps(minimal, indent=2), encoding="utf-8")
        _restrict_permissions(config_path)
        return

    _restrict_permissions(config_path)
    _backfill_token_issue_secret(config_path)


def _backfill_token_issue_secret(config_path: Path) -> None:
    """Add a generated ``websocket.token_issue_secret`` to an existing config.

    No-ops if the config already has a non-empty ``token`` or
    ``token_issue_secret`` — an explicit operator choice is never overwritten.
    """
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return

    websocket = data.get("websocket")
    if not isinstance(websocket, dict):
        websocket = {}

    existing_secret = ""
    for key in _TOKEN_ISSUE_SECRET_KEYS:
        existing_secret = str(websocket.get(key) or "").strip()
        if existing_secret:
            break
    existing_token = str(websocket.get("token") or "").strip()
    if existing_secret or existing_token:
        return

    websocket["token_issue_secret"] = secrets.token_urlsafe(32)
    data["websocket"] = websocket
    try:
        config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return
    _restrict_permissions(config_path)


def _restrict_permissions(config_path: Path) -> None:
    """Best-effort: keep config.json (holds the bootstrap secret) unreadable
    by other local users. A no-op quirk on some filesystems (e.g. FAT); the
    real isolation boundary on Android is the per-app UID sandbox."""
    with contextlib.suppress(OSError):
        config_path.chmod(0o600)
