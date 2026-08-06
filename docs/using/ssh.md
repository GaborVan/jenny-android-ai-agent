# SSH access

Jenny can log into machines you own — a home NAS, a VPS, a Raspberry Pi — and run commands there on your behalf. This is the only capability in the whole app that acts on a computer that isn't your phone, so it is off by default and every host has to be declared by hand.

## What this actually is

Three things define the shape of this feature, and it's worth reading them before you turn it on:

- **Jenny can only reach hosts you registered.** The agent doesn't pass an address, a username or a password to anything — it passes an *alias* you already created in Settings. There is no tool that takes "connect to 10.0.0.7 as root". If the alias isn't in your list, the answer is an error, not a connection.
- **Jenny herself has no SSH.** The assistant you talk to delegates remote work to a **`sysadmin` subagent**, which is the only kind of agent that gets the SSH tools — and which has no web access and no local code execution in exchange. Whoever reads untrusted web pages is not whoever holds a shell on your server.
- **The credential never leaves the phone.** With key authentication — the default — Jenny generates an ed25519 key pair on device, shows you the public half to install on the server, and keeps the private half in a directory the agent's own file tools cannot read. Password authentication is also available, is more convenient and is weaker; the differences are spelled out in [Password instead of a key](#password-instead-of-a-key) below.

The SSH client is native (jsch + Bouncy Castle, on the Android side), not a Python library. You don't have to care, except for one visible consequence: SSH works in the Android app and nowhere else.

## Setting it up

Everything lives under **Settings → SSH**, a section of its own between Tools and Telegram.

### 1. Turn it on and add a host

Flip **Enable SSH access**, then **Add host**:

| Field | Notes |
|---|---|
| **Alias** | The only name Jenny uses for this machine (`nas`, `vps`). 1–32 characters, letters/digits/`-`/`_`. **It cannot be changed later** — it's also the name of the key file on disk. |
| **Host** | Hostname or IP (`nas.home.lan`, `192.168.1.10`). |
| **Port** | Default 22. |
| **User** | The account to log in as. |
| **Description** | Free text, and not decoration: it is shown *to the model* so it can pick the right machine when you have several ("the home NAS", "the website VPS"). |
| **Authentication** | **ed25519 key** (default) or **Password**. Key is the default and stays the default for hosts you already registered. With Password selected, a password field appears — it's required, and an empty one is refused rather than saved. |

The address is checked against Jenny's network policy when you save it, and again on every connection. Private LAN ranges are allowed — a home server is the main use case — and so is the carrier-grade-NAT range Tailscale hands out, so a Tailscale hostname works with no extra configuration. Loopback, link-local and cloud-metadata addresses are refused: those point at the phone itself, so refusing them stops the agent from SSHing into its own device or using the tool as a way back into Jenny's own API.

This policy is deliberately wider than the one guarding `web_fetch`, and the difference is who picks the address. There, the model does. Here, you type the host in Settings and accept its fingerprint by hand before a single byte is sent — the same two steps that make key pinning worth anything.

### 2. Generate the key and install it on the server

Tap **Generate key**. Jenny creates an ed25519 pair *for that alias* — each host gets its own key — and shows you the public line. Paste it into `~/.ssh/authorized_keys` of the user you configured:

```
echo 'ssh-ed25519 AAAA... jenny' >> ~/.ssh/authorized_keys
```

The public key is kept so you can read it again later; the private key is never displayed, never sent to the WebUI, and never readable by any of Jenny's tools. **Regenerate key** asks for confirmation, because a new pair revokes the access you already installed on the server.

### Password instead of a key

If you set **Authentication** to **Password**, Jenny logs in with the account password instead of a key pair. The "Generate key" button and the public-key block disappear from that host's card — there is nothing to install on the server — and the card shows whether a password is set rather than whether a key exists.

This is genuinely more convenient: nothing to paste into `authorized_keys`, nothing to install on a machine you can't easily reach, and it works on a server where you can't edit `authorized_keys` at all. It is also weaker, in three concrete ways, and none of them are hypothetical:

- **The password is stored in `config.json`.** That is the same place as your Telegram bot token and your provider API keys — inside the app's private storage, which no other app on the phone can read. It is **not encrypted at rest**, and unlike the SSH private key, `config.json` sits *inside* the workspace, so Jenny's own file tools can read it. The private key deliberately lives outside the workspace precisely so that they can't. A password does not get that protection.
- **It goes into your backups.** `config.json` is part of the workspace, so it is inside snapshots and inside an exported `.jbk` (which is encrypted with your backup passphrase — that passphrase is what protects it there). The private key never travels that way.
- **You can't revoke it in isolation.** A key installed for Jenny is a key you can delete from `authorized_keys` and nothing else changes — you keep logging in the way you always did. The password is the same one you use yourself: taking it away from Jenny means changing it for you too.

If you can install a key, install a key. If you can't, a password is a reasonable trade — just make it a dedicated account with only the rights Jenny actually needs, rather than the one you use for everything.

Switching an existing host from password back to key **erases the stored password**. That's on purpose: a credential nothing uses any more has no business sitting in the file, and you'd reasonably assume that flipping the switch removed it. Switching back to password later means typing it again.

Editing a password host without retyping the password keeps the saved one — the field is always blank when you open the form, because the password is never sent back to the screen it was typed into.

### 3. Verify the host fingerprint

Tap **Verify fingerprint**. Jenny contacts the host without authenticating, reads the key it presents, and shows you the SHA256 fingerprint. Compare it with what the server itself says:

```
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

If they match, tap **Accept**. From that moment Jenny only talks to the machine presenting that exact key.

There is **no trust-on-first-use**. Until a person has accepted a fingerprint, every SSH tool call for that alias fails with an error telling the model to ask you to open Settings — and the model has no way around it. The fingerprint you accept is the one you were shown: if the reading is older than 10 minutes, or the host answers with something different in the meantime, the acceptance is refused and you have to probe again.

This step is mandatory in both authentication modes, and **with a password it matters more, not less** — the dialog says so on the spot. Authenticating with a key to the wrong machine is embarrassing but cheap: the impostor gets a signature it cannot reuse anywhere. Authenticating with a password hands it your password, in full, on the first command. The fingerprint is what decides who receives it, which is why there is no way to skip it.

### If the fingerprint changes later

An already-accepted host that starts presenting a different key does **not** get updated silently. Jenny shows both fingerprints side by side, says plainly that this is what a man-in-the-middle looks like, and requires a second explicit confirmation to replace it. Reinstalling a server does cause this legitimately — but so does an attack, and only you know which one happened.

Editing an existing host's address or port has the same effect from the other direction: the accepted fingerprint is dropped and the `known_hosts` line forgotten, because a verification of the old address says nothing about the new one. You'll have to verify again.

### Restart after enabling

**The SSH tools are built when the gateway starts.** Turning the switch on, or adding your first host, does not hand the tools to the agent until Jenny restarts. Adding a *second* host to an install that already had one working host does take effect immediately — the host list is read live on every call.

Switching SSH **off** is the opposite: it applies instantly, mid-turn, even to a subagent already working on a server. That asymmetry is deliberate — the toggle is meant to work as an emergency stop.

## What you can ask for

Anything you'd type in a terminal, described in words:

- "Check if nginx is still running on the VPS and show me the last errors in the log."
- "The NAS says it's out of space — find out what's eating it."
- "Update the packages on the VPS and tell me when it's done."
- "Fetch `/etc/nginx/sites-enabled/default` from the VPS so we can go through it together."

Jenny hands the job to a `sysadmin` subagent, and while that agent works you can keep talking to her about something else. The subagent's activity is visible live in the UI, you can send it a correction mid-run ("no, restart the container instead of rebuilding it"), and you can cancel it outright.

Ask which machine it worked on if you have more than one — the agent is instructed to name the alias in what it reports back, but the habit is worth checking.

If SSH is switched off, or no host is registered yet, Jenny does not start the subagent at all: she tells you which of the two it is and where to fix it. A `sysadmin` without SSH would only improvise, and the useful answer is one sentence, not one round trip. Turning SSH on, or adding a host, takes effect on the next job — no restart.

## Short commands vs long jobs

This is the distinction that matters most in practice, and Jenny makes it for you — but knowing it explains what you'll see.

**Short commands** (`ssh_exec`) run and return their output right away: exit code, stdout, stderr. They are capped at **60 seconds** by default (300 maximum) and about **10,000 characters** of output. Beyond either cap, the result is truncated or the command times out.

**Long commands** (`ssh_job`) are not waited for at all. Jenny launches them detached from the connection, with output going to a log file on the server (`/tmp/jenny-jobs/<job-id>.log` by default), and then reads that log *incrementally* — each check returns only what's new since the last one. You get a job id back straight away.

The reason is your phone. Jenny's gateway runs as a foreground service **without a wake lock**: with the screen off the CPU can suspend, and a walk from wifi to mobile data kills the TCP connection. An `apt upgrade` waited for over an open SSH channel doesn't fail cleanly — it fails halfway, and nobody knows how far it got. A detached job doesn't care: the server keeps working, the output accumulates safely on the server, and Jenny picks up from the exact byte she'd reached, whether that's thirty seconds or a day later. Jobs survive a dropped connection, a gateway restart, and the app being killed.

What you'll notice:

- A long job gets acknowledged immediately ("started, I'll check on it"), not on completion. Ask "how's the upgrade going?" and Jenny reads the new output.
- Jenny doesn't sit and poll in a loop. If you want to know the moment it finishes, ask her to set a reminder — that's what [`cron`](scheduling.md) is for.
- Stopping a job sends a SIGTERM to the remote process and its children. It's best-effort: a deep process tree or a program that ignores SIGTERM survives it, and the only honest confirmation is checking the job afterwards.
- A job can come back as **lost** rather than finished: the process is gone but never recorded an exit code, which means it was killed — the OOM killer, or the server rebooting. That work is unfinished, and Jenny is told to treat it as such rather than assume success.

## Moving files

`ssh_transfer` copies **one file at a time** in either direction, over SFTP on the same connection. The local side is always inside the workspace — a path outside it is refused — and transfers are capped at **50 MB** by default, checked before anything is written, so you never end up with a truncated download that looks complete.

There is no directory sync and no recursive copy. For a whole tree, ask Jenny to `tar` it up on the server with a job first and then fetch the single archive.

## Limits and surprises

Read this section before you rely on any of it.

### A workspace restore does not restore SSH access

The private key and `known_hosts` live **outside** the workspace, and snapshots and encrypted backups only ever walk the workspace. That's deliberate — a key that could be read by the agent's own file tools, or that travelled inside an exported backup file, would be a much worse problem.

The price is real and you should plan for it: **restoring a `.jbk` backup, or restoring a snapshot, brings back your host list but not the keys.** After a restore (or a phone swap, or a reinstall) each host will show "No key", and for each one you'll need to generate a fresh key, paste the new public line into `authorized_keys` on the server, and remove the old one. The accepted fingerprints are gone with it, so you'll verify each host again too.

This is the one genuinely unpleasant part of the design. Nothing warns you at restore time.

**Password hosts are the exception, and it cuts both ways.** The password lives in `config.json`, which *is* inside the workspace, so a restore brings it back with everything else and that host works again immediately — no reinstalling anything. The other side of the same fact is that your server password travelled inside that backup file. A `.jbk` is encrypted with the passphrase you chose, so that passphrase is what stands between the file and the password; a local snapshot is not encrypted, and sits in the app's private storage like the config it came from.

You will still have to verify the fingerprint again either way: `known_hosts` is outside the workspace and does not come back.

### No interactive shell

Every command runs without a TTY and without stdin. Anything that stops to ask a question doesn't prompt — it hangs until it times out. In practice:

- **`sudo` that asks for a password will not work** — including on a host that authenticates with a password. Registering one is not the same as having something that answers prompts: the command still runs with no TTY and no stdin, so the prompt hangs until the timeout. Configure `NOPASSWD` for the specific commands you want Jenny to run, or log in as a user that already has the rights it needs.
- Package managers and installers need their non-interactive flags (`-y`, `DEBIAN_FRONTEND=noninteractive`, …). Jenny knows to add them, but a tool that hides a prompt in an unusual place will still stall.
- There is no `ssh` session you can attach to, no shell history, no `screen`/`tmux` integration. Each command is independent, and a `cd` in one does not carry into the next.

### The toggle is asymmetric

Turning SSH **off** takes effect immediately. Turning it back **on** requires a gateway restart before the agent has the tools again. If you re-enable SSH and Jenny insists she has no way to reach your server, that's this — not a bug, and not something she can fix from inside the conversation.

The same applies to your very first host: enabling the switch with an empty host list registers nothing.

### The agent sees your hostnames and usernames

`ssh_hosts` lists alias, host, username, port and description to the model — it has to, or it couldn't choose between two machines or tell you which one it touched. Those go to your LLM provider like everything else in the context. Neither the private key nor the password is ever in that list, in a tool argument, or in a tool result.

One honest caveat on top of that: `config.json` lives in the workspace, so Jenny's file tools can *read* the file that holds a password — the same way they can already read your Telegram token and API keys. The SSH private key is the thing that got moved outside the workspace to make that impossible. If that difference matters to you, that's the argument for a key.

### Other edges worth knowing

- **One key per alias**, not one key for all hosts. Deleting a host deletes its key, its public key, its accepted fingerprint and — on a password host — its stored password. An alias recreated later with the same name starts from scratch, which is the point: deleting really revokes. (On a password host, deleting removes Jenny's copy; the password itself still works for you, because it's yours.)
- **Job logs stay on the server.** `/tmp/jenny-jobs` is not cleaned up by Jenny, and on most systems `/tmp` is wiped on reboot — which will make an old job unreadable. The per-host `jobLogDir` can be pointed somewhere durable, but only by editing `config.json` (there is no field for it in Settings).
- **The job registry keeps 100 entries**, pruning only finished ones. Running jobs are never pruned.
- **Output is truncated, not paged.** When a command produces more than the cap, Jenny is told how many characters were dropped and to re-run it narrowed with `grep`/`tail` rather than guess. Expect the occasional second command instead of a wall of text.
- **`idleCloseS` in the config does nothing today.** It is declared and validated but never read by the connection pool.

## See also

- [Tool reference](../reference/tools.md) — the exact behavior and limits of `ssh_hosts`, `ssh_exec`, `ssh_job` and `ssh_transfer`.
- [Settings](../reference/settings.md) and [Configuration](../reference/configuration.md) — every field of the SSH section and the `tools.ssh.*` keys.
- [Security model](../internals/security-model.md) — where SSH sits among Jenny's containment layers.
- [Backup and restore](backup.md) — what a `.jbk` does and does not carry (spoiler: not your SSH key).
