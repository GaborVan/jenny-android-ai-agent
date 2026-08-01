"""Storage action executor: typed mutations on JSONL collections in data/.

One collection = one ``<app>/data/<collection>.jsonl`` file. Records carry an
auto-assigned ``id`` (12 hex chars) and ``ts`` (ISO-8601). Appends go through
a per-file asyncio.Lock; rewrites (set/update/delete) are atomic.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from jenny.apps.manifest import COLLECTION_RE, AppAction
from jenny.utils.path import atomic_write

DEFAULT_QUERY_LIMIT = 200
DEFAULT_MAX_COLLECTION_BYTES = 5_000_000

_LOCKS: dict[str, asyncio.Lock] = {}


class StorageError(Exception):
    """Structured storage failure; ``status`` maps to an HTTP-ish code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _lock_for(path: Path) -> asyncio.Lock:
    key = str(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = _LOCKS[key] = asyncio.Lock()
    return lock


def _collection_path(app_dir: Path, collection: str) -> Path:
    if not COLLECTION_RE.match(collection):
        raise StorageError(f"invalid collection name '{collection}'")
    return app_dir / "data" / f"{collection}.jsonl"


def _read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    skipped = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                skipped += 1
    if skipped:
        logger.warning("Skipped {} corrupt line(s) in {}", skipped, path)
    return records


def _check_size(path: Path, max_bytes: int) -> None:
    if path.is_file() and path.stat().st_size >= max_bytes:
        raise StorageError(
            f"collection '{path.stem}' exceeds the {max_bytes} byte limit", status=413
        )


def _new_record(params: dict) -> dict:
    record = {k: v for k, v in params.items() if k != "id"}
    return {
        "id": secrets.token_hex(6),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **record,
    }


def _dump(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False)


def _rewrite(path: Path, records: list[dict]) -> None:
    content = "".join(_dump(r) + "\n" for r in records)
    atomic_write(path, content)


def _require_id(params: dict) -> str:
    record_id = params.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise StorageError("'id' parameter is required for this operation")
    return record_id


async def execute_storage_action(
    app_dir: Path,
    action: AppAction,
    params: dict,
    *,
    max_bytes: int = DEFAULT_MAX_COLLECTION_BYTES,
) -> dict:
    """Execute one storage op; returns a JSON-safe result dict."""
    assert action.collection is not None and action.op is not None
    path = _collection_path(app_dir, action.collection)

    async with _lock_for(path):
        if action.op == "append":
            _check_size(path, max_bytes)
            record = _new_record(params)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(_dump(record) + "\n")
                f.flush()
            return {"ok": True, "record": record}

        if action.op == "query":
            filters = {k: v for k, v in params.items() if k != "limit"}
            limit = params.get("limit")
            if not isinstance(limit, int) or limit <= 0:
                limit = DEFAULT_QUERY_LIMIT
            records = _read_records(path)
            matched = [
                r for r in records
                if all(r.get(k) == v for k, v in filters.items())
            ]
            return {"ok": True, "records": matched[:limit], "count": len(matched)}

        if action.op == "set":
            _check_size(path, max_bytes)
            record_id = _require_id(params)
            records = _read_records(path)
            replacement = {**{k: v for k, v in params.items() if k != "id"}, "id": record_id}
            for i, record in enumerate(records):
                if record.get("id") == record_id:
                    replacement.setdefault("ts", record.get("ts"))
                    records[i] = replacement
                    break
            else:
                replacement.setdefault(
                    "ts", datetime.now(timezone.utc).isoformat(timespec="seconds")
                )
                records.append(replacement)
            _rewrite(path, records)
            return {"ok": True, "record": replacement}

        if action.op == "update":
            record_id = _require_id(params)
            records = _read_records(path)
            for i, record in enumerate(records):
                if record.get("id") == record_id:
                    updated = {**record, **{k: v for k, v in params.items() if k != "id"}}
                    records[i] = updated
                    _rewrite(path, records)
                    return {"ok": True, "record": updated}
            raise StorageError(f"record '{record_id}' not found", status=404)

        if action.op == "delete":
            record_id = _require_id(params)
            records = _read_records(path)
            remaining = [r for r in records if r.get("id") != record_id]
            if len(remaining) == len(records):
                raise StorageError(f"record '{record_id}' not found", status=404)
            _rewrite(path, remaining)
            return {"ok": True, "deleted": record_id}

    raise StorageError(f"unknown storage op '{action.op}'")
