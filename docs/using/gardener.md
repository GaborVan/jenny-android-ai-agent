# The gardener

Capture, inside a [project](./projects.md), is deliberately cheap: a fact you mention becomes one line in that day's journal and nothing more. The **gardener** is what happens to those lines afterwards. It is a background pass that runs between conversations, reads the journal lines nobody has read yet, turns the ones that earn it into pages, and brings the map back in line with what now exists.

It is the third periodic worker after [Dream and Atlas](./memory.md), and like them it is **on by default**. Unlike them, it writes inside *your* folders rather than a derived file — which is why almost everything below is about what it will not touch.

## What one pass does

1. **Promote what earns a page.** A journal line that names something the project will keep talking about — a place, a person, a constraint, a decision — becomes, or joins, a page named after that thing. Several lines about one thing are one page, not three.
2. **Leave the rest.** Chatter, one-off logistics, a detail that only made sense that day: these pass through. A pass that promotes nothing has done its job correctly, and most passes are that.
3. **Link.** A page that names something with a page of its own links to it with `[[page-name]]`. Those links are what make the folder a wiki.
4. **Then fix the map**, last: `wiki/index.md` describes what now exists.

Every page it writes carries frontmatter: a `state:` of `open`, `hypothesis`, `decided` or `done`, and a `source:` pointing at the journal day the content came from — the trail from a page back to the sentence that caused it. A newly promoted line starts at `state: open`; only your own words, or a page that already says so, can justify anything stronger.

## When it runs

Three clocks have to agree before a pass starts, and then there has to be something to do.

| Clock | Default | What it means |
|---|---|---|
| Interval | every **30 min** | How often it *looks* for work. A tick that finds nothing makes no LLM call and costs nothing. |
| Required silence | **30 min** | How long that project's conversation must have been quiet. The gardener works on cold material: entering mid-conversation would promote half of a discussion and rewrite the map while you are reading it. |
| Distance between passes | **6 h** | How long before it comes back to the *same* project. This is the measured failure mode of Dream written as a number — a second close pass on one subject reworks what the first wrote instead of adding to it. Counted per project, and counted from *attempts*, not successes, so a project that keeps failing cannot monopolise every tick. |

On top of that:

- **One project per tick.** Every eligible project is collected and the least-recently-touched one runs; never-gardened projects come first. The others are named in the log and wait for the next tick.
- **A project with a turn in flight is skipped outright**, whatever the silence setting says.
- **One pass per project at a time.** A second pass on the same project is *refused*, not queued — two passes would overwrite each other's pages.
- **You always win.** The pass re-checks whether you are active in that project before *every* write. If you come back mid-pass it stands down, keeps whatever pages already landed, and leaves the journal marked unread so the next pass sees those lines again. (`/stop` in the project chat does not cancel a pass — the pass runs under its own session key. Coming back is the mechanism.)
- Being a periodic job, it only runs while Jenny is running — see [Scheduling and proactivity](./scheduling.md).

There is a **second reason** a pass can start, with no new journal lines at all: the project's map has grown past its 2,000-character ceiling *and* is bigger than the last pass left it. Then the gardener goes in for the map alone, moving prose out to the pages it belongs to. If a prune leaves the map still over the ceiling, it is not retried until the map grows again — a reason that stays true after a pass would otherwise loop forever.

Each pass reads at most **200** unread journal lines; if there are more, the overflow is disclosed in the pass's own prompt and picked up next time.

## What it may write, and what it must not touch

| Path | The gardener |
|---|---|
| `<project>/wiki/` — pages and the map | **writes.** This is the whole writable surface. |
| `<project>/raw/journal/` | appends only, and only to recover a fact you said that capture missed. Never rewrites a line. |
| `<project>/AGENTS.md` | refused — those are the project's premises, and they change when *you* change them, in conversation. |
| `<project>/raw/`, `<project>/audit/` | refused — `raw/` is verbatim by definition, and `audit/` is your channel, not its. |
| `<project>/log/` | written by the code, not by the model: the pass records itself. |
| Anything else in the installation | refused. It cannot even read outside the project it was given. |

And these rules govern what it does inside `wiki/`:

- **Add and promote; do not rewrite.** On a page that already exists it appends, or it changes the `state:` — it does not re-word body text that was already right. Rewriting is how a wiki decays one careful pass at a time.
- **A page that outgrows the budget is split, not shortened.** Past 6,000 characters a page stops entering conversations entirely, so it is cut along the things it talks about: each part becomes a page named after its own thing, carrying the sentences that were already about it, moved word for word. The original keeps its name and one part and links to the others. Nothing is deleted and no sentence is re-worded — which is why a split counts as a promotion rather than a rewrite.
- **Never delete a page, or a section of one.** A split (and a map prune) move text verbatim into a page the original links to; that is the only exception, and it is not a deletion.
- **A workspace snapshot is taken before every pass**, tagged `pre_gardener`, so a pass that made a mess is recoverable from the [snapshot](./backup.md) list. Two honest limits: "never delete a page" is a prompt rule and not enforced by the toolbox — a page overwritten with nothing would succeed — and if the snapshot itself fails, the pass runs anyway and says so in the log rather than refusing to work.
- **It does not settle contradictions.** If two pages disagree, or a journal line denies a page marked `decided`, it leaves both alone and writes the question into the map's **Open** section — one line, naming both pages. That section is read at the start of every conversation in that project, so the question reaches the person who can answer it. Deciding it itself is the one thing it must not do.

## What a pass can see

A pass is given: the unread journal lines, the map, the list of pages that exist (marked with which of them are already over the page budget), your recent messages in that project's conversation, and Jenny's identity files (`SOUL.md`, `USER.md`, `memory/MEMORY.md`).

It is **not** given the cross-project wiki directory (`memory/WIKI.md`) or the tail of your personal conversation. Both used to arrive and were closed deliberately: the choice of project has already been made, and a maintenance pass with no user to talk to has no business carrying either your private life or an inventory of your other projects into a page you will read. Jenny's identity stays, because the pass writes prose you read and the alternative is the one actor with no idea who it is writing for.

The transcript it sees is a window — the recent stretch, capped — not the whole record.

### Recovering what capture missed

Capture happens live, mid-conversation, and it misses things. So a pass also compares what you *said* over the recent stretch against what the journal actually recorded, and looks for one stable fact that never made it in. If it finds one it appends a single journal line, marked `[recovered]`, and the next pass promotes it like any other line.

It recovers the *line*, never the page — and it is told to leave anything it is unsure about, because the journal it can see is a window: a fact it cannot find there may simply have been recorded on an older day, and a duplicate line becomes a second page or a page that argues with itself.

## What it tells you

Normally nothing — the folder is the output, and a pass that wrote nothing leaves no trace at all. Beyond that:

- Something it could not settle goes into the map's **Open** section (which every conversation in that project reads) *and* as one line in the project's `log/`.
- After **three** consecutive passes that failed to record any progress, you get a notification: *"The gardener has failed N passes in a row on 'X': its journal is not becoming pages. Run /gardener X to see the error."* It costs no tokens and does not depend on the model, which in that situation may be exactly what is broken — and it repeats on every further failed pass rather than only at the crossing.

## Running one by hand: `/gardener`

`/gardener` replies immediately with `Gardening <project>...` and posts the outcome when the pass finishes.

| Form | What it does |
|---|---|
| `/gardener` | One pass on the project you are in, now. Outside a project it refuses and tells you to name one. |
| `/gardener <project>` | One pass on a named project. If there is no `wikis/<project>/wiki/`, it says so. |
| `/gardener settings` | Shows how the periodic pass is set: on/off, the three numbers with their ranges and meanings, the history-compaction state, and the valid forms. |
| `/gardener off` / `/gardener on` | Stops or starts the periodic pass. Takes effect immediately — no restart. |
| `/gardener interval <min>` | How often it looks for work. 1–1440. |
| `/gardener idle <min>` | Silence required in that project before a pass. 0–1440; `0` lets a pass start while you are talking. |
| `/gardener distance <h>` | Minimum gap between two passes on one project. 0–8760; `0` lets it come straight back. |
| `/gardener compact on\|off` | Whether a project's chat history is archived once it goes idle. **This one needs a gateway restart.** See [Projects](./projects.md#history-and-when-it-gets-compacted) before turning it on. |

The outcome message distinguishes the ways a pass can do nothing — nothing new in the journal (no tokens spent), nothing that earned a page (journal marked read), finished without writing (journal left unread) — from the two ways it can do half the job: some writes refused, or pages written but the read-position not recorded. In both of those the pages are on disk and the journal is deliberately left unread, so the next pass sees those lines again. Re-promoting is safe by design; losing a line is not.

Every write goes through the config write funnel, so a value you set is read back and kept, and a value that is already what you asked for does not rewrite `config.json` at all. Out-of-range values are refused with the range named, and the refusal says which alternative is the reversible one.

A project genuinely named `settings`, `off`, `on`, `compact`, `interval`, `idle` or `distance` is shadowed by these forms; `/gardener` from inside it still works.

## Turning it off

```text
/gardener off
```

That is the whole escape hatch, and it applies live — the periodic pass stops looking. `/gardener` and `/gardener <project>` still work by hand, so turning it off is not the same as losing the feature; and `/gardener on` re-arms the periodic job without a restart.

Turning the gardener off does **not** turn off capture: journal lines keep being written by the conversation itself, and they simply wait until a pass — periodic or manual — reads them. Capture is governed by the Writes/Read-only switch, not by this.

## Configuration reference

Under `agents.defaults.gardener` in `config.json`, all four settable from `/gardener`:

| Key | Meaning | Default | Range |
|---|---|---|---|
| `enabled` | Register the periodic pass | `true` | — |
| `intervalMin` | How often it looks for work | `30` | 1–1440 |
| `idleMin` | Silence required in that project | `30` | 0–1440 |
| `minHoursBetweenPasses` | Gap before returning to one project | `6` | 0–8760 |

Plus one sibling under `agents.defaults`:

| Key | Meaning | Default |
|---|---|---|
| `compactProjectsWhenIdle` | Archive a project's chat history once it goes idle, like the personal one | `false` |

An out-of-range number in a `config.json` written by an older version is **clamped to the bound**, not rejected — a stricter schema that refused it would quarantine the whole file and take your provider settings with it. There is no Settings-screen control for any of this; `/gardener` is the surface.

## See also

- [Projects](./projects.md) — what a project is, capture, the map and the pages, and the history fence.
- [Memory, Dream and Atlas](./memory.md) — the other two periodic workers, and the personal memory files.
- [Wiki](./wiki.md) — reading and auditing the pages the gardener writes.
- [Configuration (config.json)](../reference/configuration.md) — the full config reference.
