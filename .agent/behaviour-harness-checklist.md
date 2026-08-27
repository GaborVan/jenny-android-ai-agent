# Behaviour Harness — Working Checklist

Execution list for [`behaviour-harness-plan.md`](./behaviour-harness-plan.md). That file holds the
reasoning; this one holds the state. Tick a box only when it has actually run — and for a property,
"run" includes its calibration, because a check that has never failed proves nothing.

Branch: `jenny-memory`.

> **Nothing here is scheduled.** The harness is designed and deliberately unbuilt — the four probes
> are run by hand from [`memory-probes.md`](./memory-probes.md). See the status note at the top of
> the plan for why, and build Stage 1 first if hand-running starts getting skipped.

---

## Stage 0 — Scaffolding

- [ ] **0.1** `scripts/behaviour_check.py` — entry point, `--config <path>` for the provider, `--only <property>`, `--runs N`
- [ ] **0.2** Synthetic workspace builder: invented people, invented facts, nonce tokens, always under `tmp` *(never the user's workspace, never the device)*
- [ ] **0.3** Report writer: per-property rate, raw model output for every run, token cost
- [ ] **0.4** Baseline file committed to the repo; the harness diffs against it and prints the delta, not a verdict
- [ ] **0.5** Decide `N` from the spread of the first calibration run *([open decision 2](./behaviour-harness-plan.md#open-decisions))*
- [ ] **0.6** `--config` may point at **any** provider; the report names the model that produced each rate. Nothing in a fixture or assertion may depend on a provider or a model *([open decision 1](./behaviour-harness-plan.md#open-decisions) — closed)*
- [ ] **0.7** Run every property against **at least two different models**. Uniform pass means the prompt carries the guarantee; pass on one and fail on another means it lives in the reader, and that is a defect to fix before shipping

## Stage 1 — The consolidator's promises *(cheapest, and the newest guarantee)*

Needs only `Consolidator.archive()`: provider + store, with `sessions` / `build_messages` /
`get_tool_definitions` stubbed. No agent, no loop, no bus.

- [ ] **1.1** **P1** — a batch of pure restatement extracts nothing: no planted nonce in the output, at most one non-`[skip]` line
- [ ] **1.2** **P2** — the same batch plus one new fact: the new nonce appears, the old ones still do not *(P1's control — without it, a dead consolidator scores perfectly)*
- [ ] **1.3** Calibrate P1: suppress the known-facts block, confirm the rate collapses
- [ ] **1.4** Record the baseline for P1 and P2

## Stage 2 — Dream frees space instead of taking a refusal

- [ ] **2.1** Stand up a Dream turn outside the two existing call sites (`runtime/cron_dispatch.py`, `command/builtin.py`)
- [ ] **2.2** **P3** — `MEMORY.md` over its cap, a batch carrying a nonce: the nonce lands, `unrecovered_refusals == 0`, the cursor advances
- [ ] **2.3** Calibrate P3: restore pre-Phase-2 behaviour (`remove` without archiving) and confirm the refusals return
- [ ] **2.4** Record the baseline
- [ ] **2.5** **Decide, with the duplication in hand:** is one shared Dream-turn callable worth extracting? *([open decision 3](./behaviour-harness-plan.md#open-decisions))*

## Stage 3 — `recall` across a language boundary

- [ ] **3.1** Stand up a main-agent turn against a synthetic workspace
- [ ] **3.2** **P4** — one archived entry in Italian carrying a nonce, an English question that does not name it: `recall` is called, the nonce reaches the reply
- [ ] **3.3** Calibrate P4: point the archive prompt line back at `grep`, confirm the tool stops being called
- [ ] **3.4** Record the baseline

## Once it exists

- [ ] **4.1** Run it, then move the `# Memory` block to the end of the system prompt and run it again — the token side is already measured (~17,900 uncached per Dream write, [memory-plan 3.1](./memory-plan.md#31-the-measurement--done-2026-08-20-and-it-argues-against-most-of-this-phase)); this is the quality side, which is the half that has been missing
- [ ] **4.2** Note in `CONTRIBUTING.md` that a prompt edit under `jenny/templates/agent/` is a reason to run this

---

## Gate for every property

- [ ] It has been made to **fail** by removing its guarantee, before its baseline was recorded
- [ ] Its assertion reads a **nonce**, not a phrasing — and nothing in it is specific to a provider or a model
- [ ] It runs against a workspace under `tmp` with invented facts — safe to publish, safe to lose
- [ ] The report carries the raw model output, so a red run is diagnosable without re-running it
