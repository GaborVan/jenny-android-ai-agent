# Common Gotchas

## Do not use `ruff format`

**Do not run `ruff format`** — it destroys git blame history. Only `ruff check` should be used (CONTRIBUTING.md says the same).

## Config `${VAR}` References

`config/loader.py` resolves `${VAR}` patterns in `config.json` at load time. This is **not** a shell-like default-value syntax. If the environment variable is missing, `load_config` raises `ValueError` and the agent falls back to default configuration.

Example valid usage:
```json
{ "providers": { "openrouter": { "apiKey": "${OPENROUTER_KEY}" } } }
```

## Android-only runtime

Android is the only supported runtime target. There is no shell, no pip, and no CLI. Use `python_exec` for all code execution. Do not introduce shell execution tools or desktop-only workflows.

## Prompt Templates

Agent system prompts and scenario-specific instructions live in `jenny/templates/` as Jinja2 markdown files (`identity.md`, `platform_policy.md`, `HEARTBEAT.md`, `SOUL.md`, etc.). Changing these files alters agent behavior as directly as changing Python code. They are loaded by `utils/prompt_templates.py`.

Tool descriptions, skills, and replayed session history also shape model behavior. Treat changes to those surfaces like runtime code: keep them narrow, add a focused regression test when possible, and avoid teaching the model to repeat internal markers, local paths, or tool-call text.

## Context Pollution Persists

Anything written into memory, session history, or prompt inputs can be replayed into future LLM calls. Metadata such as timestamps, local media paths, tool-call echoes, and raw fallback dumps must be bounded and sanitized before they become examples for the model to imitate.

## Skills as Extension Point

Built-in skills live in `jenny/skills/` (markdown + YAML frontmatter format). Agent capabilities that are "know-how" rather than code should be added as skills, not hardcoded into the agent loop. External skills can be published to and installed from ClawHub.

## Atomic writes: one helper, `utils/path.py::atomic_write`

Any write that replaces a **whole file of state Jenny reads back herself** — cursors,
skills, wiki entries, manifests, snapshot blobs, config — goes through
`jenny/utils/path.py::atomic_write` (unique temp file + fsync + rename + tolerant directory
fsync). Android kills processes freely, so a plain `write_text`/`open(..., "w")` leaves a
*visible* truncated file: a skill whose frontmatter no longer parses, a cursor that reads
back as 0, an unreadable manifest.

Two things that keep going wrong here, both already fixed once:

- **Do not hand-roll temp-file + `os.replace`.** Five places had done it, each subtly
  different and every one of them missing the `fsync` — atomic against a killed process,
  but not against power loss. If you need different behaviour, add a keyword argument to
  the helper; do not write a sixth copy.
- **Orphan temps are hidden, not absent.** A process killed mid-write leaves
  `name.ext.<hex>.tmp` behind for good; `*.tmp` is in `_DEFAULT_INTERNAL_PATTERNS`
  (`webui/workspace_files.py`) so the file browser does not offer it next to the real file.

The exception is deliberate: the agent's own file tools (`tools/filesystem.py`,
`apply_patch.py`, `python_exec_builtins.py`) write the *user's* files, where the write is
the requested effect and replacing the inode would change semantics (`apply_patch` keeps
`newline=""`, permissions and hardlinks must survive). Appends (`history.jsonl`,
transcripts, app collections, the cron action log) are a different failure mode — a partial
trailing line, which every reader already skips — and are not atomic-write candidates.

## Android WebView search/fetch

`jenny/agent/tools/android_web.py` implements `web_search`/`web_fetch` via Chaquopy calling the Kotlin `AgenticSearchBridge` (`android/app/src/main/java/com/flagdizero/jenny/AgenticSearchBridge.kt`), which drives a real hidden WebView to bypass bot detection.

- **Threading**: the Kotlin bridge call is blocking (`CountDownLatch`), so `_bridge_search`/`_bridge_fetch` run it via `asyncio.to_thread` wrapped in `asyncio.wait_for(timeout + 10)`. The extra 10s is an asyncio-level backstop independent of the Kotlin-side timeout, so a stuck WebView can never block the gateway loop.
- **Remote debugging**: `WebView.setWebContentsDebuggingEnabled(true)` is already enabled unconditionally in `AgenticSearchBridge`'s companion `init`. Connect the emulator/device via adb and open `chrome://inspect/#devices` in desktop Chrome to inspect the hidden WebView.
- **Timeout config**: default is 30s, configurable via `workspace/config.json` under `androidWeb.search.timeout`. Note `AndroidWebFetchTool` reuses this same `search.timeout` — there is no separate fetch timeout.
- **CAPTCHA/bot-block detection**: `_looks_like_captcha()` matches known Bing/Google/DuckDuckGo block-page markers and raises a clear error instead of returning garbage. The search engine is hardcoded to Bing (`search_engine != "bing"` raises `ValueError`); adding another engine requires new JS selectors in the Kotlin bridge.
- **Debug commands**:
  ```bash
  adb shell pidof com.flagdizero.jenny
  adb logcat -d --pid=$(adb shell pidof com.flagdizero.jenny) \
    | grep -iE "AgenticSearchBridge|_bridge|searchBing|fetchUrl|timeout|error"
  ```

## Static asset manifests (templates / skills / UI)

`sync_workspace_templates` extracts bundled files into the workspace using **hardcoded
manifests** in `jenny/utils/android_assets.py` (`_TEMPLATES_MANIFEST`, `_SKILLS_MANIFEST`,
`_UI_MANIFEST`). A new file under `jenny/templates/`, `jenny/skills/` or
`jenny/templates/ui/` that is not listed there **silently never reaches the device** —
and for UI assets the SPA fallback in `_serve_static` masks the failure by returning
`index.html` with a 200 for the missing path. When adding bundled files, add them to the
matching manifest and verify by checking the extracted file's *content*, not the HTTP status.

## Native JS dialogs do not work in the app WebView

`confirm()`, `prompt()` and `alert()` **never appear** in Jenny's WebView and resolve as
if the user had dismissed them — `confirm()` returns `false`, `prompt()` returns `null`.
The `WebChromeClient` in `MainActivity.loadWebView` only implements `onShowFileChooser`,
and nothing handles the JS-dialog callbacks.

The failure mode is the worst kind: the guarded action simply never runs. No dialog, no
request, no error, no log. Three features shipped broken this way — deleting a provider
from Settings, renaming a workspace entry, creating a file or folder — plus four error
messages that went nowhere. Note it works fine in a desktop browser pointed at the
gateway, so it survives any testing that is not done on the device.

Use the helpers in `jenny/templates/ui/assets/shared/dialog.js` — `confirmDialog()` and
`promptDialog()`, both `async`, with their markup already in `index.html` — and
`showToast(msg, 'error')` for failures. Grep before adding a new one:

```bash
grep -rn --include="*.js" -E "(^|[^.[:alnum:]_])(confirm|alert|prompt)\(" \
  jenny/templates/ui/assets/ | grep -vE "confirmDialog|promptDialog"
```
