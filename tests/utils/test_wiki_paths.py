"""Discovery e fingerprint delle wiki (``jenny/utils/wiki_paths.py``).

Il fingerprint è ciò che decide se Atlas parte o no: se sbaglia per eccesso si
paga un turno LLM a vuoto ogni tick, se sbaglia per difetto la rubrica resta
indietro. Questi test fissano i due confini.
"""

from __future__ import annotations

import os
from pathlib import Path

from jenny.utils.wiki_paths import (
    discover_wiki_roots,
    discover_wikis,
    iter_wiki_sources,
    read_wiki_scope,
    wiki_fingerprint,
    wiki_schema_file,
)


def _make_wiki(wikis_dir: Path, name: str, *, pages: dict[str, str] | None = None) -> Path:
    root = wikis_dir / name
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text(f"# {name}\n", encoding="utf-8")
    for rel, body in (pages or {}).items():
        target = root / "wiki" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def _touch_newer(path: Path) -> None:
    """Riscrive *path* forzando un mtime più recente.

    Su filesystem a granularità bassa due write consecutive possono condividere
    lo stesso mtime: qui lo spostiamo in avanti a mano invece di sperare.
    """
    path.write_text(path.read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


class TestDiscovery:
    def test_finds_only_dirs_with_a_wiki_subfolder(self, tmp_path):
        wikis = tmp_path / "wikis"
        _make_wiki(wikis, "main")
        (wikis / "notawiki").mkdir()

        assert list(discover_wikis(wikis)) == ["main"]

    def test_missing_dir_is_not_an_error(self, tmp_path):
        assert discover_wikis(tmp_path / "nope") == {}

    def test_roots_point_one_level_above_pages(self, tmp_path):
        wikis = tmp_path / "wikis"
        _make_wiki(wikis, "main")

        assert discover_wiki_roots(wikis)["main"] == wikis / "main"
        assert discover_wikis(wikis)["main"] == wikis / "main" / "wiki"


class TestScope:
    def test_prefers_frontmatter_summary(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "CLAUDE.md").write_text(
            "---\nsummary: AI loops and step executors\n---\n\n# main\n", encoding="utf-8"
        )

        assert read_wiki_scope(root) == "AI loops and step executors"

    def test_falls_back_to_first_scope_bullet(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "CLAUDE.md").write_text(
            "# main\n\n## Scope\n\nWhat this wiki covers:\n\n- Personal projects\n- Other stuff\n",
            encoding="utf-8",
        )

        assert read_wiki_scope(root) == "Personal projects"

    def test_placeholders_do_not_count_as_scope(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "CLAUDE.md").write_text(
            "---\nsummary: <what this wiki is about>\n---\n\n# main\n", encoding="utf-8"
        )

        assert read_wiki_scope(root) == "(no scope set)"

    def test_missing_schema_file_is_reported(self, tmp_path):
        """Il nome nel messaggio e' quello che una wiki dovrebbe avere *oggi*.

        Dal 22/08 il file di istruzioni si chiama ``AGENTS.md``; ``CLAUDE.md``
        resta letto nelle sette wiki che l'avevano gia' (passo 2.3), ma non e'
        piu' il nome da suggerire a chi non ce l'ha.
        """
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "CLAUDE.md").unlink()

        assert read_wiki_scope(root) == "(no AGENTS.md)"


class TestSources:
    def test_includes_schema_pages_and_registry(self, tmp_path):
        wikis = tmp_path / "wikis"
        _make_wiki(wikis, "main", pages={"index.md": "# Index", "entities/ada.md": "# Ada"})
        (wikis / "_index.md").write_text("# Workspace Index\n", encoding="utf-8")

        names = {p.relative_to(wikis).as_posix() for p in iter_wiki_sources(wikis)}

        assert names == {
            "_index.md",
            "main/CLAUDE.md",
            "main/wiki/index.md",
            "main/wiki/entities/ada.md",
        }

    def test_excludes_log_and_audit(self, tmp_path):
        wikis = tmp_path / "wikis"
        root = _make_wiki(wikis, "main", pages={"index.md": "# Index"})
        for rel in ("log/20260806.md", "audit/open-1.md", "audit/resolved/old.md"):
            target = root / "wiki" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("noise", encoding="utf-8")

        names = {p.relative_to(wikis).as_posix() for p in iter_wiki_sources(wikis)}

        assert names == {"main/CLAUDE.md", "main/wiki/index.md"}


class TestFingerprint:
    def test_is_stable_across_calls(self, tmp_path):
        wikis = tmp_path / "wikis"
        _make_wiki(wikis, "main", pages={"index.md": "# Index"})

        assert wiki_fingerprint(wikis) == wiki_fingerprint(wikis)

    def test_changes_when_a_page_changes(self, tmp_path):
        wikis = tmp_path / "wikis"
        root = _make_wiki(wikis, "main", pages={"index.md": "# Index"})
        before = wiki_fingerprint(wikis)

        _touch_newer(root / "wiki" / "index.md")

        assert wiki_fingerprint(wikis) != before

    def test_changes_when_a_page_appears(self, tmp_path):
        wikis = tmp_path / "wikis"
        root = _make_wiki(wikis, "main", pages={"index.md": "# Index"})
        before = wiki_fingerprint(wikis)

        (root / "wiki" / "entities").mkdir()
        (root / "wiki" / "entities" / "ada.md").write_text("# Ada", encoding="utf-8")

        assert wiki_fingerprint(wikis) != before

    def test_ignores_log_churn(self, tmp_path):
        wikis = tmp_path / "wikis"
        root = _make_wiki(wikis, "main", pages={"index.md": "# Index"})
        before = wiki_fingerprint(wikis)

        log = root / "wiki" / "log"
        log.mkdir()
        (log / "20260806.md").write_text("## [10:00] lint | ok\n", encoding="utf-8")

        assert wiki_fingerprint(wikis) == before

    def test_extra_paths_participate(self, tmp_path):
        wikis = tmp_path / "wikis"
        _make_wiki(wikis, "main", pages={"index.md": "# Index"})
        policy = tmp_path / "WIKI_POLICY.md"
        before = wiki_fingerprint(wikis, extra_paths=(policy,))

        policy.write_text("only plants with a nickname\n", encoding="utf-8")

        assert wiki_fingerprint(wikis, extra_paths=(policy,)) != before


class TestQualeFileDiIstruzioni:
    """Passo 2.3: ``AGENTS.md``, e se non c'e' ``CLAUDE.md``.

    Le sette wiki che esistevano prima del rinomino hanno il vecchio nome scritto
    a mano, e finche' il passo 7 non le migra e' l'unico posto in cui e' scritto
    di cosa si occupano. Le nuove nascono col nuovo. Chi legge accetta tutt'e
    due; chi scrive, solo il nuovo.
    """

    def test_agents_vince_su_claude(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "AGENTS.md").write_text("---\nsummary: il nuovo\n---\n", encoding="utf-8")
        (root / "CLAUDE.md").write_text("---\nsummary: il vecchio\n---\n", encoding="utf-8")

        assert wiki_schema_file(root).name == "AGENTS.md"
        assert read_wiki_scope(root) == "il nuovo"

    def test_claude_resta_leggibile_da_solo(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "CLAUDE.md").write_text("---\nsummary: il vecchio\n---\n", encoding="utf-8")

        assert wiki_schema_file(root).name == "CLAUDE.md"
        assert read_wiki_scope(root) == "il vecchio"

    def test_senza_nessuno_dei_due_e_none(self, tmp_path):
        root = _make_wiki(tmp_path / "wikis", "main")
        (root / "CLAUDE.md").unlink()

        assert wiki_schema_file(root) is None

    def test_limpronta_vede_agents(self, tmp_path):
        """Il punto che senza ripiego sarebbe rimasto muto.

        Se ``iter_wiki_sources`` guardasse solo il vecchio nome, una wiki che
        tiene le istruzioni in ``AGENTS.md`` non farebbe mai cambiare l'impronta:
        la modifichi e Atlas non se ne accorge, senza un errore e senza un log.
        """
        wikis = tmp_path / "wikis"
        root = _make_wiki(wikis, "main", pages={"index.md": "# Index"})
        (root / "CLAUDE.md").unlink()
        agents = root / "AGENTS.md"
        agents.write_text("---\nsummary: prima\n---\n", encoding="utf-8")

        names = {p.relative_to(wikis).as_posix() for p in iter_wiki_sources(wikis)}
        assert "main/AGENTS.md" in names

        before = wiki_fingerprint(wikis)
        agents.write_text("---\nsummary: dopo\n---\n", encoding="utf-8")
        assert wiki_fingerprint(wikis) != before
