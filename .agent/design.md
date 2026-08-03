# Design Constraints

These rules govern architectural decisions. When adding a feature or fixing a bug, prefer paths that respect these boundaries.

## Core stays small; extend at the edges

New capabilities should be added via `channels/`, `tools/`, or skills. The files `agent/loop.py` and `agent/runner.py` form the critical core path; changes there should be minimal and justified. If a feature can live in a channel adapter, a tool, or a skill, it should not be inlined into the agent loop.

Runtime state fan-out follows the same boundary. `AgentLoop` may publish generic runtime events from `jenny.bus.runtime_events` for turn/run/model/goal state changes, but WebUI/WebSocket wire details such as `_turn_end`, `_goal_status`, title refreshes, and goal-state sync belong in `jenny.session.webui_turns.WebuiTurnCoordinator` or the relevant channel adapter.

## Less structure, more intelligence

Prefer simple, readable code over new framework layers and indirection. Add structure only when it removes real complexity, protects an important boundary, or matches an established local pattern. The best fix is often a smaller prompt, a tighter tool contract, a channel-local change, or one focused regression test.

## Prefer duplication over premature abstraction

Channels and providers are allowed to repeat similar logic (send retries, media handling, message splitting). Do not introduce complex base classes or shared helpers just to eliminate duplication across channel files. Each channel file should remain self-contained and readable on its own. The same applies to provider implementations.

## Minimal change that solves the real problem

Fix bugs by changing only what is necessary. Do not bundle unrelated refactors or clean-ups into a feature or bugfix PR. If a refactor is genuinely required, it should be a separate, clearly scoped PR.

## Keep PRs reviewable

A bugfix should make the protected invariant clear, change the smallest surface that enforces it, and add only the closest regression test. If a diff starts changing ownership boundaries or mixing behavior changes with clean-up, split it before it becomes hard to review.

## Explicit over magical

Configuration must be declared explicitly in `config/schema.py` Pydantic models. Error handling should raise clear exceptions rather than silently correcting bad input. Provider auto-detection exists, but every resolution path must be traceable from the factory to the concrete provider class.

One deliberate exception, worth knowing so it doesn't read as a lapse: loading `config.json` does **not** raise on an unusable file. It falls back to the `.bak`, or sets the file aside and starts on defaults. Raising was the honest choice right up until you remember where this runs — on a phone, a config the gateway refuses to load is an app the user cannot start *and* cannot repair, because the file lives in storage they cannot reach. The principle is kept where it can be: the fallback is loud (ERROR + WARNING in the log, a notice in Settings, the broken file preserved), never silent. Correcting bad input without telling anyone is still forbidden.
