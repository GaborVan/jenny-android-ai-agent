import contextlib
import json
import secrets
from pathlib import Path
from typing import Any

from jenny.utils.path import atomic_write

_TOKEN_ISSUE_SECRET_KEYS = ("token_issue_secret", "tokenIssueSecret")

#: Nome del provider seminato al primo avvio. Deve combaciare con quello del
#: preset della UI: chi apre le impostazioni riconosce lo stesso profilo, e
#: può modificarlo o cancellarlo come qualunque altro.
DEFAULT_PROVIDER_NAME = "Домашній міст (Tailscale)"

#: Il modello che il bridge espone per Codex. Scritto in ``agents.defaults``
#: solo se l'utente non ne ha già scelto uno.
DEFAULT_PROVIDER_MODEL = "codex"


def ensure_minimal_config(workspace_path: Path) -> None:
    """Create a minimal gateway config in the workspace if none exists.

    Also backfills a per-install ``websocket.token_issue_secret`` when none is
    configured. Without it, ``/webui/bootstrap`` falls back to a loopback-only
    check — but Android does not isolate loopback TCP sockets between apps,
    so any app on the device could mint a fully privileged API token. The
    secret is generated once and persisted into ``config.json`` (workspace
    storage, private to this app's Android UID), so it survives restarts and
    is never sent back out over the network by this function.

    Resta fuori dal funnel di :mod:`jenny.config.store`: gira **prima**
    dell'event loop del gateway, quando nessun altro scrittore esiste, quindi
    non serve il lock asincrono. Le scritture sono comunque atomiche, e
    lavorano sul JSON grezzo — così non toccano le chiavi che lo schema non
    conosce.
    """
    config_path = workspace_path / "config.json"

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        minimal: dict[str, Any] = {
            "gateway": {"host": "127.0.0.1"},
            "websocket": {
                "enabled": True,
                "token_issue_secret": secrets.token_urlsafe(32),
            },
        }
        _seed_default_provider(minimal)
        atomic_write(config_path, json.dumps(minimal, indent=2, ensure_ascii=False))
        restrict_config_permissions(config_path)
        return

    restrict_config_permissions(config_path)
    _backfill_token_issue_secret(config_path)
    _backfill_default_provider(config_path)


def home_bridge_provider_payload() -> dict[str, Any]:
    """Il provider del bridge di casa, in forma JSON con gli alias dello schema.

    Chiavi camelCase perché è quello che scrive ``save_config``: un file scritto
    a metà fra i due stili farebbe comparire i campi come "chiavi sconosciute"
    al primo salvataggio.
    """
    from jenny.providers.presets import DEFAULT_HOME_BRIDGE_BASE_URL

    return {
        "name": DEFAULT_PROVIDER_NAME,
        "format": "openai_compat",
        "apiBase": DEFAULT_HOME_BRIDGE_BASE_URL,
        # ``local``: l'endpoint è una macchina dell'utente dentro il tailnet. Il
        # factory non pretende una api_key per i tier ``local``/``subscription``,
        # quindi il provider funziona appena il dispositivo è nel tailnet.
        "tier": "local",
    }


def _seed_default_provider(data: dict[str, Any]) -> bool:
    """Aggiunge il provider di casa a un config che non ne conosce nessuno.

    Regola: si semina **solo quando la chiave ``providers`` è del tutto
    assente**. Un config già scritto dallo schema porta sempre la chiave, anche
    con la lista vuota — e una lista vuota è un utente che ha cancellato il
    profilo o non ne ha mai aggiunto uno: in entrambi i casi rimetterlo sarebbe
    una riapparizione, non un default. Così il seed copre la prima installazione
    (e i config legacy di chi non ha mai toccato le impostazioni) senza mai
    risuscitare un provider cancellato né sovrascrivere una scelta dell'utente.

    Ritorna ``True`` se ha modificato *data*.
    """
    if "providers" in data:
        return False

    data["providers"] = {
        "providers": [home_bridge_provider_payload()],
        "default": DEFAULT_PROVIDER_NAME,
    }

    agents = data.get("agents")
    if not isinstance(agents, dict):
        agents = {}
    defaults = agents.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    # Solo se l'utente non ha già scelto un modello: la scelta esplicita vince
    # sul default che portiamo noi.
    if not str(defaults.get("model") or "").strip():
        defaults["model"] = DEFAULT_PROVIDER_MODEL
    agents["defaults"] = defaults
    data["agents"] = agents
    return True


def _backfill_default_provider(config_path: Path) -> None:
    """Semina il provider di casa in un config esistente che non ne ha mai avuti.

    No-op se la chiave ``providers`` c'è già (anche vuota) o se il file non è
    leggibile: qui si sta solo *riempiendo un vuoto*, mai correggendo una scelta.
    """
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    if not _seed_default_provider(data):
        return
    try:
        atomic_write(config_path, json.dumps(data, indent=2, ensure_ascii=False))
    except OSError:
        return
    restrict_config_permissions(config_path)


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
        atomic_write(config_path, json.dumps(data, indent=2))
    except OSError:
        return
    restrict_config_permissions(config_path)


def restrict_config_permissions(config_path: Path) -> None:
    """Best-effort: keep config.json (holds the bootstrap secret) unreadable
    by other local users. A no-op quirk on some filesystems (e.g. FAT); the
    real isolation boundary on Android is the per-app UID sandbox.

    Va richiamata dopo *ogni* scrittura del file: ``atomic_write`` sostituisce
    l'inode, quindi i permessi del file precedente non si conservano da soli.
    Vale anche per il backup, che porta gli stessi segreti."""
    with contextlib.suppress(OSError):
        config_path.chmod(0o600)
