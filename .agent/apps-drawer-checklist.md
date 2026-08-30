# Il cassetto che si digita — lista di esecuzione

Stato di [`apps-drawer-plan.md`](./apps-drawer-plan.md). Il ragionamento sta là,
qui c'è solo cosa è fatto. Si spunta quando è **girato**, non quando è scritto.

Ramo: `feat/apps-drawer`, aperto. **Passi 0 e 1 girati (30/08/2026), e su un
emulatore quadrato — il Titan 2 non era collegato.** Il passo 7 non è una fase
finale ma un prerequisito sparso: le tre incognite in fondo al piano vanno chiuse
*prima* del passo 5. Delle tre, il passo 1 ne chiude una: `goHome()` smonta il
foglio con **una** chiamata a `dismiss`, non otto (v. 1.8).

---

## Passo 0 — le misure che mancano *(nessun codice; sblocca il passo 5)*

Girato il 30/08/2026 sull'AVD `jenny_square` (1440×1440 @ 480 dpi, Android 17 —
v. [`emulator-setup.md`](./emulator-setup.md)). **Nessuna di queste caselle è
stata verificata sul Titan 2.**

- [ ] **0.1a** `adb shell settings get secure navigation_mode` **sul Titan 2** — `0` tre pulsanti, `2` gesture. Se è `0`, il passo 5 si semplifica ma **non si salta**: è un'impostazione dell'utente. *Telefono non collegato: non letto.*
- [x] **0.1b** Idem **sull'emulatore**: `2` (gesture) è il default dell'immagine android-37.0. Interruttore gesture ↔ tre pulsanti documentato in `emulator-setup.md`
- [x] **0.2** `mandatorySystemGestureInsets.bottom` stampato e scritto nel piano accanto a D8: **96 px fisici = 32 dp** in gesture, 144 px / 48 dp con tre pulsanti (dove combacia con la barra: sovrapposizione zero). Di quei 96 px, **8 px CSS** cadono dentro la WebView. *Emulatore, non Titan 2*
- [x] **0.2b** Scoperto misurando: `env(safe-area-inset-*)` è `0px` su tutti e quattro i lati — il decor di AppCompat consuma gli inset prima della WebView. D8 non è un affinamento, è l'unica via
- [ ] **0.3** ~~Verificare che il dock di oggi (43 px, solo tap) non si becchi già la gesture di home~~ — **la domanda è mal posta**: il dock è alto **56** px CSS, non 43, e sull'emulatore quadrato **non è affatto a schermo** (`@media (max-height: 500px)` in `mobile-style.css:3387` lo mette a `display: none`; viewport misurato 432 px CSS). Se valesse anche sul Titan 2, cadrebbero D1 e D2, non il passo 5. Da chiudere con `adb shell wm density` sul telefono — v. «Il dock potrebbe non essere sullo schermo» nel piano
- [ ] **0.4** Rimuovere la sonda temporanea `JennyInsetProbe` da `MainActivity.kt` (metodo `logInsetProbe`, la chiamata in `onPageFinished`, gli import `ViewCompat`/`WindowInsetsCompat`) quando 5.3 la sostituisce con il metodo vero del bridge

## Passo 1 — il foglio vuoto che si apre e si chiude

> D1/D2 rivisti dopo il passo 0: si apre dal composer, **il dock non si tocca**.

Girato il 30/08/2026 sull'AVD `jenny_square`, build debug installata con
`app:installDebug`. Onboarding superato con un provider fittizio in `config.json`
(nessuna chiave vera) — procedura in [`emulator-setup.md`](./emulator-setup.md).
Osservazioni prese via CDP sulla WebView + screenshot; **niente è spuntato per
ragionamento.** Nuovo modulo: `jenny/templates/ui/assets/mobile-launcher.js`.

- [x] **1.1** Controllo nella riga del composer che chiama `openLauncher()` — `#btn-launcher` in `#input-row`; `adb shell input tap` sul pulsante apre il foglio (screenshot)
- [x] **1.2** `data-mode="apps"` **resta** sullo slot del dock: sull'emulatore il dock è `display: none` (`matchMedia('(max-height: 500px)')` vero, viewport 480×432 CSS) eppure `_visibleModes()` torna tutte e cinque, e le cinque schede si raggiungono a swipe in **entrambe** le direzioni. *Nota non attribuibile al cassetto: partendo dalla tela del grafo lo swipe è mangiato da d3 e la vista non cambia — partendo dalla riga del titolo va. È il comportamento di oggi, non una regressione.*
- [x] **1.3** `openLauncher()` rispetta il blocco del primo avvio come fa `switchMode`: con `_setFirstRunLock(true)` e nessun `onboarding-complete`, `openLauncher()` non apre e dirotta su `onboarding`; tolto il blocco, riapre. *A onboarding davvero incompleto il pulsante non è comunque a schermo: `view-chat` è nascosta.*
- [x] **1.4** Livello `launcher` in `_overlayLayers()`, **fra `miniapp` e `drawer`**: ordine letto a runtime = `dialog, lightbox, minichat, miniapp, launcher, drawer`. Dichiara `present` e `dismiss`; **non** dichiara `close`, perché per il foglio coincide con `dismiss` e il registro lo prevede come default (`layer.close || layer.dismiss`) — un `close` identico farebbe credere a una differenza che non c'è. Lo smontaggio completo esiste ed è `LauncherController.close()`, che `dismiss()` chiama.
- [x] **1.5** `switchMode` chiude il foglio, accanto a `this.drawer.closeAll()` — verificato su `switchMode('workspace')` e `switchMode('apps')` col foglio aperto
- [x] **1.6** Indietro col foglio aperto lo chiude e non torna alla schermata precedente: da `workspace` con `_navPos = 1`, il tasto Indietro fisico chiude il foglio e lascia `mode = workspace`, `_navPos = 1`. Controprova: la **seconda** pressione naviga (`chat`, `_navPos = 0`), quindi la prima è stata consumata, non persa
- [x] **1.7** Indietro con una mini-app aperta **sopra** il foglio chiude prima l'app, e il foglio resta — provato con una Jenny App di prova temporanea: livelli presenti `[miniapp, launcher]`, dopo Indietro `.app-frame-overlay` sparito e `launcher.isOpen() === true` (screenshot). **Ha richiesto una riga in `mobile-apps.js::handleBack`**: il ritorno dalla mini-app faceva `switchMode('apps')`, che per 1.5 chiude il foglio — due livelli smontati con una pressione. V. la nota qui sotto
- [x] **1.8** Home (foglio aperto) lo smonta — e **non** chiama `dismiss` otto volte: intent Home vero (`am start -a MAIN -c HOME -n …/.MainActivity` → `onNewIntent` → `goHome()`) con un contatore temporaneo su `launcher.dismiss`: **1 chiamata**, foglio smontato, inerzia dello sfondo rilasciata. Il contatore era una patch a runtime via CDP, non una riga di sorgente: niente da togliere
- [x] **1.9** Col foglio aperto, digitare non scrive più nel composer della chat: fuoco su `BODY`, `adb shell input text "abc"` → `#chat-input` resta vuoto. Controprova a foglio chiuso: gli stessi tre caratteri finiscono nel composer, che prende il fuoco. `mobile-chat.js` **non compare nel diff**
- [x] **1.10** Girato sull'emulatore `jenny_square` con contenuto finto, **prima** di scrivere il passo 2

> **Nota per il passo 3 — il ritorno dalla mini-app.** `AppsController.handleBack`
> chiudeva l'app e poi *sempre* `switchMode('apps', false)`, cioè la scheda. Col
> foglio aperto quello switch lo chiudeva (1.5), e una pressione di Indietro
> smontava due livelli invece di uno. Ora esce prima se
> `window.mobileApp.launcher.isOpen()`: **se l'app è stata lanciata dal foglio, si
> torna al foglio**. Al passo 3, quando il foglio lancerà davvero, questa è la
> regola giusta anche per le Android app.

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
