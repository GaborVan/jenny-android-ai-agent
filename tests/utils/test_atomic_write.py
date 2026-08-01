"""Tests for atomic_write helper."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from jenny.utils.path import atomic_write


class TestAtomicWrite:
    def test_writes_string_content(self, tmp_path: Path) -> None:
        path = tmp_path / "out.txt"
        atomic_write(path, "hello world")
        assert path.read_text(encoding="utf-8") == "hello world"

    def test_writes_bytes_content(self, tmp_path: Path) -> None:
        path = tmp_path / "out.bin"
        atomic_write(path, b"\x00\x01\x02")
        assert path.read_bytes() == b"\x00\x01\x02"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "a" / "b" / "c.txt"
        atomic_write(path, "nested")
        assert path.read_text(encoding="utf-8") == "nested"

    def test_atomic_replace(self, tmp_path: Path) -> None:
        path = tmp_path / "target.txt"
        path.write_text("old", encoding="utf-8")
        atomic_write(path, "new")
        assert path.read_text(encoding="utf-8") == "new"

    def test_no_temp_file_left_behind(self, tmp_path: Path) -> None:
        path = tmp_path / "clean.txt"
        atomic_write(path, "data")
        assert list(tmp_path.glob("*.tmp")) == []
        assert list(tmp_path.glob(".clean*")) == []

    def test_fsync_dir_failure_is_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.txt"
        with patch("os.open") as mock_open:
            mock_open.side_effect = OSError("android fuse")
            atomic_write(path, "data")
        assert path.read_text(encoding="utf-8") == "data"

    def test_fsync_file_failure_not_hidden(self, tmp_path: Path) -> None:
        path = tmp_path / "fail.txt"
        with patch("os.fsync") as mock_fsync:
            mock_fsync.side_effect = OSError("disk full")
            with pytest.raises(OSError):
                atomic_write(path, "data")
        assert not path.exists()

    def test_disable_fsync_file(self, tmp_path: Path) -> None:
        path = tmp_path / "nofsync.txt"
        with patch("os.fsync") as mock_fsync, patch(
            "jenny.utils.path._fsync_dir"
        ) as mock_fsync_dir:
            atomic_write(path, "data", fsync_file=False, fsync_dir=False)
        mock_fsync.assert_not_called()
        mock_fsync_dir.assert_not_called()
        assert path.read_text(encoding="utf-8") == "data"

    def test_disable_fsync_dir(self, tmp_path: Path) -> None:
        path = tmp_path / "nodirsync.txt"
        with patch("jenny.utils.path._fsync_dir") as mock_fsync_dir:
            atomic_write(path, "data", fsync_dir=False)
        mock_fsync_dir.assert_not_called()
        assert path.read_text(encoding="utf-8") == "data"

    def test_concurrent_writers_same_path_do_not_clobber(self, tmp_path: Path) -> None:
        """Regressione F54: writer concorrenti sullo stesso path non devono
        collidere sul file temporaneo (suffisso uuid univoco per scrittura)."""
        path = tmp_path / "shared.json"
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def _writer(payload: str) -> None:
            try:
                barrier.wait()
                for _ in range(20):
                    atomic_write(path, payload, fsync_file=False, fsync_dir=False)
            except BaseException as exc:  # noqa: BLE001 - raccolta per l'asserzione
                errors.append(exc)

        payloads = [f"writer-{i}" for i in range(8)]
        threads = [threading.Thread(target=_writer, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Nessun temp orfano e il file finale è uno dei payload integri.
        assert list(tmp_path.glob("*.tmp")) == []
        assert path.read_text(encoding="utf-8") in payloads
