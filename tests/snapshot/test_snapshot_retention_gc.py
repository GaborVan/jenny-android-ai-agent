"""Test di retention e garbage collection dello store snapshot."""

from __future__ import annotations

from pathlib import Path

from jenny.snapshot.engine import SnapshotEngine
from jenny.snapshot.store import iter_blob_hashes, object_path

DAY_MS = 86_400_000


def _engine(root: Path) -> SnapshotEngine:
    root.mkdir(exist_ok=True)
    (root / "file.txt").write_text("v0", encoding="utf-8")
    return SnapshotEngine(root, root / ".jenny" / "snapshots")


def _snap(engine: SnapshotEngine, root: Path, version: int, now_ms: int) -> str:
    (root / "file.txt").write_text(f"v{version}", encoding="utf-8")
    manifest = engine.create_snapshot(trigger="auto", now_ms=now_ms)
    assert manifest is not None
    return manifest.id


def test_retention_keeps_recent_and_thins_old(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = 100 * DAY_MS

    # 3 snapshot nello stesso giorno vecchio (giorno 10), 2 in un altro
    # giorno vecchio (giorno 20), 3 recenti.
    old_day1 = [_snap(engine, tmp_path, v, 10 * DAY_MS + v * 1000) for v in range(3)]
    old_day2 = [_snap(engine, tmp_path, 10 + v, 20 * DAY_MS + v * 1000) for v in range(2)]
    recent = [_snap(engine, tmp_path, 20 + v, now - v * 1000) for v in range(3)]

    removed = engine.apply_retention(keep_recent=3, thin_after_days=30, now_ms=now)

    kept = {s["id"] for s in engine.list_snapshots()}
    # I 3 recenti sono intoccabili.
    assert set(recent) <= kept
    # Dei giorni vecchi resta solo il più recente per giornata.
    assert old_day1[-1] in kept
    assert old_day2[-1] in kept
    assert set(old_day1[:-1]) <= set(removed)
    assert set(old_day2[:-1]) <= set(removed)
    assert len(kept) == 5


def test_retention_noop_when_all_recent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = 100 * DAY_MS
    ids = [_snap(engine, tmp_path, v, now - v * 1000) for v in range(4)]
    removed = engine.apply_retention(keep_recent=2, thin_after_days=30, now_ms=now)
    assert removed == []
    assert {s["id"] for s in engine.list_snapshots()} == set(ids)


def test_retention_max_age_drops_old_snapshots(tmp_path: Path) -> None:
    """Con l'orizzonte impostato gli snapshot oltre max_age_days spariscono
    del tutto (niente assottigliamento 1/giorno), ma i keep_recent restano
    protetti anche se più vecchi dell'orizzonte."""
    engine = _engine(tmp_path)
    now = 100 * DAY_MS

    ancient = [_snap(engine, tmp_path, v, 10 * DAY_MS + v * 1000) for v in range(3)]
    mid = _snap(engine, tmp_path, 10, now - 20 * DAY_MS)
    recent = [_snap(engine, tmp_path, 20 + v, now - v * 1000) for v in range(2)]

    removed = engine.apply_retention(
        keep_recent=2, thin_after_days=30, max_age_days=60, now_ms=now
    )

    kept = {s["id"] for s in engine.list_snapshots()}
    assert set(recent) <= kept
    assert mid in kept  # dentro l'orizzonte
    assert set(ancient) <= set(removed)  # oltre i 60 giorni: eliminati tutti


def test_retention_max_age_protects_keep_recent(tmp_path: Path) -> None:
    """Anche se TUTTI gli snapshot sono oltre l'orizzonte, gli ultimi
    keep_recent non vengono toccati (paracadute per utenti inattivi)."""
    engine = _engine(tmp_path)
    now = 100 * DAY_MS
    ids = [_snap(engine, tmp_path, v, 10 * DAY_MS + v * 1000) for v in range(4)]

    removed = engine.apply_retention(
        keep_recent=2, thin_after_days=30, max_age_days=7, now_ms=now
    )

    kept = {s["id"] for s in engine.list_snapshots()}
    assert set(ids[-2:]) <= kept
    assert set(ids[:2]) == set(removed)


def test_retention_max_age_zero_means_forever(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = 100 * DAY_MS
    old = _snap(engine, tmp_path, 1, 40 * DAY_MS)
    _snap(engine, tmp_path, 2, now)

    removed = engine.apply_retention(
        keep_recent=1, thin_after_days=30, max_age_days=0, now_ms=now
    )
    assert removed == []
    assert old in {s["id"] for s in engine.list_snapshots()}


def test_gc_removes_only_orphan_blobs(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = 100 * DAY_MS
    _snap(engine, tmp_path, 1, 10 * DAY_MS)
    _snap(engine, tmp_path, 2, 10 * DAY_MS + 1000)
    keep_id = _snap(engine, tmp_path, 3, now)

    before = set(iter_blob_hashes(engine.objects_dir))
    engine.apply_retention(keep_recent=1, thin_after_days=30, now_ms=now)
    removed_blobs = engine.gc()
    after = set(iter_blob_hashes(engine.objects_dir))

    assert removed_blobs > 0
    assert after < before
    # Tutti i blob degli snapshot sopravvissuti sono ancora leggibili.
    for summary in engine.list_snapshots():
        manifest = engine.load_manifest(summary["id"])
        for entry in manifest.files:
            assert object_path(engine.objects_dir, entry.hash).is_file()
    assert engine.head_id() == keep_id


def test_gc_noop_when_everything_referenced(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _snap(engine, tmp_path, 1, 1000)
    _snap(engine, tmp_path, 2, 2000)
    assert engine.gc() == 0
