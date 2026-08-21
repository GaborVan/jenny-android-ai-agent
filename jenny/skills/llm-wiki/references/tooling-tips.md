# Tooling Tips

Practical setup and usage notes for the LLM Wiki stack.

## Workspace scripts

All wikis live under a workspace `wikis/` directory. There is no shell here: the scripts are imported and called through `python_exec`, with `working_dir` on their own directory and absolute wiki paths (see SKILL.md → "Running the scripts").

```
python_exec(
    working_dir="<workspace>/skills/llm-wiki/scripts",
    code="import lint_wiki; lint_wiki.lint('<workspace>/wikis/<name>')",
)
```

- `scaffold.scaffold('<workspace>/wikis/<name>', '<Title>')` — create a wiki and register it in `wikis/_index.md`. Safe on an existing wiki: it writes only the files that are missing and returns the list of what it added.
- `reindex_wikis.regenerate_index('<workspace>/wikis')` — rebuild the `_index.md` registry block; `reindex_wikis.check_index(...)` returns drift problems instead. Run after renaming/deleting a wiki.
- `lint_wiki.lint('<workspace>/wikis/<name>')` / `lint_wiki.lint_workspace('<workspace>/wikis', fix=False)` — health check one wiki, or all of them plus the registry; `fix=True` repairs registry drift.
- `audit_review.main('<workspace>/wikis/<name>', 'open')` (modes `open`/`resolved`/`all`) / `audit_review.run_workspace('<workspace>/wikis', 'open')` — group audits.

Each wiki's one-line scope in the registry comes from a `summary:` field in its `CLAUDE.md` frontmatter (fallback: the first `## Scope` bullet). Set `summary:` for a clean registry.

## Obsidian setup

### Essential settings

1. **Attachment folder**: Settings → Files and links → "Attachment folder path" → `raw/assets/`.
2. **New file location**: Settings → Files and links → "Default location for new notes" → `wiki/concepts/`.
3. **Download attachments hotkey**: Settings → Hotkeys → search "Download attachments" → bind to `Ctrl+Shift+D`. After clipping an article, hit the hotkey to download all images locally.

### Optional plugins

- **Obsidian Web Clipper** (browser extension) — converts any webpage to Markdown and saves to your vault. Configure to save to `raw/articles/`.
- **Dataview** — query frontmatter fields; build dynamic tables of articles by tag, date, source count.
- **Marp** — render wiki content as slide decks directly from Obsidian.

### Graph view

Graph view (`Ctrl+G`) is the best way to see your wiki's shape:
- Dense hub = a well-connected concept page.
- Isolated node = orphan page (needs inbound links or removal). `lint_wiki.py` flags these.
- Cluster = a sub-topic worth a dedicated folder-split under `wiki/concepts/`.

## Filing audits

Audits are plain Markdown files under each wiki's `audit/` directory — file them by hand (or have the agent do it). See `audit-guide.md` for the frontmatter and anchor format, then review with `audit_review.py` and process via the `audit` operation.

## Obsidian Web Clipper usage

1. Install from [obsidian.md/clipper](https://obsidian.md/clipper).
2. Configure template to save to `raw/articles/`.
3. Clip an article → hit the download-images hotkey → file is ready for `ingest`.

For complex pages (paywalled, dynamic): copy-paste the main text manually, save as `raw/articles/<slug>.md`.

## qmd (optional, for large wikis)

[qmd](https://github.com/tobi/qmd) is a local semantic search engine for Markdown files with BM25 + vector hybrid search. Useful when the wiki grows beyond ~100 pages and `wiki/index.md` scanning becomes slow.

```bash
pip install qmd
qmd collection add wiki/ --name my-wiki
qmd embed
qmd query "what are the tradeoffs of RAG vs wiki" --collection my-wiki
```

qmd also has an MCP server so LLMs can use it as a native tool.

## Marp — generating slide decks from wiki content

```markdown
---
marp: true
theme: default
---

# Slide title

Content here

---

# Next slide
```

Install the Marp plugin in Obsidian to preview/export directly.

## Generating charts

For quantitative analyses, ask the LLM to generate a matplotlib script and save to `outputs/charts/`:

```python
# outputs/charts/my-analysis.py
import matplotlib.pyplot as plt
# ... chart code ...
plt.savefig('outputs/charts/my-analysis.png')
```

Embed in a wiki article: `![[my-analysis.png]]`.

## Git workflow

The wiki is a git repo. Benefits:
- Version history for every article.
- Branching for experimental research directions.
- Audit files are tracked, so "who suggested this and when" is first-class.

```bash
git add .
git commit -m "ingest: 3 papers on attention mechanisms"
git push
```

Keep large files (PDFs >10 MB, raw images at full resolution, video, model weights) in `.gitignore`. Use the raw file policy: pointer files in `raw/refs/`, not copies.

## Interactive HTML outputs

For complex analyses, the LLM can generate interactive HTML with JavaScript and save to `outputs/`. These can be opened in a browser or embedded in Obsidian with the HTML plugin.

