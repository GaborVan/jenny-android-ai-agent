# Project Folder

You are working inside one project: `{{ project_path }}`. It is a wiki, and what you produce for
it goes inside it — filed by kind, never dumped at the root, which holds only this layout:

- `wiki/concepts/`, `wiki/entities/`, `wiki/summaries/` — the pages. `wiki/index.md` catalogues them.
- `raw/articles|papers|notes/` — sources copied in verbatim. `raw/refs/` — a pointer file for anything too large to copy.
- `outputs/` — deliverables that are not wiki pages; `outputs/queries/` for the answer to a question.
- `log/YYYYMMDD.md` — one appended line per operation.
- `audit/` — open review notes, `audit/resolved/` once closed.
- `AGENTS.md` — this project's own instructions: scope, conventions, what it deliberately excludes. Read it before working, and keep it current when the answer to "how we work here" changes. It is yours to edit.

Read anywhere in the installation, but **write only inside this folder**: a write outside it is
refused, so do not plan one. That includes another project — there is no cross-project work.

Depth lives in the `llm-wiki` skill: the five operations, page format and frontmatter, lint and
audit. Read it when you are about to do one of them, not before.
