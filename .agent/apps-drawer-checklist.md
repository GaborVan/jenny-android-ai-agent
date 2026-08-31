# Il cassetto che si digita — lista di esecuzione

Stato di [`apps-drawer-plan.md`](./apps-drawer-plan.md). Il ragionamento sta là,
qui c'è solo cosa è fatto. Si spunta quando è **girato**, non quando è scritto.

Ramo: `feat/apps-drawer`, aperto. **Passi da 0 a 6 girati (30/08/2026), e
su un emulatore quadrato — il Titan 2 non era collegato.** Il passo 7 non è una
fase finale ma un prerequisito sparso: le incognite in fondo al piano si chiudono
una alla volta. Il passo 1 ne ha chiusa una (`goHome()` smonta il foglio con
**una** chiamata a `dismiss`, non otto — v. 1.8); il passo 5 un'altra, ma solo
sull'emulatore: **la soglia della gesture di home sul Titan 2 resta non letta**,
e il passo 5 è fatto apposta per non averne bisogno — la legge a runtime.

---

## Passo 0 — le misure che mancano *(nessun codice; sblocca il passo 5)*

Girato il 30/08/2026 sull'AVD `jenny_square` (1440×1440 @ 480 dpi, Android 17 —
v. [`emulator-setup.md`](./emulator-setup.md)). **Nessuna di queste caselle è
stata verificata sul Titan 2.**

- [x] **0.1a** `settings get secure navigation_mode` **sul Titan 2** → **2 (gesture)**, letto il 31/08/2026. La navigazione a gesture è attiva: tutto il passo 5 è necessario su questo telefono, non teorico
- [x] **0.1b** Idem **sull'emulatore**: `2` (gesture) è il default dell'immagine android-37.0. Interruttore gesture ↔ tre pulsanti documentato in `emulator-setup.md`
- [x] **0.2** `mandatorySystemGestureInsets.bottom` stampato e scritto nel piano accanto a D8: **96 px fisici = 32 dp** in gesture, 144 px / 48 dp con tre pulsanti (dove combacia con la barra: sovrapposizione zero). Di quei 96 px, **8 px CSS** cadono dentro la WebView. *Emulatore, non Titan 2*
- [x] **0.2b** Scoperto misurando: `env(safe-area-inset-*)` è `0px` su tutti e quattro i lati — il decor di AppCompat consuma gli inset prima della WebView. D8 non è un affinamento, è l'unica via
- [x] **0.3** **Il dock c'è.** `wm size` 1436x1440, `wm density` **400** (non 480: l'AVD era stato creato a occhio) → **574x576 px CSS**, sopra la soglia dei 500, quindi `@media (max-height: 500px)` **non** scatta. Confermato a schermo. Cade la preoccupazione del passo 0; D2 resta per la ragione del passo 6
- [x] **0.4** Sonda `JennyInsetProbe` rimossa dal codice col passo 5: `logInsetProbe` e la sua chiamata in `onPageFinished` sostituite da `refreshGestureInsets()`, il commento «TEMPORANEO» sull'import via. Gli import `ViewCompat`/`WindowInsetsCompat` **restano**, perché ora servono al metodo vero. `grep -rn JennyInsetProbe` non trova più niente fuori da questi due file di piano, dove è il verbale di una misura fatta

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
- [x] **1.4** Livello `launcher` in `_overlayLayers()`, **fra `miniapp` e `drawer`**: ordine letto a runtime = `dialog, lightbox, minichat, miniapp, launcher, drawer`. Dichiara `present` e `dismiss`. ⚠️ *Al passo 1 dichiarava **solo** quei due, perché `close` coincideva con `dismiss` e il registro lo prevede come default (`layer.close || layer.dismiss`). **Dal passo 4 non coincidono più** — `dismiss` svuota prima la ricerca — e il livello dichiara anche un `close` proprio: v. la nota sotto il passo 4 e `test_home_dismounts_the_sheet_in_one_call`.* Lo smontaggio completo è `LauncherController.close()`, che `dismiss()` chiama a campo vuoto.
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

Girato il 30/08/2026 sullo stesso AVD `jenny_square`, build debug da
`app:installDebug`. Osservazioni via CDP sulla WebView + screenshot; nessuna
casella spuntata per ragionamento. Nessun modulo JS nuovo (il foglio è ancora
`mobile-launcher.js`), quindi `_UI_MANIFEST` non cambia.

- [x] **2.1** `AppsController` istanziabile senza che `view-apps` sia a schermo — nuovo `MobileApp.appsController()`, che usa la stessa `controllerFactories.apps` e registra l'istanza in `this.controllers` (così `switchMode` non ne costruisce una seconda e `onPackageChanged` la trova). Letto a foglio aperto in una sessione partita in chat: `currentMode: "chat"`, `getComputedStyle(view-apps).display === "none"`, `!!controllers.apps === true`, 27 righe nel foglio. Controprova: dopo una visita alla scheda App, `controllers.apps` è **lo stesso oggetto** di prima
- [x] **2.2** Il foglio legge skill, Jenny App e app Android da lì — `launcherEntries()` sta in `mobile-apps.js`, e `mobile-launcher.js` non contiene né `api.`, né `fetch(`, né `wsManager`: **non parla con la rete** e non ha una seconda macchina di ricarica. Screenshot con le tre categorie insieme (`SKILL`, `JENNY APP`, `ANDROID APP`), icone base64 vere per le Android e glifi Tabler per le altre
- [x] **2.3** Le app nascoste restano nascoste anche nel foglio — YouTube nascosta dal **percorso vero** (pressione lunga sulla cella nella scheda → "Hide", `adb shell input`). Due controprove: (a) con l'occhio della scheda **acceso** (`_showHidden === true`, YouTube di nuovo nella griglia) il foglio ne ha 27 e non ce l'ha; (b) dopo force-stop e riavvio dell'app la lista nascoste torna dal gateway e il foglio ne ha ancora 27 su 28. Nel foglio non c'è nessun controllo occhio (`querySelector` su `#launcher-sheet` per `[data-action="toggle-hidden"]`/`.ti-eye*`: nessuno)
- [x] **2.4** Un `apps_list_changed` mentre il foglio è aperto lo aggiorna senza chiuderlo — creata `workspace/apps/prova-cassetto/` con `run-as`, poi un turno di chat vero (il frame lo emette `_sync_apps_and_notify`, non una simulazione): 27 → 28 righe, la nuova è `jenny:prova-cassetto`, `launcher.isOpen() === true` e lo scrim ancora `open` **durante e dopo**. Cancellata la cartella e rifatto: 28 → 27. Attribuito con un contatore temporaneo sui frame: **1** `apps_list_changed` per ciascun cambio
- [x] **2.5** Una app disinstallata dal telefono sparisce dal foglio via `onPackageChanged` — APK di sonda minimo (`com.flagdizero.probeapp`, "Sonda Cassetto", una `LAUNCHER` e niente altro) costruito fuori dal worktree e installato con `adb install`: 28 → 29 a foglio aperto (`added:`). `adb uninstall`: 29 → 28, riga sparita, foglio ancora aperto e alla stessa posizione di scorrimento (screenshot prima/dopo). Attribuito: `onPackageChanged` chiamata `removed` ×2 (REMOVED + FULLY_REMOVED, come previsto da `MainActivity`), e **zero** `apps_list_changed` nello stesso intervallo. *Un pacchetto di sistema non serve: `pm uninstall --user 0` vuole root, e l'immagine playstore non ce l'ha.*

> **Fuori dalle caselle, di proposito.** Le righe non sono ancora attivabili:
> l'avvio arriva col passo 4, insieme ai tasti che scelgono cosa avviare.
> Descrizione, ricerca e ordine per pertinenza sono il passo 3 — qui l'ordine è
> alfabetico e stabile, e la `key` è già `android:<pkg>` / `jenny:<slug>` /
> `skill:<nome>` perché il ranking del passo 3 la trovi pronta.

## Passo 3 — la lista digitabile

Girato il 30/08/2026 sullo stesso AVD `jenny_square`, build debug da
`app:installDebug`. Osservazioni via CDP sulla WebView + screenshot + tocchi
veri (`adb shell input tap` sulle coordinate lette a runtime) e tasti veri
(`adb shell input text`); nessuna casella spuntata per ragionamento. Nuovo
modulo puro `jenny/templates/ui/assets/shared/launcher-rank.js` — aggiunto a
`_UI_MANIFEST`.

- [x] **3.1** Ricerca su nome **e** descrizione, sui tre spazi di nomi insieme — tastiera vera sull'emulatore: `remind` → **solo** `skill:cron`, che quel termine ce l'ha solo nella descrizione; `com.android` → solo `android:com.android.chrome`, dal nome del pacchetto; `jenny` → 6 righe su due spazi di nomi (una app Android per nome, quattro skill per descrizione). Più termini sono in AND e possono cadere in campi diversi (provato sotto node). Accenti e maiuscole non contano (NFKD, come `wiki-search.js`)
- [x] **3.2** La `description` compare nella riga — difetto 02 chiuso. `launcherEntries()` porta `description` (Jenny App), `_skillUserSummary()` (skill: preferisce `user_summary` localizzato, ripiega su `description`, e scarta il ripiego sul *nome*) e il `packageName` per le app Android, che una descrizione non ce l'hanno: **non è testo inventato**, e per giunta si cerca. Usati anche `available`/`unavailable_reason`/`disabled` (come guasto) e `has_server` (glifo `ti-cloud`). Screenshot con le tre categorie insieme, ciascuna con la sua seconda riga
- [x] **3.3** Ordine: pertinenza, poi frequenza, poi recenza — sul telefono: con `tel` vince *Telefono* (attacco sul nome) su *Voicetel* (sottostringa) su *Impostazioni* (solo descrizione); a campo vuoto, con `cassetto-rotta` a 3 avvii e `cassetto-buona` a 2 (più recente), l'ordine è **rotta, buona**, cioè la frequenza batte la recenza, e le voci a 1 avvio seguono per recenza. Le mai aperte restano in coda in ordine alfabetico. Il titolo del foglio a campo vuoto è quello del gruppo in cima e «Risultati» appena si digita. *L'etichetta letta durante il passo 3 era «Recenti»; corretta lo stesso giorno in **«Più usate»**, perché l'ordine è per frequenza — v. «Una domanda che solo l'uso può chiudere» nel piano. La chiave i18n resta `launcher.recent`.*
- [x] **3.4** Ranking in `localStorage`, chiavi `android:<pkg>` / `jenny:<slug>` / `skill:<nome>` — letto dal telefono dopo quattro avvii veri: `{"android:com.google.android.calendar":[1,…],"skill:app-creator":[1,…],"jenny:cassetto-rotta":[3,…],"jenny:cassetto-buona":[2,…]}`. **Sopravvive a force-stop + riavvio**: riaperto il foglio dopo il restart, le quattro voci usate sono ancora in cima nello stesso ordine
- [x] **3.5** Una app rotta compare con il suo errore **nella riga** (difetto 05) — `app.json` malformata scritta con `run-as` in `workspace/apps/cassetto-rotta/`, poi un turno di chat vero per far partire `apps_list_changed`. Misurato a foglio aperto: **28 righe, un'unica altezza — 52 px CSS per tutte**, compresa quella rotta, con l'errore dentro la riga in `var(--error)` e `scrollWidth === clientWidth` (nessuno sfondamento laterale). Il tocco sulla riga rotta apre lo stesso dialog di conferma della cella
- [x] **3.6** Test automatico vero: `tests/webui/test_launcher_rank_client.py`, che esegue `shared/launcher-rank.js` sotto node (idioma di `test_wiki_search_client.py`). Tre test sulla casella: i contatori di `skill:notes` e `jenny:notes` restano separati, la separazione **si vede nell'ordine**, e ciò che finisce in `localStorage` porta il prefisso (una separazione solo in memoria si perderebbe al riavvio, e si noterebbe solo dopo un force-stop)

**In più, e parte del passo 3:**

- [x] **3.7** Le righe si attivano **col tocco**, tutte e tre le specie — tocchi veri sull'emulatore: *Calendar* → l'app parte davvero (`topResumedActivity` passa a `com.google.android.gms/…MinuteMaidActivity`, il primo avvio di Calendar) e **il foglio si chiude**; *Bacheca del cassetto* → `.app-frame-overlay` sopra il foglio (livelli `[miniapp, launcher]`), e Indietro torna al foglio con la query intatta (1.7 per la via vera); *app-creator* (skill locked) → la scheda della skill sopra il foglio (`[dialog, launcher]`), e Indietro chiude solo quella. Ogni attivazione registra l'uso **prima** di avviare
- [x] **3.8** Digitare non ridisegna (difetto 07) — marchiate le 27 righe in cache, poi 10 tasti **e** un `_render()` completo (la via di `apps_list_changed`): **27 su 27 sopravvivono** e tutti e 22 gli `<img>` restano gli stessi nodi, cioè nessuna icona base64 viene ridecodificata né dai tasti né da un ricaricamento in cui non è cambiato niente. Misurato: **0,22 ms per tasto** contro **1,0 ms** per una ricostruzione completa dello stesso elenco (e quel confronto sottostima il guadagno, perché anche il rebuild riusa la bitmap già decodificata dalla stessa data URL)

> **Nota per il passo 5, vista provando il passo 3 — 5.5 non è un ritocco.**
> ⚠️ *Il sintomo qui sotto è giusto, il meccanismo no: rimisurato col passo 5,
> `innerHeight` scende a 124 px e la finestra **si ridimensiona**. V. la nota in
> fondo al passo 5.*
> Col fuoco nel campo la tastiera software dell'emulatore sale e **copre tutto
> il foglio**, lista compresa: `innerHeight` resta 432 px CSS, la WebView non si
> ridimensiona, e le righe continuano a stare dove stavano — sotto la tastiera.
> Si cerca alla cieca. Sul Titan 2, che ha la tastiera fisica, non si vede; su
> qualunque altro dispositivo è la prima cosa che si nota. Il `visualViewport`
> di 5.5 è quindi un requisito del cassetto, non una rifinitura, e va fatto
> prima di mostrare il foglio a chi non ha una tastiera hardware.
>
> Conseguenza pratica per chi verifica: dopo aver digitato con
> `adb shell input text`, serve un `KEYCODE_BACK` per far scendere la tastiera
> **prima** di leggere le coordinate delle righe o di toccarne una — altrimenti
> il tap finisce sui tasti (una volta ha scritto una `t` nel campo invece di
> aprire l'app, e sembrava che l'attivazione non funzionasse).

## Passo 4 — tastiera e rotella

Girato il 30/08/2026 sullo stesso AVD `jenny_square`, build debug da
`app:installDebug`. Tasti **veri** (`adb shell input keyevent`, `input text`,
`input keycombination`), tocchi veri sulle coordinate lette a runtime, e — per
la casella 4.5 — l'**albero di accessibilità della WebView** letto via CDP
(`Accessibility.getPartialAXTree`), che è quello che TalkBack consuma, non gli
attributi nel sorgente. Nessuna casella spuntata per ragionamento. Nuovo modulo
puro `jenny/templates/ui/assets/shared/type-ahead.js` — aggiunto a
`_UI_MANIFEST`.

- [x] **4.1** Type-ahead con le guardie di `_maybeTypeAheadFocus`, **estratte** in `shared/type-ahead.js` e usate da entrambi i chiamanti (la chat non se le riscrive: `mobile-chat.js` ora la importa). Osservato col fuoco *fuori* dal campo e un registratore di `keydown` che attribuisce ogni evento: spazio consegnato come `key: " "` → campo non focalizzato e vuoto; `ctrl+A` consegnato come `Control` + `a`+ctrl → idem; `"cal"` → il campo prende il fuoco **e tiene tutti e tre i caratteri** (il primo non si perde), titolo → «Results», lista → la sola *Calendar*. Non-regressione della chat provata sul telefono: a foglio chiuso `"ciao"` finisce ancora nel composer. Cinque test sotto node in `tests/webui/test_type_ahead_client.py`
- [x] **4.2** All'apertura il campo **non** ha il fuoco, e la tastiera software **non sale** — osservato sull'emulatore, che la tastiera software ce l'ha: dopo il tocco sul pulsante, `mInputShown=false`, `document.activeElement` = `launcher-sheet`, campo vuoto, e lo screenshot non mostra tasti. Il fuoco va sul **contenitore** e non più sulla ✕ del passo 1: con il fuoco su un pulsante il ⏎ di 4.4 chiudeva il foglio invece di aprire il primo risultato
- [x] **4.3** ↑↓ muovono la selezione e il fuoco **non** si sposta (resta sul campo, o sul foglio): tre ↓ → *Calendar*→*Clock*, altri dieci → *llm-wiki*, due ↑ → *Google*, con `aria-activedescendant` che segue ogni passo. La riga resta in vista senza salti: `scrollTop` 0 → 103 → 680, e la riga selezionata dentro il riquadro della lista a ogni misura (`block: 'nearest'`; due ↑ che non richiedono scorrimento lasciano `scrollTop` fermo a 680). Rotella: v. la nota in fondo
- [x] **4.4** ⏎ apre davvero — «clock» + ⏎ → `topResumedActivity` passa a `com.google.android.deskclock/…DeskClock`, il foglio si chiude e `launcher-usage` guadagna `android:com.google.android.deskclock`. ⇧⏎ (`input keycombination SHIFT_LEFT ENTER`) apre la **scheda**: con «wiki» selezionato *llm-wiki*, compare `skill-sheet` sopra il foglio (livelli `[dialog, launcher]`, screenshot) e Indietro chiude solo quella, con la query intatta. Esc pulisce e poi chiude: 27 righe e «Most used» tornano alla prima pressione, il foglio si chiude alla seconda, la terza (a foglio chiuso) non fa niente perché si è alla radice. **E il tasto Indietro fa le stesse due tappe**, misurato a parte
- [x] **4.5** Semantica giusta dalla nascita: `role="listbox"` sulla lista, `role="option"` sulle righe, `combobox` sul campo. Letto nell'albero di accessibilità della WebView: campo = `combobox` con `expanded`, `hasPopup: listbox`, `autocomplete: list` e `activedescendant` → la riga selezionata; lista = `listbox` "Results"; righe = `option`, tutte `focusable`, `selected` su **una** sola. Tab le raggiunge una per una (foglio → ✕ → campo → riga → riga → …) e ogni riga che prende il fuoco **diventa** la selezione

> **La rotella resta l'incognita di questo passo.** Il Titan 2 ha una rotella
> fisica e **non si sa quali eventi emetta**: `wheel`, i codici delle frecce, o
> altro. Sull'emulatore la rotella non c'è, quindi da qui la domanda non è
> chiudibile in nessun modo. Sono coperte le due letture più probabili, che
> portano allo stesso posto — ↑↓ (4.3, provate) e `wheel` (`_onWheel`). Il ramo
> `wheel` è stato fatto girare per davvero, ma attraverso la pipeline di input
> di Chromium (`Input.dispatchMouseEvent` con `type: "mouseWheel"`), non con un
> dito su una rotella: `deltaY: 100` = 4 righe, tre passate = 12, due passate
> all'indietro = 8, una tacca da 8 px non muove niente e tre sì (l'accumulatore
> di `WHEEL_PIXELS_PER_STEP`). **Che sia `wheel` quello che la rotella produce
> resta da leggere sul telefono vero** — v. «Cosa NON è stabilito» nel piano.

**Tre difetti trovati facendo girare il passo, non leggendo il codice:**

- La selezione **sopravviveva alla chiusura**: riaperto il foglio, era ancora
  sulla riga dell'altra volta — e ⏎ appena aperto avrebbe *lanciato* qualcosa
  che nessuno aveva scelto adesso. Ora `open()` chiama `_select(null)`.
- Azzerare la sola chiave non bastava: la riga di prima resta in cache **con la
  sua classe e il suo `aria-selected` addosso**, e nell'albero di accessibilità
  si vedevano **due** righe selezionate insieme.
- La selezione restava incollata alla riga che era in cima al **primo** disegno,
  quando c'erano solo le skill: le app Android arrivano dopo, la lista si
  riordina, e ci si ritrovava evidenziata la dodicesima voce, fuori schermo
  (`selected: app-creator` con `first: Camera`). Ora la selezione *segue la
  cima* finché non la si sposta, e si "pinna" solo su un'azione vera (frecce,
  rotella, Tab). Digitare la spinna: una query nuova è una domanda nuova.

E due che l'albero di accessibilità ha reso visibili, e che a leggere il DOM non
si vedevano: il glifo Tabler di una skill contribuiva al nome accessibile della
riga con un carattere della zona a uso privato (`aria-hidden` ora), mentre
quello del server — che invece porta informazione — si nomina con `aria-label`.

## Passo 5 — geometria e gesture

Girato il 30/08/2026 sullo stesso AVD `jenny_square`, build debug da
`app:installDebug`, navigazione a **gesture** (`navigation_mode = 2`). Per 5.1 e
5.4 **gesti veri** (`adb shell input swipe` e `input tap`, durate da 80 a
1200 ms), non `dispatchTouchEvent` da CDP: il punto è come li vede il sistema,
che CDP scavalca. CDP serve solo a leggere lo stato dopo. Nessun modulo JS nuovo
(la geometria sta in `mobile-launcher.js`), quindi `_UI_MANIFEST` non cambia, e
nessuna stringa nuova.

- [x] **5.1** Trascinano la maniglia e la riga del titolo; **la lista no** — passata verso il **basso** partita nella lista (720,1072 → 720,1320): la lista scorre (`scrollTop` 143 → 49), il foglio resta aperto e un `MutationObserver` sul suo `style`/`class` conta **0** mutazioni, cioè non si è mosso di un pixel né ha mai preso la classe `dragging`. Dalla maniglia (720,540 → 720,1100): 9 aggiornamenti da `translateY(14px)` a `translateY(172.7px)`, `dragging` vista addosso, e al rilascio — 172,7 > 85,5, cioè il 30% dei 285 px del foglio — si chiude. Dalla riga del titolo un trascinamento **corto** (24,1 px): torna su e resta aperto. La ✕, che sta dentro la zona, resta un pulsante: il tocco la chiude (guardia su `button` in `_onDragStart`, senza la quale il `setPointerCapture` le portava via il `click`)
- [x] **5.2** Il margine viene dal ponte, non da una costante — a foglio aperto il fondo della lista sta a `y = 424` px CSS su un viewport di 432: **8 px CSS esatti** sopra il bordo della WebView, cioè i 24 px fisici che il nativo riporta. Controprova che non è cablato: passando a tre pulsanti il valore diventa `0px` da solo (là la WebView finisce dove comincia la zona, sovrapposizione zero), e tornando a gesture torna `8px`
- [x] **5.3** `JennyGestureBridge.getBottomGestureInset()` — torna i px **fisici** di WebView che cadono dentro `mandatorySystemGestureInsets`, non l'inset intero: letto 24 su questa geometria, che è il numero calcolato nel piano (zona `[1344,1440)`, WebView `[72,1368)`). Legge un campo `@Volatile` che il thread UI tiene aggiornato — un `runOnUiThread` non basterebbe, il metodo deve *restituire* un valore. **Si rilegge davvero, e senza ricaricare la pagina**: con un marcatore su `window` e un contatore di eventi, alzare la tastiera (che ridimensiona la WebView) porta 24 → 0 con `marker` ancora vivo e **1** `jenny-gesture-insets` ricevuto. Su un cambio di modalità di navigazione l'activity si ricrea e la SPA riparte: il valore è giusto lo stesso, ma per l'altra via
- [x] **5.4** Provato con **Jenny come app HOME dell'emulatore** (`cmd package set-home-activity`, verificata con `resolve-activity` prima e dopo; alla fine rimessa a `com.google.android.apps.nexuslauncher/.NexusLauncherActivity`). Sette passate verso l'alto partite **in fondo al foglio** — da `y = 1343`, cioè l'ultimo pixel della lista, e poi 1340, 1330, 1300 — con durate 80, 200, 600 e 1200 ms: **tutte scorrono la lista** (`scrollTop` 0 → 250 → 500 → 742 → 966 → 1392) col foglio ancora aperto, il livello `launcher` ancora presente e `mode` invariato. **Controprova nella stessa sessione, con Jenny già HOME**: la stessa passata partita a `y = 1420` — dentro la zona — va a casa e lascia `layers: []`. Gli 8 px CSS sono esattamente ciò che separa «scorre» da «si smonta tutto»
- [x] **5.5** Il foglio si ridimensiona sopra la tastiera, e la riga selezionata resta visibile — col fuoco nel campo: prima 82 px di foglio su 124 di viewport, cornice e **zero righe**; ora 116 px (da 8 a 124: tutto lo spazio meno il distacco in cima), lista 46,3 px, righe strette a 43,7, **una riga intera visibile — e la riga selezionata è quella** (`selectedFullyVisible: true`, screenshot). Tre ↓ con la tastiera su: la selezione si muove e resta dentro il riquadro. **Il meccanismo però non è quello che diceva il piano**: v. la nota qui sotto
- [x] **5.6** Niente `setGestureExclusion` sul bordo inferiore, e il perché sta scritto dove verrebbe la tentazione: nel doc-comment di `setGestureExclusion` stesso, che ora dice che vale sui bordi verticali, che in basso un rettangolo passerebbe **senza errori e senza effetto**, e che l'unica cosa che si può fare è starne fuori — con il rimando a `getBottomGestureInset`

**In più, e non richiesto dalle caselle:** `prefers-reduced-motion` verificato
emulando davvero la media feature (`Emulation.setEmulatedMedia`), non leggendo
il CSS: `.launcher-sheet` e `.launcher-scrim` passano da
`transform, visibility | 0.32s` a `none | 1e-05s` e tornano indietro. Quello che
il movimento ridotto spegne è il tratto *dopo* il rilascio (ritorno su o
discesa); il tratto sotto il dito non è un'animazione ma una manipolazione
diretta, e resta.

> **Un difetto che solo il far girare ha mostrato, e che vale per chi tocca
> questo file dopo.** La prima versione chiamava `app.whenShellReady()` dal
> costruttore di `LauncherController`. Ma quel costruttore gira **dentro** quello
> di `MobileApp` (riga 63), e `_shellReadyCbs` nasce alla riga 88: il metodo
> moriva su `undefined.push`, e con lui tutto `new MobileApp()`. Il sintomo era
> `window.mobileApp === undefined` **senza un errore in logcat** — la SPA
> caricata, il CSS applicato (il costruttore del cassetto era arrivato fin
> quasi in fondo), e niente che rispondesse. Il ponte nativo c'è già al primo
> script, quindi l'attesa non serviva a niente.

> **Il passo 3 aveva letto male la tastiera, e la correzione cambia la forma
> della soluzione.** La nota in fondo al passo 3 dice «`innerHeight` resta
> 432 px CSS, la WebView non si ridimensiona». Rimisurato oggi sullo stesso AVD:
> **la finestra si ridimensiona eccome**, `innerHeight` 432 → **124**, e
> `visualViewport.height` con lei (`offsetTop` resta 0). Il sintomo osservato era
> giusto — si cerca alla cieca — ma la causa è l'opposta: non un foglio *coperto*
> dalla tastiera, ma un foglio alto il **66% di quel che la tastiera gli ha
> lasciato**, cioè 82 px di sola cornice.
>
> Ne discende la forma di 5.5, che non è «spostare il foglio sopra la tastiera»:
> - l'altezza è il 66% del viewport **senza** tastiera (ricordato, e azzerato
>   quando cambia la *larghezza*, cioè a una rotazione), **limitato** dallo
>   spazio che c'è davvero. Con la tastiera giù non cambia niente (285 px, come
>   prima del passo 5); con la tastiera su il foglio prende tutto lo spazio meno
>   8 px, invece di rimpicciolirsi con esso;
> - `--launcher-kb-inset` copre comunque **l'altro** guscio possibile, quello che
>   non ridimensiona la finestra (`adjustPan`): lì `innerHeight` non si muove e
>   solo `visualViewport` vede la tastiera. Sul telefono vero, con la sua
>   `windowSoftInputMode`, potrebbe essere quella la via che si accende;
> - su questo schermo la tastiera si prende il **69%** dell'altezza (996 px su
>   1440): riordinare lo spazio non basta, e serve una cornice `.compact`
>   (padding stretti, righe da 43,7 px invece di 52). È l'unica ragione per cui
>   una riga intera ci sta. Su un telefono di proporzioni normali il ramo
>   `.compact` non si accende nemmeno: 66% di ~800 px CSS lascia ~500 px di
>   spazio e otto righe.

## Passo 6 — i bordi

Girato il 30/08/2026 sullo stesso AVD `jenny_square`, build debug da
`app:installDebug`, navigazione a **gesture**. Tocchi veri (`adb shell input
tap` sulle coordinate lette a runtime — attenzione: la WebView comincia a
`y = 72` px fisici, quindi `phys_y = 72 + css_y·3`) e tasti veri; CDP per
leggere lo stato. Nessun modulo JS nuovo, quindi `_UI_MANIFEST` non cambia.

- [x] **6.1** Riga «Gestisci» in fondo al foglio → `switchMode('apps')` — `#launcher-manage`, **fuori** dalla lista (non è una `option`: non si apre con ⏎ e non si trova cercando). Tocco vero: `mode` chat → `apps`, `launcher.isOpen() === false`, livelli presenti `[]`, `#app.inert === false`, scrim a `pointer-events: none`, `view-apps` a `display: flex` — **nessun overlay orfano**. Lo stesso col ⏎ sul pulsante a fuoco (il guard su `BUTTON` in `_onKeyDown` lascia passare l'attivazione nativa). Il fondo della riga sta a `y = 424` su 432, cioè gli 8 px CSS di 5.2 sono rispettati anche ora che l'ultimo elemento è lei. *Verificata anche la guardia: con la vista già su `apps` — dove `switchMode` esce subito — il tocco chiude comunque il foglio.*
- [x] **6.2** Stati vuoti distinti: **quattro**, e in più l'avviso che li scavalca. Sul telefono, guasto iniettato al livello di `window.fetch` con **il payload vero del gateway** (`{"apps": [], "error": "unavailable"}`, quello che ora risponde un ponte rotto): 27 → **5 righe** (skill e Jenny App), striscia visibile con «Incomplete list: something did not answer.» + **Riprova**, foglio ancora aperto, e **zero toast** (la guardia `!failed` impedisce l'annuncio di 22 disinstallazioni). Tocco vero su «Riprova» a guasto persistente: striscia ancora lì, pulsante riacceso; con l'endpoint sano, **un tocco** riporta 22 app Android, 27 righe e nasconde la striscia — cioè la ritentata rifà davvero la fetch. Tutte e quattro le fetch fallite → nota «Could not read the list of apps.» e `role="presentation"` sulla lista; tutte riuscite ma vuote → «No app, Jenny App or skill to open.»; query senza esito → «No results for "zzzqq".» *Il guasto del **ponte** al livello del gateway è provato dal test unitario che fa alzare un'eccezione al bridge vero (`test_list_declares_a_bridge_failure`); sul telefono non c'è modo di rompere il `PackageManager` dall'esterno, quindi lì è entrato il payload risultante, non la sua causa.*
- [x] **6.3** Un avvio fallito dice qualcosa — con un **fallimento vero**: `pm disable-user com.google.android.deskclock` a foglio aperto lascia la riga stantia (disabilitare non emette `PACKAGE_REMOVED`: 27 righe prima e dopo), e il tocco vero su quella riga produce il toast `mobile-toast error` «Could not start Clock — it may have been uninstalled or disabled.», **il foglio resta aperto** (`layers: [launcher]`, `mode: chat`) e niente parte. Controprova nella stessa sessione: riabilitato il pacchetto, lo stesso tocco porta `topResumedActivity` su `com.google.android.deskclock/…DeskClock` e al ritorno il foglio è chiuso, senza toast. *Il pacchetto è stato riabilitato: `pm list packages -d` è tornato ai quattro di partenza.*
- [x] **6.4** Stringhe in `it.json` **e** `en.json`, niente testo cablato — quattro chiavi nuove sotto `launcher` (`manage`, `error`, `loadFailed`, `retry`) e una sotto `apps` (`launchFailed`). Parità **letta, non guardata**: `tests/webui/test_i18n_parity.py` verde (650 → 655 chiavi per file, insiemi identici, segnaposto `{name}` uguali nelle due lingue), più un test che le cinque chiavi esistano per nome in entrambe. A schermo: `it` e `en` provate girando (le prove sopra sono in inglese, la lingua dell'emulatore)
- [x] **6.5** `docs/using/app-launcher.md` aggiornato — **stesso percorso, stesso `# H1`**: quel file genera una rotta pubblica del sito e la sua voce di menu, e spostarlo o rinominarlo cambierebbe un URL. Riscritto il contenuto: il cassetto è il lanciatore, la scheda è la gestione, e i due limiti che 6.2 e 6.3 hanno chiuso non sono più elencati come limiti — al loro posto c'è cosa fa adesso. Aggiunti i limiti veri di oggi (app disabilitata = riga stantia, «Gestisci» e avviso che spariscono con la tastiera su, Tab che passa per tutte le righe, frequenza senza decadimento). Aggiornate le quattro righe che lo descrivevano altrove (`docs/README.md`, `webui-tour.md`, `start/launcher-setup.md` ×2, `wiki.md`); nessun file spostato, nessun link rotto
- [x] **6.6** `ruff check jenny/ tests/ && npx pyright jenny/bus jenny/command jenny/runtime jenny/session && python3 -m pytest -q` — «All checks passed!», «0 errors, 0 warnings, 0 informations», **8865 passed, 5 skipped** in 138 s. (`pytest` nudo non gira su questa macchina: serve `python3 -m`.)

**In più, e non richiesto dalle caselle:**

- [x] **6.7** Un test che asserisce che **il foglio esiste**: i contract dei passi 3-5 guardano tutti il *controller*, e `LauncherController` esce in silenzio (`if (!this.sheet) return`) se i nodi non ci sono. Cancellare il blocco HTML lasciava verdi tutti gli altri test e un pulsante che non apre niente. `test_the_sheet_is_actually_in_the_page` guarda i nodi, e che stiano **dopo** `.app`
- [x] **6.8** Il difetto che il passo 6 stava per introdurre, trovato misurando: con la tastiera su, «Gestisci» (30 px) e l'avviso (28 px) portavano la lista da 46 px a **14 px, zero righe intere** — il difetto di 5.5 rimesso in piedi da un bordo. In `.compact` spariscono entrambi: rimisurato sullo stesso schermo, lista di nuovo a 46,3 px e **una riga intera** anche a guasto acceso, e alla discesa della tastiera la striscia torna (`display: flex`, testo integro)

## Passo 7 — verifica sul telefono

> **Girato sul Titan 2 il 31/08/2026**, build di release firmata, PID annotato.
> **5.4 riconfermato sul dispositivo vero**: passata verso l'alto da y=1330
> (ultimo pixel della lista) → la lista scorre, foglio aperto; **la stessa
> passata da y=1435, dentro la fascia obbligatoria → `goHome()`, tutto smontato.**
> Il margine fa il suo lavoro su questo telefono, non solo sull'emulatore.
> **Difetto trovato solo qui:** la mascotte (z-index 120) restava dipinta sopra
> il foglio (100) e le sue righe — `inert` toglie i tocchi, non l'impilamento.
> Corretto; preesistente in forma identica sopra l'overlay delle mini-app (110),
> **non toccato**.

**Il telefono non era collegato** (30/08/2026): 7.2, 7.3 e 7.4 restano aperte per
definizione. Fatto tutto ciò che non lo richiede — v.
[`apps-drawer-handover.md`](./apps-drawer-handover.md) per la sequenza esatta da
eseguire quando il Titan 2 si ricollega.

- [x] **7.1** Build di release su worktree pulito (Chaquopy impacchetta l'albero di lavoro, non HEAD) — `git status` pulito, `ANDROID_HOME=$HOME/Library/Android/sdk ./gradlew app:assembleRelease` da `<worktree>/android`: **BUILD SUCCESSFUL in 48s**. L'unico `[jenny] WARNING` dell'output intero (non del `tail`) è quello atteso della firma assente: `keystore.properties` è gitignored e nel worktree non c'è, quindi esce `app-release-unsigned.apk` (75,7 MB) — che va benissimo, perché qui non si installava niente. Gli altri WARNING sono le regole ProGuard delle librerie (costruttore di default implicito) e un `setPassword` deprecato in `SshBridge.kt`: entrambi preesistenti, nessuno tocca il cassetto
- [x] **7.1b** **R8 non ha mangiato il metodo nuovo del ponte** — la prova che i passi 0-6, tutti su build *debug*, non potevano dare: `isMinifyEnabled = true` vale solo in release, e `getBottomGestureInset()` è raggiunto **solo per reflection** da `@JavascriptInterface`. Letto nel dex vero (`unzip classes.dex` + `dexdump` di build-tools 37.0.0, `apkanalyzer` non è installato in questo SDK): il metodo c'è come `com.flagdizero.jenny.MainActivity$JennyGestureBridge.getBottomGestureInset:()I`, `PUBLIC FINAL`, **col nome non offuscato** e dentro la classe non rinominata; `dexdump -a` mostra su di lui `VISIBILITY_RUNTIME Landroid/webkit/JavascriptInterface;`, che è ciò che la WebView cerca a runtime. Il corpo legge il campo `@Volatile` via `access$getBottomGestureInsetPx$p`. Lo tiene in vita `-keep class com.flagdizero.jenny.** { *; }` (`proguard-rules.pro:5`), **ma la regola non è la verifica**: qui è stato letto nell'APK
- [x] **7.1c** Suite anche sulla **versione del dispositivo** (3.11, come Chaquopy; qui `python3` è 3.14) — venv `python3.11 -m venv` con `pip install -e <worktree>` (verificato che `jenny.__file__` punti al *worktree*, non all'albero principale) e pytest lanciato **da dentro** il worktree: **8864 passed, 6 skipped**. Su 3.14 nello stesso albero: **8865 passed, 5 skipped**. La differenza è **una sola** ed è dichiarata: `test_python_exec_sandbox.py:654 — onexc esiste da 3.12`, cioè una guardia di versione, non un guasto. Gli altri cinque skip sono identici sulle due versioni. *Niente che somigli al 12/08/2026 (161 failed / 137 errors su 3.11).* Con loro: `ruff check jenny/ tests/` → «All checks passed!», `npx pyright jenny/bus jenny/command jenny/runtime jenny/session` → «0 errors»
- [x] **7.2** Aperto AdAway dal foglio sul Titan 2, Indietro, conversazione ritrovata dov'era (v. §Prova sul dispositivo)
- [ ] **7.3** Aprire una Jenny App dal foglio e verificare la catena Indietro completa: schermata interna → app → foglio → chat
- [ ] **7.4** Una sessione d'uso vera, e poi guardare se l'ordine della lista somiglia a come si usa davvero il telefono
