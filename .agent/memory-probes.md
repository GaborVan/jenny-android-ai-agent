# The four memory probes — run these by hand

Five of the guarantees in Jenny's long-term memory are not mechanisms. They are requests written in
a prompt, and they hold because the model honours them. When one stops holding, **nothing goes
red**: lint passes, types pass, the whole suite passes, and Jenny quietly starts re-recording things
she already knows or saying "I don't remember" over a full archive.

So they get checked by hand. **Run these after editing anything under `jenny/templates/agent/`, and
after changing model.** Each takes a few minutes. There is a plan for automating the first four
([`behaviour-harness-plan.md`](./behaviour-harness-plan.md)) which is deliberately unbuilt — at
current stakes, none of these failures loses data, and doing this four times a year by hand is
cheaper than the scaffolding.

**Run each probe against two different models.** This is the part that is easy to skip and is the
whole point: a promise only one model keeps is not a promise, it is a fragile prompt paired with a
forgiving reader. Same result on both means the guarantee lives in the text. Pass on one and fail on
the other means it lives in the reader, and that is a defect to fix now rather than discover after a
model swap.

**Never use real personal facts.** Invent them, and plant a nonsense word in each — `zafkril`,
`brindolo`. The nonce is what turns "did it understand?" into a substring you can grep for, with no
second model needed to judge the answer.

---

## Apparatus

Everything below is driven from a laptop over `adb`. Reading device state needs root (`su`);
**never write workspace files as root** — a file created as uid 0 gets the wrong owner and SELinux
label and the app then fails on it.

```bash
export ANDROID_SERIAL=<phone serial>          # a second device on adb will silently take the call
adb forward tcp:18790 tcp:18790
```

Then talk to the gateway with a `websockets` client at
`ws://127.0.0.1:18790/?client_id=probe&token=<websocket.token_issue_secret from config.json>`.
Send `{"content": "..."}`; replies arrive as `delta` / `message` / `turn_end` frames. Slash commands
answer on the same socket, and a `/dream` reply lands **minutes** after its "Dreaming…" ack, so keep
the socket open rather than reconnecting. Remove the forward and delete any local copy of
`config.json` when you are done.

Tool calls appear in `adb logcat` as `Tool call: <name>({...})`; **tool results do not**, so read
files for outcomes. Attribute anything you measure **by PID, not by clock**: installing kills the
app and the watchdog restarts it within the same second, so a cycle that fired just before the swap
belongs to the previous build.

Budgets are moved only through `/dream budget <memory|user|soul> <chars>` — that path goes through
the config write funnel. Editing `config.json` by hand does not.

---

## Probe 1 — the consolidator does not re-record what memory already holds

**Guards** the known-facts block shown to the consolidator (memory plan, phase 4).

**Setup.** Note what is already in `USER.md` and `memory/MEMORY.md`.

**Do.** In a fresh session (`/new`), state two or three facts that are *already* recorded, in
different words. Then `/new` again — that forces the consolidator to run on the chunk instead of
waiting for a token overflow. Wait ~20s and read the new entry appended to `memory/history.jsonl`.

**Pass.** The restated facts are absent from the new history entry, or carry `[skip]`.

**Fail.** They come back as `[durable]` or `[permanent]`, which is the duplicate-extraction defect
returning.

**Watch out.** Pick facts recorded in *different wording* from what you say, and verify beforehand
that they really are in those two files — twice on 2026-08-19 a probe "passed" because the model
answered from the prompt without a tool call, and the fact turned out to be in `USER.md` all along.
Check the logcat for tool calls before believing a result.

**To confirm the probe still bites:** suppress the known-facts block and re-run. The duplicates
must come back. A probe that cannot be made to fail is not measuring what its name says.

---

## Probe 2 — but a genuinely new fact still gets through

**Guards** Probe 1 against the reading where the consolidator has simply stopped working. Without
this one, a dead consolidator scores perfectly.

**Do.** Same as Probe 1, with one clearly new fact added that carries a nonce.

**Pass.** The nonce appears in the new history entry with a non-`[skip]` mark, and the restated
facts still do not.

**Watch out.** Facts *about the memory system itself* get judged operational noise and dropped —
observed 2026-08-19, when a seeded batch was rejected as "metadati operativi del consolidator
stesso". Make the fact about something else.

---

## Probe 3 — Dream frees space instead of taking a refusal

**Guards** the budget paragraph in `dream.md`, and the demotion path (phase 2).

**Setup.** `/dream budget` to read the current sizes. Then cap the file the fact will land in a few
hundred chars *below* its current size.

**Do.** Seed a new fact carrying a nonce (message, then `/new`, as above). Run `/dream`. Read
`memory/.dream_cursor` and `memory/.dream_review` before and after.

**Pass.** The nonce lands in the file, the cursor advances, `stuck_runs` stays 0. The run reports
something like *"both files were over budget, so I freed space then wrote"*. Everything it pruned is
in `memory/archive/`.

**Fail.** `stuck_runs` climbs and the cursor is stuck — the write was refused and never recovered.

**Note.** Reference results, 2026-08-19: at 41 chars over cap and again at 144% of cap, Dream freed
the space itself both times. A refusal is now *hard* to provoke, which is phase 2 working. If you
need to see one, the cap has to be low enough that no amount of safe pruning reaches it.

**Restore the budgets afterwards.** Note the original numbers before you start.

---

## Probe 4 — the archive gets searched, across a language boundary

**Guards** the `recall` tool and the `## Archive` line in the system prompt (phase 7.1).

**Setup.** Find a detail that exists **only** in `memory/archive/` — grep the rest of the workspace
to be sure, including `wikis/`, which the agent will happily read instead.

**Do.** In a fresh session, ask about it **in the other language** from the one it is recorded in,
without using any word that appears in the record.

**Pass.** `Tool call: recall(...)` appears in the logcat, and the answer contains the detail.

**Fail.** She answers "I don't remember", or reaches only for `grep` — which cannot match across
languages and skips large files in silence, the two failure modes this replaces.

**Watch out.** Most of the archive is *superseded wordings* of facts still present in the live
files, because rewriting an entry archives the old version. Finding a genuinely absent detail takes
looking; two attempts on 2026-08-20 picked facts that were still in `USER.md` in other words.

---

## Probe 5 — the wiki directory follows what is actually there

**Guards** the rules in `atlas.md`: *"remove what disappeared from the wiki"* and
*"Keep the `## Wikis` section complete"*. That section is **derived
membership**: every wiki in the inventory gets a line, always, and nothing else
gets one. The model writes the wording of each line; it does not get to choose
the membership, and this probe looks only at membership.

**Why it matters.** `memory/WIKI.md` is loaded into **every** prompt. A line
naming a deleted project is not an untidy file — it is a false thing Jenny reads
on every turn, with a `→ wikis/<slug>/wiki/index.md` pointer that leads nowhere.
And `wikis/_index.md`, which says the same thing, updates immediately because
code regenerates it — so when the two disagree, the wrong one is always this one.

**Setup.** A **throwaway** project, never a real one: make one for the purpose,
send it a message so it genuinely exists, and check it has reached the `## Wikis`
section of `memory/WIKI.md` (that needs an Atlas pass, so either wait or force
one). Note the starting list.

**Do.** Delete that project from the file browser — hold it, pick Delete — then
`/atlas`. Without `force`: deleting changes the fingerprint, so the pass starts
on its own, and that is the real path. Wait for it to finish (tens of seconds)
and read `memory/WIKI.md` again.

**Pass.** The deleted project's line is gone, and the other lines are untouched.

**Fail.** The line stays. From then until whenever, Jenny reads on every turn
that the project exists.

**The other half, for free.** If meanwhile there is a wiki that is **present and
not listed** — one born after the last pass, say — the same read also checks the
other direction of the rule: it should have appeared. Worth setting up on
purpose; it is one extra project and no extra work.

**Watch out.** Atlas runs every ~12h and **skips when the wiki fingerprint has
not changed**. Confirm from the logs that a pass actually ran rather than
inferring it from the file: a `WIKI.md` with an old mtime means Atlas never got
in, and a probe that did not run is not a fail, it is nothing. Quickest check:
`ls -la sessions/ | grep atlas` and look at the last one.

**Where to write the result.** Not `memory-plan.md` like the other four — this
one guards Atlas, so its home is
[`stale-name-bindings-plan.md`](./stale-name-bindings-plan.md), step 1.

**If it fails**, the fix is planned and should not be improvised:
[`stale-name-bindings-plan.md`](./stale-name-bindings-plan.md), step 1b — the
drift is **computed in Python** and handed to the prompt, and no line is ever
deleted from that file by code. `WIKI.md` is prose the model wrote: reading it
can fail silently and cost nothing, rewriting it cannot.

---

## After a run

Write the date, the models used, and what each probe did into
[`memory-plan.md`](./memory-plan.md) beside the phase it guards. A result nobody recorded is a
result nobody can compare against next time, which is the only thing that makes these worth
running twice.
