# Skills

A skill teaches Jenny how to do something in chat — reminders, self-diagnostics, building an app for you — without any screen of its own.

## Skills vs. mini-apps

If a capability needs its own screen, it's a [mini-app](mini-apps.md). If it only ever lives inside the conversation, it's a skill. Setting a reminder, learning a new procedure, or building a new Jenny App are all skill-driven; none of them need a UI beyond chat.

## How skills are loaded

Each skill is a folder at `workspace/skills/<name>/SKILL.md` — a markdown file with a frontmatter header plus instructions. Jenny loads skills progressively: only a short summary (name, description, path) sits in the model's context at all times, and Jenny reads the full `SKILL.md` on demand when a task calls for it. This keeps the context small while still giving Jenny access to dozens of skills.

To see what's currently enabled, type `/skill` in chat. It lists every enabled skill with its description — this is the fastest way to check whether a given capability is actually turned on.

## Managing skills from the Apps tab

Skills are **rows in their own Skills room** in the Apps tab, reached by the segmented strip at the top — not cards mixed in with Jenny Apps and Android apps. They are not in the launcher drawer either: a skill isn't something you launch, so it isn't offered as one.

There is no status badge. There used to be one saying *active / idle / disabled*, and it was removed on purpose, because it mixed two different things into one word:

- **`disabled` is a decision** — yours, reversible on the spot. It shows as a **toggle switch** on the row, and flipping it writes `disabled: true` into the skill's frontmatter; Jenny stops seeing that skill in her context until you flip it back.
- **Unavailable is an impediment** — the skill *cannot* run, because it is missing a tool, a key or a file. The toggle has nothing to do with it: switching it on would not make the skill work. It shows as a separate warning-coloured line carrying the actual reason, which is the only information you can act on.

The two can coexist: a skill that is both switched off and unavailable shows both. Built-in skills carry a **lock icon** instead of a toggle — they cannot be disabled from here.

- **Tap** a row to open the skill's file if it is one you can edit, or a read-only card if it isn't.
- **Long-press** a row for its informational sheet.
- Enabling and disabling is the inline toggle; there is no context menu for it.
- **Delete** confirms with `Delete skill "{name}"?` before removing it.

To create a new one, tap **New Skill**. It opens the chat with the prompt `Hi, I want to create a new skill. Can you help me?`, and Jenny walks you through the same kind of guided conversation used for apps, using the built-in `skill-creator` skill.

## Three visibility tiers

Not every skill is meant to be poked at by hand. A skill's frontmatter puts it in one of three tiers:

| Tier | What you see | Examples |
|---|---|---|
| **Normal** | Full card, fully manageable (edit/enable/disable/delete) | Skills you or Jenny create yourself |
| **Locked** | Visible in the grid, but tapping it shows only a short descriptive card instead of an editor — no edit/disable/delete outside Developer mode | `cron`, `app-creator`, `skill-creator`, `llm-wiki`, `ssh` |
| **Internal** | Hidden from the grid entirely unless **Developer mode** is on | `memory`, `my`, `http-client`, `data-processing`, `long-goal` |

Locked skills are core parts of how Jenny works (scheduling, building apps and skills, the wiki, remote machines over SSH); internal skills are plumbing you're unlikely to ever need to touch directly (self-awareness bookkeeping, low-level HTTP/data helpers). Turning on **Developer mode** in Settings → System reveals both the internal skills and the management actions on locked ones. Its hint text is explicit about the intent: *"Also shows what Jenny uses to work: system skills and internal files (memory, configuration) appear in the lists. Only useful for looking under the hood."*

## Honesty note: builtin skills don't survive a restart

This is worth stating plainly rather than discovering the hard way: **the skills that ship with Jenny (all the locked and internal ones, plus any others bundled with the app) are re-extracted from the APK every time the app starts**, overwriting whatever is in their folder. If you disable a builtin skill, or edit its `SKILL.md` — even in Developer mode — that change is silently undone the next time Jenny restarts. The toggle in the UI doesn't warn you about this.

Persistence only works for **skills you (or Jenny, on your behalf) create yourself** — those live in the same `workspace/skills/` folder but are never touched by the startup extraction, so edits, disables, and deletions on them stick.

In short: treat disabling or editing a builtin skill as a "for this session" change, not a permanent setting. If you genuinely don't want a builtin capability available, the durable option is to ask Jenny not to use it, rather than relying on the disable toggle surviving a restart.

## See also

- [Mini-apps](mini-apps.md) — when a capability needs a screen instead.
- [Slash commands](slash-commands.md) — `/skill` and other chat commands.
- [Tool reference](../reference/tools.md) — the built-in tools that back skills like `cron`, `my`, and the file/python tools.
