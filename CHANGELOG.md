# Changelog

All notable changes to Jenny are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
at [Semantic Versioning](https://semver.org/spec/v2.0.0.html) for the versions
that reach Releases — each release page carries its own notes, and this file
carries the reasoning that outlives them.

## [Unreleased]

### Added

- **The app can register itself as an OpenClaw node.** A new Settings → OpenClaw node
  section points Apex at an OpenClaw Gateway (`wss://…` or an `oc-pair://` setup link),
  connects with an Ed25519 device identity, and — once the operator approves the pairing
  and allowlists the commands — serves four node commands: `ui.dump` (the accessibility
  tree of the current screen), `clipboard.get` / `clipboard.set`, and `voice.speak`
  (through the existing Android TTS). The identity and the issued device token are
  persisted, so a restart reuses the same pairing instead of minting a new request.
  `ui.dump` and clipboard reads require the accessibility service / foreground rules the
  node page documents. Config keys: `openclaw_node.*`.

- **The OpenClaw node page says what is missing, and the service has a name.** The
  section now shows three status lines — node connection, screen reading, clipboard —
  and a button that opens Android's Accessibility settings directly. The hint names the
  service exactly as the system shows it (`«Керування інтерфейсом Apex»` / “Apex UI
  control”), and a disabled service is reported with the command it blocks (`ui.dump`).
  New routes: `GET /api/settings/accessibility/status`, `GET /api/settings/accessibility/open`.

- **Agent replies follow the device language and stay short by default.** A new identity
  block tells the model which language to answer in and to keep replies brief; both are
  configurable (`agents.defaults.languageInstruction`, `agents.defaults.conciseReplies`).
  The default language is now *auto*: resolved at load from the device locale (Ukrainian
  → `uk`, otherwise the previous `it`). An existing `language: "it"` is migrated to auto
  once (config schema v2).

- **The home bridge is configured out of the box.** A fresh install now starts
  with a single provider — the Tailscale bridge, `tier: local`, no key — already
  saved and already active, so the first message does not require pasting a long
  `.ts.net` URL into Settings by hand. The seed runs once, in
  `config/bootstrap.py`, and only when the config has never carried a
  `providers` key: a config that already has providers is left untouched, and a
  provider the user deleted is never resurrected (deleting leaves the key with
  an empty list, and that is a decision, not a gap). The seeded profile is an
  ordinary `ProviderConfig` — editable and deletable like any other. Covered by
  `tests/config/test_default_provider_seed.py`, including an end-to-end
  `make_provider` call and a check that the seeded URL matches the UI preset.
- **Voice input.** A Telegram voice note is now transcribed on the phone and
  delivered to the agent as text, with the audio still attached, and a new
  `transcribe_audio` tool does the same for an audio file already in the
  workspace. The engine is Android's own `SpeechRecognizer` — no key, no model
  shipped, no service of ours — behind a new Kotlin bridge (`SttBridge.kt`) that
  decodes the audio on the device with `MediaExtractor`/`MediaCodec` and feeds
  the recogniser through `EXTRA_AUDIO_SOURCE` (Android 13+; below that the
  refusal names the reason). This is a deliberate re-entry of voice input, which
  `FORK_BOUNDARY.md` listed as removed — what was removed is the *Python*
  transcription stack, and that is still gone; `FORK_BOUNDARY.md` now carries
  both halves. Gated by `voice.enable` (default `true`) and
  `voice.prefer_offline` (default `false`). No new permission, and no
  microphone: it reads files.
- **Provider tiers.** `ProviderConfig.tier` (`api` | `subscription` | `local`)
  says how an endpoint is paid for, and the one behaviour it changes is that a
  `local` or `subscription` profile no longer needs an API key — pointing Jenny
  at a bridge for a CLI subscription (or at a model server on your own machine)
  used to require inventing a dummy key. It is in the provider dialog as *How
  it's paid*, surfaced by the settings API, and documented with a worked config
  in [docs/reference/providers.md](docs/reference/providers.md).
- **A preset for the home bridge, and a diagnosis for its failures.** `Add
  provider` opens with a **Home bridge (Tailscale)** preset
  (`jenny/providers/presets.py`): it fills the tailnet base URL, picks
  `tier: subscription` so the token stays optional, and names the models the
  bridge exposes — while leaving every field editable, so the saved entry is an
  ordinary provider. A failed call to a `.ts.net` endpoint is classified rather
  than pasted raw (`jenny/providers/endpoint_errors.py`): no response at all
  says to enable Tailscale and MagicDNS, `401` says the token was rejected, and
  `502/503/504` says the CLI behind the bridge is not answering (usually an
  exhausted session limit). The model list from `GET /v1/models` is read by a
  single parser (`jenny/providers/model_listing.py`) that the Settings model
  probe now shares. Documented in
  [docs/reference/providers.md](docs/reference/providers.md#connecting-through-tailscale-home-bridge).
- **Voice output.** The agent can read text aloud on the phone through the
  system Android `TextToSpeech` engine: two tools, `speak` (text, optional
  language tag, optional rate) and `stop_speaking`. Nothing leaves the device and
  no permission is involved. An unavailable engine or an unsupported language
  comes back as `{"ok":false,"error":…}` with a hint, never as a crash, and the
  tool description tells the model that failure is not worth retrying in a loop.
  Gated by `tools.tts.enable` (default `true`); no Settings-screen switch.
  See [Tools](docs/reference/tools.md) and
  [Configuration](docs/reference/configuration.md).
- `CHANGELOG.md` — this file.
- `tests/test_android_requirements_match_pyproject.py` — pins the invariant that
  the runtime dependencies in `pyproject.toml` and the lines of
  `requirements-android.txt` are the same set. Until now that was only a comment
  in the requirements file, and a drift means CI tests a runtime the APK does
  not ship.

### Changed

- **CI signs the APK with the release key, so updates install over the previous
  build.** Every CI run used to mint a fresh debug keystore, and Android refuses
  an update whose signer differs — which is why each release required an
  uninstall first. `build-apk.yml` now materialises the release keystore from
  the `KEYSTORE_BASE64` secret into the runner's temp dir and builds
  `assembleRelease` when the four signing secrets are present, falling back to
  the debug build when they are not, so a fork without secrets still produces an
  APK instead of failing. `android/app/build.gradle.kts` already read the
  credentials from `JENNY_KEYSTORE_*`; the workflow is what now supplies them.
  The signing certificate is reused, not regenerated: an update only installs
  over builds carrying the same certificate.
- **Privacy documentation updated for voice.** Recognising speech sends the
  audio to whichever engine the phone has installed, and that engine may upload
  it: [Privacy](docs/internals/privacy.md) now lists it as the sixth data
  recipient, and the README's outbound-connection count went from six to seven.
  The in-app microphone button is unaffected — that path was already there.
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
