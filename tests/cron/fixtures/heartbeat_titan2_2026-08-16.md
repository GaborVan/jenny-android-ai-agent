# Heartbeat Tasks

<!--
This file is checked periodically by your Jenny agent. When the gateway starts with `gateway.heartbeat.enabled=true`, it automatically registers a protected heartbeat cron job that reads this file.

If this file has no tasks (only headers and comments), the agent will skip it. Completed tasks should be deleted, not kept — heartbeat only reads "Active Tasks".
-->

## Active Tasks

<!-- Add your periodic tasks below this line -->

### WaterBot: monitoraggio umidità piante
- Ogni ciclo, segui la skill `waterbot` per leggere l'umidità di tutte le piante.
- Avverti l'utente SOLO se almeno una pianta ha umidità **< 15%**. Se tutto è ≥15%, non dire nulla.
- Anti-spam: notifica una sola volta per pianta per evento sotto soglia; non ripetere finché quella pianta non torna ≥15%, oppure se resti sotto soglia da oltre 6 ore.
- Se hps/Tailscale è irraggiungibile: salta il ciclo in silenzio, nessun avviso (riproverai al prossimo ciclo).

