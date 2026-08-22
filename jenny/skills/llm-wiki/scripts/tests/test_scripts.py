#!/usr/bin/env python3
"""
Tests for the llm-wiki scripts. Stdlib only — no pip install.

Run (paths relative to the skill's scripts/ dir):
    python3 scripts/tests/test_scripts.py
    python3 -m unittest discover -s scripts/tests -t scripts
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the scripts importable (this file lives in scripts/tests/).
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

import audit_review  # noqa: E402
import lint_wiki  # noqa: E402
import reindex_wikis  # noqa: E402
import scaffold as scaffold_mod  # noqa: E402


def quiet(fn, *args, **kwargs):
    """Call fn with stdout/stderr suppressed; return its result."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*args, **kwargs)


def registry_block(wikis_dir: Path) -> str:
    text = (wikis_dir / "_index.md").read_text(encoding="utf-8")
    m = reindex_wikis.BLOCK_RE.search(text)
    return m.group(1) if m else ""


def make_wiki(wikis_dir: Path, name: str, claude_md: str) -> Path:
    """Minimal wiki (CLAUDE.md + wiki/index.md) without going through scaffold.

    Deliberately the *old* name: this fixture stands for the seven wikis that
    already existed when the file was renamed to AGENTS.md, and reading those
    is exactly what must keep working.
    """
    root = wikis_dir / name
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    (root / "wiki" / "index.md").write_text(f"# Index — {name}\n", encoding="utf-8")
    return root


def make_page(wiki_root: Path, relpath: str, title: str, sources=None, body="Body.") -> Path:
    p = wiki_root / "wiki" / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = [f"title: {title}", "type: concept"]
    if sources is not None:
        fm.append("sources: [" + ", ".join(sources) + "]")
    p.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n", encoding="utf-8")
    return p


def run_lint(root: Path):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = lint_wiki.lint(str(root))
    return rc, buf.getvalue()


def write_audit(wiki_root: Path, filename: str, fields: dict, resolved=False) -> Path:
    d = wiki_root / "audit" / ("resolved" if resolved else "")
    d.mkdir(parents=True, exist_ok=True)
    fm = "\n".join(f"{k}: {v}" for k, v in fields.items())
    body = f"---\n{fm}\n---\n\n# Comment\n\nTest.\n\n# Resolution\n"
    path = d / filename
    path.write_text(body, encoding="utf-8")
    return path


VALID_AUDIT = {
    "id": "20260101-090000-abcd",
    "target": "index.md",
    "target_lines": "[1, 1]",
    "anchor_before": '""',
    "anchor_text": '"# Index"',
    "anchor_after": '""',
    "severity": "warn",
    "author": "tester",
    "source": "manual",
    "created": "2026-01-01T09:00:00+01:00",
    "status": "open",
}


class ScaffoldAndRegistry(unittest.TestCase):
    def test_scaffold_creates_tree_and_registers(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            quiet(scaffold_mod.scaffold, str(wikis / "loops"), "Loops")

            self.assertTrue((wikis / "main" / "wiki" / "index.md").is_file())
            self.assertTrue((wikis / "main" / "AGENTS.md").is_file())
            self.assertTrue((wikis / "_index.md").is_file())

            block = registry_block(wikis)
            self.assertIn("[[main/wiki/index|main]]", block)
            self.assertIn("[[loops/wiki/index|loops]]", block)
            self.assertEqual(reindex_wikis.check_index(wikis), [])

    def test_registry_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            for n in ("zebra", "alpha", "mid"):
                quiet(scaffold_mod.scaffold, str(wikis / n), n)
            names = reindex_wikis.discover_wikis(wikis)
            self.assertEqual(names, ["alpha", "mid", "zebra"])


class ScopeExtraction(unittest.TestCase):
    def test_summary_frontmatter_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            make_wiki(wikis, "w", "---\nsummary: The explicit summary line\n---\n\n# W\n")
            self.assertEqual(
                reindex_wikis.read_wiki_scope(wikis / "w", "w"),
                "The explicit summary line",
            )

    def test_scope_bullet_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            md = "# W\n\n## Scope\n\nWhat this wiki covers:\n- Bullet scope text\n"
            make_wiki(wikis, "w", md)
            self.assertEqual(
                reindex_wikis.read_wiki_scope(wikis / "w", "w"),
                "Bullet scope text",
            )

    def test_placeholder_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            md = "---\nsummary: <one-line scope>\n---\n\n# W\n\n## Scope\n\nWhat this wiki covers:\n- <describe>\n"
            make_wiki(wikis, "w", md)
            self.assertEqual(
                reindex_wikis.read_wiki_scope(wikis / "w", "w"),
                "(no scope set)",
            )


class ReindexDriftAndHeal(unittest.TestCase):
    def test_drift_detected_and_healed(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            self.assertEqual(reindex_wikis.check_index(wikis), [])

            # New wiki on disk, not yet registered.
            make_wiki(wikis, "extra", "# Extra\n")
            problems = reindex_wikis.check_index(wikis)
            self.assertTrue(any("extra" in p for p in problems))

            quiet(reindex_wikis.regenerate_index, wikis)
            self.assertEqual(reindex_wikis.check_index(wikis), [])

    def test_ghost_dir_without_wiki_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            (wikis / "ghost").mkdir()  # no wiki/ subdir
            self.assertNotIn("ghost", reindex_wikis.discover_wikis(wikis))
            self.assertEqual(reindex_wikis.check_index(wikis), [])

    def test_missing_index_reported_by_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            make_wiki(wikis, "main", "# Main\n")  # no _index.md written
            problems = reindex_wikis.check_index(wikis)
            self.assertTrue(problems)


class Lint(unittest.TestCase):
    def test_fresh_scaffold_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            self.assertEqual(quiet(lint_wiki.lint, str(wikis / "main")), 0)

    def test_cross_wiki_link_is_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            page = wikis / "main" / "wiki" / "concepts" / "A.md"
            page.write_text("---\ntitle: A\n---\n[[loops/wiki/concepts/B]]\n", encoding="utf-8")
            idx = wikis / "main" / "wiki" / "index.md"
            idx.write_text(idx.read_text().replace("*(none yet)*", "- [[A]]", 1), encoding="utf-8")
            self.assertNotEqual(quiet(lint_wiki.lint, str(wikis / "main")), 0)

    def test_workspace_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            quiet(scaffold_mod.scaffold, str(wikis / "loops"), "Loops")
            self.assertEqual(quiet(lint_wiki.lint_workspace, str(wikis)), 0)

    def test_workspace_fix_repairs_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            make_wiki(wikis, "extra", "---\nsummary: Extra wiki\n---\n# Extra\n")
            # Drift present → non-fix run fails.
            self.assertNotEqual(quiet(lint_wiki.lint_workspace, str(wikis)), 0)
            # Fix run repairs the registry and passes.
            self.assertEqual(quiet(lint_wiki.lint_workspace, str(wikis), True), 0)
            self.assertEqual(reindex_wikis.check_index(wikis), [])


class AuditShape(unittest.TestCase):
    def _wiki(self, tmp):
        wikis = Path(tmp) / "wikis"
        quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
        return wikis / "main"

    def test_valid_audit_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            write_audit(root, "20260101-090000-note.md", VALID_AUDIT)
            self.assertEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_custom_source_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            fields = dict(VALID_AUDIT, source="my-tool")
            write_audit(root, "20260101-090000-note.md", fields)
            self.assertEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_missing_field_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            fields = dict(VALID_AUDIT)
            del fields["severity"]
            write_audit(root, "20260101-090000-note.md", fields)
            self.assertNotEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_filename_timestamp_mismatch_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            # id ts 20260101-090000 vs filename ts 20260102-100000
            write_audit(root, "20260102-100000-note.md", VALID_AUDIT)
            self.assertNotEqual(quiet(lint_wiki.lint, str(root)), 0)

    def test_duplicate_id_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._wiki(tmp)
            write_audit(root, "20260101-090000-a.md", VALID_AUDIT)
            write_audit(root, "20260101-090000-b.md", VALID_AUDIT)
            self.assertNotEqual(quiet(lint_wiki.lint, str(root)), 0)


class DuplicatePages(unittest.TestCase):
    def test_dup_key_normalization(self):
        k = lint_wiki.dup_key
        self.assertEqual(k("Market Making"), k("Market-Making"))
        self.assertEqual(k("Market Making"), k("market_making"))
        self.assertEqual(k("RAG vs LLM Wiki"), k("LLM Wiki vs RAG"))
        # Different scope must NOT collide.
        self.assertNotEqual(k("Market Making"), k("Market Making Strategy"))

    def test_lint_flags_duplicate_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            root = wikis / "main"
            make_page(root, "concepts/MarketMaking.md", "Market Making")
            make_page(root, "concepts/Market-Making.md", "Market-Making")
            rc, out = run_lint(root)
            self.assertIn("Possible duplicate pages", out)
            self.assertNotEqual(rc, 0)

    def test_index_pages_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            root = wikis / "main"
            # Two folder-split index.md files must not count as duplicates.
            make_page(root, "concepts/a/index.md", "A")
            make_page(root, "concepts/b/index.md", "B")
            _, out = run_lint(root)
            self.assertIn("No duplicate-looking pages", out)


class SourceIntegrity(unittest.TestCase):
    def test_missing_source_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            root = wikis / "main"
            make_page(root, "concepts/P.md", "P", sources=["ghost-source"])
            _, out = run_lint(root)
            self.assertIn("sources not found in raw/", out)
            self.assertIn("ghost-source", out)

    def test_valid_source_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            root = wikis / "main"
            (root / "raw" / "articles" / "realsrc.md").write_text("x", encoding="utf-8")
            make_page(root, "concepts/P.md", "P", sources=["realsrc"])
            _, out = run_lint(root)
            self.assertIn("All cited sources resolve", out)


class AuditReview(unittest.TestCase):
    def test_review_reads_open_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            write_audit(wikis / "main", "20260101-090000-note.md", VALID_AUDIT)
            self.assertEqual(quiet(audit_review.main, str(wikis / "main"), "open"), 0)

    def test_workspace_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            wikis = Path(tmp) / "wikis"
            quiet(scaffold_mod.scaffold, str(wikis / "main"), "Main")
            quiet(scaffold_mod.scaffold, str(wikis / "loops"), "Loops")
            self.assertEqual(quiet(audit_review.run_workspace, str(wikis), "open"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
