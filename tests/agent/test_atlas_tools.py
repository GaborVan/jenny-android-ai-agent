"""Sandbox di scrittura di Atlas.

Atlas e Dream scrivono nella stessa cartella e a cadenze diverse. L'unica cosa
che impedisce a uno di cancellare il lavoro dell'altro è che i loro registry
non si sovrappongono: Atlas può scrivere ``memory/WIKI.md`` e nient'altro,
Dream può scrivere tutto tranne quello. Questi test difendono quel confine.
"""

from __future__ import annotations

import pytest

from jenny.agent.atlas import AtlasStore
from jenny.agent.memory import MemoryStore
from jenny.security.workspace_access import (
    bind_workspace_scope,
    default_workspace_scope,
    reset_workspace_scope,
)

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


@pytest.fixture
def store(tmp_path) -> AtlasStore:
    memory = MemoryStore(tmp_path)
    memory.memory_file.write_text("# Memory\n- Project X active\n", encoding="utf-8")
    memory.soul_file.write_text("# Soul\n- Helpful\n", encoding="utf-8")
    memory.user_file.write_text("# User\n- Speaks Italian\n", encoding="utf-8")
    (tmp_path / "wikis" / "main" / "wiki").mkdir(parents=True)
    return AtlasStore(tmp_path)


class TestToolset:
    def test_exposes_reading_plus_a_single_write_target(self, store):
        tools = store.build_tools()

        assert set(tools.tool_names) == {
            "read_file",
            "list_dir",
            "find_files",
            "grep",
            "write_file",
            "edit_file",
            "apply_patch",
        }

    def test_has_no_general_purpose_tools(self, store):
        names = set(store.build_tools().tool_names)

        assert "python_exec" not in names
        assert "message" not in names
        assert "web_fetch" not in names


class TestWriteBoundary:
    @pytest.mark.asyncio
    async def test_can_write_the_directory(self, store):
        tools = store.build_tools()

        result = await tools.execute(
            "write_file",
            {"path": "memory/WIKI.md", "content": "# Wiki Directory\n"},
        )

        assert "Successfully wrote" in result
        assert store.wiki_file.read_text(encoding="utf-8") == "# Wiki Directory\n"

    @pytest.mark.asyncio
    async def test_can_edit_the_directory_in_place(self, store):
        store.wiki_file.write_text("# Wiki Directory\n\n## People\n- **Ada**\n", encoding="utf-8")
        tools = store.build_tools()

        result = await tools.execute(
            "edit_file",
            {"path": "memory/WIKI.md", "old_text": "**Ada**", "new_text": "**Ada Lovelace**"},
        )

        assert "Successfully edited" in result
        assert "Ada Lovelace" in store.wiki_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target", ["memory/MEMORY.md", "SOUL.md", "USER.md"])
    async def test_cannot_touch_dream_owned_files(self, store, target):
        tools = store.build_tools()
        before = (store.workspace / target).read_text(encoding="utf-8")

        result = await tools.execute("write_file", {"path": target, "content": "hijacked"})

        assert "Error" in result
        assert (store.workspace / target).read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_apply_patch_obeys_the_same_boundary(self, store):
        tools = store.build_tools()
        before = (store.workspace / "memory" / "MEMORY.md").read_text(encoding="utf-8")

        result = await tools.execute(
            "apply_patch",
            {
                "edits": [
                    {
                        "path": "memory/MEMORY.md",
                        "action": "replace",
                        "old_text": "Project X active",
                        "new_text": "hijacked",
                    }
                ]
            },
        )

        assert "hijacked" not in result or "Error" in result
        assert (store.workspace / "memory" / "MEMORY.md").read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_cannot_write_a_new_file_anywhere_in_the_workspace(self, store):
        tools = store.build_tools()

        result = await tools.execute(
            "write_file", {"path": "memory/SOMETHING.md", "content": "nope"}
        )

        assert "Error" in result
        assert not (store.workspace / "memory" / "SOMETHING.md").exists()

    @pytest.mark.asyncio
    async def test_cannot_write_into_the_wiki_it_reads(self, store):
        """La wiki è la fonte: riscriverla sarebbe un loop di auto-alimentazione."""
        tools = store.build_tools()

        result = await tools.execute(
            "write_file", {"path": "wikis/main/wiki/index.md", "content": "rewritten"}
        )

        assert "Error" in result
        assert not (store.workspace / "wikis" / "main" / "wiki" / "index.md").exists()

    @pytest.mark.asyncio
    async def test_write_scope_holds_even_with_full_workspace_access(self, store):
        """Uno scope 'accesso libero' non deve allargare la allowlist del run."""
        tools = store.build_tools()
        scope = default_workspace_scope(store.workspace, restrict_to_workspace=False)
        outside = store.workspace.parent / f"{store.workspace.name}-outside"
        outside.mkdir()
        target = outside / "escape.txt"

        token = bind_workspace_scope(scope)
        try:
            result = await tools.execute(
                "write_file", {"path": str(target), "content": "owned"}
            )
        finally:
            reset_workspace_scope(token)

        assert "Error" in result
        assert not target.exists()


class TestReadBoundary:
    @pytest.mark.asyncio
    async def test_can_read_the_wiki(self, store):
        page = store.workspace / "wikis" / "main" / "wiki" / "index.md"
        page.write_text("# Index\n- [[entities/Ada]]\n", encoding="utf-8")
        tools = store.build_tools()

        result = await tools.execute("read_file", {"path": "wikis/main/wiki/index.md"})

        assert "[[entities/Ada]]" in result

    @pytest.mark.asyncio
    async def test_can_grep_across_the_wiki(self, store):
        page = store.workspace / "wikis" / "main" / "wiki" / "entities" / "ada.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Ada\nnickname: Countess\n", encoding="utf-8")
        tools = store.build_tools()

        result = await tools.execute("grep", {"pattern": "nickname", "path": "wikis"})

        assert "ada.md" in result


class TestFileStates:
    def test_registry_exposes_the_run_write_tracker(self, store):
        tools = store.build_tools()

        assert getattr(tools, "file_states", None) is not None
        assert tools.file_states.writes_ok == 0
        assert tools.file_states.writes_attempted == 0

    @pytest.mark.asyncio
    async def test_a_blocked_write_counts_as_attempted_not_ok(self, store):
        tools = store.build_tools()

        await tools.execute("write_file", {"path": "memory/MEMORY.md", "content": "x"})

        assert tools.file_states.writes_attempted > 0
        assert tools.file_states.writes_ok == 0
