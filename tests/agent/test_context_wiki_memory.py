"""La rubrica Atlas nel system prompt.

Il punto del meccanismo è che ``memory/WIKI.md`` sia in *ogni* prompt. Due
trappole da tenere chiuse: che il blocco sparisca perché MEMORY.md è ancora il
template intatto, e che un file lungo il doppio del dovuto si porti il proprio
costo su tutti i turni.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.context import ContextBuilder

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "memory").mkdir()
    return workspace


def _write_directory(workspace: Path, body: str) -> None:
    (workspace / "memory" / "WIKI.md").write_text(body, encoding="utf-8")


class TestInjection:
    def test_absent_file_adds_no_block(self, tmp_path):
        prompt = ContextBuilder(_workspace(tmp_path)).build_system_prompt()

        assert "## Wiki Directory" not in prompt

    def test_empty_file_adds_no_block(self, tmp_path):
        workspace = _workspace(tmp_path)
        _write_directory(workspace, "   \n\n")

        prompt = ContextBuilder(workspace).build_system_prompt()

        assert "## Wiki Directory" not in prompt

    def test_directory_lands_in_the_prompt(self, tmp_path):
        workspace = _workspace(tmp_path)
        _write_directory(workspace, "# Wiki Directory\n\n## People\n- **Ada** — maths → [[Ada]]")

        prompt = ContextBuilder(workspace).build_system_prompt()

        assert "## Wiki Directory" in prompt
        assert "**Ada** — maths" in prompt

    def test_survives_an_untouched_memory_template(self, tmp_path):
        """La regressione più facile da introdurre: annidare i due blocchi.

        Con MEMORY.md ancora uguale al template il blocco memoria viene saltato;
        la rubrica non deve sparire insieme a lui.
        """
        workspace = _workspace(tmp_path)
        from jenny.utils.helpers import load_bundled_template

        template = load_bundled_template("memory/MEMORY.md")
        assert template is not None
        (workspace / "memory" / "MEMORY.md").write_text(template, encoding="utf-8")
        _write_directory(workspace, "# Wiki Directory\n\n## People\n- **Ada**")

        prompt = ContextBuilder(workspace).build_system_prompt()

        assert "## Wiki Directory" in prompt
        assert "## Long-term Memory" not in prompt

    def test_both_sections_share_one_memory_heading(self, tmp_path):
        workspace = _workspace(tmp_path)
        (workspace / "memory" / "MEMORY.md").write_text("- Project X active", encoding="utf-8")
        _write_directory(workspace, "## People\n- **Ada**")

        prompt = ContextBuilder(workspace).build_system_prompt()

        assert prompt.count("# Memory\n") == 1
        assert prompt.index("## Long-term Memory") < prompt.index("## Wiki Directory")


class TestBudget:
    def test_long_directory_is_truncated_to_the_cap(self, tmp_path):
        workspace = _workspace(tmp_path)
        _write_directory(workspace, "\n".join(f"- **Entity {i}** — filler text" for i in range(4000)))

        prompt = ContextBuilder(workspace, wiki_directory_max_tokens=200).build_system_prompt()

        assert "Entity 0" in prompt
        assert "Entity 3999" not in prompt

    def test_short_directory_is_left_alone(self, tmp_path):
        workspace = _workspace(tmp_path)
        body = "## People\n- **Ada** — maths → [[Ada]]"
        _write_directory(workspace, body)

        prompt = ContextBuilder(workspace, wiki_directory_max_tokens=200).build_system_prompt()

        assert body in prompt


class TestPromptStability:
    def test_prompt_is_unchanged_when_the_directory_is_unchanged(self, tmp_path):
        """Il blocco entra nel prefisso cacheable: non deve variare da solo."""
        workspace = _workspace(tmp_path)
        _write_directory(workspace, "## People\n- **Ada**")
        builder = ContextBuilder(workspace)

        assert builder.build_system_prompt() == builder.build_system_prompt()
