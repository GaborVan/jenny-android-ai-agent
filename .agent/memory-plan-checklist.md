# Long-Term Memory — Working Checklist

Execution list for [`memory-plan.md`](./memory-plan.md). The plan holds the *why* and the
measurements; this file holds the *what next*. Defect ids (`D1`…`D9`) refer to the plan's
register. Tick a box only when its verification has actually run — the on-device pass is part of
"done", not a follow-up (three of today's defects were invisible to the suite).

Branch: `jenny-memory`.

> **Closed 2026-08-21.** Phases 0, 1, 1b, 2, 4, 5, 6 (bar 6.1) and 7.1 are done and verified on
> device. The unticked boxes below are **measured-and-declined or deliberately deferred**, not a
> queue: phase 3 past 3.1 (its own measurement argued against it), 6.1 (two changes at once, nothing
> has asked), 7.2 (explicitly not to be built early). The reasons are in the plan's status section.
> The four prompt-shaped guarantees are checked by hand — [`memory-probes.md`](./memory-probes.md).

---

## Phase 0 — Stop the bleeding

Independent, no design risk, shippable alone.

- [x] **0.1** Reset `forced_at_stuck` when the cursor advances — `jenny/agent/memory.py::set_review_state`, clear it in the same write when `stuck_runs == 0` *(D2)*
- [x] **0.1t** Test: climb to `stuck == 2`, advance, climb again, assert the review is forced **both** times
- [x] **0.2** Settle the budget's meaning for the main agent — **decided 2026-08-18: advisory**, `write_size_guard` stays off the main registry, pinned by a behavioural test *(D1, [open decision 1](./memory-plan.md#open-decisions) — closed)*
- [x] **0.2d** Say it in `docs/using/memory.md` (public URL — content edit only, no rename)
- [x] **0.3** `/dream budget` gauge names which writer each cap binds

> **Phase 0 verified on device, 2026-08-18.** Release build of `73fec16` installed on the Titan 2 (PID 16163).
> Before: `{"runs_since_review": 2, "stuck_runs": 0, "forced_at_stuck": 4}` — the mine, armed in production.
> After one `/dream`: `{"runs_since_review": 3, "stuck_runs": 0, "forced_at_stuck": 0}` — cleared. 0.3's line renders in the live `/dream budget`.

## Phase 1 — Make the fact the unit

The enabler. Phases 2, 5 and 6 all lean on it.

> **Code-complete and run on device 2026-08-18** (release build of `d5850ee`, PID 17214).
> Confirmed there: the tool is offered and *preferred* — the run used `memory add` and made zero
> `edit_file`/`apply_patch`/`write_file` calls, adding one line under the right heading, 19→20
> entries. The review pass still reaches for `apply_patch` (1.11, live). The new prompt is
> byte-identical on the device, so the boot-time re-extraction works.
>
> It also found **D10**, which Phase 1b now covers. Still never observed: a fact from a held batch
> landing after room is freed — the fixture used for it named itself a test, and the model
> correctly declined to store a transient fact.

- [x] **1.1** New tool module `jenny/agent/tools/memory_entries.py` — `MemoryEntryTool`, no `TOOLS` list yet (that list *is* the mount, see 1.2/1.12)
- [x] **1.2** Mount it — **on Dream, in `MemoryStore.build_dream_tools`**, not `_HARDCODED_TOOL_MODULES` (that list is the *main agent's* registry; the checklist said the wrong one). Carries the run's `FileStates` and the budget guard
- [x] **1.3** Actions `add(file, text)` / `replace(file, old_id|old_text, text)` / `remove(file, id|text)`
- [x] **1.4** Stable ids by **content hash**, never position
- [x] **1.5** Targets `USER.md` + `memory/MEMORY.md` only — `SOUL.md` stays file-edited
- [x] **1.6** No file-format change: entries are the existing bullets, files stay hand-editable
- [x] **1.7** Every call returns current usage **and** the entry list (Hermes' one-round-trip fix)
- [x] **1.8** `dream.md`: "inspect and make surgical edits" → "propose entries". Also rewrote the Budget paragraph, whose closing line (*"a run that only prunes is a run well spent"*) blessed the exact behaviour measured 6/6
- [x] **1.9** The evidence is now counted, not measured: `added` / `already_present` / `replaced` from the tool. `consolidation_landed` survives as a net for the file-tool path *(D8, D9)*
- [x] **1.10** `under_write_pressure` and `_PRESSURE_PCT = 90` are gone — `already_present` answers the question the threshold was guessing at
- [x] **1.11** File tools stay mounted for the review pass — `apply_patch`'s atomic move has no entry-tool equivalent. Pinned, along with the two registries not sharing counters
- [x] **1.12** **Decided 2026-08-18: Dream first.** A flaw found in a nightly run costs a replayed batch; the same flaw found mid-conversation costs the user's turn. The main agent gets it once the unattended path has run it *([open decision 4](./memory-plan.md#open-decisions) — closed)*

## Phase 1b — Batch on the action that produces evidence *(D10, found on device)*

- [x] **1b.1** `add` accepts `texts`, answering per item. One write for the batch; facts applied one by one so a refusal keeps what fitted
- [x] **1b.2** `dream.md`: propose the batch in one `add`; `list` is for finding an id to `replace`/`remove`; proposing a known fact is declared free
- [x] **1b.3** Regression at both levels: the tool reports each duplicate, and the cron path advances on `already_present`
- [x] **1b.4** Re-ran on device — the batched `add` is used, and the run revealed the deeper cause
- [x] **1b.5** `attempted` brake: hold only a run that tried to write *(the device's own answer)*

## Phase 2 — Demotion instead of deletion

Closes `D7`, defuses `D4`, removes the reason `D3` hurts.

- [x] **2.1** Cold-tier home: **`memory/archive/`** — path unused, inside snapshots, visible in the file browser, outside Dream's write allowlist *([open decision 2](./memory-plan.md#open-decisions) — closed)*
- [x] **2.2** Layout in `jenny/agent/memory_archive.py`: `YYYY-MM-DD-<id>.md`, frontmatter for the metadata, the fact alone as the body. Idempotent — a set of facts, not a log of events
- [x] **2.3** One file per entry, held by a test — `grep` skips large files silently, and that reads as "I never knew that"
- [x] **2.4** Demotion is **mechanical**: `remove` archives from inside the tool, before the file shrinks. The model is never given the path *(commitment 6)*
- [x] **2.4c** The entry tool writes through its own `_commit`, so the file-boundary hook is mounted there too — `replace` was silently discarding the old version *(D11, found on device)*
- [x] **2.4b** Pre-write archiver at the **file boundary** — `make_entry_archiver`, mounted on all four of Dream's tools. Whatever rewrites `USER.md` or `memory/MEMORY.md` now archives the entries it drops, `apply_patch` and the review pass included. **This is what closes D4**
- [x] **2.5** One flat line in the system prompt, suppressed while the archive is empty, and explicit that the model never writes there
- [x] **2.6** No retention policy — nothing prunes the archive, by omission and on purpose
- [x] **2.7** Confirmed **mechanically** and pinned end-to-end: a removal is never refused, costs nothing, and the whole remove→remove→add cycle lands the fact with every original still readable

> **Phase 2 verified on device, 2026-08-19** (release build of `0866162`, PID 464). A forced review
> pass — `reviewEveryRuns: 1`, `USER.md` over budget — pruned aggressively across two runs: three
> entries removed via `apply_patch`, seven reworded via `memory replace`, plus a whole-file
> `write_file`. **Ten entries reached the archive and nothing was lost**, checked line by line
> against the file as it was before. The model reports "10 voci in memory/archive/" from the
> prompt line alone, without opening anything (2.5). The first run is what found D11.

## Phase 3 — Enforce at injection, not at storage

- [x] **3.1** Measured 2026-08-20 on DeepSeek: **81.4% cache hit over 2,776 requests / 65.2M tokens**, and a +254-char Dream write cost **~17,900 uncached tokens** on the next turn — ~280× the edit, and *independent of the memory block's size* *([open decision 3](./memory-plan.md#open-decisions) — closed)*

> **The measurement argues against most of this phase.** The two files inject ~708 tokens, ~3% of
> the prompt, already under Hermes' 1,300 with no cap in force. The invalidation cost is everything
> *downstream* of the block, so capping its size buys nothing; moving it later would. And the whole
> prize is ~1% of daily tokens. Items 3.2–3.5 below are therefore **on hold pending a decision**,
> not scheduled work.
- [ ] **3.2** Storage caps become advisory; `write_size_guard` stops refusing
- [ ] **3.3** Hard injection cap at prompt build — follow `context.py::get_wiki_memory_context(max_tokens)`, the seam exists
- [ ] **3.4** Truncation is visible and structured: whole entries from a section tail, never mid-bullet, plus a marker the model can act on (`USER.md: 3 entries not shown, read the file`)
- [ ] **3.5** New config `memoryInjectionMaxTokens` / `userInjectionMaxTokens`, beside `atlas.maxContextTokens`

## Phase 4 — Stop paying for redundant extraction *(D5)*

- [x] **4.1** `MemoryStore.get_known_facts_context()` — the two hot files **and** the history tail past `.dream_cursor`, appended to the consolidator's system message. Both, not either: with the files alone the 99/100/101 case stays duplicated, because at the second extraction the files are still empty
- [x] **4.1b** The escape hatch that keeps memory updatable: a fact contradicting a recorded one is a `[correction]` and must be extracted; an addition to one is new. Stated **above** the list, since truncation eats the tail
- [x] **4.1c** The static template stops saying the opposite — "never `[skip]` on a *guess*" is true whether or not the block is there
- [x] **4.1d** Raw dumps filtered out (annotated fact lines only), and the block subtracted from the conversation's token budget
- [x] **4.2** Instrument: `N facts extracted, K already recorded shown, R verbatim repeats`. `R` is a lower bound by construction — a signal that the model is ignoring the block, not a ratio
- [x] **4.2d** On device: `/new` forces `archive()` on the unconsolidated tail — no waiting for a token overflow
- [x] **4.3** The queue is served **first**, with a share of its own — the cap was cutting exactly the source the plan calls dominant *(found on device)*
- [x] **4.4** Entries pack whole and the remainder is counted: half a fact under "already recorded" reads as a different fact *(found on device)*
- [x] **4.5** `_KNOWN_FACTS_MAX_TOKENS` 1200 → 1600, measured against the real block instead of summed from the file budgets

> **Phase 4 verified on device, 2026-08-19** (release builds of `31a9f8b` then `c3fd5cb`, PID 5770).
> Round 1: a fact restated verbatim from `MEMORY.md` **never reached `history.jsonl`**; the new
> fact beside it did. Round 2 caught the block over its cap — see the plan for the three defects
> behind it. `[correction]` survives both rounds, which is the one thing that had to not break.

## Phase 5 — Split `stuck` into two counters *(D6)*

- [x] **5.1** `stuck_runs` now counts **only** budget refusals — the one cause a review pass can act on; it still forces one
- [x] **5.2** `nothing_new_runs` — nothing landed, nothing refused. Logs at 4, forces nothing, alerts nobody: there is no cap to raise. Shown in `/dream budget` only when non-zero
- [x] **5.3** `format_stuck_alarm` names the cap again — it can, now that only one cause reaches it

## Phase 6 — Give the review pass its trust back

Unblocked by Phase 2: its mistakes are no longer permanent. What is left is telling it so, and
watching what it does with the permission — in that order.

- [x] **6.0** Floor reworded to **never lose** in both prompts — with `SOUL.md` held back at the hard floor, since nothing archives what leaves it
- [x] **6.0b** Shipped together, 6.2 written first
- [ ] **6.1** **Last, and only if the device justifies it:** bring `reviewEveryRuns` below 12. The documented floor of 6 exists only because deletion was terminal — but a faster cadence with a fresh permission is two changes at once
- [x] **6.2** A pass demoting more than 5 entries logs **what** it moved, not just how many; the count rides in `ReviewOutcome.demoted`

> **Phases 6.0 and 6.2 verified on device, 2026-08-19** (release build of `135fc48`, PID 3287).
> A forced review on a `USER.md` over its cap now **merges and demotes** where the same pass under
> the old wording only reworded: six entries left, four rewritten ones took their place, 181 chars
> freed against 109 before. All six are in the archive; **nothing lost**, checked line by line.
> 6.2 fired with the facts named: `Dream review demoted 6 entries to memory/archive/ in one pass:
> Preferisce le riunioni corte del mattino…`

## Phase 7 — Retrieval for the cold tier

Only once the archive has content, and in this order.

- [x] **7.1** `recall` tool — one line per archived entry, then the ids opened in full. **Not** `spawn`ed: that path is asynchronous by contract and hands the choice to a model that cannot see the conversation. Derived from the directory, never stored. Zero deps, selects on salience
- [x] **7.1b** The prompt line stops pointing at `grep` — it matches substrings, so it cannot find an Italian fact from an English question, and it skips large files silently
- [x] **7.1c** A cut list always says how many it did not show, and crowding the cap logs — that log is the measurement that decides when 7.2 is due
> **Phase 7.1 verified on device, 2026-08-20** (release build of `78b3b39`, PID 7271, 44 archived
> entries). `recall({})` fired **unprompted** on an English question about a fact recorded in
> Italian, alongside a wiki `grep` — the tool is discovered from the archive prompt line alone.
> Index: 44 of 44, nothing truncated, crowding log silent (~4k of 24k). `recall({"ids": [...]})`
> returned the entry with `from USER.md › Work, demoted 2026-08-19`. Two attempts to find a
> genuinely *absent* fact to ask about both failed: nearly the whole archive is superseded wordings
> of facts still in the hot files, so the tool's value is still latent.

- [ ] **7.2** Embeddings **only if** the index stops fitting a context: remote embeddings, binary quantisation, Hamming via big-int `XOR` + `int.bit_count()`. Do not build early

---

## Per-phase verification gate

Repeat for every phase — unit tests were necessary and **not sufficient**.

- [ ] `ruff check jenny/ tests/`
- [ ] `npx pyright jenny/bus jenny/command jenny/runtime jenny/session`
- [ ] `/tmp/py311/bin/python -m pytest -q` (device is 3.11; local `python3` is 3.14 and the gap has hidden a real bug)
- [ ] On-device run over the `adb forward` WS client, attributing results **by PID**, not by clock
- [ ] Read state with `su -c cat`; **never write workspace files as root**

Still never observed, and the thing the whole plan is for:

- [x] **A fact from a held batch landing after room is freed.** Seen 2026-08-19 on `c3fd5cb` (PID 6447): a refused write left `stuck_runs: 1` with the cursor held at 115 and the fact saved only in abbreviated form; the cap was raised, the batch replayed, and the full text landed — cursor 115 → 116, counter back to 0. `6a11ff2` is a repair.

> **Phase 5 verified on device, 2026-08-19**, in the same four runs. The budget refusal incremented
> `stuck_runs` and left `nothing_new_runs` at 0 — the discrimination the phase exists for. Two
> earlier attempts failed to reproduce a refusal at all: with demotion available, a file over its
> cap is now a cap the model routes around, which invalidates the premise the counter was built on
> and is Phase 2 working as designed.
