# Memory, Dream and Atlas

Jenny keeps two very different kinds of memory: the live conversation you're having right now, and a set of durable text files that survive across chats, app restarts, and (if you back up) phone changes.

Two background processes maintain those files. **Dream** distils your conversations into what Jenny knows about you and your work. **Atlas** does the same for [your wikis](./wiki.md), compiling them into a directory of the people, projects and systems that matter. They have separate files and cannot write to each other's.

## The shape of memory

Jenny does not treat memory as one giant file. It separates it into layers, because different kinds of remembering deserve different tools:

- The live chat — what you're seeing on screen right now.
- `memory/history.jsonl` — a running archive of compressed past turns.
- `SOUL.md`, `USER.md`, and `memory/MEMORY.md` — the durable knowledge files that Jenny actually reads at the start of every conversation.
- `memory/WIKI.md` — the wiki directory, read at the start of every conversation too, but built from your wikis rather than from your chats.

This keeps a single chat fast in the moment, while still letting Jenny build up a durable picture of you and the project over weeks of use.

## The two-phase pipeline

Memory moves through Jenny in two stages, and both have to run before something you said becomes part of Jenny's long-term knowledge.

```text
live chat  →  Consolidator  →  memory/history.jsonl  →  Dream (every 2h)  →  MEMORY.md / USER.md / SOUL.md / skills
```

### Stage 1: the Consolidator (context compaction)

While you chat, Jenny is not trying to carry every old message forever. When the conversation grows large enough to pressure the model's context window, or when the session has been idle long enough, the Consolidator summarizes the oldest safe slice of the conversation with an LLM call and appends that summary to `memory/history.jsonl`.

This file is:

- append-only
- cursor-based (each write gets a numeric cursor so later steps know what's new)
- optimized for machine consumption first, human inspection second

Each line is a JSON object, roughly:

```json
{"cursor": 42, "timestamp": "2026-04-03 00:02", "content": "- User prefers dark mode\n- Decided to use PostgreSQL"}
```

It is not the final memory — it's the raw material Dream later shapes into something durable. Two triggers feed it:

- **Token pressure**: once the conversation gets close to filling the configured context window, older messages are summarized and archived so the newest turns keep fitting.
- **Idle timeout**: if the chat has been idle for a while (15 minutes by default — see [Configuration](../reference/configuration.md) for `idleCompactAfterMinutes`), the session is auto-compacted the same way.

If the LLM call that produces the summary fails, the Consolidator falls back to a raw `[RAW]` dump of the messages instead of losing them — you get less polish, but nothing disappears.

### Stage 2: Dream

Dream is the slower, more thoughtful layer. By default it runs automatically every 2 hours while the app is running, and you can also trigger it on demand with `/dream`.

Each Dream run:

1. Reads new, unprocessed entries from `memory/history.jsonl` (up to 20 entries per run, each truncated to 500 characters).
2. Reads the current `SOUL.md`, `USER.md`, and `memory/MEMORY.md`.
3. Edits those long-term files — and skill files under `workspace/skills/<name>/SKILL.md` — surgically, in a single pass, using a restricted set of file-editing tools.

Dream doesn't rewrite everything from scratch; it makes the smallest honest change that keeps memory coherent. That's why Jenny's memory is interpretive, not just archival — and also why Dream **prunes as well as adds**. Its instructions tell it to be "ruthless about pruning": removing stale, duplicated, or resolved content is treated as just as important as writing new facts. A fact you thought was permanently saved can be trimmed, merged, or rewritten in a later Dream pass if Jenny judges it no longer earns its place.

Because of that, Jenny takes a workspace snapshot right before every Dream run. If Dream ever prunes or rewrites something you wanted kept, that snapshot is your way back — see [Backup and restore](./backup.md) for how to browse and restore snapshots. The snapshot attempt is best-effort: if it fails for some reason, Dream still runs (the safety net just wouldn't be there for that one pass).

Dream's cursor into `history.jsonl` only advances once a run completes cleanly **and** actually manages to write something (or has nothing to write in the first place). If Dream gets blocked or a write fails partway through, the cursor stays put and those entries are retried on the next run — nothing is silently skipped.

## Why Dream can say "nothing to process"

If you run `/dream` on a chat that just started, or one that's still short, Jenny will reply that there's no conversation history to process yet. This is expected, not a bug: Dream only reads from `memory/history.jsonl`, and fresh conversations only reach that file *after* the Consolidator has compacted them (see Stage 1 above). A short, still-active chat simply hasn't produced any compacted history for Dream to read yet.

Concretely, `/dream` will tell you this and suggest enabling automatic idle compaction (`idleCompactAfterMinutes`) so completed chats become Dream input on their own, or waiting until the current chat actually gets compacted.

## Atlas: the wiki side of memory

Dream reads your conversations. Atlas reads [your wikis](./wiki.md) and maintains one file, `memory/WIKI.md` — a directory, not a summary. It lists every wiki you have with a one-line scope, then the entities from your main wiki that matter operationally: people you actually deal with, projects you're running, systems you operate. Each entry is one line plus a `[[wikilink]]` to the page that holds the detail.

The point is what it saves you. Without it, "what's the nickname of that plant I'm monitoring?" costs Jenny a few tool calls through the wiki. With it, the answer is already in the prompt.

Three things worth knowing about how Atlas behaves:

- **It runs every 12 hours, but usually does nothing.** Before calling the model, Atlas fingerprints your wiki pages. If nothing changed since the last run, it stops there — no tokens, no battery. `log/` and `audit/` are deliberately excluded from that fingerprint, so routine lint and audit activity doesn't trigger pointless rebuilds.
- **It can only write `memory/WIKI.md`.** Not `MEMORY.md`, not `SOUL.md`, not `USER.md`, and not the wiki it reads from. That's a sandbox, not a convention — the tools it runs with have no other writable path.
- **It updates by difference.** Entries that are still correct keep their wording; new pages get added, deleted ones get removed.

You can steer what goes in. Create `memory/WIKI_POLICY.md` in your workspace and write your own inclusion rules in plain language — "plants only if I've given them a nickname", "no medical topics", "skip anything archived over three months". Those rules override the generic criteria. Changing that file also changes the fingerprint, so the next run picks it up.

## The files

```text
workspace/
├── SOUL.md              # The bot's long-term voice, behavior rules, tool-use strategy
├── USER.md               # Stable knowledge about you: identity, preferences, communication style
└── memory/
    ├── MEMORY.md         # Project facts, decisions, and durable context
    ├── WIKI.md           # Wiki directory (Atlas output) — do not hand-edit, it gets rebuilt
    ├── WIKI_POLICY.md    # Optional: your own rules for what belongs in the directory
    ├── history.jsonl     # Append-only history summaries (Consolidator output)
    ├── .cursor           # Consolidator write cursor
    ├── .dream_cursor     # Dream read cursor
    └── .atlas_state.json # Atlas wiki fingerprint
```

These files play different roles:

- `SOUL.md` remembers how Jenny should behave and sound — guardrails, interaction patterns, tool-use strategy.
- `USER.md` remembers who you are and what you prefer — identity, habits, language, tone, reply length.
- `MEMORY.md` remembers what remains true about the work itself — goals, decisions, infrastructure.
- `WIKI.md` remembers what's *in* your wikis — a switchboard of names and links, not the content itself.
- `history.jsonl` remembers what happened on the way there, as compressed, timestamped summaries.
- Recurring workflows can also be promoted into `workspace/skills/<name>/SKILL.md` by Dream, rather than staying as prose inside `MEMORY.md` or `USER.md`.

All of these are plain text in your workspace. You can read them, edit them by hand, or just ask Jenny to change something in them — nothing about memory is hidden behind a locked format.

### `history.jsonl` instead of a plain history file

`history.jsonl` replaced an older, more casual "history as prose" format because it needed to be an operational substrate, not just pleasant reading. The JSON-lines format gives Jenny stable incremental cursors, safer machine parsing, easier batching per Dream run, and a cleaner boundary between raw history and curated knowledge. It's capped at **1000 entries**; once the cap is reached, the oldest entries are dropped to make room for new ones as the file is compacted.

You can search it yourself with the `python_exec` tool or by asking Jenny to `grep` it, if you ever want to see exactly what got archived.

## What Jenny actually remembers when you open a new chat

At the start of every conversation, Jenny's system prompt includes:

- `SOUL.md` and `USER.md`, loaded as bootstrap files.
- `MEMORY.md`, if it has real content (an untouched template file isn't injected).
- `memory/WIKI.md`, if Atlas has built one. It sits under the same "Memory" heading as `MEMORY.md` but is injected independently — an untouched `MEMORY.md` doesn't suppress it — and is capped at roughly 1,200 tokens so a long directory can't tax every turn.
- Any history entries from `memory/history.jsonl` that Dream hasn't processed yet (capped to the last 50 entries / roughly 8,000 tokens of text) — this is the bridge between "compacted but not yet dreamed" and the durable files.

So a brand-new chat isn't a blank slate: it inherits your durable profile and project notes from the last Dream pass, plus whatever's been compacted since then but not yet folded in.

## Commands

| Command | What it does |
|---------|--------------|
| `/dream` | Runs Dream immediately instead of waiting for the next scheduled pass. Replies "Dreaming..." right away, then follows up with the outcome once it finishes (completed and how long it took, completed-but-wrote-nothing, failed, or nothing to process). |
| `/atlas` | Rebuilds the wiki directory now. If nothing in your wikis changed since the last run it says so and spends nothing; `/atlas force` rebuilds anyway. |

## Configuration

Dream's configuration lives under `agents.defaults.dream` in `config.json`, and it is much smaller than older documentation for this project suggested — there is no `cron`, `modelOverride`, or `maxBatchSize` field. Only two fields actually exist:

```json
{
  "agents": {
    "defaults": {
      "dream": {
        "enabled": true,
        "intervalH": 2
      }
    }
  }
}
```

| Field | Meaning | Default |
|-------|---------|---------|
| `enabled` | Whether the periodic Dream job is registered at all. | `true` |
| `intervalH` | How often Dream runs automatically, in hours. Internally this becomes an "every N hours" schedule. | `2` |

Atlas has its own block, `agents.defaults.atlas`, with the same shape plus a size cap:

```json
{
  "agents": {
    "defaults": {
      "atlas": {
        "enabled": true,
        "intervalH": 6,
        "maxContextTokens": 1200
      }
    }
  }
}
```

| Field | Meaning | Default |
|-------|---------|---------|
| `enabled` | Whether the periodic Atlas job is registered. Leaving it on costs nothing if you have no wikis — the job exits before calling the model. | `true` |
| `intervalH` | How often Atlas checks whether the wiki changed, in hours. | `6` |
| `maxContextTokens` | Hard cap on the directory block injected into every prompt. A longer `WIKI.md` is truncated at injection time. | `1200` |

Which wiki supplies the entity list follows `wiki.defaultWiki` (default `main`); the wiki *list* always covers every wiki under `wiki.wikisDir`.

Related settings that shape *when* material reaches Dream in the first place (not Dream-specific, but relevant here) live under `agents.defaults` too: `idleCompactAfterMinutes` (idle-triggered compaction, default 15 minutes), `maxMessages` (default 120), and the consolidation ratio that controls how aggressively old messages are summarized (default 0.5). See [Configuration](../reference/configuration.md) for the full reference.

None of this is exposed in the Settings UI today — Dream's interval and the compaction thresholds can currently only be changed by editing `config.json` directly. The memory files themselves need no special mode to see: `SOUL.md`, `USER.md`, `memory/MEMORY.md`, and `memory/history.jsonl` are all visible from the Workspace file browser by default. The only things the file browser hides by default are dotfiles and a handful of runtime-internal paths (`config.json`, `agent/`, `cron/`, `sessions/`, `ui/`) — including the `memory/.cursor` and `memory/.dream_cursor` cursor files, which are dotfiles. Turning on **Developer mode** in Settings → System reveals those too, but it has no effect on the memory files themselves, which were never hidden.

## Gotchas worth knowing

- **"Every 2 hours" is a floor, not a promise.** Since 0.6.0 the deadline survives an app restart and a run missed while the app was dead is caught up shortly after it comes back — before that, every restart pushed it out by another 2 hours. What restarts can't fix is Android's doze: even on a process that stays alive for hours, the gap between runs stretches. Expect Dream to run somewhat less often than the number suggests.
- **Dream prunes, not just adds.** Expect Jenny's memory to occasionally lose detail on purpose — that's Dream doing its job, not corruption. The pre-Dream snapshot is there specifically so a bad prune is recoverable.
- **`/dream` on a short or fresh chat will say there's nothing to process.** That's because Dream reads `memory/history.jsonl`, not the live chat — see above.
- **Memory files are visible in the file browser by default, no Developer mode needed.** If you go looking for `MEMORY.md` in the Workspace tab and don't see it, the more likely explanation is that it's still an untouched template with no real content yet. Developer mode (Settings → System) only reveals dotfiles and runtime-internal folders like `agent/`, `cron/`, and `sessions/` — it doesn't gate the memory files.
- **If the provider is down when the Consolidator needs to summarize, it degrades to a raw `[RAW]` dump** instead of a clean summary — you don't lose the content, but it won't read as nicely until a later pass cleans it up.
- **Hand edits to `memory/WIKI.md` don't survive.** Atlas rebuilds that file from the wiki. To change what it contains, change the wiki or write your rules into `memory/WIKI_POLICY.md`.
- **Atlas has no pre-run snapshot, unlike Dream.** It doesn't need one: `WIKI.md` is derived from your wikis, so the worst case is losing it until the next run rebuilds it. Dream rewrites memory that exists nowhere else, which is why *it* gets a checkpoint.
- **Dream's own model, interval, and batch size are not independently configurable today** — despite what an earlier draft of this documentation implied, there is no `modelOverride` field: Dream always uses the same model as your main agent, and there is no `maxBatchSize` or `cron` override to reach for.

## In practice

What this design means in daily use:

- Conversations stay fast without carrying infinite context.
- Durable facts about you and your projects get clearer over time instead of noisier, because Dream is actively editing, not just appending.
- You can force a consolidation pass with `/dream` whenever you want, and you can always recover a pruning mistake from a pre-Dream snapshot.

See also [Scheduling and proactivity](./scheduling.md) for how the Dream job relates to other background jobs (heartbeat, reminders), and [Backup and restore](./backup.md) for how workspace snapshots work.
