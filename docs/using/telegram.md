# Telegram bridge

Jenny can also live in Telegram as a personal bot, so you can talk to it from any device without opening the WebUI — but it's a narrower window onto the same conversation, not a second assistant.

## What this actually is

The Telegram channel is a **single-user bot**: your Jenny bot pairs with exactly one Telegram chat. It is not a way to give other people access to your agent, and it is not a separate conversation — it shares the same session as the WebUI (see [Shared session](#shared-session-new-resets-both-sides) below). Your phone has to stay on and Jenny's app has to be running for the bot to answer anything; there is no cloud component.

## Setting it up

You'll find this under **Settings → Telegram**, and optionally as a step during first-run setup ("Connect Telegram (optional)").

1. Open **@BotFather** on Telegram and send `/newbot`.
2. Pick a name and a username for the bot.
3. Paste the token BotFather gives you into the **Bot token** field in Jenny.

Press **Connect**. Jenny validates the token immediately against Telegram's `getMe` API before saving anything:

- Wrong or malformed token → `telegram token rejected: <reason from Telegram>`, nothing is saved.
- Phone offline / Telegram unreachable → `cannot reach Telegram: <error type>` (HTTP 502), nothing is saved.
- Success → the channel is enabled, the bot's command menu is registered (`/start` "Quick guide", `/new` "New conversation" — best-effort; if this sub-step fails, pairing still proceeds), and a 6-digit pairing code is generated.

Jenny never shows a saved token in full again — only a masked hint (first 4 and last 4 characters, e.g. `1234...abcd`). Keep the real token somewhere safe (e.g. re-request it from @BotFather) in case you need it again.

## Pairing your chat

Once the token is saved, Jenny shows a 6-digit code and the line "Send this code to your bot @yourbot on Telegram to pair it:", plus a link to your bot. The WebUI polls for pairing status every 2.5 seconds.

Open the chat with your bot and send the code — either as a plain message, or as the payload of a `/start` deep link (i.e. following a `t.me/yourbot?start=123456` link). A bare `/start` with no code gets the reply "To pair, send me the 6-digit code shown in Jenny's WebUI."; a wrong code gets "Invalid code. Check the 6-digit code in Jenny's WebUI and try again."

On success the bot replies "✅ Paired! You can now talk to Jenny from this chat." followed by its welcome text, the WebUI shows "Paired with @username" (and a toast "Telegram paired!" if you did this during onboarding), and the code is cleared.

The pairing code is written to config, so **it survives an app restart** — you don't lose your place mid-setup. It is regenerated whenever you save a new token or unpair (see below).

## The 5-attempt limit — read this before you start guessing

To stop a stranger from brute-forcing the 6-digit code, Jenny caps pairing attempts at **5 per chat** (and tracks at most 512 chats at once; beyond that, further chats are ignored outright). This cap applies to *any* message sent during the pairing window that isn't the exact code — including a bare `/start`, which also counts as an attempt.

This protection has a sharp edge: **it applies to you too.** If you mistype the code five times, that chat becomes permanently ineligible to pair — even if you then send the *correct* code, the bot goes completely silent (no error message, nothing). The only way out is to make Jenny regenerate the channel: go to Settings → Telegram and either unpair or save a new token. Both actions issue a fresh pairing code and reset the attempt counter.

The attempt counter is kept **in memory only** — it also resets whenever the Jenny app itself restarts, so a restart is a (side-effect) way out too, but don't count on it as a fix.

## Unlink vs. Disable

Two different buttons appear once you're paired, and they behave differently:

| Action | What it does | To reconnect |
|---|---|---|
| **Unpair** | Clears the paired chat and generates a fresh pairing code. Keeps the channel enabled. | Send the new code to the bot again — no need to touch the token. |
| **Disable** | Stops the channel entirely (no more polling, the bot won't reply to anything) but keeps the token and the pairing on record. | You have to go through **Connect** again and re-enter the full bot token — there is no separate "re-enable" button, and the saved token is only ever shown as a masked hint, so keep the real token somewhere if you plan to re-enable later. |

## Everything hot-reloads

Saving a token, unpairing, and disabling all take effect immediately — the running channel is stopped and, if still enabled, restarted with the new configuration in the background. You never need to restart the app for a Telegram setting to take effect.

## Battery exemption

Telegram delivery uses long polling — the app keeps an open request to Telegram's servers waiting for new messages. Android's Doze mode can throttle or suspend that when the screen is off and the phone isn't charging, which delays replies. If Jenny detects it isn't already exempt from battery optimization, the paired view shows a hint ("For instant replies even when the phone is not charging, exempt Jenny from battery optimization.") and an **Exempt from battery** button that opens the Android system prompt directly.
<!-- TODO: verify on-device (O-5): actual delay under real Doze without the exemption -->

## What works from Telegram vs. the WebUI

Telegram is a much narrower surface than the WebUI. Be clear-eyed about the gap:

| | Telegram | WebUI |
|---|---|---|
| Text messages in | Yes | Yes |
| Location sharing in | Yes (see [below](#sharing-your-location-from-telegram)) | N/A (uses phone GPS automatically) |
| Photos / voice notes / documents / stickers / video in | **No** — any of these gets the reply "📎 Photos, voice notes and documents are coming soon: text only for now." | Yes |
| Live streaming of the reply | **No** — only the finished message | Yes |
| Tool-use / progress indicators | **No** | Yes (expandable tool pills) |
| "Show reasoning" block | **No** | Yes, model-dependent |
| Typing indicator while Jenny works | **No** — the bot gives no signal at all until the final message arrives | N/A |

That "coming soon" line is a fixed message baked into the bot's replies today, not a release date or a promise — treat it as "not supported."

### Commands

- `/start` — from the paired chat, shows the quick-guide welcome text again (does not start an LLM turn).
- `/new` — starts a new conversation. **This affects the WebUI too** — see [below](#shared-session-new-resets-both-sides).
- `/stop` — stops the currently running turn, wherever it was started.

Messages from anyone other than the paired chat are ignored completely and silently — the bot never confirms or denies that it exists to a stranger.

### Outbound messages: final text, then attachments

Because Telegram only ever gets the finished answer, long replies are split into chunks of at most **3500 characters** of raw text (Telegram's own hard limit is 4096; the smaller number leaves room for HTML formatting overhead), preferring to break on paragraph or line boundaries. Supported formatting is a small HTML subset — bold, italic, inline code, code blocks, and links; headings become bold text. Everything else degrades to plain characters. If Telegram rejects the formatted chunk, Jenny automatically retries as plain text.

Any files Jenny attaches (via the `message` tool, or file responses) follow the text:

- Raster images (`.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`) are sent as photos with a native preview.
- Everything else (PDFs, SVGs, etc.) is sent as a document.
- **Files over 10 MB are silently skipped** — logged on the device but nothing is shown to you in the chat.
- If Telegram rejects an image as a photo, Jenny automatically retries it as a plain document rather than losing it.

## Canonical view: WebUI sees everything, Telegram doesn't

The WebUI is the single source of truth for the conversation. Every message you send from Telegram, and every reply, is mirrored live into the WebUI's chat view with a small **Telegram** badge — so opening the app later shows the full history regardless of where you actually chatted.

The reverse is **not** true: messages you send from the WebUI are never delivered to Telegram. Telegram only ever receives replies to its own messages, plus proactive messages (reminders, heartbeat notifications). Service replies from the bot itself — pairing codes, the welcome text, the "coming soon" media message — never appear in the WebUI either; they aren't part of the conversation.

## Shared session: `/new` resets both sides

There is only one conversation — Jenny doesn't keep a separate memory or context per channel. WebUI and Telegram are two windows onto the exact same session. This has one consequence you should know before setting up Telegram:

**Sending `/new` from *either* channel resets the conversation for *both*.** If someone has access to your paired Telegram chat, they can wipe the conversation you've been having on your phone in the WebUI (the prior history is archived, not deleted, but the active context is gone). Keep that in mind before pairing a bot you might share access to.

## Sharing your location from Telegram

Sending a location or venue in the Telegram chat (paperclip → Location) is handled specially: it's recorded as a **Telegram-only override** valid for `tools.location.telegramTtlS` (default 3600 seconds / 1 hour, minimum 60), and it immediately triggers an LLM turn so Jenny can react with your shared position already in context. For a venue, Jenny prefers the place's name and address as the location label. Within the TTL, all Telegram replies use that shared position; once it expires (or from the WebUI at any time), Jenny falls back to the phone's live GPS.

This override is **in-memory** — it's lost if the app restarts, even before the hour is up.

If the location toggle (`tools.location.enable`, default on) is switched off, sharing a location from Telegram is **ignored entirely**, and — this is the one genuinely misleading part of the whole feature — the bot replies with the same "coming soon" media message used for unsupported photos and documents, which has nothing to do with location being off. If your location share from Telegram seems to vanish, check the Location toggle in Settings first.

Sending your location also spends a real LLM turn (and therefore tokens) even if you didn't type anything.

## Offline behavior: the phone is the server

There is no server other than your phone. If the Jenny app isn't running (or the phone is off), the bot does not answer — full stop.

When the app comes back, it resumes long polling from scratch (Telegram's internal update offset isn't saved across restarts) and Telegram delivers whatever messages are still queued for that offset. In practice this means messages sent while the app was closed are processed **in sequence, one LLM turn per queued message**, once the app is running again — several backlogged messages will trigger several turns back to back, not one merged conversation. How long Telegram itself holds undelivered updates before giving up is set by Telegram's own Bot API, not by Jenny — commonly cited as roughly 24 hours, but check Telegram's own documentation for the current number; Jenny has no control over it and can't recover a message Telegram has already dropped.

There is no typing indicator: while Jenny is working on a reply, the chat gives you no sign of activity at all — you'll wait in silence and then see the finished message appear.

## See also

- [Chat basics](chat.md) for how the WebUI side of the same conversation behaves.
- [Location](location.md) for the two-tier location model and the privacy implications of leaving it on.
- [Scheduling and proactivity](scheduling.md) for reminders and heartbeat messages that also get delivered to Telegram.
- [Troubleshooting](troubleshooting.md) if the bot has gone quiet and you're not sure why.
