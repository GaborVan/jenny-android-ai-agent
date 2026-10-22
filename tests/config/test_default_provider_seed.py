"""Il provider di casa seminato al primo avvio.

Chi installa l'app su un telefono nuovo deve poter parlare subito, senza
copiare a mano l'URL del bridge o inventarsi una api_key: queste prove pinnano
il default **e** i due limiti che lo rendono innocuo — non sovrascrive provider
esistenti, e non risuscita un provider cancellato.

Le stringhe di default sono in ucraino: la lingua di *questo* dispositivo, come
per il preset (``jenny.providers.presets``).
"""

from __future__ import annotations

import json
from pathlib import Path

from jenny.config.bootstrap import (
    DEFAULT_PROVIDER_MODEL,
    DEFAULT_PROVIDER_NAME,
    ensure_minimal_config,
)
from jenny.config.loader import load_config
from jenny.providers.factory import make_provider
from jenny.providers.presets import DEFAULT_HOME_BRIDGE_BASE_URL


def _written(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


# ── (a) su un config pulito il provider viene creato ───────────────────────


def test_a_provider_is_created_on_a_fresh_config(tmp_path: Path) -> None:
    ensure_minimal_config(tmp_path)

    config = load_config(tmp_path / "config.json")

    assert len(config.providers.providers) == 1
    assert config.providers.providers[0].name == DEFAULT_PROVIDER_NAME


def test_the_created_provider_is_the_active_one(tmp_path: Path) -> None:
    ensure_minimal_config(tmp_path)

    config = load_config(tmp_path / "config.json")

    assert config.providers.default == DEFAULT_PROVIDER_NAME
    assert config.get_active_provider().name == DEFAULT_PROVIDER_NAME


def test_the_default_model_points_at_the_bridge(tmp_path: Path) -> None:
    """Senza modello scelto l'agente userebbe una stringa vuota: il bridge non
    saprebbe cosa servire. Il seed scrive ``codex``, che è quello che espone."""
    ensure_minimal_config(tmp_path)

    config = load_config(tmp_path / "config.json")

    assert config.agents.defaults.model == DEFAULT_PROVIDER_MODEL == "codex"


def test_a_seeded_config_can_build_a_provider_without_an_api_key(tmp_path: Path) -> None:
    """End-to-end: il config appena scritto regge ``make_provider``.

    È il punto del tier ``local``: prima di ``ProviderConfig.tier`` un provider
    senza chiave veniva rifiutato, e questo default sarebbe stato inutilizzabile
    fino al primo giro nelle impostazioni."""
    ensure_minimal_config(tmp_path)
    config = load_config(tmp_path / "config.json")

    provider = make_provider(config)

    assert provider.provider_name == DEFAULT_PROVIDER_NAME


# ── (b) se un provider c'è già, non si tocca nulla ─────────────────────────


def test_an_existing_provider_is_left_alone(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    existing = {
        "providers": {
            "providers": [
                {
                    "name": "Il mio provider",
                    "format": "openai_compat",
                    "apiBase": "https://example.invalid/v1",
                    "apiKey": "sk-mine",
                    "tier": "api",
                }
            ],
            "default": "Il mio provider",
        },
        "agents": {"defaults": {"model": "gpt-4o"}},
    }
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    ensure_minimal_config(tmp_path)

    data = _written(tmp_path)
    assert [p["name"] for p in data["providers"]["providers"]] == ["Il mio provider"]
    assert data["providers"]["default"] == "Il mio provider"
    # Anche il modello resta quello scelto dall'utente.
    assert data["agents"]["defaults"]["model"] == "gpt-4o"


def test_a_deleted_provider_is_not_resurrected(tmp_path: Path) -> None:
    """Cancellare il profilo lascia la chiave ``providers`` con lista vuota:
    quella è una decisione, non un config da riempire."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"providers": {"providers": [], "default": None}}),
        encoding="utf-8",
    )

    ensure_minimal_config(tmp_path)

    data = _written(tmp_path)
    assert data["providers"]["providers"] == []
    assert data["providers"]["default"] is None
    # Nemmeno il modello viene toccato: nessun seme, nessun effetto collaterale.
    assert "model" not in data.get("agents", {}).get("defaults", {})


def test_a_legacy_config_without_a_providers_key_gets_the_seed(tmp_path: Path) -> None:
    """Un config scritto prima che il seed esistesse non conosce ``providers``:
    è il caso "utente nuovo con file vecchio", e va coperto."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"gateway": {"host": "127.0.0.1"}}),
        encoding="utf-8",
    )

    ensure_minimal_config(tmp_path)

    config = load_config(config_path)
    assert config.providers.default == DEFAULT_PROVIDER_NAME


def test_seeding_twice_writes_once(tmp_path: Path) -> None:
    ensure_minimal_config(tmp_path)
    first = (tmp_path / "config.json").read_text(encoding="utf-8")

    ensure_minimal_config(tmp_path)

    assert (tmp_path / "config.json").read_text(encoding="utf-8") == first


# ── (c) i valori dei campi sono quelli giusti ──────────────────────────────


def test_the_seeded_provider_fields_are_the_home_bridge(tmp_path: Path) -> None:
    ensure_minimal_config(tmp_path)

    provider = load_config(tmp_path / "config.json").providers.providers[0]

    assert provider.format == "openai_compat"
    assert provider.api_base == DEFAULT_HOME_BRIDGE_BASE_URL
    assert provider.api_base.startswith("https://")
    assert provider.api_base.endswith("/v1")
    assert ".ts.net" in provider.api_base
    # ``local``: endpoint di casa, nessuna chiave pretesa dal factory.
    assert provider.tier == "local"
    assert provider.api_key is None


def test_the_seeded_provider_matches_the_ui_preset_url(tmp_path: Path) -> None:
    """Il seme e il preset della UI devono puntare allo stesso posto: due URL
    che divergono silenziosamente sono due bug travestiti da feature."""
    ensure_minimal_config(tmp_path)

    provider = load_config(tmp_path / "config.json").providers.providers[0]

    from jenny.providers.presets import HOME_BRIDGE_PRESET_ID, get_preset

    preset = get_preset(HOME_BRIDGE_PRESET_ID)
    assert preset is not None
    assert provider.api_base == preset.api_base
    assert provider.name == preset.name
