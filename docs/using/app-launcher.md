# Phone app launcher

Jenny can open the other apps installed on your phone. There are two places for that, and they do different jobs: a **drawer** that slides up over the conversation, which is where you launch things, and the **Apps tab**, which is where you manage them. This page covers both, and is honest about where they still fall short.

## The drawer: where you launch things

Tap the **grid icon in the middle of the dock** and a sheet slides up over the chat. (There used to be a second entrance, a button in the message box; it is gone — one way in that works everywhere beats two where one lies.) It is a search field with a list under it — not a grid of icons — and it holds the two kinds of thing that can actually be *launched*, mixed together:

- your installed **Android apps**,
- your **Jenny Apps** (see [Mini-apps](mini-apps.md)).

Skills are not in the drawer. They are not launchable — you don't open a skill, Jenny uses one — so they live in the Apps tab's own Skills room instead (see [Skills](skills.md)).

Each row shows a name and, under it, a second line: the description for a Jenny App, the package name for an Android app. If something is broken — an invalid manifest, a skill that isn't available — that takes the second line instead, in red, so you can see what's wrong without opening anything.

### Using it

- **Type to filter.** The field doesn't grab focus when the sheet opens (that would raise the software keyboard and eat the sheet), but the first printable key you press puts the cursor there and keeps the character. Search matches the name *and* the second line, so `com.android` finds an app by package id and `remind` finds the skill whose description mentions reminders. Accents and capitals don't matter, and several words are combined with AND.
- **Tap a row** to open it.
- **↑ / ↓** move the highlighted row without moving the cursor out of the search field, so you can keep typing. **Enter** opens the highlighted row; **Shift+Enter** opens its detail card instead (the same card a long-press gives you in the Apps tab — where you uninstall, hide, or read what something is).
- **Esc** — and the hardware Back button, which does the same thing — clears the search first, and closes the sheet on the second press.
- **Drag the handle or the title row down** to dismiss it. Dragging inside the list scrolls the list and never moves the sheet.
- On a phone with a scroll wheel, the wheel is wired to move the highlighted row as well — though that has only been exercised with synthetic wheel events so far, not on a real wheel.

With an empty search field the list is titled **Most used** and is ordered by how often you open things, then by how recently. That ranking is stored on the device only; clearing the app's data resets it, and it rebuilds itself in a few days of use.

Opening an Android app closes the drawer, because the app takes over the screen. Opening a Jenny App does not: the mini-app appears *over* the drawer, and Back brings you back to it with your search intact.

### Where it can't go

The drawer's list deliberately stops short of the very bottom of the screen. On a phone in gesture navigation, the last strip above the screen edge belongs to the system's home gesture, and an app cannot claim it back. Since Jenny is often the device's own home screen, a swipe up that started in that strip would not just close the drawer — it would tear down every overlay in the UI. So the list keeps clear of it. The size of that strip is read from Android at runtime, so it is right for your phone and shrinks to nothing when you switch to three-button navigation.

## The Apps tab: where you manage things

The dock's grid icon opens the *drawer*, not this tab. To reach the tab, swipe to it, or tap **Manage apps** at the bottom of the drawer — which takes you there and closes the drawer behind you.

The tab is a **segmented strip at the top with one room showing at a time**, not a stack of sections: only the open room is in the page at all.

1. **Jenny Apps** — mini-apps you and Jenny build together.
2. **Skills** — Jenny's chat skills.
3. **Android** — every "launchable" app installed on the phone.

Which room you left open **is** remembered between visits, on the device only.

The Android section lists only apps that have a launcher icon of their own — anything Android's `MAIN`/`LAUNCHER` intent filter would resolve to, the same set you'd see on a normal home screen. Background services and other UI-less packages never appear.

- **Tap** a cell to open that app.
- **Long-press** a cell for a context menu with four actions:

| Action | What it does |
|---|---|
| Open | Same as tapping the cell — launches the app. |
| App info | Opens Android's own "App info" system screen for that app (permissions, storage, force-stop, etc.). |
| Uninstall | Only shown for non-system apps. Asks Jenny's own confirmation dialog first (`Uninstall "AppName"?`), then hands off to Android's real uninstall dialog. |
| Hide / Show | Toggles whether the app appears in Jenny (see below). |

Uninstalling and viewing app info both delegate to real Android system screens — Jenny is not the one uninstalling anything, and cannot know whether you actually went through with it in Android's own dialog. The endpoint behind these two actions only reports whether it managed to *open* the system screen, not what you did once it was showing.

System apps (anything flagged as a system or updated-system app by Android) never show "Uninstall" in this menu — only "Open", "App info", and "Hide"/"Show".

### Search in the tab

The search bar at the top of the Apps tab filters **the room you are in**, not all three — its placeholder changes with the room to say so. Unlike the drawer, it matches only the **visible name**: searching `com.android` there will not find anything by package id.

Hidden apps never show up in search results unless you've already turned on "Show hidden apps".

## Hidden apps

"Hide" removes an app from Jenny only — the app itself stays fully installed and still shows up in your normal Android launcher. It's purely cosmetic bookkeeping inside Jenny. Hidden apps disappear from the drawer as well, and the drawer has no way to reveal them: that switch lives with the rest of management.

To review or restore hidden apps, tap the eye icon in the Apps tab header ("Show hidden apps"). With it on:

- Hidden apps reappear in the grid, drawn semi-transparent with a small eye-off badge on the icon.
- Long-pressing one of them offers "Show" instead of "Hide" to bring it back permanently.

A few things worth knowing:

- **The eye toggle does not persist.** It resets to off every time you leave the Apps tab, so each time you come back the hidden apps are hidden again by default — you have to tap the eye again to see them.
- Hidden packages are stored in a small JSON file in the app's own data directory (not inside your workspace), with a hard cap of 2,000 packages. This is not something you're likely to hit in practice — realistically you'd need to hide 2,000 apps — but if you ever did, anything past the cap is silently dropped rather than saved.
- Because this file lives outside the workspace, it is **not** included in workspace backups or snapshots (see [Backup and restore](backup.md)) — a restore won't bring your hidden-apps choices back.

## When something goes wrong

- **A launch that fails says so.** If tapping an app doesn't open it — it was uninstalled or disabled since the list was loaded, or Android refuses for some other reason — you get an error message naming the app, and the drawer stays open so you can try something else. (It used to do nothing at all, which was indistinguishable from a tap that didn't register.)
- **An empty list and a broken one are different screens.** The drawer distinguishes four states, and each one asks for something different: still loading, *"Could not read the list of apps"* when a fetch or the native bridge failed, *"No app, Jenny App or skill to open"* when there genuinely is nothing, and *"No results for …"* when your search matched nothing. If only part of the list failed — the usual case, since the phone's app list comes from a different place than skills and mini-apps — a strip appears above the list saying so, with a **Retry** button. Reopening the drawer retries a failed list too.

## What it still doesn't do well

- **A disabled app leaves a stale row.** Installing or uninstalling an app updates Jenny's list on its own, because Android broadcasts those. *Disabling* one doesn't broadcast the same thing, so the row stays until the list is reloaded — tapping it gets you the error message above rather than the app.
- **The drawer's Manage row and the incomplete-list strip disappear when the software keyboard is up** on a short screen. There is only room for so much, and while you are typing the results matter more than either. Both come back when the keyboard goes down.
- **Reaching "Manage apps" with a keyboard means tabbing past the whole list**, because every row is a tab stop on purpose (that is what makes the rows reachable one by one with TalkBack).
- **The ranking is frequency-first.** An app you opened fifty times last month and never since keeps its place near the top. Whether that needs a recency decay is an open question that only real use can answer.

## See also

- [Tour of the WebUI](webui-tour.md) — the rest of the Apps tab and overall navigation.
- [Mini-apps](mini-apps.md) and [Skills](skills.md) — the other two things the drawer can open.
- [Backup and restore](backup.md) — what does and doesn't survive a restore.
