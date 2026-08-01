# Is Jenny for you?

A short, honest checklist before you spend time installing it.

## Probably yes, if

- You want an assistant that actually remembers you — not a chat window that forgets everything the moment you close it.
- You have a spare Android phone doing nothing, and like the idea of turning it into an always-on AI device (see [Set it as your launcher](launcher-setup.md) for the "dedicated device" option).
- You'd rather hold your own API key and pay your provider directly than rent another subscription.
- You enjoy reading the source of things you run, or at least want the option to.
- You're fine with a sideloaded app: no Play Store, no App auto-updates, checking back for new releases yourself.

## Probably not, if

- You want a polished one-tap install today. Jenny is a pre-release prototype: it works, but onboarding has rough edges and things are still being tightened.
- You're on iOS. Android is the only supported runtime — this is not a temporary gap, the whole design assumes a device you own that stays powered on.
- You want a feature-complete launcher. Jenny can act as your Android home screen, but it has no widgets, no folders, no icon packs, no wallpaper management. The launcher part is a means to being present, not an end in itself — see [Set it as your launcher](launcher-setup.md).
- You need something audited and hardened before pointing it at models or content you don't trust. `python_exec` runs arbitrary Python in-process and is explicitly not a sandbox, prompt injection is not solved (nobody's is), and provider API keys are stored in plain text in the workspace. None of this is hidden — see the security model page — but if that's a dealbreaker for your use case, know it going in.

## What it costs you

Jenny itself is free and open source, with no account and no subscription. What you pay for is whichever model provider you connect: your API usage is billed by that provider, directly to you. Running a local model (Ollama, LM Studio) can bring that cost to zero, at the cost of quality tracking the model you run. There's no incentive built into Jenny to ration your usage one way or the other — it doesn't route requests through anything of ours, so it simply doesn't see your token consumption at all.

## If you're still deciding

Read [Introduction](introduction.md) for what Jenny actually is, then [Install the APK](install.md) when you're ready to try it. Nothing here requires a long-term commitment: it's an APK you can uninstall like any other, though see the note in [Backup and restore](../using/backup.md) about what uninstalling costs you if you skip a backup first.
