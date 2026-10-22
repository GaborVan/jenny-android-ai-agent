# Shared memory across devices (Google Drive sync)

Two Apex agents — the one on this phone and the one on the PC — can share a
single pool of durable knowledge through one Google Drive folder named
**Apex-Pamyat**. The phone syncs it with **Settings → Cloud sync**, the same
switch that backs up `SOUL.md`/`USER.md`/`memory/` between devices. Choosing
the *Apex-Pamyat* folder turns on the shared scope; choosing any other folder
keeps the old, per-device behavior.

## Two scopes in one sync

Every sync run has two independent loops that share one state file and one
remote manifest (`apex-sync-manifest.json` at the folder root):

| Scope | When active | Local mirror | Remote home |
|---|---|---|---|
| Instance | Always (any chosen folder) | `SOUL.md`, `USER.md`, `memory/**` | Root of the chosen Drive folder |
| **Shared** | Only when the folder is named **Apex-Pamyat** | `shared/**` | Real subfolders `profile/`, `knowledge/`, `notes/` in Apex-Pamyat |

Files are matched per scope and per name; conflicts inside a scope are settled
per file with **last-writer-wins by modification time**, ties broken by content
hash (local wins an exact tie). Nothing ever merges file contents, and nothing
outside the two scopes is touched: not `config.json`, not `.jenny/`, not
skills, not sessions — and not any file the other side (or you) left in the
folder root, which is simply ignored.

## The shared folder layout

Apex-Pamyat holds three real subfolders. On the phone each one is mirrored
under `<workspace>/shared/<name>/`, and the agents on both sides write the
same structure:

```text
Apex-Pamyat/                 (Google Drive)
├── apex-sync-manifest.json  # sync bookkeeping — ignored, never hand-edited
├── profile/
│   └── USER.md              # the shared persona/profile files
├── knowledge/
│   └── *.md                 # durable project/instance knowledge
└── notes/
    ├── apex-pc-*.md         # notes written by the PC Apex
    └── apex-phone-*.md      # notes written by this phone's Apex
```

- `profile/` — the shared `USER.md`-style profile that both agents read.
- `knowledge/` — durable facts both agents need (instances, core projects).
- `notes/` — dated, single-writer day notes. The file name prefix says who
  wrote it: the PC Apex uses `apex-pc-YYYY-MM-DD.md`, this phone's Apex uses
  `apex-phone-YYYY-MM-DD.md`. Two agents never edit the same note file.

Names are flattened with the same `__` convention as `memory/` when they cross
the wire (`shared/profile/USER.md` → `shared__profile__USER.md`), so nested
files under a subfolder are fine locally — a nested `shared/knowledge/x/y.md`
is stored remotely as one file `x__y.md` inside `knowledge/`.

## Rules for the agents that write here

- **Who writes what**: the PC Apex and the phone Apex own their own notes
  (`apex-pc-*` vs `apex-phone-*`). Profile and knowledge files are shared
  state: edit them like memory files, respecting the LWW rules — a change made
  on one side wins over the other side's older copy.
- **Secrets are forbidden.** The shared folder is not encrypted and is
  readable by anyone with the Drive link. No API keys, tokens, passwords,
  pairing codes or other credentials, ever — neither in files nor in names.
- **Read before you trust**: remote content is downloaded as plain text and
  mirrored into `shared/`; treat it like any other user-provided file.
- Only `profile/`, `knowledge/` and `notes/` are synced — anything else in the
  folder (instance files, stray uploads) is left alone and never read.

## Behavior notes worth knowing

- If the chosen Drive folder is **not** named Apex-Pamyat, the shared scope is
  fully off: local `shared/**` files stay local, are neither uploaded nor
  listed in the remote manifest, and no subfolder is created in the folder.
  Deleting a local shared file then never deletes anything remotely.
- Subfolders are created in Apex-Pamyat only when there is something to write
  into them — an empty shared folder is never created for its own sake.
- Each side keeps its own copy of `shared/`; deletion of a file by one agent
  (local delete + unchanged remote) propagates to the other side as a
  tombstone delete, exactly like the memory scope. When in doubt the remote
  file wins — a file changed on the other side after your last sync is
  downloaded, never deleted.
