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

**D1 — Si apre con un tocco sullo slot del dock.** L'icona griglia è già dove il
pollice la cerca. Cambia solo cosa fa: apre il foglio sopra la vista corrente
invece di cambiare vista.

**D2 — Lo slot perde `data-mode` e prende `data-action="launcher"`.** Non è
cosmetico: `_visibleModes()`
([`mobile-app.js:744`](../jenny/templates/ui/assets/mobile-app.js)) deriva le
schede navigabili da `.dock-item[data-mode]`, quindi togliendo l'attributo il
carosello orizzontale salta lo slot **gratis**, senza un caso speciale da
ricordare. Anche il click handler di `mobile-app.js:132` smette di riguardarlo da
solo.

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

## Cosa NON è stabilito

Tre cose, e la prima è quella che può far ricominciare da capo il passo 5.

**Non si sa se il Titan 2 sia in navigazione a gesture.** Tutto il ragionamento
sul bordo inferiore vale per la modalità gesture; con i tre pulsanti il conflitto
non esiste proprio. Si legge in un secondo — `adb shell settings get secure
navigation_mode`, `0` = tre pulsanti, `2` = gesture — ma **non è stato letto**,
perché mentre il piano si scriveva non c'era nessun dispositivo collegato. Va
letto prima del passo 5, non dopo. E si progetta comunque per il caso peggiore:
è un'impostazione dell'utente e può cambiare senza preavviso.

**Non si sa quanto sia alta davvero la soglia della gesture di home su questo
telefono.** D8 dice come ottenerla; nessuno l'ha ancora stampata. Finché non c'è
quel numero, qualunque margine inferiore scritto nel CSS è indovinato.

**Non si sa se `goHome()` chiuda il foglio in modo pulito.** Il ciclo di
`_dismissAllOverlays` chiama il livello fino a otto volte finché `present()` è
vero: un foglio che si smonta con una transizione di chiusura resta `present`
per la durata dell'animazione, esattamente come è già annotato per la mini-app
(«l'overlay resta nel DOM per i 200 ms della dissolvenza»). O il `present()` del
foglio ignora chi si sta chiudendo, o Home lo chiamerà otto volte a vuoto. È il
tipo di dettaglio che si vede solo a implementazione finita.

---

## Vedi anche

- [`apps-drawer-checklist.md`](./apps-drawer-checklist.md) — lo stato di esecuzione
- [`design.md`](./design.md) — vincoli di architettura
- [`jenny-apps.md`](./jenny-apps.md) — il contratto delle mini-app che il foglio apre
- `docs/using/app-launcher.md` — cosa fa oggi la sezione Android, e dove è onesta sui propri limiti
