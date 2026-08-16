# Plan — fixing what the 2026-08-11 device investigation surfaced

Status: **proposed**, nothing implemented yet.
Owner: unassigned.
Written: 2026-08-11.

> Not under `docs/` on purpose: `docs/**` is published by `flagdizero/jenny-site`.
> `roadmap/` is gitignored and local-only, by design.

## The roadmap index

Companion notes for the architectural items. **All of them**, with their real status — this
list used to carry four of seven, and a reader triaging by header was skipping live work.

| File | Status |
| --- | --- |
| [`roadmap/audit-0.8.0-corrections.md`](roadmap/audit-0.8.0-corrections.md) | **The 2026-08-16 re-audit of everything below**, and the plan the 0.8.0 correction pass executed. Start here: it carries corrections C1–C8 to the other six documents, and its *Open decisions* section is the only short list of what is genuinely unanswered |
| [`roadmap/agents-md-ownership.md`](roadmap/agents-md-ownership.md) | **Done** — three stages shipped and validated on the device. The write permission (open question 5) is decided in principle and **blocked** on a budget and a routing rule |
| [`roadmap/memory-budget.md`](roadmap/memory-budget.md) | **Partially done** — gauge and refusal shipped and now enforced by default; day log and promote-by-use deferred. Owns the unanswered `AGENTS.md` budget |
| [`roadmap/user-md-drift.md`](roadmap/user-md-drift.md) | **Done in code**; one open decision (whether refusal on `USER.md` is wanted) |
| [`roadmap/heartbeat-escalation-amnesia.md`](roadmap/heartbeat-escalation-amnesia.md) | **Done**, in two waves, both measured. A delegating cron monitor is reported and unbuilt |
| [`roadmap/workspace-scratch-dir.md`](roadmap/workspace-scratch-dir.md) | **Split**: the scratch directory is superseded, its *original subject* is still live, and two items have no owner |
| [`roadmap/inbound-webhooks.md`](roadmap/inbound-webhooks.md) | **Split**: Phase 1 is not planned, but **Phase 0 is endorsed and unbuilt** and there is a costed one-commit alternative |
| [`roadmap/project-sessions.md`](roadmap/project-sessions.md) | **Proposed**, not started. Work item 7 was rewritten after T5.4 — as originally written it would have opened a real leak |

---

## Context you need if you are reading this cold

On 2026-08-11 the Titan 2 (v0.6.6) was inspected live over adb, and its
`AGENTS.md` / `USER.md` / `memory/MEMORY.md` were repaired through the chat. Repairing them
took five agent turns instead of one, and along the way the gateway **died once**. Every bug
below was found during that session and then **verified against the source**, not inferred
from behaviour.

Two facts about the platform that explain most of the list, and that are easy to forget:

- **The main agent cannot write files.** `gateway.orchestrator_mode` defaults to `True`
  (`jenny/config/schema.py:125`): the primary loop gets read + control tools only, and every
  write, `python_exec` or web call happens inside a spawned subagent. This is deliberate.
- **`python_exec` runs with cwd `/`**, and the `working_dir` parameter that looks like it
  fixes that does nothing (B5). Relative paths passed to `open()` are resolved against the
  *workspace root*, not the cwd and not `working_dir`
  (`jenny/security/workspace_policy.py:131`).

Repo verification command for every phase:

```bash
ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session && pytest -q
```

Local note: use `python3 -m pytest -q` on the dev machine (the package is not installed
editable), and build the Android side with `ANDROID_HOME=$HOME/Library/Android/sdk` +
`app:assembleRelease` (the phone carries a release-signed APK; `installDebug` fails).

---

## Bug index

| # | Bug | Severity | Phase |
|---|---|---|---|
| B1 | `python_exec` lets `BaseException` escape the sandbox → kills the gateway | **critical** | A |
| B2 | The 3-attempt restart loop never runs for that crash | **critical** | A |
| B3 | Dead-gateway detection threshold is 3 hours when only the Python thread dies | **high** | A |
| B4 | Workspace sandbox confines `open()` but not `os.*` namespace operations | **high (security)** | B |
| B5 | `python_exec`'s `working_dir` is accepted and ignored | high | C |
| B6 | Skills have no supported way to own and import their own `.py` files | high | D |
| B7 | `web_fetch` fails on `text/plain` URLs (observed, cause unproven) | medium | D |
| B8 | A monitor's failure is indistinguishable from its success | **high** | E |
| B9 | `llm-wiki/SKILL.md` documents a doubled path and a `python3` shell command that cannot exist | medium | D |
| B10 | `skill-creator/SKILL.md` documents relative script paths that cannot resolve | medium | D |
| B11 | `_load_bootstrap_files` has no template-content guard | low | F |

---

# Phase A — the gateway must survive agent-authored code

Do this first. Everything else can wait; this cannot, because it takes the whole agent down
and the recovery is measured in hours.

## B1 — `BaseException` escapes the `python_exec` sandbox

**Symptom.** A subagent ran a validation script containing `raise SystemExit` as a normal
"stop here". The gateway died:

```
com.chaquo.python.PyException: SystemExit:
  at <python>.__python_exec__.<module>(<string>:29)
  at <python>.jenny.agent.tools.python_exec.execute(python_exec.py:495)
  at <python>.jenny.agent.tools.python_exec._run(python_exec.py:587)
  ...
  at <python>.jenny.android_entry.run_gateway(android_entry.py:170)
Gateway thread exited: no agent behind the service until restarted
```

**Root cause.**

- `jenny/agent/tools/python_exec.py:496` — `PythonNamespace.execute()` wraps the
  `eval`/`exec` in `except Exception:`. `SystemExit`, `KeyboardInterrupt` and `GeneratorExit`
  are `BaseException` subclasses and pass straight through.
- `jenny/agent/tools/python_exec.py:519` — the identical hole in `call_function()`.
- `jenny/agent/tools/python_exec.py:587` — the thread body `_run()` catches only
  `PythonExecInterrupted`, so the escaped exception lands on the future.

**Why the existing safety net does not help.** `jenny/agent/tool_execution.py:432` already
has `except BaseException as exc:` around the tool call. It never fires, and this is the part
worth understanding before "fixing" the wrong file: CPython's `asyncio` treats these two
exceptions specially. In `Task.__step_run_and_handle`:

```python
except (KeyboardInterrupt, SystemExit) as exc:
    super().set_exception(exc)
    raise            # <-- re-raised out of the event loop
```

The re-raise tears the loop down before any downstream `except` runs. **The only place a fix
works is at the sandbox boundary**, i.e. inside `python_exec`.

**Fix.**

1. In `execute()` and `call_function()`, catch `BaseException` instead of `Exception`, with
   two carve-outs that must keep propagating:
   - `PythonExecInterrupted` (`python_exec.py:536`) — it is deliberately a `BaseException`
     so user code cannot swallow it, and `_run()` relies on catching it;
   - `asyncio.CancelledError` — `/stop` and turn abandonment depend on it.
2. Render the swallowed `SystemExit` as a normal tool error in `stderr_buf`, so the model
   sees `SystemExit: 2` in the tool result and can correct itself, exactly as it does for a
   `ValueError`.
3. Add the same guard to `_run()` (`python_exec.py:587`) as defence in depth — it is one
   `except BaseException` that converts to the `("", traceback, None)` tuple.

Sketch:

```python
except (PythonExecInterrupted, asyncio.CancelledError):
    raise
except BaseException:                      # noqa: BLE001 — sandbox boundary, see B1
    traceback.print_exc(file=stderr_buf)
```

**Tests** (`tests/agent/tools/test_python_exec.py`):

- `raise SystemExit` in `code=` → tool returns a string containing `SystemExit`, the event
  loop is still running afterwards;
- `exit()` and `quit()` → same;
- `raise KeyboardInterrupt` → same;
- `PythonExecInterrupted` still propagates (timeout path still returns the timeout message);
- `asyncio.CancelledError` still propagates (the `/stop` test must keep passing).

**Risk.** Low. The only behaviour change is that a previously fatal exception becomes a tool
error string.

## B2 — the retry loop is dead code for exactly this crash

**Root cause.** `jenny/android_entry.py:185` — the loop at line 174 wraps
`asyncio.run(_run_gateway(...))` in `except Exception as exc:`. `SystemExit` is not an
`Exception`, so the three attempts (`MAX_RETRIES = 3`, `RETRY_DELAY_S = 5`, lines 17-18) are
skipped entirely and `run_gateway` returns straight to Kotlin.

**Fix.** Catch `BaseException` in the retry loop, re-raising only `KeyboardInterrupt` (which
on Android is not a user action but the interrupt mechanism) if that turns out to be needed —
verify against `_interrupt_thread` before deciding. Log the class name so the next occurrence
is greppable.

**Test** (`tests/runtime/`): patch `_run_gateway` to raise `SystemExit` on the first call and
succeed on the second; assert `run_gateway` retries rather than returning.

**Risk.** Low, but note the interaction with B1: once B1 lands, `SystemExit` should never
reach here. B2 is the belt to B1's braces, and it is what turns "gateway gone for hours" into
"gateway back in 5 seconds" for any *other* `BaseException` we have not thought of.

## B3 — a dead Python thread is not noticed for up to 3 hours

**Root cause.** When `run_gateway` returns, `GatewayService.kt:298` catches, logs
`Gateway thread exited: no agent behind the service until restarted` (line 306) and the
thread ends — but **the foreground service and its notification stay alive**, so
`GatewayService.isRunning` stays `true`. `Watchdog.isGatewayAlive` therefore falls through to
the heartbeat-age check, whose threshold is (`Watchdog.kt:216-217`):

```
staleAfterMs = baseIntervalMin(15) × MULTIPLIER_DOZE(4) × STALE_PERIODS(3) × 60_000
             = 180 minutes
```

So the app shows "running" in the notification shade while nothing is behind it, and no
restart happens for up to three hours — with the check itself only running every 15-60
minutes.

**Fix.** Do not wait for the heartbeat to go stale when we *know* the thread is gone. In
`GatewayService`, set an explicit flag (e.g. `gatewayThreadAlive = false`) in the same place
the "thread exited" line is logged, and have `Watchdog.isGatewayAlive` return `false`
immediately when the service is running but that flag is down. The generous 3-hour heartbeat
threshold stays as-is for the case it was designed for (a *live* thread that is merely slow
or dozing) — it is the wrong instrument for a thread that has provably exited.

Also consider having the service restart the thread itself once, immediately, before falling
back to the watchdog — cheaper than a whole `startForegroundService` cycle.

**Test.** Kotlin unit test on `isGatewayAlive` with `isRunning=true`, fresh heartbeat, thread
flag down → expect `false`.

**Risk.** Medium: touching the restart path risks a restart loop. Guard with a minimum
interval between self-restarts and keep the existing `MAX_RETRIES` semantics.

---

# Phase B — the workspace boundary must mean one thing

## B4 — the sandbox confines `open()` but not the `os.*` namespace operations

**This is the most serious item in the list**, because `restrict_to_workspace` is documented
as a security boundary and is on by default.

**What is actually enforced** (`jenny/agent/tools/python_exec.py`):

- confined to the workspace: `builtins.open` (`_workspace_builtin_open`, line 254),
  `io.open` (`_patch_io_open`, line 463), `os.open` (`_patch_os_open`, line 407) — all three
  through `resolve_allowed_path`;
- blocked outright: only the process/exec/privilege list in `_OS_BLOCKED_FUNCTIONS`
  (lines 263-278) — `system`, `popen*`, `exec*`, `spawn*`, `fork*`, `kill*`, `set[ug]id*`,
  `chroot`, `chown`, `mkfifo`, `mknod`, …

**What is neither confined nor blocked:** `os.remove`, `os.unlink`, `os.rmdir`, `os.rename`,
`os.replace`, `os.mkdir`, `os.makedirs`, `os.listdir`, `os.walk`, `os.scandir`, `os.stat`,
`os.chmod`, `os.truncate`, `os.symlink`, `os.link`.

**Consequence.** With `restrict_to_workspace=True`, guarded code **cannot read** a file
outside the workspace but **can delete, rename or truncate** anything the app UID can reach —
`workspace/config.json` (API keys and the bootstrap secret), `sessions/`, `jenny_src/`, the
snapshot store — and can enumerate the entire private data directory.

**Evidence from the session, not theory.** A subagent ran
`os.walk('/data/data/com.flagdizero.jenny/files/chaquopy')`, printed the tree and located the
target file; the `open()` on that same path was then refused. One call, two different
boundaries.

**Fix.** Extend the guard-gated patch pattern already used by `_patch_os_open` to the whole
path-taking surface of `os`, in three groups:

1. **single-path mutators** — `remove`, `unlink`, `rmdir`, `mkdir`, `makedirs`, `truncate`,
   `chmod`: resolve the argument through `resolve_allowed_path` with
   `allowed_root=self.workspace`.
2. **two-path mutators** — `rename`, `replace`, `link`, `symlink`: resolve **both** ends.
   A rename with one end outside is an escape.
3. **enumerators** — `listdir`, `scandir`, `walk`, `stat`, `lstat`: resolve the argument the
   same way. Enumeration is not harmless; it is what let the subagent map the app's private
   directory.

Keep the existing discipline exactly: patch the *global* `os` module, store the real function
on `_jenny_real_fn`, and gate on `_active_guard_rules() is not None` so host code is
untouched. That gate is load-bearing — the comment at `_patch_os_open` explains that an
ungated patch broke Chaquopy's `tempfile` when extracting native `.so` files.

Also refuse `dir_fd=` and integer-fd arguments on all of them, as `_patch_os_open` already
does — a `dir_fd` sidesteps path resolution entirely.

Consider `shutil` too: it is currently reachable and its functions call `os.*` internally, so
patching `os` covers most of it, but `shutil.rmtree` deserves an explicit check (the device
`SOUL.md` already records it as blocked — confirm where).

**Tests** (new `tests/agent/tools/test_python_exec_sandbox.py`):

For each patched function, two cases — inside the workspace succeeds, outside raises
`WorkspaceBoundaryError` — plus:

- `os.rename` with source inside / destination outside → refused;
- `os.walk` on an outside path → refused;
- `dir_fd=` → refused;
- with `restrict_to_workspace=False`, all of the above pass through untouched;
- host code on another thread (guard inactive) is unaffected.

**Risk.** Medium-high: this narrows what existing skills and Jenny Apps can do. Anything that
was quietly reaching outside will start failing. Mitigate by logging every refusal at WARNING
with the path, running one release with logging before enforcing if you want a soft landing,
and checking `jenny/apps/executor.py` for legitimate outside-workspace access first.

---

# Phase C — make `python_exec` stop lying

## B5 — `working_dir` is accepted and ignored

**Root cause.** Declared in the schema (`python_exec.py:666`, *"Optional working directory
for the execution"*), accepted at line 785, assigned at line 796
(`self.namespace.working_dir = working_dir`) — and **read nowhere**. No `os.chdir`, no
`sys.path` insertion, no effect on path resolution. The only other read is at construction
time (line 199/203) where it seeds the sandbox root.

**Why it matters more than a dead parameter usually would.** The model believes it. The
WaterBot heartbeat passes `working_dir` on every cycle because
`skills/waterbot/SKILL.md` says *"con cwd = workspace root"*. It has no effect, the bare
`import wb_probe` fails, and the agent spends four extra tool calls per cycle rediscovering
that it must insert the path by hand (see B6). The device's `SOUL.md` carries a hand-learned
note — *"python_exec runs with cwd `/` (not the workspace root) — always use absolute paths"* —
which is a user paying, in prompt tokens, for a broken parameter.

**Fix.** Make it honest, and separate two concepts the code currently conflates:

- **resolution base** — what a relative path is measured from. Today always the workspace
  root (`resolve_allowed_path(..., workspace=self.workspace)`,
  `jenny/security/workspace_policy.py:131`).
- **boundary** — what may not be escaped. Must remain the workspace root.

Concretely:

1. Use `working_dir` as the resolution base for relative paths in `_resolve_workspace_write`
   and `_patch_os_open`, while keeping `allowed_root=self.workspace` as the boundary. A
   relative path then resolves against `working_dir`; an escape is still refused.
2. Put `working_dir` at the head of `sys.path` for the duration of a guarded exec and remove
   it in the `finally`, using the same enter/exit shape as `_enter_guard`/`_exit_guard`.
   This is what makes `import <module_next_to_the_skill>` work.
3. Validate `working_dir` through `resolve_allowed_path` on entry, so it cannot be used to
   point the resolution base outside the workspace.
4. Update the parameter description to say what it now actually does — relative paths *and*
   imports resolve against it.

**Why not `os.chdir`.** Rejected deliberately: the process cwd is global and the gateway is
async with a thread pool. A `chdir` inside one exec would change the cwd of every other
coroutine and of Chaquopy's own machinery. `sys.path` mutation is also global, but it is
additive, scoped to the exec, and the execs are already serialised by
`_stdout_redirect_lock` in `execute()`. Confirm that serialisation still holds before
relying on it, and if it ever stops holding, move to the explicit-loader design (B6 option D).

**Tests.**

- default (`working_dir` unset) → relative `open("x.txt")` still resolves to the workspace
  root, i.e. **no behaviour change** for existing callers;
- `working_dir=<workspace>/skills/foo` → `open("bar.txt")` hits
  `skills/foo/bar.txt`; `import helper` finds `skills/foo/helper.py`;
- `working_dir` outside the workspace → refused;
- after the call, `sys.path` is back to its previous value (assert exact equality, including
  on the exception path);
- an escape via `working_dir` + `../../..` is still refused.

**Risk.** Low if the default stays the workspace root. The whole change is inert unless a
caller passes `working_dir`.

---

# Phase D — skills own their code

## B6 — a skill cannot own a `.py` file today

**This is the item you asked about, and the answer is: yes, `.py` files belong in the skill's
own folder — but the doc rule on its own does not work today, which is why B5 has to land
first.**

**The case that proved it.** On 2026-08-11 at 19:15 a Jenny subagent wrote `wb_probe.py`
into the **workspace root** and its first, working invocation was:

```python
import sys
sys.path.insert(0, "/data/data/com.flagdizero.jenny/files/workspace")
import wb_probe
```

It then wrote `skills/waterbot/SKILL.md` with the code block **minus** the `sys.path.insert`,
replaced by the prose *"con cwd = workspace root"*. Every later heartbeat copies that block
verbatim, the bare import fails, and the agent self-heals:

```
21:45:06  python_exec  import wb_probe …                          ← as written in the skill
21:45:09  find_files   {"query": "wb_probe"}                      ← "where is this file?"
21:45:47  python_exec  print(sys.path); find_spec("wb_probe")     ← diagnosing
21:47:24  python_exec  sys.path.insert(0, …); import wb_probe     ← works
```

Two minutes and four extra calls per cycle, rediscovered every time. And the module sits in
the workspace root, where it is indistinguishable from debris — a scratch-directory sweep
written without knowing this would have deleted it and silently broken plant monitoring
(this is why [`roadmap/workspace-scratch-dir.md`](roadmap/workspace-scratch-dir.md) carries
the correction).

**Mechanism options considered.**

| Option | Verdict |
|---|---|
| A. Documentation only (skills must hand-write `sys.path.insert`) | Insufficient alone. The WaterBot case *is* the counter-example: an agent transcribing its own working code dropped the boilerplate. |
| B. Make `working_dir` real via `os.chdir` | **Rejected** — process-global cwd in an async gateway. |
| C. Make `working_dir` real via scoped `sys.path` + relative-resolution base | **Chosen.** This is B5. Small, honest, and matches what the agent hand-rolls anyway. |
| D. A dedicated builtin (`load_skill_module(...)`) | Keep in reserve. Explicit and stateless, but a new API the model has to learn, and skills already reach for plain `import`. |
| E. Put every `skills/*/` on `sys.path` at startup | Rejected — name collisions across skills, and a permanently wider import surface. |

**The policy, once C is in place.**

1. A skill's helper code lives **inside the skill folder**: `skills/<name>/scripts/<mod>.py`
   (matching the `scripts/` convention already documented in
   `jenny/skills/skill-creator/SKILL.md:156` and used by `llm-wiki` and `app-creator`).
2. The SKILL.md invocation block **must** pass
   `working_dir="<workspace>/skills/<name>/scripts"` and then plain `import <mod>`.
3. Nothing a skill owns is ever written to the workspace root.

**Edits to `jenny/skills/skill-creator/SKILL.md`:**

- Add a short, rule-shaped section (the file already uses `<rule>` blocks — match that style)
  stating points 1-3 above, with one copy-pasteable correct example.
- State the negative explicitly: *never write a helper module to the workspace root* — an
  agent that has just built a working snippet at the root will otherwise leave it there,
  which is exactly what happened.
- Require the generated SKILL.md to include the `working_dir` in every `python_exec` example
  it emits, so the next transcription cannot drop it.
- Note that `python_exec` is the only execution tool: no shell, no `python3` (see B9).

**Test.** Add a case to the skills tests that lints every bundled `SKILL.md`: any fenced
`python_exec` block that contains a bare `import` of a module shipped inside that skill must
also set `working_dir`. Cheap, and it fixes B9/B10 permanently rather than once.

## B9 — `llm-wiki/SKILL.md` documents a command that cannot exist

Two independent defects on the same lines (`jenny/skills/llm-wiki/SKILL.md:53, 74, 91, 97,
268, 295, 296, 321, 365`):

1. **The path is doubled.** The doc says
   `python3 skills/llm-wiki/llm-wiki/scripts/<script>.py`. The manifest
   (`jenny/utils/android_assets.py:50-53`) extracts `llm-wiki/scripts/*.py` into
   `workspace/skills/`, so the real path is `skills/llm-wiki/scripts/<script>.py` — one
   `llm-wiki`, not two.
2. **`python3` does not exist on this platform.** `jenny/templates/agent/tool_contract.md:45`
   states it outright: no shell, no subprocess, and specifically *"do not attempt to run …
   `python3` …"*. A shipped skill instructing the agent to shell out contradicts a shipped
   system prompt, and the prompt is the one that is right.

**Fix.** Rewrite those invocations as `python_exec` calls with `working_dir` set to
`<workspace>/skills/llm-wiki/scripts` (after B5), passing arguments via `sys.argv` and
`runpy`/`exec` rather than a shell line. Note the scripts already self-insert their own
directory (`sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` in
`scaffold.py:43`, `lint_wiki.py:50`, `audit_review.py:30`) so their internal imports keep
working either way.

## B10 — `skill-creator/SKILL.md` documents unresolvable relative paths

`jenny/skills/skill-creator/SKILL.md:72, 373, 379` tell the agent to run:

```python
python_exec(code="exec(open('scripts/init_skill.py').read())")
```

The script really is shipped (`android_assets.py:66-68`) at
`workspace/skills/skill-creator/scripts/init_skill.py`. But a relative `open()` resolves
against the **workspace root** (`workspace_policy.py:131`), so this reads
`workspace/scripts/init_skill.py`, which does not exist. The documented happy path for
creating a skill cannot work as written.

**Fix.** Same shape as B9: `working_dir="<workspace>/skills/skill-creator/scripts"` plus
`open("init_skill.py")`, or an absolute path. Covered by the same lint test as B6.

## B7 — `web_fetch` fails on `text/plain`

**Status: observed, cause not proven.** Two attempts on
`https://raw.githubusercontent.com/...` (both `extractMode: "text"` and `"markdown"`) failed
with *"Android web_fetch bridge failed"* (`jenny/agent/tools/android_web.py:532`), while the
`http_get` builtin inside `python_exec` fetched the same URL fine. `web_fetch` goes through a
WebView (`_bridge_fetch`), which renders pages rather than returning documents.

**Fix.** Read the Kotlin side of the bridge first and confirm the content-type assumption
before changing anything. If confirmed, either fall back to a plain HTTP client for
non-HTML content types, or return an actionable error (*"this URL is not an HTML page; use
`http_get`"*) instead of a generic bridge failure. Note that the failure path also calls
`destroy_bridge()`, so one bad URL tears down the shared WebView for the next caller —
check whether that is wanted.

---

# Phase E — monitors must be able to admit failure

## B8 — a monitor's failure looks exactly like its success

**The shape of it.** `HEARTBEAT.md` says *"Avverti l'utente SOLO se almeno una pianta ha
umidità < 15%. Se tutto è ≥15%, non dire nulla"*, and
`jenny/templates/agent/cron_monitor.md` teaches the same contract for `mode='monitor'`:
silence is the default and costs nothing. That contract is correct and should not change.

The problem is that there is currently **no third state**. A cycle where the check ran and
found nothing, and a cycle where the check could not run at all, produce byte-identical
output: nothing. Under B5/B6 the WaterBot check was failing its first import on every cycle;
had the agent not self-healed, the user would have seen exactly what a healthy garden looks
like. The skill even has a legitimate reason to be silent (*"se hps è irraggiungibile salta
il ciclo in silenzio"*), which makes the broken state perfectly camouflaged.

**Fix — minimum viable version.** Record an outcome per monitor/heartbeat run, and escalate
only on a streak:

1. Persist a small per-job state next to the cron store: `last_run_at`, `last_outcome`
   (`ok` | `no_finding` | `could_not_check`), `consecutive_failures`.
2. Teach the monitor prompt a third reportable state. `cron_monitor.md` currently offers
   "speak" or "stay silent"; add "I could not perform the check" as a distinct, *non-speaking*
   outcome the agent records rather than messages.
3. Escalate on a streak, not on a single failure: after K consecutive `could_not_check`
   (K = 3 is a reasonable default, ~1.5 h at the 30-minute heartbeat), send one message and
   then stay quiet until the state changes. This preserves "silence is cheap" while making
   "silently broken" impossible to sustain.
4. Surface the state in the WebUI where cron jobs are listed, so the answer to *"is the plant
   monitor actually working?"* does not require reading logcat.

**Design note.** Resist making an uneventful run speak. The value of the heartbeat is that it
costs nothing when there is nothing to say; the fix is to distinguish *nothing to say* from
*nothing to say because I fell over*, not to remove the silence.

**Tests** (`tests/cron/`): three consecutive `could_not_check` runs produce exactly one
message; a subsequent `ok` resets the counter; a single failure produces none.

---

# Phase F — already-filed, listed for completeness

## B11 — no template guard on the bootstrap files — **DONE**

`jenny/agent/context.py:236-247` injects `AGENTS.md`, `SOUL.md` and `USER.md` into every
system prompt with no checks, while `MEMORY.md` is guarded by `_is_template_content` at
line 106. An untouched template is therefore injected as if it were content.

Closed by `5bc4d9e` (the guard, and the decision to skip `USER.md` but merely label
`SOUL.md`), `97d7b38` (`_RETIRED_TEMPLATE_DIGESTS`, so rewriting a template does not
silently promote every untouched copy to user prose) and `007c60d` (`AGENTS.md` joins the
skipped set once its system half moved to `agent/scheduling.md`). Open question 1 of
[`roadmap/agents-md-ownership.md`](roadmap/agents-md-ownership.md) is answered there.

The other four architectural items already have their own files and are **not** duplicated
here: AGENTS.md ownership, the memory budget, the scratch directory, and the USER.md drift.

---

# Phase G — on-device cleanup (only after A-F)

Explicitly last, per the decision on 2026-08-11: **do not touch the phone's `waterbot` skill
until the code fixes are in.** Moving `wb_probe.py` before B5 would turn an intermittent
failure into a permanent one.

Order once the build is on the device:

1. `adb` snapshot first — Settings → Backup & restore → *Create snapshot now* (the 21:18
   `manual` snapshot from the investigation is the current rollback point).
2. Move `wb_probe.py` from the workspace root to `skills/waterbot/scripts/wb_probe.py`.
3. Rewrite the code block in `skills/waterbot/SKILL.md` to pass
   `working_dir="<workspace>/skills/waterbot/scripts"` and keep the plain `import wb_probe`.
4. Verify by waiting for one real heartbeat cycle and confirming in logcat that there is **no**
   `find_files wb_probe` / `sys.path` diagnostic follow-up — that absence is the pass
   condition.
5. Remove `__pycache__/` from the workspace root.

Getting content onto the device reliably (learned the hard way, 5 turns):

- `adb shell input text` **corrupts** what it types (IME autocapitalises and drops
  characters: `get_data H4sIAKx1` arrived as `_D4s1`) and throws on any non-ASCII. Fine for
  prose instructions, useless for exact content.
- The exact-content channel is: publish the text at a URL, then have a subagent fetch it with
  the `http_get` builtin inside `python_exec` and write it. `web_fetch` will not do it (B7).
- When dictating a verification check, **quote the file, do not paraphrase it**. Asking for
  the substring `Pick the mode deliberately` failed because the real text is
  `` Pick the `mode` deliberately `` — the agent correctly refused to write a correct
  download.
- Screenshots need the physical display id (`screencap -d 4627039422300187648`); `-d 0`
  returns a stale frame.
- Do not read a file back until `Turn completed` appears in logcat. There is no client-side
  cache (`mobile-workspace.js:682` re-fetches every time); a "not written" reading taken
  mid-turn is just early.

---

# Suggested order of work

1. **B1 + B2** together — one PR, small, high value. The gateway stops dying.
2. **B3** — Kotlin side, independent, closes the recovery gap.
3. **B4** — the security fix. Own PR, own test file, soft-launch with logging if you want.
4. **B5** — makes `working_dir` honest. Unblocks D.
5. **B6 + B9 + B10** — the skills pass, including the lint test that keeps them honest.
6. **B8** — design first, then implement.
7. **B7** — needs the Kotlin bridge read before anything is decided.
8. **Phase G** on the phone.

B1-B4 are worth shipping as one release before starting Phase D; B5 changes tool semantics
and should not ride in the same build as a crash fix you want to be able to attribute.

> **What actually happened, 2026-08-11 night → 2026-08-12.** All of B1-B13 were implemented
> in one sitting by parallel agents, and the advice in the paragraph above was not followed:
> everything rode in one build. That is what made Round 2 necessary. See below.

---

# Round 2 — what the post-implementation review found

Status: **in progress**, 2026-08-12.

`jenny/agent/tools/python_exec.py` was rewritten five times in one night (B1 → B4 → B5 → B4b
→ B12) by five agents, none of whom could see the others' work. Each verified its own slice
and left the suite green. **Nobody read the result as a whole**, so before installing on the
phone the file was put through four independent read-only reviews, each with a different
lens: guard lifecycle, boundary completeness, host blast-radius, and cross-change
interference.

That was the right call. The reviews found **two regressions we introduced**, one hole that a
later layer made worse, one bug that last night's work promoted from rare to routine, and
nine comments that are now false.

The general lesson, worth keeping: **a green suite proves each agent's assumptions are
self-consistent, not that the file is correct.** The tests were written by the same agents
whose code they cover, so they encode the same blind spots. Every finding below came from
reading, and several were reproduced afterwards.

## R2 index

| # | Finding | Origin | Severity |
|---|---|---|---|
| R1 | `_workspace_open` is the only resolver without `_path_guard_bypass()` — 18-33 spurious WARNINGs and ~1,400 `lstat` per legal `os.open` | B4, missed by B4b's sweep | high |
| R2 | `_exit_guard` does its fallible work first; an async exception mid-teardown poisons a **shared** pool worker | B5 ordering | high |
| R3 | `bypass` is the one guard thread-local `_exit_guard` never clears; a leak silently disables `restrict_to_workspace` for the process | B4 | high |
| R4 | `_SessionStopped` is swallowed by B1's `except BaseException` — `/stop` on a session reports success | **regression, B1** | high |
| R5 | `shutil.rmtree`'s `onerror`/`onexc` callbacks run inside `_path_guard_bypass()` | B4, worsened by B4b | **critical** |
| R6 | `store.py::_LOCK` missing from `android_entry`'s reset block — every config write dies on the 2nd loop | pre-existing, made routine by B2+B3 | **critical** |
| R7 | The `jenny:gateway` wakelock is never re-acquired after a Service destroy+recreate | **regression, B3** | **critical** |
| R8 | The boundary is thread-local and `asyncio` is allowlisted — `to_thread` steps off it silently | B4 design | high |
| R9 | A global lock held across all user code, on the shared default executor, uninterruptible by `SetAsyncExc` | pre-existing, widened by B5 | high |
| R10 | A `working_dir` module permanently shadows a stdlib module via `sys.modules` | B5 | high |
| R11 | Chaquopy's `.so` extraction uses `os.stat`/`listdir`/`makedirs`/`replace`, now refused on the guarded thread | B4 | high |
| R12 | `WorkspaceBoundaryError` subclasses `PermissionError`, so `glob` and `Path.glob` swallow it and return `[]` | pre-existing | medium |
| R13 | `os.utime`, `access`, `readlink`, `chdir`, `*xattr` in neither the patched nor the blocked set | B4 | medium |
| R14 | `python_exec_builtins.py:300` still inherits PEP 563 | B12 scope | medium |
| R15 | Nine comments now false; two teach the wrong model of what the bypass suspends | all layers | medium |

## Two findings worth reading in full

**R5 — the callback hole.** `_reject_fd_kwargs` screens only the `*dir_fd` kwargs, so
`onerror=`/`onexc=` reach `rmtree`, which calls them *inside* the bypass. B4's docstring
reasons only about `rmtree`'s own fd-based traversal — correct, and beside the point. When B4
wrote the carve-out the bypass suspended only the `os` path table; B4b then added
`builtins.open` and `io.FileIO` to what it suspends. **A carve-out written against one
version of a shared mechanism silently widened when the mechanism grew.** Trigger:
`shutil.rmtree("<ws>/does-not-exist", onerror=cb)` passes non-strict validation, fails, and
runs `cb` with the whole boundary off.

**R7 — the load-bearing side effect.** B3 correctly stopped `startGateway` from launching a
second gateway into a live interpreter. But the *only* thing that re-acquires the wakelock is
`apply_service_lock()` on a fresh `run_gateway`, and before B3 the double-start re-ran it as a
side effect. Removing a bug removed the accidental repair that depended on it — and the
casualty is the anti-doze behaviour measured and shipped in 0.6.6.

## Order of work

Almost everything lands in one file, so R2 is mostly serial:

```
parallel:  K  wakelock + swapped log lines (Kotlin)
           L  store._LOCK reset + sweep for sibling module-level asyncio primitives
           M  builtins: PEP 563 fallback, path_resolve agreement

serial on python_exec.py:
  A  R1 R2 R3 R4 R5          the mechanical five
  B  R11 R12 R13 + open_code + fdopen    boundary completeness
  C  R8 R9 R10               the architectural ones
  D  R15                     comment truth pass — last, must describe the final state
```

D goes last on purpose. The whole failure mode of the first night was an agent trusting a
comment that a later change had made false.

---

# Round 3 — what the phone found that no reviewer could

Status: **done**, 2026-08-12.

Round 2's build went onto the device. Within one hour of real use, two cycles of the WaterBot
heartbeat surfaced two bugs that four independent code reviews had not — because both are
about **what the model infers from the runtime**, which only shows up when a model is driving.

| # | Finding | Origin |
|---|---|---|
| R3 | `os.getcwd()` did not report `working_dir`, so the model computed paths that did not exist | **B5 + R2-B**: making `working_dir` real without making `getcwd` agree |
| R4 | A **delegated** check could never be recorded as `could_not_check` | **B13**: the outcome is read off the turn that only delegates |
| R5 | On the device's 3.11 the suite is **161 failed / 137 errors**, not green: the guarded `sys` proxy leaks into host code and permanently breaks `sys.modules` | **pre-existing and shipped** — already in 0.6.6, hidden by a dev machine on 3.14 |

## R3, and why it is the same bug three times

The device wrote this, by itself, and it is entirely reasonable code:

```python
{"working_dir": "…/files/workspace",
 "code": "sys.path.insert(0, os.path.join(os.getcwd(), \"skills\", \"waterbot\", \"scripts\"))"}
```

`os.getcwd()` was deliberately unpatched and returns `/`, so that join produced
`/skills/waterbot/scripts`. Third iteration of one failure on one device:

1. the skill said *"con cwd = workspace root"* about a parameter that **did nothing** (B5);
2. the skill said `working_dir` **in prose** next to a bare code block, and the next agent
   copied the block (Phase G, first attempt);
3. the parameter worked, and the model reasoned about it through `getcwd()`, which **lied**.

The rule now written into the code: **the resolution base and the reported working directory
must be the same thing.** A sandbox that resolves `open("x")` against one directory while
`getcwd()` names another is not a guardrail, it is a trap the model walks into by being
sensible. Thirteen channels now agree; `os.environ["PWD"]` is the documented residual.

There is a documentation lesson too, and it is not solved by a rule: **a Markdown ```python
fence cannot express a tool argument.** `working_dir` lives in the `python_exec` call, not in
the code, so a fenced block can never carry it — only the surrounding prose can, and prose is
exactly what gets dropped in transcription. A SKILL.md must therefore show *the call*, not
the code. B6's rule says "every example must carry `working_dir`"; satisfying it honestly
means showing the invocation.

## R4 — a correct instruction that broke the recording

B13's preamble tells the agent, correctly, not to speak before a delegated subagent answers.
But `record_task_outcomes` reads the **orchestrator turn's** `final_text`, and that turn only
delegated — so no marks, and every delegated task was recorded healthy. The subagent's result
returns later as its own turn, which does not pass through `_run_heartbeat`. Since the main
agent is orchestrator-only and has no `python_exec`, **any real check is delegated**, so B13
could not cover the case it was built for.

Closed with a two-turn protocol: `CHECK_DELEGATED <n>` at T0 marks a task pending (so the
per-run prune does not zero its streak), and a follow-up block on the announce turn collects
the verdict. A pending entry with no verdict resolves in the task's favour after one cycle,
which is B13's optimism applied to the new state.

## R5 — the suite was green on a Python the device does not run

Filed as a housekeeping item ("two tests use 3.12+ APIs"). It was not housekeeping — it was
the only finding in three rounds that is **already in production**.

`android/app/build.gradle.kts:140` pins Chaquopy to **3.11**; this machine runs 3.14. CI
already has a `["3.11", "3.12"]` matrix — but **nothing from these three days has ever been
committed**, so the only interpreter that has seen this work is the one that does not ship.
Installing a real 3.11 and running the suite:

| | 3.14 (dev machine) | 3.11 (the device) |
|---|---|---|
| before | 5787 passed | **161 failed, 137 errors** |
| after | 5789 passed | 5788 passed, 6 skipped |

298 of those had **one** root cause, and it is not a test problem:

```
453 × AttributeError: '_GuardedSysModules' object has no attribute 'copy'
```

`_patch_sys_backreferences` replaces `os.sys`, `posixpath.sys`, `pathlib.sys`,
`warnings.sys` &c. with `_GUARDED_SYS`, so guarded code cannot reach a blocked module through
a module's own back-reference. Unlike every other patch in the file, that substitution is
**permanent and process-wide** — there is no point at which it is undone, and there could not
be one without racing the other threads. `_GuardedSysModule.modules` was a class constant, so
after the **first** `python_exec` of the process, every line of host code reaching `sys`
through one of those modules got a proxy that is not a dict: no `copy`, no item assignment,
no `pop`. On 3.11 `inspect._signature_from_builtin` calls `sys.modules.copy()` on every
builtin it inspects. The gateway degrades permanently, and the trigger is the agent doing its
job once.

Fixed by making `.modules` a property that is guard-gated like everything else in the file:
the real dict when no guard is active on this thread, the filtered proxy inside one. That is
also *more* correct than the constant was — a per-thread question now gets a per-thread
answer. `_GuardedSysModules.copy()` was added too, so guarded code gets a filtered view
rather than an `AttributeError`.

The two test-only items were real as filed: `Path.exists(follow_symlinks=)` is 3.12+, and
`Path.glob` swallows the refusal only from 3.12 — on 3.11 `is_dir()` lets it through, which
is *better* behaviour. That test now asserts the invariant that matters (the model must never
see a bare `[]`) instead of one version's way of delivering it.

**The lesson is not "add a 3.11 job".** The job existed. The lesson is that a suite validates
whatever interpreter you happen to run it on, and "green" said nothing about the phone until
someone typed `python3.11`.

## The pattern across all three rounds

Four times in two days, **a correct fix created the next bug**, always at a seam its author
could not see:

- B3 stopped the double gateway start — and removed the accidental side effect that
  re-acquired the wakelock (R7).
- B4b widened what `_path_guard_bypass()` suspends — silently widening a `rmtree` carve-out
  written against the narrower version (R5, round 2).
- B5 made `working_dir` real — and made `getcwd()` a liar (R3).
- B13 told the agent not to speak too early — and moved the outcome out of reach (R4).

R5 is the exception that proves the rule: it is **not** one of ours. `_patch_sys_backreferences`
was already in `HEAD` — the bug shipped in 0.6.6 and has been degrading the gateway on every
`python_exec` since. It survived four code reviews of this very file for the same reason the
rest of this list did: everyone, reviewers included, was reading it on a machine where the
symptom does not exist.

None of these is a mistake by the agent that made it. They are the cost of parallel work on a
shared mechanism, and the only thing that caught them was, in order: reading the whole file
with fresh eyes, and then **running it on the real device with a real model driving**. The
suite was green at every single one of these points.
