---
name: ui-automation
description: See and operate other Android apps through the accessibility service — read the current screen (text tree or screenshot), tap, swipe, type and press system keys. Use when the user asks you to "look at the screen", "open/use another app", "press that button", "scroll", "enter text somewhere", or to do anything inside an app that is not Jenny herself. Requires the accessibility service enabled (Settings → Accessibility → Jenny UI control); check ui_status first.
internal: true
---

# UI Automation (occhi e mani sugli altri app)

Dà a Jenny la possibilità di vedere e toccare qualunque app Android: dump
dell'albero di accessibilità, screenshot, tap (per testo o coordinate), swipe,
digitazione testo e azioni globali.

## Quando usarla

- L'utente chiede di guardare o usare un'altra app («cosa c'è sullo schermo?»,
  «apri Telegram e scrivi a X», «premi Salva», «scorri in basso»)
- Devi verificare visivamente qualcosa fuori da Jenny (codici, stati, messaggi)
- Qualunque operazione che richiede di interagire con la UI di un'altra app

## Come usarla (flusso)

1. **`ui_status`** — verifica che il servizio di accessibilità sia attivo.
   Se risponde `service_not_enabled`, chiedi all'utente di abilitare
   "Jenny UI control" in Impostazioni → Accessibilità (o usa
   `ui_open_accessibility_settings` per aprire la schermata giusta).
2. **`ui_screen_dump`** — leggi la schermata corrente come albero di nodi
   (testi, descrizioni, id, bounds `[left,top,right,bottom]`, flag
   clickable/editable/scrollable). Questa è la tua "vista" principale.
3. **Agisci**: `ui_tap` (con `text` del pulsante visibile, o `x`/`y` dai
   bounds), `ui_swipe` per scroll, `ui_type` per scrivere nel campo
   focalizzato, `ui_press` per back/home/recents/notifications.
4. **`ui_screenshot`** — quando serve una vista visiva (icone, grafica) o per
   salvare una prova; il PNG finisce in `<workspace>/screenshots/`.
5. **Ridump** dopo l'azione per verificare che sia andata a buon fine.

## Regole

- **Prima di toccare, guarda**: mai tap alla cieca — prima un dump, poi
  un'azione mirata (per testo o per coordinate dai bounds del nodo).
- **Le coordinate sono in pixel assoluti** dello schermo, così come escono dai
  `bounds` del dump.
- **`ui_type` sostituisce** il contenuto del campo (ACTION_SET_TEXT): se il
  campo non è vuoto e vuoi accodare, tocca prima la fine o gestisci il testo
  completo.
- **Servizio spento = nessuna azione**: se `ui_status` non è connected, ogni
  tool ritorna `service_not_enabled` — abilitalo prima.
- **Privacy**: lo schermo di altre app può contenere dati personali. Usa il
  minimo necessario e non ripetere contenuti sensibili se non serve.

## Limitazioni note

- Serve **Android 11+** per `ui_screenshot` (API 30); finestre protette
  (secure flags, es. schermate di pagamento) non si possono catturare.
- L'accessibilità va abilitata a mano dall'utente una volta sola; è un
  permesso di sistema che dà a Jenny lettura dello schermo e gesture simulate.
