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
    ├── AGENTS.md          (this wiki's scope and notes)
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

    # AGENTS.md — le istruzioni di *questa* wiki, e basta.
    #
    # Fino al 22/08 qui c'era uno schema da 2,5 kB: convenzioni di naming,
    # mermaid e KaTeX, policy sui raw, le cinque operazioni. Roba vera, ma
    # identica in ogni wiki e ricopiata in ognuna — quindi ferma alla versione
    # in cui la wiki e' stata creata. Ora la dice `agent/project.md`, che sta nel
    # prompt di sistema e si riscrive a ogni avvio. Qui resta solo cio' che di
    # questa wiki e' vero e di nessun'altra.
    #
    # `summary:` e la riga "What this wiki covers:" non sono decorazione: sono
    # esattamente cio' che `read_wiki_scope` cerca per comporre `_index.md`.
    # `id:` e' l'identita' della wiki, e serve a una cosa sola: ritrovare la
    # propria chat se un giorno la cartella cambia nome (passo 7). Non e' un
    # indirizzo e non finisce in nessun nome di file. Nasce qui perche' una wiki
    # creata oggi non deve aspettare la migrazione del prossimo avvio;
    # `secrets` e non `uuid` per la stessa forma da 12 esadecimali che legge
    # `utils/wiki_paths.py::wiki_id`.
    wiki_id = __import__("secrets").token_hex(6)
    agents_md = f"""---
id: {wiki_id}
summary: <one-line scope — shown next to this wiki in wikis/_index.md>
---

# {title}

## Scope

What this wiki covers:
- <describe the topic area>

What this wiki deliberately excludes:
- <describe out-of-scope areas>

## Notes

<Anything true of this wiki and no other: sources to prefer, conventions that
depart from the default, open questions. The folder layout and the five
operations are not written here — the agent already has them.>
"""
    # Non lo si crea accanto a un `CLAUDE.md`. Questo script gira anche in
    # top-up su wiki vere, e una wiki non ancora migrata quel file ce l'ha col
    # nome vecchio: aggiungerne un secondo alla radice produrrebbe di proposito
    # lo stato che la migrazione (`utils/wiki_migration.py`) si rifiuta di
    # risolvere — e la migrazione, al prossimo avvio, lo rinomina da se'.
    legacy = os.path.isfile(os.path.join(root, "CLAUDE.md"))
    if not legacy and _write(root, "AGENTS.md", agents_md):
        created.append("AGENTS.md")
        print("✓ Created AGENTS.md")
    elif legacy:
        print("· CLAUDE.md still there — the next start renames it, left as it is")
    else:
        print("· AGENTS.md already there — left as it is")

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
                "- Created AGENTS.md with the wiki's scope\n"
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
  1. Fill in AGENTS.md — define what this wiki covers and what it excludes
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

