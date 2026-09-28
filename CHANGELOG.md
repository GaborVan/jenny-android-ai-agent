# Changelog

All notable changes to Jenny are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
at [Semantic Versioning](https://semver.org/spec/v2.0.0.html) for the versions
that reach Releases — each release page carries its own notes, and this file
carries the reasoning that outlives them.

## [Unreleased]

### Added

- **Voice output.** The agent can now read text aloud on the phone through the
  system Android `TextToSpeech` engine: two new tools, `speak` (text, optional
  language tag, optional rate) and `stop_speaking`. Nothing leaves the device and
  no permission is involved — synthesis uses whatever TTS engine the phone
  already has. An unavailable engine or an unsupported language comes back as
  `{"ok":false,"error":…}` with a hint, never as a crash, and the tool
  description tells the model that failure is not worth retrying in a loop.
  Gated by `tools.tts.enable` (default `true`); no Settings-screen switch.
  See [Tools](docs/reference/tools.md) and
  [Configuration](docs/reference/configuration.md).
- `tests/test_android_requirements_match_pyproject.py` — pins the invariant that
  the runtime dependencies in `pyproject.toml` and the lines of
  `requirements-android.txt` are the same set. Until now that was only a comment
  in the requirements file, and a drift means CI tests a runtime the APK does
  not ship.

### Changed

- **Linting raised** from `E, F, I, N, W` to `E, F, I, N, W, B, C4`
  (`flake8-bugbear` and `flake8-comprehensions`). Every resulting finding in
  `jenny/` and `tests/` was fixed rather than silenced: explicit `strict=` on
  `zip()` calls, precise exception types where a test asserted a blind
  `Exception`, `raise … from …` where an exception was being replaced, and
  immutable `ContextVar` defaults in place of a shared mutable `{}`.
- CI: pip downloads are cached in both the `lint` and `test` jobs; `pip-audit`
  runs as a real gate over the installed runtime dependencies in the `lint` job;
  a new **non-blocking** `coverage` job reports coverage for the suites that do
  not touch the `python_exec` sandbox. The blocking `test` job is still run
  without `--cov`, deliberately — see the comment above it in
  [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
