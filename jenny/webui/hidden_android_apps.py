"""Persisted list of Android app packages hidden from the launcher.

UI-only launcher metadata, scoped to the active jenny instance data
directory. It deliberately does not modify agent sessions or the main config,
and follows a read/normalize/atomic-write pattern.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.config.paths import get_webui_dir
from jenny.utils.path import atomic_write

HIDDEN_ANDROID_APPS_SCHEMA_VERSION = 1
_MAX_STATE_FILE_BYTES = 256 * 1024
_MAX_PACKAGES = 2_000
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.]{1,255}$")


def hidden_android_apps_path() -> Path:
    return get_webui_dir() / "hidden-android-apps.json"


def default_hidden_android_apps() -> dict[str, Any]:
    return {
        "schema_version": HIDDEN_ANDROID_APPS_SCHEMA_VERSION,
        "packages": [],
        "updated_at": None,
    }


def _clean_packages(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value[:_MAX_PACKAGES]:
        if not isinstance(item, str):
            continue
        pkg = item.strip()
        if not pkg or pkg in seen or _PACKAGE_RE.match(pkg) is None:
            continue
        seen.add(pkg)
        out.append(pkg)
    return out


def normalize_hidden_android_apps(raw: Any) -> dict[str, Any]:
    """Return a schema-v1 hidden-apps state from any older/partial input."""
    if not isinstance(raw, dict):
        raw = {}
    state = default_hidden_android_apps()
    state["packages"] = _clean_packages(raw.get("packages"))
    updated_at = raw.get("updated_at")
    state["updated_at"] = updated_at if isinstance(updated_at, str) else None
    return state


def read_hidden_android_apps() -> dict[str, Any]:
    path = hidden_android_apps_path()
    if not path.is_file():
        return default_hidden_android_apps()
    try:
        if path.stat().st_size > _MAX_STATE_FILE_BYTES:
            logger.warning("hidden android apps state too large, ignoring: {}", path)
            return default_hidden_android_apps()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read hidden android apps failed {}: {}", path, e)
        return default_hidden_android_apps()
    return normalize_hidden_android_apps(raw)


def write_hidden_android_apps(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_hidden_android_apps(raw)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise ValueError("hidden android apps state is too large")

    path = hidden_android_apps_path()
    atomic_write(path, encoded + b"\n")
    return state
