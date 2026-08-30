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

> **Decisione rinviata al passo 6, non dimenticata.** Se lo slot Apps del dock
> *debba anche lui* aprire il foglio invece di cambiare vista è una scelta che
> dipende da un fatto non misurato (esiste, quel dock?) e che confonderebbe se
> presa male: tocco → foglio e swipe → scheda sono due destinazioni per lo stesso
> nome. Il passo 1 costruisce `openLauncher()` con l'apertura dal composer, che è
> corretta comunque; agganciarci anche il dock è una riga, da scrivere quando si
> sa. Default se nessuno decide: **non agganciarlo.**

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

**D6 — Type-ahead, non autofocus.** All'apertura il campo **non** prende il
fuoco: su un telefono con tastiera software l'autofocus alzerebbe la tastiera e
si mangerebbe il foglio. Il primo carattere stampabile lo mette a fuoco, con la
stessa guardia già scritta per la chat (`_maybeTypeAheadFocus`,
[`mobile-chat.js:548`](../jenny/templates/ui/assets/mobile-chat.js)): niente
spazio, niente combo con modificatori, niente furto se si sta già scrivendo
altrove. Sul Titan 2 questo *è* l'interazione principale; altrove il foglio resta
un cassetto da toccare.

**D7 — Trascina solo la maniglia.** Il foglio si chiude trascinando la maniglia e
la riga delle rotaie. La lista mai. Nessun axis-lock, nessuna soglia di velocità
da tarare: decide l'origine del tocco, e decide sempre allo stesso modo.

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

**D9 — Il ranking parte da `localStorage`.** Frequenza e recenza per chiave
(`android:<pkg>`, `jenny:<slug>`, `skill:<nome>`). Non è un dato prezioso: se si
perde svuotando i dati dell'app, l'ordine si riforma in qualche giorno d'uso. Se
diventa fastidioso perderlo, la strada già battuta è quella delle app nascoste —
JSON privato dell'app via gateway, fuori dal workspace e fuori dai backup.

---

## I passi

**Passo 1 — il foglio vuoto che si apre e si chiude** *(mezza giornata)*
Lo scheletro e tutti gli innesti di navigazione, con dentro un contenuto finto.
È il passo che vale la pena far girare sul telefono da solo: se Indietro, Home e
il carosello si comportano bene con un foglio vuoto, il resto è contenuto.

**Passo 2 — i dati** *(mezza giornata)*
`AppsController` istanziabile senza vista; il foglio legge le tre liste da lì e
si aggiorna sui frame che quello già ascolta.

**Passo 3 — la lista digitabile** *(una giornata)*
Ricerca sui tre spazi di nomi con la `description` che oggi si butta
([`apps_api.py:31`](../jenny/webui/apps_api.py),
[`skills_api.py:52`](../jenny/webui/skills_api.py)); ordinamento per pertinenza,
poi per frequenza e recenza; a campo vuoto, «Recenti». Righe con nome +
descrizione, e il tipo a destra.

**Passo 4 — tastiera e rotella** *(mezza giornata)*
Type-ahead, ↑↓ e rotella muovono la selezione, ⏎ apre, ⇧⏎ apre la scheda del
risultato, Esc pulisce e poi chiude.

**Passo 5 — geometria e gesture** *(mezza giornata)*
Maniglia trascinabile, inset misurato dal nativo, foglio che si ridimensiona
sopra la tastiera software (`visualViewport`).

**Passo 6 — i bordi** *(mezza giornata)*
Riga «Gestisci», stati vuoti veri (nessun risultato ≠ elenco che non si è
caricato — oggi sono lo stesso messaggio), stringhe i18n in `it.json` e `en.json`.

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
