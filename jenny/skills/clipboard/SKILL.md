---
name: clipboard
description: Read and write the Android system clipboard. Use to grab a code/link the user copied elsewhere, to place content that another app should paste, or to hand the user something copyable. Reading may be blocked by Android 10+ unless Jenny has focus or is the default IME — the tool reports that clearly.
internal: true
---

# Clipboard (appunti di sistema)

Lettura e scrittura degli appunti Android: utile per prendere un codice o un
link copiato altrove, o per preparare testo da incollare in un'altra app
(spesso insieme a `ui-automation`).

## Quando usarla

- L'utente ha copiato qualcosa e ti chiede di usarlo («ho copiato un codice»,
  «usa il link che ho copiato»)
- Vuoi mettere negli appunti un codice/indirizzo da incollare altrove
- Prima di un incolla in un'altra app: `clipboard_set`, poi tocca il campo con
  `ui_tap` e fai incollare (o verifica con `clipboard_get` dopo una copia)

## Come usarla

1. **`clipboard_get`** — leggi il testo corrente degli appunti.
2. **`clipboard_set`** — scrivi testo negli appunti (per incollarlo dopo).

## Regole

- **Contenuti sensibili** (password, codici, dati personali): usali per lo
  scopo richiesto e non ripeterli più del necessario.
- **Android 10+ limita la lettura**: funziona quando Jenny ha il focus o è la
  IME predefinita. Se il tool risponde `clipboard_read_blocked`, chiedi
  all'utente di aprire Jenny (portarla in primo piano) e riprova.
- La **scrittura** è sempre permessa.

## Limitazioni note

- Solo testo: non gestisce immagini/altri tipi di contenuto negli appunti.
- La lettura fuori dal focus è bloccata dal sistema su Android 10+ (non è un
  bug del tool — è la policy di Android).
