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

"Wrote something" is not the same as "saved the batch", and the difference is worth stating because it cost real entries. A run can obey the first half of an over-budget refusal — free some space by rewriting an existing line shorter — and then stop without adding the new fact. Every counter reads healthy: a write succeeded, no refusal is outstanding. So Dream also checks, at the end of each run, whether **any** memory file grew. If a memory file was already near its budget when the run started, the batch carried facts tagged for retention, and nothing grew, the cursor is held and those entries come back next run, which is also what pulls in the review pass that frees room. The "near its budget" part is what keeps this quiet: without it the check fires on every batch of facts Jenny already knows, which is most of them — the consolidator re-extracts the same facts each pass. Below that mark, a run that adds nothing is believed. This is a size heuristic, not a proof: a run that legitimately replaces a line with a shorter one carrying the new fact reads as "nothing landed" and gets replayed a few times. That is the cheap direction to be wrong in — the expensive one is losing the fact — and the replay is bounded: after four held runs Dream gives up on the batch, advances, and the stuck alarm has already fired.

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

Note the asymmetry in that list. `WIKI.md` and the pending history are *capped* at injection time; `SOUL.md`, `USER.md` and `MEMORY.md` are injected **whole**, at whatever length they happen to be, on every single turn. That is on purpose — see [The budgets bound what Dream writes, not what a turn pays](#the-budgets-bound-what-dream-writes-not-what-a-turn-pays).

## Commands

| Command | What it does |
|---------|--------------|
| `/dream` | Runs Dream immediately instead of waiting for the next scheduled pass. Replies "Dreaming..." right away, then follows up with the outcome once it finishes (completed and how long it took, completed-but-wrote-nothing, failed, or nothing to process). When a review pass ran first, the note also says how many characters it freed, how many facts it moved into `memory/archive/` (with the ids to ask for them back), and how many writes were refused by a size budget and never landed — that last one is usually the explanation for "nothing was freed". |
| `/dream budget` | Shows the current size of each memory file against its budget, and how often the review pass runs. |
| `/dream budget <memory\|user\|soul> <n>` | Sets that file's budget to `n` characters. `0` disables enforcement for that file — the size is still shown, nothing is refused. |
| `/dream budget review <n>` | Runs the review pass every `n` Dream runs. Anything below **12** is refused unless you repeat it with the confirmation phrase the refusal prints — `1` runs a review every time, which is the way to watch one happen instead of waiting a day for it. [Why there is a floor.](#the-review-cadence-has-a-floor-of-12-and-it-is-enforced) |
| `/atlas` | Rebuilds the wiki directory now. If nothing in your wikis changed since the last run it says so and spends nothing; `/atlas force` rebuilds anyway. |

`/dream budget` writes to `config.json` and takes effect on the next run — no restart. It exists because raising a default in a new version of the app does **not** reach a `config.json` that has already been written: the file wins. If you are on an install from before these settings existed, the command is the only way to change them.

## Configuration

Dream's configuration lives under `agents.defaults.dream` in `config.json`, and it is much smaller than older documentation for this project suggested — there is no `cron`, `modelOverride`, or `maxBatchSize` field. Six fields exist: two for scheduling, and four that give the memory files a size to aim at.

```json
{
  "agents": {
    "defaults": {
      "dream": {
        "enabled": true,
        "intervalH": 2,
        "memoryBudgetChars": 3000,
        "userBudgetChars": 3000,
        "soulBudgetChars": 0,
        "reviewEveryRuns": 12
      }
    }
  }
}
```

| Field | Meaning | Default |
|-------|---------|---------|
| `enabled` | Whether the periodic Dream job is registered at all. | `true` |
| `intervalH` | How often Dream runs automatically, in hours. Internally this becomes an "every N hours" schedule. | `2` |
| `memoryBudgetChars` | Target size for `memory/MEMORY.md`, in characters. Dream sees how full the file is in every prompt, and a Dream write that would push it further over the line is refused — a write that *shrinks* it is always allowed, or an over-budget file could never be pruned. `0` means "measure, don't enforce". | `3000` |
| `userBudgetChars` | The same for `USER.md`. | `3000` |
| `soulBudgetChars` | The same for `SOUL.md` — and it ships at `0` on purpose. That file mixes Jenny's identity, which must never be pruned, with notes that belong elsewhere, and a size limit cannot tell the two apart. The review pass reads before it decides; the limit does not. | `0` |
| `reviewEveryRuns` | Every how many Dream runs the **review pass** runs: a pass whose only job is to make the files smaller, rather than to add to them. At the default interval, twelve runs is about once a day. **`/dream budget review` refuses anything below 12** — see below. Editing `config.json` by hand is not blocked (the schema still accepts any value from `1` up, so a restored config always loads), but 12 is the number the design assumes. | `12` |

The budgets are counted in **characters**, not tokens, because that is the only unit the model can count while it is writing.

**These caps bind Dream, not Jenny.** The refusal is mounted only on the tools Dream's own runs get. The tools Jenny uses while you are talking to her carry no size guard at all, so a chat turn can write past a budget and nothing stops it — measured on a real device at 2,399 characters against a cap of 2,400. That is deliberate. A refusal in the middle of a conversation would land on the one writer that has you sitting there, and it would trade a visible failure for an invisible one: the thing you just asked Jenny to remember would quietly not be saved. So for the main agent the numbers are **advisory** — the size the review pass aims for, not a wall. They are enforced where the writer is unattended and has a review pass behind it to make room.

The cost of that choice is worth knowing: a chat turn can leave a file saturated, and it is Dream that then finds no room. If you see Dream reporting that it consolidated nothing, a file already at its cap is the first thing to check — `/dream budget` shows it.

**Being over budget is not an error.** It means the next thing Dream wants to add has to wait for the review pass to make room — usually by *moving* something to where it belongs (a task specification to a skill file, project context to `MEMORY.md`) rather than by forgetting it.

### The review cadence has a floor of 12, and it is enforced

`/dream budget review 4` does not write. The command refuses anything below **12** runs and tells you why, because below that the review passes start meeting each other: the second one lands on a file the first has already pruned, and it keeps looking for things to remove. Measured on a real device, two consecutive passes took `USER.md` from 3,524 characters to 1,626 — 31% of that on the second pass alone — and a forced pass on a later build removed five real entries: two open questions, a plan, a biographical detail and one insight.

Losing them is no longer possible. Every entry that leaves `USER.md` or `memory/MEMORY.md` is written to `memory/archive/` before the shrinking write lands, whichever tool does the shrinking — measured over two runs at `reviewEveryRuns: 1`, ten entries were archived and nothing was lost. So the floor is not there to stop deletion any more. It is there because a faster cadence spends tokens on every Dream run, unattended, for pruning that has nothing left to prune, and 12 is the cadence the rest of the design assumes.

If you want a faster cadence anyway — measuring on a real device is the reason this path exists — the refusal prints a confirmation phrase to repeat the command with:

```
/dream budget review 1 i-accept-back-to-back-reviews
```

It is a phrase rather than a short flag on purpose: it should be something you decide to type, not something that gets added for you. Setting it back is `/dream budget review 12`, with no phrase needed.

### The budgets bound what Dream writes, not what a turn pays

These are budgets on the *files*, not on the prompt. There is no read-side cap on `SOUL.md`, `USER.md` or `memory/MEMORY.md`: each one is injected into the system prompt whole, on every turn, however long it has become — and `SOUL.md` has no write-side budget either, since `soulBudgetChars` ships at `0`. Everything else injected alongside them *is* capped: `memory/WIKI.md` at ~1,200 tokens, the unprocessed history at ~8,000, the page content inside a [project](./projects.md) at 6,000 characters. The three durable files are the deliberate exception. (`AGENTS.md` is a fourth file loaded unfiltered, and it has no budget and no curator at all — that one is an open question, not a decision.)

The reason is that a cap at injection time would be a limit with nobody behind it. What actually keeps these files small is the review pass: it reads a file before deciding, and *moves* what doesn't belong there rather than dropping it. That is what took `SOUL.md` from 6,447 characters to about 2,100 in under a week — by relocating platform notes into the app's own bundled templates, where they get rewritten at every boot. A cap can't do that. It can't tell Jenny's identity from a stale implementation note; it would cut whichever of the two happens to sit at the end of the file, on every turn, and report it to nobody who could act. A refused write, by contrast, leaves the file intact and tells the writer, the log, the counters and — if it keeps happening — you.

The other half of the reason is that these files are terminal. A truncated line in the wiki directory costs you a link, and the page it pointed at is still one `read_file` away; the tail of `USER.md` is not written down anywhere else, so a "the rest is over there" notice would have nothing to point at.

What that leaves you responsible for: the system prompt is a fixed cost, so if these files ever do get big, it's the live conversation that gets compacted earlier to make room. `/dream budget` is the number to watch, and it's a number to act on rather than a wall that will act for you.

### Atlas

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

### Related settings, and where to change them

Related settings that shape *when* material reaches Dream in the first place (not Dream-specific, but relevant here) live under `agents.defaults` too: `idleCompactAfterMinutes` (idle-triggered compaction, default 15 minutes), `maxMessages` (default 120), and the consolidation ratio that controls how aggressively old messages are summarized (default 0.5). See [Configuration](../reference/configuration.md) for the full reference.

None of this is exposed in the Settings UI today. The four budget and cadence fields have a chat surface — `/dream budget`, in the table above — and everything else, including Dream's interval and the compaction thresholds, can currently only be changed by editing `config.json` directly. The memory files themselves need no special mode to see: `SOUL.md`, `USER.md`, `memory/MEMORY.md`, and `memory/history.jsonl` are all visible from the Workspace file browser by default. The only things the file browser hides by default are dotfiles and a handful of runtime-internal paths (`config.json`, `agent/`, `cron/`, `sessions/`, `ui/`) — including the `memory/.cursor` and `memory/.dream_cursor` cursor files, which are dotfiles. Turning on **Developer mode** in Settings → System reveals those too, but it has no effect on the memory files themselves, which were never hidden.

## Gotchas worth knowing

- **"Every 2 hours" is a floor, not a promise.** Since 0.6.0 the deadline survives an app restart and a run missed while the app was dead is caught up shortly after it comes back — before that, every restart pushed it out by another 2 hours. The second half of the problem was doze: with the screen off the phone suspends, and the timer Dream was waiting on stops advancing with it, so the gap stretched even on a process that had stayed alive for hours. Since 0.6.6 the scheduler also asks Android to wake the phone at the next deadline, and holds the CPU awake for the length of the run itself, so a consolidation pass no longer has to wait for the phone to wake on its own or risk freezing halfway through. It is still a floor: an alarm the OS downgrades to inexact, or a battery manager that kills the app outright, will still push a run later. See [Scheduling and proactivity](./scheduling.md#what-066-changed-and-what-it-didnt) for what is and isn't guaranteed, and Settings → Background activity for whether Jenny has actually been up.
- **Dream never sees a project conversation.** Everything on this page is about the personal chat. A [project](./projects.md) conversation is not archived into `memory/history.jsonl` at all, so nothing said inside a project can reach `MEMORY.md`, `USER.md` or `SOUL.md`. The boundary is one-directional on purpose: your profile *is* read into a project's turns, but a project's content never flows back. A project remembers by writing pages in its own folder instead.
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
