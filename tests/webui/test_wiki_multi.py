"""Tests for the multi-wiki backend module."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.webui.wiki import (
    _split_wikilink,
    build_graph,
    build_home_graph,
    build_home_tree,
    build_tree,
    create_audit,
    create_renderer,
    discover_wikis,
    list_audits,
    load_audits,
    resolve_audit,
    resolve_wikilink,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def wikis_dir(tmp_path: Path) -> Path:
    """Create a temporary wikis directory."""
    d = tmp_path / "wikis"
    d.mkdir()
    return d


def _make_wiki(wikis_dir: Path, name: str, pages: dict[str, str]) -> Path:
    """Create a wiki with pages.  pages is {rel_path: content}.

    Returns the wiki *root* (parent of wiki/ and audit/ directories).
    """
    wiki_root = wikis_dir / name
    for rel, content in pages.items():
        full = wiki_root / "wiki" / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return wiki_root


# ── Discovery ───────────────────────────────────────────────────────────────


class TestDiscoverWikis:
    def test_discover_single_wiki(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        result = discover_wikis(wikis_dir)
        assert result == {"main": wikis_dir / "main" / "wiki"}

    def test_discover_multiple_wikis(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        (wikis_dir / "loops" / "wiki").mkdir(parents=True)
        result = discover_wikis(wikis_dir)
        assert set(result.keys()) == {"main", "loops"}

    def test_discover_skips_non_wiki_dirs(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        (wikis_dir / "not-a-wiki").mkdir()
        result = discover_wikis(wikis_dir)
        assert "not-a-wiki" not in result

    def test_discover_returns_empty_for_missing_dir(self, tmp_path: Path):
        result = discover_wikis(tmp_path / "nonexistent")
        assert result == {}

    def test_discover_returns_empty_for_empty_dir(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        result = discover_wikis(d)
        assert result == {}


# ── Tree ────────────────────────────────────────────────────────────────────


class TestBuildTree:
    def test_single_wiki_tree(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home",
            "sub/page.md": "# Page",
        })
        tree = build_tree(wikis_dir / "main")
        assert tree.name == "wiki"
        assert tree.kind == "dir"
        names = {c.name for c in tree.children}
        assert "index" in names
        folders = [c for c in tree.children if c.kind == "dir"]
        assert len(folders) == 1
        assert folders[0].name == "sub"

    def test_summaries_folder_excluded_from_tree(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home",
            "concepts/foo.md": "# Foo",
            "summaries/src.md": "# Src",
        })
        tree = build_tree(wikis_dir / "main")
        top_names = {c.name for c in tree.children}
        assert "summaries" not in top_names
        assert "concepts" in top_names

    def test_summaries_still_resolvable(self, wikis_dir: Path):
        # Escluso dall'albero/grafo ma la pagina resta raggiungibile (link Sources).
        wiki_root = _make_wiki(wikis_dir, "main", {"summaries/src.md": "# Src"})
        assert resolve_wikilink(wiki_root, "summaries/src.md") is not None

    def test_empty_wiki_returns_empty_tree(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        tree = build_tree(wikis_dir / "main")
        assert tree.kind == "dir"
        assert tree.children == []

    def test_missing_wiki_root(self, tmp_path: Path):
        tree = build_tree(tmp_path / "nonexistent")
        assert tree.name == "wiki"
        assert tree.children == []

    def test_file_paths_have_no_leading_slash(self, wikis_dir: Path):
        # I path dei file non devono iniziare con '/' (verrebbero scartati come
        # assoluti da safe_wiki_page_path → 400 nel drawer file).
        from jenny.webui.wiki_routes import safe_wiki_page_path

        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home",
            "concepts/page.md": "# Page",
        })
        tree = build_tree(wikis_dir / "main")

        paths: list[str] = []

        def collect(node):
            if node.kind == "file":
                paths.append(node.path)
            for c in node.children or []:
                collect(c)

        collect(tree)
        assert paths, "tree should contain files"
        for p in paths:
            assert not p.startswith("/"), p
            assert safe_wiki_page_path(p) == p


class TestBuildHomeTree:
    def test_home_tree_with_index(self, wikis_dir: Path):
        (wikis_dir / "_index.md").write_text("# Home")
        _make_wiki(wikis_dir, "main", {"index.md": "# Main"})
        tree = build_home_tree(wikis_dir)
        assert tree.name == "wikis"
        names = {c.name for c in tree.children}
        assert "Home" in names
        assert "main" in names

    def test_home_tree_without_index(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {"index.md": "# Main"})
        tree = build_home_tree(wikis_dir)
        names = {c.name for c in tree.children}
        assert "Home" not in names
        assert "main" in names

    def test_home_tree_empty(self, wikis_dir: Path):
        tree = build_home_tree(wikis_dir)
        assert tree.name == "wikis"
        assert tree.children == []


# ── Graph ───────────────────────────────────────────────────────────────────


class TestBuildGraph:
    def test_single_wiki_graph(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home",
            "concepts/page.md": "# Concept",
        })
        graph = build_graph(wikis_dir / "main")
        node_ids = {n.id for n in graph.nodes}
        assert "wiki/index.md" in node_ids
        assert "wiki/concepts/page.md" in node_ids

    def test_graph_with_wikilinks(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\n\nVedi [[Target]].",
            "target.md": "# Target",
        })
        graph = build_graph(wikis_dir / "main")
        edges = {(e.source, e.target) for e in graph.edges}
        assert ("wiki/index.md", "wiki/target.md") in edges

    def test_graph_self_link_ignored(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\n\nVedi [[index]].",
        })
        graph = build_graph(wikis_dir / "main")
        assert len(graph.edges) == 0

    def test_graph_anchor_link_ignored(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\n\nVedi [[#Anchor]].",
        })
        graph = build_graph(wikis_dir / "main")
        assert len(graph.edges) == 0

    def test_graph_empty_wiki(self, wikis_dir: Path):
        (wikis_dir / "main" / "wiki").mkdir(parents=True)
        graph = build_graph(wikis_dir / "main")
        assert graph.nodes == []
        assert graph.edges == []

    def test_mutual_wikilinks_produce_single_edge(self, wikis_dir: Path):
        # A↔B: un solo arco non orientato, degree contato una volta per lato.
        _make_wiki(wikis_dir, "main", {
            "a.md": "# A\n\nVedi [[b]].",
            "b.md": "# B\n\nVedi [[a]].",
        })
        graph = build_graph(wikis_dir / "main")
        assert len(graph.edges) == 1
        degrees = {n.id: n.degree for n in graph.nodes}
        assert degrees["wiki/a.md"] == 1
        assert degrees["wiki/b.md"] == 1

    def test_ambiguous_stem_does_not_resolve(self, wikis_dir: Path):
        # Due file con lo stesso stem in cartelle diverse: un link per stem
        # nudo non deve risolvere (ambiguo → nessun arco non deterministico).
        _make_wiki(wikis_dir, "main", {
            "concepts/foo.md": "# Foo concept",
            "entities/foo.md": "# Foo entity",
            "index.md": "# Home\n\nVedi [[foo]].",
        })
        graph = build_graph(wikis_dir / "main")
        assert graph.edges == []

    def test_summaries_excluded_from_graph(self, wikis_dir: Path):
        # I summaries sono livello di citazione: fuori dal grafo (nodi e archi).
        _make_wiki(wikis_dir, "main", {
            "index.md": "# Home",
            "concepts/foo.md": "# Foo\n\nFonte [[summaries/src]].",
            "summaries/src.md": "# Src summary",
        })
        graph = build_graph(wikis_dir / "main")
        node_ids = {n.id for n in graph.nodes}
        assert "wiki/summaries/src.md" not in node_ids
        assert "wiki/concepts/foo.md" in node_ids
        # Nessun arco verso il summary escluso.
        for e in graph.edges:
            assert "summaries" not in e.source
            assert "summaries" not in e.target

    def test_relpath_link_resolves_unambiguously(self, wikis_dir: Path):
        # Con stem ambiguo, il link per rel-path completo risolve al nodo giusto.
        _make_wiki(wikis_dir, "main", {
            "concepts/foo.md": "# Foo concept",
            "entities/foo.md": "# Foo entity",
            "index.md": "# Home\n\nVedi [[concepts/foo]].",
        })
        graph = build_graph(wikis_dir / "main")
        edges = {(e.source, e.target) for e in graph.edges}
        assert ("wiki/index.md", "wiki/concepts/foo.md") in edges
        assert len(graph.edges) == 1


class TestBuildHomeGraph:
    def test_home_graph_star(self, wikis_dir: Path):
        _make_wiki(wikis_dir, "main", {"index.md": "# Main"})
        _make_wiki(wikis_dir, "loops", {"index.md": "# Loops"})
        graph = build_home_graph(wikis_dir)
        node_ids = {n.id for n in graph.nodes}
        assert "_home" in node_ids
        assert "main" in node_ids
        assert "loops" in node_ids
        edges = {(e.source, e.target) for e in graph.edges}
        assert ("_home", "main") in edges
        assert ("_home", "loops") in edges
        home = next(n for n in graph.nodes if n.id == "_home")
        assert home.group == "home"

    def test_home_graph_empty(self, wikis_dir: Path):
        graph = build_home_graph(wikis_dir)
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "_home"
        assert graph.edges == []


# ── Renderer ────────────────────────────────────────────────────────────────


class TestCreateRenderer:
    def test_basic_page_render(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        renderer = create_renderer(wiki_root)
        result = renderer("# Hello")
        assert "<h1" in result.html
        assert "Hello" in result.html
        assert result.title == "Hello"

    def test_frontmatter_title(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root)
        raw = "---\ntitle: My Page\n---\n\nContent"
        result = renderer(raw)
        assert result.title == "My Page"
        assert result.frontmatter == {"title": "My Page"}

    def test_same_wiki_wikilink(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {
            "target.md": "# Target",
        })
        renderer = create_renderer(wiki_root, current_wiki="main", wikis_map={"main": wiki_root})
        result = renderer("Vedi [[Target]].")
        assert 'href="/?wiki=main' in result.html
        assert "page=" in result.html

    def test_cross_wiki_wikilink(self, wikis_dir: Path):
        main_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        other_root = _make_wiki(wikis_dir, "other", {"page.md": "# Other"})
        wikis_map = {"main": main_root, "other": other_root}
        renderer = create_renderer(main_root, current_wiki="main", wikis_map=wikis_map)
        result = renderer("Vedi [[other:page]].")
        assert 'href="/?wiki=other&page=page.md"' in result.html

    def test_anchor_only_wikilink(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root, current_wiki="main")
        result = renderer("Vedi [[#Open Questions]].")
        assert 'href="#open-questions"' in result.html

    def test_dead_wikilink(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root, current_wiki="main", wikis_map={"main": wiki_root})
        result = renderer("Vedi [[Nonexistent]].")
        assert '/?wiki=main&page=Nonexistent' in result.html

    def test_escaped_pipe_in_table_wikilink(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        renderer = create_renderer(wiki_root, current_wiki="main", wikis_map={"main": wiki_root})
        result = renderer("| Link |\n|------|\n| [[index\\|Apri]] |")
        assert 'href="/?wiki=main&page=index.md"' in result.html
        assert ">Apri</a>" in result.html

    def test_cross_wiki_strips_wiki_prefix(self, wikis_dir: Path):
        main_root = _make_wiki(wikis_dir, "main", {"page.md": "# Page"})
        wikis_map = {"main": main_root}
        renderer = create_renderer(wikis_dir, current_wiki=None, wikis_map=wikis_map)
        result = renderer("Vedi [[main/wiki/page|main/page]].")
        assert 'href="/?wiki=main&page=page.md"' in result.html

    def test_cross_wiki_directory_index(self, wikis_dir: Path):
        main_root = _make_wiki(wikis_dir, "main", {"concepts/page/index.md": "# Page"})
        wikis_map = {"main": main_root}
        renderer = create_renderer(wikis_dir, current_wiki=None, wikis_map=wikis_map)
        result = renderer("Vedi [[main/concepts/page|main/page]].")
        assert 'href="/?wiki=main&page=concepts/page/index.md"' in result.html

    def test_mermaid_preserved(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root)
        raw = "```mermaid\ngraph TD\nA-->B\n```"
        result = renderer(raw)
        assert 'class="mermaid-block"' in result.html

    def test_raw_markdown_preserved(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": ""})
        renderer = create_renderer(wiki_root)
        raw = "# Hello\n\nWorld"
        result = renderer(raw)
        assert result.raw_markdown == raw


# ── Audit ───────────────────────────────────────────────────────────────────


class TestAudit:
    def test_create_and_list_audit(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\ncontent here",
        })
        result = create_audit(
            wiki_root=wiki_root,
            target="index.md",
            raw_markdown="# Home\ncontent here",
            sel_start=8,
            sel_end=15,
            comment="typo",
            severity="warn",
            author="test",
        )
        assert "id" in result
        assert result["filename"]

        audits = load_audits(wiki_root, target="index.md", mode="open")
        assert len(audits) == 1
        assert audits[0].target == "index.md"

    def test_list_audits_empty(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        audits = load_audits(wiki_root, target="index.md", mode="open")
        assert audits == []

    def test_create_audit_missing_target(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        with pytest.raises(FileNotFoundError):
            create_audit(
                wiki_root=wiki_root,
                target="nonexistent.md",
                raw_markdown="",
                sel_start=0,
                sel_end=0,
                comment="test",
                severity="warn",
                author="test",
            )

    def test_resolve_audit(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\ncontent here",
        })
        created = create_audit(
            wiki_root=wiki_root,
            target="index.md",
            raw_markdown="# Home\ncontent here",
            sel_start=8,
            sel_end=15,
            comment="typo",
            severity="warn",
            author="test",
        )
        audit_id = created["id"]
        resolve_result = resolve_audit(wiki_root, audit_id, "fixed")
        assert "id" in resolve_result
        # I path restituiti devono essere relativi a wiki_root, mai assoluti
        # (non esporre il layout del filesystem dell'host).
        assert not resolve_result["from"].startswith("/")
        assert not resolve_result["to"].startswith("/")
        assert resolve_result["from"].startswith("audit/")
        assert resolve_result["to"].startswith("audit/resolved/")

        open_audits = load_audits(wiki_root, mode="open")
        assert len(open_audits) == 0

        resolved_audits = load_audits(wiki_root, mode="resolved")
        assert len(resolved_audits) == 1
        assert resolved_audits[0].status == "resolved"

    def test_failed_write_leaves_no_half_written_audit(
        self, wikis_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Una scrittura fallita non deve lasciare un audit troncato sul disco.

        Un audit a metà è illeggibile da ``load_audits`` ma occupa il suo id: il
        rename finale dell'helper atomico rende il file visibile solo completo.
        """
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home\ncontent here"})

        def boom(*_args, **_kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr("jenny.webui.wiki.atomic_write", boom)
        with pytest.raises(OSError):
            create_audit(
                wiki_root=wiki_root,
                target="index.md",
                raw_markdown="# Home\ncontent here",
                sel_start=8,
                sel_end=15,
                comment="typo",
                severity="warn",
                author="test",
            )
        assert load_audits(wiki_root, mode="all") == []

    def test_failed_resolve_keeps_the_open_audit(
        self, wikis_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Se la copia risolta non riesce, l'audit aperto resta dov'è.

        ``resolve_audit`` scrive la copia *prima* di cancellare l'originale, così
        il caso peggiore è un duplicato — mai un audit perso.
        """
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home\ncontent here"})
        created = create_audit(
            wiki_root=wiki_root,
            target="index.md",
            raw_markdown="# Home\ncontent here",
            sel_start=8,
            sel_end=15,
            comment="typo",
            severity="warn",
            author="test",
        )

        def boom(*_args, **_kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr("jenny.webui.wiki.atomic_write", boom)
        with pytest.raises(OSError):
            resolve_audit(wiki_root, created["id"], "fixed")

        open_audits = load_audits(wiki_root, mode="open")
        assert len(open_audits) == 1
        assert load_audits(wiki_root, mode="resolved") == []

    def test_list_audits_mode_all(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\ncontent here",
        })
        create_audit(
            wiki_root=wiki_root,
            target="index.md",
            raw_markdown="# Home\ncontent here",
            sel_start=8,
            sel_end=15,
            comment="typo",
            severity="warn",
            author="test",
        )
        audits_all = load_audits(wiki_root, mode="all")
        assert len(audits_all) == 1


# ── Wikilink Resolution ─────────────────────────────────────────────────


class TestResolveWikilink:
    def test_exact_match(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"target.md": "# Target"})
        result = resolve_wikilink(wiki_root, "target.md")
        assert result is not None
        assert result.name == "target.md"

    def test_case_insensitive(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"Target.md": "# Target"})
        result = resolve_wikilink(wiki_root, "target.md")
        assert result is not None

    def test_slug_match(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"sviluppo-idee.md": "# Sviluppo Idee"})
        result = resolve_wikilink(wiki_root, "Sviluppo Idee")
        assert result is not None
        assert result.name == "sviluppo-idee.md"

    def test_subdir_resolution(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"concepts/page.md": "# Page"})
        result = resolve_wikilink(wiki_root, "concepts/page.md")
        assert result is not None

    def test_no_match_returns_none(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"index.md": "# Home"})
        result = resolve_wikilink(wiki_root, "nonexistent.md")
        assert result is None

    def test_strips_wiki_prefix(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"page.md": "# Page"})
        result = resolve_wikilink(wiki_root, "wiki/page")
        assert result is not None
        assert result.name == "page.md"

    def test_directory_with_index(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {"concepts/page/index.md": "# Page"})
        result = resolve_wikilink(wiki_root, "concepts/page")
        assert result is not None
        assert result.name == "index.md"


# ── Wikilink Splitting ─────────────────────────────────────────────────────


class TestSplitWikilink:
    def test_plain_separator(self):
        assert _split_wikilink("target|label") == ("target", "label")

    def test_escaped_pipe_separator(self):
        assert _split_wikilink("target\\|label") == ("target", "label")

    def test_no_separator(self):
        assert _split_wikilink("target") == ("target", None)


# ── list_audits() compatibility ─────────────────────────────────────────────


class TestListAuditsCompat:
    """Tests for the list_audits() HTTP-friendly wrapper."""

    def test_list_audits_returns_list_of_dicts(self, wikis_dir: Path):
        wiki_root = _make_wiki(wikis_dir, "main", {
            "index.md": "# Home\ncontent here",
        })
        create_audit(
            wiki_root=wiki_root,
            target="index.md",
            raw_markdown="# Home\ncontent here",
            sel_start=8,
            sel_end=15,
            comment="typo",
            severity="warn",
            author="test",
        )
        entries = list_audits(wiki_root, target="index.md", mode="open")
        assert isinstance(entries, list)
        if entries:
            assert isinstance(entries[0], dict)
            assert "id" in entries[0]
            assert "severity" in entries[0]


# ── Frontmatter allowlist (/api/page privacy) ───────────────────────────────


class TestFrontmatterAllowlist:
    def test_drops_internal_keys(self):
        from jenny.webui.wiki_routes import _filter_frontmatter

        fm = {
            "title": "Foo",
            "tags": ["a", "b"],
            "type": "concept",
            "source_url": "https://internal/secret",
            "provenance": "scaffold-run-42",
            "draft": True,
        }
        filtered = _filter_frontmatter(fm)
        assert filtered == {"title": "Foo", "tags": ["a", "b"], "type": "concept"}
        assert "source_url" not in filtered
        assert "provenance" not in filtered
        assert "draft" not in filtered

    def test_none_and_non_dict_passthrough(self):
        from jenny.webui.wiki_routes import _filter_frontmatter

        assert _filter_frontmatter(None) is None
        assert _filter_frontmatter("not a dict") == "not a dict"
