---
name: notifications
description: Read and dismiss Android notifications from other apps — 2FA codes, messages, statuses, alerts. Use when the user asks "check my notifications", "did I get a code/message", "read what just arrived", or when you need info that arrives via notification (verification codes, order status, delivery updates). Requires notification access enabled (Settings → Notifications → Notification access → Jenny notifications access).
internal: true
---

# Notifications (le orecchie sugli altri app)

Dà a Jenny la possibilità di leggere le notifiche attive di sistema (codici
2FA, messaggi, stati di app) e di dismissarle — senza aprire l'app.

## Quando usarla

- «Controlla le notifiche», «è arrivato un codice?», «cosa c'è di nuovo?»
- Serve un codice di verifica (2FA) arrivato via SMS/app
- Stato di un ordine, consegna, messaggio — quando l'app non è aperta
- Pulire la shade dopo aver letto/gestito una notifica

## Come usarla (flusso)

1. **`list_notifications`** — elenca le notifiche attive: package, titolo,
   testo, tempo. Questa è la lettura principale.
2. Se serve agire sull'app da cui arriva la notifica, usa i tool di
   `ui-automation` (apri l'app, vai al punto giusto).
3. **`dismiss_notification`** (con la `key` dal dump) — rimuovi la notifica
   dopo averla gestita, per tenere pulita la shade.

## Regole

- **I contenuti possono essere sensibili** (codici usa-e-getta, messaggi
  personali): usali per lo scopo richiesto e non ripeterli più del necessario.
- **Non dismissare prima di aver letto/gestito**: prima `list_notifications`,
  poi eventualmente `dismiss_notification` con la key esatta.
- **Servizio spento = nessuna lettura**: se il tool risponde
  `service_not_enabled`, chiedi all'utente di abilitare l'accesso alle
  notifiche per Jenny (Settings → Notifications → Notification access), o usa
  `open_notification_settings` per aprire la schermata giusta.

## Limitazioni note

- Si leggono solo le notifiche **attive** nella shade (quelle già scartate o
  su altri dispositivi non sono visibili).
- L'accesso alle notifiche va concesso a mano dall'utente una volta sola; è un
  permesso di sistema.
