/** Mobile Apps Controller — le tre stanze della scheda Apps.
 *
 *  La fisarmonica di prima (tre sezioni in un unico scorrimento) è stata
 *  sostituita da un segmento in cima e **una stanza per volta**, ognuna col
 *  layout che le serve: lista per le Jenny App, lista con interruttori per le
 *  skill, griglia densa con guida A–Z per le app Android. È la direzione *Tre
 *  stanze* del rilievo, e chiude quattro dei difetti che quel rilievo aveva
 *  misurato — 5,9 schermate di scorrimento (01), tre passi di riga diversi,
 *  l'errore di una app rotta che alza tutta la sua riga (05), le dodici icone
 *  puzzle identiche (03).
 */

import { api } from './shared/api-client.js';
import { escapeHtml, showToast } from './shared/utils.js';
import { confirmDialog } from './shared/dialog.js';
import { i18n } from './shared/i18n.js';
import { wsManager } from './shared/ws-manager.js';
import { currentTheme, themeTokens } from './shared/theme.js';
import { setupLongPress } from './shared/longpress.js';
import { advancedMode } from './shared/advanced-mode.js';

/** Le tre stanze, nell'ordine in cui stanno nel segmento. La chiave è anche
 *  quella salvata in `localStorage`: cambiarla dimentica la stanza attiva di
 *  chi aggiorna, non di più. */
const ROOMS = ['jenny', 'skills', 'android'];

const ROOM_LABELS = {
  jenny: 'apps.roomJenny',
  skills: 'apps.roomSkills',
  android: 'apps.roomAndroid',
};

const ROOM_SEARCH_PLACEHOLDERS = {
  jenny: 'apps.searchJenny',
  skills: 'apps.searchSkills',
  android: 'apps.searchAndroid',
};

const ROOM_STORAGE_KEY = 'apps-active-room';

export class AppsController {
  constructor() {
    this.contentEl = document.getElementById('apps-content');
    this.roomsEl = document.getElementById('apps-rooms');
    this.searchInput = document.getElementById('apps-search-input');
    /* La stanza attiva si ricorda fra una visita e l'altra (difetto 07: le
       sezioni chiuse della fisarmonica non lo erano, e ogni rientro le
       riapriva tutte). `localStorage` può alzare eccezioni — finestra privata,
       dati del sito bloccati — e una scheda che non si apre perché non si è
       potuto leggere quale stanza mostrare sarebbe un guasto sproporzionato. */
    this.activeRoom = this._loadActiveRoom();
    this._roomTabs = null;
    // La guida A–Z e il suo scorrevole, per la misura post-inserimento.
    this._azRail = null;
    this._azScroll = null;
    this.skills = [];
    this.androidApps = [];
    this.jennyApps = [];
    this._skillsLoaded = false;
    this._androidAppsLoaded = false;
    this._jennyAppsLoaded = false;
    this._openApp = null;
    // Skill in apertura nell'editor del Workspace (v. _openSkillFile): il
    // percorso ha due await, e una seconda apertura concorrente scriverebbe
    // sopra `currentDir`/`_returnMode` della prima.
    this._openingSkill = null;
    // Richieste di HTML all'app aperta in volo: nonce → { resolve, timer }.
    this._appHtmlWaiters = new Map();
    this._appHtmlSeq = 0;
    this.hiddenPackages = new Set();
    this._hiddenLoaded = false;
    this._showHidden = false;
    this._pendingReload = false;
    this._androidRefreshTimer = null;
    this._androidLoadSeq = 0;
    this._removalsAnnounced = new Set();
    /* Quali caricamenti sono **falliti**, per lista (passo 6.2). Non è la stessa
       domanda di `_*Loaded`, che dice solo "la risposta è arrivata": una fetch
       andata male segna comunque la lista come caricata — altrimenti la UI
       resterebbe a "Caricamento…" per sempre — e da lì in poi un elenco vuoto
       per guasto e uno vuoto per davvero sono indistinguibili. È esattamente il
       limite che `docs/using/app-launcher.md` denunciava. Qui restano separati,
       e il cassetto ci scrive sopra un avviso invece di un "nessuna app". */
    this._loadFailed = { skills: false, android: false, jenny: false, hidden: false };
    /* Chi vuole essere avvisato quando una delle tre liste cambia. Esiste per
       il cassetto (D5): i dati restano di questo controller — il ricaricamento
       delle app Android, l'elenco delle nascoste, `onPackageChanged`, i frame
       `apps_list_changed` — e il foglio si limita a rileggerli quando cambiano,
       invece di tenerne una seconda copia che andrebbe risincronizzata. */
    this._changeListeners = new Set();

    this.searchInput?.addEventListener('input', () => this.render());
    // Bridge from sandboxed app iframes (opaque origin → origin check is
    // meaningless; we validate the source window instead).
    window.addEventListener('message', e => this._onAppMessage(e));
    /* Agent-side storage mutations → refresh the open app iframe.

       Stream non filtrato per `chat_id`, **e deve restare così**: questi due
       frame sono broadcast a ogni connessione e non portano `chat_id` affatto
       (`ws_sender.send_app_data_changed` / `send_apps_list_changed`), quindi
       l'evento per-chat (`chat:<id>:message`) non scatterebbe mai per loro. Non
       sono nemmeno per-conversazione: i dati di una app sono gli stessi
       qualunque chat li abbia cambiati, e una app aperta va aggiornata comunque.
       Il filtro sul `chat_id` riguarda i frame che *dipingono una
       conversazione*, che stanno in `mobile-chat.js`. */
    wsManager.addEventListener('chat:message', e => {
      if (e.detail?.event === 'app_data_changed') this.notifyAppDataChanged(e.detail.slug);
      if (e.detail?.event === 'apps_list_changed') this._reloadJennyApps();
    });
    // SPA theme toggled while an app is open → restamp the iframe's theme.
    // Apps receive the resolved binary scheme, the theme accent and the rest of
    // the palette (`themeTokens`): senza quest'ultima l'app resterebbe alla
    // copia statica di jenny-kit.css e solo l'accent seguirebbe il tema.
    new MutationObserver(() => {
      const t = currentTheme();
      this._openApp?.iframe.contentWindow?.postMessage(
        { type: 'jenny:theme', theme: t.scheme, accent: t.accent, onAccent: t.onAccent,
          tokens: themeTokens() }, '*');
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    window.addEventListener('advancedmodechange', () => this.render());
    // Le etichette del segmento e i titoli dei gruppi si costruiscono qui, non
    // in `index.html`: senza questo, cambiare lingua li lascerebbe indietro.
    i18n.onLocaleChange(() => this.render());
  }

  /* ── Le stanze ─────────────────────────────────────────────────────────── */

  _loadActiveRoom() {
    try {
      const saved = localStorage.getItem(ROOM_STORAGE_KEY);
      if (ROOMS.includes(saved)) return saved;
    } catch {
      // storage non leggibile: si riparte dalla prima stanza
    }
    return ROOMS[0];
  }

  /** Cambia stanza. Pubblica perché è anche l'unica via da fuori (test, e un
   *  domani un link diretto), non solo il tocco sul segmento. */
  setRoom(room) {
    if (!ROOMS.includes(room) || room === this.activeRoom) return;
    this.activeRoom = room;
    try {
      localStorage.setItem(ROOM_STORAGE_KEY, room);
    } catch {
      // la stanza resta valida per questa visita, non per la prossima
    }
    /* Una query scritta per una stanza non vuol dire niente nell'altra: la
       ricerca ora filtra **la stanza attiva** (prima filtrava tutte e tre
       insieme), quindi portarsela dietro mostrerebbe una stanza vuota senza
       una ragione visibile. */
    if (this.searchInput) this.searchInput.value = '';
    /* L'occhio vive solo nella stanza Android: uscendo, lo stato "mostra
       nascoste" va spento insieme al pulsante, o al rientro l'icona direbbe
       una cosa e la griglia un'altra. */
    if (room !== 'android') this._showHidden = false;
    this.render();
  }

  async loadSkills() {
    try {
      const data = await api.getSkills();
      /* **Tutte** le skill, non solo quelle del workspace. Il filtro che c'era
         qui nascondeva del tutto le skill di serie: l'agente le usava, la
         scheda non le nominava, e la sola traccia della loro esistenza era una
         risposta che arrivava da un pezzo di macchina invisibile. La stanza
         Skill le mostra in un gruppo a parte, col lucchetto invece
         dell'interruttore. Restano fuori solo le `internal` — puro plumbing —
         e solo fuori dalla Modalità avanzata: quel filtro è in
         `_renderSkillsRoom`, dove sta anche il resto della visibilità. */
      this.skills = data.skills || [];
      this._loadFailed.skills = false;
    } catch {
      this.skills = [];
      this._loadFailed.skills = true;
    }
    this._skillsLoaded = true;
    this.render();
  }

  /** Ricarica la lista delle app Android.
   *
   *  Con ``announceRemovals`` le app che sono sparite rispetto alla lista
   *  precedente vengono annunciate con un toast: è la conferma visibile di una
   *  disinstallazione andata a buon fine (il dialog di sistema non ce ne dà
   *  nessuna). Il confronto si fa solo se la fetch è riuscita, altrimenti una
   *  lista vuota per errore verrebbe letta come "disinstallato tutto". */
  async loadAndroidApps({ announceRemovals = false } = {}) {
    const token = ++this._androidLoadSeq;
    const previous = this.androidApps;
    let apps = null;
    /* Due modi di fallire, e vanno distinti entrambi da "non ci sono app":
       la fetch che non arriva (gateway giù, 401) e il **ponte nativo** che non
       risponde — che torna 200 con una lista vuota e un `error` dentro. Il
       secondo è il caso che la documentazione denunciava: senza guardare quel
       campo, un PackageManager muto si legge come un telefono senza app. */
    let failed = false;
    try {
      const data = await api.getAndroidApps();
      apps = data.apps || [];
      failed = !!data.error;
    } catch {
      apps = null;
      failed = true;
    }
    // Le risposte possono tornare fuori ordine: se nel frattempo è partita una
    // fetch più recente, questa è vecchia e riscriverebbe la griglia con uno
    // stato stantio (proprio l'app appena disinstallata tornerebbe su).
    if (token !== this._androidLoadSeq) return;
    this.androidApps = apps || [];
    this._loadFailed.android = failed;
    this._androidAppsLoaded = true;
    /* `!failed` accanto ad `apps`: una lista vuota **per guasto del ponte**
       arriva come `[]`, cioè verissima a guardarla, e senza questa guardia
       annuncerebbe come disinstallate tutte le app del telefono in un colpo.
       Prima del passo 6.2 non si poteva sapere: il guasto e il vuoto erano la
       stessa risposta. */
    if (announceRemovals && apps && !failed) {
      const present = new Set(apps.map(a => a.packageName));
      // `_removalsAnnounced` scarta le rimozioni per cui il toast è già uscito
      // dalla via del broadcast: senza questo, una fetch partita prima di quella
      // notifica annuncerebbe la stessa disinstallazione una seconda volta. Da
      // qui in poi il set è inutile: qualunque fetch più vecchia perde comunque
      // il controllo sul token e non annuncia niente.
      const gone = previous.filter(a =>
        !present.has(a.packageName) && !this._removalsAnnounced.has(a.packageName));
      this._removalsAnnounced.clear();
      if (gone.length) {
        this._forgetHiddenPackages(gone.map(a => a.packageName));
        this._announceUninstalled(gone.map(a => a.label));
      }
    }
    this.render();
  }

  /** Conferma visibile di una disinstallazione: il dialog di sistema non ne dà
   *  nessuna. Un solo toast anche per più app — i toast sono sovrapposti, non
   *  impilati, quindi due insieme si coprirebbero. */
  _announceUninstalled(labels) {
    const message = labels.length === 1
      ? i18n.t('apps.uninstalled', { name: labels[0] })
      : i18n.t('apps.uninstalledMany', { names: labels.join(', ') });
    showToast(message, 'success');
  }

  /** Un pacchetto disinstallato non deve restare nell'elenco delle nascoste:
   *  altrimenti una futura reinstallazione ricomparirebbe già invisibile. */
  _forgetHiddenPackages(packageNames) {
    const removed = packageNames.filter(pkg => this.hiddenPackages.delete(pkg));
    if (removed.length) this._persistHiddenApps();
  }

  async loadHiddenApps() {
    try {
      const data = await api.getHiddenApps();
      this.hiddenPackages = new Set(data.packages || []);
      this._loadFailed.hidden = false;
    } catch {
      this.hiddenPackages = new Set();
      this._loadFailed.hidden = true;
    }
    this._hiddenLoaded = true;
    this.render();
  }

  async _persistHiddenApps() {
    try {
      await api.setHiddenApps([...this.hiddenPackages]);
    } catch {
      // best-effort: the in-memory set still reflects the user's choice
    }
  }

  async loadJennyApps() {
    try {
      const data = await api.getJennyApps();
      this.jennyApps = data.apps || [];
      this._loadFailed.jenny = false;
    } catch {
      this.jennyApps = [];
      this._loadFailed.jenny = true;
    }
    this._jennyAppsLoaded = true;
    this.render();
  }

  _reloadJennyApps() {
    this._jennyAppsLoaded = false;
    this.loadJennyApps();
  }

  /** Iscrive un ascoltatore ai cambi delle tre liste; ritorna la funzione che
   *  lo disiscrive. */
  addChangeListener(fn) {
    this._changeListeners.add(fn);
    return () => this._changeListeners.delete(fn);
  }

  /* Un ascoltatore che esplode non deve portarsi via gli altri né la ridisegnata
     della scheda: il cassetto è un consumatore, non un pezzo di questo flusso. */
  _emitChange() {
    for (const fn of this._changeListeners) {
      try {
        fn();
      } catch (err) {
        console.error('Apps change listener failed:', err);
      }
    }
  }

  /** Avvia le fetch che mancano, senza pretendere che la scheda sia a schermo.
   *
   *  `activate()` la chiama e in più ridisegna; il cassetto la chiama e basta —
   *  la sua lista la ricostruisce l'ascoltatore quando le risposte arrivano. */
  ensureLoaded() {
    if (!this._skillsLoaded || this._loadFailed.skills) this.loadSkills();
    if (!this._androidAppsLoaded || this._loadFailed.android) this.loadAndroidApps();
    if (!this._jennyAppsLoaded || this._loadFailed.jenny) this.loadJennyApps();
    if (!this._hiddenLoaded || this._loadFailed.hidden) this.loadHiddenApps();
  }

  /** Vero finché una delle quattro fetch iniziali non è tornata. Distingue
   *  "non c'è niente" da "non è ancora arrivato niente". */
  isLoadingLists() {
    return !(this._skillsLoaded && this._jennyAppsLoaded
             && this._androidAppsLoaded && this._hiddenLoaded);
  }

  /** Almeno una delle quattro liste non si è potuta leggere (6.2).
   *
   *  Terza risposta accanto a `isLoadingLists()` e a "l'elenco è vuoto", e le
   *  tre non si sovrappongono: un guasto **non** lascia la UI in caricamento —
   *  la risposta è arrivata, dice solo che è andata male — e non è nemmeno un
   *  elenco vuoto, perché le altre liste possono esserci tutte. È il caso
   *  peggiore da diagnosticare proprio perché sembra normale: le app Android
   *  mancano e basta.
   */
  listsFailed() {
    return Object.values(this._loadFailed).some(Boolean);
  }

  /** Riprova **solo** le liste andate male; le altre restano dove sono.
   *
   *  `ensureLoaded()` fa già lo stesso a ogni apertura del cassetto, quindi
   *  chiudere e riaprire basta; questo è il pulsante per chi il foglio ce l'ha
   *  già aperto sotto gli occhi e non deve indovinare che riaprirlo ritenta.
   */
  retryFailedLists() {
    this.ensureLoaded();
  }

  /** Le tre liste normalizzate in righe, per il cassetto (D5: la sorgente è qui).
   *
   *  Le app nascoste restano nascoste, e non c'è un modo per rivelarle: lo
   *  `_showHidden` della scheda è l'occhio della *gestione*, e nel foglio non
   *  c'è. Le app Android non escono finché non sono arrivate **entrambe** le
   *  liste — altrimenti sulla corsa del primo caricamento una nascosta
   *  comparirebbe per un istante, che è la stessa guardia di `_renderAndroidRoom`.
   *
   *  L'ordine è alfabetico e stabile: il cassetto lo riordina per pertinenza,
   *  frequenza e recenza (`shared/launcher-rank.js`), ma questa è la lista, non
   *  la classifica. Il `key` è quello del ranking (`android:<pkg>`,
   *  `jenny:<slug>`, `skill:<nome>`), così due voci omonime in spazi diversi non
   *  si confondono mai; `id` è la stessa cosa senza prefisso, per chi deve poi
   *  avviarla.
   *
   *  **`description` e `problem` non sono decorazione** (difetto 02 del
   *  rilievo). Il gateway manda per ogni skill e per ogni Jenny App una
   *  descrizione, e dice anche quando qualcosa non va — `broken`/`error`,
   *  `available`/`unavailable_reason`, `disabled` — e la griglia di oggi lo
   *  riduce tutto a un pallino colorato o a un nome troncato. Qui arriva intero
   *  alle righe, che è anche ciò su cui si cerca. Le app Android non hanno una
   *  descrizione e non gliene inventiamo una: al suo posto va il nome del
   *  pacchetto, che è un dato vero, distingue due app omonime, e si cerca
   *  ("gmail" trova *Gmail* anche da `com.google.android.gm`). */
  launcherEntries() {
    /* Niente skill qui. Il cassetto è un **lanciatore**: le skill non si
       lanciano — toccarne una apre una scheda o un file — e mescolarle alle
       app faceva una lista di tre nature diverse, cioè il difetto 01 del
       rilievo rimesso in piedi un livello più in là. Restano nella scheda
       Apps; dove vadano davvero a stare è una decisione ancora aperta. */
    const entries = [];
    for (const app of this.jennyApps) {
      const problem = app.broken
        ? (app.error || i18n.t('apps.invalidManifest')) : null;
      entries.push({
        key: `jenny:${app.slug}`, id: app.slug, kind: 'jenny', name: app.name || app.slug,
        glyph: app.broken ? 'ti-alert-triangle' : (app.icon || 'ti-apps'), icon: null,
        description: app.description || '',
        problem,
        hasServer: !!app.has_server,
        // Anche l'errore si cerca: "manifest" deve far emergere le app rotte
        // tutte insieme, che è il modo in cui uno le ripara.
        searchText: [app.description, app.slug, problem].filter(Boolean).join(' '),
      });
    }
    if (this._androidAppsLoaded && this._hiddenLoaded) {
      for (const app of this.androidApps) {
        if (this.hiddenPackages.has(app.packageName)) continue;
        entries.push({
          key: `android:${app.packageName}`, id: app.packageName, kind: 'android',
          name: app.label,
          glyph: 'ti-apps', icon: app.icon || null,
          description: app.packageName,
          problem: null,
          searchText: app.packageName,
        });
      }
    }
    entries.sort((a, b) =>
      a.name.localeCompare(b.name, i18n.locale, { sensitivity: 'base' })
      || a.key.localeCompare(b.key));
    return entries;
  }

  /** Avvia una voce del cassetto.
   *
   *  Sta qui e non nel foglio perché "aprire" significa tre cose diverse nei tre
   *  spazi di nomi, e sono già decise: sono le stesse azioni del tap sulla cella
   *  della scheda (v. `_buildJennyRow`, `_openSkill`, `_buildAndroidCell`).
   *  Due copie di questa scelta divergerebbero
   *  al primo caso particolare — una skill locked, una Jenny App rotta — ed è
   *  esattamente dove la divergenza si nota di meno e costa di più. */
  activateEntry(entry) {
    if (!entry) return;
    if (entry.kind === 'android') {
      /* **Ritornata**, non lasciata cadere: è l'unica delle tre attivazioni che
         può fallire in modo osservabile, e il cassetto ci decide sopra se
         chiudersi (6.3). Le altre due aprono qualcosa *sopra* il foglio e non
         hanno un esito da aspettare. */
      return this.launchAndroidApp(entry.id);
    }
    if (entry.kind === 'jenny') {
      // Rotta compresa: `openApp` chiede conferma e propone la riparazione in
      // chat, che dalla riga del cassetto è la strada giusta come dalla cella.
      this.openApp(entry.id);
      return;
    }
    this._openSkill(entry.id);
  }

  /** Una skill è **di serie**?
   *
   *  Non si legge da `source`, ed è un fatto misurato che il piano non aveva:
   *  le skill impacchettate vengono **copiate** in `workspace/skills/` al boot,
   *  e `SkillsLoader.list_skills` guarda solo lì — quindi `source` vale
   *  `"workspace"` per tutte, sempre, e un gruppo costruito su quel campo
   *  resterebbe vuoto per sempre. Quel che distingue davvero una skill di serie
   *  è la sua frontmatter: `locked` (visibile ma da non toccare) o `internal`
   *  (puro plumbing, e fuori dalla Modalità avanzata nemmeno in lista). */
  _skillIsBuiltIn(skill) {
    return !!skill && (!!skill.locked || !!skill.internal);
  }

  /** Una skill si gestisce (interruttore/modifica/elimina) solo se non è
   *  bloccata — o se si è in Modalità avanzata, che è la stessa regola che
   *  `showSkillSheet` applicava già alle proprie azioni.
   *
   *  **`locked` non è un dettaglio estetico**: fuori dalla Modalità avanzata la
   *  scheda di `cron` o di `ssh` non ha mai offerto "Disabilita", e dare a
   *  quelle righe un interruttore vorrebbe dire poter spegnere il cron con un
   *  tocco — una protezione che c'era, tolta per distrazione.
   *
   *  `source` resta nella condizione perché è la regola delle rotte del gateway
   *  (`update_workspace_skill` e `delete_workspace_skill` guardano solo dentro
   *  `workspace/skills/`): oggi è sempre vera, e il giorno in cui non lo fosse
   *  più questa UI non offrirebbe un'azione che il gateway rifiuta. */
  _skillIsManageable(skill) {
    return !!skill && skill.source === 'workspace' && (!skill.locked || advancedMode());
  }

  /** Il tocco su una skill: il file per quelle che si possono modificare, la
   *  scheda in sola lettura (con `user_summary`) per le altre. */
  _openSkill(name) {
    const skill = this.skills.find(s => s.name === name);
    if (!skill) return;
    if (this._skillIsManageable(skill)) this._openSkillFile(name);
    else this.showSkillSheet(name);
  }

  /** Apre la *scheda* di una voce del cassetto: il foglio informativo, non la
   *  cosa. È ⇧⏎ dal cassetto, ed è la pressione lunga dalla cella della
   *  griglia — cioè la stessa strada già battuta, per la stessa ragione di
   *  `activateEntry`: due copie della scelta divergerebbero al primo caso
   *  particolare.
   *
   *  Le tre schede sono `<dialog>` aperte con `showModal()`, quindi vivono nel
   *  livello `dialog`, che sta **sopra** `launcher`: si sovrappongono al foglio
   *  e Indietro chiude prima loro, esattamente come la scheda di una skill
   *  locked aperta col tocco (3.7).
   */
  detailEntry(entry) {
    if (!entry) return;
    if (entry.kind === 'android') this.showAndroidAppSheet(entry.id);
    else if (entry.kind === 'jenny') this.showJennyAppSheet(entry.id);
    else this.showSkillSheet(entry.id);
  }

  render() {
    /* Prima del ritorno anticipato, e prima di toccare il DOM della scheda: il
       cassetto legge queste stesse liste anche quando `view-apps` non è mai
       stata a schermo, e ogni strada che le cambia passa di qui. */
    this._emitChange();
    if (!this.contentEl) return;
    this._renderRoomTabs();
    this._syncSearchPlaceholder();
    this._syncHeaderActions();
    const q = (this.searchInput?.value || '').toLowerCase().trim();
    /* Azzerato **prima** di costruire la stanza, non dopo: è
       `_renderAndroidRoom` a riempire questi due, e ripulirli dopo cancellava
       proprio ciò che aveva appena scritto — la guida restava a schermo sempre,
       perché `_syncAzRail` trovava null e usciva. Visto sul telefono. */
    this._azRail = null;
    this._azScroll = null;
    let room;
    if (this.activeRoom === 'skills') room = this._renderSkillsRoom(q);
    else if (this.activeRoom === 'android') room = this._renderAndroidRoom(q);
    else room = this._renderJennyRoom(q);
    this.contentEl.replaceChildren(room);
    // Dopo l'inserimento: la guida A–Z si misura, e prima di stare in pagina
    // non c'è niente da misurare (v. `_syncAzRail`).
    this._syncAzRail();
  }

  /** Il segmento in cima. Costruito una volta sola e poi solo risincronizzato:
   *  `render()` gira a ogni tasto digitato nel campo di ricerca, e rifare tre
   *  pulsanti a ogni carattere è lavoro sul thread principale in cambio di
   *  niente.
   *
   *  Nessun `roving tabindex`: è il pattern ARIA canonico per un `tablist`, ma
   *  vive di frecce ←→ che qui non ci sono, e su un telefono dove Tab è
   *  navigazione primaria lascerebbe due stanze su tre irraggiungibili. Tutte e
   *  tre restano fermate di Tab. */
  _renderRoomTabs() {
    if (!this.roomsEl) return;
    if (!this._roomTabs) {
      this._roomTabs = new Map();
      this.roomsEl.setAttribute('role', 'tablist');
      this.roomsEl.dataset.i18nAria = 'apps.roomsLabel';
      this.roomsEl.setAttribute('aria-label', i18n.t('apps.roomsLabel'));
      const tabs = ROOMS.map(room => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'apps-room-tab';
        btn.dataset.room = room;
        btn.dataset.i18n = ROOM_LABELS[room];
        btn.setAttribute('role', 'tab');
        btn.setAttribute('aria-controls', 'apps-content');
        btn.textContent = i18n.t(ROOM_LABELS[room]);
        btn.addEventListener('click', () => this.setRoom(room));
        this._roomTabs.set(room, btn);
        return btn;
      });
      this.roomsEl.replaceChildren(...tabs);
    }
    for (const [room, btn] of this._roomTabs) {
      const active = room === this.activeRoom;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', String(active));
      // La lingua può essere cambiata da quando i pulsanti sono nati.
      btn.textContent = i18n.t(ROOM_LABELS[room]);
    }
  }

  /** Il campo di ricerca resta uno, ma dice quale stanza sta filtrando: con 47
   *  app Android e tre elenchi diversi sotto lo stesso segnaposto, "Cerca…" non
   *  dice dove si sta cercando. */
  _syncSearchPlaceholder() {
    const key = ROOM_SEARCH_PLACEHOLDERS[this.activeRoom];
    if (!this.searchInput || !key) return;
    this.searchInput.dataset.i18nPlaceholder = key;
    this.searchInput.placeholder = i18n.t(key);
  }

  /** L'occhio «mostra nascoste» riguarda solo le app Android: nelle altre due
   *  stanze non c'è niente di nascosto da mostrare, e un pulsante che non fa
   *  niente è peggio di un pulsante che non c'è. */
  _syncHeaderActions() {
    const header = window.mobileApp?.header;
    if (!header) return;
    if (this.activeRoom === 'android') header.showAction('toggle-hidden');
    else header.hideAction('toggle-hidden');
  }

  /* ── Mattoni comuni alle tre stanze ────────────────────────────────────── */

  /** Lo scorrevole di una stanza. Lo scorrimento sta **qui dentro** e non in
   *  `.apps-content`: la guida A–Z della stanza Android deve poter stare ferma
   *  sul bordo mentre la griglia scorre. */
  _roomScroll() {
    const el = document.createElement('div');
    el.className = 'apps-room-scroll';
    return el;
  }

  /** Il corpo toccabile di una riga: nome, righe secondarie, azione a destra.
   *
   *  È un `<button>` vero e non un `<div role="button">`: Invio e Spazio
   *  funzionano da soli, il fuoco pure, e non c'è un `tabindex` da
   *  riappiccicare a ogni ridisegno, come faceva la fisarmonica di prima.
   *
   *  Tutti i testi si scrivono con `textContent`. Arrivano da manifest scritti
   *  da un LLM e dal PackageManager: non sono roba nostra e non passano mai da
   *  `innerHTML`. */
  _rowMain(name, lines = [], action = null) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'apps-row-main';

    const text = document.createElement('span');
    text.className = 'apps-row-text';
    const nameEl = document.createElement('span');
    nameEl.className = 'apps-row-name';
    nameEl.textContent = name;
    text.appendChild(nameEl);
    for (const line of lines) {
      if (!line || !line.text) continue;
      const lineEl = document.createElement('span');
      lineEl.className = line.className || 'apps-row-desc';
      lineEl.textContent = line.text;
      text.appendChild(lineEl);
    }
    btn.appendChild(text);

    if (action) {
      const actionEl = document.createElement('span');
      actionEl.className = 'apps-row-action' + (action.danger ? ' apps-row-action--repair' : '');
      actionEl.textContent = action.label;
      btn.appendChild(actionEl);
    }
    return btn;
  }

  /** La riga tratteggiata in fondo a una stanza: "Nuova app", "Nuova skill". */
  _buildAddRow(label, onClick, id) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'apps-add-row';
    if (id) btn.id = id;
    const plus = document.createElement('i');
    plus.className = 'ti ti-plus';
    plus.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span');
    text.textContent = label;
    btn.append(plus, text);
    btn.addEventListener('click', onClick);
    return btn;
  }

  /** Il titolo di un gruppo dentro una stanza ("Tue skill", "Di serie"). */
  _groupTitle(label) {
    const el = document.createElement('h2');
    el.className = 'apps-group-title';
    el.textContent = label;
    return el;
  }

  _note(key, params) {
    const note = document.createElement('p');
    note.className = 'apps-note';
    note.textContent = i18n.t(key, params);
    return note;
  }

  /** Una riga della stanza è pertinente alla query? Si cerca su tutti i campi
   *  che la riga mostra, più quelli che la identificano (slug, pacchetto). */
  _matches(q, ...fields) {
    if (!q) return true;
    return fields.some(field => (field || '').toLowerCase().includes(q));
  }

  /** Il caricamento non è ancora finito, o è finito male? Le due risposte non
   *  sono la stessa cosa e non sono "non c'è niente": la prima chiede di
   *  aspettare, la seconda di riprovare. */
  _roomStatusNote(loadedFlag, failedFlag) {
    if (!loadedFlag) return this._note('apps.loading');
    if (failedFlag) return this._note('apps.loadFailed');
    return null;
  }

  /* ── Stanza «Jenny App» ────────────────────────────────────────────────── */

  /** Lista, non griglia: nome più descrizione, azione a destra.
   *
   *  **L'errore di una app rotta è una banda in cima alla stanza** (difetto 05).
   *  Nella griglia stava dentro la tessera, e due righe di messaggio alzavano
   *  quella cella da 100 a 147 px portandosi dietro tutta la fila. Qui la riga
   *  della app rotta dice solo «ripara»; il perché sta in cima, dove si legge
   *  senza deformare niente. */
  _renderJennyRoom(q) {
    const scroll = this._roomScroll();
    const status = this._roomStatusNote(this._jennyAppsLoaded, this._loadFailed.jenny);
    if (status) {
      scroll.appendChild(status);
      return scroll;
    }

    const broken = this.jennyApps.filter(app => app.broken);
    if (broken.length) scroll.appendChild(this._buildBrokenBand(broken));

    const filtered = this.jennyApps.filter(app =>
      this._matches(q, app.name, app.slug, app.description, app.error));
    if (filtered.length) {
      const list = document.createElement('div');
      list.className = 'apps-list';
      for (const app of filtered) list.appendChild(this._buildJennyRow(app));
      scroll.appendChild(list);
    } else if (q) {
      scroll.appendChild(this._note('apps.noResults'));
    }

    // La riga «Nuova app» non compare in coda a una ricerca senza risultati:
    // lì la risposta è "non c'è", non "creane un'altra".
    if (!q) {
      scroll.appendChild(
        this._buildAddRow(i18n.t('apps.newApp'), () => this._startAppCreation(), 'jenny-app-add'));
    }
    return scroll;
  }

  _buildBrokenBand(broken) {
    const band = document.createElement('div');
    band.className = 'apps-band apps-band--error';
    band.setAttribute('role', 'status');
    const icon = document.createElement('i');
    icon.className = 'ti ti-alert-triangle';
    icon.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span');
    text.textContent = broken.length === 1
      ? i18n.t('apps.brokenBand', {
        name: broken[0].name || broken[0].slug,
        error: broken[0].error || i18n.t('apps.invalidManifest'),
      })
      : i18n.t('apps.brokenBandMany', { count: broken.length });
    band.append(icon, text);
    return band;
  }

  _buildJennyRow(app) {
    const row = document.createElement('div');
    row.className = 'apps-row';
    if (app.broken) row.classList.add('apps-row--broken');
    const main = this._rowMain(
      app.name || app.slug,
      [{ text: app.description || '' }],
      app.broken
        ? { label: i18n.t('apps.repair'), danger: true }
        : { label: i18n.t('apps.open') },
    );
    main.dataset.jennySlug = app.slug;
    main.addEventListener('click', () => {
      if (main.dataset.longpress) { delete main.dataset.longpress; return; }
      this.openApp(app.slug);
    });
    setupLongPress(main, () => this.showJennyAppSheet(app.slug));
    row.appendChild(main);
    return row;
  }

  /* ── Stanza «Skill» ────────────────────────────────────────────────────── */

  /** Lista con interruttori, in due gruppi. Nessuna icona per riga: dodici
   *  glifi puzzle identici erano il difetto 03 — non distinguevano niente e
   *  costavano 22% della cella.
   *
   *  Le skill di serie ci sono, col **lucchetto** invece dell'interruttore: non
   *  si spengono, e il gateway le rifiuterebbe comunque. Le `internal` restano
   *  fuori dalla Modalità avanzata, come prima. */
  _renderSkillsRoom(q) {
    const scroll = this._roomScroll();
    const status = this._roomStatusNote(this._skillsLoaded, this._loadFailed.skills);
    if (status) {
      scroll.appendChild(status);
      return scroll;
    }

    const visible = this.skills.filter(skill =>
      (advancedMode() || !skill.internal)
      && this._matches(q, skill.name, skill.description, skill.unavailable_reason));
    /* Il gruppo dice **da dove viene** la skill, non se in questo momento la si
       può toccare: così una riga non salta da un gruppo all'altro accendendo la
       Modalità avanzata — cambia solo il suo comando, dal lucchetto
       all'interruttore. */
    const mine = visible.filter(skill => !this._skillIsBuiltIn(skill));
    const builtIn = visible.filter(skill => this._skillIsBuiltIn(skill));

    if (mine.length) {
      scroll.appendChild(this._groupTitle(i18n.t('apps.yourSkills')));
      scroll.appendChild(this._buildSkillList(mine));
    }
    if (builtIn.length) {
      scroll.appendChild(this._groupTitle(i18n.t('apps.builtInSkills')));
      scroll.appendChild(this._buildSkillList(builtIn));
    }
    if (!visible.length && q) scroll.appendChild(this._note('apps.noResults'));

    if (!q) {
      scroll.appendChild(
        this._buildAddRow(i18n.t('apps.newSkill'), () => this._startSkillCreation(), 'app-add'));
    }
    return scroll;
  }

  _buildSkillList(skills) {
    const list = document.createElement('div');
    list.className = 'apps-list';
    for (const skill of skills) list.appendChild(this._buildSkillRow(skill));
    return list;
  }

  /** Una riga di skill.
   *
   *  **Lo stato non è binario, e prima lo era.** Il badge di ieri diceva
   *  "attiva/inattiva/disabilitata" da un solo dato mescolato, e le due cose
   *  che possono andare storte si somigliavano solo a guardarle:
   *
   *  - `disabled` è una **decisione**, dell'utente, reversibile lì per lì: lo
   *    dice l'interruttore, e toccarlo la cambia;
   *  - `available === false` è un **impedimento**: la skill non *può* girare
   *    (le manca un tool, una chiave, un file), e l'interruttore non c'entra —
   *    accenderlo non la farebbe partire. Lo dice una riga in `var(--warning)`
   *    con dentro `unavailable_reason`, che è l'unica informazione da cui si
   *    capisce cosa fare.
   *
   *  Le due possono coesistere: una skill spenta *e* non disponibile mostra
   *  l'interruttore giù e l'avviso sotto, che è la verità. */
  _buildSkillRow(skill) {
    const row = document.createElement('div');
    row.className = 'apps-row';
    const lines = [];
    if (skill.description) lines.push({ text: skill.description });
    if (skill.available === false) {
      lines.push({
        text: skill.unavailable_reason
          ? i18n.t('apps.unavailableWhy', { reason: skill.unavailable_reason })
          : i18n.t('apps.unavailable'),
        className: 'apps-row-warn',
      });
    }
    const main = this._rowMain(skill.name, lines);
    main.dataset.skill = skill.name;
    main.addEventListener('click', () => {
      if (main.dataset.longpress) { delete main.dataset.longpress; return; }
      this._openSkill(skill.name);
    });
    setupLongPress(main, () => this.showSkillSheet(skill.name));
    row.append(main, this._skillIsManageable(skill)
      ? this._buildSkillToggle(skill)
      : this._buildSkillLock());
    return row;
  }

  /** L'interruttore. Riusa `toggle-switch`/`toggle-slider` delle Impostazioni:
   *  un secondo interruttore con un aspetto suo sarebbe una seconda cosa da
   *  imparare per la stessa azione. */
  _buildSkillToggle(skill) {
    const label = document.createElement('label');
    label.className = 'toggle-switch apps-row-toggle';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !skill.disabled;
    input.dataset.skillToggle = skill.name;
    input.setAttribute('aria-label', i18n.t('apps.toggleSkill', { name: skill.name }));
    input.addEventListener('change', () => this._onSkillToggled(skill.name, input));
    const slider = document.createElement('span');
    slider.className = 'toggle-slider';
    label.append(input, slider);
    return label;
  }

  _buildSkillLock() {
    const lock = document.createElement('i');
    lock.className = 'ti ti-lock apps-row-lock';
    lock.setAttribute('role', 'img');
    lock.setAttribute('aria-label', i18n.t('apps.builtInLocked'));
    lock.title = i18n.t('apps.builtInLocked');
    return lock;
  }

  /** L'interruttore è stato mosso: si scrive e si rilegge.
   *
   *  `loadSkills()` ridisegna, quindi in caso di successo questo nodo sparisce
   *  e non c'è niente da rimettere a posto. In caso di errore il ridisegno
   *  **non** avviene, e la casella va riportata dov'era: lasciarla dove l'ha
   *  messa il dito direbbe che la skill è spenta mentre il gateway la tiene
   *  accesa. */
  async _onSkillToggled(name, input) {
    const disabled = !input.checked;
    input.disabled = true;
    try {
      await this._setSkillDisabled(name, disabled);
      await this.loadSkills();
    } catch {
      input.checked = !disabled;
      showToast(i18n.t('apps.operationFailed'), 'error');
    } finally {
      input.disabled = false;
    }
  }

  /* ── Stanza «Android» ──────────────────────────────────────────────────── */

  /** Griglia densa a sei colonne più la guida A–Z sul bordo destro.
   *
   *  Sei colonne e non quattro perché una cella da 4 colonne sprecava 56 px di
   *  larghezza per icona; la guida sta a destra perché è l'unico posto dove
   *  quello spazio orizzontale c'è davvero, e con 47 app è l'unico modo di
   *  arrivare alla V senza sei passate di pollice. */
  _renderAndroidRoom(q) {
    const wrap = document.createElement('div');
    wrap.className = 'apps-android';
    const scroll = this._roomScroll();
    wrap.appendChild(scroll);

    /* Si aspettano **entrambe** le liste prima di disegnare una cella: sulla
       corsa del primo caricamento una app nascosta comparirebbe per un istante.
       Stessa guardia di `launcherEntries()`. */
    const ready = this._androidAppsLoaded && this._hiddenLoaded;
    if (!ready || this._loadFailed.android) {
      scroll.appendChild(this._note(ready ? 'apps.loadFailed' : 'apps.loading'));
      return wrap;
    }

    const visible = this.androidApps.filter(app =>
      (this._showHidden || !this.hiddenPackages.has(app.packageName))
      && this._matches(q, app.label, app.packageName));

    const grid = document.createElement('div');
    grid.className = 'apps-grid';
    /* La prima cella di ogni lettera, nell'ordine in cui compare nella griglia:
       il ponte nativo consegna la lista già ordinata per etichetta minuscola
       (`InstalledAppsBridge.listInstalledApps`), quindi l'ordine di inserimento
       *è* l'ordine alfabetico e la guida non ha bisogno di riordinare niente. */
    const anchors = new Map();
    for (const app of visible) {
      const cell = this._buildAndroidCell(app);
      const letter = this._indexLetter(app.label);
      if (!anchors.has(letter)) anchors.set(letter, cell);
      grid.appendChild(cell);
    }
    scroll.appendChild(grid);

    if (!visible.length) {
      scroll.appendChild(this._note(q ? 'apps.noResults' : 'apps.noAppsFound'));
      return wrap;
    }
    // Una guida con una lettera sola non guida da nessuna parte.
    if (anchors.size > 1) {
      this._azRail = this._buildAzRail(anchors);
      this._azScroll = scroll;
      wrap.appendChild(this._azRail);
    }
    return wrap;
  }

  /** La guida serve solo se c'è qualcosa da scorrere.
   *
   *  Misurato sul telefono: con 18 app la griglia a sei colonne sta in tre file
   *  e **non scorre affatto** — la guida sarebbe una colonna di lettere che non
   *  portano da nessuna parte, e in cambio si prenderebbe 22 px di larghezza
   *  alla griglia per sempre. Si misura invece di indovinare da un conteggio di
   *  righe, perché l'altezza disponibile cambia con la tastiera e con la
   *  geometria dello schermo.
   *
   *  Va chiamata **dopo** che il nodo è in pagina: prima, `scrollHeight` e
   *  `clientHeight` valgono zero e la guida sparirebbe sempre. */
  _syncAzRail() {
    const rail = this._azRail;
    const scroll = this._azScroll;
    if (!rail || !scroll || !rail.isConnected) return;
    rail.hidden = scroll.scrollHeight <= scroll.clientHeight;
  }

  /** La lettera d'indice di un'etichetta. Gli accenti si piegano sulla lettera
   *  base — "Élite" sotto E, non in un secchio suo da una voce — e tutto ciò
   *  che lettera non è (cifre, ideogrammi, emoji) finisce sotto `#`. */
  _indexLetter(label) {
    const first = (label || '').trim().charAt(0);
    if (!first) return '#';
    const folded = first.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    const upper = folded.toLocaleUpperCase(i18n.locale).charAt(0) || '#';
    return /\p{L}/u.test(upper) ? upper : '#';
  }

  _buildAzRail(anchors) {
    const rail = document.createElement('div');
    rail.className = 'apps-az';
    rail.setAttribute('role', 'group');
    rail.setAttribute('aria-label', i18n.t('apps.azGuide'));
    for (const [letter, cell] of anchors) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'apps-az-letter';
      btn.dataset.letter = letter;
      btn.textContent = letter;
      btn.setAttribute('aria-label', i18n.t('apps.jumpToLetter', { letter }));
      btn.addEventListener('click', () => cell.scrollIntoView({ block: 'start' }));
      rail.appendChild(btn);
    }
    return rail;
  }

  _buildAndroidCell(app) {
    const isHidden = this.hiddenPackages.has(app.packageName);
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'app-cell' + (isHidden ? ' app-cell--hidden' : '');
    cell.dataset.androidPackage = app.packageName;
    if (app.system) cell.dataset.androidSystem = '1';

    const iconWrap = document.createElement('div');
    iconWrap.className = 'app-icon';
    /* `src` accetta anche schemi che eseguono, e questo valore ha fatto un giro
       fuori dal nostro codice (PackageManager → ponte → gateway → qui). Il
       ponte consegna `data:image/png;base64,…`: tutto il resto è un glifo. */
    if (app.icon && app.icon.startsWith('data:image/')) {
      const img = document.createElement('img');
      img.src = app.icon;
      img.alt = '';
      iconWrap.appendChild(img);
    } else {
      const glyph = document.createElement('i');
      glyph.className = 'ti ti-apps';
      glyph.setAttribute('aria-hidden', 'true');
      iconWrap.appendChild(glyph);
    }
    if (isHidden) {
      const badge = document.createElement('div');
      badge.className = 'app-hidden-badge';
      const eye = document.createElement('i');
      eye.className = 'ti ti-eye-off';
      eye.setAttribute('aria-hidden', 'true');
      badge.appendChild(eye);
      iconWrap.appendChild(badge);
    }

    const label = document.createElement('div');
    label.className = 'app-label';
    label.textContent = app.label;

    cell.append(iconWrap, label);
    cell.addEventListener('click', () => {
      if (cell.dataset.longpress) { delete cell.dataset.longpress; return; }
      this.launchAndroidApp(app.packageName);
    });
    setupLongPress(cell, () => this.showAndroidAppSheet(app.packageName));
    return cell;
  }

  // ── Jenny Apps ──

  async openApp(slug) {
    const app = this.jennyApps.find(a => a.slug === slug);
    if (!app) return;
    if (app.broken) {
      const ok = await confirmDialog(
        i18n.t('apps.brokenConfirm', { name: app.name || slug, error: app.error || i18n.t('apps.invalidManifest') })
      );
      if (ok) {
        this._sendChatPrompt(i18n.t('apps.brokenPrompt', { slug, error: app.error || i18n.t('apps.invalidManifest') }));
      }
      return;
    }

    if (!api.getSecret()) {
      try { await api.bootstrap(); } catch { return; }
    }
    const t = currentTheme();
    const lang = document.documentElement.lang || 'it';
    const src = `/apps/${encodeURIComponent(slug)}/index.html`
      + `?token=${encodeURIComponent(api.getSecret())}`
      + `&theme=${encodeURIComponent(t.scheme)}&lang=${encodeURIComponent(lang)}`
      + `&accent=${encodeURIComponent(t.accent)}&onAccent=${encodeURIComponent(t.onAccent)}`
      + `&tokens=${encodeURIComponent(themeTokens())}`;

    this.closeApp();
    const overlay = document.createElement('div');
    overlay.className = 'app-frame-overlay';
    overlay.innerHTML = `
      <div class="app-frame-header">
        <span class="app-frame-title">${escapeHtml(app.name || slug)}</span>
        <button class="app-frame-close" title="${i18n.t('apps.close')}"><i class="ti ti-x"></i></button>
      </div>
    `;
    const iframe = document.createElement('iframe');
    // Opaque origin on purpose: the app must not reach the SPA DOM/localStorage.
    iframe.setAttribute('sandbox', 'allow-scripts');
    iframe.src = src;
    overlay.appendChild(iframe);
    overlay.querySelector('.app-frame-close').addEventListener('click', () => this.closeApp());

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('visible'));
    this._openApp = { slug, overlay, iframe, depth: 1 };
  }

  handleBack() {
    const open = this._openApp;
    if (!open) return false;
    /* `depth` è ciò che l'app dichiara via `jenny:nav-state`: schermate interne
       più <dialog> aperti. I dialog contano perché l'iframe ha origine opaca e
       il livello `dialog` della catena, che interroga solo il documento del
       parent, non li vede: senza questo ramo Indietro chiudeva tutta l'app
       portandosi via il form a metà. */
    if (open.depth > 1) {
      open.iframe.contentWindow?.postMessage({ type: 'jenny:go-back' }, '*');
      return true;
    }
    // Back out of the mini-app: reveal the Apps tab underneath. Do not push a
    // forward history entry (we are going *back*); switchMode is only needed on
    // the off chance the app was opened from another mode.
    this.closeApp();
    /* Col cassetto aperto, l'app è stata lanciata da lì: la destinazione del
       ritorno è il foglio, non la scheda. Senza questa uscita anticipata lo
       `switchMode` qui sotto chiuderebbe il foglio (v. MobileApp.switchMode),
       e una pressione di Indietro smonterebbe due livelli invece di uno —
       proprio ciò che l'ordine `miniapp` → `launcher` promette di non fare. */
    if (window.mobileApp.launcher?.isOpen()) return true;
    if (window.mobileApp.currentMode !== 'apps') window.mobileApp.switchMode('apps', false);
    return true;
  }

  /* Smontare l'overlay basta perché l'SDK non scrive la history: la profondità
     dell'app è pura contabilità (v. jenny-sdk.js). Quando invece l'SDK spingeva
     le schermate nella history con `pushState`, ognuna lasciava una entry nella
     joint session history del WebView che nemmeno `iframe.remove()` toglieva —
     e dopo la ✕ restavano pressioni di Indietro morte. */
  closeApp() {
    const open = this._openApp;
    if (!open) return;
    this._openApp = null;
    open.overlay.classList.remove('visible');
    setTimeout(() => open.overlay.remove(), 200);
  }

  notifyAppDataChanged(slug) {
    const open = this._openApp;
    if (!open || (slug && open.slug !== slug)) return;
    open.iframe.contentWindow?.postMessage({ type: 'jenny:data-changed', slug: open.slug }, '*');
  }

  _onAppMessage(event) {
    const open = this._openApp;
    if (!open || event.source !== open.iframe.contentWindow) return;
    const msg = event.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'jenny:nav-state') {
      // Il valore arriva da codice dell'app: si accetta solo un intero in un
      // intervallo sensato, altrimenti un NaN (o un numero enorme) renderebbe
      // Indietro inutile fino alla ✕.
      const depth = Math.floor(Number(msg.depth));
      open.depth = Number.isFinite(depth) ? Math.min(99, Math.max(1, depth)) : 1;
      return;
    }
    if (msg.type === 'jenny:discuss') {
      const app = this.jennyApps.find(a => a.slug === open.slug);
      const name = app?.name || open.slug;
      const text = String(msg.text || '').slice(0, 4000);
      this.closeApp();
      this._sendChatPrompt(i18n.t('apps.chatAboutApp', { name, text }));
      return;
    }
    if (msg.type === 'jenny:ui-result') {
      // Risposta al round-trip di requestAppHtml: risolve il waiter del nonce.
      const waiter = this._appHtmlWaiters.get(msg.nonce);
      if (waiter) {
        this._appHtmlWaiters.delete(msg.nonce);
        clearTimeout(waiter.timer);
        waiter.resolve(String(msg.html || ''));
      }
    }
  }

  /* Chiede all'iframe dell'app aperta il proprio HTML (l'SDK legge il suo DOM
     e lo rimanda: il parent non può leggerlo, l'iframe ha origin opaca). Ritorna
     null se non c'è un'app aperta o se l'app non risponde entro il timeout. */
  requestAppHtml(timeoutMs = 2000) {
    const open = this._openApp;
    if (!open || !open.iframe.contentWindow) return Promise.resolve(null);
    const nonce = 'app-html-' + (++this._appHtmlSeq);
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        this._appHtmlWaiters.delete(nonce);
        resolve(null);
      }, timeoutMs);
      this._appHtmlWaiters.set(nonce, { resolve, timer });
      open.iframe.contentWindow.postMessage({ type: 'jenny:ui-query', nonce }, '*');
    });
  }

  _startAppCreation() {
    this._sendChatPrompt(i18n.t('apps.createAppPrompt'));
  }

  /** Avvia una app Android. Ritorna **se ci è riuscita** (6.3).
   *
   *  Prima qui c'era un `catch` vuoto commentato "best effort", e un avvio
   *  fallito non diceva niente: nessun toast, nessun messaggio — il difetto che
   *  `docs/using/app-launcher.md` elencava. L'informazione c'era già e la si
   *  buttava: l'endpoint risponde 404 quando il pacchetto non c'è più o Android
   *  rifiuta di avviarlo, e `api.launchAndroidApp` lo alza.
   *
   *  Il caso vero non è esotico: una app disinstallata (o disabilitata) fra il
   *  caricamento della lista e il tocco lascia una riga stantia, e toccarla non
   *  faceva assolutamente niente — indistinguibile da un tocco non registrato.
   *
   *  L'etichetta si cerca nella lista in memoria: se il pacchetto è già sparito
   *  di lì, il nome del pacchetto è comunque meglio di una frase senza soggetto.
   */
  async launchAndroidApp(packageName) {
    try {
      await api.launchAndroidApp(packageName);
      return true;
    } catch {
      const name = this.androidApps.find(a => a.packageName === packageName)?.label
        || packageName;
      showToast(i18n.t('apps.launchFailed', { name }), 'error');
      return false;
    }
  }

  /** Il PackageManager ha annunciato un cambio di pacchetto (notifica da
   *  MainActivity). È la via principale per tenere il launcher aggiornato: il
   *  dialog di disinstallazione di sistema è spesso un'activity translucida,
   *  quindi la WebView non passa mai per ``hidden`` e il fallback su
   *  ``visibilitychange`` qui sotto non scatta.
   *
   *  L'icona viene rimossa subito (la lista in memoria ha già tutto il
   *  necessario) e la fetch che riallinea con PackageManager — costosa, ricodifica
   *  tutte le icone in base64 — è posticipata e accorpata. */
  onPackageChanged(kind, packageName) {
    if (kind === 'removed') {
      const app = this.androidApps.find(a => a.packageName === packageName);
      // Pacchetto senza activity di launcher: mai stato in lista, niente da dire.
      if (app) {
        this.androidApps = this.androidApps.filter(a => a.packageName !== packageName);
        this._forgetHiddenPackages([packageName]);
        this.render();
        this._removalsAnnounced.add(packageName);
        this._announceUninstalled([app.label]);
      }
    }
    clearTimeout(this._androidRefreshTimer);
    // `announceRemovals` anche qui: il broadcast può mancare per un'app (il
    // filtro di visibilità dei pacchetti non garantisce la consegna), e in quel
    // caso la sua sparizione la vede solo il confronto con la lista precedente.
    this._androidRefreshTimer = setTimeout(
      () => this.loadAndroidApps({ announceRemovals: true }), 500);
  }

  /** Reload the Android app list once, when the WebView next regains focus
   * (e.g. after returning from the system uninstall / app-info screen).
   * Seconda linea di difesa dietro ``onPackageChanged``: copre il caso in cui
   * il broadcast di sistema non arrivi (es. disinstallazione fatta da "Info
   * app"), quindi annuncia anche lei le app sparite. */
  _reloadAndroidAppsOnReturn() {
    if (this._pendingReload) return;
    this._pendingReload = true;
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      document.removeEventListener('visibilitychange', onVisible);
      this._pendingReload = false;
      this.loadAndroidApps({ announceRemovals: true });
    };
    document.addEventListener('visibilitychange', onVisible);
  }

  // ── App Android context sheet ──

  showAndroidAppSheet(packageName) {
    const app = this.androidApps.find(a => a.packageName === packageName);
    if (!app) return;

    const sheet = document.getElementById('android-app-sheet');
    if (!sheet) return;

    const isHidden = this.hiddenPackages.has(packageName);
    const icon = app.icon
      ? `<img src="${escapeHtml(app.icon)}" alt="">`
      : '<i class="ti ti-apps"></i>';
    document.getElementById('android-app-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon">${icon}</div>
        <div class="app-sheet-name">${escapeHtml(app.label)}</div>
      </div>`;

    const actions = [
      { icon: 'ti-player-play', label: i18n.t('apps.open'), action: 'launch' },
      { icon: 'ti-info-circle', label: i18n.t('apps.appInfo'), action: 'info' },
    ];
    if (!app.system) {
      actions.push({ icon: 'ti-trash', label: i18n.t('apps.uninstall'), action: 'uninstall', danger: true });
    }
    actions.push(isHidden
      ? { icon: 'ti-eye', label: i18n.t('apps.show'), action: 'unhide' }
      : { icon: 'ti-eye-off', label: i18n.t('apps.hide'), action: 'hide' });

    const actionsEl = document.getElementById('android-app-actions');
    actionsEl.innerHTML = actions.map(a =>
      `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
        <i class="ti ${a.icon}"></i>${a.label}
      </button>`
    ).join('');

    const close = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        sheet.close();
        await this._handleAndroidSheetAction(btn.dataset.action, app);
      });
    });

    const cancelBtn = document.getElementById('android-app-cancel');
    cancelBtn.onclick = close;
    // Ignore the synthetic tap that follows a touch long-press for a moment,
    // so it doesn't immediately close the freshly-opened sheet via the backdrop.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) close(); };

    sheet.showModal();
  }

  async _handleAndroidSheetAction(action, app) {
    const pkg = app.packageName;
    if (action === 'launch') {
      this.launchAndroidApp(pkg);
    } else if (action === 'info') {
      try { await api.openAndroidAppInfo(pkg); } catch {}
      // Da "Info app" si può disinstallare: al rientro la lista va riallineata.
      this._reloadAndroidAppsOnReturn();
    } else if (action === 'uninstall') {
      // Nessuna conferma nostra: quella di Android arriva comunque e non è
      // aggirabile, quindi la nostra era solo un tap in più prima della
      // domanda vera. Stesso comportamento dei launcher di sistema.
      try { await api.uninstallAndroidApp(pkg); } catch {}
      this._reloadAndroidAppsOnReturn();
    } else if (action === 'hide') {
      this.hiddenPackages.add(pkg);
      this._persistHiddenApps();
      this.render();
    } else if (action === 'unhide') {
      this.hiddenPackages.delete(pkg);
      this._persistHiddenApps();
      this.render();
    }
  }

  // ── Skill file editor + context sheet ──

  /** Apre SKILL.md nell'editor del Workspace.
   *
   *  Fra il cambio di sezione e l'apertura vera ci sono due `await` (la
   *  `ready` del controller e la lettura del file): una finestra lunga
   *  abbastanza da riceverci dentro un secondo tap — la scheda skill si chiude
   *  con `sheet.close()` *prima* di invocare l'azione, quindi la griglia è di
   *  nuovo sotto il dito. Due aperture concorrenti si sovrascrivevano a
   *  vicenda `currentDir` e `_returnMode` e potevano lasciare l'editor su un
   *  file con il breadcrumb dell'altro. Il guard dichiara "apertura in corso" e
   *  scarta la seconda; il flag vive in `finally`, quindi un errore non lo
   *  lascia acceso. Il tasto Indietro non c'entra e non va toccato: qui non si
   *  consuma nessuna pressione. */
  async _openSkillFile(name) {
    if (this._openingSkill) return;
    this._openingSkill = name;
    const path = `skills/${name}/SKILL.md`;
    try {
      window.mobileApp.switchMode('workspace');
      const ws = window.mobileApp.controllers.workspace;
      await ws.ready;
      ws.currentDir = `skills/${name}`;
      await ws.openFile(path, 'md');
      ws._returnMode = 'apps';  // editor "back" returns to Apps, not the explorer
    } catch (err) {
      console.error('Failed to open skill file:', err);
      showToast(i18n.t('apps.cannotOpen', { name }), 'error');
    } finally {
      this._openingSkill = null;
    }
  }

  _skillUserSummary(skill) {
    const s = skill.user_summary;
    if (!s) return skill.description || skill.name;
    return s[i18n.locale] || s.it || s.en || skill.description || skill.name;
  }

  showSkillSheet(name) {
    const skill = this.skills.find(s => s.name === name);
    if (!skill) return;

    const sheet = document.getElementById('skill-sheet');
    if (!sheet) return;

    document.getElementById('skill-sheet-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon"><i class="ti ti-puzzle"></i></div>
        <div class="app-sheet-name">${escapeHtml(skill.name)}</div>
      </div>`;

    const bodyEl = document.getElementById('skill-sheet-body');
    const actionsEl = document.getElementById('skill-sheet-actions');
    const close = () => sheet.close();

    /* Le skill "locked" (bundle di sistema tipo cron/ssh) mostrano solo la
       spiegazione d'uso fuori dalla Modalità avanzata: niente azioni che
       permetterebbero di modificarle/disabilitarle/eliminarle per sbaglio. La
       regola è una sola e sta in `_skillIsManageable`, che è anche quella che
       decide fra interruttore e lucchetto nella riga: due copie divergerebbero
       e una delle due offrirebbe ciò che l'altra protegge. */
    if (!this._skillIsManageable(skill)) {
      bodyEl.textContent = this._skillUserSummary(skill);
      actionsEl.innerHTML = '';
    } else {
      bodyEl.textContent = '';
      const actions = [
        { icon: 'ti-edit', label: i18n.t('apps.edit'), action: 'edit' },
        skill.disabled
          ? { icon: 'ti-eye', label: i18n.t('apps.enable'), action: 'enable' }
          : { icon: 'ti-eye-off', label: i18n.t('apps.disable'), action: 'disable' },
        { icon: 'ti-trash', label: i18n.t('apps.delete'), action: 'delete', danger: true },
      ];

      actionsEl.innerHTML = actions.map(a =>
        `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
          <i class="ti ${a.icon}"></i>${a.label}
        </button>`
      ).join('');

      actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          sheet.close();
          await this._handleSkillSheetAction(btn.dataset.action, skill);
        });
      });
    }

    document.getElementById('skill-sheet-cancel').onclick = close;
    // Ignore the synthetic tap that follows a touch long-press for a moment,
    // so it doesn't immediately close the freshly-opened sheet via the backdrop.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) close(); };

    sheet.showModal();
  }

  async _handleSkillSheetAction(action, skill) {
    const name = skill.name;
    if (action === 'edit') {
      this._openSkillFile(name);
    } else if (action === 'disable' || action === 'enable') {
      try {
        await this._setSkillDisabled(name, action === 'disable');
        await this.loadSkills();
        this.render();
      } catch {
        showToast(i18n.t('apps.operationFailed'), 'error');
      }
    } else if (action === 'delete') {
      const ok = await confirmDialog(i18n.t('apps.deleteSkillConfirm', { name }));
      if (!ok) return;
      try {
        await this._deleteSkill(name);
        await this.loadSkills();
        this.render();
      } catch {
        showToast(i18n.t('apps.deleteFailed'), 'error');
      }
    }
  }

  // ── Jenny app context sheet ──

  showJennyAppSheet(slug) {
    const app = this.jennyApps.find(a => a.slug === slug);
    if (!app) return;

    const sheet = document.getElementById('jenny-app-sheet');
    if (!sheet) return;

    const icon = app.broken ? 'ti-alert-triangle' : (app.icon || 'ti-apps');
    document.getElementById('jenny-app-sheet-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon"><i class="ti ${escapeHtml(icon)}"></i></div>
        <div class="app-sheet-name">${escapeHtml(app.name || app.slug)}</div>
      </div>`;

    const actions = [
      { icon: 'ti-player-play', label: i18n.t('apps.open'), action: 'open' },
      { icon: 'ti-edit', label: i18n.t('apps.edit'), action: 'edit' },
      { icon: 'ti-trash', label: i18n.t('apps.delete'), action: 'delete', danger: true },
    ];

    const actionsEl = document.getElementById('jenny-app-sheet-actions');
    actionsEl.innerHTML = actions.map(a =>
      `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
        <i class="ti ${a.icon}"></i>${a.label}
      </button>`
    ).join('');

    const close = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        sheet.close();
        await this._handleJennySheetAction(btn.dataset.action, app);
      });
    });

    document.getElementById('jenny-app-sheet-cancel').onclick = close;
    // Ignore the synthetic tap that follows a touch long-press for a moment,
    // so it doesn't immediately close the freshly-opened sheet via the backdrop.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) close(); };

    sheet.showModal();
  }

  async _handleJennySheetAction(action, app) {
    const slug = app.slug;
    if (action === 'open') {
      this.openApp(slug);
    } else if (action === 'edit') {
      this._startAppModification(app);
    } else if (action === 'delete') {
      const ok = await confirmDialog(
        i18n.t('apps.deleteAppConfirm', { name: app.name || slug })
      );
      if (!ok) return;
      try {
        await api.deleteJennyApp(slug);
        await this.loadJennyApps();
        this.render();
        showToast(i18n.t('apps.appDeleted'), 'success');
      } catch {
        showToast(i18n.t('apps.deleteFailed'), 'error');
      }
    }
  }

  _startAppModification(app) {
    this._sendChatPrompt(i18n.t('apps.editAppPrompt', { name: app.name || app.slug, slug: app.slug }));
  }

  // ── API calls ──

  async _startSkillCreation() {
    this._sendChatPrompt(i18n.t('apps.createSkillPrompt'));
  }

  async _sendChatPrompt(prompt) {
    window.mobileApp.switchMode('chat');
    const chat = window.mobileApp?.controllers?.chat;
    if (!chat) return;

    chat.input.value = prompt;
    chat._autoResize?.();
    chat._updateSendState?.();

    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 50));
      if (wsManager.chatWs?.readyState === WebSocket.OPEN) break;
    }

    chat.sendMessage();
  }

  async _setSkillDisabled(name, disabled) {
    const res = await fetch(
      `/api/webui/skills/${encodeURIComponent(name)}/update?disabled=${disabled}`,
      { headers: { 'Authorization': `Bearer ${api.getSecret()}` } });
    if (!res.ok) throw new Error(i18n.t('apps.updateFailed'));
  }

  async _deleteSkill(name) {
    const res = await fetch(`/api/webui/skills/${encodeURIComponent(name)}/delete`, {
      headers: { 'Authorization': `Bearer ${api.getSecret()}` }
    });
    if (!res.ok) throw new Error(i18n.t('apps.deleteSkillFailed'));
  }

  /** `render()` **sempre**, anche a liste non ancora arrivate: ognuna delle tre
   *  stanze sa dire "sto caricando" da sé, e da qui passa anche la visibilità
   *  dell'occhio nel titolo — che `header.setMode` ha appena ridisegnato
   *  acceso. Con la vecchia guardia, entrando nella scheda prima che le fetch
   *  tornassero l'occhio restava a schermo in una stanza che non ne ha uno. */
  activate() {
    this.ensureLoaded();
    this.render();
  }

  deactivate() {
    this.closeApp();
    // Header actions are re-rendered (eye-off) on re-entry, so reset the
    // toggle state to stay in sync with the freshly-rendered icon.
    this._showHidden = false;
    // Force re-fetch of Jenny Apps on next activate (app may have been
    // created/modified by the agent while this tab was inactive).
    this._jennyAppsLoaded = false;
  }

  handleAction(action) {
    if (action === 'add-skill') this._startSkillCreation();
    else if (action === 'toggle-hidden') this._toggleShowHidden();
  }

  _toggleShowHidden() {
    this._showHidden = !this._showHidden;
    const iconEl = document.querySelector('#title-apps [data-action="toggle-hidden"] i');
    if (iconEl) {
      iconEl.classList.toggle('ti-eye', this._showHidden);
      iconEl.classList.toggle('ti-eye-off', !this._showHidden);
    }
    this.render();
  }
}
