# Android permissions

Every permission Jenny's manifest declares, why it's there, and what happens if you refuse or revoke it.

Jenny asks for a short, deliberately narrow list. Two permissions are requested at runtime (notifications, location) with an Android system prompt; the rest are either automatic (granted at install, no prompt) or "special" permissions that route through a system settings screen instead of a normal dialog.

## Requested permissions

| Permission | Type | Why Jenny wants it | If you deny it |
|---|---|---|---|
| `INTERNET` | Normal (install-time, no prompt) | Talking to your configured LLM provider, web search/fetch, Telegram, downloads — essentially everything Jenny does over the network. | Can't be denied on Android; without it the app cannot function at all. |
| `ACCESS_NETWORK_STATE` | Normal (install-time) | Lets the app check network connectivity state. | Can't be denied; no user-visible effect either way. |
| `FOREGROUND_SERVICE` | Normal (install-time) | Lets the gateway run as a foreground service so it keeps working with the screen off or the app backgrounded. | Can't be denied; required for the agent to run at all. |
| `FOREGROUND_SERVICE_SPECIAL_USE` | Normal (install-time) | Declares the foreground service's category as "special use" (running a local agent runtime doesn't fit Android's other foreground-service categories like media playback or navigation). | Can't be denied; part of how the persistent service is allowed to exist on modern Android. |
| `FOREGROUND_SERVICE_LOCATION` | Normal (install-time) | Lets the foreground service keep reading location while running in the background (screen off, Telegram-only turns, scheduled/cron turns), without needing the much broader `ACCESS_BACKGROUND_LOCATION` permission. Access is tied to the service's lifetime, not a standing background grant. | Can't be denied by itself; it only matters if you've also granted fine/coarse location below. |
| `POST_NOTIFICATIONS` | **Runtime** (Android 13+ only; asked at first run) | Shows the persistent "Jenny ✦ online" foreground-service notification, and any proactive notifications (reminders, heartbeat messages, Telegram activity) when Jenny isn't in the foreground. | The persistent service notification silently fails to show, and proactive alerts never appear — Jenny keeps running, but you get no visual sign of it and no pings for reminders or proactive messages. No error is shown; you can grant it later from Android's app-notification settings and Jenny picks it up on the next service start. |
| `RECEIVE_BOOT_COMPLETED` | Normal (install-time) | Lets a boot receiver relaunch the gateway automatically after the phone restarts — the "server in a drawer" behavior. | Can't be denied; if you don't want auto-restart after reboot you'd need to disable it at the OS level (e.g. via app battery/autostart settings on some OEM skins) or simply not rely on it. |
| `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` | **Runtime** (asked at first run, since the in-app location toggle defaults to on) | Lets Jenny read device location: a free last-known reading injected into agent context, plus an on-demand fresh GPS fix when explicitly requested. No background location permission is used — location is only read while the foreground service is alive. | Denying it doesn't break anything or show an error: the location feature simply returns nothing, silently. Jenny keeps working normally without location context. You can grant it later from Android's per-app permission settings; see [Location](../using/location.md). |
| `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` | Special (no manifest-time prompt; triggered by an in-app button) | Lets Jenny launch the system's "exempt from battery optimization" screen. Exemption keeps Telegram's long-poll connection (and other background activity) responsive even when the phone isn't charging and Doze would otherwise throttle it. | If you don't grant the exemption, Android's Doze/App Standby can delay background work — Telegram replies and proactive checks may lag or stall while the screen is off, especially unplugged. Nothing fails outright; things just get slower or less reliable in the background. The button that triggers this is in Settings → Telegram, not in a general section, since Telegram's long-poll is the main thing that benefits from it. <!-- TODO: verify on-device (O-5): honest numbers for cron/long-poll behavior under real Doze without the exemption --> |

## What Jenny deliberately does not ask for

| Not requested | What happens instead |
|---|---|
| Camera | Attaching a photo taken on the spot goes through Android's standard system camera app via an image-capture intent (`ACTION_IMAGE_CAPTURE`), triggered from the chat attachment picker. Jenny never touches the camera hardware directly and never needs the `CAMERA` permission. |
| Any storage permission (`READ_EXTERNAL_STORAGE`, `MANAGE_EXTERNAL_STORAGE`, etc.) | File attachments, backup export/import, and "save to Downloads" all go through Android's Storage Access Framework and `MediaStore` — system pickers and content-resolver APIs that don't require a storage permission grant at all. Saving a file to the public Downloads folder does require Android 10 (API 29) or newer on the device; Jenny's manifest supports back to Android 8.0 for everything else. |
| Contacts, SMS, phone state, other apps' data | Never requested, never used. The sandbox around what Jenny can touch is Android's own per-app isolation — see [Security model](../internals/security-model.md). |

## App visibility (`<queries>`)

Separately from permissions, Jenny's manifest declares two narrow `<queries>` entries so `PackageManager` will tell it about specific other apps, without the broad `QUERY_ALL_PACKAGES` permission:

- Apps that declare a launcher entry point (`MAIN`/`LAUNCHER`) — this is what powers the "Android apps" launcher tab inside Jenny. See [Phone app launcher](../using/app-launcher.md).
- Any camera app that can handle `IMAGE_CAPTURE` — this is what lets the attachment chooser offer "take a photo" without Jenny needing the camera permission itself.

## Where this fits

This table only covers what Android permission system involves. For the layers of the actual sandbox — what Jenny's own file-access policy allows inside its workspace, what the network/SSRF filter does, and why `python_exec` isn't a security boundary by itself — see [Security model](../internals/security-model.md). For what data ever leaves the device, and to whom, see [Privacy](../internals/privacy.md).

## See also

- [Install the APK](../start/install.md) — the build these permissions come from, and the `allowBackup` caveat
- [First run](../start/first-run.md) — what happens the moment the app opens for the first time
- [Location](../using/location.md) — the location toggle and permission interaction in detail
- [Telegram bridge](../using/telegram.md) — why the battery exemption button lives there
- [Security model](../internals/security-model.md)
- [Privacy](../internals/privacy.md)
