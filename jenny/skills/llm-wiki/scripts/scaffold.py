#!/usr/bin/env python3
"""
scaffold.py — Create an LLM Wiki directory structure, or top up an existing one.

Usage:
    python3 scaffold.py <wiki-root> "<Topic Title>"

Example:
    python3 scaffold.py workspace/wikis/ai-research "AI Research"

The canonical layout puts every wiki under a workspace's wikis/ directory, so
<wiki-root> is normally wikis/<name>. After creating the wiki, this script
registers it in wikis/_index.md via reindex_wikis.regenerate_index().

Safe to re-run on a wiki that already exists: every file is written only when it
is absent, so nothing already on disk is rewritten. That makes this the way to
add what a wiki is missing after the scaffold has drifted — it creates the gaps,
leaves the rest byte-identical, and reports what it added.

Creates:
    <wiki-root>/
    ├── CLAUDE.md          (schema template)
    ├── log/
    │   └── YYYYMMDD.md    (first day's log with scaffold entry)
    ├── audit/
    │   ├── .gitkeep
    │   └── resolved/
    │       └── .gitkeep
    ├── raw/
    │   ├── articles/
    │   ├── papers/
    │   ├── notes/
    │   └── refs/
    ├── wiki/
    │   ├── index.md       (category-structured catalog)
    │   ├── concepts/
    │   ├── entities/
    │   └── summaries/
    └── outputs/
        └── queries/
"""

import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reindex_wikis  # noqa: E402  (sibling script, same scripts/ dir)


def scaffold(root: str, title: str) -> list[str]:
    """Crea quel che manca sotto ``root``, senza toccare quel che c'e' gia'.

    Ritorna i percorsi creati, relativi a ``root``: su una wiki completa e' una
    lista vuota, ed e' anche il report che viene stampato.
    """
    # Distingue "wiki nuova" da "top-up" per una cosa sola: come si intitola la
    # voce di log. Il comportamento sui file e' lo stesso nei due casi.
    root_existed = os.path.isdir(root)
    created: list[str] = []

    today = date.today()
    today_iso = today.isoformat()
    today_compact = today.strftime("%Y%m%d")
    now_hm = datetime.now().strftime("%H:%M")

    dirs = [
        "raw/articles",
        "raw/papers",
        "raw/notes",
        "raw/refs",
        "wiki/concepts",
        "wiki/entities",
        "wiki/summaries",
        "outputs/queries",
        "log",
        "audit",
        "audit/resolved",
    ]

    new_dirs = [d for d in dirs if not os.path.isdir(os.path.join(root, d))]
    for d in new_dirs:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    created.extend(f"{d}/" for d in new_dirs)
    if new_dirs:
        print(f"✓ Created under {root}/: " + ", ".join(f"{d}/" for d in new_dirs))
    else:
        print(f"· Directory tree already complete under {root}/")

    # .gitkeep for empty audit dirs
    for keep in ("audit/.gitkeep", "audit/resolved/.gitkeep"):
        if _write(root, keep, ""):
            created.append(keep)

    # CLAUDE.md
    claude_md = f"""---
summary: <one-line scope — shown next to this wiki in wikis/_index.md>
---

# {title} Knowledge Base

> Schema document — read at the start of every session together with this wiki's
> `wiki/index.md` and the workspace `../_index.md` registry.
> Update after every major compile, ingest batch, or structural change.
> This wiki is isolated: never wikilink into another wiki. Cross-wiki references
> go through `wikis/_index.md` only.

## Scope

What this wiki covers:
- <describe the topic area>

What this wiki deliberately excludes:
- <describe out-of-scope areas>

## Operations

This wiki follows the llm-wiki skill's five operations: `compile`, `ingest`, `query`, `lint`, `audit`.
Every operation appends an entry to `log/YYYYMMDD.md`.

## Naming conventions

- **Concept pages** (`wiki/concepts/`): Title Case noun phrases.
- **Folder-split concepts** (`wiki/concepts/<topic>/`): used when a topic exceeds ~1200 words. Contains `index.md` + one file per aspect.
- **Entity pages** (`wiki/entities/`): Proper names.
- **Summary pages** (`wiki/summaries/`): kebab-case source slug.

All pages require YAML frontmatter: `title`, `type`, `created`, `updated`, `sources`, `tags`.

### Diagrams and formulas
- All diagrams are **mermaid**. No ASCII art.
- All formulas are **KaTeX** (inline `$...$` or block `$$...$$`).

### Raw file policy
- Small text sources → copy into `raw/<subfolder>/`.
- Large binaries → create a pointer file at `raw/refs/<slug>.md` with `kind: ref` and `external_path` fields. Do not copy the binary.

## Current articles

*None yet — update this list after every compile.*

### Concepts
*(none)*

### Entities
*(none)*

### Summaries
*(none)*

## Open research questions

- <What do you want to understand better?>
- <What are the key open questions in this domain?>

## Research gaps

Sources to ingest:
- [ ] <URL or paper title> — why it's relevant

## Audit backlog

*(none — refresh with `python_exec(working_dir="<workspace>/skills/llm-wiki/scripts", code="import audit_review; audit_review.main('<wiki-root>', 'open')")`)*

## Notes for the LLM

- Language: <en | zh | bilingual>
- Tone: <neutral, academic, conversational, ...>
- Depth: <survey-level | deep technical>
- Handling contradictions: state both, cite each, add to Open Research Questions.
"""
    if _write(root, "CLAUDE.md", claude_md):
        created.append("CLAUDE.md")
        print("✓ Created CLAUDE.md")
    else:
        print("· CLAUDE.md already there — left as it is")

    # wiki/index.md
    index_md = f"""# Index — {title}

> One-sentence scope of the wiki.

## 🔖 Navigation
- [[#Concepts]] · [[#Entities]] · [[#Summaries]] · [[#Open Questions]]

## Concepts

*(none yet)*

## Entities

*(none yet)*

## Summaries (chronological)

*(none yet)*

## Open Questions

- <First research question>
"""
    if _write(root, "wiki/index.md", index_md):
        created.append("wiki/index.md")
        print("✓ Created wiki/index.md")
    else:
        print("· wiki/index.md already there — left as it is")

    # log/<today>.md — per ultimo, perche' la sua voce elenca quel che questo run
    # ha creato davvero, e per saperlo devono essere passati tutti gli altri file.
    # Se il log di oggi c'e' gia', resta intatto: la voce non viene appesa. La
    # skill chiede di loggare ogni operazione, ma "non toccare quel che c'e'" e'
    # la regola che rende un top-up sicuro su una wiki vera, e vince su questa.
    # Il report finisce su stdout, che e' cio' che l'agente legge comunque.
    log_rel = f"log/{today_compact}.md"
    if created:
        if root_existed:
            headline = f"Topped up {title} scaffolding"
            bullets = "".join(f"- Created {c}\n" for c in created)
        else:
            headline = f"Initialized {title} knowledge base"
            bullets = (
                "- Created directory tree (raw/, wiki/, log/, audit/, outputs/)\n"
                "- Created CLAUDE.md schema template\n"
                "- Created wiki/index.md category skeleton\n"
            )
        log_md = f"# {today_iso}\n\n## [{now_hm}] scaffold | {headline}\n{bullets}"
        if _write(root, log_rel, log_md):
            created.append(log_rel)
            print(f"✓ Created {log_rel}")
        else:
            print(f"· {log_rel} already there — left as it is, nothing appended")

    # Register this wiki in the workspace registry (wikis/_index.md).
    wikis_dir = Path(root).resolve().parent
    if wikis_dir.name != "wikis":
        print(
            f"⚠️  Parent dir is '{wikis_dir.name}', not 'wikis' — the canonical "
            f"layout is wikis/<name>. Registering in {wikis_dir}/_index.md anyway.",
            file=sys.stderr,
        )
    index_path = reindex_wikis.regenerate_index(wikis_dir)
    print(f"✓ Registered in {index_path}")

    if not created:
        print(f"\n✅ Nothing to add — {root}/ already has the whole scaffolding.")
    else:
        verb = "scaffolded" if not root_existed else "topped up"
        added = "".join(f"  + {c}\n" for c in created)
        print(f"\n✅ Wiki {verb} at: {root}/\n\nAdded:\n{added}")
        if not root_existed:
            print("""Next steps:
  1. Fill in CLAUDE.md — define scope and naming conventions
  2. Add sources to raw/ (copy articles/papers/notes into raw/<subfolder>/)
  3. Run ingest: tell your LLM agent "ingest raw/<file>.md"
  4. Ask questions: "what does the wiki say about X?"
""")

    print(f"""Run these through python_exec, with working_dir="<workspace>/skills/llm-wiki/scripts":
       lint:               import lint_wiki; lint_wiki.lint({root!r})
       feedback:           import audit_review; audit_review.main({root!r}, 'open')
       whole workspace:    import lint_wiki; lint_wiki.lint_workspace({str(wikis_dir)!r})
""")

    return created


def _write(root: str, path: str, content: str) -> bool:
    """Scrive ``content`` solo se il file non c'e'. True se l'ha creato.

    Prima questa funzione era ``open(full, "w")`` secco: rilanciare lo scaffold
    su una wiki esistente per "aggiungere quel che manca" ne azzerava
    ``wiki/index.md`` e riscriveva il log di oggi. Il confine sta qui, in un
    punto solo, e non in ognuno dei chiamanti.
    """
    full = os.path.join(root, path)
    if os.path.exists(full):
        return False
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    scaffold(sys.argv[1], sys.argv[2])

