"""Lint dei documenti che le skill impacchettate spediscono sul telefono.

Perché esiste. Il 2026-08-11 un subagent ha scritto `wb_probe.py` nella radice
del workspace e l'ha importato con un `sys.path.insert` esplicito. Quando ha
trascritto quel codice — funzionante — dentro `skills/waterbot/SKILL.md`, ha
lasciato cadere il `sys.path.insert` sostituendolo con la prosa "con cwd =
workspace root". Da lì in poi ogni heartbeat ricopiava il blocco, l'import nudo
falliva e l'agente bruciava quattro tool call per cicli a riscoprire `sys.path`.
La documentazione da sola non basta: serve un test che diventi rosso.

Cosa controlla, sui file Markdown *impacchettati* (`SKILL.md` e
`references/*.md` di ogni skill in `jenny/skills/`):

1. nessuna invocazione di interprete/shell (`python3 x.py`, `bash …`): su questa
   piattaforma esiste solo `python_exec` (`jenny/templates/agent/tool_contract.md`);
2. nessun percorso con il nome della skill duplicato (`llm-wiki/llm-wiki/…`);
3. ogni blocco `python_exec` che tocca uno script della skill stessa deve
   passare `working_dir` puntato a `skills/<nome>/scripts`;
4. ogni `scripts/*.py` e `references/*.md` citato esiste su disco ed è nel
   manifest che finisce sul dispositivo (`_SKILLS_MANIFEST`).

Limiti dichiarati — questo lint è testuale, non semantico:

* i blocchi si individuano con un parser di fence Markdown, non con un parser
  Python: un `python_exec` costruito dinamicamente, spezzato su più fence o
  scritto in prosa non viene esaminato;
* il riferimento a uno script si riconosce per nome (`init_skill.py` o
  `import init_skill`): un modulo caricato via stringa calcolata sfugge;
* `working_dir` si verifica per *presenza* della chiave e per la sottostringa
  `skills/<nome>/scripts`, non valutando il valore reale;
* il controllo 4 esclude `skill-creator`, che documenta come si scrivono le
  skill *in generale*: i suoi `scripts/rotate_pdf.py` e `references/finance.md`
  sono esempi di skill ipotetiche, non file suoi;
* nulla qui prova che il codice documentato funzioni: prova solo che non
  contenga i tre difetti che sono già costati tempo sul dispositivo.
"""

from __future__ import annotations

import re
from pathlib import Path

from jenny.utils.android_assets import _SKILLS_MANIFEST

SKILLS_DIR = Path(__file__).resolve().parents[2] / "jenny" / "skills"

# Skill che documentano l'autoria di skill in generale: i percorsi di risorse
# che citano appartengono a skill di esempio, non a loro.
_GENERIC_AUTHORING_SKILLS = {"skill-creator"}

# `python3 foo.py`, `bash setup.sh`, `python -m pytest`. L'argomento deve
# iniziare per carattere non-trattino, così `npm --yes` (una flag documentata
# dalla skill ssh per comandi su host remoti) non viene scambiata per una
# invocazione locale.
_SHELL_INVOCATION_RE = re.compile(
    r"\b(?:python3?|node|bash|sh|zsh|npx)\s+(?:-m\s+[\w.]+|[^\s`\"']*\.(?:py|js|sh)\b)"
)

_RESOURCE_PATH_RE = re.compile(r"\b(scripts/[\w.-]+\.py|references/[\w.-]+\.md)\b")


def _skill_names() -> list[str]:
    """Le skill impacchettate, scoperte dal filesystem (mai una lista fissa)."""
    return sorted(p.name for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())


def _bundled_docs() -> list[tuple[str, str, str]]:
    """(nome skill, percorso relativo, testo) per ogni Markdown impacchettato."""
    docs: list[tuple[str, str, str]] = []
    for name in _skill_names():
        root = SKILLS_DIR / name
        paths = [root / "SKILL.md", *sorted((root / "references").glob("*.md"))]
        for path in paths:
            rel = f"{name}/{path.relative_to(root).as_posix()}"
            docs.append((name, rel, path.read_text(encoding="utf-8")))
    return docs


def _skill_modules(name: str) -> list[str]:
    """Moduli spediti in `skills/<name>/scripts/` (solo il livello superiore)."""
    scripts = SKILLS_DIR / name / "scripts"
    if not scripts.is_dir():
        return []
    return sorted(p.stem for p in scripts.glob("*.py") if p.name != "__init__.py")


def _code_blocks(text: str) -> list[tuple[int, str]]:
    """(riga di apertura, contenuto) di ogni fence Markdown del documento.

    Le fence si chiudono solo con almeno altrettanti backtick di quelli che le
    hanno aperte: serve perché `llm-wiki` annida un blocco mermaid dentro una
    fence a quattro backtick.
    """
    blocks: list[tuple[int, str]] = []
    fence: str | None = None
    start = 0
    buffer: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if fence is None:
            match = re.match(r"(`{3,})", stripped)
            if match:
                fence = match.group(1)
                start = lineno
                buffer = []
            continue
        if re.fullmatch(r"`{3,}\s*", stripped) and len(stripped.strip()) >= len(fence):
            blocks.append((start, "\n".join(buffer)))
            fence = None
            continue
        buffer.append(line)
    if fence is not None:  # fence non chiusa: la trattiamo comunque
        blocks.append((start, "\n".join(buffer)))
    return blocks


def _python_exec_snippets(text: str) -> list[tuple[int, str]]:
    """Blocchi fence che invocano `python_exec`, più le righe sciolte che lo fanno."""
    snippets = [(ln, body) for ln, body in _code_blocks(text) if "python_exec(" in body]
    covered = {ln for ln, _ in snippets}
    in_fence = False
    fence: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if fence is None:
            match = re.match(r"(`{3,})", stripped)
            if match:
                fence = match.group(1)
                in_fence = True
            continue
        if re.fullmatch(r"`{3,}\s*", stripped) and len(stripped.strip()) >= len(fence):
            fence = None
            in_fence = False
            continue
        if not in_fence and "python_exec(" in line and lineno not in covered:
            snippets.append((lineno, line))
    return snippets


def test_no_shell_or_interpreter_invocations() -> None:
    """Su Android non c'è shell: `python_exec` è l'unico strumento di esecuzione."""
    offenders: list[str] = []
    for _name, rel, text in _bundled_docs():
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = _SHELL_INVOCATION_RE.search(line)
            if match:
                offenders.append(f"{rel}:{lineno}: {match.group(0)!r} in {line.strip()!r}")
    assert not offenders, (
        "queste skill dicono all'agente di lanciare un comando che su questa "
        "piattaforma non esiste (vedi templates/agent/tool_contract.md); "
        "riscriverle come python_exec:\n  " + "\n  ".join(offenders)
    )


def test_no_doubled_skill_name_in_documented_paths() -> None:
    """`skills/llm-wiki/llm-wiki/scripts/…` non è il percorso che finisce sul telefono."""
    offenders: list[str] = []
    names = _skill_names()
    for _skill, rel, text in _bundled_docs():
        for name in names:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if f"{name}/{name}/" in line:
                    offenders.append(f"{rel}:{lineno}: {line.strip()!r}")
    assert not offenders, (
        "percorso con il segmento della skill duplicato; il manifest "
        "(jenny/utils/android_assets.py) estrae <skill>/... una volta sola:\n  "
        + "\n  ".join(offenders)
    )


def test_python_exec_blocks_using_own_scripts_set_working_dir() -> None:
    """Un blocco che importa/esegue uno script della skill deve dire da dove.

    Senza `working_dir` l'import nudo non risolve e l'`open()` relativo si misura
    dalla radice del workspace: entrambi i modi in cui B9 e B10 erano rotti.
    """
    offenders: list[str] = []
    for name, rel, text in _bundled_docs():
        modules = _skill_modules(name)
        if not modules:
            continue
        expected = f"skills/{name}/scripts"
        for lineno, body in _python_exec_snippets(text):
            used = [
                mod
                for mod in modules
                if f"{mod}.py" in body or re.search(rf"\bimport\s+{re.escape(mod)}\b", body)
            ]
            if not used:
                continue
            if "working_dir" not in body:
                offenders.append(
                    f"{rel}:{lineno}: usa {', '.join(used)} senza working_dir"
                )
            elif expected not in body:
                offenders.append(
                    f"{rel}:{lineno}: working_dir non punta a {expected!r}"
                )
    assert not offenders, (
        "ogni python_exec che tocca gli script della skill deve passare "
        'working_dir="<workspace>/skills/<nome>/scripts":\n  ' + "\n  ".join(offenders)
    )


def test_documented_resources_are_shipped() -> None:
    """Un `scripts/`/`references/` citato deve esistere ed essere nel manifest."""
    offenders: list[str] = []
    manifest = set(_SKILLS_MANIFEST)
    for name, rel, text in _bundled_docs():
        if name in _GENERIC_AUTHORING_SKILLS:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for resource in set(_RESOURCE_PATH_RE.findall(line)):
                if not (SKILLS_DIR / name / resource).is_file():
                    offenders.append(f"{rel}:{lineno}: {resource} non esiste su disco")
                elif f"{name}/{resource}" not in manifest:
                    offenders.append(
                        f"{rel}:{lineno}: {resource} non è in _SKILLS_MANIFEST"
                    )
    assert not offenders, (
        "documentazione che cita risorse che sul dispositivo non arrivano:\n  "
        + "\n  ".join(offenders)
    )
