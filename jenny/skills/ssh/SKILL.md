---
name: ssh
description: Running commands on the user's own remote machines — alias-only targeting, short commands vs detached jobs, delta polling, SFTP transfers, and how to report what you did and where.
locked: true
user_summary:
  it: "Comandi sulle macchine remote che hai registrato in Impostazioni > SSH: Jenny può controllare un servizio, leggere un log, aggiornare un server o scambiare un file. Raggiunge solo gli host che hai dichiarato tu, e i comandi lunghi restano in corso anche a telefono spento."
  en: "Commands on the remote machines you registered in Settings > SSH: Jenny can check a service, read a log, update a server or move a file. She can only reach hosts you declared yourself, and long commands keep running even with the phone asleep."
---

# Remote machines over SSH

You have four tools — `ssh_hosts`, `ssh_exec`, `ssh_job`, `ssh_transfer` — and they act on a computer that is **not** this phone. Nothing you do here is undone by a snapshot: a deleted file on the server is deleted, a stopped service is down for whoever depends on it. Read the two rules below before the rest.

**You cannot choose a target.** Every tool takes `host`, and `host` is an *alias* the user registered by hand. You cannot pass an address, a username, a port or a credential. If you don't know the aliases, call `ssh_hosts` — it reads live config, so an alias the user just added is there without a restart.

**You cannot bypass a refusal.** An unpinned host key, a missing key, a blocked address, SSH switched off: these are decisions that belong to a person, and the error text says which screen to open. Report it to the user and stop. Do not retry, do not try another tool, do not look for a workaround.

## Pick the right tool before you run anything

| Situation | Tool |
|---|---|
| Don't know the aliases, or one was rejected | `ssh_hosts` |
| Inspect, read a config, check a service, restart a container | `ssh_exec` |
| Anything that could take more than a few seconds | `ssh_job` (`action="start"`) |
| Move one file to or from the workspace | `ssh_transfer` |

`ssh_exec` waits for the command on an open connection. This phone's CPU can suspend with the screen off and its network switches between wifi and mobile data, so a command that outlives the timeout doesn't fail cleanly — it dies half-way and nobody can tell how far it got. That is why the timeout is low and why you cannot raise it: `timeout_s` can only lower the configured cap.

Use `ssh_job` for package installs and upgrades, builds, backups, database dumps, large copies, `rsync`, anything with a progress bar, anything you would run in `screen`. When in doubt, start it as a job — a job that finishes in three seconds costs you one extra poll; an `ssh_exec` that times out costs you the whole operation and leaves the server in an unknown state.

## Fire and follow: the job pattern

```
ssh_job(host="vps", action="start", command="apt-get -y upgrade")
  -> job vps-1a2b3c4d (remote pid 4821), logging to /tmp/jenny-jobs/vps-1a2b3c4d.log
ssh_job(host="vps", action="poll", job_id="vps-1a2b3c4d")
  -> running, new output since your last poll
```

`start` launches the command detached from the connection, with output going to a log file on the server. It survives the connection dropping, the gateway restarting and the screen going off. You do **not** have to keep the turn open, and you must not.

`poll` returns only what is new since your previous poll. **Never track a byte offset yourself** — the cursor is kept for you, and it is correct across context compaction, app restarts and days of elapsed time. Just call `poll` again.

How to behave between polls:

1. **Report and hand control back.** After `start`, tell the user the job is running and what you'll do next. Do not poll in a loop hoping to see it finish — you are burning tokens on a process that doesn't know you're watching.
2. **Poll again when there is a reason**: the user asks, the turn comes back to you, or you were told the job matters for the next step.
3. **The one exception**: if a poll says more bytes are already waiting, poll again immediately — that output exists, you're just behind.
4. **Never start a second job that depends on the first while the first is running.** Poll until it is `finished` with exit code 0, *then* start the next one. There is no dependency mechanism.

Read the status, not the output, to decide what happened:

| Status | What it means | What to do |
|---|---|---|
| `running` | Alive | Report progress, come back later |
| `finished` (exit 0) | Done, succeeded | Move on |
| `finished` (exit ≠ 0) | Done, **failed** | Read the tail of the log, report the actual error, do not re-run blindly |
| `stopped` | Someone signalled it | The work is incomplete — say so |
| `lost` | Process gone, no exit code recorded | It was **killed** (out of memory, server reboot). Treat as unfinished and say why it is ambiguous |

A job that seems stuck: poll it. Same output as last time and still `running` means it is genuinely working (a compile, a large copy) — say so rather than escalating. `stop` sends SIGTERM to the process and its children and is **best-effort**: the return value proves nothing, only a subsequent `poll` does. If the job was already gone, say that instead of claiming you killed it.

If you lose a `job_id` — after compaction, or in a later conversation — use `ssh_job(action="list")` for that host. It works even when the host is unreachable, which is exactly when you need it.

## A supervisable deploy

Structure remote work so that every step is verifiable and the failure of one does not leave the machine half-changed:

1. **Look before you touch.** `ssh_exec` to check what is actually there: service state, disk space, current version, whether the path you're about to write exists. State assumptions and verify them; a deploy that assumed the wrong directory is worse than one that didn't start.
2. **Back up what you're about to overwrite**, on the server, with a short command — `cp config.yml config.yml.bak` costs nothing and turns a mistake into an `mv`.
3. **Upload with `ssh_transfer`**, then verify the upload landed (size, checksum) before acting on it.
4. **Run the long part as a job**, and say what you started.
5. **Poll to completion, check the exit code**, and then verify the *effect* independently: the service is up, the port answers, the version string changed. Exit code 0 means the command ended, not that the thing works.
6. **Report what changed, on which host, and what you did not verify.**

If a step fails, stop and report. Do not improvise a repair on a production machine the user cannot see.

## Commands that will hang

There is no TTY and no stdin. A command that waits for input does not prompt — it stalls until it times out. Before running anything, make it non-interactive:

- `apt-get -y`, `DEBIAN_FRONTEND=noninteractive`, `npm --yes`, `--no-input`, `--force-confold`.
- **`sudo` that asks for a password cannot work.** If a command needs a password, don't retry it and don't attempt to feed one — there is nowhere to type it, and you have no credentials. Tell the user that command needs passwordless sudo or a differently privileged account.
- No pagers (`git --no-pager`, `| cat`), no editors, no `top`/`htop` — use `ps`, `free`, `df`.
- Each call is an independent shell: a `cd` does not carry to the next command. Chain within one command, or use absolute paths.

## Output is capped

Both `ssh_exec` and `poll` truncate, and tell you how many characters were dropped. When that happens, do not guess at the missing part and do not re-run the same command hoping for less output: re-run it **narrowed** — `grep` for the error, `tail -n 50`, `head`, `--quiet`, `wc -l` when you only need a count. Prefer commands whose output you actually need; `cat` on a large log is a wasted round trip.

## Transfers

`ssh_transfer` moves **one** file. The local side is always a path inside the workspace, and there is a size cap checked before anything is written. There is no recursive copy: for a directory, `tar` it on the server with a job and transfer the single archive. When downloading logs or dumps, prefer fetching them and reading locally over `cat`-ing them through `ssh_exec`.

## Reporting

**Always name the host you acted on**, by its alias, every time — the user may have several machines and cannot see your tool calls. "Restarted nginx on `vps`" not "restarted nginx".

Also state, briefly: what you changed (not just what you inspected), the exit code when it wasn't 0, anything you started that is still running with its job id, and anything you could not verify. If a command failed, quote the actual error line rather than paraphrasing it — the user is the one who can fix the server, and they need the real message.
