# Recurring Work

Three destinations. Picking the wrong one is what turns a check into chat spam.

- Ambient check, fine on the shared heartbeat beat → add a line to `HEARTBEAT.md`,
  **under its `## Active Tasks` heading**: only that section is read, so a line put
  anywhere else — above it, or under a later `## Notes` — never runs, and nothing says so.
  A protected cron job registered by the gateway already reads that file; never create
  a second heartbeat job — unless the user disabled the built-in one and wants their own
  schedule for it.
- Must reach the user every time, or needs its own schedule → `cron` with `mode='reminder'`.
- Needs its own schedule *and* should speak only when there is something to report →
  `cron` with `mode='monitor'`.

Any request phrased as a condition — "only tell me if…", "warn me when…", "check every
10 minutes whether it's back up" — is a `monitor` or a `HEARTBEAT.md` line, never a plain
reminder.

`HEARTBEAT.md` has one interval for the whole file, and its runs are silent: the only way
they reach the user is the `message` tool. So a conditional check written there must state
its condition in the line itself, and an uneventful run must produce no message at all.

Writing a reminder into `memory/MEMORY.md` schedules nothing. Before scheduling, read
`skills/cron/SKILL.md`: syntax, timezones, examples and `list` semantics live there.
