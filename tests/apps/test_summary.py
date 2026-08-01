"""Tests for the agent-context apps summary."""

from __future__ import annotations

import json

from jenny.apps.summary import build_apps_summary

MANIFEST = {
    "name": "Note",
    "description": "Note veloci",
    "actions": [
        {"name": "add_note", "description": "Aggiunge", "kind": "storage",
         "op": "append", "collection": "notes",
         "params": {"testo": {"type": "string"}}, "required": ["testo"]},
    ],
}


def _write_app(workspace, slug="note", manifest=MANIFEST):
    app_dir = workspace / "apps" / slug
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return app_dir


class TestBuildAppsSummary:
    def test_empty_workspace_is_empty_string(self, tmp_path):
        assert build_apps_summary(tmp_path) == ""

    def test_app_line_has_name_tools_and_data_path(self, tmp_path):
        _write_app(tmp_path)
        summary = build_apps_summary(tmp_path)
        assert "**Note** (`note`)" in summary
        assert "`note_add_note`" in summary
        assert "`apps/note/data/`" in summary
        assert "AGENT.md" not in summary

    def test_agent_md_referenced_by_path_only(self, tmp_path):
        app_dir = _write_app(tmp_path)
        (app_dir / "AGENT.md").write_text("Il basilico va annaffiato.", encoding="utf-8")
        summary = build_apps_summary(tmp_path)
        assert "`apps/note/AGENT.md`" in summary
        # Progressive disclosure: the content itself must never be inlined.
        assert "basilico" not in summary

    def test_broken_app_line(self, tmp_path):
        app_dir = tmp_path / "apps" / "rotta"
        app_dir.mkdir(parents=True)
        (app_dir / "app.json").write_text("{broken", encoding="utf-8")
        summary = build_apps_summary(tmp_path)
        assert "**rotta** — BROKEN" in summary
        assert "apps/rotta/app.json" in summary
