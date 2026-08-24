# Wiki

The Wiki tab is a browsable knowledge base that Jenny builds for you out of sources you feed it, not something you write by hand from scratch.

## What it is

A wiki, here, is a set of cross-linked Markdown pages that Jenny compiles from raw material — articles, notes, PDFs, web pages — into concept and entity pages that reference each other with `[[wikilinks]]`. Jenny builds and maintains it using a built-in skill called `llm-wiki`, which you invoke conversationally rather than through any dedicated button:

- "Create a wiki about X" — scaffolds a new one.
- "Ingest this article/PDF/page into the X wiki" — adds a raw source.
- "Compile the notes I gave you into pages" — turns raw material into wiki pages.
- "What does the wiki say about Y?" — answers a question from what's already compiled.
- "Run a lint pass on the wiki" — checks for dead links, orphan pages, and coverage gaps.

There is no in-app "new wiki" form: everything starts as a chat request.

**The wiki does not update itself.** Ingesting a source doesn't automatically compile it into pages, and pages don't automatically get relinted after you edit them — each of those steps only happens when you (or a scheduled task you've set up) explicitly asks Jenny to do it.

The one thing that *does* happen on its own is the reverse direction: a background job called **Atlas** reads your wikis every 12 hours and compiles a short directory of them into `memory/WIKI.md`, which Jenny loads at the start of every conversation. It never writes to the wiki — it only reads it. See [Atlas](./memory.md#atlas-the-wiki-side-of-memory).

## Wikis and projects

Everything on this page describes a wiki as something you *ask* Jenny to build and maintain. There is a second way to work with one: open it as a **[project](./projects.md)** from the chip above the message box. A project is a wiki — the same folder, the same pages, the same graph — but the conversation is bound to it, its map and pages are put in front of Jenny on every turn, facts you mention are captured into a journal inside it, and a background pass (the [gardener](./gardener.md)) turns those journal lines into pages between conversations.

So the two views are not alternatives: a wiki you created by asking can be opened as a project tomorrow, and a project you created from the chip appears in the Wiki tab immediately.

## Multiple wikis

You can have more than one wiki side by side — for example, one about a research topic and a separate one for a hobby project. Each one lives under `workspace/wikis/<name>/`, entirely isolated from the others; Jenny works on one wiki root at a time and won't mix content across them unless you ask it to. A top-level `wikis/_index.md` file lists all of them.

Because everything is plain Markdown under the workspace, wiki pages are files like any other: you can open and edit them directly from the Workspace tab's file browser if you want to (see [Tour of the WebUI](webui-tour.md)), and they're included in [backups and snapshots](backup.md) exactly like the rest of your workspace.

## Navigating a wiki

Opening the **Wiki** tab from the dock actually lands you on a **graph overview** first — a star layout showing each of your wikis as a node, with no per-page detail. From there:

- **Tap** a wiki's node to open that wiki's own graph, now scoped to just its pages and links (not the page view yet).
- From a wiki's scoped graph, use the "Pages" button in the header to switch to the **page view**: the actual rendered article, with a breadcrumb trail above it (Home → wiki name → folder → page) and a collapsible file tree (the folder icon in the header) for jumping around — or **double-tap** a specific page node in the graph to jump straight into that page.
- From the page view, the "Graph" button in the header switches back to that wiki's scoped graph.
- Inside a page, `[[wikilinks]]` are clickable and take you straight to the linked page.

### Graph view

The graph view renders pages as nodes and links between them as edges, laid out automatically (drag to reposition, pinch/scroll to zoom, tap a node to focus its immediate neighbors). Nodes are colored by group, with a legend in the corner:

| Legend color | Meaning |
|---|---|
| Concepts | Concept/topic pages |
| Entities | People, tools, papers, organizations |
| Other | Anything that doesn't fit the two groups above |

The top-level graph overview (all your wikis at once) does not use this legend — there, each wiki gets its own color just to tell them apart, not a semantic one.

### Searching a wiki

A wiki's scoped graph has a search box above it. It searches **page contents**, not just titles: as you type, nodes that match stay lit and get an accent ring, everything else dims, and a counter shows how many pages matched. The last word you type matches by prefix, so results narrow letter by letter without waiting for you to finish the word.

- Accents are optional — typing `citta` finds "Città".
- Several words are an **and**: `doze batteria` keeps only pages containing both.
- Page titles, file paths, headings, and frontmatter tags all count, and a hit in a title ranks above one buried in a paragraph.
- Very common words are ignored rather than treated as a failed search, so a query like `il sonno` behaves like `sonno`.
- Tapping a result focuses it as usual, lighting its neighbors even if they didn't match; the matched pages stay ringed so you can still tell them apart. Back steps out of the focus first, then clears the search.

The search box does not appear on the top-level overview, where the nodes are whole wikis rather than pages. Search runs entirely on-device: the index is built alongside the graph and rebuilt only when a page actually changes, so typing never waits on anything.

Mermaid diagrams are the one place in Jenny's UI where they actually render as diagrams: if a wiki page contains a fenced ` ```mermaid ` block, it's drawn as a real diagram here. Chat replies do not render Mermaid at all, even though they render most other markdown — see [Chat basics](chat.md).

### Empty or broken states

These apply to the top-level Wiki home (the landing page before you pick a specific wiki), not to an individual wiki:

- If you have no wikis at all yet (no Markdown pages anywhere under `workspace/wikis/`), it shows: "The wiki is empty. Ask Jenny to create one."
- If wikis with pages exist but the top-level `workspace/wikis/_index.md` file itself is missing, it shows a message pointing out the problem and inviting you to ask Jenny to fix it.

Both are dead ends by design — the fix is a chat message, not a button in the UI.

## Feedback and audits

If you spot something wrong or missing in a wiki page, you can leave feedback directly on the text, without switching to chat:

1. Select a stretch of text in a wiki page. A small "Add audit" button appears near the selection.
2. Tap it. A dialog titled "Add Feedback" opens, showing a preview of the selected text, a comment box ("Describe the issue…"), and a severity picker with four levels: **info**, **suggest**, **warn**, **error**.
3. Submit. The feedback is saved as an audit entry tied to that page.

This only works inside an actual wiki page — there is no way to file an audit from the graph view or from the wiki's home page. If your selected text can't be matched precisely back to the page's source (for example, because whitespace or formatting makes it ambiguous), Jenny asks for confirmation before creating an anchor-less audit rather than silently guessing at the location.

Audits are reviewed from the **Audits** drawer (clipboard icon in the page-view header, next to the file tree):

- Two tabs: **Open** and **Resolved**.
- Each entry shows the flagged text, severity tag, author, and timestamp.
- From the Open tab, "mark resolved" lets you add a short resolution note before the entry moves to Resolved.

Jenny is expected to process open audits when you ask it to run a wiki lint or maintenance pass — filing an audit doesn't make Jenny act on it by itself.

## Privacy notes

- The wiki only shows a small, fixed set of page metadata to the UI: title, type, entity type, tags, and created/updated dates. Anything else in a page's frontmatter — including source URLs and provenance notes Jenny records internally — stays server-side and out of the interface, even though it's present in the raw file if you open it in the Workspace editor.
- Every ingested source keeps a `summaries/` folder of per-source digest pages. These are deliberately hidden from the file tree and the graph view to avoid cluttering navigation, but they are **not** encrypted or otherwise access-controlled — the pages are still readable if you know (or Jenny gives you) a direct link, and they still live as plain files in your workspace.

## Turning it off

A config key, `wiki.enabled` (default `true`), can disable the wiki backend entirely; when it's off, wiki API calls fail. There is no toggle for this in Settings — it's config.json-only, see [Configuration reference](../reference/configuration.md).

<!-- TODO: verify on-device (O-12) whether the Wiki tab in the dock disappears when wiki.enabled=false, or stays visible and simply fails to load content -->

## See also

- [Projects](projects.md) — opening a wiki as a conversation: capture, the map, and the write boundary.
- [The gardener](gardener.md) — the pass that maintains a project's pages on its own.
- [Tour of the WebUI](webui-tour.md) — overall navigation, dock, and tab layout.
- [Backup and restore](backup.md) — wiki content is part of your regular workspace backup.
- [Phone app launcher](app-launcher.md) — the other under-documented corner of the Apps tab.
