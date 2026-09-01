# Il cassetto che si digita — cosa fare quando il telefono è collegato

Il ramo `feat/apps-drawer` è finito e provato, ma **su un emulatore quadrato**
([`emulator-setup.md`](./emulator-setup.md)), non sul Titan 2. Questo file è
l'elenco ordinato delle cose che si possono fare **solo** con il dispositivo in
mano, con i comandi esatti. Lo stato di esecuzione sta in
[`apps-drawer-checklist.md`](./apps-drawer-checklist.md), il ragionamento in
[`apps-drawer-plan.md`](./apps-drawer-plan.md).

Ordine consigliato: **§1 → §2 → §3 → §4 → §5**. Le prime due sono letture che
non richiedono nemmeno una build; §3 è la build da installare; §4 e §5 sono le
caselle vere.

---

## Stato al 31/08/2026 — cosa è già stato fatto sul telefono

Sessione di prova sul Titan 2, build di release firmata da `c4a8d96`:

| | esito |
|---|---|
| 0.1a `navigation_mode` | **2 — gesture attiva.** Il passo 5 serve davvero qui |
| 0.3 `wm density` | **400**, non 480 → viewport 574×576 CSS → **il dock c'è** |
| 5.4 sul dispositivo | passata da y=1330 (fondo lista) → **scorre**; da y=1435 (dentro la fascia) → **`goHome()`**. Il margine funziona |
| 7.2 | AdAway aperta dal foglio, Indietro, conversazione dov'era |
| 7.3 | Todo aperta, Indietro → **torna al foglio con la query intatta** |
| tastiera software | **non compare** (tastiera fisica): il rischio `kb-open` non si materializza su questo telefono |
| mascotte sopra il foglio | **difetto trovato e corretto** (`c4a8d96`) |

**Resta aperto e serve una mano umana:** la rotella (§sotto) e 7.4, che vuole
giorni d'uso, non un comando.


## 0. Prima di tutto: `kb-open` si incolla?

Il punto più fragile della catena (v. la sezione omonima nel piano). Col foglio
aperto e **nessuna tastiera a schermo**:

```bash
adb shell input tap <x> <y>   # apri il cassetto dal composer
```

poi, via CDP, leggi `document.querySelector('.launcher-sheet').classList` e
`getComputedStyle(...).paddingBottom`. Atteso: **niente `kb-open`**, e un
`padding-bottom` pari al valore di `getBottomGestureInset()` diviso il dpr.

Se `kb-open` c'è senza tastiera, il margine è zero e la lista sta dentro la
fascia della gesture di home: è il difetto che tutto il passo 5 esiste per
evitare, e va corretto prima di qualunque altra cosa in questo documento.


## Prima di tutto

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
adb devices -l          # deve mostrare il Titan 2, e uno solo
export ANDROID_SERIAL=<serial del Titan 2>
```

`ANDROID_SERIAL` va pinnato: se l'emulatore `jenny_square` è ancora acceso,
senza quella variabile `adb` sceglie a caso e ogni misura successiva è del
dispositivo sbagliato. (`adb emu kill` lo spegne, se non serve più.)

---

## §1 — Le due letture che chiudono la casella 0.1a *(nessuna build)*

Sono la prima cosa da fare, e costano due comandi. Vanno lette **prima** di
toccare qualunque altra cosa, perché una delle due può riaprire una decisione
di progetto.

```bash
adb shell wm density              # → "Physical density: NNN"
adb shell wm size                 # → "Physical size: 1440x1440"
adb shell settings get secure navigation_mode   # 0 = tre pulsanti, 2 = gesture
```

**Cosa farne.**

- **`wm density`** decide se il dock esiste sul Titan 2, che è la domanda
  dietro D1 e D2. `mobile-style.css:3387` ha
  `@media (max-height: 500px) { .dock { display: none } }`, e il conto è
  aritmetico: a 480 dpi uno schermo da 1440 px è alto **480 px CSS in tutto**,
  barre comprese — 480 < 500 sempre, quindi **il dock non c'è**. La soglia sta
  intorno ai **~432 dpi**: sopra, il dock sparisce; sotto, resta.
  - Se il dock **non c'è**: D1 e D2 sono confermate come sono scritte (si apre
    dal composer, il dock non si tocca) e la casella 0.3 si chiude.
  - Se il dock **c'è**: nulla si rompe — la feature è puramente additiva — ma
    si riapre la nota di D2 nel piano («no, lo slot del dock non apre il
    foglio»), che dice dove si scriverebbe la riga se un giorno si volesse il
    contrario: `mobile-app.js::switchMode`. **Non è un lavoro da fare adesso**;
    è una decisione da riprendere con il numero in mano.
  - Controprova a schermo, se serve: da CDP (v. §3), `matchMedia('(max-height:
    500px)').matches` e `getComputedStyle(document.querySelector('nav.dock')).display`.
- **`navigation_mode`** dice se il Titan 2 è in gesture (`2`) o a tre pulsanti
  (`0`). Con `0` la fascia della gesture di home non esiste e
  `getBottomGestureInset()` deve tornare **0**; con `2` deve tornare un numero
  positivo. **In nessuno dei due casi il passo 5 si salta**: è
  un'impostazione dell'utente e può cambiare senza preavviso, e tutto il lato
  CSS legge il valore a runtime proprio per questo.

Annotare i due numeri nella casella 0.1a e in 0.3 della checklist.

---

## §2 — La rotella *(la vera incognita del passo 4)*

Il Titan 2 ha una rotella fisica e **non si sa quali eventi emetta**: `wheel`,
i codici delle frecce, o altro. Sull'emulatore la rotella non c'è, quindi da lì
la domanda non era chiudibile in nessun modo. Il passo 4 copre le due letture
più probabili — ↑↓ in `_onKeyDown` (provate con tasti veri) e `wheel` in
`LauncherController._onWheel` (fatto girare, ma attraverso la pipeline di input
di Chromium, non con un dito su una rotella).

**Il test è una riga.** Con la SPA aperta e la WebView collegata a CDP (§3),
si installa un registratore e si gira la rotella:

```js
// da Runtime.evaluate sulla WebView
window.__wheelProbe = [];
addEventListener('keydown', e => window.__wheelProbe.push(
  ['keydown', e.key, e.keyCode, e.code]), true);
addEventListener('wheel', e => window.__wheelProbe.push(
  ['wheel', e.deltaY, e.deltaMode]), true);
'armato';
```

Poi: aprire il cassetto (tocco sul pulsante a sinistra del composer), **girare
la rotella di qualche tacca in entrambi i versi**, e leggere:

```js
window.__wheelProbe;
```

**Come si legge il risultato:**

| Cosa esce | Cosa vuol dire |
|---|---|
| `['keydown','ArrowDown',40,'ArrowDown']` e simili | funziona già: `_onKeyDown` la gestisce, niente da scrivere |
| `['wheel', ±N, 0]` | funziona già: `_onWheel` la gestisce. Verificare che `WHEEL_PIXELS_PER_STEP = 24` dia un passo per tacca e non tre; se il ritmo è sbagliato, è **quella sola costante** da tarare |
| `['wheel', ±N, 1]` o `2` | funziona già, ramo `deltaMode !== 0`: lì l'unità è già un passo |
| qualcos'altro, o **niente** | è il caso non coperto. **Il punto dove attaccare una terza sorgente è uno solo: `_moveSelection(step)`** in `mobile-launcher.js` — è già l'unico modo di muovere la selezione, e sia le frecce sia la rotella ci finiscono dentro. Non aggiungere una seconda strada altrove |

Se dalla rotella non esce niente **nemmeno a foglio chiuso**, il problema è a
monte della SPA (la rotella non arriva alla WebView) e non è lavoro del
cassetto: annotarlo e basta.

Aggiornare poi la nota «Non si sa quali eventi produca la rotella del Titan 2»
in fondo al piano, e quella in fondo al passo 4 della checklist.

---

## §3 — La build da installare: **release**, non debug

> **Questo è il punto in cui è più facile fare un danno. Leggerlo per intero
> prima di lanciare qualcosa.**

Sul Titan 2 c'è installato l'**APK di release firmato**.
`./gradlew app:installDebug` — cioè quello usato per tutte le prove
sull'emulatore — **fallisce lì con `INSTALL_FAILED_UPDATE_INCOMPATIBLE`**,
perché la firma di debug non è quella di release. E la via d'uscita ovvia è la
peggiore: **disinstallare cancellerebbe workspace, chiavi API, cronologia e
memoria**. Non si disinstalla.

La strada giusta è costruire un **release firmato** e installare quello.

### 3.1 — Il keystore

`android/keystore.properties` è **gitignored**, quindi nel worktree
`jenny-apps-drawer` non c'è: senza, la build esce **non firmata** (è ciò che è
successo alla casella 7.1, ed era voluto, perché lì non si installava niente).
Va copiato dentro **e cancellato dopo**:

`$REPO` è il checkout principale, `$WORKTREE` il worktree:

```bash
cp $REPO/android/keystore.properties \
   $WORKTREE/android/keystore.properties
```

*(In alternativa, senza copiare niente: esportare
`JENNY_KEYSTORE_PATH` / `JENNY_KEYSTORE_PASSWORD` / `JENNY_KEY_ALIAS` /
`JENNY_KEY_PASSWORD`, che `app/build.gradle.kts` legge allo stesso modo. È la
via più pulita — non lascia un file da ricordarsi di cancellare.)*

### 3.2 — La build

**Il worktree deve essere pulito**: Chaquopy impacchetta l'**albero di lavoro**,
non `HEAD`. Un file mezzo modificato finisce dentro l'APK.

```bash
cd $WORKTREE
git status --short            # deve essere vuoto
cd android
export ANDROID_HOME=$HOME/Library/Android/sdk
./gradlew app:assembleRelease 2>&1 | tee /tmp/rel.log
```

**Non giudicare l'esito da un `tail`**: gli avvisi di Gradle escono **in cima**
e il riepilogo in fondo. Si controlla l'output intero:

```bash
grep -n "\[jenny\]" /tmp/rel.log       # ← la firma si dichiara qui
grep -ni "warn\|error\|unsigned" /tmp/rel.log
```

Se compare `[jenny] WARNING: release signing credentials not found`, **la build
è NON firmata** e non si installerà: il keystore non è stato letto, si torna a
3.1. Con la firma a posto l'artefatto è `app-release.apk` (non
`app-release-unsigned.apk`) sotto
`android/app/build/outputs/apk/release/`.

### 3.3 — L'installazione

```bash
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

`-r` aggiorna in place e **conserva i dati**. Se anche così arriva
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`, **fermarsi**: vuol dire che la firma non
è quella dell'APK già sul telefono, e la risposta non è disinstallare — è
capire quale keystore ha firmato quello installato.

### 3.4 — Dopo

```bash
rm $WORKTREE/android/keystore.properties
git -C $WORKTREE status --short   # deve tornare vuoto
```

### 3.5 — Collegare la WebView a CDP

**Sulla release probabilmente funziona lo stesso, ma non darlo per scontato.**
`WebView.setWebContentsDebuggingEnabled(true)` sta in un `init` della companion
di `AgenticSearchBridge.kt:40` — **senza guardia su `BuildConfig.DEBUG`**, ed è
così anche su `main`, quindi non è una novità di questo ramo. È una chiamata di
processo: accende l'ispezione per *tutte* le WebView, release compresa. Quello
che non è garantito è **quando** quella classe viene caricata: se il ramo della
ricerca agentica non è mai stato toccato in quella sessione, l'`init` potrebbe
non essere ancora girato.

Si verifica in un comando, e la risposta è sì/no:

```bash
adb shell cat /proc/net/unix | grep webview_devtools_remote
```

Se non esce niente, CDP non c'è: le letture di §1 e §2 vanno fatte **senza**.
Per §2 la sostituzione è a occhio — girare la rotella con il cassetto aperto e
guardare se l'evidenziazione si muove (e in quale verso), il che distingue
«coperto» da «non coperto» anche se non dice *quale* dei due rami si è acceso.
Per §1 i tre `adb shell` di sopra bastano da soli.

Procedura CDP, quando il socket c'è (da [`emulator-setup.md`](./emulator-setup.md)):

```bash
adb shell cat /proc/net/unix | grep -o "webview_devtools_remote.*" | head -1
adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>
curl -s http://127.0.0.1:9222/json/list      # il target con url html-mobile
```

Il socket porta il **PID**: dopo ogni force-stop cambia e il `forward` va rifatto.

---

## §4 — Le caselle 7.2 e 7.3

Sono scritte in checklist e vanno spuntate solo dopo averle **fatte girare**.

### 7.2 — «Aprire un'app Android dal foglio, tornare indietro, e ritrovare la conversazione dov'era»

1. Chat, con qualche messaggio a schermo. **Annotare dove si è**: quale
   messaggio è in cima, e `scrollTop` della conversazione se si può leggere.
2. Tocco sul pulsante a sinistra del composer → il foglio sale.
3. Toccare una app Android qualsiasi (o digitarne il nome e ⏎).
4. **Atteso**: l'app parte e **il foglio si chiude** — un'app Android porta via
   tutto il task, e al ritorno ritrovarlo aperto sopra la conversazione sarebbe
   un overlay che nessuno ha chiesto (`_activate`, ramo `entry.kind === 'android'`).
5. Tasto Indietro / gesture indietro dall'app → si torna a Jenny.
6. **Atteso**: la chat è dov'era, allo stesso punto di scorrimento, senza
   ricaricamenti. Nessun foglio a schermo.

Controprova utile nella stessa sessione: **un avvio che fallisce non chiude il
foglio** (6.3). Disabilitare un pacchetto (`adb shell pm disable-user <pkg>`)
lascia una riga stantia — disabilitare non emette `PACKAGE_REMOVED` — e il
tocco su quella riga deve produrre un toast rosso «Impossibile avviare *X*…»
**con il foglio ancora aperto**. Riabilitare dopo:
`adb shell pm enable <pkg>`, e verificare con `adb shell pm list packages -d`
che l'elenco dei disabilitati sia tornato quello di prima.

### 7.3 — «Aprire una Jenny App dal foglio e verificare la catena Indietro completa»

Serve una Jenny App con **almeno una schermata interna** (altrimenti la catena
è di un anello più corta e non prova quello che deve provare).

1. Cassetto aperto → toccare una Jenny App.
2. **Atteso**: la mini-app compare **sopra** il foglio, che resta aperto sotto
   (livelli `[miniapp, launcher]`).
3. Navigare a una schermata interna della mini-app.
4. Indietro → **schermata interna → app**.
5. Indietro → **app → foglio**, e la query di ricerca **intatta**. È l'anello
   che costa una riga in `mobile-apps.js::handleBack` (`if
   (window.mobileApp.launcher?.isOpen()) return true;`): senza, quella
   pressione smonterebbe due livelli invece di uno.
6. Indietro → se il campo ha del testo, **prima si svuota**; la pressione dopo
   chiude il foglio. **Foglio → chat.**
7. Indietro ancora → il comportamento normale della chat (non deve chiudere
   l'app né tornare indietro nella history per colpa del cassetto).

Fare la stessa catena **anche con Esc**, se la tastiera fisica ce l'ha: Esc e
Indietro sono lo stesso tasto per il cassetto (`keyboard.register('escape')`
finisce in `handleHardwareBack()`), e devono comportarsi identici.

---

## §5 — La casella 7.4, e la domanda della frecency

> **Non si fa in un pomeriggio.** Vuole *qualche giorno d'uso vero* del
> telefono come telefono. Fino ad allora resta aperta, ed è giusto così: il
> piano dice esplicitamente che l'algoritmo non si può giudicare senza averlo
> usato.

### Cosa fare adesso

Niente, se non **usare il cassetto** al posto della scheda per aprire le cose.
Il ranking impara solo dagli avvii che passano di lì.

### Cosa guardare dopo qualche giorno

Aprire il cassetto **a campo vuoto** e guardare la lista sotto il titolo
«Più usate», dall'alto:

1. **Le prime cinque righe sono le cinque cose che apri davvero di più?**
   Se sì, la regola attuale (pertinenza → frequenza → recenza) va bene e non si
   tocca niente. Spuntare 7.4 e chiudere la questione nel piano.
2. **C'è in cima qualcosa che non apri da settimane?** È *il* difetto noto della
   frequenza pura: una app aperta cinquanta volte il mese scorso e mai più resta
   inchiodata lì per sempre. Se succede, e dà fastidio, allora la domanda è
   matura.
3. **Quello che apri più spesso *adesso* è più in basso di quello che aprivi
   prima?** Stesso sintomo, visto dall'altra parte.

Il dato grezzo si legge da `localStorage`, chiave `launcher-usage`, formato
compatto `{"<chiave>": [conteggio, ultimoMs]}` con chiavi `android:<pkg>` /
`jenny:<slug>` / `skill:<nome>`. Guardarlo aiuta a distinguere «l'ordine è
sbagliato» da «non ho ancora usato abbastanza il cassetto».

### Se la risposta è "serve un decadimento"

È uno scambio di **due righe** in `rankEntries` (`shared/launcher-rank.js`), che
è un modulo **puro** con undici test propri: si cambia sotto node, senza
telefono. Due direzioni, e vanno decise con i numeri sotto gli occhi, non ora:

- **frecency**: pesare il conteggio con un decadimento sulla recenza. Vuole una
  costante di decadimento, che è esattamente ciò che non si poteva tarare senza
  un giorno di dati — e che dopo una settimana d'uso si può.
- **scambiare i due criteri**: recenza prima, frequenza poi. Più semplice, e
  rende il gruppo in cima davvero «recenti». Se si sceglie questa,
  **l'etichetta va rimessa a «Recenti»** (chiave `launcher.recent` in
  `it.json` / `en.json`, che oggi vale «Più usate» / «Most used»): l'etichetta
  e l'algoritmo devono dire la stessa cosa, ed è già stato sbagliato una volta.

---

## §6 — Cosa NON serve il telefono per fare, e che è già fatto

Perché nessuno lo rifaccia credendo che manchi:

- **Build di release** (7.1): fatta su worktree pulito, `BUILD SUCCESSFUL`,
  APK non firmato — che è quello che serviva, perché non si installava niente.
- **R8 non ha mangiato `getBottomGestureInset()`** (7.1b): letto nel dex vero
  con `dexdump`, nome non offuscato e annotazione `@JavascriptInterface`
  presente a runtime. È la sola cosa che le prove su debug non potevano dire.
- **Suite su Python 3.11**, la versione del dispositivo (7.1c): 8864 passed,
  6 skipped, contro 8865/5 su 3.14 — un solo skip di differenza, dichiarato
  (`onexc esiste da 3.12`).
- `ruff`, `pyright` sul sottoinsieme bloccante: verdi.

---

## §7 — Tre cose viste rileggendo tutto il ramo, da tenere d'occhio sul telefono

Non sono difetti osservati: sono punti in cui il telefono può dire qualcosa che
l'emulatore non poteva. Dettagli nel referto della revisione.

1. **`kb-open` azzera il margine della gesture** (`.launcher-sheet.kb-open {
   padding-bottom: 0 }`), e la classe si accende anche con
   `layoutH < this._fullViewportH` — dove `_fullViewportH` è un massimo che si
   azzera **solo a un cambio di larghezza**. Sul Titan 2, con la tastiera
   *fisica*, la tastiera software potrebbe non salire mai e la classe non
   accendersi mai: benissimo. Ma **se il telefono restringe la finestra in
   altezza per qualcos'altro** (multi-finestra, una barra di sistema che
   compare), la classe si incolla e il foglio perde il margine mentre tocca
   ancora il fondo dello schermo. Da guardare: a foglio aperto e tastiera giù,
   `document.documentElement.style.getPropertyValue('--gesture-inset-bottom')`
   deve essere > `0px` in modalità gesture, e `#launcher-sheet` **non** deve
   avere la classe `kb-open`.
2. **`getBottomGestureInset()` va letto sul Titan 2, non dedotto.** Le soglie
   di gesture le decide la shell di sistema, e Unihertz ci mette la propria su
   un altro livello di Android: il **32 dp / 96 px** del piano è un numero
   dell'emulatore. Da leggere: il valore vero, e la controprova che passando a
   tre pulsanti va a `0px` da solo.
3. **La prova che regge tutto — 5.4 — non è stata fatta con Jenny come launcher
   vero.** Sull'emulatore Jenny è stata resa HOME apposta
   (`cmd package set-home-activity`); sul Titan 2 lo è per davvero. Da rifare
   lì: una passata verso l'alto partita **nell'ultimo pixel della lista** deve
   scorrere la lista, e la stessa passata partita **sotto** la lista, dentro la
   fascia, deve andare a casa. Gli 8 px CSS misurati sull'emulatore sono
   esattamente ciò che separa le due cose, e sul Titan 2 quel numero è un altro.

---

## Vedi anche

- [`apps-drawer-checklist.md`](./apps-drawer-checklist.md) — lo stato di esecuzione
- [`apps-drawer-plan.md`](./apps-drawer-plan.md) — le decisioni e «Cosa NON è stabilito»
- [`emulator-setup.md`](./emulator-setup.md) — l'AVD quadrato, CDP, onboarding senza chiavi
