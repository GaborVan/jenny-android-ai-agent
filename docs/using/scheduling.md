# Scheduling and proactivity

Jenny can remind you of things, watch a checklist in the background, work a long task across many turns, and delegate side work to a helper agent — all by asking in chat, no separate scheduling screen involved.

## The one thing to know before you rely on this

**Everything in this page lives inside the app's own gateway process.** There is no server in the cloud keeping time for you. If Android kills the app (or you swipe it away, or the battery optimizer freezes it) at the moment a reminder was supposed to fire, here is what actually happens:

- A **one-shot reminder** ("remind me at 6pm") whose time passed while the app was dead is lost **forever, silently**. When the gateway starts back up it recomputes each job's next run; for a one-shot whose time is already in the past, the next run comes back empty and the job is never retried or reported missed — nothing tells you it didn't happen.
- A **recurring reminder** ("every 30 minutes", "every day") does not catch up on missed runs either. Every time the app restarts, its interval resets to "now + interval" — so a daily reminder that should have fired at 9am, if the app happened to restart at 9:05am, now fires roughly 24 hours from the restart, not from the original schedule.
- The same applies to the built-in Heartbeat and Dream jobs described below: their clocks also restart from zero whenever the app restarts.

None of this is announced anywhere in the app. If reminders matter to you, the practical fix is to keep the app from being killed:

- Grant the battery-optimization exemption Jenny's first-run flow offers (or add it later from Android's own battery settings) so the OS is less likely to freeze the background service.
- Keep the phone charged and connected when a reminder is close to due.
- Treat "at" reminders as best-effort, not guaranteed alarms — for anything truly time-critical, use your phone's own alarm clock as a backup.

<!-- TODO: verify on-device (O-5): how reminders and the heartbeat actually behave under real Doze/battery-optimization conditions without the exemption granted — the code guarantees no catch-up, but the practical drift/skip rate under Doze hasn't been measured. -->

## Reminders (the `cron` tool)

You don't configure this from a screen — you just ask, in plain language, and Jenny translates it into a scheduled job:

- "Remind me to take the pizza out in 20 minutes."
- "Every day at 9am, ask me how I slept."
- "Every 2 hours, check if it's raining and tell me if so."
- "List my reminders."
- "Cancel the pizza reminder."

Under the hood there are three schedule kinds:

| Kind | How you'd phrase it | Behavior |
|---|---|---|
| One-shot (`at`) | "remind me at 6pm", "in 20 minutes" | Fires once, then the job deletes itself automatically. |
| Interval (`every`) | "every 30 minutes", "every 2 hours" | Repeats forever at a fixed interval, counted from when the job was (re)armed — not from a fixed clock time. |
| Cron expression (`cron_expr`) | "every day at 9am", "every Monday at 8" | Standard 5-field cron syntax (e.g. `0 9 * * *`); accepts an optional IANA timezone (e.g. `America/Vancouver`) for that one job. |

The timezone used when you don't specify one is your **device's timezone**, resolved once when the app starts (falls back to UTC if the device timezone can't be determined). A timezone override only applies to cron-expression schedules — one-shot and interval schedules always use the device timezone.

When a reminder fires, it runs as a completely normal agent turn in the same chat you created it from, seeded with an instruction along the lines of "the scheduled time has arrived, execute this job and report the result." The reply lands in your chat exactly like any other message from Jenny — and, if the app isn't in the foreground at that moment, it also rings as an Android notification titled "Jenny ⏰ <job name>" (see [Notifications](#android-notifications) below). If a reminder fires while you're already mid-conversation with Jenny on that same chat, it politely waits and delivers once your current turn is idle, rather than interrupting it.

A few smaller things worth knowing:

- A reminder job cannot schedule further jobs from inside its own execution — this only matters if you ask Jenny to "set up a reminder that then sets another reminder" in one step.
- The job's default name is just the first 30 characters of your reminder's message, unless you give it something more memorable.
- You can list and remove reminders by asking — there is no dedicated screen for this; it's entirely conversational (e.g. "what reminders do I have?", "remove job xyz").

### Protected system jobs

When you ask Jenny to list reminders, you'll also see three jobs you didn't create: **`dream`**, **`atlas`** and **`heartbeat`**. These are system-managed and will show up as protected — visible for inspection, but Jenny will refuse to remove them if asked (a removal attempt gets a reply along the lines of "this is a protected system-managed cron job" and cannot be removed). `dream` runs the memory-consolidation pass and `atlas` rebuilds the wiki directory, both described in [Memory, Dream and Atlas](memory.md); `heartbeat` is described next.

## Heartbeat: a periodic checklist

Heartbeat is Jenny's own background watchdog, driven entirely by one file: `workspace/HEARTBEAT.md` in your workspace. You can edit it directly through the Workspace file browser, or just ask Jenny to add something to it.

Only the section literally named `## Active Tasks` is read — anything you write under a different heading, or outside any heading, is ignored. The file ships with a comment reminding you of this and to delete tasks once they're done rather than leaving them checked off.

Every **30 minutes** by default (`gateway.heartbeat.intervalS`, default `1800` seconds), Jenny reads that section. If it's empty (only headers, blank lines, or HTML comments), the cycle is skipped entirely before any model call happens — so an empty Heartbeat costs you nothing. If there's at least one task line, Jenny runs a real turn to check on it, then passes its own draft answer through a second, silent LLM judgment step that decides whether it's actually worth telling you about. That judgment defaults to staying quiet on any error or uncertainty (fail-closed) — so "I set a task and never hear anything" is a real, expected outcome if nothing noteworthy ever comes up, not a bug. When Heartbeat does decide to speak, the message is delivered proactively to **both** the WebUI chat and, if you've paired a Telegram bot, that chat too — it's not confined to whichever channel you're currently looking at.

The practical rule of thumb: **write tasks under `## Active Tasks`, and delete them once they're done.** Every cycle where that section has content triggers one real LLM call — a forgotten task left in the file keeps costing tokens every 30 minutes indefinitely, even if Heartbeat never finds anything worth reporting.

Example of something reasonable to put there: "Check the weather forecast around 7am and warn me if it looks like rain."

## `/goal` and long-running tasks

For work that should span many turns — a multi-step research task, an ongoing project, a "keep an eye on this and get back to me" ask — use `/goal <description>` instead of a plain message.

```
/goal Research flight options from Milan to Tokyo for the first two weeks of September and put together a comparison table in the workspace.
```

What changes once a goal is active:

- The objective is pinned into the model's context every single turn (up to 4000 characters), so it survives conversation compaction and idle-timeout summarization instead of quietly falling out of view.
- The usual LLM wall-clock timeout is **disabled** for the whole session while the goal is active, so a turn is allowed to run much longer than normal without being cut off mid-work.
- Only **one** goal can be active per chat at a time. If you try `/goal` again while one is already running, Jenny replies telling you to `/stop` first.
- A goal that sits inactive for **12 hours** expires on its own at the start of the next turn (`JENNY_GOAL_INACTIVITY_TTL_H`, default 12 — see [Environment variables](../reference/environment-variables.md)).
- `/stop` cancels the active goal outright.
- Jenny is expected to close the loop herself by calling her own "mark goal complete" step with an honest recap — whether the goal succeeded, was cancelled, or was redirected. There's no separate "goal progress" view: while a turn is running (goal-related or not) you'll see the same "Agent running" banner with a timer at the bottom of the chat, which survives a page reload if the turn is still in flight.

`/goal` doesn't spin up a separate orchestrator or a hidden process — it's still the same chat, using the same tools, just with the objective kept in view and the timeout removed. If Android kills the app mid-goal, the goal's state lives in the session, so it either resumes on your next message or quietly expires after the 12-hour inactivity window, same as above.

## Subagents (`spawn`)

When Jenny decides a task is complex or slow enough to run independently, she can delegate it to a subagent — a second, short-lived agent instance working in the background — rather than asking you to wait.

- You'll see this happen as a short confirmation in chat, something like *"Subagent [research] started (id: xxxxxxxx). I'll notify you when it completes."* — after that the chat is free for you to keep talking about anything else.
- When the subagent finishes, its result is fed back in as a fresh turn of the main conversation: Jenny reads the outcome and summarizes it for you naturally (the announcement is explicitly told not to mention "subagent" or task IDs in the final reply, so it may just read like an ordinary answer).
- By default only **1** subagent can run at a time (`agents.defaults.maxConcurrentSubagents`, default `1`). Asking for a second one while the first is still running gets a plain "concurrency limit reached" reply instead of being queued.
- A subagent is **blind to your conversation** — it only knows what task text it was handed. If a delegated task comes back disappointing, the usual cause is that the task description didn't carry enough context, not that the subagent "misunderstood."
- Subagents use the same model/provider as your main agent and consume tokens like a full turn — delegating is not free.
- `/goal` refuses to start while a subagent is still active on the session.
- `/stop` disowns any subagents running for that chat: their in-flight work is abandoned, and if one finishes anyway after being disowned, its stale result is silently discarded rather than injected into the chat.
- If the app process dies while a subagent is working, that work is gone — subagent state lives only in memory, not on disk.

## Android notifications

None of the proactive messages above are guaranteed to make a sound — whether a notification fires depends entirely on whether the app is in the foreground at the moment the message is delivered:

- **App in the foreground:** no notification at all — you're already looking at the message as it streams in.
- **App in the background or closed:** a system notification is posted on a dedicated channel named **"Jenny · avvisi"** (the channel name is fixed by the app and not translated). Reminders show a title like `Jenny ⏰ <job name>`, Heartbeat shows `Jenny · monitoraggio`, and anything else uses a plain `Jenny` title. The notification body is the message text collapsed to a single line and capped at **200 characters** (cut off with an ellipsis beyond that).
- Notifications on this channel use high importance (sound and vibration by default), and you can customize or silence that from Android's own per-app notification settings — there is no volume/sound toggle inside Jenny itself. This channel is separate from the silent, persistent "Jenny ✦ online" notification the foreground service keeps up at all times.
- Two notifications from the same source (e.g. the same reminder firing twice in a row) replace each other rather than stacking — you'll only ever see the latest one for that job.
- Tapping a notification opens the app; simply opening the app (bringing it to the foreground) clears any pending Jenny alerts, read or not.
- **The chat message is always there regardless.** Whether or not a notification actually rang, the reply from a reminder, Heartbeat, or a completed subagent is written into the chat exactly the same way — the notification is only ever an added ping layered on top of a delivery that already happened.
- On Android 13 and newer, posting notifications requires the runtime `POST_NOTIFICATIONS` permission, which the app requests automatically the first time it starts. If you deny it, everything above still happens in chat — you simply never get the ringing/vibrating notification for it. <!-- TODO: verify on-device (O-4): confirm exactly when/how the POST_NOTIFICATIONS prompt appears on a real Android 13+ device and what the app does if it's denied and later revisited. -->

## Related pages

- [Memory, Dream and Atlas](memory.md) — what the `dream` and `atlas` system jobs (visible in your reminders list) actually do.
- [Telegram bridge](telegram.md) — how proactive deliveries (Heartbeat, reminders) reach a paired Telegram chat.
- [Slash commands](slash-commands.md) — full reference for `/goal`, `/stop`, and the rest.
- [Android permissions](../reference/android-permissions.md) — the full permission table, including `POST_NOTIFICATIONS` and battery-optimization exemption.
- [Configuration reference](../reference/configuration.md) — `gateway.heartbeat.*`, `agents.defaults.timezone`, `agents.defaults.maxConcurrentSubagents`, and related keys.
- [Environment variables](../reference/environment-variables.md) — `JENNY_GOAL_INACTIVITY_TTL_H` and other `JENNY_*` knobs.
