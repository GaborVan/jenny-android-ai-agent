# Slash commands

Typing a message that starts with `/` in the chat input can trigger a built-in command instead of a normal turn. You don't have to remember any of them: the **Commands** chip above the composer lists them all with a one-line description each, and picking one either sends it or fills the composer in for you. Here is the full list and exactly what each one prints.

## How commands work

Commands are matched on the whole message (case-insensitive), either as an exact word (`/new`) or as a prefix followed by an argument (`/model fast`). If what you type doesn't match any known command, it is **not rejected** — it's forwarded to the agent as an ordinary chat message, exactly as if it had no leading slash.

You can type them by hand, or pick them from the **Commands** chip in the row above the composer, next to the scope chip and the write switch. That list is built from the same table that `/help` prints, so it can't drift out of date; a command that takes an argument (`/model`, `/history`, `/goal`, …) is written into the composer for you instead of being sent immediately, so you can finish the line. There is still no autocomplete on `/` itself.

## Where a command works

**The subject decides.** Each command acts on one of three things, and that is what says where it can be sent from:

| Acts on | Commands | Where |
|---|---|---|
| **this conversation** | `/new` `/stop` `/status` `/history` `/goal` `/help` | anywhere |
| **the personal memory, or the installation** | `/dream` `/atlas` `/model` `/skill` | the personal chat only |
| **this project** | `/gardener` `/tidy` `/init` | inside a [project](./projects.md) only |

Both halves are enforced, and both are visible: the Commands chip lists what this conversation can do — entering a project *removes* `/dream` and friends as well as adding `/tidy` — and `/help` prints the same list. Sending one anyway is refused with a line that says where it does work, and the refusal comes from the command layer: it never reaches the model as a message.

Two consequences worth naming. Working on a project is always done **from inside** it: no command takes a project name, the same way the journal tool has no argument to reach another project's journal. And on Telegram — always the personal conversation — the project commands simply do not exist.

Two commands — `/stop` and `/status` — are handled on a "priority" fast path that runs even while a turn is actively streaming or a tool is executing. The rest wait for the current dispatch to be free, which in practice is rarely noticeable.

All server-side command responses below are **hardcoded in English**, regardless of whether the WebUI is set to Italian or English. This is true for the confirmation text, the usage/error messages, and the `/status`/`/model` output.

## The server commands

| Command | Arguments | What it does |
|---|---|---|
| `/new` | none | Stops the active task (if any) and clears the model's context for a fresh conversation |
| `/stop` | none | Cancels the active agent turn for this chat |
| `/status` | none | Shows a runtime snapshot: version, model, token usage, context budget, session size, uptime, active tasks |
| `/model` | `[preset]` | Shows the current model/preset, or switches the active preset |
| `/history` | `[n]` | Prints the last `n` persisted user/assistant messages (default 10, max 50) |
| `/goal` | `<description>` | Tells the agent to treat the request as a long-running goal |
| `/dream` | none | Manually triggers a memory consolidation (Dream) run in the background |
| `/atlas` | `[force]` | Rebuilds the wiki directory (`memory/WIKI.md`) from your wikis, in the background |
| `/gardener` | none | Runs one [gardener](./gardener.md) pass on the project you are in, now |
| `/skill` | none | Lists the currently enabled skills with their descriptions |
| `/help` | none | Lists the commands of this conversation |

Two more, `/tidy` and `/init`, appear in the chip and in `/help` but are not in the list above because they are not commands in the same sense: inside a [project](./projects.md) each is expanded into an ordinary agent turn — one restructures that project's wiki, the other writes its `AGENTS.md`. Outside a project they are refused like any other project command.

**The knobs are not here.** Dream's budgets and review cadence, and everything about the periodic gardener pass, used to be arguments of `/dream` and `/gardener`. They are settings, so they live in **Settings** — under *Memory* and *Wiki and projects* — next to the numbers they act on. Typing the old form answers with where it went.

Full details and exact output text for each command follow.

### `/new` — start a fresh conversation

Cancels any active task first, then clears the model's context (the LLM stops remembering everything before this point) and archives the discarded messages into long-term memory for later Dream processing. The response is rendered as a separator line in the chat, not a bubble:

```text
New session started.
```

There are three ways to run it: the **New chat** button in the composer (next to the paperclip), the first entry in the Commands chip, or typing `/new`. The first two ask for confirmation first, since the reset can't be undone.

**The screen starts again from the separator.** The conversation before it is not deleted: the separator is a page break in the visible history, and scrolling up loads the previous session as an older page. That's true after closing and reopening the app too — the chat comes back starting at the last separator, not at the top of everything.

**And the model really does start empty.** Two things would otherwise leak back into the very next turn's system prompt through the `# Recent History` block ([the agent turn](../internals/agent-turn.md)): the summaries of any auto-compaction that happened during the cleared conversation, and the summary `/new` itself writes when archiving it. Neither is injected any more — a per-session floor covers the first, and the archived summary is marked as Dream-only for the second. Dream still consolidates both into long-term memory: `/new` resets what is *in front of* the model, and never deletes what it has already learned.

If a conversation seems to keep dragging in an old topic — or to ignore a rule you added to `AGENTS.md` halfway through, which it re-reads from disk on every turn — this is the command you want. A model imitates its own history, and the history is what `/new` takes away.

### `/stop` — cancel the active turn

There is no stop button in the WebUI; this command is the only way to interrupt a turn that's in progress, and it works specifically because it's on the priority fast path (it's processed even mid-turn, before the normal dispatch lock). Response:

```text
Stopped N task(s).
```

or, if nothing was running:

```text
No active task to stop.
```

`/stop` also cancels an active `/goal` and discards any subagent working in the background for this chat — a subagent that finishes after being stopped has its result silently thrown away.

### `/status` — runtime snapshot

No arguments. Output is a fixed-format block (rendered as plain text, not markdown), for example:

```text
🐈 jenny v0.9.5
🧠 Model: gpt-4o
📊 Tokens: 1234 in / 567 out (40% cached)
📚 Context: 12k/65k (22% of input budget)
💬 Session: 48 messages
⏱ Uptime: 2h 14m
⚡ Tasks: 0 active
```

The cached-percentage part of the token line only appears when the last turn actually used cached tokens. "% of input budget" is the context estimate divided by (context window − max output tokens − a small safety margin), not divided by the raw context window. `/status` and `/stop` are the two commands that work even while a turn is running.

### `/model [preset]` — show or switch model preset

Without an argument, shows the current state:

```text
## Model
- Current model: `gpt-4o`
- Current preset: `default`
- Available presets: `default`, `fast`, `deep`
```

`default` is always available and reflects the plain `agents.defaults.*` model fields; named presets come from `modelPresets` in `config.json` — there is no UI for creating presets, they exist only in the config file. See [Configuration reference](../reference/configuration.md#modelpresets).

With one argument, it switches presets for future turns and confirms:

```text
Switched model preset to `fast`.
- Model: `gpt-4o-mini`
- Context window: 65536
- Max output tokens: 4096
```

If the name doesn't match a configured preset:

```text
Could not switch model preset: <error detail>

Available presets: `default`, `fast`, `deep`
```

If you pass more than one word:

```text
Usage: `/model [preset]`
```

Switching is **runtime-only**: it does not rewrite `config.json`, and a turn that is already in progress keeps using the model it started with.

### `/history [n]` — print recent messages

Shows the last `n` persisted user/assistant messages from the current session. Default `n` is 10, maximum is 50 (a value above 50 is silently capped, not rejected). Each message is truncated to 200 characters with a trailing `…`.

```text
Last 10 message(s):
👤 You: what's the weather like today?
🤖 Bot: It's sunny and 22°C where you are right now.
```

If there's nothing to show:

```text
No conversation history yet.
```

If the argument isn't a number:

```text
Usage: /history [count] — e.g. /history 5 (default: 10, max: 50)
```

This reads the persisted session history, not the on-screen transcript: the two are separate, and `/new` resets the first without deleting the second.

### `/goal <description>` — start a long-running goal

Rewrites your message into a normal agent turn that instructs the model to register a sustained objective (via the internal `long_task` tool) instead of answering as a one-shot request. There's no separate "goal view" — it's the same chat, with normal turns, but the objective stays pinned in the agent's context (so it survives compaction) and the per-turn timeout is disabled until it's done. A banner with a running timer appears while a turn is in progress.

Without a description:

```text
Usage: /goal <long-running task description>
```

If a task is already running in this chat:

```text
A task is already running for this chat. Use `/stop` first, then send `/goal <long-running task description>` again.
```

Only one goal can be active per chat at a time. The agent closes it itself (or you can cancel with `/stop`); an inactive goal also expires automatically after 12 hours.

### `/dream` — run memory consolidation now

Triggers Dream (the long-term memory consolidation job) in the background. It replies immediately:

```text
Dreaming...
```

and later, once the run finishes, with a separate message giving the outcome — one of:

```text
Dream completed in 4.2s.
```
```text
Dream completed in 4.2s but wrote nothing (attempts blocked/refused); memory cursor was not advanced.
```
```text
Dream did not complete after 4.2s; memory cursor was not advanced.
```
```text
Dream failed after 4.2s: <error>
```

If there's no new history to process yet (common on a fresh or short chat, since Dream only reads from `memory/history.jsonl`, which is only populated after compaction), you get a longer explanation instead, ending with suggestions like enabling `idleCompactAfterMinutes`. See [Memory, Dream and Atlas](./memory.md) for the full model.

The command takes no arguments. The three file budgets, the review cadence, and Dream's own schedule are in **Settings → Memory**, which also shows what each file currently measures — the number the budget is chosen from. `/dream budget …` answers with a line saying so.

### `/atlas` — rebuild the wiki directory now

Triggers Atlas, the job that compiles your wikis into `memory/WIKI.md`. Like `/dream` it acknowledges immediately:

```text
Mapping the wiki...
```

and follows up with the outcome. The interesting cases are the ones where it deliberately does nothing:

```text
Atlas updated `memory/WIKI.md` in 6.4s.
```
```text
The wiki hasn't changed since the last Atlas run, so `memory/WIKI.md` is already current — no tokens spent. Use `/atlas force` to rebuild it anyway.
```
```text
Atlas found no wikis to map.
```

`/atlas force` skips the change check and rebuilds regardless. It does not skip the "do you have any wikis" check — with no wikis there is nothing to compile. See [Atlas](./memory.md#atlas-the-wiki-side-of-memory).

### `/gardener` — run a gardener pass on this project

Inside a [project](./projects.md), `/gardener` runs one [gardener](./gardener.md) pass on that project right now: it acknowledges with `Gardening <name>...` and follows up when the pass finishes. It takes no arguments — the project you are in *is* the subject.

Outside a project it is refused, and the refusal says to open one. There is no way to garden a project from the personal chat: that is deliberate, and it matches the tool layer, where the journal has no argument for reaching another project either.

The periodic pass — whether it runs at all, how often it looks, how much silence it waits for, how long before it returns to the same project — is in **Settings → Wiki and projects**. Turning it off there leaves `/gardener` working by hand. `/gardener settings` and the other old words answer with a line saying where they went.

### `/tidy` — restructure this project's wiki

Only inside a project, and like `/init` it is not answered by the command layer: it becomes a full agent turn, in this conversation, with the project's pages and your answers in hand. It splits pages that have outgrown the per-turn budget, moves prose out of an oversized map, and realigns the page list — the operation a periodic gardener pass cannot do, because it has nobody to ask. If the wiki is already in good shape it says so in one line and changes nothing.

### `/init` — write this project's instructions

Only inside a project. Unlike everything else on this page it is not answered by the command layer: it is expanded into a full agent turn that reads `wiki/index.md`, the pages, the recent `log/` entries and the existing instructions file, and then writes that project's `AGENTS.md` — scope, the conventions the pages already follow, and the open questions. If the file already has content it is updated rather than replaced. What you see in the chat stays `/init`.

Outside a project, the same refusal every project command gives:

```text
`/init` works on one project, and this conversation is not a project.

Open the project — the chip above the composer does it — and send `/init` there.
```

### `/skill` — list enabled skills

```text
Available skills (3):

- **weather** — Look up current weather and forecasts.
- **app-creator** — Guide the user through building a new Jenny App.
- **llm-wiki** — Maintain the workspace wiki (scaffold, ingest, compile).
```

or, if none are enabled:

```text
No skills available.
```

### `/help` — list the commands of this conversation

In the personal chat:

```text
✿ jenny commands:
/new — Stop the current task and start a fresh conversation.
/stop — Cancel the active agent turn for this chat.
/status — Display runtime, provider, and channel status.
/model [preset] — Show or switch the active model preset.
/history [n] — Print the last N persisted conversation messages.
/goal <goal> — Tell the agent to treat the request as a long-running goal.
/dream — Manually trigger memory consolidation now. The budgets and the review cadence live in Settings, under Memory.
/atlas [force] — Rebuild the wiki directory in memory/WIKI.md. Add 'force' to skip the change check.
/skill — List enabled skills and their descriptions.
/help — List available slash commands.
```

Inside a project the list is a different one, not a longer one: `/dream`, `/atlas`, `/model` and `/skill` drop out, and `/gardener`, `/tidy` and `/init` appear.

## What `/new` does and does not delete

`/new` is a reset of the model's context, not a delete button — nothing is destroyed on the server:

| | What happens |
|---|---|
| The model's context | Cleared. It stops remembering anything before the separator. |
| The visible chat | Starts again from the separator. Scroll up to reach the previous session; reopening the app shows the same thing. |
| The persisted transcript | Untouched — it is a separate, permanent log, and the page break only changes where reading starts. |
| Long-term memory | Untouched. The discarded conversation is archived for Dream, which will consolidate it into `MEMORY.md` as usual. |

If you want a conversation to actually go away, deleting it is a different operation: a [project](./projects.md) and its conversation are removed together (tap the bin on its row in the scope chip, or use the file manager), and the personal conversation's transcript lives in the workspace.

> **A note for anyone upgrading.** There used to be a `/clear` command, undocumented in `/help` and handled entirely inside the WebUI: it wiped the screen, printed `Chat cleared.` and left the model's context completely intact. It gave a convincing confirmation for something it had not done — the model went on remembering everything — and it was the first thing most people tried. It has been removed. Typing `/clear` now just sends an ordinary message to the agent; the command you want is `/new`.

## Periodic tasks (HEARTBEAT.md)

This is unrelated to slash commands but shares the same "plain files, no terminal" spirit: Jenny also runs a periodic check every 30 minutes (`gateway.heartbeat.intervalS`, default 1800) driven by `workspace/HEARTBEAT.md`. It only acts on lines under a `## Active Tasks` heading; everything else in the file is ignored. You can edit that file directly from the Workspace tab, or just ask Jenny in chat to "add a periodic task" and she'll update it for you. This job shows up in the agent's internal job list as `heartbeat`, but it's system-managed and can't be removed the way a normal reminder can; to disable it you'd set `gateway.heartbeat.enabled` to `false` in `config.json` and restart the app (there is no in-app toggle for it). See [Scheduling and proactivity](./scheduling.md) for the full picture, including the cost-per-cycle caveat and reliability limits.

## See also

- [Chat basics](./chat.md) for the message composer, streaming, and the "Agent running" banner referenced above.
- [Memory, Dream and Atlas](./memory.md) for what `/dream` and `/atlas` actually process, and why either can say there's nothing to do.
- [Scheduling and proactivity](./scheduling.md) for `/goal`, reminders, and the heartbeat.
- [Configuration reference](../reference/configuration.md) for `modelPresets` and other config-only settings.
- [Troubleshooting](./troubleshooting.md) if a command's response looks wrong or the chat seems unresponsive.
