"""Il preset del bridge di casa: i valori giusti e, soprattutto, modificabili.

Il preset esiste per togliere di mezzo la copia a mano di un URL lungo e la
scelta del tier sbagliato. Ma un preset che non si può più cambiare sarebbe un
valore cablato travestito: questi test pinnano sia i default sia la libertà di
sovrascriverli (nome, URL, token, tier).

Le stringhe di default sono in ucraino: la lingua di *questo* dispositivo, non
una scelta di localizzazione generale (quella resta in ``assets/i18n``).
"""

from __future__ import annotations

from jenny.config.schema import Config
from jenny.providers.factory import make_provider
from jenny.providers.presets import (
    DEFAULT_HOME_BRIDGE_BASE_URL,
    HOME_BRIDGE_DEFAULT_MODELS,
    HOME_BRIDGE_PRESET_ID,
    ProviderPreset,
    build_provider_config,
    get_preset,
    presets_payload,
    provider_presets,
)
from jenny.webui.settings_api import settings_payload


def _preset() -> ProviderPreset:
    preset = get_preset(HOME_BRIDGE_PRESET_ID)
    assert preset is not None
    return preset


def _config_with(provider_config) -> Config:
    config = Config()
    config.providers.providers.append(provider_config)
    config.agents.defaults.model = "codex"
    return config


# ── I default del preset ───────────────────────────────────────────────────


def test_the_preset_points_at_the_tailscale_bridge() -> None:
    preset = _preset()

    assert preset.api_base == DEFAULT_HOME_BRIDGE_BASE_URL
    # L'endpoint è privato e nominale: HTTPS con certificato valido, host
    # MagicDNS, e la porta del bridge.
    assert preset.api_base.startswith("https://")
    assert preset.api_base.endswith("/v1")
    assert ".ts.net" in preset.api_base
    assert ":8899" in preset.api_base


def test_the_preset_is_a_keyless_subscription_bridge() -> None:
    preset = _preset()

    assert preset.format == "openai_compat"
    # ``subscription`` è ciò che rende la chiave facoltativa nel factory; un
    # preset con ``api`` riporterebbe l'utente a inventarsi una chiave finta.
    assert preset.tier == "subscription"
    assert preset.key_required is False


def test_the_preset_names_the_models_the_bridge_exposes() -> None:
    preset = _preset()

    assert preset.models == HOME_BRIDGE_DEFAULT_MODELS
    assert set(preset.models) == {"codex", "claude", "gemini"}


def test_an_unknown_preset_id_is_not_an_error() -> None:
    assert get_preset("nope") is None


def test_the_preset_payload_is_json_shaped_for_the_ui() -> None:
    payload = presets_payload()

    assert len(payload) == len(provider_presets())
    first = payload[0]
    assert first["id"] == HOME_BRIDGE_PRESET_ID
    assert first["api_base"] == DEFAULT_HOME_BRIDGE_BASE_URL
    assert first["tier"] == "subscription"
    assert first["key_required"] is False
    assert first["models"] == list(HOME_BRIDGE_DEFAULT_MODELS)


# ── Il preset resta modificabile ───────────────────────────────────────────


def test_the_preset_builds_a_complete_provider_config() -> None:
    config = build_provider_config(_preset())

    assert config.name == "Домашній міст (Tailscale)"
    assert config.format == "openai_compat"
    assert config.api_base == DEFAULT_HOME_BRIDGE_BASE_URL
    assert config.tier == "subscription"
    assert config.api_key is None


def test_overrides_win_over_the_preset_defaults() -> None:
    config = build_provider_config(
        _preset(),
        name="Міст (інша адреса)",
        api_base="https://other-host.ts.net:1234/v1",
        api_key="bridge-token",
        tier="local",
    )

    assert config.name == "Міст (інша адреса)"
    assert config.api_base == "https://other-host.ts.net:1234/v1"
    assert config.api_key == "bridge-token"
    assert config.tier == "local"


def test_blank_overrides_keep_the_preset_defaults() -> None:
    # Un campo lasciato in bianco nella UI non deve cancellare l'URL di default:
    # è la differenza fra "non l'ho toccato" e "l'ho svuotato".
    config = build_provider_config(_preset(), name="   ", api_base="", api_key="", tier="")

    assert config.name == "Домашній міст (Tailscale)"
    assert config.api_base == DEFAULT_HOME_BRIDGE_BASE_URL
    assert config.api_key is None
    assert config.tier == "subscription"


def test_an_unknown_tier_falls_back_to_the_preset_tier() -> None:
    config = build_provider_config(_preset(), tier="free-lunch")

    assert config.tier == "subscription"


# ── Il profilo costruito avvia davvero, con o senza token ──────────────────


def test_the_bridge_profile_starts_without_a_token() -> None:
    provider = make_provider(_config_with(build_provider_config(_preset())))

    assert provider.api_base == DEFAULT_HOME_BRIDGE_BASE_URL
    assert provider._base_url() == DEFAULT_HOME_BRIDGE_BASE_URL
    # Senza token si manda il segnaposto che i bridge ignorano: la richiesta
    # parte lo stesso, invece di essere rifiutata all'avvio.
    assert provider._api_key_for_client == "no-key"


def test_the_bridge_profile_uses_the_token_when_given() -> None:
    provider = make_provider(
        _config_with(build_provider_config(_preset(), api_key="bridge-token"))
    )

    assert provider._api_key_for_client == "bridge-token"


# ── Il payload delle impostazioni porta i preset alla UI ───────────────────


def test_settings_payload_offers_the_presets(tmp_path, monkeypatch) -> None:
    from jenny.config.loader import save_config
    from jenny.runtime.context import get_runtime_context

    config_path = tmp_path / "config.json"
    save_config(Config.model_validate({"agents": {"defaults": {"model": "codex"}}}), config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)

    payload = settings_payload()

    presets = payload["provider_presets"]
    assert [p["id"] for p in presets] == [HOME_BRIDGE_PRESET_ID]
    assert presets[0]["api_base"] == DEFAULT_HOME_BRIDGE_BASE_URL
