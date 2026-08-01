# First run

The first time you open Jenny after installing the APK, you go through a slow boot and then a short setup wizard — this page walks through exactly what happens and what you'll see.

## The first boot is slow, and that's normal

Jenny bundles its own Python 3.11 runtime (via Chaquopy) inside the APK — there's nothing else to install, but on the very first launch that runtime has to be unpacked onto the device before the app can do anything. You'll see a loading screen while this happens. The app polls the local gateway for up to **90 seconds** before giving up, so if the loading screen sits there for a while, it's usually still working, not frozen.

<!-- TODO: verify on-device (O-1): real elapsed time for first-boot extraction on a reference device -->

Subsequent launches are fast: the Python runtime is already extracted, and the app only has to start the gateway process.

If the 90-second timeout is reached and the app never connects, see [Troubleshooting](../using/troubleshooting.md).

## What gets created on first launch

Before you see the setup wizard, the app has already:

- Created a workspace folder inside the app's private storage (`<filesDir>/workspace` — not visible to file managers, and not touched by other apps on the phone).
- Written a minimal `config.json` into that workspace, containing a random **per-install secret** (`websocket.token_issue_secret`) used to authenticate the WebUI to the local gateway. This secret never leaves the device: the native app reads it from `config.json` and passes it to the embedded WebView as a URL *fragment* (`#bs=...`), never as a query parameter, so it can't end up in logs or server request lines.
- Extracted the built-in prompt templates, skills, and UI assets from the APK into the workspace.
- Started the gateway itself — **without any LLM provider configured**. The gateway is alive and the WebUI can load, but there's no agent yet: that's exactly the gap the Setup wizard fills in.

Nothing about this step requires action from you; it's what happens between tapping the app icon and seeing the wizard.

## The Setup wizard

Because no provider is configured yet, the app shows a 4-step wizard (labeled **Setup** in the navigation) and blocks the rest of the UI until it's done. A row of progress dots at the top of each step shows how far you are.

<!-- TODO: screenshots (O-1) -->

### Step 1 — Choose your provider format

Two cards:

| Card | Covers |
|---|---|
| **OpenAI Compatible** | OpenAI, Groq, DeepSeek, Ollama, vLLM, OpenRouter, Together, Fireworks... |
| **Anthropic Compatible** | Claude models via Anthropic Messages API |

Pick whichever matches the service you have an API key for. Below the two cards there's also a **Restore from backup** button — see [Restoring from a backup instead](#restoring-from-a-backup-instead) below.

<!-- TODO: screenshots (O-1) -->

### Step 2 — Connect your provider

Three fields:

- **Provider name** — a free-form label to identify this provider (e.g. "My Claude"). Required.
- **API Key** — required. This is the only thing that authenticates you to the LLM provider; it is stored in `config.json` on the device and is never sent anywhere except to that provider's API.
- **Base URL** — optional. If you leave it empty, Jenny uses the format's default: `https://api.openai.com/v1` for OpenAI Compatible, `https://api.anthropic.com` for Anthropic Compatible. Change this if you're pointing at OpenRouter, Groq, DeepSeek, a self-hosted Ollama/vLLM instance, or any other compatible endpoint — see [Providers and models](../reference/providers.md) and [Local models](../reference/local-models.md) for details.

The Next button only enables once both the provider name and the API key are non-empty.

<!-- TODO: screenshots (O-1) -->

### Step 3 — Choose a model

This step tries to fetch the live list of models straight from the provider's `/models` endpoint (a 10-second timeout applies) so you can pick from what's actually available rather than typing an ID from memory.

- If the fetch succeeds, you get a searchable list — type in the **Search models** box to filter it.
- If the fetch fails, or the provider doesn't expose a `/models` endpoint, an **Or type a custom model name** field appears instead so you can enter the model ID by hand.
- This step also has an **Assistant name** field for what you want Jenny to call herself (default "Jenny").

Once a model is selected (from the list or typed manually), the **Launch** button enables.

<!-- TODO: screenshots (O-1) -->

### Step 4 — Connect Telegram (optional)

After Launch succeeds, a final, skippable step offers to pair a Telegram bot so you can talk to Jenny from Telegram as well as the WebUI. Tap **Skip for now** to finish onboarding without it — you can pair Telegram later from Settings at any time. See [Telegram bridge](../using/telegram.md) for the full pairing flow.

<!-- TODO: screenshots (O-1) -->

## What "Launch" actually does

Pressing **Launch** does the following, in order:

1. **Validates the provider before saving anything.** The backend tries to actually construct a provider from what you entered. If that fails (bad format, missing key, provider rejects the request), you get an error toast on the spot — e.g. `Provider configuration is invalid: ...` — and **nothing is written to config.json**. You stay on the same step and can fix the fields and try again.
2. Only if validation succeeds: writes `providers.providers` (replacing whatever provider list previously existed — see the gotcha below), `providers.default`, `agents.defaults.model`, `agents.defaults.bot_name`, and the current UI language to `agents.defaults.language`.
3. Adds a localized welcome message to the chat — in English: *"Hi, I'm {assistant name} and from today I live on your smartphone. Nice to meet you!"*
4. Signals the gateway to create and start the agent **immediately, with no app restart**. The gateway was already running (just without an agent); it picks up the new configuration and becomes usable right away.

## Interrupting the wizard

- **Before pressing Launch on step 3**: nothing has been persisted yet. If you close the app or back out, reopening it lands you back at step 1 — you start over from scratch.
- **After Launch, during the optional Telegram step**: the provider, model, and assistant name are already saved and the agent is running. If you close the app here, the next launch goes straight to the normal chat view (Telegram can still be paired later from Settings).

## Restoring from a backup instead

If you've used Jenny before and have an encrypted `.jbk` backup file, tap **Restore from backup** on step 1 instead of going through the wizard. This opens the same import flow used from Settings: pick the file via the Android system picker, enter the backup passphrase, and confirm. The restore is staged, not applied immediately — the app then prompts you to restart, and the actual restore happens at that restart, before anything else touches the workspace. Once restored, the app boots straight into chat with your old provider, history, and memory already in place — the wizard is skipped because a provider is already configured. See [Backup and restore](../using/backup.md) for the full mechanics.

<!-- TODO: verify on-device (O-2): full restore-from-onboarding flow, including SAF picker behavior with Google Drive -->

## Things to know that aren't obvious

- **Onboarding replaces the entire provider list.** If you ever run through the Setup wizard again on a device that already had providers configured (this shouldn't normally happen after first run, but can matter if you're scripting or debugging), it discards the old provider list and keeps only the one you just entered. Add further providers afterward from Settings → Model → API keys.
- **Model-fetch error messages come from the backend in English**, regardless of which UI language you're using — for example "The provider rejected the configured credential." or "Configure an API base URL to load models." They aren't translated.
- **An empty model list is not an error.** If the provider's `/models` endpoint returns zero models (wrong key, wrong base URL, or the provider just doesn't have any), the backend still responds with HTTP 200 and an explanatory message; the wizard shows that message instead of an error banner, and reveals the manual model-ID field.

## What's next

Once onboarding is done, take the [Tour of the WebUI](../using/webui-tour.md) or jump straight into [Chat basics](../using/chat.md). If you want Jenny available from your home screen, see [Set it as your launcher](launcher-setup.md).
