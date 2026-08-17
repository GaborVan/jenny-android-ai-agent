"""Il prompt di Dream non deve offrirgli una destinazione che il suo registry rifiuta.

Trovato girando il build sul Titan 2, non leggendolo. Al primo run di Dream dopo
l'installazione: ``write_file({"path": "AGENTS.md", ...})`` rifiutato, nessun'altra
scrittura, ``cursor remains at 92``, ``stuck`` da 0 a 1 — 35 secondi di turno LLM
buttati, e il batch che tornava identico al run dopo.

La causa è una contraddizione fra due pezzi dello stesso prompt. La sezione *Which
File a Fact Belongs In* di ``agent/tool_contract.md`` elenca quattro quaderni,
``AGENTS.md`` compreso, e sta nella coda che nessun gate per-tool tocca: la legge
anche Dream. Ma la allowlist di scrittura di ``build_dream_tools`` è di tre file più
le skill, e ``AGENTS.md`` ne è fuori per una decisione deliberata (il permesso di
scrittura è ancora aperto in ``roadmap/agents-md-ownership.md``). Quel routing è
scritto per l'agente principale, che lì può scrivere.

Il costo di un rifiuto non è un tentativo sprecato ma il run intero: la regola di
commit (v. ``test_dream_budget_refusal_commit``) esiste proprio per non far avanzare
il cursore su un fatto non scritto, quindi indirizzare Dream su un file vietato
manda in stallo anche i fatti che avrebbe salvato.

I due assert sono le due metà della stessa invariante, e servono entrambi: se
qualcuno concede a Dream la scrittura su ``AGENTS.md`` cade il primo, se qualcuno
toglie la riga dal prompt cade il secondo.
"""

from __future__ import annotations

from pathlib import Path

from jenny.agent.memory import MemoryStore
from jenny.utils.prompt_templates import render_template


def _dream_write_surface(tmp_path: Path) -> dict[str, tuple[str | None, set[str]]]:
    """Per ogni tool di scrittura di Dream: la dir consentita e i file esatti.

    Le due metà contano entrambe. ``write_file`` non ha file extra e vive nella
    sola ``skills/``; sono ``edit_file`` e ``apply_patch`` a portare i tre file di
    memoria. Un test che guardasse un solo tool concluderebbe il falso.
    """
    store = MemoryStore(tmp_path)
    tools = store.build_dream_tools()
    surface: dict[str, tuple[str | None, set[str]]] = {}
    for name in ("write_file", "edit_file", "apply_patch"):
        tool = tools.get(name)
        assert tool is not None, f"il registry di Dream deve avere {name}"
        state = vars(tool)
        allowed_dir = state.get("_allowed_dir")
        files = state.get("_extra_write_allowed_files")
        assert files is not None, (
            f"{name} deve esporre la allowlist di file esatti; se l'attributo "
            "cambia nome, questo test va aggiornato con esso"
        )
        surface[name] = (
            Path(allowed_dir).name if allowed_dir else None,
            {Path(p).name for p in files},
        )
    return surface


def test_no_dream_tool_can_write_agents_md(tmp_path: Path) -> None:
    """``AGENTS.md`` non è raggiungibile da nessuno dei tre tool di scrittura."""
    surface = _dream_write_surface(tmp_path)
    assert surface["write_file"] == ("skills", set())
    for name in ("edit_file", "apply_patch"):
        assert surface[name] == ("skills", {"SOUL.md", "USER.md", "MEMORY.md"}), surface[name]
    for name, (_, files) in surface.items():
        assert "AGENTS.md" not in files, f"{name} ha guadagnato AGENTS.md"


def test_dream_prompt_says_agents_md_is_not_writable(tmp_path: Path) -> None:
    """E il prompt glielo dice, perché il contratto dei tool gliela nomina."""
    prompt = render_template(
        "agent/dream.md", strip=True, skill_creator_path="/w/skills/skill-creator/SKILL.md",
        budget_gauge="",
    )
    assert "AGENTS.md" in prompt, (
        "tool_contract.md elenca AGENTS.md fra i quaderni e Dream legge quella coda: "
        "se dream.md non la smentisce, Dream ci prova e perde il run"
    )
    # Non basta che il nome compaia: deve comparire come divieto.
    head = prompt[: prompt.find("**Routing examples:**")]
    assert "not yours either" in head
    assert "refused" in head
