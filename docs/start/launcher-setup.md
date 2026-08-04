# Set it as your launcher

Jenny can optionally replace your phone's home screen, but you don't have to use it that way — everything else about the app works identically whether or not you do.

## Why this exists

Jenny's manifest declares the `HOME`, `LAUNCHER`, and `DEFAULT` intent categories on its main activity, the same categories a real launcher app declares. That's a deliberate choice, not an accident: the idea is presence. An assistant you have to remember to open is easy to forget about; one that's simply *there* when you press Home is not. If the agent is going to message you proactively and act on a schedule anyway, being the first thing you see reinforces that instead of competing with it.

It is also what makes the "dedicated device" use case work: a spare Android phone, sitting on a desk, permanently plugged in, that boots straight into the agent instead of an app drawer nobody opens.

## Setting it as your launcher

1. Install Jenny (see [Install the APK](install.md)) and complete onboarding.
2. Press the Home button on your device.
3. Android detects more than one app registered to handle Home and shows you a chooser. Pick Jenny, and choose "Always" (rather than "Just once") if you want it to stick without asking again every time.

<!-- TODO: verify on-device (O-3): exact wording and timing of the Android HOME-app chooser, and whether it appears immediately after onboarding or only on the next Home press. Behavior can vary by Android version and OEM launcher. -->

If you don't want the chooser to appear at all yet, just don't press Home after installing — Jenny only takes over the role once you actively pick it.

## Where the Home button lands

Once Jenny is your launcher, every press of Home arrives inside the app and means "collapse back to the home screen": any open mini-app closes, the drawer closes, any open dialog closes. What counts as the home screen is yours to choose, in **Settings → Personalization → Home button**:

| Choice | What Home does |
|---|---|
| Chat | Lands on the chat (✿). The historical behavior, and still the default. |
| Apps | Lands on the Apps tab. |
| Workspace | Lands on the Workspace tab. |
| Wherever I was | Changes no view at all — it closes the mini-app, drawer and dialogs and leaves you on whichever tab you were reading. |

Earlier versions always went to chat, which is fine if you chat all day and less fine if you mostly use Jenny for mini-apps or files: every Home press threw away where you were. Leave the setting alone and nothing changes from before.

This is separate from what happens on a cold start. When the app is launched fresh it reopens on the tab you last used, regardless of this setting — the setting governs the Home button specifically.

## Reverting to your normal launcher

Android's Home-app selection is a system setting, not something Jenny controls once you've picked "Always." To change it back:

1. Open Android **Settings → Apps → Default apps → Home app** (the exact path varies a bit by Android version and manufacturer skin).
2. Select your previous launcher (Nova, the stock launcher, whatever you used before).

Uninstalling Jenny also removes it from the list of launcher candidates automatically, but you don't need to uninstall it just to stop using it as Home — you can keep the app, keep your memory and conversation, and simply launch it like a normal app from your regular home screen instead.

Used that way, Jenny sits in the app switcher like any other app, so swiping through Recents brings you back to it without going through the drawer. (Up to 0.3.0 it didn't: the activity declared `excludeFromRecents`, which earns nothing in launcher mode — the system already keeps the active home task out of Recents — and only ever applied to the case it hurt. Tapping the ongoing notification also brings you back.)

## A note on screen shape

The screenshots in this documentation, and the device the project is developed and tested against day to day, is a Unihertz Titan 2 with a square 1440×1440 display and a physical scroll wheel. Jenny's UI is built mobile-first and works on ordinary tall rectangular phone screens too, but if something in a screenshot looks unusually square, that's why — it isn't a fixed aspect ratio the app requires.

## The honest assessment: as a launcher, it's not a good one

Judged purely as a home-screen replacement, Jenny is a weak launcher. There are no widgets, no folders, no icon packs, and no wallpaper management — none of the things a dedicated launcher app is judged on. What it has instead is a grid of your installed Android apps inside its own "Android App" section of the Apps tab (see [Phone app launcher](../using/app-launcher.md) for what that grid can and can't do), plus a theme picker and a mascot.

The launcher role is the *how*, not the *what*: it exists to make the agent the thing you land on, not to compete with Nova or Niagara on features. If you want both — Jenny's presence and a fully-featured launcher — treat this as an either/or per device rather than expecting Jenny to cover both jobs on your primary phone.

## Related pages

- [Phone app launcher](../using/app-launcher.md) — what the Android-apps grid inside Jenny can actually do (open, uninstall, hide)
- [Install the APK](install.md) — permissions declared, including why there's no `CAMERA` or storage permission
- [Introduction](introduction.md) — the "daily launcher vs. dedicated device" framing in full
