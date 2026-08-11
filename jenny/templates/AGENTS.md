# Agent Instructions

## Workspace Guidance

Use this file for project-specific preferences, recurring workflow conventions, and instructions you want the agent to remember for this workspace. Keep durable facts about the user in `USER.md`, personality/style guidance in `SOUL.md`, and long-term memory in `memory/MEMORY.md`.

## Scheduled Reminders

- Before scheduling reminders, check available skills and follow skill guidance first.
- Use the built-in `cron` tool to create/list/remove jobs.
- Get USER_ID and CHANNEL from the current session (e.g., `default` and `websocket` from `web:default`).
- Pick the `mode` deliberately:
  - `reminder` (default) — the user wants to hear from it every time it fires. Runs in this conversation and always replies.
  - `monitor` — a recurring check that should only speak when it finds something ("only tell me if…", "let me know when it changes", "check every 10 min if it's back up"). Runs in its own session, keeps its earlier checks so it can report changes rather than repeat itself, and its reply is suppressed unless you call the `message` tool. Requires `every_seconds` or `cron_expr`; never valid with `at`.
- In a `monitor` run, silence is a correct outcome: call `message` only when the finding is worth interrupting the user for. Every run still costs a full turn, so prefer the loosest interval that works.

**Do NOT just write reminders to MEMORY.md** — that won't trigger actual notifications.

## Heartbeat Tasks

`HEARTBEAT.md` is checked periodically by the protected heartbeat cron job that the gateway registers when `gateway.heartbeat.enabled` is true. Do not create a duplicate heartbeat job unless the user has disabled the built-in one and explicitly wants a custom schedule.

- Use `apply_patch` for normal task-list updates, especially when adding, removing, or changing multiple lines.
- Use `edit_file` only for small exact replacements copied from the current `HEARTBEAT.md`.
- Use `write_file` for first creation or intentional full-file rewrites.

A heartbeat run is **silent** by the same contract as a `cron` monitor: its answer
is not delivered anywhere, and the only way to reach the user is the `message`
tool. So a conditional check written as a heartbeat line must state the condition
("…and warn me only if humidity is below 15%"), and an uneventful run must produce
no message at all — never "All clear." or "nothing to report".

Choosing where a recurring request belongs:

- Ambient task on the shared list, fine on the standard heartbeat beat → add a line to `HEARTBEAT.md` (not a one-time reminder, and not a duplicate heartbeat job).
- Must reach the user every time, or needs its own schedule → `cron` with `mode='reminder'`.
- Needs its own schedule *and* should only speak when something is worth reporting → `cron` with `mode='monitor'`.

`HEARTBEAT.md` has a single interval for the whole file; when the user asks for a different cadence for one specific check, that is a `cron` job, not a heartbeat line.
