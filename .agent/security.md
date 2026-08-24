# Security Boundaries

The agent operates with significant power (file system, code execution, web). The following guards must not be bypassed when modifying related code.

## Workspace Restriction

Filesystem tools (`read_file`, `write_file`, `edit_file`, `list_dir`, `apply_patch`) resolve paths through the workspace path resolver (`agent/tools/filesystem.py` / `agent/tools/path_utils.py`), which enforces that the resolved path must lie under the active workspace when workspace restriction is enabled. The media upload directory is always an internal extra read root while restricted.

Additional filesystem roots must be capability-specific. `extra_allowed_dirs` is a legacy read-only alias. Use `extra_read_allowed_dirs` for read-only roots, `extra_write_allowed_dirs` only when a write-capable tool is intentionally allowed to modify an extra directory, and exact file allowlists when a tool may modify only specific files.

**Rule**: Any new path-handling logic must go through the workspace path resolver or perform an equivalent containment check with explicit read/write capability semantics.

## The project/diary boundary runs one way only

A project's words cannot reach the personal diary. The gate is a **single funnel**:
`MemoryStore.append_history` returns `0` for a `project:` session key, so both writers on that path
(`Consolidator.archive`'s summary and the `raw_archive` dump when the LLM call fails) stop there
rather than each carrying their own filter. A second key turn covers the read side —
`read_recent_history_for_prompt` returns nothing at all for a project key, which also closes entries
written by an older version or by hand.

**The reverse direction has no structural boundary, and that is deliberate.** `SOUL.md` and
`USER.md` are always composed from the installation root (`ContextBuilder._IDENTITY_FILES`) and
`MEMORY.md` is injected for every session kind, so a project's turn — and every internal pass that
runs on the installation root, the gardener's included — carries the user's personal profile.
`MemoryRecallTool` likewise captures the install-root archive at construction and ignores workspace
scope, so a project session can `recall` the personal archive. The declared line is **who you are
travels; where else you work does not**: what is gated on the session is the *cross-project
inventory* (`memory/WIKI.md`, Atlas's directory of every wiki, person and plant), not the identity.

So "the diary is kept personal" must not be read as symmetric. Personal is not secret-from-a-project.

Two narrowings on that gated half, both keyed on the session and both about actors whose write
surface is a single project — the second one is why the first is not the whole rule:

- a `project:` conversation gets no wiki directory and no `Recent History` block;
- a **gardener** pass (`gardener:` key) gets neither either (T7.8). Its toolbox reads inside one
  project and writes only in `wikis/<name>/wiki/`, and `agent/gardener.md` tells it to work only
  from the journal, the map and the page inventory. Before this, the pass with no user to talk to
  was shown the personal conversation's queue while the project conversation it maintains was shown
  none of it.

**Rule**: when adding an internal actor whose writable surface is one project, gate
`get_wiki_memory_context` and `read_recent_history_for_prompt` on it — do not "fix" the identity
path to match, and do not widen the gardener's read root (its `build_tools` comment, *"Lettura:
dentro il progetto. Non l'intera installazione come Atlas"*, is a boundary somebody chose: T4.5
records a proposed fix that would have silently undone it).

## SSRF Protection

All outbound HTTP requests from agent tools must pass through `validate_url_target` (`security/network.py`). By default it blocks loopback, RFC1918 private addresses, CGNAT ranges, link-local ranges, and cloud metadata endpoints (including `169.254.169.254`).

The only escape hatch is `configure_ssrf_whitelist(cidrs)`, which reads from `config.tools.ssrf_whitelist` at load time.

**Rule**: Do not add direct `httpx.get` / `requests.get` calls in tools. Route through the existing web fetch utilities or replicate the `validate_url_target` check.

### Jenny Apps server SSRF policy (intentionally more permissive)

Jenny App `http` actions use a **distinct, deliberately more permissive** policy, `validate_app_server_target` (`security/network.py`), backed by `_APP_SERVER_BLOCKED_NETWORKS`. Unlike `validate_url_target`, it **allows RFC1918 private ranges** (`10/8`, `172.16/12`, `192.168/16`) and IPv6 ULA (`fc00::/7`) **by design**: an app server is a user-declared LAN device, reachable at a `server.baseUrl` that the user sees and approves in the manifest. Loopback (`127.0.0.0/8`, `::1`), link-local / cloud metadata (`169.254.0.0/16`), `0.0.0.0/8`, and CGNAT (`100.64.0.0/10`) remain blocked — so an app manifest cannot use the proxy as an authenticated bridge to the gateway's own API. Redirects are never followed, so a server cannot bounce the proxy to a blocked address.

As a related safeguard, `server.auth` is **fail-closed**: when an app declares it needs authentication but no credential store exists yet, the action is refused with a 501 rather than being sent unauthenticated (`apps/http.py`).

**Rule**: Keep the two policies separate. Widening the app-server allowlist further (or letting it follow redirects) requires re-evaluating the LAN-device threat model; do not route general agent web fetches through `validate_app_server_target`.

### SSH target policy (a third one, wider still)

`validate_ssh_target` (`security/network.py`), backed by `_SSH_BLOCKED_NETWORKS`, allows RFC1918, IPv6 ULA **and** CGNAT (`100.64.0.0/10`), blocking only `0.0.0.0/8`, loopback and link-local/metadata. CGNAT is allowed here rather than through `configure_ssrf_whitelist` on purpose: the whitelist is global, so opening it for Tailscale would also open CGNAT to `web_fetch` and to Jenny Apps — a narrow permission in one policy beats a wide one across all three. What backs the extra room is that an SSH host is user-typed in Settings and host-key pinned before any connection, not that SSH is inherently safer.

**Rule**: Loopback stays blocked in all three policies — it is the phone itself, and the gateway's own API lives there.

## Telegram pairing oracle

The Telegram bot follows a **no-oracle rule** (`channels/telegram.py`): outside a pairing
window — no `pairing_code` set, or already paired — the bot NEVER replies to non-owner
chats, so it does not reveal that it exists or its pairing state.

**Accepted trade-off** for onboarding: while a `pairing_code` is active (from token save
until a successful pairing), the bot answers service replies (`/start` prompt, wrong-code
feedback) up to `_MAX_PAIR_ATTEMPTS` per chat, with the attempt table bounded fail-closed
at `_MAX_TRACKED_CHATS` (no eviction). A chat at/over the cap — or a new chat when the
table is full — becomes **ineligible to pair even with the correct code**: the cap is a
brute-force defence on the 6-digit code (total guess budget ≈ cap × bound out of 10^6 per
channel lifetime), not just a reply throttle.

Known limits, accepted by design: the pairing window is not time-bounded (a code persists
after unpair until re-paired), and in-memory counters reset on channel reload/gateway
restart with the same persisted code — the reload paths that matter (token save, unpair)
regenerate the code anyway. Owner lockout recovers via WebUI "Unpair"/"Change token".

**Rule**: any new reply on the unpaired path MUST go through the attempt counter and MUST
NOT fire when `pairing_code` is unset. Never reply to non-owner chats once paired.

## Code Execution

`PythonExecTool` (`agent/tools/python_exec.py`) is the current execution surface. It runs arbitrary Python **in-process** on the single Chaquopy interpreter (in the executor threadpool / dedicated session threads) — **not** in a subprocess, and it is **not a security sandbox**. This matches the honest trust-boundary docstring in `python_exec.py`. The real containment comes from three layers outside the interpreter:

- the **Android app sandbox** (the app's own uid / permissions);
- the **workspace path policy** for filesystem writes — now enforced for the builtin `open` / `io.open` / `pathlib` I/O paths too, not just the registered helpers and `os.open` (`security/workspace_policy.py`);
- the **SSRF policy** for outbound network.

The module allow/block lists are a **usability guardrail** (they stop the model from accidentally reaching for e.g. `subprocess`), **not** a containment control. In particular, `httpx` is **no longer in the default allowlist** (`config/tool_schemas.py`): outbound network is available only through the `http_get` / `http_post` builtins, which validate targets via the SSRF policy. Raw `httpx` can be re-added explicitly in config, accepting the risk.

Deployments that do not trust the model must disable the tool via `tools.python_exec.enable = false` — that is the real answer to "no sandbox", not in-process hardening.

### The read-only turn is on the same side of that boundary

The read-only switch (`WorkspaceScope.writable = False`) is enforced inside `python_exec` by `_refuse_write_if_readonly`, and its gate is thread-local: it fires only on a thread the guard entered. Thread hops made through `asyncio` — `asyncio.to_thread`, `loop.run_in_executor` — carry the whole turn across (`_carry_turn_across_thread`, T4.13: both the path boundary *and* the read-only ContextVar). A **raw thread** reached through an allowed module's internals (`asyncio.base_events.threading.Thread`, `asyncio.futures.concurrent.futures.ThreadPoolExecutor`) carries neither, so it bypasses the path boundary **and** the read-only turn alike — measured 23/08/2026, `restrict_to_workspace` on and off: a raw thread's `open(p, 'w')` writes during a read-only turn. This is an accepted limit (closing it means patching `threading.Thread` process-wide); see `TestKnownRemainingDoors` and the trust-boundary comment in `python_exec.py`.

So read-only is an **instruction backed by tool refusals**, not a containment control, and the two halves fail together rather than one at a time. The prompt block the model reads (`templates/agent/readonly.md`) is written to state intent for exactly this reason: a prompt sentence that promised more than the code keeps would be worse than one that states the intent, because the reader is the model.

**Rule**: Do not describe `python_exec` as a sandbox, and do not describe the read-only turn as one either. Do not introduce shell execution or command wrappers. Any new path-handling or network path added to the execution surface must go through the workspace path policy and the SSRF policy, since those layers (not the interpreter) are the containment boundary.
