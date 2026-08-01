"""Config dataclasses for the built-in tools.

Queste classi vivevano accanto alle implementazioni dei tool, costringendo
``ToolsConfig`` a forward-ref + risoluzione lazy (``_lazy_default`` +
``model_rebuild`` + ``try/except ImportError``) per evitare cicli d'import.

Qui stanno in un modulo LEGGERO che importa solo ``Base`` + ``Field`` (nessuna
dipendenza dai moduli tool, pesanti). Così sia ``config/schema.py`` sia i moduli
tool importano da qui *verso il basso* — niente cicli, niente rebuild a runtime,
e un fallimento d'import è un errore rumoroso allo startup invece di essere
silenziato.

I moduli tool re-esportano la loro classe (``from jenny.config.tool_schemas
import PythonExecConfig``) così gli import storici continuano a funzionare.
"""

from __future__ import annotations

from jenny.config_base import Base
from jenny.pydantic_compat import Field


class PythonExecConfig(Base):
    """Python exec tool configuration."""

    enable: bool = True
    timeout: int = Field(default=60, ge=0)  # 0 = no limit
    max_output_chars: int = Field(default=10_000, ge=1000, le=50_000)
    allowed_modules: list[str] = Field(
        default_factory=lambda: [
            "os", "sys", "pathlib", "json", "re", "math", "datetime",
            "collections", "itertools", "functools", "typing",
            "io", "shutil", "glob", "hashlib",
            # Raw URL/HTTP clients (httpx, urllib) intentionally NOT allowlisted:
            # `import httpx` or `from urllib.request import urlopen` would let
            # guarded code hit loopback/link-local/RFC1918 targets (SSRF) or read
            # local files via file:// (LFI), bypassing both the SSRF policy and
            # the workspace policy. Outbound HTTP stays available only via the
            # http_get/http_post helpers (which call validate_url_target).
            # Re-add "httpx"/"urllib" here explicitly to opt back into raw access.
            "base64", "asyncio", "csv",
            "platform", "time", "struct", "textwrap", "unicodedata",
            "html", "xml", "dataclasses", "enum", "uuid",
        ]
    )
    blocked_modules: list[str] = Field(
        default_factory=lambda: [
            "subprocess", "pty", "shlex",
            "multiprocessing", "ctypes", "socket", "signal",
            "termios", "tty", "grp", "pwd", "resource",
            "syslog", "curses", "readline", "_thread", "fcntl",
        ]
    )


class FileToolsConfig(Base):
    """Filesystem tools configuration."""

    enable: bool = True  # built-in file tools on by default
    # Grant read-only access to jenny's own source so the agent can
    # inspect the framework it runs on (never writable).
    expose_package_source: bool = True


class MyToolConfig(Base):
    """Self-inspection tool configuration."""

    enable: bool = True
    allow_set: bool = False


class AndroidWebSearchConfig(Base):
    """Android WebView-backed search configuration."""

    search_engine: str = "bing"
    max_results: int = 5
    timeout: int = 30


class AndroidWebFetchConfig(Base):
    """Android WebView-backed fetch configuration."""

    max_chars: int = 50000


class AndroidWebToolsConfig(Base):
    """Android-only WebView web tools configuration."""

    enable: bool = True
    search: AndroidWebSearchConfig = Field(default_factory=AndroidWebSearchConfig)
    fetch: AndroidWebFetchConfig = Field(default_factory=AndroidWebFetchConfig)


class LocationConfig(Base):
    """Configurazione della posizione del dispositivo (solo Android).

    Sorgente primaria: GPS del telefono via ``LocationBridge`` nativo (fix
    last-known, gratis, iniettato nel contesto a ogni turno). Il tool
    ``get_location`` on-demand forza un fix fresco solo quando l'agente passa
    ``precise=true``. La posizione condivisa via Telegram fa da override
    per-canale con validità ``telegram_ttl_s`` (poi anche Telegram ricade sul
    GPS live).

    ``enable`` è il toggle utente (default ON), comunque gattato dal permesso
    runtime Android ``ACCESS_FINE_LOCATION``: se il permesso manca il bridge
    ritorna sempre ``None`` e non viene iniettato nulla.
    """

    enable: bool = True
    # Validità di una posizione condivisa via Telegram prima del fallback a GPS.
    telegram_ttl_s: int = Field(default=3600, ge=60)  # 1 h
    # Timeout del fix fresco on-demand (precise=true).
    fresh_timeout_s: int = Field(default=15, ge=1, le=60)


class IntrospectToolConfig(Base):
    """Source introspection tool configuration."""

    enable: bool = True


class DiagnosticsToolConfig(Base):
    """Diagnostics tool configuration."""

    enable: bool = True
