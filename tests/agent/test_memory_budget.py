"""Tests for the long-term memory budget — report, gauge and write-size guard."""

import pytest

from jenny.agent.memory import MemoryStore
from jenny.agent.memory_budget import (
    FileBudget,
    budget_report,
    make_write_size_guard,
    render_gauge,
)


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path)
    s.memory_file.write_text("# Memory\n- Project X active", encoding="utf-8")
    s.user_file.write_text("# User\n- Lives in Italy", encoding="utf-8")
    s.soul_file.write_text("# Soul\n- Helpful", encoding="utf-8")
    return s


def _report(store, *, memory=0, user=0, soul=0):
    return budget_report(store, memory_chars=memory, user_chars=user, soul_chars=soul)


class TestBudgetReport:
    def test_orders_memory_user_soul(self, store):
        report = _report(store)
        assert [item.label for item in report] == ["MEMORY.md", "USER.md", "SOUL.md"]
        assert [item.path for item in report] == [
            store.memory_file, store.user_file, store.soul_file,
        ]

    def test_counts_characters_of_existing_files(self, store):
        store.memory_file.write_text("x" * 120, encoding="utf-8")
        report = _report(store, memory=6000)
        assert report[0].chars == 120
        assert report[0].budget == 6000

    def test_missing_file_reports_zero_chars(self, tmp_path):
        # Nessuno dei tre file esiste: il report deve essere comunque costruibile,
        # perché gira anche al primo avvio su un workspace vuoto.
        empty = MemoryStore(tmp_path)
        report = _report(empty, memory=6000, user=2000, soul=1000)
        assert [item.chars for item in report] == [0, 0, 0]

    def test_zero_budget_still_measured(self, store):
        store.soul_file.write_text("y" * 42, encoding="utf-8")
        soul = _report(store, memory=6000)[2]
        assert soul.budget == 0
        assert soul.enforced is False
        assert soul.chars == 42
        assert soul.over is False

    def test_pct_is_none_when_not_enforced(self, store):
        report = _report(store)
        assert all(item.pct is None for item in report)

    def test_pct_truncates(self, store):
        item = FileBudget(label="MEMORY.md", path=store.memory_file, chars=4021, budget=6000)
        assert item.pct == 67
        assert item.over is False

    def test_over_when_above_budget(self, store):
        item = FileBudget(label="MEMORY.md", path=store.memory_file, chars=6001, budget=6000)
        assert item.over is True


class TestRenderGauge:
    def test_enforced_line(self, store):
        item = FileBudget(label="MEMORY.md", path=store.memory_file, chars=4021, budget=6000)
        assert "MEMORY.md [67% — 4,021/6,000 chars]" in render_gauge([item])

    def test_unenforced_line(self, store):
        item = FileBudget(label="MEMORY.md", path=store.memory_file, chars=4021, budget=0)
        assert "MEMORY.md [4,021 chars — no budget]" in render_gauge([item])

    def test_empty_report_renders_empty_string(self):
        assert render_gauge([]) == ""

    def test_mentions_the_80_percent_rule(self, store):
        gauge = render_gauge(_report(store, memory=6000))
        assert "80%" in gauge
        # Una riga di istruzioni + una per file: deve restare compatto, finisce
        # in ogni prompt di Dream.
        assert len(gauge.splitlines()) == 4


class TestWriteSizeGuard:
    def test_rejects_growing_write_over_budget(self, store):
        guard = make_write_size_guard(_report(store, memory=100))
        result = guard(store.memory_file, "z" * 150)
        assert result is not None
        assert "MEMORY.md" in result
        assert "150" in result and "100" in result

    def test_accepts_shrinking_write_still_over_budget(self, store):
        # Il test che protegge dall'autoblocco: un file già fuori misura può
        # essere potato solo scrivendoci sopra. Se il guard guardasse soltanto
        # "risultato > budget", la prima potatura verrebbe rifiutata e nessun
        # file oltre budget potrebbe più rientrare — cioè proprio lo stato in cui
        # la feature si trova al primo avvio sul device.
        store.memory_file.write_text("z" * 500, encoding="utf-8")
        guard = make_write_size_guard(_report(store, memory=100))
        assert guard(store.memory_file, "z" * 400) is None

    def test_rejects_equal_size_write_over_budget(self, store):
        store.memory_file.write_text("z" * 500, encoding="utf-8")
        guard = make_write_size_guard(_report(store, memory=100))
        # Stessa dimensione non è potatura: non fa progresso e va rifiutata.
        assert guard(store.memory_file, "w" * 500) is not None

    def test_rejects_oversized_write_to_missing_file(self, tmp_path):
        empty = MemoryStore(tmp_path)
        guard = make_write_size_guard(_report(empty, memory=100))
        assert guard(empty.memory_file, "z" * 150) is not None

    def test_accepts_write_within_budget(self, store):
        guard = make_write_size_guard(_report(store, memory=100))
        assert guard(store.memory_file, "z" * 100) is None

    def test_zero_budget_never_rejects(self, store):
        guard = make_write_size_guard(_report(store, memory=0))
        assert guard(store.memory_file, "z" * 100_000) is None

    def test_path_outside_report_passes(self, store, tmp_path):
        guard = make_write_size_guard(_report(store, memory=100, user=100, soul=100))
        assert guard(tmp_path / "notes" / "other.md", "z" * 5000) is None

    def test_matches_through_a_symlinked_parent(self, tmp_path, store):
        # Analogo di /data/user/0/<pkg> vs /data/data/<pkg> su Android: il tool
        # può presentare il path in una forma non canonica. Un guard che
        # confronta forme diverse non matcha mai, e non matchare mai equivale a
        # non esistere — qui il rifiuto deve scattare lo stesso.
        alias = tmp_path.parent / f"{tmp_path.name}-alias"
        try:
            alias.symlink_to(tmp_path, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported here")
        guard = make_write_size_guard(_report(store, memory=100))
        assert guard(alias / "memory" / "MEMORY.md", "z" * 150) is not None

    def test_rejection_leaves_the_file_untouched(self, store):
        store.memory_file.write_text("original", encoding="utf-8")
        before_mtime = store.memory_file.stat().st_mtime_ns
        guard = make_write_size_guard(_report(store, memory=10))
        assert guard(store.memory_file, "z" * 500) is not None
        assert store.memory_file.read_text(encoding="utf-8") == "original"
        assert store.memory_file.stat().st_mtime_ns == before_mtime

    def test_rejection_message_is_actionable(self, store):
        guard = make_write_size_guard(_report(store, memory=100))
        result = guard(store.memory_file, "z" * 150)
        assert result is not None
        lowered = result.lower()
        assert "same turn" in lowered
        assert "delete" in lowered
        assert "smaller" in lowered
