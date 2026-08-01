# Security model

Jenny's containment is four independent layers stacked on top of each other, and the outermost one — the Android app sandbox — is the only one that is a real, OS-enforced boundary; the other three are application-level guards that a sufficiently motivated adversary (or a misbehaving model) can defeat from inside the app's own process.

## The four levels

### Level 1 — Android app sandbox (the real fence)

This is the only level enforced by the operating system, not by Jenny's own code. Jenny runs as an ordinary Android app under its own UID, in its own private storage (`<filesDir>/workspace`), with no shared storage permission and no way to touch another app's data.

Concretely, this means the agent:

- **cannot** read or write any other app's files or data,
- **cannot** access the camera directly (photo capture is delegated to the system camera app via an intent; Jenny never holds the `CAMERA` permission),
- **cannot** read contacts, SMS, or call logs (no permission is ever requested for them),
- **cannot** access shared/external storage except through user-initiated actions (`share`, `save to Downloads`) that go through Android's own share sheet or Storage Access Framework.

Location and notifications *are* requested at runtime (`ACCESS_FINE_LOCATION`/`ACCESS_COARSE_LOCATION`, `POST_NOTIFICATIONS`), but both are optional, user-facing toggles — see [Location](../using/location.md).

Everything below this line is Jenny's own code running *inside* that sandbox. None of it can widen the sandbox; it can only narrow what the agent is allowed to do within it.

### Level 2 — Workspace policy (fail-closed)

File tools (`read_file`, `write_file`, `edit_file`, `list_dir`, `find_files`, `grep`, `apply_patch`) are constrained to the workspace directory when `security.restrictToWorkspace` is `true` (the default). The enforcement point is `resolve_allowed_path()` in `jenny/security/workspace_policy.py`, and it is deliberately **fail-closed**: if a caller doesn't pass an explicit allowed root or file allowlist, the path is rejected outright rather than defaulting to permissive. Symlinks are resolved before the containment check, so a symlink pointing outside the workspace does not bypass the boundary.

When a path falls outside the boundary, the tool raises an error whose message says, verbatim:

> *(this is a hard policy boundary, not a transient failure; do not retry with alternative tools, and ask the user how to proceed if the resource is genuinely required)*

That phrasing is intentional — it's written to stop the model from treating a workspace-boundary rejection as a fluke worth retrying with a different tool.

One deliberate exception: Jenny's own Python source (`jenny/`) is exposed **read-only** to the agent when `tools.file.exposePackageSource` is `true` (default), so it can inspect the framework it runs on. It is never writable through this path.

Note the framing: this is an **application-level guard**, not a sandbox. If `restrict_to_workspace` is turned off, the agent is *not* granted access to the rest of the phone — Android's own sandbox (Level 1) still applies — but it loses the extra safety net that keeps it inside `workspace/` even when it shouldn't need to leave it.

### Level 3 — Network / SSRF filter

Outbound network tools (`web_fetch`, `download_file`, and the `http_get`/`http_post` helpers available inside `python_exec`) validate every target address before connecting. Requests to private, loopback, link-local, and carrier-grade-NAT ranges are blocked by default:

| Blocked range | Why |
|---|---|
| `0.0.0.0/8` | unspecified |
| `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | RFC1918 private |
| `100.64.0.0/10` | carrier-grade NAT |
| `127.0.0.0/8`, `::1/128` | loopback |
| `169.254.0.0/16`, `fe80::/10` | link-local (includes cloud metadata endpoints) |
| `fc00::/7` | IPv6 unique local |

`security.ssrfWhitelist` (default `[]`) lists CIDR ranges that are exempted from this block — the documented use case is a Tailscale range like `100.64.0.0/10` so the agent's tools can reach a self-hosted service over your own VPN.

**This filter does not cover calls to your configured LLM provider.** Provider requests (the actual chat completions) go out through the HTTP client used by the provider integration, not through the tool-layer SSRF check. If you point a provider's `apiBase` at a LAN or VPN address, that call is not subject to the SSRF whitelist at all — reachability and the HTTPS-outside-localhost constraint (below) are what actually gate it. See [Local models](../reference/local-models.md).

Jenny Apps get a separate, slightly looser blocklist for their own outbound `http` actions (private/RFC1918 ranges are allowed there, since app servers are expected to be LAN devices the user pointed the app at — loopback, link-local, and CGNAT stay blocked so an app manifest can't use the proxy as a back door into the gateway's own API).

### Level 4 — `python_exec` guardrails (explicitly not a sandbox)

`python_exec` runs arbitrary Python **in-process**, on the same Chaquopy interpreter as the rest of the gateway. The source file itself states this bluntly, and this documentation states it just as bluntly: **it is not a security sandbox.**

The allow/block module lists (`os`, `sys`, `pathlib`, `shutil`, and a few dozen others are allowed; `subprocess`, `ctypes`, `socket`, `pty`, and similar are blocked) are a *usability guardrail* — they stop the agent from casually reaching for `subprocess` — not a containment control. With `os`/`sys`/`shutil` allowed, there is no in-process boundary that resists a motivated adversary: guarded code can still reach arbitrary modules through `sys.modules` or `os` internals. `httpx` and `urllib` are deliberately excluded from the allowlist specifically so that raw HTTP clients can't bypass the SSRF and workspace policies — outbound HTTP from `python_exec` is only available through the `http_get`/`http_post` helpers, which do go through `validate_url_target()`.

What actually contains `python_exec`, in order, is:

1. the Android app sandbox (Level 1) — the code runs as this app's UID, nothing more,
2. the workspace path policy (Level 2), to the extent guarded code goes through Jenny's own file helpers,
3. the SSRF policy (Level 3), to the extent guarded code goes through Jenny's own HTTP helpers.

If code inside `python_exec` calls `os` or `shutil` directly, it can do anything the app's own UID can do on disk — which in practice is still confined to the app's private storage, because that's all the UID has access to.

Defaults: `tools.pythonExec.enable` = `true`, `timeout` = 60 seconds (`0` = no limit), `maxOutputChars` = 10,000.

**If you don't trust the model to run arbitrary code responsibly, the only real mitigation is to turn the tool off:** set `tools.pythonExec.enable` to `false` in `workspace/config.json`. There is no "sandboxed mode" to fall back to.

## What the agent can and cannot do on the phone

**Can:**
- Read, write, and edit files inside `workspace/` (and read Jenny's own source, read-only).
- Download files from the web into `workspace/downloads/`.
- Search the web and fetch/read pages (through the hidden WebView — see [Tool reference](../reference/tools.md)).
- Send you notifications and schedule reminders (`cron`).
- Read your last-known device location, or request a fresh GPS fix, if the location toggle and the Android permission are both granted.
- Send and receive messages over Telegram, if you've paired a bot.
- Run Python code in-process (unless you disable `python_exec`).
- Spawn subagents and run long-running background tasks.

**Cannot:**
- Read or write any other app's data — the Android sandbox stops this regardless of any Jenny-level toggle.
- Use the camera directly, or read contacts, SMS, or call logs — these permissions are never requested.
- Reach private/loopback/link-local network addresses with its web tools, unless you've added them to `security.ssrfWhitelist`.
- Meaningfully resist a compromised or adversarial model once inside `python_exec` — that boundary is the Android sandbox, not Jenny's own guardrails.
- Escape the workspace boundary through file tools when `security.restrictToWorkspace` is `true` — the fail-closed policy rejects paths outside it rather than silently widening scope.

## Signed media URLs

Media previews served by the WebUI (images, attachments) use URLs of the form `/api/media/<signature>/<path>`, where `<signature>` is an HMAC-SHA256 digest (truncated to 16 bytes) over the path, keyed by a per-install secret. This means another app on the phone can't guess a valid media URL for a file it doesn't already have a link to.

Two things worth knowing:

- **The signature never expires.** Responses are served with `Cache-Control: private, max-age=31536000, immutable` — a year. Anyone who obtains a signed URL (through a screenshot, a shared link, a proxy log) can reuse it for as long as the underlying file exists and the signing secret hasn't changed. There's no revocation or expiry mechanism.
- Path traversal outside the media root returns a plain 404, and an invalid signature returns 401 — the check is namespaced to the media directory, so a valid signature for one file can't be replayed against an arbitrary path.

## WebUI authentication

Every WebUI API call and the WebSocket handshake require a per-install secret (`websocket.tokenIssueSecret`, generated once at first boot and stored in `config.json` with `chmod 600`). It's checked as an `Authorization: Bearer <secret>` or `X-Jenny-Auth: <secret>` header on HTTP requests, and as a `?token=` query parameter on the WebSocket handshake.

Android hands this secret to the WebView as a **URL fragment** (`#bs=<secret>`), never as a query parameter — fragments aren't sent to the server and aren't logged the way query strings can be. The WebView's JavaScript reads the fragment locally and exchanges it once, over `/webui/bootstrap`, for the actual WebSocket URL.

The gateway listens on `127.0.0.1:18790` by default (WebSocket and HTTP share the same host/port). If you were to reconfigure `websocket.host` to `0.0.0.0` (all interfaces) without setting `tokenIssueSecret`, the config itself refuses to validate — this is rejected before the gateway can even start, specifically to prevent an unauthenticated gateway from being exposed to the rest of your network.

## Related pages

- [Privacy](./privacy.md) — what leaves the device and when.
- [Configuration reference](../reference/configuration.md) — the `security.*` and `tools.*` keys in full.
- [Tool reference](../reference/tools.md) — per-tool limits and behavior.
- [Local models](../reference/local-models.md) — the HTTPS-outside-localhost constraint for self-hosted providers.
