"""Inventario, stato e prompt di Atlas (``jenny/agent/atlas.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.atlas import AtlasStore

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


def _make_wiki(
    workspace: Path,
    name: str,
    *,
    scope: str | None = None,
    entities: dict[str, str] | None = None,
    concepts: dict[str, str] | None = None,
) -> Path:
    root = workspace / "wikis" / name
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    header = f"---\nsummary: {scope}\n---\n\n# {name}\n" if scope else f"# {name}\n"
    (root / "AGENTS.md").write_text(header, encoding="utf-8")
    for group, pages in (("entities", entities), ("concepts", concepts)):
        for rel, body in (pages or {}).items():
            target = root / "wiki" / group / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
    return root


@pytest.fixture
def store(tmp_path) -> AtlasStore:
    (tmp_path / "memory").mkdir()
    return AtlasStore(tmp_path)


class TestState:
    def test_no_wikis_is_detected(self, store):
        assert store.has_wikis() is False

    def test_wikis_are_detected(self, store):
        _make_wiki(store.workspace, "main")

        assert store.has_wikis() is True

    def test_first_run_is_always_stale(self, store):
        _make_wiki(store.workspace, "main", entities={"ada.md": "# Ada"})

        assert store.last_fingerprint() == ""
        assert store.is_stale() is True

    def test_recording_the_fingerprint_clears_staleness(self, store):
        _make_wiki(store.workspace, "main", entities={"ada.md": "# Ada"})

        store.write_state(store.fingerprint())

        assert store.is_stale() is False

    def test_a_wiki_change_makes_it_stale_again(self, store):
        root = _make_wiki(store.workspace, "main", entities={"ada.md": "# Ada"})
        store.write_state(store.fingerprint())

        (root / "wiki" / "entities" / "grace.md").write_text("# Grace", encoding="utf-8")

        assert store.is_stale() is True

    def test_corrupt_state_file_reads_as_no_state(self, store):
        _make_wiki(store.workspace, "main")
        store.state_file.write_text("{not json", encoding="utf-8")

        assert store.last_fingerprint() == ""
        assert store.is_stale() is True


class TestInventory:
    def test_lists_every_wiki_with_scope_and_counts(self, store):
        _make_wiki(
            store.workspace, "main", scope="Personal projects", entities={"ada.md": "# Ada"}
        )
        _make_wiki(store.workspace, "loops", scope="AI loops", concepts={"ralph.md": "# Ralph"})

        inventory = store.build_inventory()

        assert "**main** — Personal projects" in inventory
        assert "**loops** — AI loops" in inventory
        # Un conteggio solo, non uno per gruppo: dal 22/08 (T3) la rubrica non
        # nomina più `entities`/`concepts`, perché sono i gruppi del pattern di
        # ricerca e un progetto nel formato nuovo ha le pagine piatte. Il numero
        # vale per entrambe le forme.
        assert "(1 pages)" in inventory

    def test_pages_are_listed_only_for_the_default_wiki(self, store):
        _make_wiki(store.workspace, "main", entities={"ada.md": "# Ada Lovelace"})
        _make_wiki(store.workspace, "loops", entities={"ralph.md": "# Ralph Wiggum"})

        inventory = store.build_inventory()

        assert "Ada Lovelace" in inventory
        assert "Ralph Wiggum" not in inventory

    def test_falls_back_to_the_first_wiki_when_the_default_is_absent(self, tmp_path):
        (tmp_path / "memory").mkdir()
        store = AtlasStore(tmp_path, default_wiki="nonexistent")
        _make_wiki(tmp_path, "loops", entities={"ralph.md": "# Ralph"})

        assert "Pages in `loops`" in store.build_inventory()

    def test_titles_fall_back_to_the_filename(self, store):
        _make_wiki(store.workspace, "main", entities={"untitled.md": "no heading here"})

        assert "`entities/untitled.md` — untitled" in store.build_inventory()

    def test_truncation_is_announced(self, tmp_path):
        (tmp_path / "memory").mkdir()
        store = AtlasStore(tmp_path, max_entries=3)
        _make_wiki(
            tmp_path,
            "main",
            entities={f"e{i}.md": f"# Entity {i}" for i in range(10)},
        )

        inventory = store.build_inventory()

        assert "inventory truncated at 3 pages" in inventory
        assert inventory.count("- `entities/") == 3

    def test_empty_wiki_says_so(self, store):
        _make_wiki(store.workspace, "main")

        assert "no pages yet" in store.build_inventory()

    def test_flat_pages_are_listed_too(self, store):
        """Il difetto che T3 chiude: la rubrica elencava solo `entities/` e
        `concepts/`, cioè i gruppi del pattern di ricerca. Su un progetto nel
        formato nuovo — pagine piatte sotto `wiki/` — trovava zero voci e
        dichiarava «no pages yet» a una cartella piena. Il silenzio più
        pericoloso: una rubrica che dice "non c'è niente" viene creduta.
        """
        wiki = store.workspace / "wikis" / "casa" / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "index.md").write_text("# Casa\n", encoding="utf-8")
        (wiki / "riscaldamento.md").write_text("# Riscaldamento\n", encoding="utf-8")
        (wiki / "proprietario.md").write_text("# Il proprietario\n", encoding="utf-8")

        inventory = store.build_inventory()

        assert "Riscaldamento" in inventory and "Il proprietario" in inventory
        # L'indice **è** la mappa, non una voce di rubrica: elencarlo fra le
        # pagine lo farebbe sembrare contenuto.
        assert "`index.md`" not in inventory
        assert "(2 pages)" in inventory


class TestPrompt:
    def test_carries_mechanism_inventory_and_current_file(self, store):
        _make_wiki(store.workspace, "main", entities={"ada.md": "# Ada"})
        store.wiki_file.write_text("# Wiki Directory\n\n## People\n- **Ada**", encoding="utf-8")

        prompt = store.build_prompt()

        assert "wiki directory compiler" in prompt
        assert "## Wiki Inventory" in prompt
        assert "Ada" in prompt
        assert "Current `memory/WIKI.md`" in prompt

    def test_marks_the_first_run(self, store):
        _make_wiki(store.workspace, "main")

        assert "_(empty — first run)_" in store.build_prompt()

    def test_user_policy_is_included_and_declared_authoritative(self, store):
        _make_wiki(store.workspace, "main")
        store.policy_file.write_text("Plants only if they have a nickname.", encoding="utf-8")

        prompt = store.build_prompt()

        assert "Plants only if they have a nickname." in prompt
        assert "## User Policy (authoritative" in prompt

    def test_policy_section_is_absent_when_there_is_no_policy(self, store):
        _make_wiki(store.workspace, "main")

        # Il template *nomina* la policy; quello che non deve comparire è la
        # sezione con il contenuto.
        assert "## User Policy" not in store.build_prompt()


class TestFromConfig:
    def test_reads_wiki_dir_and_default_from_config(self, tmp_path):
        from jenny.config.schema import Config

        config = Config()
        store = AtlasStore.from_config(tmp_path, config)

        assert store.wikis_dir == tmp_path / config.wiki.wikis_dir
        assert store.default_wiki == config.wiki.default_wiki


class TestSessionKey:
    def test_is_namespaced_so_it_reads_as_internal(self):
        from jenny.agent.memory import MemoryStore

        key = AtlasStore.session_key()

        assert key.startswith("atlas:")
        # Un run Atlas non deve rientrare come input di Dream.
        assert MemoryStore._is_internal_history_session(key)
