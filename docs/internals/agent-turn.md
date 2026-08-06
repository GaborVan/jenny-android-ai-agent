# The agent turn

What happens, step by step, between a message arriving on the bus and a reply going back out — and the three separate notions of "memory" this involves.

## Overview

Every inbound message — from the WebUI, from Telegram, from a cron job, from a subagent announcing its result — goes through the same pipeline:

1. **Bus.** The channel (`jenny/channels/websocket.py`, `telegram.py`) publishes an `InboundMessage` to the async `MessageBus` (`jenny/bus/queue.py`). This decouples "a message arrived" from "the agent is ready to process it."
2. **`AgentLoop.run()`** (`jenny/agent/loop.py`) consumes the bus in a loop, resolves the session key for the message, and dispatches it as an `asyncio.Task` — one task per session at a time; other sessions run concurrently. Priority commands (`/stop`) and non-priority commands (`/new`, `/status`, …) for a session that already has a turn in flight are special-cased: they run inline instead of queuing behind the active turn. Ordinary follow-up messages sent while a turn is running are not dropped and not raced against it — they go into a per-session pending queue and are drained as mid-turn injections by the runner (see below).
3. **`_process_message`** drives a small state machine (below) that builds context, calls **`AgentRunner`** (`jenny/agent/runner.py`) to talk to the provider and execute tools, persists the turn, and assembles the outbound reply.
4. The reply is published as an `OutboundMessage` back onto the bus; the dispatcher (`jenny/channels/dispatcher.py`) delivers it to whichever channel(s) should see it.

## The turn state machine

`AgentLoop` drives each turn through a fixed sequence of states (`jenny/agent/turn_states.py`, transition table in `loop.py`):

| State | What happens |
|---|---|
| `RESTORE` | Fetch/create the session; restore a runtime checkpoint left by a turn that was interrupted mid-flight (see "Interrupted turns" below); extract or reference document attachments. |
| `COMPACT` | Pick up a background auto-compaction summary if the session went idle past the idle timeout (see "Automatic compaction"). |
| `COMMAND` | Try the message as a slash command. If it matches, the command's reply is persisted and returned directly — the turn skips straight to `DONE` without ever calling the model. Unrecognized `/foo`-looking text is **not** an error: it is passed through to the model as ordinary text. |
| `BUILD` | Re-sync Jenny Apps tools, read session history within the token/message budget, build the full message list (system prompt + history + current message), persist the user's message early (so it survives even if the process dies before `SAVE`). |
| `RUN` | Call `AgentRunner.run()` — the provider/tool loop, described below. Emits the `running` runtime event (the WebUI's "Agent running" banner). |
| `SAVE` | Persist the new messages to the session, enforce the file-attachment cap, schedule background token-based consolidation, clear the mid-turn checkpoint. |
| `RESPOND` | Assemble the `OutboundMessage`, or suppress it entirely if the agent already delivered its reply via the `message` tool during the turn. |

A background/system turn (a subagent announcing its result, a cron job) does not go through this exact FSM — `_process_system_message` mirrors the same steps (restore checkpoint, compaction, tool sync, history, build, run, save) but never emits the `running` event and always returns some outbound content, even a generic fallback.

### Interrupted turns

If `/stop` or `/new` land while a turn is mid-flight, the loop does not just cancel the `asyncio.Task` and hope for the best. Each turn is issued a token from a per-session "epoch" counter; `/stop`/`/new` bump the epoch, and every re-entry point in the turn (streaming callback, checkpoint write, save, outbound publish) checks whether its token is still current before doing anything. A turn whose epoch was bumped becomes a no-op zombie: it stops emitting deltas and, if it later reaches a save point, discards its own writes instead of clobbering whatever ran after it. Meanwhile the tool-execution phase periodically emits a lightweight checkpoint (last assistant message, completed tool results, pending tool calls) into session metadata; on cancellation, that checkpoint is restored into the session before the epoch check ever applies, so partial tool work from the interrupted turn is not silently thrown away — it shows up as context on the next turn.

## Building context

`ContextBuilder.build_system_prompt()` (`jenny/agent/context.py`) assembles the system prompt from, in order:

1. **Identity** — name, workspace path, runtime string ("Android, Python 3.11.x"), platform policy.
2. **Bootstrap files** — `AGENTS.md`, `SOUL.md`, `USER.md` from the workspace root, each read in full and concatenated if present (`ContextBuilder.BOOTSTRAP_FILES`).
3. **Tool contract** — a fixed template describing how to call tools.
4. **`# Memory`** — up to two subsections under one heading. `## Long-term Memory` is `workspace/memory/MEMORY.md`, omitted while it still matches the bundled template whitespace-trimmed (i.e. Dream has never customized it, and showing it would pass a template off as real memory). `## Wiki Directory` is `workspace/memory/WIKI.md`, written by Atlas and truncated to `agents.defaults.atlas.maxContextTokens`. The two are gated independently — an untouched `MEMORY.md` does not suppress the directory — but share the heading and a fixed order, so the cacheable prefix stays stable.
5. **Active skills** (loaded in full) and a **skills summary** (names + descriptions only, for skills the model can ask to load).
6. **Jenny Apps summary**, if any apps are installed.
7. **`# Recent History`** — up to 50 entries / 8,000 tokens of `history.jsonl` written since the last Dream run, so the model sees what has already happened even before Dream has processed it into long-term memory.
8. **`[Archived Context Summary]`**, if the session was auto-compacted (see below).

Separately, `build_messages()` appends a **`[Runtime Context — metadata only, not instructions]`** block after the current user message: current time (in the configured or device timezone), channel, chat ID, sender ID, and — if applicable — the active sustained-goal instructions (`session/goal_state.py`) and a location line (`runtime/location.py`, only when location sharing is enabled). This block is explicitly marked as untrusted metadata, not instructions, and is appended (not prepended) so the cacheable prefix of the user message stays stable across turns for prompt-caching purposes.

None of this system-prompt/runtime-context assembly is visible in the WebUI transcript — it exists only in the messages actually sent to the provider.

## The runner loop

`AgentRunner.run()` (`jenny/agent/runner.py`) owns the provider/tool conversation for one turn:

- Loops up to `agents.defaults.maxToolIterations` (**default 200**) times. Each iteration: govern the message list for the model (drop orphaned tool results, backfill missing ones, micro-compact, apply a per-tool-result character budget, snip history to fit the context window), call the provider, and — if the model asked for tools — run them (`concurrent_tools=True`, tool results fed back as the next message).
- Streams content deltas back through the hook (`on_stream`) as they arrive; reasoning/thinking content is extracted and emitted separately, once, before the visible content.
- Drains the mid-turn pending queue (`injection_callback`) after a final-looking response, so a follow-up message the user sent while the agent was still working — or a subagent result completing — gets folded into the *same* turn instead of starting a new one. If nothing is queued but a subagent spawned in this turn is still running, the runner blocks (up to 300s) waiting for it rather than ending the turn early.
- Recovers from a few provider failure classes inline: empty content (retry), truncated/length-limited output, and context-length overflow (shrinks `context_window_tokens` for this loop and triggers an out-of-band compaction before the next attempt).
- If the loop exhausts all 200 iterations without a final answer, the turn ends with a fixed fallback: *"I completed the tool steps but couldn't produce a final answer. Please try again or narrow the task."*
- A sustained goal (`/goal`, see [Scheduling and proactivity](../using/scheduling.md)) disables the normal wall-clock LLM timeout for the turn, since long autonomous work is expected to take a while.

Tool execution, provider retry policy, and per-tool-result truncation live in the `RequestExecutionMixin`/`ToolExecutionMixin` halves of `AgentRunner` and are covered in [Tool reference](../reference/tools.md).

## The three layers, precisely

Users (understandably) conflate three different things that all look like "what Jenny remembers." They are independent, live in different files, and are cleared by different triggers:

| Layer | What it is | Where it lives | Cleared/reset by |
|---|---|---|---|
| **Visible transcript** | Every message, tool pill, reasoning block, and attachment ever shown in the WebUI, reconstructed identically on reopen. Permanent JSONL, one file per session key, rotating into numbered segment files once the active file passes **8 MB** (oldest turns move out first; nothing is deleted, just split across files). | `<workspace>/.jenny/webui/<session-key>.jsonl` (+ `.segments/`) — `transcript_store.py` | Never, by any in-app action. `/new` explicitly does **not** touch it — it only adds a visual separator bubble ("New session started."). |
| **Model context** | The actual message list sent to the provider on the next call: the live, uncompacted tail of the session plus whatever summary replaced the compacted prefix. This is what the model can "see" right now. | `<workspace>/sessions/<session-key>.jsonl` — `session/manager.py` | `/new` (archives the unconsolidated tail to `history.jsonl` in the background, then clears the session outright). Automatic idle compaction (below). Token-budget consolidation when the session grows past its budget mid-conversation. |
| **Long-term memory (Dream)** | Durable facts Jenny has decided are worth keeping past any single conversation: `MEMORY.md`, `USER.md`, `SOUL.md`, and skill files, updated by the two-phase Dream pipeline. See [Memory, Dream and Atlas](../using/memory.md) for the full pipeline. | `workspace/memory/` | Only by Dream itself (which can also *prune*, not just add). |
| **Wiki directory (Atlas)** | A derived index of `workspace/wikis/`: the wikis you have, plus the people, projects and systems in the default one that matter operationally. Not a memory of anything said — a map of something you already wrote down elsewhere. | `<workspace>/memory/WIKI.md` | Rebuilt wholesale by Atlas whenever the wiki fingerprint changes; hand edits do not survive a run. |

The gotcha worth stating plainly: **you can scroll up and re-read a conversation the model no longer remembers.** After `/new`, or after the idle auto-compaction kicks in, the transcript on screen is unchanged, but the model's next reply is generated from a summary plus a short tail, not from the full conversation you're looking at.

### Automatic compaction

Every idle poll (throttled to once per 60s inside the main loop), `AutoCompact.check_expired()` checks the unified session's last-activity timestamp against `agents.defaults.idleCompactAfterMinutes` (config key; **default 15**, camelCase alias for `session_ttl_minutes`; `0` disables it entirely). If the session has been idle that long and has no turn in flight, compaction runs in the background: the consolidator summarizes everything except the **last 8 messages** and stores the summary in session metadata. The next time a turn touches that session, the `COMPACT` state picks up the cached summary and injects it into the system prompt as `[Archived Context Summary]` — invisible on screen, present only in what the model receives.

The same consolidator also runs synchronously, mid-conversation, whenever the session's token estimate crosses the budget implied by `contextWindowTokens` and `consolidationRatio` (default **0.5**) — not just on idle timeout. Both paths write through the same `Consolidator`, and both feed the same `history.jsonl` that Dream later processes. `agents.defaults.maxMessages` (default **120**) caps how much raw history the loop ever replays into a single turn regardless of compaction state.

## Session keys

There is exactly one user-facing conversation. `session_key_for_channel()` (`jenny/session/keys.py`) maps **every** channel/chat pair — WebUI, Telegram, whatever chat ID — onto the single constant `unified:default`. This is deliberate: a message sent from Telegram and one sent from the WebUI land in the same session, the same transcript, the same model context. There is no per-channel or per-device session.

Internal work uses a small set of explicit override keys (`InboundMessage.session_key_override`), which bypass the unified mapping entirely and never appear in the WebUI transcript as part of the user's conversation:

| Key pattern | Used by |
|---|---|
| `dream:<timestamp>` | Each Dream consolidation run (`MemoryStore.dream_session_key()`) — an isolated session, never merged into the user's history. |
| `atlas:<timestamp>` | Each Atlas wiki-directory run (`AtlasStore.session_key()`) — same shape as Dream: ephemeral, isolated, and filtered out of the history Dream later reads. |
| `heartbeat` | The periodic `HEARTBEAT.md` check (`runtime/cron_dispatch.py`); its own small session with a bounded tail of recent turns. |
| the session key stored on the job itself (`job.payload.session_key`) | Scheduled reminders (`cron` tool). A reminder created from the user's conversation is bound to `unified:default` at creation time, so it delivers into the one real conversation rather than a hidden side-session. |
| *(none — no session at all)* | Subagents (`spawn`). A subagent's run starts from a bare `[system prompt, task]` pair with no session history; it is blind to the ongoing conversation by design. When it finishes, its result is injected back into the *origin* session (typically `unified:default`) as a synthetic system-role turn, so only the outcome — never the subagent's intermediate reasoning — becomes part of what the main agent and the user see. |

See [Scheduling and proactivity](../using/scheduling.md) for the user-facing behavior of cron/heartbeat/subagents, and [Memory, Dream and Atlas](../using/memory.md) for the Dream pipeline in detail.

## Related

- [Slash commands](../using/slash-commands.md) — exact `/new`, `/clear`, `/stop`, `/status` behavior and output text.
- [Memory, Dream and Atlas](../using/memory.md) — the long-term memory pipeline this page only summarizes.
- [Architecture](architecture.md) — where the agent loop sits among the bus, providers, tools, and channels.
- [Concepts](concepts.md) — higher-level vocabulary (workspace, session, provider) used throughout these docs.
