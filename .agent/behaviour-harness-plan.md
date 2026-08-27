# Behaviour Harness — guarding the promises that live in prompts

Companion to [`memory-plan.md`](./memory-plan.md), which is where the need was found. That plan
holds the memory work; this one holds the reasoning for the thing that watches it. Execution state
is in [`behaviour-harness-checklist.md`](./behaviour-harness-checklist.md).

> **Status: designed, deliberately unbuilt (2026-08-21). This is an option, not a queue.**
>
> The four probes are run **by hand** instead — [`memory-probes.md`](./memory-probes.md) — and that
> is the right call at current stakes. Phase 2 of the memory plan made every one of these failures
> non-destructive: a broken promise here wastes tokens or gives an annoying answer, it does not lose
> anybody's facts. Against that, a hand run costs minutes and the automation costs three stages of
> scaffolding for something that gets run perhaps four times a year.
>
> What was actually missing was never the automation — it was that **nobody had written down what to
> check**. `memory-probes.md` fixes that for an hour's work and no ongoing cost.
>
> Build this if hand-running becomes the thing that gets skipped. Start with Stage 1, which needs no
> agent and guards the newest promise. Everything below stays valid; only the urgency was wrong.

## The problem, stated exactly

The memory work closed eleven defects and shipped six phases. Four of the guarantees it shipped are
not mechanisms. They are **requests written in a prompt**, and they hold because the model chooses
to honour them:

| guarantee | where it lives | observed working |
|---|---|---|
| the consolidator does not re-extract what memory already holds | `consolidator_archive.md` + the injected known-facts block | twice, by hand, 2026-08-19 |
| Dream frees space instead of taking a refusal | `dream.md` budget paragraph | four runs, 2026-08-19 |
| the review pass demotes rather than empties | `dream_review.md`, the fifth step | one forced pass, 2026-08-19 |
| `recall` is reached for on a recall-shaped turn | the `## Archive` line in the system prompt | once, unprompted, 2026-08-20 |

Every one of those observations was a person watching a log. None of them is checked by anything.

The failure mode is specific and it is not a crash. A model changes, or a prompt grows and the
paragraph that carried the request drifts into the middle of forty others, and the guarantee stops
holding. **Nothing goes red.** `ruff` passes, `pyright` passes, 6,731 tests pass, and Jenny quietly
starts re-extracting facts she already knows, or answering "I don't remember" over a full archive.
The only detector today is somebody noticing months later.

Phase 2 shows what the alternative looks like: archiving happens in the runtime, inside the tool,
before the file shrinks. The model cannot skip it because it is never asked to. That is the right
shape and it is not always available — a *retrieval* decision genuinely has to be the model's. So
the answer is not "make everything mechanical". It is: **when a guarantee has to be a request, put
a detector on it.**

## The organising idea

**The harness does not pass or fail. It notices a change.**

A pass/fail threshold on a stochastic system has two settings and both are bad: tight enough to be
meaningful and it flaps, loose enough to be quiet and it never fires. Either way it gets ignored,
and an ignored check is worse than no check because it reads as coverage.

So each property is run N times and the harness records a **rate**, compared against a **baseline
committed to the repo**. `4/5 → 4/5` is silence. `5/5 → 1/5` is the signal, and it points at
exactly which promise stopped being kept. The number in the baseline file is not a target anyone
should tune towards; it is a measurement of how the current model behaves, kept so the next one can
be compared to it.

## Four design choices, and what each rules out

**Not in `pytest`, not in CI.** Every test in `tests/` is offline today and that must stay true:
CI has no key, and provider calls cost money and time. More decisively — a flaky test in a CI suite
gets marked skip within a fortnight, which converts a real signal into a lie about coverage. This
lives in `scripts/`, is run deliberately, and its output is a report, not an exit code.

**Not exact-output assertions.** The model rewords. Pinning phrasing produces failures that mean
nothing, and a harness that cries wolf is a harness nobody reads.

**Not an LLM judge.** It is the obvious way to check a semantic property, and it is wrong here: it
adds a second model-dependent component to a harness whose entire purpose is to detect
model-dependence. When the judge drifts, the harness lies in the direction that is hardest to
notice.

**Nonce tokens instead.** Each fixture carries an invented word — `zafkril`, `brindolo` — planted
where the property can be read off a substring check the harness controls. Wanting to know whether
a known fact was re-extracted becomes: *is the nonce that is already in `USER.md` present in the
extraction?* Exact, cheap, and immune to rewording. This is the one trick the whole design rests on.

## Model-agnostic, and why that is the whole point

A first draft of this plan proposed keying baselines to a model id and running the harness against
whichever model the device happens to use. Both are wrong, and the reason is worth writing down
because it is the difference between a smoke alarm and a thermometer.

**A promise that only one model keeps is not a promise.** These four guarantees are properties the
*prompt* is supposed to have. If the consolidator stops re-extracting only because one particular
model is unusually obedient, then nothing has been engineered — a fragile prompt has been paired
with a forgiving reader, and the day the reader changes, the fragility surfaces as a mystery.
Testing against the model in production would confirm the pairing and say nothing about the prompt.

So: **nothing in a fixture or an assertion may depend on a provider or a model.** No provider-shaped
parsing, no thresholds tuned to one family's verbosity, no assertion that reads a quirk. The
properties are counts, planted nonce words, and whether a tool was called — all three are things any
competent model can be asked for and none of them is anybody's dialect.

The harness is meant to be **pointed at more than one model**, and the interesting outcome is
disagreement. Uniform pass means the prompt carries the guarantee. Pass on one and fail on another
means the guarantee lives in the reader, not in the text — which is a defect in the prompt, found
before it ships rather than after a model swap. That result is unavailable to a single-model
harness, and it is the most valuable thing this design can produce.

A recorded rate therefore names the model that produced it, the way any measurement names its
instrument. That is not the same as designing around one.

## The four properties

Each is a guarantee above, turned into something with a setup, an assertion and a cost.

**P1 — a batch of pure restatement extracts nothing.** A synthetic workspace whose `USER.md` and
`MEMORY.md` hold facts carrying nonces; a conversation chunk that restates them and adds nothing.
Assert: **no planted nonce appears** in the extraction, and the count of non-`[skip]` lines is at
most one. Guards Phase 4.

**P2 — a new fact still gets through.** The same chunk plus one genuinely new fact carrying its own
nonce. Assert: **that nonce appears**, and the old ones still do not. P1 alone is satisfied by a
consolidator that has stopped working entirely, which is why P2 is not optional.

**P3 — an over-budget file gets pruned, not refused.** A workspace with `MEMORY.md` above its cap
and a batch carrying a nonce fact. Run a Dream cycle. Assert: the nonce is in `MEMORY.md`,
`unrecovered_refusals == 0`, and the cursor advanced. This is yesterday's hand-run experiment,
mechanised.

**P4 — `recall` is reached for across a language boundary.** A workspace with one archived entry
written in Italian carrying a nonce, and a user turn in English that asks about it without naming
it. Assert: `recall` is among the tool calls, and the nonce reaches the reply. Guards both the
tool's discoverability and the archive prompt line.

## Staging, by wiring cost

The four are not equally expensive to stand up, and the cheap half covers the guarantee that is
both newest and least observed.

- **Stage 1 — P1 and P2.** `Consolidator.archive()` touches only the provider and the store;
  `sessions`, `build_messages` and `get_tool_definitions` can be stubs. A provider comes from
  `providers/factory.py::make_provider(config)`. No agent, no loop, no bus.
- **Stage 2 — P3.** Needs an object that can run a Dream turn. `begin_dream_cycle` /
  `finish_dream_cycle` take an `agent`, and the surrounding loop is written twice —
  `runtime/cron_dispatch.py` and `command/builtin.py`. Standing this up will show whether that
  duplication is worth collapsing; it is a fair second question, not a prerequisite.
- **Stage 3 — P4.** Needs the main agent loop, because the property *is* "the model chose to call
  the tool". Heaviest, and the only one that cannot be approximated by something smaller.

Ship Stage 1 alone if that is all there is time for. It is a real detector on a real promise.

## Calibration — the part that makes it worth anything

**A check that has never failed proves nothing.** Every property must be shown to go red when its
guarantee is taken away, before the baseline is recorded:

- P1: suppress the known-facts block, confirm the rate collapses.
- P2: it is P1's control and needs none.
- P3: restore the pre-Phase-2 behaviour (`remove` without archiving) so the model has nowhere to
  put what it prunes, and confirm the refusals return.
- P4: point the archive prompt line back at `grep`, and confirm the tool stops being called.

A property that cannot be made to fail is not measuring what its name claims, and gets deleted
rather than shipped green.

## Safety

- **A synthetic workspace under `tmp`, always.** Never the user's workspace, never the device. The
  fixtures are invented people with invented facts, which is also what makes them publishable in a
  public repo.
- **The key is passed in, never committed.** The harness takes a path to a config that has one and
  builds the provider from it; nothing about credentials is stored here.
- **The report records token cost**, because a tool that quietly spends money gets run less than it
  should be, and one that says what it spent gets trusted.

## What this is not

Not a quality eval, not a benchmark, not a model comparison, and not a gate on merging. It answers
exactly one question — *are the four promises still being kept?* — and it is allowed to answer
"yes, still" every single time for a year. That is what a smoke alarm does.

## Open decisions

1. ~~**Baseline granularity** — one rate per property, or per property and model?~~ **Closed
   2026-08-21, and the question was the wrong one.** See *Model-agnostic, and why that is the whole
   point* above: a baseline records which model produced it, and disagreement between models is a
   result rather than an inconvenience.
2. **N.** 5 runs per property is the guess. It should be chosen from the observed spread of the
   first calibration run, not before it — and the spread is likely to differ between models, so the
   number may end up per-model even though nothing else is.
3. **Whether P3's staging justifies collapsing the two Dream loops** into one callable. Decide
   after Stage 1, with the duplication actually in hand.
