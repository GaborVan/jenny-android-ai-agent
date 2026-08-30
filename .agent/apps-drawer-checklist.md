# Il cassetto che si digita — lista di esecuzione

Stato di [`apps-drawer-plan.md`](./apps-drawer-plan.md). Il ragionamento sta là,
qui c'è solo cosa è fatto. Si spunta quando è **girato**, non quando è scritto.

Ramo: da aprire. **Niente di fatto: il piano è del 30/08/2026 e nessun passo è
partito.** Il passo 7 non è una fase finale ma un prerequisito sparso: le tre
incognite in fondo al piano vanno chiuse *prima* del passo 5.

---

## Passo 0 — le misure che mancano *(nessun codice; sblocca il passo 5)*

- [ ] **0.1** `adb shell settings get secure navigation_mode` sul Titan 2 — `0` tre pulsanti, `2` gesture. Se è `0`, il passo 5 si semplifica ma **non si salta**: è un'impostazione dell'utente
- [ ] **0.2** Stampare `WindowInsets.getMandatorySystemGestureInsets().bottom` in dp e px fisici, e scriverlo nel piano accanto a D8
- [ ] **0.3** Verificare che il dock di oggi (43 px, solo tap) non si becchi già la gesture di home — se se la becca, il problema esiste *prima* del cassetto e va segnalato a parte

## Passo 1 — il foglio vuoto che si apre e si chiude

- [ ] **1.1** Lo slot Apps del dock: via `data-mode="apps"`, dentro `data-action="launcher"`
- [ ] **1.2** Verificato che con quell'attributo lo slot sparisca da `_visibleModes()` e il carosello orizzontale salti da Chat a Workspace senza passare per Apps
- [ ] **1.3** `openLauncher()` rispetta il blocco del primo avvio come fa `switchMode` (onboarding incompleto → non si apre)
- [ ] **1.4** Livello `launcher` in `_overlayLayers()`, **fra `miniapp` e `drawer`**, con `present` / `dismiss` / `close`
- [ ] **1.5** `switchMode` chiude il foglio, accanto a `this.drawer.closeAll()`
- [ ] **1.6** Indietro col foglio aperto lo chiude e non torna alla schermata precedente
- [ ] **1.7** Indietro con una mini-app aperta **sopra** il foglio chiude prima l'app, e il foglio resta
- [ ] **1.8** Home (foglio aperto) lo smonta — e **non** chiama `dismiss` otto volte a vuoto durante la transizione di chiusura (v. la terza incognita del piano)
- [ ] **1.9** Col foglio aperto, digitare non scrive più nel composer della chat: `hasOverlayAbove()` ferma `_maybeTypeAheadFocus` da solo, senza righe aggiunte in `mobile-chat.js`
- [ ] **1.10** Girato sul telefono con contenuto finto, **prima** di scrivere il passo 2

## Passo 2 — i dati

- [ ] **2.1** `AppsController` istanziabile senza che `view-apps` sia a schermo
- [ ] **2.2** Il foglio legge skill, Jenny App e app Android da lì — nessuna seconda copia della macchina di ricarica
- [ ] **2.3** Le app nascoste restano nascoste anche nel foglio (e l'occhio non c'è: è gestione, sta nella scheda)
- [ ] **2.4** Un `apps_list_changed` mentre il foglio è aperto lo aggiorna senza chiuderlo
- [ ] **2.5** Una app disinstallata dal telefono sparisce dal foglio via `onPackageChanged`

## Passo 3 — la lista digitabile

- [ ] **3.1** Ricerca su nome **e** descrizione, sui tre spazi di nomi insieme
- [ ] **3.2** La `description` compare nella riga — è il difetto 02 del rilievo, e questo è il passo che lo chiude
- [ ] **3.3** Ordine: pertinenza, poi frequenza, poi recenza. A campo vuoto: «Recenti»
- [ ] **3.4** Ranking in `localStorage`, chiavi `android:<pkg>` / `jenny:<slug>` / `skill:<nome>`
- [ ] **3.5** Una app rotta compare con il suo errore **nella riga**, non in una tessera che deforma la griglia (difetto 05)
- [ ] **3.6** Test: due voci con lo stesso nome in spazi diversi non si sovrascrivono nel ranking

## Passo 4 — tastiera e rotella

- [ ] **4.1** Type-ahead con le stesse guardie di `_maybeTypeAheadFocus` (no spazio, no modificatori, no furto se si scrive altrove)
- [ ] **4.2** All'apertura il campo **non** ha il fuoco (verificato su un dispositivo con tastiera software: non deve salire niente)
- [ ] **4.3** ↑↓ e rotella muovono la selezione; la riga selezionata resta in vista
- [ ] **4.4** ⏎ apre, ⇧⏎ apre la scheda del risultato, Esc pulisce il campo e — se già vuoto — chiude il foglio
- [ ] **4.5** Le righe restano raggiungibili con Tab e annunciate da TalkBack (le celle di oggi sono `<div>` con `tabindex` aggiunto a mano: qui si parte con la semantica giusta)

## Passo 5 — geometria e gesture *(bloccato dal passo 0)*

- [ ] **5.1** La maniglia e la riga delle rotaie trascinano il foglio; la lista **no**
- [ ] **5.2** Il margine inferiore viene dal valore misurato in 0.2, non da una costante
- [ ] **5.3** Nuovo metodo su `JennyGestureBridge` che espone l'inset obbligatorio alla WebUI
- [ ] **5.4** Provato: scorrere la lista con passate verso l'alto partite in fondo al foglio **non** fa collassare la UI (è il caso peggiore, v. `goHome`)
- [ ] **5.5** Il foglio si ridimensiona sopra la tastiera software (`visualViewport`) e la riga selezionata resta visibile
- [ ] **5.6** Niente `setGestureExclusion` sul bordo inferiore: non funzionerebbe e lascerebbe credere il contrario a chi legge il codice dopo

## Passo 6 — i bordi

- [ ] **6.1** Riga «Gestisci» in fondo al foglio → `switchMode('apps')`
- [ ] **6.2** Stati vuoti distinti: nessun risultato ≠ elenco non caricato (oggi sono lo stesso messaggio, v. `docs/using/app-launcher.md`)
- [ ] **6.3** Un avvio fallito dice qualcosa — oggi non dice niente, ed è scritto nella documentazione come limite noto
- [ ] **6.4** Stringhe in `it.json` **e** `en.json`, niente testo cablato
- [ ] **6.5** `docs/using/app-launcher.md` aggiornato: descrive la scheda, e la scheda non è più il lanciatore
- [ ] **6.6** `ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session && pytest -q`

## Passo 7 — verifica sul telefono

- [ ] **7.1** Build di release su worktree pulito (Chaquopy impacchetta l'albero di lavoro, non HEAD)
- [ ] **7.2** Aprire un'app Android dal foglio, tornare indietro, e ritrovare la conversazione dov'era
- [ ] **7.3** Aprire una Jenny App dal foglio e verificare la catena Indietro completa: schermata interna → app → foglio → chat
- [ ] **7.4** Una sessione d'uso vera, e poi guardare se l'ordine della lista somiglia a come si usa davvero il telefono
