# Files and attachments

You can attach photos, camera shots, and any other file to a chat message; this page covers how attaching works, what limits apply, what the model actually gets to see, and how to open files back out of the chat.

## Attaching something

Tap the paperclip button in the composer. It opens the Android system chooser — the same picker any app uses — so you get your file manager, your gallery, and a "take a photo" option all in one place. Jenny does not request the `CAMERA` permission for this: taking a photo is delegated entirely to your phone's own camera app, which hands the finished picture back to Jenny.

The paperclip hides itself once you start typing text, and reappears when the input is empty again.

You can send a message that is attachments only, with no text at all — an empty message is only rejected if it has neither text nor attachments.

Before you hit send, each attachment shows up in the composer as a thumbnail (images) or a small chip with a filename (everything else). Tap the small "×" on any of them to drop it from the message before sending — nothing has been uploaded yet at that point.

## Limits per message

Jenny enforces the same limits on both the phone (composer) and the gateway (server), so a well-formed message never gets silently truncated after the fact — see the honesty notes below for what happens when you go over.

| Kind | Max count per message | Max size each | Recognized formats |
|---|---|---|---|
| Images | 4 | 8 MB | PNG, JPEG, WebP, GIF only |
| Generic files | 4 | 20 MB | anything |
| Video | 1 | 20 MB | MP4, WebM, QuickTime (.mov) |

"Image" here is a strict allowlist by MIME type (or, when the camera hands back a file with no MIME type, by file extension): PNG, JPEG, WebP, and GIF. **SVG is not on that list** — an attached SVG is treated as a generic file, never rendered inline as an image.

All attachments, once sent, are saved on the device under `workspace/uploads/` — they're ordinary files in your workspace, visible and browsable from the Workspace tab (see [Tour of the WebUI](webui-tour.md)) and included in workspace backups and snapshots (see [Backup and restore](backup.md)).

## What the model actually sees

Attaching a file doesn't mean the model reads all of it, all the time — Jenny is deliberately conservative about what goes into every turn's context:

- **Images** are sent to the model as vision input, if the active model supports vision.
- **Small text files and PDFs** (under 512 KB) have their text extracted and inlined directly into your message, so the model sees the content immediately without needing to use a tool. Extractable text types: `.txt`, `.md`, `.csv`, `.json`, `.xml`, `.html`/`.htm`, `.log`, `.yaml`/`.yml`, `.toml`, `.ini`, `.cfg`, plus `.pdf`. Extracted text is capped at 200,000 characters — anything longer is cut off with a `(truncated, N chars total)` note.
- **Everything else** — binaries, files over 512 KB, or anything whose extraction failed — is *not* read up front. Instead the model gets a plain reference in the turn, `[Attachment: <path>]`, and can open the file itself later with its own file tools (see [Tool reference](../reference/tools.md)) if it decides it needs to.

This inline-extraction behavior is controlled by the `extractDocumentText` config key, which defaults to **false** (the on-demand-reference behavior above). Setting it to `true` switches to a legacy mode that force-extracts text from documents up to 50 MB instead of skipping straight to a reference above 512 KB — but the 200,000-character cap on extracted text still applies either way; this key is config-only today, there's no UI toggle for it — see [Configuration](../reference/configuration.md).

If the active model doesn't support vision, Jenny drops the attached images and retries with text only, then appends a visible warning to its reply so it doesn't look like the attachment was silently ignored. As of this writing that warning string is hardcoded in Italian in the backend regardless of your UI language setting:

> ⚠️ Le immagini allegate non sono state elaborate: il modello attivo non supporta input visivi.

("The attached images were not processed: the active model does not support visual input.")

## Opening files back out of the chat

- **Non-image attachments** show up in the chat as a chip: `📄 filename`. Tapping it opens the file in your phone's system viewer (the same "open with" mechanism Android uses everywhere), falling back to opening the file's URL in a new browser tab if the native bridge isn't available (this fallback only matters when debugging the WebUI from a desktop browser, not on the phone).
- **Images** — both attached and any inline in a message — open in an in-app lightbox with pinch-to-zoom, since normal browser/page pinch-zoom is disabled across the whole app. Close with the "×", by tapping the background, or with Esc on a physical keyboard.
- **From the Workspace file browser**, long-pressing (about 600 ms) any file opens a context sheet with: **Open with system app**, **Share** (the normal Android share sheet), **Save to Downloads** (copies the file into your phone's public Downloads folder), **Rename**, **Clone**, and **Delete**. Images opened directly (not long-pressed) go to the same in-app lightbox, with the same three actions available from its action bar.

## Honesty: things that can surprise you

- **Going over a limit fails silently in the composer.** If you try to attach a 5th image, or a file bigger than its size cap, the composer just... doesn't add it. There is no toast, no error, nothing — the attachment simply never appears in your pending list. If you expected 5 images and only see 4, that's why.
- **If the server itself rejects a message** (for example because the two of you disagree on the count somehow, or the payload is malformed), the chat shows a raw, non-localized error token rather than a friendly sentence — something like `Error: image_rejected`. It's not broken, it's just not translated to a human-readable message yet.
- **Video has a client/server mismatch.** The composer counts a video toward the same "4 generic files" bucket the client enforces, but the gateway itself only accepts **1 video per message**. Attaching two videos will pass the composer's own check and then get rejected by the server as `too_many_videos`.
- **The WebView suspends background fetches when the screen is off or the phone is in Doze.** If you're waiting on an attachment to load or upload and the screen has gone dark, don't be surprised if nothing happens until you wake the phone.

## See also

- [Chat basics](chat.md) — sending, streaming, and reading a response
- [Tour of the WebUI](webui-tour.md) — the Workspace tab file browser
- [Backup and restore](backup.md) — what happens to `workspace/uploads/` on backup and restore
- [Configuration](../reference/configuration.md) — `extractDocumentText` and other config-only keys
- [Tool reference](../reference/tools.md) — how the agent reads attachment references on demand
