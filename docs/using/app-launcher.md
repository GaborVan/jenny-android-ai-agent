# Phone app launcher

The Apps tab has a third section that turns Jenny into a launcher for the other apps installed on your phone; this page covers what it shows, what you can do with it, and where it quietly falls short.

## Where it lives

Open the **Apps** tab (grid icon in the dock) and you'll find three collapsible sections stacked vertically:

1. **Jenny Apps** — mini-apps you and Jenny build together (see [Mini-apps](mini-apps.md)).
2. **Skill** — Jenny's chat skills (see [Skills](skills.md)).
3. **Android App** — every "launchable" app installed on the phone.

Tap a section's header to collapse or expand it; that state is not remembered between visits to the tab.

The third section is a genuine launcher: a grid of your installed apps with real icons and names, sorted alphabetically by label. It only lists apps that have a launcher icon of their own — anything Android's `MAIN`/`LAUNCHER` intent filter would resolve to, the same set you'd see on a normal home screen. Background services and other UI-less packages never appear.

## Using it

- **Tap** a cell to open that app.
- **Long-press** a cell to open a context menu with four actions:

| Action | What it does |
|---|---|
| Open | Same as tapping the cell — launches the app. |
| App info | Opens Android's own "App info" system screen for that app (permissions, storage, force-stop, etc.). |
| Uninstall | Only shown for non-system apps. Asks Jenny's own confirmation dialog first (`Uninstall "AppName"?`), then hands off to Android's real uninstall dialog. |
| Hide / Show | Toggles whether the app appears in this grid (see below). |

Uninstalling and viewing app info both delegate to real Android system screens — Jenny is not the one uninstalling anything, and cannot know whether you actually went through with it in Android's own dialog. The endpoint behind these two actions only reports whether it managed to *open* the system screen, not what you did once it was showing.

System apps (anything flagged as a system or updated-system app by Android) never show "Uninstall" in this menu — only "Open", "App info", and "Hide"/"Show".

## Search

The single search bar at the top of the Apps tab ("Search...") filters all three sections at once. For the Android section it matches against the app's **visible name**, not its package name — searching `com.android` will not find anything by package id. While a search is active, any section that was collapsed is forced open so its matches are visible; clearing the search restores the previous collapsed/expanded state.

Hidden apps never show up in search results unless you've already turned on "Show hidden apps" (see below).

## Hidden apps

"Hide" removes an app from Jenny's grid only — the app itself stays fully installed and still shows up in your normal Android launcher. It's purely cosmetic bookkeeping inside Jenny.

To review or restore hidden apps, tap the eye icon in the Apps tab header ("Show hidden apps"). With it on:

- Hidden apps reappear in the grid, drawn semi-transparent with a small eye-off badge on the icon.
- Long-pressing one of them offers "Show" instead of "Hide" to bring it back permanently.

A few things worth knowing:

- **The eye toggle does not persist.** It resets to off every time you leave the Apps tab, so each time you come back the hidden apps are hidden again by default — you have to tap the eye again to see them.
- Hidden packages are stored in a small JSON file in the app's own data directory (not inside your workspace), with a hard cap of 2,000 packages. This is not something you're likely to hit in practice — realistically you'd need to hide 2,000 apps — but if you ever did, anything past the cap is silently dropped rather than saved.
- Because this file lives outside the workspace, it is **not** included in workspace backups or snapshots (see [Backup and restore](backup.md)) — a restore won't bring your hidden-apps choices back.

## Honesty: what this feature doesn't do well

- **The app list loads once per WebUI session.** It's fetched the first time you open the Apps tab after the WebUI (re)loads, and it is not refreshed automatically after that. If you install a new app on your phone, it will not appear in Jenny's grid until you reload the WebUI (or restart the app). The one exception: returning to the WebUI right after using the "Uninstall" action reloads the list automatically, since that's the one case Jenny already knows to expect a change. Using "App info" and then uninstalling from *there* does **not** trigger that reload — the grid will show a stale entry for the app you just removed.
- **A failed launch gives you no feedback at all.** If tapping an app fails to open it — the app was disabled, uninstalled since load, or Android otherwise refuses — nothing happens on screen: no toast, no error message. The underlying request does distinguish success from failure internally, but the WebUI currently swallows the failure silently as a "best effort" action.
- **Bridge or connectivity problems degrade to an empty list**, not an error. If the native bridge to Android's app list fails or times out, or the WebUI has no Android context at all (which can't normally happen on-device), the section simply shows "No apps found" — the same message you'd see for a genuinely empty search result. There's no way to tell from the UI alone whether there really are no matching apps or something went wrong; the only trace is a warning in the gateway's own logs.

<!-- TODO: verify on-device (see plan §5 O-1..O-12 area) whether a disabled/refused app really produces zero feedback, and how long the list takes to load on a phone with a large number of installed apps -->

## See also

- [Tour of the WebUI](webui-tour.md) — the rest of the Apps tab and overall navigation.
- [Mini-apps](mini-apps.md) and [Skills](skills.md) — the other two sections of this tab.
- [Backup and restore](backup.md) — what does and doesn't survive a restore.
