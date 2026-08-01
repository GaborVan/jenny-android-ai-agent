"""Tests for the Jenny Apps storage executor."""

from __future__ import annotations

import json

import pytest

from jenny.apps.manifest import AppAction
from jenny.apps.storage import StorageError, execute_storage_action


def _action(op: str, collection: str = "notes") -> AppAction:
    return AppAction(name=f"{op}_x", description="t", kind="storage", op=op,
                     collection=collection)


def _collection_file(app_dir, collection="notes"):
    return app_dir / "data" / f"{collection}.jsonl"


class TestAppend:
    async def test_append_assigns_id_and_ts(self, tmp_path):
        result = await execute_storage_action(tmp_path, _action("append"), {"testo": "ciao"})
        record = result["record"]
        assert result["ok"] is True
        assert record["testo"] == "ciao"
        assert len(record["id"]) == 12
        assert "T" in record["ts"]
        lines = _collection_file(tmp_path).read_text().strip().splitlines()
        assert json.loads(lines[0]) == record

    async def test_append_ignores_client_id(self, tmp_path):
        result = await execute_storage_action(
            tmp_path, _action("append"), {"id": "evil", "testo": "x"}
        )
        assert result["record"]["id"] != "evil"

    async def test_size_cap(self, tmp_path):
        f = _collection_file(tmp_path)
        f.parent.mkdir(parents=True)
        f.write_text("x" * 100)
        with pytest.raises(StorageError) as e:
            await execute_storage_action(tmp_path, _action("append"), {"a": 1}, max_bytes=50)
        assert e.value.status == 413


class TestQuery:
    async def test_query_filters_and_limits(self, tmp_path):
        for i in range(5):
            await execute_storage_action(
                tmp_path, _action("append"), {"n": i, "tag": "even" if i % 2 == 0 else "odd"}
            )
        res = await execute_storage_action(tmp_path, _action("query"), {"tag": "even"})
        assert res["count"] == 3
        res = await execute_storage_action(tmp_path, _action("query"), {"limit": 2})
        assert len(res["records"]) == 2
        assert res["count"] == 5

    async def test_query_empty_collection(self, tmp_path):
        res = await execute_storage_action(tmp_path, _action("query"), {})
        assert res == {"ok": True, "records": [], "count": 0}

    async def test_corrupt_lines_skipped(self, tmp_path):
        f = _collection_file(tmp_path)
        f.parent.mkdir(parents=True)
        f.write_text('{"id": "a", "v": 1}\n{broken\n[1,2]\n{"id": "b", "v": 2}\n')
        res = await execute_storage_action(tmp_path, _action("query"), {})
        assert [r["id"] for r in res["records"]] == ["a", "b"]


class TestSetUpdateDelete:
    async def test_update_merges(self, tmp_path):
        added = await execute_storage_action(tmp_path, _action("append"), {"v": 1, "k": "a"})
        rid = added["record"]["id"]
        res = await execute_storage_action(tmp_path, _action("update"), {"id": rid, "v": 2})
        assert res["record"]["v"] == 2
        assert res["record"]["k"] == "a"
        assert res["record"]["ts"] == added["record"]["ts"]

    async def test_update_not_found(self, tmp_path):
        await execute_storage_action(tmp_path, _action("append"), {"v": 1})
        with pytest.raises(StorageError) as e:
            await execute_storage_action(tmp_path, _action("update"), {"id": "manca", "v": 2})
        assert e.value.status == 404

    async def test_set_replaces_whole_record(self, tmp_path):
        added = await execute_storage_action(tmp_path, _action("append"), {"v": 1, "k": "a"})
        rid = added["record"]["id"]
        res = await execute_storage_action(tmp_path, _action("set"), {"id": rid, "w": 9})
        assert res["record"]["id"] == rid
        assert "k" not in res["record"] or res["record"].get("w") == 9
        records = (await execute_storage_action(tmp_path, _action("query"), {}))["records"]
        assert len(records) == 1
        assert records[0]["w"] == 9

    async def test_set_upserts_missing_id(self, tmp_path):
        res = await execute_storage_action(tmp_path, _action("set"), {"id": "nuovo", "v": 1})
        assert res["record"]["id"] == "nuovo"
        records = (await execute_storage_action(tmp_path, _action("query"), {}))["records"]
        assert len(records) == 1

    async def test_delete(self, tmp_path):
        added = await execute_storage_action(tmp_path, _action("append"), {"v": 1})
        rid = added["record"]["id"]
        res = await execute_storage_action(tmp_path, _action("delete"), {"id": rid})
        assert res == {"ok": True, "deleted": rid}
        assert (await execute_storage_action(tmp_path, _action("query"), {}))["count"] == 0

    async def test_delete_not_found(self, tmp_path):
        with pytest.raises(StorageError) as e:
            await execute_storage_action(tmp_path, _action("delete"), {"id": "manca"})
        assert e.value.status == 404

    async def test_mutations_require_id(self, tmp_path):
        for op in ("set", "update", "delete"):
            with pytest.raises(StorageError):
                await execute_storage_action(tmp_path, _action(op), {"v": 1})

    async def test_rewrite_leaves_no_tmp_residue(self, tmp_path):
        added = await execute_storage_action(tmp_path, _action("append"), {"v": 1})
        await execute_storage_action(
            tmp_path, _action("update"), {"id": added["record"]["id"], "v": 2}
        )
        leftovers = list((tmp_path / "data").glob("*.tmp"))
        assert leftovers == []


class TestCollectionSafety:
    async def test_invalid_collection_rejected(self, tmp_path):
        bad = AppAction(name="x", description="t", kind="storage", op="append",
                        collection="../evil")
        with pytest.raises(StorageError):
            await execute_storage_action(tmp_path, bad, {"a": 1})
        assert not (tmp_path.parent / "evil.jsonl").exists()
