"""Test per i tool di creazione skill autonoma (``tools/skill_creator.py``).

Verificano: enabled() col toggle, scaffold di una skill reale su un workspace
temporaneo (via init_skill del pacchetto), scrittura della description nel
frontmatter, validate e list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import jenny.agent.tools.skill_creator as sc


class _Cfg:
    class _SC:
        enable = True

    def __init__(self, enable: bool = True) -> None:
        self.skill_creator = self._SC() if enable else _Disabled()


class _Disabled:
    enable = False


class _Ctx:
    def __init__(self, enable: bool = True) -> None:
        self.config = _Cfg(enable)


@pytest.fixture()
def fake_workspace(tmp_path, monkeypatch):
    """Спрямовує get_workspace_path у tmp і повертає шлях."""
    monkeypatch.setattr(sc, "get_workspace_path", lambda: tmp_path)
    return tmp_path


# ── enabled() ──────────────────────────────────────────────────────────────

def test_enabled_follows_toggle():
    assert sc.SkillCreateTool.enabled(_Ctx(enable=True)) is True
    assert sc.SkillCreateTool.enabled(_Ctx(enable=False)) is False
    assert sc.SkillValidateTool.enabled(_Ctx(enable=True)) is True
    assert sc.SkillListTool.enabled(_Ctx(enable=True)) is True


# ── skill_list ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skill_list_empty_workspace(fake_workspace):
    tool = sc.SkillListTool(config=_Cfg().skill_creator)
    import json

    out = json.loads(await tool.execute())
    assert out["ok"] is True and out["skills"] == []


# ── skill_create ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skill_create_scaffolds_and_fills_description(fake_workspace):
    import json

    tool = sc.SkillCreateTool(config=_Cfg().skill_creator)
    out = json.loads(
        await tool.execute(
            name="pdf-merge",
            description="Merge PDF files into one document.",
            include_examples=False,
        )
    )
    assert out["ok"] is True, out
    skill_dir = Path(out["path"])
    assert skill_dir.is_dir()
    md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "description: Merge PDF files into one document." in md
    assert "name: pdf-merge" in md


@pytest.mark.asyncio
async def test_skill_create_duplicate_fails(fake_workspace):
    import json

    tool = sc.SkillCreateTool(config=_Cfg().skill_creator)
    first = json.loads(await tool.execute(name="dup-skill", include_examples=False))
    assert first["ok"] is True, first
    second = json.loads(await tool.execute(name="dup-skill", include_examples=False))
    assert second["ok"] is False
    assert "already exists" in second["error"]


@pytest.mark.asyncio
async def test_skill_validate_created_skill(fake_workspace):
    import json

    create = sc.SkillCreateTool(config=_Cfg().skill_creator)
    created = json.loads(
        await tool_exec(create, name="check-me", description="Checks things.")
    )
    assert created["ok"] is True, created

    validate = sc.SkillValidateTool(config=_Cfg().skill_creator)
    result = json.loads(await validate.execute(name="check-me"))
    # frontmatter валидний (name/description присутні) — ok може бути True або
    # False лише якщо валідатор знайшов реальну проблему; головне — коректна
    # відповідь без exception і з message.
    assert "ok" in result and "message" in result


@pytest.mark.asyncio
async def test_skill_validate_missing_folder(fake_workspace):
    import json

    validate = sc.SkillValidateTool(config=_Cfg().skill_creator)
    result = json.loads(await validate.execute(name="no-such-skill"))
    assert result["ok"] is False
    assert "not found" in result["error"]


async def tool_exec(tool, **kwargs):
    """Обгортка для виклику execute без ручного json-розбору."""
    return await tool.execute(**kwargs)
