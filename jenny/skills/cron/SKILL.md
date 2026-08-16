---
name: cron
description: Schedule reminders, recurring checks and conditional alerts.
locked: true
user_summary:
  it: "Promemoria, controlli ricorrenti e avvisi condizionati: chiedi a Jenny di ricordarti qualcosa a un'ora precisa, di ripetere un'azione nel tempo, o di avvertirti solo se una certa condizione si verifica."
  en: "Reminders, recurring checks and conditional alerts: ask Jenny to remind you of something at a specific time, to repeat an action over time, or to warn you only when a condition is met."
---

# Cron

Use the `cron` tool to schedule work. Every scheduled job runs a full agent turn.

## Pick the mode first

`mode` is a real parameter of `action="add"`. It decides whether the job is
**allowed to speak on its own**, and it is the single most important choice here —
getting it wrong is what turns a check into chat spam.

| `mode` | Speaks | Session | Use when |
|---|---|---|---|
| `reminder` (default) | Every run, automatically | this conversation | the user wants to hear from it every time it fires |
| `monitor` | **Only** if you call the `message` tool | its own isolated session | the run should stay silent unless it finds something |

`monitor` requires `every_seconds` or `cron_expr`; it is never valid with `at`.

**Any request phrased as a condition is a `monitor`.** Listen for: "only tell me
if…", "warn me when…", "let me know if it goes below…", "check every 10 minutes
whether it's back up". If the user described *when they want to be interrupted*,
they also described when they do **not** — that is `mode="monitor"`.

In a `monitor` run, silence is a **correct, successful outcome**. Do not call
`message` to report that everything is normal, and never send filler like
"All clear.", "All done." or "nothing to report" — the user asked to be left
alone in exactly that case. A monitor keeps its earlier runs in its own session,
so it can report a *change* instead of repeating itself. Each run costs a full
turn: prefer the loosest interval that still answers the need.

## Examples

Fixed reminder — speaks every time:
```
cron(action="add", message="Time to take a break!", every_seconds=1200, mode="reminder")
```

Conditional alert — speaks only when the condition fires:
```
cron(action="add", message="Check the soil humidity of all plants; if any is below 15%, warn me. Otherwise say nothing.", every_seconds=3600, mode="monitor")
```

Recurring check whose result the user always wants:
```
cron(action="add", message="Check GitHub stars and report the count", every_seconds=600, mode="reminder")
```

One-time scheduled task (compute ISO datetime from current time; auto-deletes):
```
cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
```

Timezone-aware schedule:
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver", mode="reminder")
```

List/remove:
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

`action="list"` reports each job's last status. `silenced` there means a monitor
ran fine and had nothing to say — it is a success, not an error.

## cron or HEARTBEAT.md?

- Ambient check, fine on the shared heartbeat beat → add a line to `HEARTBEAT.md`.
- Must reach the user every time, or needs its own schedule → `cron` with `mode="reminder"`.
- Needs its own schedule *and* should only speak when something is worth
  reporting → `cron` with `mode="monitor"`.

`HEARTBEAT.md` has one interval for the whole file: when the user wants a
different cadence for one specific check, that is a `cron` job, not a heartbeat
line. Heartbeat runs are silent by the same contract as a monitor — the only way
they reach the user is the `message` tool — so a conditional check written as a
heartbeat line must say what the condition is, and must not report a normal
result.

Only the lines under `## Active Tasks` are read: a heading inside that section is
kept as context for the lines below it, everything outside it — including HTML
comments anywhere in the file — never reaches the model. A file with no task
lines is skipped without running a turn, so a finished task should be deleted
rather than crossed out or left in place.

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | every_seconds: 1200 |
| every hour | every_seconds: 3600 |
| every day at 8am | cron_expr: "0 8 * * *" |
| weekdays at 5pm | cron_expr: "0 17 * * 1-5" |
| 9am Vancouver time daily | cron_expr: "0 9 * * *", tz: "America/Vancouver" |
| at a specific time | at: ISO datetime string (compute from current time) |

## Timezone

Use `tz` with `cron_expr` to schedule in a specific IANA timezone. Without `tz`, the server's local timezone is used.
