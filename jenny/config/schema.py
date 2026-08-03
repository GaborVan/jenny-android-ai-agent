"""Configuration schema using Pydantic."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from jenny.config.tool_schemas import (
    AndroidWebToolsConfig,
    DiagnosticsToolConfig,
    FileToolsConfig,
    IntrospectToolConfig,
    LocationConfig,
    MyToolConfig,
    PythonExecConfig,
)
from jenny.config_base import Base
from jenny.cron.types import CronSchedule
from jenny.pydantic_compat import (
    AliasChoices,
    BaseSettings,
    Field,
    model_validator,
)
from jenny.snapshot.engine import DEFAULT_EXCLUDE_GLOBS


class DreamConfig(Base):
    """Dream memory consolidation configuration."""

    _HOUR_MS = 3_600_000

    enabled: bool = True  # Register the periodic Dream consolidation job on startup
    interval_h: int = Field(default=2, ge=1)  # Every 2 hours by default

    def build_schedule(self) -> CronSchedule:
        """Build the runtime schedule from the configured interval."""
        return CronSchedule(kind="every", every_ms=self.interval_h * self._HOUR_MS)

    def describe_schedule(self) -> str:
        """Return a human-readable summary for logs and startup output."""
        hours = self.interval_h
        return f"every {hours}h"


class AgentDefaults(Base):
    """Default agent configuration."""

    model: str = ""
    # Tetto per singola risposta. Sui reasoning model il thinking pesa su questo
    # stesso budget: con 8192 un turno che pianifica a lungo lo consumava tutto
    # prima di dire qualcosa. 16384 lascia margine restando dentro la finestra
    # anche coi prompt più grossi osservati (~38k su 65536).
    max_tokens: int = 16384
    context_window_tokens: int = 65536
    context_block_limit: int | None = None
    temperature: float = 0.1
    # Esplicito invece di lasciare il default del provider: un reasoning model a
    # briglia sciolta consuma tutto il budget di output in ragionamento su un
    # compito aperto. "medium" limita il thinking senza appiattirlo.
    reasoning_effort: str | None = "medium"
    max_tool_iterations: int = 200
    max_concurrent_subagents: int = Field(default=1, ge=1)
    max_tool_result_chars: int = 16000
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    tool_hint_max_length: int = Field(default=40, ge=20, le=500)
    # Stringa vuota = auto: timezone del dispositivo su Android, altrimenti
    # UTC. Risolta una volta per load in ``loader._resolve_default_timezone``.
    timezone: str = ""
    bot_name: str = "Jenny"
    bot_icon: str = "✿"
    language: str = "it"
    tool_choice: Literal["auto", "any", "none", "required"] = Field(
        default="auto",
        validation_alias=AliasChoices("toolChoice", "tool_choice"),
    )
    disabled_skills: list[str] = Field(default_factory=list)
    session_ttl_minutes: int = Field(
        default=15,
        ge=0,
        validation_alias=AliasChoices("idleCompactAfterMinutes"),
        serialization_alias="idleCompactAfterMinutes",
    )
    max_messages: int = Field(default=120, ge=0)
    consolidation_ratio: float = Field(default=0.5, ge=0.1, le=0.95)
    dream: DreamConfig = Field(default_factory=DreamConfig)
    model_preset: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelPreset", "model_preset"),
    )


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider configured by the user."""

    name: str
    format: Literal["openai_compat", "anthropic"]
    api_key: str | None = Field(default=None, repr=False)
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    extra_query: dict[str, str] | None = None
    api_type: Literal["auto", "chat_completions", "responses"] = "auto"


class ProvidersConfig(Base):
    """User-defined LLM providers."""

    providers: list[ProviderConfig] = Field(default_factory=list)
    default: str | None = None


class HeartbeatConfig(Base):
    """Heartbeat service configuration (now backed by cron)."""

    enabled: bool = True
    interval_s: int = Field(default=30 * 60, ge=1)  # 30 minutes
    keep_recent_messages: int = 8


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class ToolsConfig(Base):
    """Tools configuration.

    I tipi dei sub-config dei tool sono importati direttamente da
    ``config.tool_schemas`` (modulo leggero, nessun ciclo): niente più
    forward-ref / ``model_rebuild`` / risoluzione lazy.
    """

    android_web: AndroidWebToolsConfig = Field(
        default_factory=AndroidWebToolsConfig,
        validation_alias=AliasChoices("androidWeb", "android_web"),
    )
    python_exec: PythonExecConfig = Field(
        default_factory=PythonExecConfig,
        validation_alias=AliasChoices("pythonExec", "python_exec"),
    )
    file: FileToolsConfig = Field(default_factory=FileToolsConfig)
    location: LocationConfig = Field(default_factory=LocationConfig)
    my: MyToolConfig = Field(default_factory=MyToolConfig)
    introspect: IntrospectToolConfig = Field(default_factory=IntrospectToolConfig)
    diagnostics: DiagnosticsToolConfig = Field(default_factory=DiagnosticsToolConfig)
    # NB: canonical home = ``Config.security`` (SecurityConfig). Questo campo
    # resta su ToolsConfig come **mirror** sincronizzato (il tool-layer lo legge
    # via ``ctx.config.restrict_to_workspace``); il validator di ``Config`` lo
    # tiene allineato a ``security``. Non impostarlo a mano: usare ``security``.
    restrict_to_workspace: bool = True


class SecurityConfig(Base):
    """Policy di sicurezza a livello top-level (fonte canonica).

    ``restrict_to_workspace``: mantiene l'accesso dei tool dentro il workspace.
    ``ssrf_whitelist``: CIDR esentati dal blocco SSRF (es. ``["100.64.0.0/10"]``
    per Tailscale). Retro-compat: se un vecchio ``config.json`` porta questi campi
    sotto ``tools`` e non c'è ``security``, il validator di ``Config`` li migra qui.
    """

    restrict_to_workspace: bool = True
    ssrf_whitelist: list[str] = Field(default_factory=list)


class WikiConfig(Base):
    """Wiki configuration."""

    enabled: bool = True
    wikis_dir: str = "wikis"  # Relativo a workspace
    default_wiki: str = "main"
    extensions: list[str] = Field(default_factory=lambda: [
        "fenced_code",
        "tables",
        "toc",
        "wikilinks",
        "mermaid",
    ])


class WorkspaceConfig(Base):
    """Workspace file management configuration."""

    enabled: bool = True
    max_file_size: int = 1_000_000  # 1MB
    allow_delete: bool = True
    allow_write: bool = True


class AppsConfig(Base):
    """Jenny Apps configuration (workspace apps with typed actions)."""

    enabled: bool = True
    http_timeout_s: float = Field(default=20.0, ge=1.0, le=120.0)
    max_collection_bytes: int = 5_000_000


class SnapshotConfig(Base):
    """Configurazione del versioning locale del workspace (snapshot + backup).

    Gli snapshot sono creati automaticamente dal runtime (debounce su quiete,
    checkpoint pre-Dream, shutdown, safety giornaliero) senza coinvolgere
    l'LLM. ``pbkdf2_iterations`` governa la derivazione chiave del backup
    cifrato esportato.
    """

    enabled: bool = True
    scan_interval_minutes: int = Field(default=5, ge=1)
    quiet_minutes: int = Field(default=10, ge=1)
    daily_safety_snapshot: bool = True
    retention_recent: int = Field(default=20, ge=1)
    retention_thin_after_days: int = Field(default=30, ge=1)
    # Orizzonte massimo della storia in giorni (0 = per sempre). Gli ultimi
    # ``retention_recent`` snapshot restano comunque protetti dall'orizzonte.
    retention_max_age_days: int = Field(default=0, ge=0)
    # Il tetto rispecchia MAX_KDF_ITERATIONS del formato container (crypto.py).
    pbkdf2_iterations: int = Field(default=600_000, ge=100_000, le=10_000_000)
    # Unica fonte di verità: la costante del motore di snapshot (engine.py).
    exclude_globs: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXCLUDE_GLOBS)
    )


class TelegramConfig(Base):
    """Configurazione del canale Telegram (bot personale).

    Stato derivato, nessuna enum persistita:
    disabled → token presente ma unpaired (``pairing_code`` attivo) → paired.
    Il ``pairing_code`` è persistito così il pairing sopravvive ai riavvii del
    processo (frequenti su Android) e viene azzerato al pairing riuscito.
    """

    enabled: bool = False
    bot_token: str | None = Field(default=None, repr=False)
    bot_username: str | None = None
    paired_chat_id: str | None = None
    paired_username: str | None = None
    pairing_code: str | None = Field(default=None, repr=False)
    poll_timeout_s: int = Field(default=50, ge=1, le=300)


class ModelPresetConfig(Base):
    """Named model preset configuration."""
    label: str | None = None
    provider: str | None = Field(default=None, validation_alias=AliasChoices("provider"))
    model: str | None = None
    max_tokens: int | None = None
    context_window_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None


class Config(BaseSettings):
    """Root configuration for jenny."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    # Allegati non-immagine: di default vengono solo referenziati per path
    # (salvati in ``uploads/``) e letti on-demand dall'agente coi suoi tool,
    # senza iniettarne il testo nel contesto a ogni turno. Impostare a ``True``
    # per estrarre e inlinare subito il testo di PDF/documenti.
    extract_document_text: bool = False
    websocket: dict[str, Any] = Field(default_factory=dict)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    wiki: WikiConfig = Field(default_factory=WikiConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    apps: AppsConfig = Field(default_factory=AppsConfig)
    snapshots: SnapshotConfig = Field(default_factory=SnapshotConfig)
    model_presets: dict[str, ModelPresetConfig] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("modelPresets", "model_presets"),
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_security_fields(cls, data: Any) -> Any:
        """Retro-compat: sposta ``tools.{restrict_to_workspace,ssrf_whitelist}``
        legacy sotto ``security`` quando ``security`` non è dato esplicitamente."""
        if not isinstance(data, dict):
            return data
        tools = data.get("tools")
        if not isinstance(tools, dict):
            return data
        if "security" not in data:
            # Accetta sia snake_case sia l'alias camelCase (Base) presenti nei
            # config legacy: es. ``ssrf_whitelist`` o ``ssrfWhitelist``.
            aliases = {
                "restrict_to_workspace": ("restrict_to_workspace", "restrictToWorkspace"),
                "ssrf_whitelist": ("ssrf_whitelist", "ssrfWhitelist"),
            }
            migrated: dict[str, Any] = {}
            for field, keys in aliases.items():
                for key in keys:
                    if key in tools:
                        migrated[field] = tools[key]
                        break
            if migrated:
                data = {**data, "security": migrated}
        return data

    @model_validator(mode="after")
    def _sync_security_mirror(self) -> Config:
        """``security`` è canonico; ``tools`` ne è il mirror letto dal tool-layer."""
        self.tools.restrict_to_workspace = self.security.restrict_to_workspace
        return self

    @property
    def workspace_path(self) -> Path:
        """Get the fixed workspace path."""
        from jenny.config.paths import get_workspace_path
        return get_workspace_path()

    def get_active_provider(self) -> ProviderConfig:
        """Return the active provider config.

        Uses ``providers.default`` if set, otherwise the first provider in
        the list.  Raises ValueError if no provider is configured.
        """
        if self.providers.default:
            for p in self.providers.providers:
                if p.name == self.providers.default:
                    return p
        if self.providers.providers:
            return self.providers.providers[0]
        raise ValueError("No provider configured. Add one in settings or config.json.")
