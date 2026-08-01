# Providers and models

How Jenny talks to an LLM: the two wire formats it speaks, how to add or change a provider, and what actually happens (and doesn't) when you do.

Jenny does not ship with any model. It is bring-your-own-key: you point it at an LLM endpoint you already have access to, and every request — and every dollar or token it costs — goes through your own account.

## There is no built-in catalog

Jenny does not maintain a list of "supported providers." A provider is just an entry you define yourself: a name you pick, a wire format, a credential, and an endpoint. Jenny never infers anything from the model name, the shape of the API key, or the base URL — whatever you configure is exactly what runs. This is why pasting the right API key against the wrong base URL, or the right base URL with a model ID from a different service, is the single most common first-run failure (see [Quick checks](#quick-checks)).

## The two formats

Every provider entry declares one `format`, and that field alone decides which backend code path handles the request:

| Format | Speaks | Default base URL | Typical services |
|---|---|---|---|
| `anthropic` | Claude via the Anthropic Messages API | `https://api.anthropic.com` | Anthropic direct, or any Anthropic-Messages-compatible proxy |
| `openai_compat` | OpenAI Chat Completions (and optionally the Responses API) | `https://api.openai.com/v1` | OpenAI, Groq, DeepSeek, Ollama, vLLM, OpenRouter, Together, Fireworks, and any other Chat-Completions-shaped endpoint |

These are the exact hints shown in the onboarding wizard's "Choose your provider format" step: **Anthropic Compatible** — "Claude models via Anthropic Messages API" — and **OpenAI Compatible** — "OpenAI, Groq, DeepSeek, Ollama, vLLM, OpenRouter, Together, Fireworks...". The same two choices reappear in Settings → API keys whenever you add or edit a provider.

Both default base URLs apply only when you leave the base URL field empty. Change it whenever the service isn't the default one — Groq, DeepSeek, OpenRouter, Together, Fireworks, or anything self-hosted all need their own `apiBase` under the `openai_compat` format.

## Managing providers

Providers live under Settings → **Model** → **API keys** (the same screen the onboarding wizard's "Connect your provider" step writes to). Each entry shown there has:

- **Name** — a free-form label (e.g. "My Claude"), not a service identifier. It's what other config, like model presets, refers to.
- **Format** — the `anthropic` / `openai_compat` choice above.
- **API Key** — shown masked, as a 4+4 character hint (first four and last four characters) once one is configured, never displayed in full again.
- **Base URL** — shown as `(default)` when left empty.

Actions available from this screen: **Add provider**, **Edit**, **Delete**. A few things worth knowing:

- Saving shows **"Provider saved"**; deleting asks **`Delete provider "{name}"?`** and then confirms **"Provider deleted"**.
- You cannot delete the last remaining provider (**"Cannot delete the last provider"**) — Jenny always needs at least one configured to keep the agent runnable.
- Both **Name** and **API Key** are required to save (**"Name and API Key are required"**).
- Changing which provider is active, or editing a provider's key or base URL, **hot-reloads immediately** — no app restart, no "requires restart" prompt. The gateway rebuilds the provider backend in place the next time it notices the config changed, and swaps it into the running agent. If you've read an older doc (or `providers.md`'s previous revision) claiming a provider switch needs a restart, that claim is false as of the current code.
- `apiType`, and the advanced `extraHeaders` / `extraBody` / `extraQuery` fields described below, are **not exposed in Settings at all** — they only exist if you hand-edit `workspace/config.json`. Hand-editing `config.json` directly *does* require restarting the app for the change to take effect; only Settings changes hot-reload.

## Provider fields (`config.json`)

The full field set, only reachable by hand-editing `providers.providers[]` in `config.json`:

| Field | Required | Description |
|---|---|---|
| `name` | yes | Free-form identifier, referenced by `providers.default` and by any `modelPresets.<preset>.provider`. |
| `format` | yes | `"anthropic"` or `"openai_compat"`. The only field that picks the backend. |
| `apiKey` | yes | The gateway refuses to start a provider without one — see the exact error below. Local servers that don't check keys still need a placeholder like `"EMPTY"`. |
| `apiBase` | no | Full HTTP base URL, version path included where the service expects it (e.g. `/v1`). Omit to use the format's default. |
| `apiType` | no, `openai_compat` only | `"auto"` (default), `"chat_completions"`, or `"responses"`. See [Chat Completions vs. Responses API](#chat-completions-vs-responses-api-openai_compat-only). Config-only — not in Settings. |
| `extraHeaders` / `extraBody` / `extraQuery` | no | Extra request headers, body fields, and query params merged into every request to this provider. Config-only. |

Keys may be written as camelCase or snake_case in the file; Jenny always writes camelCase back when it saves.

## Choosing the active provider

Jenny picks the active provider in this order:

1. the entry whose `name` matches `providers.default`, if set;
2. otherwise the first entry in the `providers.providers` list;
3. otherwise startup / the next request fails with **"No provider configured"**.

## API keys are stored in plaintext

Every `apiKey` value sits in `workspace/config.json` as plain text. The only thing standing between that file and the rest of the world is Android's normal per-app sandbox — there is no separate encryption, keychain, or OS credential store involved.

This matters for one specific reason: the app's manifest sets `allowBackup="true"` with no backup-exclusion rules. Android's automatic cloud backup (Google's built-in backup service) can therefore scoop up app data — including `config.json`, and with it every API key in plaintext — into the user's Google account backup. If you use Google's device backup, treat it as a place your API keys can end up, not just your device. <!-- TODO: verify on-device (O-9): what the auto-backup actually captures under targetSdk 34 without explicit dataExtractionRules --> There's a separate, deliberate encrypted backup for disaster recovery — see [Backup and restore](../using/backup.md) — but that's a different mechanism, and it does not change what `allowBackup` exposes to Google's own backup pipeline.

If you export an unencrypted copy of `config.json` yourself (e.g. by pulling it off the device for debugging), you are exporting your API keys in the clear; handle that file accordingly.

## Chat Completions vs. Responses API (`openai_compat` only)

`apiType: "auto"` (the default) mostly means "use Chat Completions." Jenny only considers the OpenAI Responses API at all when *both* of these are true:

- the base URL points directly at `api.openai.com` (not OpenRouter, not any other gateway), **and**
- the request either sets a `reasoningEffort` value, or the model is one of OpenAI's reasoning families (o1/o3/o4, or any `gpt-5*` model).

When both hold, Jenny tries the Responses API — and if it starts failing, a small circuit breaker kicks in: after **3 consecutive failures** for that model, it stops trying Responses and falls back to Chat Completions for **5 minutes** before probing Responses again (a single "half-open" retry). This is per-model, automatic, and not configurable beyond forcing `apiType` to `"chat_completions"` or `"responses"` explicitly if you want to skip the auto-detection entirely.

For every other endpoint — Groq, DeepSeek, Ollama, OpenRouter, a self-hosted server, anything that isn't `api.openai.com` directly — `auto` always means Chat Completions; the Responses API is never attempted.

## Prompt caching: what's actually happening

Be precise about this, because it differs a lot by format:

- **`anthropic` format**: cache-control markers are always applied to every request. This is unconditional — no setting to flip.
- **`openai_compat` format**: Jenny only emits explicit `cache_control` markers when **both** the base URL is OpenRouter **and** the model name looks like a Claude/Anthropic model (contains "claude" or an `anthropic/` prefix). Every other `openai_compat` endpoint — OpenAI direct, Groq, DeepSeek, a self-hosted server, OpenRouter with a non-Claude model — gets no explicit cache markers from Jenny at all. Whatever caching happens there, if any, is entirely up to that provider's own server-side behavior; Jenny doesn't request or control it.

In short: don't expect Jenny-driven prompt caching outside of Anthropic-format requests and the OpenRouter-Claude combination specifically.

## OpenRouter attribution headers

When the configured base URL contains `openrouter` (case-insensitive), Jenny automatically attaches attribution headers to every request: an `HTTP-Referer` pointing at the project's GitHub repository and an `X-OpenRouter-Title` of "Jenny" (plus a categories header). This is fixed behavior tied to detecting an OpenRouter base URL — there's no setting to suppress it, and it has no effect on non-OpenRouter endpoints.

## Model IDs must match the endpoint exactly

Jenny sends whatever string you put in the model field straight to the provider. There's no translation or aliasing layer. The most common way a working provider config produces "model not found" is pointing a preset or the onboarding model field at a model ID that belongs to a different service than the one `apiBase`/`apiKey` are configured for — e.g. an OpenRouter-style `anthropic/claude-...` slug sent to a direct Anthropic endpoint, which expects a plain `claude-...` name (or vice versa).

## Quick checks

| Symptom | Likely cause |
|---|---|
| `Provider '<name>': api_key is required.` | The active provider entry has no `apiKey`. Local/self-hosted servers that ignore auth still need a placeholder value. |
| `No provider configured. Add a provider in Settings or edit workspace/config.json...` | `providers.providers` is empty. Add one from Settings → Model → API keys, or by hand-editing `config.json`. |
| 401 / unauthorized | The key is missing, expired, has stray whitespace, or belongs to a different service than the configured base URL. |
| Model not found | The model ID doesn't exist on the endpoint you configured — check it's the exact ID that endpoint serves, not a name copied from a different provider's docs. |
| Connection refused | A local/self-hosted server isn't running, or the base URL has the wrong host, port, or path. See [Local models](./local-models.md) if the endpoint is off-device. |
| Could not fetch models | Settings' model picker probes the endpoint's model list and failed; this doesn't block saving a provider, it just means you'll need to type the model ID manually. |

The Settings model picker's probe (`GET <apiBase>/models`) is advisory only — a failed probe never blocks you from saving a provider or typing a model ID by hand, and a successful one never changes anything in your config beyond what you explicitly choose.

## See also

- [Local models](./local-models.md) — self-hosted endpoints (Ollama, vLLM, LM Studio) reachable from the phone.
- [Configuration](./configuration.md) — full `config.json` reference, including model presets and agent defaults.
- [Settings](./settings.md) — the Settings UI tour, including the Model section and Advanced parameters.
- [First run](../start/first-run.md) — the onboarding wizard that writes your first provider entry.
- [Privacy](../internals/privacy.md) — what leaves the device and when.
