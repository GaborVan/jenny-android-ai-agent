# Privacy

There is no Jenny backend and no telemetry: everything Jenny sends off the device is a direct, traceable consequence of something you asked it to do, going to a service you configured.

## No telemetry, no Jenny-operated backend

Jenny does not phone home. There is no analytics SDK, no crash reporter, and no Jenny-operated server anywhere in the stack — the codebase has been checked for common telemetry/crash-reporting libraries (Firebase, Crashlytics, Sentry, generic "analytics" SDKs) and none are present. The one thing in the code that uses the word "telemetry" internally is the token-usage counter, and that counter is purely local bookkeeping (visible in Settings → System → Token usage): it is never transmitted anywhere.

The WebUI itself is served entirely from `127.0.0.1` — no page, font, or script it loads comes from the internet.

## The four data recipients

Data only leaves the phone through one of these four paths, each gated by a condition you control.

| Recipient | What it receives | Condition |
|---|---|---|
| **Your configured LLM provider** | Chat messages, session history, tool results, and the content of any file the agent reads from the workspace. **Also your device's last-known location, on every single turn**, if location sharing is on and the Android permission is granted — not only when you explicitly ask "where am I". | Always, for any turn — this is the provider you added in onboarding/Settings. Location is additionally gated by `tools.location.enable` (default `true`) **and** the Android location permission. |
| **Bing** | Your `web_search` queries. | Only when the agent actually calls `web_search` (`tools.androidWeb.search.enable`, default `true`). The search engine is currently fixed to Bing — there's no picker. |
| **Sites visited by `web_fetch` / `download_file`** | Whatever a normal browser visit to that site would reveal: the site sees the request coming from a real, hidden Android WebView, with the phone's own IP address, user-agent, and WebView cookies — not an anonymized fetch. | Only when the agent calls `web_fetch` or `download_file` on a URL. |
| **`api.telegram.org`** | Messages, if you've paired a Telegram bot: your conversation transits Telegram's servers under Telegram's terms, not Jenny's. | Only if `telegram.enabled` is `true` (default `false` — off until you explicitly connect a bot). |

A fifth, smaller case: if your configured provider is OpenRouter, Jenny adds fixed attribution headers to every request (`HTTP-Referer` pointing at Jenny's GitHub repo, `X-OpenRouter-Title: Jenny`) so OpenRouter can attribute traffic to the app. This doesn't add a new recipient — OpenRouter is already your chosen LLM provider — but it does add identifying metadata to that traffic.

## What stays local

Everything else lives in the app's private storage (`<filesDir>/workspace` and nearby), inaccessible to other apps and never transmitted:

- `config.json` — including provider API keys, stored in plaintext (see the caveat below).
- Chat history and consolidated long-term memory (`memory/history.jsonl`, `MEMORY.md`, `USER.md`).
- Uploaded attachments (`workspace/uploads/`) and agent downloads (`workspace/downloads/`).
- Media (images, previews) and wiki content.
- Workspace snapshots (the local "time machine" backups — see [Backup and restore](../using/backup.md)).
- Token usage counts.

## The `allowBackup` exception

Android's manifest declares `android:allowBackup="true"` with no exclusion rules. In practice this means Google's automatic cloud backup for this app **can** include app data — potentially `config.json`, with your provider API keys in plaintext, and your chat history — as part of a normal Android device backup to Google's servers. This is the one real exception to "everything stays local": it's not something Jenny does deliberately, it's a consequence of a manifest flag not yet paired with backup exclusion rules.

<!-- TODO: verify on-device (O-9): confirm exactly what Google's auto-backup captures under allowBackup=true on targetSdk 34 (quota, whether config.json is actually included, device-to-device transfer behavior) -->

Until this is tightened, if you care about your API keys not potentially ending up in a Google Account backup, check your device's backup settings for this app, or disable Android's app data backup for Jenny specifically.

## The workspace-to-provider chain

It's worth stating this relationship plainly, because it's easy to underestimate: **any file placed inside the workspace is readable by the agent, and anything the agent reads can end up in the context sent to your LLM provider.** There's no separate "private files" area inside the workspace — the workspace boundary (see [Security model](./security-model.md)) controls what the agent can reach on disk, not what it's willing to send upstream once it has read something. If you drop a sensitive document into `workspace/`, expect that its content can travel to whichever provider you've configured, the moment the agent has a reason to read it.

## How to shrink the surface

None of the four recipients above are mandatory except your LLM provider (Jenny can't function without one). To reduce what leaves the device:

- **Turn off location sharing** in Settings if you don't want your last-known location included in every turn sent to the provider.
- **Don't enable Telegram** unless you actually want a second channel — it's off by default, and enabling it means your conversation also flows through Telegram's servers.
- **Pick your LLM provider deliberately.** Since messages, history, and file contents all go to whichever provider you configure, your provider's own privacy policy and data-retention practice matters as much as anything Jenny does.
- Avoid putting anything you wouldn't want reaching your LLM provider inside `workspace/`, given the chain described above.

## Related pages

- [Security model](./security-model.md) — the containment layers and the honest can/cannot lists.
- [Location](../using/location.md) — the location toggle and Android permission.
- [Telegram bridge](../using/telegram.md) — what Telegram adds and when it's active.
- [Configuration reference](../reference/configuration.md) — `tools.location`, `telegram.enabled`, and related keys.
