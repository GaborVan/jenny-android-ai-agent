"""Tools per la creazione autonoma di skill (senza guided conversation).

Espongono a Jenny il ``skill-creator`` built-in come veri tool: può creare una
nuova skill (struttura + SKILL.md da template), validarla e listare le skill
esistenti — da sola, quando vede una attività ripetibile, senza aspettare la
frase-innesco dell'utente.

Il lavoro vero lo fanno gli script esistenti della skill ``skill-creator``
(``init_skill.py``, ``quick_validate.py``), caricati da importlib: stessi
template, stessa validazione, nessuna logica duplicata. Lo script si cerca
prima nel workspace (``<workspace>/skills/skill-creator/scripts/``, dove
Android lo copia) e poi nel pacchetto (albero sorgente).

Confine: la creazione scrive sotto ``<workspace>/skills/<name>/`` — rispetta la
workspace policy come qualunque altro tool di scrittura. Il toggle
``tools.skill_creator.enable`` è la serratura lato agente.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import (
    StringSchema,
    tool_parameters_schema,
)
from jenny.config.paths import get_workspace_path
from jenny.config.tool_schemas import SkillCreatorConfig

# Sottocartelle risorsa ammesse da init_skill (whitelist).
_ALLOWED_RESOURCES = {"scripts", "references", "assets"}


def _scripts_dir() -> Path | None:
    """Directory degli script di ``skill-creator`` (workspace prima, pkg poi)."""
    workspace = get_workspace_path()
    candidates = []
    if workspace is not None:
        candidates.append(workspace / "skills" / "skill-creator" / "scripts")
    # Albero sorgente (host/dev e fallback): il pacchetto jenny sta accanto a
    # questo modulo.
    candidates.append(Path(__file__).resolve().parent.parent.parent / "skills" / "skill-creator" / "scripts")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _load_script(scripts_dir: Path, module_name: str) -> Any | None:
    """Carica uno script della skill-creator via importlib (no side effects)."""
    path = scripts_dir / f"{module_name}.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"_jenny_skill_{module_name}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001
        return None
    return module


def _enabled(ctx: Any) -> bool:
    cfg = getattr(ctx.config, "skill_creator", None)
    return cfg is not None and cfg.enable


def _run_guard() -> str | None:
    """Ritorna un errore leggibile se la skill-creator non è disponibile."""
    scripts = _scripts_dir()
    if scripts is None:
        return "skill-creator scripts not found (workspace or package)"
    if not (scripts / "init_skill.py").is_file():
        return "init_skill.py not found under skill-creator/scripts"
    return None


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": (
                    "Skill name, kebab-case (e.g. 'pdf-merge'). Directory name "
                    "under skills/."
                ),
            },
            "description": {
                "type": ["string", "null"],
                "maxLength": 500,
                "description": (
                    "One-paragraph description of what the skill does and when "
                    "to use it. Fills the SKILL.md frontmatter description."
                ),
            },
            "resources": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["scripts", "references", "assets"],
                },
                "description": (
                    "Subdirectories to scaffold (default: scripts only)."
                ),
            },
            "include_examples": {
                "type": "boolean",
                "description": "Create example files in the resource directories.",
                "default": False,
            },
        },
        "required": ["name"],
    }
)
class SkillCreateTool(Tool):
    """Crea una nuova skill (struttura + SKILL.md da template)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "skill_create"
    description = (
        "Create a new skill: scaffolds skills/<name>/ with a SKILL.md template "
        "and resource folders (scripts/references/assets). Use this when the "
        "user asks for a repeatable capability, or when you notice a task you "
        "keep doing manually — teach yourself a skill for next time. After "
        "creating, fill in the SKILL.md TODOs (write_file) and run "
        "skill_validate."
    )

    config_key = "skill_creator"

    @classmethod
    def config_cls(cls):
        return SkillCreatorConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.skill_creator)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        name: str,
        description: str | None = None,
        resources: list[str] | None = None,
        include_examples: bool = False,
        **kwargs: Any,
    ) -> str:
        guard = _run_guard()
        if guard:
            return json.dumps({"ok": False, "error": guard}, ensure_ascii=False)

        skill_name = name.strip().lower().replace("_", "-")
        resources = [r for r in (resources or ["scripts"]) if r in _ALLOWED_RESOURCES]

        workspace = get_workspace_path()
        skills_root = (workspace / "skills") if workspace else Path.cwd() / "skills"
        try:
            skills_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return json.dumps(
                {"ok": False, "error": f"cannot create skills dir: {exc}"},
                ensure_ascii=False,
            )

        scripts_dir = _scripts_dir()
        assert scripts_dir is not None  # guard sopra
        module = _load_script(scripts_dir, "init_skill")
        if module is None or not hasattr(module, "init_skill"):
            return json.dumps(
                {"ok": False, "error": "init_skill module failed to load"},
                ensure_ascii=False,
            )

        try:
            skill_dir = module.init_skill(
                skill_name, str(skills_root), resources, bool(include_examples)
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {"ok": False, "error": f"init_skill raised: {exc}"},
                ensure_ascii=False,
            )

        if skill_dir is None:
            target = skills_root / skill_name
            reason = "already exists" if target.exists() else "unknown error"
            return json.dumps(
                {"ok": False, "error": f"skill not created ({reason}): {target}"},
                ensure_ascii=False,
            )

        # Description nel frontmatter, se fornita: il template lascia un TODO.
        if description and description.strip():
            self._write_description(Path(skill_dir), description.strip())

        return json.dumps(
            {
                "ok": True,
                "path": str(skill_dir),
                "name": skill_name,
                "next": (
                    "Edit SKILL.md (fill TODOs / description), add logic under "
                    "scripts/, then run skill_validate."
                ),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _write_description(skill_dir: Path, description: str) -> None:
        """Sostituisce la description TODO nel frontmatter di SKILL.md."""
        md_path = skill_dir / "SKILL.md"
        try:
            lines = md_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        out: list[str] = []
        replaced = False
        for line in lines:
            stripped = line.strip()
            if not replaced and stripped.startswith("description:"):
                # description: [TODO: ...] (possibile su più righe col template)
                out.append(f"description: {description}")
                replaced = True
                continue
            out.append(line)
        if replaced:
            try:
                md_path.write_text("\n".join(out) + "\n", encoding="utf-8")
            except OSError:
                pass


@tool_parameters(
    tool_parameters_schema(
        name=StringSchema(
            "Skill name (kebab-case) or path to the skill folder to validate.",
            min_length=1,
        ),
        required=["name"],
    )
)
class SkillValidateTool(Tool):
    """Valida la struttura e il frontmatter di una skill esistente."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "skill_validate"
    description = (
        "Validate a skill folder: SKILL.md present, frontmatter well-formed, "
        "name/description present. Run after skill_create or after editing a "
        "skill. Pass the kebab-case name (validated under skills/) or a path."
    )

    config_key = "skill_creator"

    @classmethod
    def config_cls(cls):
        return SkillCreatorConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.skill_creator)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, name: str, **kwargs: Any) -> str:
        scripts_dir = _scripts_dir()
        if scripts_dir is None:
            return json.dumps(
                {"ok": False, "error": "skill-creator scripts not found"},
                ensure_ascii=False,
            )
        workspace = get_workspace_path()
        skills_root = (workspace / "skills") if workspace else Path.cwd() / "skills"

        candidate = Path(name).expanduser()
        if not candidate.is_absolute():
            candidate = skills_root / name
        if not candidate.exists():
            return json.dumps(
                {"ok": False, "error": f"skill folder not found: {candidate}"},
                ensure_ascii=False,
            )

        module = _load_script(scripts_dir, "quick_validate")
        if module is None or not hasattr(module, "validate_skill"):
            return json.dumps(
                {"ok": False, "error": "quick_validate failed to load"},
                ensure_ascii=False,
            )
        try:
            ok, message = module.validate_skill(str(candidate))
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {"ok": False, "error": f"validate_skill raised: {exc}"},
                ensure_ascii=False,
            )
        return json.dumps({"ok": bool(ok), "valid": bool(ok), "message": str(message)}, ensure_ascii=False)


@tool_parameters(tool_parameters_schema(required=[]))
class SkillListTool(Tool):
    """Elenca le skill presenti nel workspace."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "skill_list"
    description = (
        "List existing skills under skills/ with their frontmatter description "
        "(first line). Use to see what you already know before creating a new "
        "skill or when the user asks what you can do."
    )

    config_key = "skill_creator"

    @classmethod
    def config_cls(cls):
        return SkillCreatorConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.skill_creator)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        workspace = get_workspace_path()
        skills_root = (workspace / "skills") if workspace else Path.cwd() / "skills"
        if not skills_root.is_dir():
            return json.dumps({"ok": True, "skills": []}, ensure_ascii=False)

        skills: list[dict[str, str]] = []
        for folder in sorted(p for p in skills_root.iterdir() if p.is_dir()):
            md = folder / "SKILL.md"
            if not md.is_file():
                continue
            description = ""
            try:
                for line in md.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("description:"):
                        description = stripped[len("description:") :].strip().strip('"')
                        if description.startswith("[TODO"):
                            description = ""
                        break
            except OSError:
                pass
            skills.append({"name": folder.name, "description": description[:200]})

        return json.dumps({"ok": True, "skills": skills}, ensure_ascii=False)


TOOLS = [SkillCreateTool, SkillValidateTool, SkillListTool]
