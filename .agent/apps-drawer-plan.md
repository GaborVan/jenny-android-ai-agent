# Il cassetto che si digita — piano

Il lanciatore esce dalla scheda Apps e diventa un foglio sopra la conversazione,
il cui corpo non è una griglia ma **un campo di testo con sotto una lista
ordinata**. Fonde due delle cinque direzioni del rilievo: *Cassetto* (dove vive)
e *Digita* (come si usa).

Il rilievo che lo motiva — 28% di cornice fissa, 5,9 schermate di scorrimento,
22% di icona per cella, 18 descrizioni scaricate e buttate — non si ripete qui.
Questo file è il piano; la diagnosi sta nell'artifact *Cinque direzioni per Apps*.

---

## Il vincolo che decide la forma

**Il bordo inferiore non è nostro, e non lo diventa.** La documentazione Android
sulla navigazione a gesture è esplicita: dalle gesture di home e quick-switch le
app non possono chiamarsi fuori come fanno col Back. Il bridge che il repo già
usa per la mascotte — `setGestureExclusion`
([`MainActivity.kt:880`](../android/app/src/main/java/com/flagdizero/jenny/MainActivity.kt),
chiamato da [`mobile-jenny.js:186`](../jenny/templates/ui/assets/mobile-jenny.js))
— vale sui bordi verticali, dove il Back *si può* escludere. In basso no.

**E qui è peggio che altrove, perché Jenny è il launcher del dispositivo.** Una
gesture di home dentro Jenny non porta via a un'altra app: arriva a
`MainActivity.onNewIntent` → `goHome()`
([`mobile-app.js:566`](../jenny/templates/ui/assets/mobile-app.js)), che smonta
*tutti* gli overlay e chiama `collapseToRoot()` su ogni controller istanziato.
Una passata verso l'alto partita per sbaglio nell'inset non chiude il foglio:
azzera la sotto-struttura di tutta la UI.

**Il secondo vincolo è interno.** In chat i trascinamenti verticali sono
promessi allo scroller per contratto: `setupSwipeNav`
([`mobile-app.js:845`](../jenny/templates/ui/assets/mobile-app.js)) fa axis-lock
con 10 px di slop e poi `if (Math.abs(dx) <= Math.abs(dy)) { reset(); return; }`.
E il basso verso l'alto è già preso una terza volta: `mobile-drawer.js:43` chiude
un drawer con uno swipe verticale rapido.

> Conclusione che regge tutto il resto: **il foglio non si apre con una gesture,
> e il suo contenuto scorrevole non tocca il fondo dello schermo.** Tolti questi
> due, non resta nessun conflitto da arbitrare.

---

## Le decisioni, e perché

**D1 (rivisto il 30/08 dopo il passo 0) — Si apre da un controllo nella riga
del composer.** L'apertura originale era «un tocco sullo slot Apps del dock». Il
passo 0 ha misurato che **quello slot potrebbe non essere a schermo**: v. la
sezione «Il dock potrebbe non essere sullo schermo». Un'apertura che dipende da
un elemento la cui esistenza non è accertata non è un'apertura. `#input-bar` c'è
in ogni geometria — ed è dove «sale dal composer» voleva stare fin dall'inizio.

**D2 (rivisto) — Il dock non si tocca. Per niente.** La versione precedente
toglieva `data-mode="apps"` dallo slot, così `_visibleModes()` lo perdeva e il
carosello orizzontale lo saltava «gratis». Su un dispositivo senza dock quel
gratis si paga caro: il carosello è **l'unica** navigazione rimasta (la media
query nasconde `.dock`, non i `.dock-item`, e `_visibleModes()` filtra
sull'inline style, quindi le cinque schede restano tutte nel carosello), e
togliere l'attributo renderebbe la scheda Apps irraggiungibile. Lasciandolo
com'è, la feature diventa **puramente additiva**: il dock — dove c'è — e lo
swipe continuano a portare alla scheda esattamente come oggi, e il rollback è
cancellare un pulsante.

> **Decisa il 30/08/2026 col passo 6: no, lo slot del dock non apre il foglio.**
> La domanda era se lo slot Apps *debba anche lui* aprire il foglio invece di
> cambiare vista. Restano le due ragioni per cui era stata rinviata — dipende da
> un fatto non misurato (esiste, quel dock, sul Titan 2?) e tocco → foglio con
> swipe → scheda sarebbero due destinazioni per lo stesso nome — e il passo 6
> ne ha aggiunta una terza, che è quella che chiude la questione: **adesso la
> scheda ha un nome e un ruolo dichiarati.** Con la riga «Gestisci» in fondo al
> foglio (6.1) il rapporto fra i due posti è scritto in un verso solo — dal
> foglio si va alla scheda — e agganciare il dock lo rovescerebbe a metà: lo
> stesso slot porterebbe a una cosa col tocco e all'altra con lo swipe, e la
> «Gestisci» diventerebbe una strada per tornare dove il dock ti aveva appena
> portato.
>
> Il costo di dire no è basso e reversibile: chi vuole il lanciatore lo ha a un
> tocco nella riga del composer, che c'è in ogni geometria; chi vuole la scheda
> ha il dock e lo swipe, come sempre. Il costo di dire sì sarebbe una riga di
> codice e una regola in più da spiegare. Se un giorno si misurasse che il dock
> sul Titan 2 c'è ed è il gesto naturale, la riga si scrive in
> `mobile-app.js::switchMode` e questa nota si riapre.
>
> **Conseguenza di questa decisione, osservata:** il pulsante che apre il foglio
> sta in `#input-bar`, che vive dentro `#view-chat`. Il modo corrente
> all'apertura è quindi **sempre** `chat`, e la chiusura che `switchMode` fa da
> sé (1.5) basta sempre — `switchMode` esce subito se il modo richiesto è già
> quello corrente, ma quel caso non si raggiunge. `_openManager()` chiude
> comunque il foglio *prima*: è una guardia su una proprietà di *dove sta un
> pulsante*, non del cassetto, ed è verificata girando (foglio aperto con la
> vista già su `apps`: il tocco su «Gestisci» lo chiude lo stesso).

**D3 — Il foglio è un livello di `_overlayLayers()`, fra `miniapp` e `drawer`.**
È l'innesto più redditizio del piano, perché quel registro è già letto da quattro
consumatori diversi e li serve tutti in un colpo:

| Consumatore | Cosa ottiene gratis |
|---|---|
| `handleHardwareBack` | Indietro chiude il foglio, ma **dopo** una mini-app aperta sopra di esso |
| `goHome` / `_dismissAllOverlays` | Home smonta il foglio come ogni altro livello |
| `hasOverlayAbove` | il type-ahead della chat smette di rubare i tasti mentre il foglio è aperto ([`mobile-chat.js:569`](../jenny/templates/ui/assets/mobile-chat.js)) |
| `switchMode` | va aggiunta la chiusura, come già fa `this.drawer.closeAll()` |

L'ordine conta: sopra `drawer` (un drawer laterale non copre il lanciatore),
sotto `miniapp` (un'app aperta *dal* foglio lo copre, e Indietro deve chiudere
prima l'app).

**D4 — `view-apps` resta, e diventa la destinazione «Gestisci».** Il foglio
*lancia*; disinstallare, nascondere, accendere una skill, riparare un manifest
rotto e creare una app nuova restano nella scheda, raggiunta da una riga in fondo
al foglio. Il guadagno è di rischio, non di grafica: **il primo taglio non tocca
nessuno dei tre renderer di `mobile-apps.js`**, e se il foglio non convince si
torna indietro togliendo un attributo dal dock. La scheda potrà poi essere
rifatta secondo la direzione *Tre stanze*, in un lavoro separato.

**D5 — `AppsController` resta il proprietario dei dati.** Ha già il ricaricamento
delle app Android con annuncio delle rimozioni, le app nascoste, `onPackageChanged`
e i frame `apps_list_changed` / `app_data_changed`. Il foglio legge da lì; non si
duplica quella macchina. Va reso istanziabile senza che la sua vista sia a
schermo — oggi è costruito lazy dentro `switchMode`.

> **Girato il 30/08/2026 col passo 2.** L'ostacolo era quello previsto e si
> toglie con `MobileApp.appsController()`: stessa `controllerFactories.apps`,
> istanza registrata in `this.controllers` — così `switchMode` non ne costruisce
> una seconda e `onPackageChanged` la trova — e nessuna dipendenza dal fatto che
> `view-apps` sia visibile, perché il suo markup sta in `index.html` fin dal
> boot, solo `display: none`. Il foglio si iscrive a `addChangeListener` e non
> ha né `api.`, né `fetch(`, né `wsManager`.
>
> **Un fatto sull'aggancio che il piano non diceva:** `apps_list_changed` non
> nasce da un osservatore del filesystem. Lo emette `_sync_apps_and_notify`
> (`agent/turn_states.py`) **all'inizio di un turno dell'agente**, quando il
> sincronizzatore dei tool nota che `workspace/apps/` è cambiata. Una cartella
> creata a mano non muove niente finché non parte un turno — che per provarlo
> basta che *inizi*, non che riesca: con un provider fittizio la chiamata
> all'LLM fallisce e il frame parte lo stesso. Non cambia una decisione, ma
> cambia come si prova la casella 2.4, e la prima idea (creare la cartella e
> aspettare) non avrebbe mostrato niente.

**D6 — Type-ahead, non autofocus.** All'apertura il campo **non** prende il
fuoco: su un telefono con tastiera software l'autofocus alzerebbe la tastiera e
si mangerebbe il foglio. Il primo carattere stampabile lo mette a fuoco, con la
stessa guardia già scritta per la chat (`_maybeTypeAheadFocus`,
[`mobile-chat.js:548`](../jenny/templates/ui/assets/mobile-chat.js)): niente
spazio, niente combo con modificatori, niente furto se si sta già scrivendo
altrove. Sul Titan 2 questo *è* l'interazione principale; altrove il foglio resta
un cassetto da toccare.

> **Girato il 30/08/2026 col passo 4.** Le quattro guardie non sono state
> riscritte: sono state **estratte** in `assets/shared/type-ahead.js`, un modulo
> puro (`activeElement` si passa, non si legge da `document`) che ora ha due
> chiamanti — `ChatController._maybeTypeAheadFocus` e il cassetto — e cinque
> test propri sotto node. Il caso che le motiva è quello del `keydown`
> con `key` undefined delle tastiere fisiche, ed è il primo test del file.
>
> **D6 ha prodotto una seconda decisione che il piano non aveva preso.** Se il
> fuoco all'apertura non va sul campo, *dove* va? Il passo 1 lo metteva sulla
> ✕; col ⏎ del passo 4 quella scelta si è rivelata sbagliata — con un pulsante
> a fuoco, ⏎ chiude il foglio invece di aprire il primo risultato, e "due tasti
> invece di sei schermate" diventa "due tasti e non succede niente". Ora va sul
> **contenitore** (`role="dialog"`, `tabindex="-1"`), che è anche ciò che fa
> annunciare a TalkBack il titolo del foglio invece della parola "Chiudi".
>
> **E una terza, che si è vista solo facendola girare: chi comanda la
> selezione.** Finché nessuno la sposta, segue la cima; si "pinna" su un'azione
> vera (frecce, rotella, Tab) e resta lì finché la sua voce è in lista;
> digitare la spinna, perché una query nuova è una domanda nuova. Senza la
> distinzione si ottiene uno dei due difetti opposti, entrambi osservati: la
> selezione riportata in cima sotto le dita da un `apps_list_changed`, oppure —
> col pin sempre acceso — incollata alla riga che era in cima al *primo*
> disegno, quando c'erano solo le skill e le app Android non erano ancora
> arrivate. Nel secondo caso ⏎ apriva la dodicesima voce, fuori schermo.

**D7 — Trascina solo la maniglia.** Il foglio si chiude trascinando la maniglia e
la riga delle rotaie. La lista mai. Nessun axis-lock, nessuna soglia di velocità
da tarare: decide l'origine del tocco, e decide sempre allo stesso modo.

> **Girato il 30/08/2026 col passo 5, con gesti veri.** L'unica costante che
> resta è la **distanza**: si chiude oltre il 30% della propria altezza, e un
> lancio veloce e corto fa la stessa cosa di un trascinamento lento e lungo.
> Verificato che una passata verso il basso partita nella lista non muove il
> foglio di un pixel (0 mutazioni di stile osservate) mentre scorre la lista, e
> che dalla maniglia il foglio segue il dito e poi chiude. Una guardia che il
> piano non prevedeva: la ✕ sta **dentro** la riga del titolo, cioè dentro la
> zona di trascinamento, e senza escludere i `button` il `setPointerCapture` le
> portava via il `click`.

**D8 — Il margine inferiore si misura, non si spera.** `env(safe-area-inset-bottom)`
dà l'inset della barra, non la soglia di riconoscimento della gesture: il valore
vero è `WindowInsets.getMandatorySystemGestureInsets()`, che sta solo sul lato
nativo. `JennyGestureBridge` è il posto giusto per esporlo, e la lista ci si
distanzia sopra.

> **Misurato il 30/08/2026 — su emulatore, NON sul Titan 2.** AVD `jenny_square`
> (1440×1440 fisici, 480 dpi, `density = 3.0`, Android 17 / API 37, immagine
> `google_apis_playstore_ps16k/arm64-v8a`). Ricrearlo: [`emulator-setup.md`](./emulator-setup.md).
> Sonda temporanea in `MainActivity`, tag logcat `JennyInsetProbe`.
>
> Con **navigazione a gesture** (`navigation_mode = 2`):
>
> | Inset | px fisici (l,t,r,b) | dp (l,t,r,b) |
> |---|---|---|
> | `mandatorySystemGestureInsets` | 0, 72, 0, **96** | 0, 24, 0, **32** |
> | `systemGestureInsets` | 90, 72, 90, 96 | 30, 24, 30, 32 |
> | `navigationBars` | 0, 0, 0, 72 | 0, 0, 0, 24 |
> | `systemBars` | 0, 72, 0, 72 | 0, 24, 0, 24 |
>
> **Il numero che serve al passo 5: 96 px fisici = 32 dp in basso.** I 30 dp
> laterali di `systemGestureInsets` sono la zona del Back — quella che
> `setGestureExclusion` già si riprende per la mascotte.
>
> Con **tre pulsanti** (`navigation_mode = 0`) l'inset obbligatorio scende a
> combaciare *esattamente* con la barra: 144 px / 48 dp per entrambi. Cioè: la
> WebView finisce dove comincia la zona, sovrapposizione zero. Il conflitto è
> davvero solo un problema della modalità gesture.
>
> **`env(safe-area-inset-*)` vale `0px` su tutti e quattro i lati.** Non «l'inset
> della barra invece della soglia»: *zero*. Il decor di AppCompat consuma gli
> inset delle barre prima della WebView (WebView 1440×1296 px = 480×432 px CSS,
> `dpr = 3`), quindi al CSS non arriva niente da cui dedurre alcunché. D8 non è
> un'ottimizzazione: senza il ponte nativo il lato CSS è cieco.
>
> **Quanta WebView cade dentro la zona obbligatoria.** Schermo alto 1440 px; la
> zona è la fascia `y ∈ [1344, 1440)`. La WebView occupa `y ∈ [72, 1368)`. Ne
> restano dentro **24 px fisici = 8 dp = 8 px CSS** sul bordo inferiore. È quello
> il margine che la lista del foglio deve tenersi sopra su questa geometria — non
> i 32 dp pieni, che sono misurati dal bordo dello *schermo*, non da quello della
> WebView.
>
> **Girato il 30/08/2026 col passo 5, e la sonda è andata via.** Il ponte è
> `JennyGestureBridge.getBottomGestureInset()`, che restituisce **proprio quella
> sovrapposizione** in px fisici — non l'inset intero, che sprecherebbe la
> fascia della barra di navigazione. La ricalcola il thread UI
> (`refreshGestureInsets`) su ogni dispatch di inset e ogni cambio di geometria
> della WebView, in un campo `@Volatile`; il getter lo legge, perché un metodo
> del bridge deve *restituire* un valore e `runOnUiThread` è asincrono. Quando
> cambia, il nativo manda un evento `jenny-gesture-insets` alla SPA — verificato
> che arriva **senza un ricaricamento** (marcatore su `window` sopravvissuto,
> contatore a 1). Il lato CSS lo riceve come `--gesture-inset-bottom` su
> `documentElement`, e il `padding-bottom` del foglio ci si appoggia.
>
> **La controprova che non è cablato**: passando a tre pulsanti il valore
> diventa `0px` da solo, e tornando a gesture torna `8px`.

**D9 — Il ranking parte da `localStorage`.** Frequenza e recenza per chiave
(`android:<pkg>`, `jenny:<slug>`, `skill:<nome>`). Non è un dato prezioso: se si
perde svuotando i dati dell'app, l'ordine si riforma in qualche giorno d'uso. Se
diventa fastidioso perderlo, la strada già battuta è quella delle app nascoste —
JSON privato dell'app via gateway, fuori dal workspace e fuori dai backup.

> **Girato il 30/08/2026 col passo 3.** Il ranking vive in
> `assets/shared/launcher-rank.js`, modulo **puro** (niente DOM, niente
> `window`, niente i18n) con lo storage passato dal costruttore — è quello che
> lo rende provabile sotto node senza un telefono, e la casella 3.6 chiedeva
> esattamente questo. Formato compatto `{"<chiave>": [conteggio, ultimoMs]}`,
> con un tetto di 300 chiavi tagliato per recenza. Verificato sul telefono che
> sopravvive a force-stop + riavvio, non solo che si scrive.

**Due scelte che il passo 3 ha dovuto fare, e che il piano non aveva deciso:**

**a) Cosa mettere sotto il nome di una app Android.** Non hanno una descrizione,
e inventargliela era escluso. Ci va il **nome del pacchetto**: è un dato vero,
distingue due app con la stessa etichetta, e — non previsto ma utile — diventa
un secondo campo su cui cercare, così `com.android` o `gm` trovano l'app anche
quando non se ne ricorda il nome commerciale. Il costo è una riga grigia sotto
ogni app di sistema; in un cassetto dove di solito si guardano cinque risultati,
non pesa.

**b) A campo vuoto il titolo dice «Recenti», ma l'ordine è per frequenza.**
Sono due cose diverse e la tensione è reale. La lettura che le tiene insieme:
«Recenti» nomina il *gruppo* in cima — quello che si usa — mentre dentro quel
gruppo comanda la frequenza, poi la recenza, come dice la regola di 3.3. Se
l'ordine di quel gruppo dovesse invece essere puramente cronologico, è una riga
sola in `rankEntries` (scambiare i due criteri); **la si cambia guardando la
lista dopo una settimana d'uso vera, non ora** — è la stessa domanda della
casella 7.4. Appena si digita, il titolo diventa «Risultati».

---

## I passi

**Passo 1 — il foglio vuoto che si apre e si chiude** *(mezza giornata)*
Lo scheletro e tutti gli innesti di navigazione, con dentro un contenuto finto.
È il passo che vale la pena far girare sul telefono da solo: se Indietro, Home e
il carosello si comportano bene con un foglio vuoto, il resto è contenuto.

**Passo 2 — i dati** *(mezza giornata)* — **girato il 30/08/2026**
`AppsController` istanziabile senza vista; il foglio legge le tre liste da lì e
si aggiorna sui frame che quello già ascolta.

**Passo 3 — la lista digitabile** *(una giornata)* — **girato il 30/08/2026**
Ricerca sui tre spazi di nomi con la `description` che oggi si butta
([`apps_api.py:31`](../jenny/webui/apps_api.py),
[`skills_api.py:52`](../jenny/webui/skills_api.py)); ordinamento per pertinenza,
poi per frequenza e recenza; a campo vuoto, «Recenti». Righe con nome +
descrizione, e il tipo a destra. **E le righe si attivano col tocco**: un
cassetto le cui righe non fanno niente non si può provare davvero.

**Passo 4 — tastiera e rotella** *(mezza giornata)* — **girato il 30/08/2026**
Type-ahead, ↑↓ e rotella muovono la selezione, ⏎ apre, ⇧⏎ apre la scheda del
risultato, Esc pulisce e poi chiude. **Della rotella resta ignoto quali eventi
produca sul Titan 2**: v. «Cosa NON è stabilito».

> **Esc e Indietro sono lo stesso tasto, e il piano non lo diceva.**
> `keyboard.register('escape')` manda Esc in `handleHardwareBack()`, cioè nella
> catena dei livelli: la semantica in due tappe di 4.4 (prima svuota la
> ricerca, poi chiude) si scrive quindi **una volta sola**, in
> `LauncherController.dismiss()`, e vale per entrambi. Verificato su tutti e
> due. Conseguenza che va tenuta insieme: da qui `dismiss` e `close` non
> coincidono più, quindi il livello `launcher` deve dichiarare anche un `close`
> proprio — il default `layer.close || layer.dismiss` farebbe fare a Home due
> giri invece di uno, e il conto di 1.8 lo direbbe.

**Passo 5 — geometria e gesture** *(mezza giornata)* — **girato il 30/08/2026**
Maniglia trascinabile, inset misurato dal nativo, foglio che si ridimensiona
sopra la tastiera software (`visualViewport`).

> **Il passo 3 ha alzato la priorità di 5.5 — e ne aveva letto male la causa.**
> Il sintomo era giusto: con la tastiera software su si cerca alla cieca. Il
> meccanismo no. Rimisurato col passo 5 sullo stesso AVD, **la finestra si
> ridimensiona**: `innerHeight` 432 → **124** px CSS, e `visualViewport` con
> lei. Il foglio non era *coperto* dalla tastiera, era alto il 66% di quel che
> la tastiera gli aveva lasciato — 82 px di sola cornice, zero righe.
>
> La soluzione che ne discende non è «spostare il foglio sopra i tasti»: è
> **calcolare l'altezza sul viewport senza tastiera** e limitarla allo spazio
> disponibile, così con la tastiera giù non cambia niente e con la tastiera su
> il foglio prende tutto invece di rimpicciolirsi. `--launcher-kb-inset` copre
> comunque l'altro guscio possibile (`adjustPan`, dove `innerHeight` non si
> muove e solo `visualViewport` vede la tastiera) — che è quello che il Titan 2
> potrebbe avere. Su questo schermo la tastiera si prende il **69%**
> dell'altezza, e serve anche una cornice `.compact`; su proporzioni normali
> quel ramo non si accende. Dettagli e misure: la
> [lista di esecuzione](./apps-drawer-checklist.md).

**Passo 6 — i bordi** *(mezza giornata)* — **girato il 30/08/2026**
Riga «Gestisci», stati vuoti veri (nessun risultato ≠ elenco che non si è
caricato — oggi sono lo stesso messaggio), stringhe i18n in `it.json` e `en.json`.

> **Gli stati vuoti da soli non bastavano, e il caso che li scavalca è il
> normale.** Il passo 2 aveva già tre messaggi distinti nel foglio, ma tutti e
> tre vivono nel ramo «la lista è vuota» — e quando il ponte nativo tace la
> lista **non è vuota**: skill e Jenny App arrivano tutte, e mancano solo le app
> del telefono. Nessuno stato vuoto compare, e l'unico segno è un cassetto che
> non trova *Telefono*. Da qui l'avviso sopra la lista, che è la vera risposta a
> 6.2; i tre messaggi restano per il caso in cui non arrivi proprio niente, e
> sono diventati quattro (si è aggiunto «non si è potuto leggere»).
>
> **E il guasto non era nemmeno rappresentabile.** Il gateway rispondeva
> `{"apps": []}` sia per un ponte rotto sia per un telefono senza app: la UI non
> aveva *da cosa* distinguere. Ora un guasto porta `"error": "unavailable"` — e
> l'assenza di contesto Android no, perché lì la lista è davvero vuota. Un
> difetto latente che quel campo ha reso visibile: `loadAndroidApps` con
> `announceRemovals` avrebbe annunciato come disinstallate **tutte** le app del
> telefono in un colpo, perché una lista vuota per guasto è indistinguibile da
> una lista vera in cui non c'è più niente. Ora la guardia c'è, e nella prova
> sull'emulatore i toast erano zero.
>
> **Un difetto introdotto dal passo 6 stesso, visto misurando.** Con la tastiera
> su, la riga «Gestisci» costa 30 px su 46 di lista e l'avviso ne costa 28: con
> l'avviso a schermo la lista scendeva a **14 px, zero righe intere** — cioè
> esattamente il difetto che 5.5 esiste per chiudere, reintrodotto da un bordo.
> In `.compact` spariscono entrambi e tornano quando la tastiera scende.

**Passo 7 — verifica sul telefono** *(mezza giornata)*
Il piano è pieno di affermazioni sul comportamento delle gesture che **non sono
state misurate su questo dispositivo**. Vedi sotto.

---

## Il dock potrebbe non essere sullo schermo

Trovato misurando, non cercandolo — e se regge, tocca D1 e D2, non il passo 5.

`mobile-style.css:3387` contiene `@media (max-height: 500px) { .dock { display:
none } }`. **Sull'emulatore quadrato la regola scatta**: la WebView è alta 432 px
CSS in gesture (408 con tre pulsanti), `matchMedia('(max-height: 500px)')` torna
`true`, e il `display` calcolato di `nav.dock` è `none`. Nessun JS lo riscrive:
`mobile-app.js` tocca solo `.dock-item`, mai il `display` del contenitore.
Controprova fatta: con `wm size 1440x2200` il viewport sale a 685 px CSS, la
media query smette di corrispondere e il dock torna `display: flex`, alto 56 px.

Il conto è aritmetico e non dipende dall'emulatore: a 480 dpi uno schermo da
1440 px è alto **480 px CSS in tutto**, barre comprese. 480 < 500 sempre. Se il
Titan 2 gira davvero a 480 dpi, quella media query lo prende comunque, e il dock
non c'è.

**Ma non è stato letto sul telefono.** Basta `adb shell wm density`: sopra i
~432 dpi il dock sparisce, sotto resta. È la prima cosa da fare quando il Titan 2
si ricollega, prima di scrivere una riga dei passi 1 e 2 — perché *«si apre con
un tocco sullo slot del dock»* presuppone uno slot visibile.

Nota a margine, dallo stesso giro: il dock è alto **56 px CSS**
(`--dock-height`, `mobile-style.css:42`), non 43 come dicevano il piano e la
casella 0.3.

---

## Una domanda che solo l'uso può chiudere — frecency

Il passo 3 ha implementato 3.3 alla lettera: **pertinenza, poi frequenza, poi
recenza**. Con quell'ordine la cima della lista a campo vuoto è *quel che usi di
più*, non *quel che hai usato per ultimo* — e l'etichetta originale «Recenti»
diceva un'altra cosa. Corretta il 30/08 in **«Più usate»**: si cambia
l'etichetta, non l'algoritmo, perché l'algoritmo non si può giudicare senza
averlo usato.

Resta la domanda vera, ed è nota: la frequenza pura ha un difetto conosciuto —
una app aperta cinquanta volte il mese scorso e mai più resta inchiodata in cima
per sempre. La risposta standard è la *frecency* (frequenza pesata da un
decadimento sulla recenza), ma sceglierla adesso significherebbe tarare una
costante di decadimento senza un solo giorno di dati.

**Chi decide: la casella 7.4**, dopo una sessione d'uso vera. Se serve, è uno
scambio di due righe in `rankEntries` (`shared/launcher-rank.js`), che è un
modulo puro con undici test propri: cambiarlo costa poco proprio perché è stato
isolato.

## Il punto più fragile della catena — `kb-open` che si incolla

Trovato dalla rilettura d'insieme del passo 7, **non corretto di proposito**: la
correzione richiede un dispositivo che qui non c'è.

`.launcher-sheet.kb-open` azzera il `padding-bottom`, ed è **giusto finché la
tastiera è davvero alzata**: lì il fondo del foglio non tocca il fondo dello
schermo — c'è la tastiera in mezzo, che quei tocchi se li prende lei. Il
problema è quando la classe resta accesa senza tastiera.

`kb-open` si accende su `kbInset > 0 || layoutH < this._fullViewportH`, e
`_fullViewportH` è un **massimo monotòno** che si ri-azzera solo a un cambio di
**larghezza**. Alla chiusura della tastiera si spegne da sé (l'altezza risale e
la disuguaglianza diventa falsa). Ma qualunque riduzione d'altezza *permanente*
che non sia la tastiera — multifinestra, una barra di sistema che compare —
lascia la disuguaglianza vera per sempre: la classe si incolla, e il foglio
perde il margine **mentre tocca ancora il fondo dello schermo**. Cioè
esattamente l'invariante che il passo 5 esiste per difendere.

Perché non l'ho corretto: le vie plausibili sono tutte euristiche (ri-basare
`_fullViewportH` alla perdita di fuoco del campo; pretendere che la riduzione
sia "grande abbastanza da essere una tastiera"; distinguere i due regimi dal
`visualViewport`), e sceglierne una senza poterla provare sul regime che la
farebbe scattare significa scambiare un difetto noto con uno ignoto. Sul Titan 2,
con la tastiera fisica, questo ramo **potrebbe non accendersi mai**.

**È la prima cosa da guardare quando il telefono è collegato** — v.
[`apps-drawer-handover.md`](./apps-drawer-handover.md).

## D1/D2 — chiusi dall'uso, il 31/08

Le due decisioni sono state riviste due volte, e la seconda l'ha decisa il
telefono in trenta secondi d'uso.

**Il difetto.** D1 sceglieva il composer perché «`#input-bar` c'è in ogni
geometria». Vero, e risolveva **l'asse sbagliato**: robusto rispetto alla
dimensione dello schermo, non rispetto alla *vista*. `#input-bar` esiste solo
in chat, quindi dalla scheda Apps — il primo posto dove si cerca un lanciatore —
non c'era **nessun** modo di aprire il cassetto. Sei passi di verifica
sull'emulatore non l'hanno visto perché ogni prova partiva dalla chat.

**La decisione.** Lo slot Apps del dock **apre il foglio**. La decisione era
rinviata al passo 6 con default «no» perché non si sapeva se quel dock fosse a
schermo; misurato (400 dpi, viewport 574×576) il motivo è caduto. `data-mode`
gli resta, così il carosello orizzontale continua a raggiungere la scheda: non
diventa irraggiungibile, solo meno immediata — dal foglio ci si arriva con
«Gestisci».

**Il contrappunto del passo 6 resta vero** («tocco → foglio e swipe → scheda
sono due destinazioni per lo stesso nome») ed è stato accettato consapevolmente:
è un prezzo minore di «il lanciatore si apre da una vista sola».

**Ordine del dock**, deciso nella stessa sessione: Wiki, Chat, **Apps al
centro**, Workspace, Impostazioni. Apps al centro perché è lì che sta il pollice.
L'ordine del DOM è anche quello del carosello (`_visibleModes`), quindi è un
contratto e ha un test. Wiki porta l'icona del grafo, la stessa che
l'intestazione usa già per la stessa destinazione.

## Cosa NON è stabilito

**Non si sa se il Titan 2 sia in navigazione a gesture.** ~~Non è stato letto.~~
Sull'**emulatore** `jenny_square` la modalità di partenza dell'immagine
android-37.0 è gesture (`navigation_mode = 2`), e l'interruttore è documentato in
[`emulator-setup.md`](./emulator-setup.md). Sul **telefono** resta non letto:
non era collegato nemmeno stavolta. `adb shell settings get secure
navigation_mode` — `0` tre pulsanti, `2` gesture. Si progetta comunque per il
caso peggiore: è un'impostazione dell'utente e può cambiare senza preavviso.

**Non si sa quanto sia alta davvero la soglia della gesture di home su questo
telefono.** ~~Nessuno l'ha stampata.~~ Ora un numero c'è: **32 dp / 96 px**, di
cui **8 px CSS** ricadono dentro la WebView — ma su un **emulatore Android 17**,
non sul Titan 2. Le soglie di gesture le decide la shell di sistema, e il Titan 2
non ha quella dell'emulatore: Unihertz ci mette la propria, su un altro livello
di Android. Il valore misurato serve a dimensionare il CSS e a scrivere il metodo
del bridge; **non** autorizza a cablare `8px` da nessuna parte. Il punto di D8
resta intero: si legge dal nativo a runtime, perché il numero è del dispositivo.
Col passo 5 è così: `8px` non compare in nessun sorgente, e sull'emulatore si
è visto il valore andare a `0px` da solo passando a tre pulsanti.

**Non si sa come il Titan 2 tratti la tastiera software — e la domanda è quasi
oziosa lì, ma non altrove.** Sull'emulatore la finestra si ridimensiona
(`innerHeight` 432 → 124); un altro guscio potrebbe invece far scorrere la
finestra sotto la tastiera senza ridimensionarla, e allora conterebbe solo
`visualViewport`. Il passo 5 copre entrambe le vie perché costa uguale, ma
**quale delle due si accenda sul telefono non è stato letto**. Sul Titan 2, con
la tastiera fisica, potrebbe non accendersi nessuna delle due.

**Non si sa quali eventi produca la rotella del Titan 2.** Potrebbe essere
`wheel`, potrebbero essere i codici delle frecce, potrebbe essere altro — e
sull'emulatore la rotella **non c'è**, quindi da qui la domanda non si chiude in
nessun modo. Il passo 4 copre le due letture più probabili, che portano allo
stesso posto: ↑↓ (provate con tasti veri) e `wheel` (`LauncherController._onWheel`,
fatto girare attraverso la pipeline di input di Chromium — non con un dito su una
rotella). Se la rotella emette frecce funziona già; se emette `wheel`, pure.
**Quale delle due sia vera si legge solo sul telefono**, ed è una riga di lavoro
per il passo 7: aprire il foglio e girare la rotella guardando un registratore di
`keydown`/`wheel`. Se non fosse nessuna delle due, il posto dove aggiungerla è
uno solo — `_moveSelection`, che è già l'unico modo di muovere la selezione.

~~**Non si sa se `goHome()` chiuda il foglio in modo pulito.**~~ **Chiuso il
30/08/2026 col passo 1: una chiamata, non otto.** Il rischio era reale — il ciclo
di `_dismissAllOverlays` richiama il livello fino a otto volte finché `present()`
è vero — e si evita in due modi che vanno tenuti *entrambi*:

- il foglio **non si smonta dal DOM**: resta in pagina e commuta una classe, così
  non c'è un intervallo in cui è ancora lì ma si sta chiudendo (è quello a
  fregare la mini-app, che si `remove()` dopo 200 ms);
- `present()` legge un **flag scritto sincronamente** da `close()`
  (`LauncherController._open`), non il DOM. È la parte che sopravvive a un
  ripensamento sull'animazione: se un domani il foglio si smontasse davvero, il
  flag continuerebbe a rispondere giusto.

Misurato con un contatore temporaneo su `dismiss` e l'intent Home vero
(`onNewIntent` → `goHome()`): **1**.

---

## Vedi anche

- [`apps-drawer-checklist.md`](./apps-drawer-checklist.md) — lo stato di esecuzione
- [`emulator-setup.md`](./emulator-setup.md) — l'AVD quadrato con cui sono stati
  presi i numeri di D8, e come riaccenderlo
- [`design.md`](./design.md) — vincoli di architettura
- [`jenny-apps.md`](./jenny-apps.md) — il contratto delle mini-app che il foglio apre
- `docs/using/app-launcher.md` — cosa fa oggi la sezione Android, e dove è onesta sui propri limiti
