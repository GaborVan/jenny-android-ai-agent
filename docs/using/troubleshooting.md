# Troubleshooting

Symptom-first fixes for the app, not the Python package — nothing here needs a terminal, `adb`, or a desktop.

## First: ask Jenny to check her own logs

Before anything else, try asking Jenny directly: *"Check your recent logs and tell me what went wrong."* She can call a built-in diagnostics tool that reads the last lines of the gateway's own runtime log — this is often the fastest way to find out *why* a tool or a provider call failed, especially since Android normally hides this log inside `adb logcat`, which most users can't reach.

Keep in mind:

- The log is an in-memory ring buffer of the last **500 lines** (DEBUG level and up). It is **cleared every time the app restarts** — if the app has been killed and relaunched since the problem happened, that evidence is already gone.
- You can ask Jenny to filter by a keyword (e.g. "check the logs for `android_web`") and how many lines to show (up to 200 at a time).
- Log lines can contain URLs visited and file names — worth keeping in mind before you paste a log excerpt somewhere or share a screenshot.

## The status dot is gray / "WebSocket not connected. Waiting for reconnection..."

The dot next to the "✿ Jenny" line at the top of the chat reflects the WebSocket connection between the WebUI and the gateway running on your phone — it says nothing about your internet connection.

If you see it turn gray/offline, or you try to send a message and get:

```text
WebSocket not connected. Waiting for reconnection...
```

there's usually nothing to actively fix: the WebUI retries forever, with a backoff starting at 3 seconds and capped at 30 seconds between attempts, and reconnects immediately as soon as the app comes back to the foreground. In practice:

1. Just wait a few seconds — it typically reconnects on its own.
2. If it doesn't, closing and reopening the app (or switching away and back) forces an immediate reconnect attempt.
3. If it never reconnects, the gateway itself may have crashed or failed to start — force-stop the app from Android settings and reopen it; if the problem persists, check the logs as described above (once you can reach a working session) or reinstall.

## Chat looks fine but nothing happens after onboarding

If the app loads, the input works, but sending a message never produces a reply (or immediately errors), the most common cause is a provider problem: go to **Settings → Model → API keys** and confirm a provider is actually configured with a valid key.

Exact errors you might see appended after "Error: " in the chat, and what they mean:

| Error | Meaning |
|---|---|
| `No provider configured. Add a provider in Settings or edit workspace/config.json to set providers.providers[0].` | Onboarding was interrupted before "Start", or the provider list was later emptied. Add one in Settings → Model → API keys → Add provider. |
| `Provider '<name>': api_key is required.` | A provider entry exists but its API key field is empty. Edit it in Settings and paste the key again. |
| `401` / Unauthorized | The API key is wrong, expired, or was pasted with extra whitespace. Regenerate it on the provider's dashboard and update it in Settings. |
| `429` / rate limit | You've hit the provider's rate limit. Wait and retry, or switch to a different model in Settings → Model. |
| `404` / model not found | The model ID doesn't exist for that provider — a display name was used instead of the API model ID, or the model was deprecated. Pick a different one from the catalog in Settings → Model → Change model. |
| Connection refused | Only relevant if you pointed the provider at a self-hosted endpoint (Ollama, LM Studio, vLLM) — the server isn't reachable from the phone, or it isn't HTTPS (cleartext HTTP is only allowed to `127.0.0.1`). See [Local models](../reference/local-models.md). |

Changing the model or provider in Settings applies immediately — there's no restart required to try again.

<!-- TODO: verify on-device (O-10): does the location toggle in Settings show a consistent state when the Android permission is denied? -->

## An attachment silently doesn't show up, or you see "Error: image_rejected"

Two different failure modes here, both currently silent or unhelpful:

- If a file you tried to attach simply **never appears** in the composer preview, it was rejected client-side for being over a limit (more than 4 images, more than 4 other files, or over the per-file size cap) — there's no toast or error, it's just dropped. Try attaching fewer files, or smaller ones, one batch at a time.
- If you do send an attachment and the server rejects it, the chat currently shows the raw, non-localized error `Error: image_rejected` rather than an explained message — this means one of the same limits was hit (too many images/files/videos, a file too large, or a file the server couldn't decode). See [Files and attachments](./attachments.md) for the exact limits.

## Reminders and periodic checks aren't firing

If you asked Jenny to remind you about something and nothing arrived, this is almost always about the app being killed, not a bug in the reminder itself:

- **Reminders only fire while the app (and its background service) is alive.** If Android kills the app before a one-time reminder's scheduled time, that reminder is lost **permanently and silently** — there is currently no catch-up or notification that it was missed.
- Recurring reminders (e.g. "every 2 hours") don't catch up either: the interval restarts counting from whenever the app was last (re)started, not from the original schedule.
- The fix is to exempt Jenny from battery optimization, so Android is less likely to kill the background service: **Settings → Telegram → Exempt from battery** (yes, the button lives in the Telegram section even though it helps all background scheduling, not just Telegram — it's there because Telegram's long-polling is what needs it most).
- The built-in periodic "heartbeat" check (which reads `workspace/HEARTBEAT.md` every 30 minutes) has the same limitation: if the app was killed when a cycle was due, that cycle is simply skipped, not queued up.

See [Scheduling and proactivity](./scheduling.md) for the full model.

## Telegram bot isn't responding

Telegram only works while your phone (running the app) is alive — the phone *is* the server, there's no cloud component. Check, in order:

1. **Is the app actually running?** If Android killed it, or the screen has been off long enough for aggressive Doze to suspend background work, the bot goes silent. Reopening the app resumes polling immediately. The Telegram section in Settings has an "Exempt from battery" button for exactly this.
2. **Did you burn your 5 pairing attempts?** Pairing a new bot requires sending a 6-digit code, and it's capped at 5 attempts per chat as an anti-brute-force measure — this cap also applies to you if you mistype the code repeatedly. Once it's hit, that chat can no longer pair even with the *correct* code. The fix is to go back to Settings → Telegram and either unpair or save a new token, which resets the counter.
3. **Is the bot actually paired?** "Disable" keeps the token but turns the channel off; "Unpair" clears the pairing and generates a new code. Both are visible in Settings → Telegram.

Messages sent while the app was closed are not lost outright — they queue up on Telegram's side and get processed in order once the app is back, though very old backlogs may eventually fall out of Telegram's own retention window.

## Web search shows a CAPTCHA / verification page

`web_search` runs through a hidden Chrome WebView using Bing (it's the only supported search engine), which occasionally shows a CAPTCHA or "verify you're human" page instead of results. When that happens Jenny reports it plainly rather than working around it — there's no bypass. Just try again after a bit, or ask a differently-worded question so the underlying request looks less automated.

<!-- TODO: verify on-device (O-7): how often does the hidden WebView actually hit Bing's CAPTCHA page in normal use? -->

## "URL blocked" errors

If a tool refuses a URL with a message like "URL blocked" or "blocked address", that's the SSRF (server-side request forgery) protection working as intended: by default, Jenny's network tools (`web_fetch`, `download_file`, and `python_exec`'s HTTP helpers) refuse to reach private, loopback, link-local, or carrier-grade-NAT addresses — even ones on your own home network or a VPN like Tailscale.

If you deliberately want the agent to reach something on your own private network (e.g. a self-hosted Ollama box, a home NAS), you can add its address range to `security.ssrfWhitelist` in `config.json` (for example `100.64.0.0/10` for Tailscale). This has to be edited in the config file directly — there is no UI control for it. See [Configuration reference](../reference/configuration.md) and [Security model](../internals/security-model.md).

**Don't do this for an SSH host.** SSH targets are checked against their own, looser policy that already allows private LAN ranges (RFC1918), IPv6 ULA, and the CGNAT range `100.64.0.0/10` that Tailscale uses — no whitelist entry is needed, and adding one would widen `web_fetch`, `download_file` and Jenny Apps to the same range for no benefit. If an SSH host is rejected as blocked, the address is one of the ones blocked in *every* policy: loopback, link-local, or `0.0.0.0/8` — all of which resolve to the phone itself. See [SSH access](./ssh.md).

Note that this whitelist only affects the agent's *tools*. It does not affect calls to your configured LLM provider itself — those never pass through the SSRF filter at all, so a self-hosted provider endpoint on a private network is not blocked by this setting either way.

## Wiki tab shows an error (503)

If opening the Wiki tab returns a `503` error rather than the wiki interface, the wiki feature has been disabled in configuration (`wiki.enabled: false` in `config.json`). Re-enable it there and restart the app.

<!-- TODO: verify on-device (O-12): with wiki.enabled=false, does the WebUI hide the Wiki tab entirely, or show it and return this 503? -->

## Collecting information before asking for help

If none of the above resolves it, gather this before reporting the problem:

- What Jenny told you after you asked her to check her logs (see the first section above).
- The app version, shown in **Settings → System**.
- Your Android version and device model.
- A description of what you did right before the problem appeared, and whether it happens every time.
- Never share your API key — if you need to show a config snippet, redact it first.

## See also

- [Chat basics](./chat.md) for what the online/offline indicator and error banners look like in context.
- [Scheduling and proactivity](./scheduling.md) for the full reminders/heartbeat model and its limits.
- [Telegram bridge](./telegram.md) for pairing, throttling, and what does and doesn't work over Telegram.
- [Files and attachments](./attachments.md) for exact attachment limits.
- [SSH access](./ssh.md) for the separate network policy SSH hosts are checked against, and why a restore doesn't restore access.
- [Security model](../internals/security-model.md) for the SSRF whitelist and the workspace policy boundary.
- [Providers and models](../reference/providers.md) for provider setup and connection errors in more detail.
