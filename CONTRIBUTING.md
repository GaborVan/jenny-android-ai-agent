# Contributing to Jenny

Thanks for your interest in contributing! Jenny is young and moving fast, so
a few simple rules keep things sane for everyone.

## Before you start

- **Bugs**: open an issue with steps to reproduce, your device/Android
  version, and the AI provider you are using.
- **Features**: check the existing issues first. For anything non-trivial,
  open an issue to discuss it *before*
  writing code — it saves you from building something that can't be merged.
- **Small fixes** (typos, docs, obvious bugs): just send the PR.

## Developer Certificate of Origin (DCO)

All contributions require a DCO sign-off. This is a simple statement that you
have the right to submit the code you are contributing, under the
[Developer Certificate of Origin](https://developercertificate.org/).

To sign off, add the `-s` flag when committing:

```
git commit -s -m "Fix reminder scheduling on Android 15"
```

This appends a line like:

```
Signed-off-by: Your Name <your@email.example>
```

The sign-off has to match the commit's own author. Pull requests with unsigned
commits fail the `dco` job in CI (`scripts/check_dco.sh`, run by
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)) and cannot be merged.

You can run the same check yourself before pushing:

```
scripts/check_dco.sh origin/main HEAD
```

If you forgot, fix the last commit with `git commit --amend -s` (or
`git rebase --signoff origin/main` for several commits) and force-push your
branch.

## Licensing of contributions

By contributing, you agree that your contributions are licensed under the
project's license, **AGPL-3.0** (see [LICENSE](LICENSE)). Note that the
project name and logo are covered by a separate
[trademark policy](TRADEMARK.md).

## Code guidelines

- Keep PRs focused: one change per PR.
- Match the existing code style of the file you are touching.
- If your change affects security boundaries (workspace sandbox, network
  guards, credential handling), call it out explicitly in the PR description.
- Test on a real device when possible, and say which one.

## Conduct

Be kind, assume good faith, keep discussions technical. The maintainer's
decision on scope and merging is final — forks are always an option, that's
what the license is for.

The full rules, and how to report a problem privately, are in
[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).
