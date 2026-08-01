"""Tests for the shared action executor (resolve, validate, dispatch)."""

from __future__ import annotations

import json

import pytest

from jenny.apps.executor import AppActionError, execute_action
from jenny.apps.manifest import action_param_schema, find_app

MANIFEST = {
    "name": "Note",
    "description": "Note veloci",
    "actions": [
        {"name": "add_note", "description": "Aggiunge", "kind": "storage",
         "op": "append", "collection": "notes",
         "params": {"testo": {"type": "string"}}, "required": ["testo"]},
        {"name": "list_notes", "description": "Elenca", "kind": "storage",
         "op": "query", "collection": "notes"},
        {"name": "edit_note", "description": "Modifica", "kind": "storage",
         "op": "update", "collection": "notes",
         "params": {"testo": {"type": "string"}}},
        {"name": "del_note", "description": "Elimina", "kind": "storage",
         "op": "delete", "collection": "notes"},
    ],
}


def _write_app(workspace, manifest=MANIFEST):
    app_dir = workspace / "apps" / "note"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return app_dir


class TestResolveErrors:
    async def test_unknown_app_is_404(self, tmp_path):
        with pytest.raises(AppActionError) as exc:
            await execute_action(tmp_path, "manca", "x", {})
        assert exc.value.status == 404

    async def test_unknown_action_is_404(self, tmp_path):
        _write_app(tmp_path)
        with pytest.raises(AppActionError) as exc:
            await execute_action(tmp_path, "note", "nope", {})
        assert exc.value.status == 404
        assert "no action 'nope'" in str(exc.value)

    async def test_broken_app_is_409(self, tmp_path):
        app_dir = tmp_path / "apps" / "rotta"
        app_dir.mkdir(parents=True)
        (app_dir / "app.json").write_text("{broken", encoding="utf-8")
        with pytest.raises(AppActionError) as exc:
            await execute_action(tmp_path, "rotta", "x", {})
        assert exc.value.status == 409
        assert "broken" in str(exc.value)


class TestParamValidation:
    async def test_missing_required_param_is_400(self, tmp_path):
        _write_app(tmp_path)
        with pytest.raises(AppActionError) as exc:
            await execute_action(tmp_path, "note", "add_note", {})
        assert exc.value.status == 400
        assert "testo" in str(exc.value)

    async def test_unknown_param_is_rejected(self, tmp_path):
        _write_app(tmp_path)
        with pytest.raises(AppActionError) as exc:
            await execute_action(tmp_path, "note", "add_note", {"testo": "x", "extra": 1})
        assert exc.value.status == 400
        assert "unknown params: extra" in str(exc.value)

    async def test_wrong_type_is_400(self, tmp_path):
        _write_app(tmp_path)
        with pytest.raises(AppActionError) as exc:
            await execute_action(tmp_path, "note", "add_note", {"testo": 7})
        assert exc.value.status == 400

    async def test_params_must_be_object(self, tmp_path):
        _write_app(tmp_path)
        with pytest.raises(AppActionError):
            await execute_action(tmp_path, "note", "add_note", ["not", "a", "dict"])


class TestReservedParams:
    def _actions(self, tmp_path):
        _write_app(tmp_path)
        app = find_app(tmp_path, "note")
        assert app is not None and app.manifest is not None
        return {a.name: a for a in app.manifest.actions}

    def test_update_delete_get_required_id(self, tmp_path):
        actions = self._actions(tmp_path)
        for name in ("edit_note", "del_note"):
            schema = action_param_schema(actions[name])
            assert schema["properties"]["id"]["type"] == "string"
            assert "id" in schema["required"]

    def test_query_gets_optional_limit(self, tmp_path):
        actions = self._actions(tmp_path)
        schema = action_param_schema(actions["list_notes"])
        assert schema["properties"]["limit"]["type"] == "integer"
        assert "limit" not in schema["required"]
        assert schema["additionalProperties"] is False

    def test_append_gets_no_injection(self, tmp_path):
        actions = self._actions(tmp_path)
        schema = action_param_schema(actions["add_note"])
        assert set(schema["properties"]) == {"testo"}


class TestDispatch:
    async def test_storage_round_trip(self, tmp_path):
        _write_app(tmp_path)
        added = await execute_action(tmp_path, "note", "add_note", {"testo": "ciao"})
        assert added["ok"] is True and "ts" in added["record"]

        listed = await execute_action(tmp_path, "note", "list_notes", {"limit": 5})
        assert [r["testo"] for r in listed["records"]] == ["ciao"]

        rid = added["record"]["id"]
        edited = await execute_action(tmp_path, "note", "edit_note", {"id": rid, "testo": "no"})
        assert edited["record"]["testo"] == "no"

        deleted = await execute_action(tmp_path, "note", "del_note", {"id": rid})
        assert deleted == {"ok": True, "deleted": rid}

    async def test_update_without_id_is_400(self, tmp_path):
        _write_app(tmp_path)
        with pytest.raises(AppActionError) as exc:
            await execute_action(tmp_path, "note", "edit_note", {"testo": "x"})
        assert exc.value.status == 400
        assert "id" in str(exc.value)

    async def test_storage_error_status_propagates(self, tmp_path):
        _write_app(tmp_path)
        with pytest.raises(AppActionError) as exc:
            await execute_action(tmp_path, "note", "del_note", {"id": "manca"})
        assert exc.value.status == 404
