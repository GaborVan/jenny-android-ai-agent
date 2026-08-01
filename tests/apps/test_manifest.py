"""Tests for Jenny App manifest loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

from jenny.apps.manifest import (
    ACTION_NAME_RE,
    COLLECTION_RE,
    SLUG_RE,
    action_param_schema,
    find_app,
    load_app,
    scan_apps,
)

VALID_MANIFEST = {
    "name": "Piante",
    "description": "Monitoraggio piante",
    "icon": "ti-plant",
    "server": {"baseUrl": "http://192.168.1.50:8080", "auth": {"secretRef": "piante_token"}},
    "actions": [
        {"name": "lista_piante", "description": "Elenco", "kind": "http",
         "method": "GET", "path": "/plants"},
        {"name": "umidita", "description": "Umidità", "kind": "http",
         "method": "GET", "path": "/plants/{id}/humidity",
         "params": {"id": {"type": "string"}}, "required": ["id"]},
        {"name": "annota", "description": "Annota", "kind": "storage",
         "op": "append", "collection": "cure",
         "params": {"nota": {"type": "string"}}, "required": ["nota"]},
    ],
}


def _write_app(root: Path, slug: str, manifest) -> Path:
    app_dir = root / "apps" / slug
    app_dir.mkdir(parents=True, exist_ok=True)
    content = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (app_dir / "app.json").write_text(content, encoding="utf-8")
    return app_dir


class TestLoadApp:
    def test_valid_manifest(self, tmp_path):
        app_dir = _write_app(tmp_path, "piante", VALID_MANIFEST)
        app = load_app(app_dir)
        assert not app.broken
        assert app.manifest.name == "Piante"
        assert app.manifest.icon == "ti-plant"
        assert app.manifest.server_base_url == "http://192.168.1.50:8080"
        assert app.manifest.server_auth == {"secretRef": "piante_token"}
        assert [a.name for a in app.manifest.actions] == ["lista_piante", "umidita", "annota"]
        assert app.manifest.actions[2].op == "append"
        assert app.manifest.actions[2].collection == "cure"

    def test_malformed_json_is_broken_not_raised(self, tmp_path):
        app_dir = _write_app(tmp_path, "rotta", "{not json")
        app = load_app(app_dir)
        assert app.broken
        assert "invalid JSON" in app.error

    def test_missing_manifest(self, tmp_path):
        app_dir = tmp_path / "apps" / "vuota"
        app_dir.mkdir(parents=True)
        app = load_app(app_dir)
        assert app.broken
        assert "app.json is missing" in app.error

    def test_invalid_slug(self, tmp_path):
        app_dir = _write_app(tmp_path, "Bad_Slug", VALID_MANIFEST)
        app = load_app(app_dir)
        assert app.broken
        assert "slug" in app.error

    def test_missing_fields(self, tmp_path):
        app_dir = _write_app(tmp_path, "incompleta", {"name": "X", "actions": []})
        app = load_app(app_dir)
        assert app.broken
        assert "'description' is required" in app.error

    def test_bad_action_kind(self, tmp_path):
        manifest = dict(VALID_MANIFEST)
        manifest["actions"] = [
            {"name": "magica", "description": "x", "kind": "magic"},
        ]
        app = load_app(_write_app(tmp_path, "kind", manifest))
        assert app.broken
        assert "'kind' must be 'storage' or 'http'" in app.error

    def test_duplicate_action_names(self, tmp_path):
        manifest = dict(VALID_MANIFEST)
        manifest["actions"] = [
            {"name": "dup", "description": "a", "kind": "storage",
             "op": "query", "collection": "c"},
            {"name": "dup", "description": "b", "kind": "storage",
             "op": "append", "collection": "c"},
        ]
        app = load_app(_write_app(tmp_path, "doppioni", manifest))
        assert app.broken
        assert "duplicate action names" in app.error

    def test_http_requires_server(self, tmp_path):
        manifest = {
            "name": "X", "description": "y",
            "actions": [{"name": "fetchit", "description": "x", "kind": "http",
                         "method": "GET", "path": "/x"}],
        }
        app = load_app(_write_app(tmp_path, "senza-server", manifest))
        assert app.broken
        assert "requires a top-level 'server.baseUrl'" in app.error

    def test_placeholder_must_be_declared(self, tmp_path):
        manifest = dict(VALID_MANIFEST)
        manifest["actions"] = [
            {"name": "buco", "description": "x", "kind": "http",
             "method": "GET", "path": "/x/{id}"},
        ]
        app = load_app(_write_app(tmp_path, "buchi", manifest))
        assert app.broken
        assert "placeholders" in app.error


class TestScanApps:
    def test_scan_skips_files_and_dotdirs(self, tmp_path):
        _write_app(tmp_path, "buona", VALID_MANIFEST)
        (tmp_path / "apps" / ".nascosta").mkdir()
        (tmp_path / "apps" / "file.txt").write_text("x")
        apps = scan_apps(tmp_path)
        assert [a.slug for a in apps] == ["buona"]

    def test_scan_missing_root(self, tmp_path):
        assert scan_apps(tmp_path) == []

    def test_scan_includes_broken(self, tmp_path):
        _write_app(tmp_path, "buona", VALID_MANIFEST)
        _write_app(tmp_path, "rotta", "{nope")
        apps = scan_apps(tmp_path)
        assert [(a.slug, a.broken) for a in apps] == [("buona", False), ("rotta", True)]

    def test_find_app(self, tmp_path):
        _write_app(tmp_path, "buona", VALID_MANIFEST)
        assert find_app(tmp_path, "buona").slug == "buona"
        assert find_app(tmp_path, "manca") is None
        assert find_app(tmp_path, "../evil") is None


class TestSchemaAndRegexParity:
    def test_action_param_schema(self, tmp_path):
        app = load_app(_write_app(tmp_path, "piante", VALID_MANIFEST))
        schema = action_param_schema(app.manifest.actions[1])
        assert schema == {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        }

    def test_regexes_match_skill_validator(self):
        """The runtime and the app-creator validator must agree on names."""
        validator = (
            Path(__file__).resolve().parents[2]
            / "jenny" / "skills" / "app-creator" / "scripts" / "validate_app.py"
        ).read_text(encoding="utf-8")
        assert SLUG_RE.pattern in validator
        assert ACTION_NAME_RE.pattern in validator
        assert COLLECTION_RE.pattern in validator
